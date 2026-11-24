"""
clean.py
--------
Cleans the raw fleet CSVs, combines them, calculates the measures,
stores everything in SQLite (fleet.db) and writes the output files.

Steps
  1. Load raw CSVs
  2. Clean fuel  : normalise vehicle IDs, convert gallons -> litres, remove duplicates,
                   flag impossible quantities / price typos
  3. Clean trips : convert miles -> km, validate the odometer sequence per vehicle
  4. Combine     : trips + fuel + maintenance by vehicle ID and month
  5. Measures    : km/L, cost per km, maintenance cost per km, % records flagged
  6. Save        : fleet.db  +  output/*.csv  +  output/report.md

Run:   python clean.py      (after generate_data.py)
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
DATA = BASE / "data"
OUT = BASE / "output"
DB = BASE / "fleet.db"
OUT.mkdir(exist_ok=True)

# ---- constants and thresholds (change these to tune the validation) ----
KM_PER_MILE = 1.609344
L_PER_GAL = 3.78541
ODO_TOL_KM = 1.0          # allowed rounding error when comparing odometer readings
JUMP_KM = 100             # gap between one trip's end and the next trip's start that looks suspicious
MISMATCH_PCT = 0.05       # distance vs odometer difference tolerance (5%) ...
MISMATCH_MIN_KM = 5       # ... but never stricter than 5 km
PRICE_RANGE = (60, 140)   # sensible Rs per litre
TANK_MARGIN = 1.05        # a fill-up above 105% of tank capacity is impossible


# ===================================================================== 1. LOAD
def load():
    vehicles = pd.read_csv(DATA / "vehicles.csv")
    trips = pd.read_csv(DATA / "trips.csv", parse_dates=["date"])
    fuel = pd.read_csv(DATA / "fuel.csv", parse_dates=["date"])
    maint = pd.read_csv(DATA / "maintenance.csv", parse_dates=["date"])
    return vehicles, trips, fuel, maint


def join_issues(flags: pd.DataFrame) -> pd.Series:
    """Turn a table of True/False flag columns into 'ISSUE_A;ISSUE_B' text."""
    return flags.apply(lambda r: ";".join(r.index[r.values]), axis=1)


# ===================================================================== 2. FUEL
def clean_fuel(raw, vehicles):
    f = raw.copy()
    f["vehicle_id"] = f["vehicle_id"].str.strip().str.upper()
    f["unit"] = f["unit"].str.strip().str.lower()
    is_gal = f["unit"].isin(["gal", "gallon", "gallons"])
    f["litres"] = np.where(is_gal, f["quantity"] * L_PER_GAL, f["quantity"]).round(2)
    f["unit_converted"] = is_gal

    # duplicates: same vehicle + date + litres + cost  (keep the first fuel_id)
    f = f.sort_values("fuel_id").reset_index(drop=True)
    key = ["vehicle_id", "date", "litres", "total_cost_inr"]
    first_id = f.groupby(key)["fuel_id"].transform("first")
    is_dup = f["fuel_id"] != first_id
    dups = f[is_dup].assign(duplicate_of=first_id[is_dup])
    f = f[~is_dup].copy()

    # sanity checks
    f = f.merge(vehicles[["vehicle_id", "tank_capacity_l"]], on="vehicle_id", how="left")
    f["price_per_l"] = (f["total_cost_inr"] / f["litres"]).round(2)
    flags = pd.DataFrame({
        "UNKNOWN_VEHICLE": f["tank_capacity_l"].isna(),
        "FUEL_EXCEEDS_TANK": f["litres"] > f["tank_capacity_l"] * TANK_MARGIN,
        "PRICE_OUTLIER": ~f["price_per_l"].between(*PRICE_RANGE),
    })
    f["issues"] = join_issues(flags)
    f["valid_for_analysis"] = f["issues"].eq("")
    f["month"] = f["date"].dt.to_period("M").astype(str)
    return f, dups


# ===================================================================== 3. TRIPS
def clean_trips(raw):
    t = raw.copy()
    t["vehicle_id"] = t["vehicle_id"].str.strip().str.upper()
    unit = t["distance_unit"].str.strip().str.lower()
    mult = np.where(unit.isin(["mi", "mile", "miles"]), KM_PER_MILE, 1.0)
    t["distance_km"] = (t["distance"] * mult).round(1)
    t["odo_start_km"] = (t["odometer_start"] * mult).round(1)
    t["odo_end_km"] = (t["odometer_end"] * mult).round(1)
    t["unit_converted"] = mult != 1.0
    t = t.sort_values(["vehicle_id", "date", "trip_id"]).reset_index(drop=True)

    issues = [[] for _ in range(len(t))]
    last_good_end = {}                       # last trustworthy odometer reading per vehicle
    for i, r in enumerate(t.itertuples()):
        s, e, dist = r.odo_start_km, r.odo_end_km, r.distance_km

        if dist <= 0:
            issues[i].append("NONPOSITIVE_DISTANCE")
        if e < s:
            issues[i].append("ODOMETER_END_BEFORE_START")
        elif dist > 0 and abs(dist - (e - s)) > max(MISMATCH_MIN_KM, MISMATCH_PCT * (e - s)):
            issues[i].append("DISTANCE_ODOMETER_MISMATCH")

        prev = last_good_end.get(r.vehicle_id)
        if prev is not None and s < prev - ODO_TOL_KM:
            issues[i].append("ODOMETER_BACKWARD")      # do not trust this reading
        else:
            if prev is not None and s - prev > JUMP_KM:
                issues[i].append("ODOMETER_JUMP")
            if e >= s:
                last_good_end[r.vehicle_id] = e

    t["issues"] = [";".join(x) for x in issues]
    t["valid_for_analysis"] = t["issues"].eq("")
    t["month"] = t["date"].dt.to_period("M").astype(str)
    return t


# ===================================================================== 4. COMBINE
def build_monthly(trips_ok, fuel_ok, maint):
    keys = ["vehicle_id", "month"]
    a = trips_ok.groupby(keys).agg(trips=("trip_id", "count"), km=("distance_km", "sum")).reset_index()
    b = fuel_ok.groupby(keys).agg(litres=("litres", "sum"), fuel_cost=("total_cost_inr", "sum")).reset_index()
    c = maint.groupby(keys).agg(maint_cost=("cost_inr", "sum")).reset_index()
    vm = a.merge(b, on=keys, how="outer").merge(c, on=keys, how="outer").fillna(0)
    vm["km_per_l"] = np.where(vm["litres"] > 0, vm["km"] / vm["litres"].where(vm["litres"] > 0), np.nan)
    vm["cost_per_km"] = np.where(vm["km"] > 0, (vm["fuel_cost"] + vm["maint_cost"]) / vm["km"].where(vm["km"] > 0), np.nan)
    return vm.sort_values(keys).reset_index(drop=True)


# ===================================================================== 5. MEASURES
def build_vehicle_summary(vm, vehicles, trips, fuel_ok, dups):
    g = vm.groupby("vehicle_id")[["trips", "km", "litres", "fuel_cost", "maint_cost"]].sum().reset_index()
    s = vehicles.merge(g, on="vehicle_id", how="left")
    s["km_per_l"] = s["km"] / s["litres"]
    s["fuel_cost_per_km"] = s["fuel_cost"] / s["km"]
    s["maint_cost_per_km"] = s["maint_cost"] / s["km"]
    s["total_cost_per_km"] = s["fuel_cost_per_km"] + s["maint_cost_per_km"]
    s["pct_of_rated"] = s["km_per_l"] / s["rated_kmpl"] * 100

    # % of records flagged (trip rows + fuel rows, duplicates count as flagged)
    trips_n = trips.groupby("vehicle_id").size()
    trips_bad = trips[~trips["valid_for_analysis"]].groupby("vehicle_id").size()
    fuel_n = fuel_ok.groupby("vehicle_id").size().add(dups.groupby("vehicle_id").size(), fill_value=0)
    fuel_bad = (~fuel_ok["valid_for_analysis"]).groupby(fuel_ok["vehicle_id"]).sum().add(
        dups.groupby("vehicle_id").size(), fill_value=0)
    s = s.set_index("vehicle_id")
    s["records_total"] = trips_n.add(fuel_n, fill_value=0)
    s["records_flagged"] = trips_bad.add(fuel_bad, fill_value=0)
    s = s.fillna({"records_flagged": 0}).reset_index()
    s["pct_flagged"] = s["records_flagged"] / s["records_total"] * 100

    s["cost_rank"] = s["total_cost_per_km"].rank(method="min").astype(int)   # 1 = cheapest per km

    med_maint = s["maint_cost_per_km"].median()
    def advice(r):
        notes = []
        if r["pct_of_rated"] < 80:
            notes.append("Fuel use >20% above rated: check engine, tyres, driving habits")
        if r["maint_cost_per_km"] > 1.5 * med_maint:
            notes.append("High maintenance cost per km: review repair history / replacement")
        if r["pct_flagged"] > 10:
            notes.append("Many records flagged: verify data entry")
        return "; ".join(notes) if notes else "OK"
    s["recommendation"] = s.apply(advice, axis=1)
    return s


def build_route_summary(trips_ok, vsum):
    t = trips_ok.merge(vsum[["vehicle_id", "fuel_cost_per_km", "maint_cost_per_km"]], on="vehicle_id")
    t["est_cost"] = t["distance_km"] * (t["fuel_cost_per_km"] + t["maint_cost_per_km"])
    r = t.groupby("route").agg(trips=("trip_id", "count"), total_km=("distance_km", "sum"),
                               vehicles=("vehicle_id", "nunique"), est_cost=("est_cost", "sum")).reset_index()
    r["avg_trip_km"] = r["total_km"] / r["trips"]
    r["est_cost_per_km"] = r["est_cost"] / r["total_km"]
    return r.drop(columns="est_cost").sort_values("est_cost_per_km", ascending=False)


# ===================================================================== VERIFICATION LIST
def build_verification(trips, fuel_ok, dups):
    rows = []
    for r in trips[trips["issues"] != ""].itertuples():
        for issue in r.issues.split(";"):
            rows.append(("TRIP", r.trip_id, r.vehicle_id, r.date, issue,
                         f"odometer {r.odo_start_km:.0f} -> {r.odo_end_km:.0f} km; reported distance {r.distance_km:.1f} km",
                         "Excluded from calculations until verified"))
    for r in fuel_ok[fuel_ok["issues"] != ""].itertuples():
        for issue in r.issues.split(";"):
            rows.append(("FUEL", r.fuel_id, r.vehicle_id, r.date, issue,
                         f"{r.litres:.1f} L for Rs {r.total_cost_inr:.0f} (Rs {r.price_per_l:.0f}/L); tank {r.tank_capacity_l} L",
                         "Excluded from calculations until verified"))
    for r in dups.itertuples():
        rows.append(("FUEL", r.fuel_id, r.vehicle_id, r.date, "DUPLICATE_FUEL_ENTRY",
                     f"same vehicle/date/litres/cost as {r.duplicate_of}", "Removed (duplicate)"))
    v = pd.DataFrame(rows, columns=["record_type", "record_id", "vehicle_id", "date", "issue", "details", "action"])
    return v.sort_values(["vehicle_id", "date", "record_id"]).reset_index(drop=True)


# ===================================================================== REPORT
def write_report(raw_trips, raw_fuel, trips, fuel_ok, dups, vm, vsum, rsum, ver):
    km, litres = vm["km"].sum(), vm["litres"].sum()
    fuel_cost, maint_cost = vm["fuel_cost"].sum(), vm["maint_cost"].sum()
    kmpl = km / litres
    naive = raw_trips["distance"].sum() / raw_fuel["quantity"].sum()
    total_rec = len(raw_trips) + len(raw_fuel)
    flagged = len(ver[ver["record_type"].isin(["TRIP", "FUEL"])].drop_duplicates(["record_type", "record_id"]))
    pct = flagged / total_rec * 100
    best, worst = vsum.sort_values("km_per_l", ascending=False).iloc[0], vsum.sort_values("pct_of_rated").iloc[0]
    cheap, dear = vsum.sort_values("total_cost_per_km").iloc[0], vsum.sort_values("total_cost_per_km").iloc[-1]
    maint_top = vsum.sort_values("maint_cost_per_km").iloc[-1]
    attention = vsum[vsum["recommendation"] != "OK"]
    counts = ver["issue"].value_counts()
    r_worst = rsum.iloc[0]

    md = f"""# Fleet Fuel Efficiency and Maintenance - Findings

