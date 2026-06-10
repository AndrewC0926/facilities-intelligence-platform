"""
Facilities Intelligence Platform — Streamlit dashboard (the DELIVERY layer).

This front end contains NO business logic. Every number on screen comes straight
from a SQL view in sql/views.sql — the same views Tableau would read. That is the
whole point: swap Streamlit for Tableau and nothing else changes.

It also hosts the 30-second quality-issue intake form, which writes structured
rows directly into the quality_issues table (the "capture quality issues across
the portfolio" function).

Run:  streamlit run app/dashboard.py
"""
import datetime
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fip import brief, db, scenario  # noqa: E402  -- presentation calls into the logic layer


def load(view_or_sql):
    conn = db.connect()
    try:
        return pd.DataFrame(db.query(conn, view_or_sql))
    finally:
        conn.close()


def load_rows(view_or_sql):
    """Raw dict rows (what the SQL views return) for the pure scenario layer."""
    conn = db.connect()
    try:
        return db.query(conn, view_or_sql)
    finally:
        conn.close()


st.set_page_config(page_title="Facilities Intelligence Platform", layout="wide")
st.title("🏭 Facilities Intelligence Platform")
st.caption("One source of truth across ERP · MRP · HRIS — every tile reads a SQL view, "
           "exactly as Tableau would.")

if not os.path.exists(db.DB_PATH):
    # Cloud-friendly bootstrap: on a fresh deploy there is no fip.db yet.
    # Run the full pipeline (seed -> schema -> ETL+reconcile -> views -> exports)
    # right here so the app works from a bare git clone with zero setup.
    with st.spinner("First boot: building the database (seed -> ETL -> reconcile -> views)..."):
        from fip import pipeline
        pipeline.run()
    st.toast("Pipeline complete. Database built from source exports.", icon="✅")

# --- Scenario controls (sidebar) ----------------------------------------------
# Per-site growth multiplier. All math lives in fip.scenario; the sidebar only
# collects the inputs and the body only renders the outputs.
base_collision = load_rows("SELECT * FROM vw_capacity_collision")
st.sidebar.header("🎛️ Scenario modeling")
st.sidebar.caption("Scale each site's demand growth. The multiplier hits BOTH the "
                   "floor and power trend, so the breach quarter — and which ceiling "
                   "binds — moves live.")
multipliers = {}
for r in sorted(base_collision, key=lambda r: r["site_name"]):
    multipliers[r["site_id"]] = st.sidebar.slider(
        r["site_name"], 0.0, 3.0, 1.0, 0.25, key=f"mult_{r['site_id']}")
if any(m != 1.0 for m in multipliers.values()):
    st.sidebar.info("Scenario active — figures below reflect your multipliers, "
                    "not the baseline plan.")

scenario_rows = scenario.apply(base_collision, multipliers)

# --- Collision detector: the headline alert -----------------------------------
st.header("⚠️ Capacity Collision Detector")
st.caption("Source view: `vw_capacity_collision` (+ `fip.scenario`) — projects MRP "
           "demand growth on TWO ceilings (floor sq ft and power kW) and warns ~2 "
           "quarters before whichever one binds first.")
warnings = sorted(
    [r for r in scenario_rows if r["binding_status"] in ("COLLISION WARNING", "AT THE WALL NOW")],
    key=lambda r: r["binding_quarters_to_wall"])
if warnings:
    for r in warnings:
        mult = "" if r["growth_multiplier"] == 1.0 else f" (growth ×{r['growth_multiplier']:g})"
        st.error(
            f"**{r['site_name']}** — binding constraint **{r['binding_constraint'].upper()}**, "
            f"{r['binding_status']}{mult}. Projected to cross 85% of its "
            f"{r['binding_constraint']} ceiling in **{r['binding_breach_quarter']}**. "
            f"Floor utilization {r['current_util_pct']}% · "
            f"power utilization {r['power_util_pct']}%.")
        rec = scenario.recommend_relocation(scenario_rows, r["site_id"])
        if rec:
            where = "same region" if rec["same_region"] else f"{rec['region']} region"
            st.info(
                f"↳ **Relocation candidate: {rec['site_name']}** ({where}) — "
                f"{rec['slack']:,} {rec['unit']} of slack below its wall, enough to absorb "
                f"the ~{rec['overflow']:,} {rec['unit']} of {r['site_name']} overflow.")
