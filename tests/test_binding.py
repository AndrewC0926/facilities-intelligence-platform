"""Multi-constraint binding logic — the collision view must report WHICH ceiling
binds first, and the scenario layer must re-project it live and stay consistent
with the SQL view at 1x."""
from fip import db, scenario


def _by_id(conn):
    return {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_capacity_collision")}


def test_arsenal_binds_on_power_before_floor(built_db):
    conn, _ = built_db
    arsenal = _by_id(conn)["arsenal-campus"]
    # floor story: breaches one quarter after power
    assert arsenal["quarters_to_wall"] == 2
    assert arsenal["projected_breach_quarter"] == "2027-Q1"
    # power hits the wall one quarter sooner -> power is the binding constraint
    assert arsenal["power_quarters_to_wall"] == 1
    assert arsenal["power_breach_quarter"] == "2026-Q4"
    assert arsenal["binding_constraint"] == "power"
    assert arsenal["binding_breach_quarter"] == "2026-Q4"
    assert arsenal["binding_status"] == "COLLISION WARNING"


def test_stable_sites_have_no_binding_constraint(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    assert rows["hq-flagship"]["binding_constraint"] == "none"
    # long-beach: a plan but both ceilings unknown (mid-buildout) -> pending, no false breach
    lb = rows["long-beach"]
    assert lb["binding_constraint"] == "none"
    assert lb["binding_status"] == "unknown — capacity data pending"


def test_reconciliation_status_view_reports_two_exceptions(built_db):
    conn, _ = built_db
    rec = db.query(conn, "SELECT * FROM vw_reconciliation_status")[0]
    assert rec["acquired_sites"] == 1
    assert rec["open_exceptions"] == 2     # CAD/USD conflict + kona-test-range orphan


def test_project_is_pure_and_dates_the_breach():
    # a constraint at 22,400 of 28,000 kW (wall 23,800), +1,400/q, last quarter 2026-Q3
    last_q = 2026 * 4 + 3 - 1
    p = scenario.project(28000, 22400, 1400, last_q)
    assert p["quarters_to_wall"] == 1
    assert p["breach_quarter"] == "2026-Q4"
    assert p["status"] == "COLLISION WARNING"
    # flat growth -> no collision; unknown capacity -> pending
    assert scenario.project(28000, 22400, 0, last_q)["quarters_to_wall"] is None
    assert scenario.project(None, 22400, 1400, last_q)["status"] == "unknown — capacity data pending"


def test_scenario_at_1x_reproduces_the_view(built_db):
    conn, _ = built_db
    base = db.query(conn, "SELECT * FROM vw_capacity_collision")
    applied = {r["site_id"]: r for r in scenario.apply(base, {})}
    fields = ["quarters_to_wall", "projected_breach_quarter", "collision_status",
              "power_quarters_to_wall", "power_breach_quarter", "power_status",
              "binding_constraint", "binding_quarters_to_wall",
              "binding_breach_quarter", "binding_status"]
    for r in base:
        s = applied[r["site_id"]]
        for f in fields:
            assert s[f] == r[f], (r["site_id"], f, s[f], r[f])


def test_higher_multiplier_pulls_the_breach_quarter_in(built_db):
    conn, _ = built_db
    base = db.query(conn, "SELECT * FROM vw_capacity_collision")
    a_1x = {r["site_id"]: r for r in scenario.apply(base, {})}["arsenal-campus"]
    a_3x = {r["site_id"]: r for r in scenario.apply(base, {"arsenal-campus": 3.0})}["arsenal-campus"]
    # floor breach moves earlier under faster growth (power was already next quarter)
    assert a_3x["quarters_to_wall"] < a_1x["quarters_to_wall"]
    # zero growth -> the site no longer collides at all (binding flips to 'none')
    a_0x = {r["site_id"]: r for r in scenario.apply(base, {"arsenal-campus": 0.0})}["arsenal-campus"]
    assert a_0x["binding_constraint"] == "none"


def test_relocation_recommends_a_same_region_site_with_slack(built_db):
    conn, _ = built_db
    rows = scenario.apply(db.query(conn, "SELECT * FROM vw_capacity_collision"), {})
    rec = scenario.recommend_relocation(rows, "arsenal-campus")
    assert rec is not None
    assert rec["constraint"] == "power"        # measured on the binding constraint
    assert rec["same_region"] is True          # Arsenal and HQ are both West
    assert rec["site_id"] == "hq-flagship"      # most power slack in-region
    assert rec["slack"] >= rec["overflow"]
