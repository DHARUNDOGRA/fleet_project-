"""
generate_data.py
----------------
Creates MOCK fleet data in ./data with DELIBERATE data problems, so that
clean.py has something real to fix:

  * duplicate fuel entries
  * mixed units  (litres vs US gallons, km vs miles)
  * messy vehicle IDs in the fuel file (lower-case, extra spaces)
  * odometer readings that go backwards or jump forward
  * wrong trip distances, impossible fuel quantities, price typos

Run:   python generate_data.py
"""
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

random.seed(42)  # same data every run

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

KM_PER_MILE = 1.609344
L_PER_GAL = 3.78541
START, END = date(2026, 1, 1), date(2026, 6, 30)

# id, reg_no, type, model, year, fuel, tank_l, rated_kmpl, real_performance (hidden, NOT saved)
VEHICLES = [
    ("V01", "MH15AB1101", "Mini Truck", "Tata Ace Gold", 2021, "Diesel", 30, 18.0, 0.97),
    ("V02", "MH15AB1102", "Mini Truck", "Tata Ace Gold", 2020, "Diesel", 30, 18.0, 0.92),
    ("V03", "MH15CD2203", "Pickup", "Mahindra Bolero Pik-Up", 2022, "Diesel", 60, 14.0, 0.98),
    ("V04", "MH15CD2204", "Pickup", "Mahindra Bolero Pik-Up", 2018, "Diesel", 60, 14.0, 0.72),
    ("V05", "MH15EF3305", "LCV", "Ashok Leyland Dost", 2021, "Diesel", 50, 13.0, 0.95),
    ("V06", "MH15EF3306", "LCV", "Eicher Pro 2049", 2022, "Diesel", 100, 11.0, 1.00),
    ("V07", "MH15EF3307", "LCV", "Tata 407", 2017, "Diesel", 90, 10.0, 0.78),
    ("V08", "MH15GH4408", "Medium Truck", "Tata 1109", 2019, "Diesel", 200, 7.0, 0.96),
    ("V09", "MH15JK5509", "Cargo Van", "Maruti Eeco Cargo", 2023, "Petrol", 32, 15.5, 0.99),
    ("V10", "MH15JK5510", "Cargo Van", "Force Traveller Cargo", 2020, "Diesel", 70, 10.5, 0.90),
]

# route: (typical km, fuel-use factor: >1 means thirstier)
ROUTES = {
    "Nashik-Pune": (210, 1.00),
    "Nashik-Mumbai": (170, 1.15),
    "Nashik-Aurangabad": (190, 0.98),
    "Nashik-Surat": (260, 1.05),
    "Nashik-Ahmednagar": (100, 0.97),
    "Nashik City Local": (45, 1.30),
}

MAINT_TYPES = [
    ("Oil Change", 2500), ("General Service", 6000), ("Tyre Replacement", 18000),
    ("Brake Service", 7500), ("Clutch Repair", 14000), ("Battery Replacement", 6500),
    ("Suspension Repair", 12000),
]
MAINT_MULT = {"Mini Truck": 1.0, "Pickup": 1.4, "LCV": 1.8, "Medium Truck": 3.0, "Cargo Van": 1.3}

trips, fuel, maint = [], [], []


def add_fuel(vid, d, litres, price):
    litres = round(litres, 2)
    in_gal = random.random() < 0.06
    fuel.append({
        "fuel_id": f"F{len(fuel) + 1:04d}",
        "vehicle_id": vid,
        "date": d,
        "quantity": round(litres / L_PER_GAL, 2) if in_gal else litres,
        "unit": "gal" if in_gal else "L",
        "total_cost_inr": round(litres * price * random.uniform(0.97, 1.03)),
    })


# ---------------------------------------------------------------- trips + fuel
for (vid, reg, vtype, model, year, ftype, tank, rated, perf) in VEHICLES:
    true_kmpl = rated * perf
    price = 94.0 if ftype == "Diesel" else 105.0
    odo = float(random.randint(20000, 90000))
    d = START + timedelta(days=random.randint(0, 2))
    used, seq, last_d = 0.0, 0, d
    while d <= END:
        route = random.choice(list(ROUTES))
        base_km, factor = ROUTES[route]
        km = round(base_km * random.uniform(0.93, 1.07), 1)
        if random.random() < 0.012:                # unlogged driving -> odometer JUMP
            odo += random.randint(600, 1800)
        start = odo + random.randint(0, 12)
        end = round(start + km, 1)
        trips.append({"vehicle_id": vid, "date": d, "route": route, "start": start,
                      "end": end, "distance": km, "seq": seq,
                      "miles": vid == "V04" or random.random() < 0.04})
        trip_use = km / true_kmpl * factor * random.uniform(0.95, 1.05)
        if used + trip_use > tank * 0.9 and used > 1:   # would run too low -> refuel BEFORE this trip
            add_fuel(vid, last_d, used, price)
            used = 0.0
        odo, seq, last_d = end, seq + 1, d
        used += trip_use
        if used >= tank * 0.6:                     # tank is getting low -> refuel
            add_fuel(vid, d, used * random.uniform(0.98, 1.02), price * random.uniform(0.97, 1.03))
            used = 0.0
        d += timedelta(days=random.randint(1, 3))
    if used > 1:
        add_fuel(vid, last_d, used, price)