## Summary measures (clean data, Jan-Jun 2026)
| Measure | Value |
|---|---|
| Fleet fuel efficiency | **{kmpl:.2f} km/L** ({km:,.0f} km on {litres:,.0f} L) |
| Fleet operating cost per km (fuel + maintenance) | **Rs {(fuel_cost + maint_cost) / km:.2f}** (fuel Rs {fuel_cost / km:.2f} + maintenance Rs {maint_cost / km:.2f}) |
| Records flagged for verification | **{pct:.1f}%** ({flagged} of {total_rec} trip and fuel records) |

## What the data cleaning changed
- Removed **{len(dups)}** duplicate fuel entries and converted mixed units (gallons to litres, miles to km).
- Flagged {counts.get('ODOMETER_BACKWARD', 0)} backward odometer readings, {counts.get('ODOMETER_JUMP', 0)} odometer jumps, \
{counts.get('DISTANCE_ODOMETER_MISMATCH', 0)} distance/odometer mismatches and {counts.get('NONPOSITIVE_DISTANCE', 0)} zero-distance trips.
- Flagged {counts.get('FUEL_EXCEEDS_TANK', 0)} fuel entries above tank capacity and {counts.get('PRICE_OUTLIER', 0)} price outliers.
- Without cleaning, the fleet would appear to run at **{naive:.2f} km/L**; after cleaning it is **{kmpl:.2f} km/L**.
  The raw figure is wrong because it mixes miles with km, gallons with litres, and counts duplicate fills.

