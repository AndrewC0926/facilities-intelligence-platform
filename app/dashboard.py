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
from fip import actions, brief, db, notify, scenario  # noqa: E402  -- presentation calls into the logic layer


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

today = datetime.date.today()
cliff_rows = load_rows("SELECT * FROM vw_lease_cliff")

(tab_scorecard, tab_cap, tab_programs, tab_occupancy, tab_actions, tab_lease,
 tab_health, tab_recon, tab_quality, tab_intake) = st.tabs([
    "📊 Scorecard", "⚠️ Capacity & Scenario", "🚀 Programs", "🪑 Occupancy & Seats",
    "✅ Actions", "📅 Lease Cliff", "💚 Site Health", "🔁 Reconciliation",
    "🧪 Quality & Cost", "📝 Report Issue"])

# === Tab: Scorecard ===========================================================
with tab_scorecard:
    st.subheader("KPI scorecard")
    st.caption("Source: `vw_kpi_scorecard` — the KPIs a COO would set (facilities never "
               "the bottleneck, forecasts graded, capital on time, space in its corridor, "
               "people ready day one). One row per KPI; the platform grades itself.")
    kpis = load("SELECT kpi_label, value, unit, target, status, detail FROM vw_kpi_scorecard")
    cols = st.columns(len(kpis)) if 0 < len(kpis) <= 8 else [st]
    for i, (_, r) in enumerate(kpis.iterrows()):
        (cols[i] if len(cols) > 1 else st).metric(
            r["kpi_label"].split("(")[0].strip(), f"{r['value']:g} {r['unit']}", r["status"])
    _bg = {"AT RISK": "background-color:#f8d7da", "watch": "background-color:#fff3cd", "ok": ""}
    st.dataframe(kpis.style.apply(lambda row: [_bg.get(row["status"], "")] * len(row), axis=1),
                 use_container_width=True, hide_index=True)

    # --- Decision queue (Phase 5): risks that need a human, on the clock ----------
    st.markdown("---")
    st.subheader("Decision queue")
    st.caption("Source: `vw_decision_queue` — every material change or at-risk collision "
               "becomes a queued decision with a deadline set by physics (the breach date "
               "minus the fix's lead time). OVERDUE = past that date; CLOSING = within 30 days.")
    dq = load("SELECT urgency, decide_by_date, days_remaining, site_name, space_type, "
              "source, title, owner FROM vw_decision_queue")
    if dq.empty:
        st.success("No open decisions on the clock.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Open decisions", len(dq))
        m2.metric("🔴 OVERDUE", int((dq["urgency"] == "OVERDUE").sum()))
        m3.metric("🟡 CLOSING", int((dq["urgency"] == "CLOSING").sum()))
        _dbg = {"OVERDUE": "background-color:#f8d7da", "CLOSING": "background-color:#fff3cd", "OPEN": ""}
        st.dataframe(dq.style.apply(lambda row: [_dbg.get(row["urgency"], "")] * len(row), axis=1),
                     use_container_width=True, hide_index=True)

    st.subheader("What changed since the last forecast")
    st.caption("Source: `vw_material_changes` — the diff of the two most recent forecast snapshots.")
    mc = load("SELECT site_name, space_type, what_changed, previous_value, current_value, direction "
              "FROM vw_material_changes")
    if mc.empty:
        st.info("No material changes yet (need two forecast snapshots to compare).")
    else:
        st.dataframe(mc, use_container_width=True, hide_index=True)