# ------------------------------------------------ inject trip / odometer errors
candidates = [i for i, t in enumerate(trips) if t["seq"] >= 2]
picked = random.sample(candidates, 17)
backward, mismatch, zero = picked[:7], picked[7:15], picked[15:]
for i in backward:                                  # odometer goes BACKWARDS
    off = random.randint(3000, 15000)
    trips[i]["start"] -= off
    trips[i]["end"] -= off
for i in mismatch:                                  # distance disagrees with odometer
    trips[i]["distance"] = round(trips[i]["distance"] * random.choice([0.55, 1.4, 10]), 1)
for i in zero:                                      # zero distance
    trips[i]["distance"] = 0

trip_rows = []
for n, t in enumerate(trips, start=1):
    s, e, dist, unit = t["start"], t["end"], t["distance"], "km"
    if t["miles"]:                                  # this trip was logged in MILES
        s, e, dist = round(s / KM_PER_MILE, 1), round(e / KM_PER_MILE, 1), round(dist / KM_PER_MILE, 1)
        unit = "mi"
    trip_rows.append({"trip_id": f"T{n:04d}", "vehicle_id": t["vehicle_id"], "date": t["date"],
                      "route": t["route"], "odometer_start": s, "odometer_end": e,
                      "distance": dist, "distance_unit": unit})

# ------------------------------------------------ inject fuel errors
typo = random.sample(range(len(fuel)), 4)
fuel[typo[0]]["quantity"] = round(fuel[typo[0]]["quantity"] * 10, 2)   # extra zero in litres
fuel[typo[1]]["quantity"] = round(fuel[typo[1]]["quantity"] * 10, 2)
fuel[typo[2]]["total_cost_inr"] *= 10                                   # extra zero in price
fuel[typo[3]]["total_cost_inr"] *= 10

safe = [r for i, r in enumerate(fuel) if i not in typo]
for row in random.sample(safe, 28):                 # DUPLICATE fuel entries (new fuel_id)
    dup = dict(row)
    dup["fuel_id"] = f"F{len(fuel) + 1:04d}"
    fuel.append(dup)

for row in fuel:                                    # messy vehicle IDs
    if random.random() < 0.04:
        v = row["vehicle_id"]
        row["vehicle_id"] = random.choice([v.lower(), " " + v, v + " "])

# ------------------------------------------------ maintenance
for (vid, reg, vtype, model, year, ftype, tank, rated, perf) in VEHICLES:
    n_events = random.randint(3, 5) + (3 if vid in ("V04", "V07") else 0)   # old vehicles break more
    for _ in range(n_events):
        kind, base = random.choice(MAINT_TYPES)
        maint.append({
            "maint_id": f"M{len(maint) + 1:04d}",
            "vehicle_id": vid,
            "date": START + timedelta(days=random.randint(0, (END - START).days)),
            "maintenance_type": kind,
            "cost_inr": int(round(base * MAINT_MULT[vtype] * random.uniform(0.8, 1.3), -1)),
        })

# ------------------------------------------------ save
pd.DataFrame([v[:8] for v in VEHICLES], columns=[
    "vehicle_id", "reg_no", "vehicle_type", "model", "year", "fuel_type",
    "tank_capacity_l", "rated_kmpl"]).to_csv(DATA / "vehicles.csv", index=False)
pd.DataFrame(trip_rows).to_csv(DATA / "trips.csv", index=False)
pd.DataFrame(fuel).sort_values(["date", "fuel_id"]).to_csv(DATA / "fuel.csv", index=False)
pd.DataFrame(maint).to_csv(DATA / "maintenance.csv", index=False)

print(f"vehicles.csv     : {len(VEHICLES)} rows")
print(f"trips.csv        : {len(trip_rows)} rows "
      f"({len(backward)} backward odometer, {len(mismatch)} distance mismatch, {len(zero)} zero distance, "
      f"{sum(t['miles'] for t in trips)} in miles)")
print(f"fuel.csv         : {len(fuel)} rows (28 duplicates, 4 typos, "
      f"{sum(r['unit'] == 'gal' for r in fuel)} in gallons)")
print(f"maintenance.csv  : {len(maint)} rows")
print(f"Saved to {DATA}")