## Interpretation
- **Most efficient vehicle:** {best['vehicle_id']} ({best['model']}) at {best['km_per_l']:.1f} km/L.
- **Furthest below its rated efficiency:** {worst['vehicle_id']} ({worst['model']}) reaches only {worst['pct_of_rated']:.0f}% of its rated {worst['rated_kmpl']:.1f} km/L.
- **Cheapest to run:** {cheap['vehicle_id']} at Rs {cheap['total_cost_per_km']:.2f}/km. **Most expensive:** {dear['vehicle_id']} at Rs {dear['total_cost_per_km']:.2f}/km.
- **Highest maintenance burden:** {maint_top['vehicle_id']} at Rs {maint_top['maint_cost_per_km']:.2f}/km.
- **Vehicles needing attention:** {', '.join(attention['vehicle_id']) if len(attention) else 'none'}.
- **Highest estimated route cost:** {r_worst['route']} at about Rs {r_worst['est_cost_per_km']:.2f}/km (driven by which vehicles run it, see limitations).

## Limitations
- Fuel is bought in fill-ups, so month-level km/L is noisy; the full-period figure per vehicle is more reliable.
- Flagged records are excluded from the calculations, which slightly understates distance for vehicles with odometer problems.
- Route cost is an estimate: each trip is charged at its vehicle's average cost per km, so route differences reflect which vehicles run them.
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")