# === Tab: Capacity & Scenario =================================================
with tab_cap:
    st.subheader("Capacity Collision Detector")
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

    # --- Stakeholder notification draft (copy-paste only) ---------------------
    owners = {a["site_id"]: a["owner"] for a in load_rows("SELECT site_id, owner FROM actions")}
    alerts = notify.build_alerts(scenario_rows, cliff_rows, owners=owners)
    if alerts:
        st.markdown("---")
        st.subheader("📣 Draft stakeholder alert")
        st.caption("A collision warning or lease-cliff AT RISK flag is active. Draft a "
                   "structured, copy-paste heads-up — FIP sends nothing.")
        names = {a["site_id"]: a["site_name"] for a in alerts}
        if len(alerts) > 1:
            sel = st.selectbox("Site", [a["site_id"] for a in alerts],
                               format_func=lambda s: names[s])
        else:
            sel = alerts[0]["site_id"]
        if st.button("Draft stakeholder alert"):
            st.code(next(a["text"] for a in alerts if a["site_id"] == sel), language="text")

    # --- Exec brief generator -------------------------------------------------
    st.markdown("---")
    st.subheader("📄 Executive brief")
    st.caption("A dated one-pager built from the live views (same content as "
               "`python -m fip.brief`), reflecting the current scenario.")
    if st.button("Generate exec brief"):
        md = brief.render(multipliers=multipliers)
        st.download_button("⬇️ Download brief (.md)", md,
                           file_name=f"fip-exec-brief-{today.isoformat()}.md",
                           mime="text/markdown")
        st.markdown(md)

# === Tab: Programs ============================================================
with tab_programs:
    st.subheader("Program ↔ facility risk")
    st.caption("Source: `vw_program_facility_risk` — the \"so what\" of the collision "
               "detector: when a building hits a wall, which PROGRAMS does it stop, how "
               "far short of their unit target, and how many quarters until it bites.")
    progs = load("SELECT * FROM vw_program_facility_risk")
    at_risk_progs = progs[progs["binding_status"].isin(["COLLISION WARNING", "AT THE WALL NOW"])]
    if not at_risk_progs.empty:
        for _, r in at_risk_progs.iterrows():
            tgt = "—" if pd.isna(r["units_per_quarter_target"]) else f"{int(r['units_per_quarter_target'])}/qtr target"
            st.error(
                f"**{r['program_name']}** ({r['program_type']}) at **{r['site_name']}** — "
                f"the {r['binding_constraint']} ceiling binds in **{r['binding_breach_quarter']}** "
                f"({int(r['quarters_to_constraint'])} quarter(s) out), capping a {tgt}.")
    else:
        st.success("No programs sit behind an imminent facilities constraint.")
    st.dataframe(progs, use_container_width=True, hide_index=True)

# === Tab: Occupancy & Seats ===================================================
with tab_occupancy:
    st.subheader("Occupancy & seat demand")
    st.caption("Sources: `vw_space_collision`, `vw_time_to_seat`, `vw_plan_reconciliation`. "
               "\"Headcount\" is N demand curves: worker archetypes consume different space "
               "types at different ratios. Desks are rarely what binds at industrial sites. "
               "SCIF utilization is badge/booking-derived — sensor occupancy is unavailable in "
               "accredited space (ICD 705).")

    st.markdown("**Binding space type per site** — the space that runs out first")
    binding = load_rows(
        "SELECT * FROM vw_space_collision WHERE is_binding = 1 "
        "ORDER BY (quarters_to_wall IS NULL), quarters_to_wall, site_name")
    warned = [b for b in binding if b["space_status"] in ("AT THE WALL NOW", "COLLISION WARNING")]
    for b in warned:
        when = b["breach_quarter"] or "now"
        st.error(
            f"**{b['site_name']}** — binding space type **{b['space_type'].upper()}**, "
            f"{b['space_status']} (~{when}). Utilization {b['current_util_pct']}% of "
            f"{b['capacity']} {b['unit_label']}(s).")
    if not warned:
        st.success("No site is at its space wall this cycle.")
    st.dataframe(load("SELECT site_name, space_type, capacity, capacity_status, current_util_pct, "
                      "space_status, breach_quarter, supportable_units FROM vw_space_collision "
                      "WHERE is_binding = 1 ORDER BY (quarters_to_wall IS NULL), quarters_to_wall"),
                 use_container_width=True, hide_index=True)

    st.markdown("**Time-to-seat** — where facilities, not hiring, is the bottleneck")
    tts = load("SELECT site_name, archetype, open_reqs, time_to_fill_days, binding_space_type, "
               "time_to_seat_days, bottleneck_flag FROM vw_time_to_seat "
               "ORDER BY bottleneck_flag DESC, site_name")
    flags = tts[tts["bottleneck_flag"] == "facilities_bottleneck"]
    if not flags.empty:
        for _, r in flags.iterrows():
            st.warning(
                f"**{r['site_name']} · {r['archetype']}** — FACILITIES IS THE BOTTLENECK: "
                f"{r['binding_space_type']} takes **{r['time_to_seat_days']}d** to add vs. "
                f"**{r['time_to_fill_days']}d** to hire.")
    _bg = {"facilities_bottleneck": "background-color:#f8d7da", "ok": ""}
    st.dataframe(tts.style.apply(lambda row: [_bg.get(row["bottleneck_flag"], "")] * len(row), axis=1),
                 use_container_width=True, hide_index=True)

    st.markdown("**Plan reconciliation** — authorized vs. pipeline-implied vs. space-supportable")
    st.caption("Where the three plans disagree. A negative `delta_supportable_vs_pipeline` means "
               "the space cannot hold the hiring plan.")
    st.dataframe(load("SELECT * FROM vw_plan_reconciliation ORDER BY site_name"),
                 use_container_width=True, hide_index=True)

