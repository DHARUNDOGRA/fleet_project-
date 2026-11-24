"""
app.py - Fleet Fuel Efficiency & Maintenance Decision Support dashboard

Run:   streamlit run app.py
(needs fleet.db, created by:  python generate_data.py  then  python clean.py)
"""
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

BASE = Path(__file__).resolve().parent
DB = BASE / "fleet.db"
REPORT = BASE / "output" / "report.md"

st.set_page_config(page_title="Fleet Fuel Efficiency", page_icon="🚚", layout="wide")

if not DB.exists():
    st.error("fleet.db not found. Run `python generate_data.py` and then `python clean.py` first.")
    st.stop()


@st.cache_data
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a SELECT on the database (opened read-only, so nothing can be changed)."""
    con = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def summarise(vm: pd.DataFrame, vehicles: pd.DataFrame) -> pd.DataFrame:
    """Recalculate the measures for whatever vehicles / months are selected in the sidebar."""
    g = vm.groupby("vehicle_id")[["trips", "km", "litres", "fuel_cost", "maint_cost"]].sum().reset_index()
    s = g.merge(vehicles, on="vehicle_id")
    litres, km = s["litres"].where(s["litres"] > 0), s["km"].where(s["km"] > 0)
    s["km_per_l"] = s["km"] / litres
    s["fuel_cost_per_km"] = s["fuel_cost"] / km
    s["maint_cost_per_km"] = s["maint_cost"] / km
    s["total_cost_per_km"] = s["fuel_cost_per_km"] + s["maint_cost_per_km"]
    return s


# ------------------------------------------------------------------ load + sidebar
vehicles = query("SELECT * FROM vehicles")
vm_all = query("SELECT * FROM vehicle_month")
vsum = query("SELECT * FROM vehicle_summary")
rsum = query("SELECT * FROM route_summary")

st.sidebar.header("Filters")
chosen = st.sidebar.multiselect("Vehicles", vehicles["vehicle_id"].tolist(), default=vehicles["vehicle_id"].tolist())
months = sorted(vm_all["month"].unique())
m_from, m_to = st.sidebar.select_slider("Months", options=months, value=(months[0], months[-1]))
st.sidebar.caption("Filters apply to the Dashboard tab. Other tabs show the full period.")

vm = vm_all[vm_all["vehicle_id"].isin(chosen) & vm_all["month"].between(m_from, m_to)]
sel = summarise(vm, vehicles)

st.title("🚚 Fleet Fuel Efficiency and Maintenance Decision Support")

tab_dash, tab_cmp, tab_inspect, tab_verify, tab_sql, tab_find = st.tabs(
    ["📊 Dashboard", "🚛 Vehicle comparison", "🔎 Inspect records", "⚠️ Needs verification", "🗄️ SQL query", "📝 Findings"])

# ------------------------------------------------------------------ dashboard
with tab_dash:
    if vm.empty or sel["km"].sum() == 0:
        st.warning("No data for this selection.")
    else:
        km, litres = sel["km"].sum(), sel["litres"].sum()
        fuel_c, maint_c = sel["fuel_cost"].sum(), sel["maint_cost"].sum()
        flagged = vsum[vsum["vehicle_id"].isin(chosen)]
        pct_flag = flagged["records_flagged"].sum() / flagged["records_total"].sum() * 100

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fuel efficiency", f"{km / litres:.2f} km/L")
        c2.metric("Operating cost per km", f"₹{(fuel_c + maint_c) / km:.2f}", help="Fuel + maintenance")
        c3.metric("Maintenance cost per km", f"₹{maint_c / km:.2f}")
        c4.metric("Records flagged", f"{pct_flag:.1f}%", help="Trip + fuel records needing verification (full period)")

        left, right = st.columns(2)
        with left:
            long = sel.melt(id_vars="vehicle_id", value_vars=["km_per_l", "rated_kmpl"], var_name="kind", value_name="km/L")
            long["kind"] = long["kind"].map({"km_per_l": "Actual", "rated_kmpl": "Rated"})
            fig = px.bar(long, x="vehicle_id", y="km/L", color="kind", barmode="group",
                         title="Fuel efficiency: actual vs rated (km/L)", labels={"vehicle_id": "Vehicle", "kind": ""})
            st.plotly_chart(fig, width="stretch")
        with right:
            long = sel.melt(id_vars="vehicle_id", value_vars=["fuel_cost_per_km", "maint_cost_per_km"],
                            var_name="part", value_name="₹ per km")
            long["part"] = long["part"].map({"fuel_cost_per_km": "Fuel", "maint_cost_per_km": "Maintenance"})
            fig = px.bar(long, x="vehicle_id", y="₹ per km", color="part", barmode="stack",
                         title="Operating cost per km (₹)", labels={"vehicle_id": "Vehicle", "part": ""})
            st.plotly_chart(fig, width="stretch")

        left, right = st.columns(2)
        with left:
            trend = vm[vm["km_per_l"].notna()]
            fig = px.line(trend, x="month", y="km_per_l", color="vehicle_id", markers=True,
                          title="Monthly km/L by vehicle", labels={"km_per_l": "km/L", "month": "Month", "vehicle_id": "Vehicle"})
            st.plotly_chart(fig, width="stretch")
            st.caption("Monthly values are noisy because fuel is bought in fill-ups.")
        with right:
            fig = px.bar(rsum, x="route", y="est_cost_per_km", title="Estimated cost per km by route (₹)",
                         labels={"route": "Route", "est_cost_per_km": "₹ per km"}, hover_data=["trips", "total_km"])
            st.plotly_chart(fig, width="stretch")
            st.caption("Route cost is an estimate based on the vehicles that run each route.")

# ------------------------------------------------------------------ comparison
CMP_COLS = ["vehicle_id", "model", "vehicle_type", "km", "km_per_l", "rated_kmpl", "pct_of_rated",
            "fuel_cost_per_km", "maint_cost_per_km", "total_cost_per_km", "cost_rank", "pct_flagged", "recommendation"]
cmp_view = vsum[CMP_COLS].sort_values("cost_rank").round(2).reset_index(drop=True)

with tab_cmp:
    st.subheader("Vehicle comparison report (full period)")
    st.caption("cost_rank 1 = cheapest to run per km. pct_of_rated = actual km/L as % of the manufacturer's rating.")
    st.dataframe(cmp_view, hide_index=True, width="stretch")
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇ Vehicle comparison (CSV)", cmp_view.to_csv(index=False), "vehicle_comparison.csv", "text/csv")
    d2.download_button("⬇ Cleaned trips (CSV)", query("SELECT * FROM trips_clean").to_csv(index=False), "cleaned_trips.csv", "text/csv")
    d3.download_button("⬇ Cleaned fuel (CSV)", query("SELECT * FROM fuel_clean").to_csv(index=False), "cleaned_fuel.csv", "text/csv")

# ------------------------------------------------------------------ inspect
with tab_inspect:
    st.subheader("Click a vehicle to see its supporting records")
    pick = st.dataframe(cmp_view[["vehicle_id", "model", "km_per_l", "total_cost_per_km", "pct_flagged"]],
                        hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="pick")
    rows = pick.selection.rows
    vid = cmp_view.iloc[rows[0]]["vehicle_id"] if rows else cmp_view.iloc[0]["vehicle_id"]
    if not rows:
        st.info(f"Showing {vid}. Click a different row in the table above to switch vehicle.")
    st.markdown(f"### Vehicle {vid}")

    t = query("SELECT * FROM trips_clean WHERE vehicle_id = ? ORDER BY date", (vid,))
    t["status"] = t["issues"].where(t["issues"] != "", "OK")
    fig = px.scatter(t, x="date", y="odo_start_km", color="status", hover_data=["trip_id", "route", "distance_km"],
                     title="Odometer readings over time (km): problems stand out", labels={"odo_start_km": "Odometer (km)"})
    st.plotly_chart(fig, width="stretch")

    a, b, c, d, e = st.tabs(["Cleaned trips", "Raw trips", "Cleaned fuel", "Raw fuel", "Maintenance"])
    a.dataframe(t.drop(columns="status"), hide_index=True, width="stretch")
    b.dataframe(query("SELECT * FROM raw_trips WHERE UPPER(TRIM(vehicle_id)) = ? ORDER BY date", (vid,)), hide_index=True, width="stretch")
    c.dataframe(query("SELECT * FROM fuel_clean WHERE vehicle_id = ? ORDER BY date", (vid,)), hide_index=True, width="stretch")
    d.dataframe(query("SELECT * FROM raw_fuel WHERE UPPER(TRIM(vehicle_id)) = ? ORDER BY date", (vid,)), hide_index=True, width="stretch")
    e.dataframe(query("SELECT * FROM maintenance WHERE vehicle_id = ? ORDER BY date", (vid,)), hide_index=True, width="stretch")

# ------------------------------------------------------------------ verification
with tab_verify:
    ver = query("SELECT * FROM needs_verification")
    st.subheader(f"Records requiring verification ({len(ver)})")
    issues = st.multiselect("Filter by issue", sorted(ver["issue"].unique()), default=sorted(ver["issue"].unique()))
    shown = ver[ver["issue"].isin(issues)]
    left, right = st.columns([2, 1])
    left.dataframe(shown, hide_index=True, width="stretch")
    counts = ver["issue"].value_counts().reset_index()
    counts.columns = ["issue", "records"]
    right.plotly_chart(px.bar(counts, x="records", y="issue", orientation="h", title="Records by issue type"), width="stretch")
    st.download_button("⬇ Download list (CSV)", shown.to_csv(index=False), "needs_verification.csv", "text/csv")

# ------------------------------------------------------------------ SQL
EXAMPLES = {
    "Efficiency ranking": "SELECT vehicle_id, model, ROUND(km_per_l, 2) AS km_per_l, rated_kmpl\nFROM vehicle_summary\nORDER BY km_per_l DESC",
    "Cost per km ranking": "SELECT vehicle_id, model,\n       ROUND(fuel_cost_per_km, 2) AS fuel,\n       ROUND(maint_cost_per_km, 2) AS maintenance,\n       ROUND(total_cost_per_km, 2) AS total\nFROM vehicle_summary\nORDER BY total DESC",
    "Flagged records by issue": "SELECT issue, COUNT(*) AS records\nFROM needs_verification\nGROUP BY issue\nORDER BY records DESC",
    "Backward odometer trips": "SELECT trip_id, vehicle_id, date, odo_start_km, odo_end_km\nFROM trips_clean\nWHERE issues LIKE '%ODOMETER_BACKWARD%'",
    "Monthly km/L for V04": "SELECT month, ROUND(km, 0) AS km, ROUND(litres, 1) AS litres, ROUND(km_per_l, 2) AS km_per_l\nFROM vehicle_month\nWHERE vehicle_id = 'V04'\nORDER BY month",
}
with tab_sql:
    st.subheader("Query the database with SQL")
    st.caption("Tables: " + ", ".join(query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")["name"]))
    choice = st.selectbox("Example queries", list(EXAMPLES))
    sql = st.text_area("SQL (SELECT only)", EXAMPLES[choice], height=150, key=f"sql_{choice}")
    if sql.strip().lower().startswith(("select", "with")):
        try:
            res = query(sql)
            st.dataframe(res, hide_index=True, width="stretch")
            st.caption(f"{len(res)} rows")
        except Exception as err:
            st.error(f"SQL error: {err}")
    else:
        st.warning("Only SELECT queries are allowed.")

# ------------------------------------------------------------------ findings
with tab_find:
    if REPORT.exists():
        st.markdown(REPORT.read_text(encoding="utf-8"))
    else:
        st.info("output/report.md not found. Run `python clean.py`.")