# ===================================================================== MAIN
def to_db(tables):
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    for name, df in tables.items():
        d = df.copy()
        for c in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[c]):
                d[c] = d[c].dt.strftime("%Y-%m-%d")
        d.to_sql(name, con, index=False)
    con.execute("CREATE INDEX idx_trips_vehicle ON trips_clean(vehicle_id)")
    con.execute("CREATE INDEX idx_fuel_vehicle ON fuel_clean(vehicle_id)")
    con.commit()
    con.close()


def main():
    vehicles, raw_trips, raw_fuel, maint = load()
    fuel_all, dups = clean_fuel(raw_fuel, vehicles)
    trips = clean_trips(raw_trips)
    maint = maint.assign(vehicle_id=maint["vehicle_id"].str.strip().str.upper(),
                         month=maint["date"].dt.to_period("M").astype(str))

    vm = build_monthly(trips[trips["valid_for_analysis"]], fuel_all[fuel_all["valid_for_analysis"]], maint)
    vsum = build_vehicle_summary(vm, vehicles, trips, fuel_all, dups)
    rsum = build_route_summary(trips[trips["valid_for_analysis"]], vsum)
    ver = build_verification(trips, fuel_all, dups)

    trip_cols = ["trip_id", "vehicle_id", "date", "month", "route", "odometer_start", "odometer_end", "distance",
                 "distance_unit", "odo_start_km", "odo_end_km", "distance_km", "unit_converted", "issues", "valid_for_analysis"]
    fuel_cols = ["fuel_id", "vehicle_id", "date", "month", "quantity", "unit", "litres", "total_cost_inr", "price_per_l",
                 "unit_converted", "tank_capacity_l", "issues", "valid_for_analysis"]
    trips_out, fuel_out = trips[trip_cols], fuel_all[fuel_cols]

    to_db({
        "vehicles": vehicles, "raw_trips": raw_trips, "raw_fuel": raw_fuel, "maintenance": maint,
        "trips_clean": trips_out, "fuel_clean": fuel_out, "vehicle_month": vm,
        "vehicle_summary": vsum, "route_summary": rsum, "needs_verification": ver,
    })

    trips_out.to_csv(OUT / "cleaned_trips.csv", index=False)
    fuel_out.to_csv(OUT / "cleaned_fuel.csv", index=False)
    vsum.round(2).to_csv(OUT / "vehicle_comparison.csv", index=False)
    ver.to_csv(OUT / "needs_verification.csv", index=False)
    write_report(raw_trips, raw_fuel, trips, fuel_all, dups, vm, vsum, rsum, ver)

    print(f"Duplicates removed : {len(dups)}")
    print(f"Trips flagged      : {(~trips['valid_for_analysis']).sum()} of {len(trips)}")
    print(f"Fuel flagged       : {(~fuel_all['valid_for_analysis']).sum()} of {len(fuel_all)} (after de-duplication)")
    print(f"Saved database     : {DB}")
    print(f"Saved outputs      : {OUT}")


if __name__ == "__main__":
    main()