# === Tab: Actions =============================================================
with tab_actions:
    st.subheader("Open actions")
    st.caption("Source: `vw_open_actions` (+ `fip.actions`). Color = age: "
               "🟢 <30 days · 🟡 30–60 days · 🔴 >60 days. Every open item has an owner and a due date.")
    conn = db.connect()
    acts = actions.open_actions(conn, today)
    conn.close()
    if acts:
        counts = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
        for a in acts:
            counts[a["age_band"]] = counts.get(a["age_band"], 0) + 1
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Open", len(acts))
        m2.metric("🟢 <30d", counts["green"])
        m3.metric("🟡 30–60d", counts["yellow"])
        m4.metric("🔴 >60d", counts["red"])
        df = pd.DataFrame(acts)[["site_name", "source", "title", "owner",
                                 "due_date", "status", "age_days", "age_band"]]
        bg = {"green": "background-color:#d4edda", "yellow": "background-color:#fff3cd",
              "red": "background-color:#f8d7da", "unknown": ""}
        styled = df.style.apply(lambda row: [bg.get(row["age_band"], "")] * len(row), axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.success("No open actions — every insight has been resolved.")

# === Tab: Lease Cliff =========================================================
with tab_lease:
    st.subheader("Lease cliff calendar")
    st.caption("Source: `vw_lease_cliff` — days between the lease option deadline and the "
               "projected capacity breach. A window < 180 days ⇒ **AT RISK** (you'd commit "
               "to a lease before you know the site fits).")
    at_risk = [r for r in cliff_rows if r["cliff_status"] == "AT RISK"]
    for r in at_risk:
        st.error(
            f"**{r['site_name']}** — AT RISK: only **{r['decision_window_days']} days** "
            f"between the lease option deadline ({r['lease_option_deadline']}) and the "
            f"projected {r['binding_constraint']} breach ({r['binding_breach_quarter']}, "
            f"~{r['breach_date']}).")
    if not at_risk:
        st.success("No lease cliffs inside the 180-day decision window.")
    cdf = pd.DataFrame(cliff_rows)
    st.dataframe(cdf, use_container_width=True, hide_index=True)
    windows = cdf.dropna(subset=["decision_window_days"])
    if not windows.empty:
        st.caption("Decision window in days (shorter bar = more urgent)")
        st.bar_chart(windows.set_index("site_name")["decision_window_days"])

# === Tab: Site Health =========================================================
with tab_health:
    st.subheader("Site health scorecard")
    st.caption("Source: `vw_site_health` — composite 0–100 = equal-weight average of four "
               "components: capacity headroom, quality, cost efficiency (vs portfolio median), "
               "and data completeness. Expand a site for its breakdown.")
    health = load("SELECT * FROM vw_site_health ORDER BY health_score DESC")
    st.bar_chart(health.set_index("site_name")["health_score"])
    st.dataframe(health, use_container_width=True, hide_index=True)
    for _, r in health.iterrows():
        with st.expander(f"{r['site_name']} — health {r['health_score']}/100"):
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Capacity headroom", r["capacity_score"])
            b2.metric("Quality", r["quality_score"])
            b3.metric("Cost efficiency", r["cost_score"])
            b4.metric("Data completeness", r["completeness_score"])

# === Tab: Reconciliation ======================================================
with tab_recon:
    st.subheader("Acquisition reconciliation & integration pipeline")
    st.caption("Sources: `vw_reconciliation_status`, `etl_exceptions`, `vw_integration_pipeline`. "
               "Dirty acquired data is reconciled where safe; the rest waits in an exceptions "
               "queue, and the integration of newer sites is tracked to completion.")
    recon = load_rows("SELECT * FROM vw_reconciliation_status")[0]
    pipeline = load("SELECT * FROM vw_integration_pipeline")
    integrating = int((pipeline["site_status"] == "acquired_integrating").sum())
    below_80 = int((pipeline["completeness_pct"] < 80).sum())
    stalled = int(pipeline["stalled_flag"].sum())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Acquired (reconciled)", recon["acquired_sites"])
    m2.metric("Open exceptions", recon["open_exceptions"])
    m3.metric("In active integration", integrating)
    m4.metric("Below 80% complete", below_80)
    st.markdown(f"**Integration pipeline summary:** {integrating} site(s) in active "
                f"integration, {below_80} below 80% data completeness"
                + (f", **{stalled} stalled** (>12 mo)" if stalled else "") + ".")
    exceptions = load("SELECT source_file, reason FROM etl_exceptions ORDER BY exception_id")
    st.markdown("**Exceptions queue — awaiting a human decision:**")
    st.dataframe(exceptions, use_container_width=True, hide_index=True)
    st.markdown("**Integration pipeline (non-operational sites):**")
    st.dataframe(pipeline, use_container_width=True, hide_index=True)

# === Tab: Quality & Cost ======================================================
with tab_quality:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🧪 Quality by site")
        st.caption("Source view: `vw_quality_by_site_quarter`")
        quality = load("SELECT * FROM vw_quality_by_site_quarter ORDER BY open_count DESC, avg_severity DESC")
        by_site = quality.groupby("site_name").agg(
            open_issues=("open_count", "sum"),
            avg_severity=("avg_severity", "mean"),
            total_issues=("issue_count", "sum")).reset_index().sort_values("open_issues", ascending=False)
        st.bar_chart(by_site.set_index("site_name")["open_issues"])
        st.dataframe(quality, use_container_width=True, hide_index=True)
    with c2:
        st.subheader("💲 Cost per square foot")
        st.caption("Source view: `vw_cost_per_sqft`")
        cost = load("SELECT * FROM vw_cost_per_sqft ORDER BY cost_per_sqft_usd")
        plot = cost.dropna(subset=["cost_per_sqft_usd"])
        st.bar_chart(plot.set_index("site_name")["cost_per_sqft_usd"])
        st.dataframe(cost, use_container_width=True, hide_index=True)

    st.subheader("🪑 Headcount vs. seats")
    st.caption("Source view: `vw_headcount_vs_seats` — over capacity = people with no desk; "
               "under-utilized = paying for empty seats.")
    seats = load("SELECT * FROM vw_headcount_vs_seats ORDER BY quarter, seat_utilization_pct DESC")
    st.dataframe(seats[seats["quarter"] == seats["quarter"].max()],
                 use_container_width=True, hide_index=True)

# === Tab: Report Issue ========================================================
with tab_intake:
    st.subheader("📝 Report a quality issue (30 seconds)")
    st.caption("Writes a structured row straight into `quality_issues` — the same table "
               "the ERP/CMMS feed lands in.")
    sites = load("SELECT site_id, site_name FROM sites ORDER BY site_name")
    latest_q = load_rows("SELECT MAX(quarter) AS q FROM headcount_snapshots")[0]["q"]
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
