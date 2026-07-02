"""Phase 4 accountability layer — assert each PATTERN: self-scoring, cost of delay,
incentive window, accreditation-driven confirm flip, day-one readiness, the change
diff's blast radius, and a complete KPI scorecard. (Config as data; no site special-cased.)"""
from fip import db, diff


# --- 1. forecast self-scoring across the two seeded run dates --------------------

def test_forecast_accuracy_scores_aged_snapshots(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_forecast_accuracy")}
    # the older run (2026-01-15) is scored against the newer 'actual' (2026-04-15)
    assert all(r["forecast_date"] < r["actual_date"] for r in rows.values())
    # arsenal parking forecast the wall a quarter late -> a miss with 1-quarter error
    arsenal = rows["arsenal-campus"]
    assert arsenal["error_quarters"] == 1
    assert arsenal["outcome"] == "miss"
    # hq SCIF forecast the right quarter -> a hit
    assert rows["hq-flagship"]["outcome"] == "hit"
    assert rows["hq-flagship"]["error_quarters"] == 0
    # both hits and misses are present -> accuracy is genuinely computed
    outcomes = {r["outcome"] for r in rows.values()}
    assert "hit" in outcomes and "miss" in outcomes


# --- 2. cost of delay: act-now vs act-at-the-wall --------------------------------

def test_cost_of_delay_prices_the_wait(built_db):
    conn, _ = built_db
    r = db.query(conn, "SELECT * FROM vw_cost_of_delay WHERE site_id='arsenal-campus'")[0]
    assert r["cost_act_now_usd"] == 3500000
    # at the wall = remedy + delay/quarter * quarters_to_wall
    assert r["cost_act_at_wall_usd"] == 3500000 + 1200000 * r["quarters_to_wall"]
    assert r["cost_of_delay_usd"] == 1200000 * r["quarters_to_wall"]


# --- 3. incentive flag fires IFF shortfall inside the window ---------------------

def test_incentive_flag_fires_iff_shortfall_in_window(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_incentive_compliance")
    assert rows
    for r in rows:
        has_shortfall = (r["jobs_shortfall"] > 0) or (r["capex_shortfall_usd"] > 0)
        in_window = r["days_to_measurement"] < 180
        if r["compliance_status"] == "AT RISK":
            assert has_shortfall and in_window, r["site_id"]
        if not has_shortfall:
            assert r["compliance_status"] == "met", r["site_id"]
    by_id = {r["site_id"]: r for r in rows}
    assert by_id["long-beach"]["compliance_status"] == "AT RISK"   # 100/500 jobs, ~75 days out
    assert by_id["arsenal-campus"]["compliance_status"] == "met"   # jobs + capex both met


# --- 4. capacity flips confirmed ONLY on the final accreditation actual_date -----

def test_capacity_flips_confirmed_only_on_final_milestone(built_db):
    conn, _ = built_db
    eff = {(r["site_id"], r["space_type"]): r
           for r in db.query(conn, "SELECT * FROM vw_space_capacity_effective")}
    # composites SCIF: audit_pending BUT fully accredited -> flips to confirmed
    comp = eff[("composites-uav", "scif_seat")]
    assert comp["raw_status"] == "audit_pending" and comp["accreditation_complete"] == 1
    assert comp["effective_status"] == "confirmed"
    # space-domain SCIF: audit_pending, final milestone NOT met -> stays unconfirmed
    sd = eff[("space-domain", "scif_seat")]
    assert sd["accreditation_complete"] == 0
    assert sd["effective_status"] == "audit_pending"
    # the invariant across every row: a pending/planned row is confirmed iff accredited
    for r in db.query(conn, "SELECT * FROM vw_space_capacity_effective"):
        if r["raw_status"] in ("planned", "audit_pending"):
            assert (r["effective_status"] == "confirmed") == (r["accreditation_complete"] == 1)


def test_accreditation_pipeline_reports_stage_and_slip(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_accreditation_pipeline")}
    assert rows["composites-uav"]["accreditation_complete"] == 1
    assert rows["composites-uav"]["current_stage"] == "accreditation"
    assert rows["space-domain"]["current_stage"] == "construction"
    assert rows["space-domain"]["next_stage"] == "inspection"
    assert rows["space-domain"]["max_slip_days"] > 0


# --- 5. day-one readiness flag fires exactly where seeded ------------------------

def test_readiness_flag_fires_where_seeded(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_day_one_readiness")}
    # arsenal cohort is one quarter out and missing equipment -> flagged
    ars = rows["arsenal-campus"]
    assert ars["is_imminent"] == 1 and ars["readiness_flag"] == "NOT READY"
    assert "equipment" in ars["not_ready_dimensions"]
    # hq cohort is one quarter out and fully ready -> not flagged
    assert rows["hq-flagship"]["is_imminent"] == 1
    assert rows["hq-flagship"]["readiness_flag"] == "ok"
    # seattle cohort is far out -> not imminent, never flagged
    assert rows["seattle-hub"]["is_imminent"] == 0
    assert rows["seattle-hub"]["readiness_flag"] == "ok"
    # portfolio: 1 of 2 imminent cohorts fully ready
    assert rows["arsenal-campus"]["portfolio_pct_fully_ready"] == 50.0


# --- 6. the change diff moves only the affected site ----------------------------

def test_diff_moves_only_the_affected_site(built_db):
    conn, _ = built_db
    rows, meta = diff.compute(conn, "Fury CCA", 18)   # cut Fury -> Arsenal's wall slips
    changed = [r["site_id"] for r in rows if r["changed"]]
    assert changed == ["arsenal-campus"] == [meta["site"]]
    arsenal = next(r for r in rows if r["site_id"] == "arsenal-campus")
    assert arsenal["breach_before"] != arsenal["breach_after"]           # the breach moved
    assert arsenal["cost_of_delay_before"] != arsenal["cost_of_delay_after"]
    # every other site is byte-identical before/after
    for r in rows:
        if r["site_id"] != "arsenal-campus":
            assert r["breach_before"] == r["breach_after"]
            assert r["constraint_before"] == r["constraint_after"]
            assert r["cost_of_delay_before"] == r["cost_of_delay_after"]


def test_diff_no_op_when_target_unchanged(built_db):
    conn, _ = built_db
    rows, _ = diff.compute(conn, "Fury CCA", 37)   # target == baseline -> nothing moves
    assert not any(r["changed"] for r in rows)


# --- 7. KPI scorecard is complete (every KPI row non-null) -----------------------

def test_kpi_scorecard_returns_every_kpi_non_null(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_kpi_scorecard")
    # the six original KPIs are unchanged and still present, plus the Phase 5 latency row
    original_six = {"worst_case_seat_gap", "forecast_accuracy", "actions_with_lead_time",
                    "util_corridor_compliance", "day_one_readiness", "plan_reconciliation_gaps"}
    keys = {r["kpi_key"] for r in rows}
    assert original_six <= keys                       # existing rows unchanged / still present
    assert keys == original_six | {"decision_latency"}  # exactly one new row added
    for r in rows:
        assert r["value"] is not None, r["kpi_key"]
        assert r["status"] is not None and r["status"] != ""
        assert r["kpi_label"] and r["detail"] is not None