else:
    st.success("No imminent capacity collisions at the current scenario.")
st.dataframe(pd.DataFrame(scenario_rows), use_container_width=True, hide_index=True)

# --- Exec brief generator -----------------------------------------------------
st.header("📄 Executive brief")
st.caption("A dated one-pager built from the live views (same content as "
           "`python -m fip.brief`), reflecting the current scenario.")
if st.button("Generate exec brief"):
    md = brief.render(multipliers=multipliers)
    st.download_button("⬇️ Download brief (.md)", md,
                       file_name=f"fip-exec-brief-{datetime.date.today().isoformat()}.md",
                       mime="text/markdown")
    st.markdown(md)

# --- Three exec questions, side by side ---------------------------------------
c1, c2 = st.columns(2)

with c1:
    st.header("🧪 Quality by site")
    st.caption("Source view: `vw_quality_by_site_quarter`")
    quality = load("SELECT * FROM vw_quality_by_site_quarter ORDER BY open_count DESC, avg_severity DESC")
    by_site = quality.groupby("site_name").agg(
        open_issues=("open_count", "sum"),
        avg_severity=("avg_severity", "mean"),
        total_issues=("issue_count", "sum")).reset_index().sort_values("open_issues", ascending=False)
    st.bar_chart(by_site.set_index("site_name")["open_issues"])
    st.dataframe(quality, use_container_width=True, hide_index=True)

with c2:
    st.header("💲 Cost per square foot")
    st.caption("Source view: `vw_cost_per_sqft`")
    cost = load("SELECT * FROM vw_cost_per_sqft ORDER BY cost_per_sqft_usd")
    plot = cost.dropna(subset=["cost_per_sqft_usd"])
    st.bar_chart(plot.set_index("site_name")["cost_per_sqft_usd"])
    st.dataframe(cost, use_container_width=True, hide_index=True)

st.header("🪑 Headcount vs. seats")
st.caption("Source view: `vw_headcount_vs_seats` — over capacity = people with no desk; "
           "under-utilized = paying for empty seats.")
seats = load("SELECT * FROM vw_headcount_vs_seats ORDER BY quarter, seat_utilization_pct DESC")
latest_q = seats["quarter"].max()
st.dataframe(seats[seats["quarter"] == latest_q], use_container_width=True, hide_index=True)

# --- 30-second quality intake form --------------------------------------------
st.header("📝 Report a quality issue (30 seconds)")
st.caption("Writes a structured row straight into `quality_issues` — the same table "
           "the ERP/CMMS feed lands in.")
sites = load("SELECT site_id, site_name FROM sites ORDER BY site_name")
with st.form("intake", clear_on_submit=True):
    fc1, fc2, fc3 = st.columns(3)
    site = fc1.selectbox("Site", sites["site_id"],
                         format_func=lambda sid: sites.set_index("site_id").loc[sid, "site_name"])
    quarter = fc1.text_input("Quarter", value=str(latest_q))
    category = fc2.selectbox("Category", ["facility", "equipment", "safety", "supply"])
    severity = fc2.slider("Severity", 1, 5, 3)
    reported = fc3.date_input("Reported date")
    desc = st.text_input("What happened?")
    if st.form_submit_button("Submit issue"):
        conn = db.connect()
        nid = db.query(conn, "SELECT COALESCE(MAX(issue_id),0)+1 AS n FROM quality_issues")[0]["n"]
        conn.execute(
            "INSERT INTO quality_issues VALUES (?,?,?,?,?,?,?,?)",
            (nid, site, quarter, category, severity, "open", str(reported), desc))
        conn.commit()
        conn.close()
        st.success(f"Logged issue #{nid} at {site}. Refresh to see it flow through the views.")
