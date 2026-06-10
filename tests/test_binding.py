"""Multi-constraint binding logic — the collision view must report WHICH ceiling
binds first, and the scenario layer must re-project it live and stay consistent
with the SQL view at 1x."""
from fip import db, scenario


def _by_id(conn):
    return {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_capacity_collision")}


def test_phoenix_binds_on_power_before_floor(built_db):
    conn, _ = built_db
    phx = _by_id(conn)["phoenix-line"]
    # floor story is intact and unchanged
    assert phx["quarters_to_wall"] == 2
    assert phx["projected_breach_quarter"] == "2027-Q1"
    # power hits the wall one quarter sooner -> power is the binding constraint
    assert phx["power_quarters_to_wall"] == 1
    assert phx["power_breach_quarter"] == "2026-Q4"
    assert phx["binding_constraint"] == "power"
    assert phx["binding_breach_quarter"] == "2026-Q4"
    assert phx["binding_status"] == "COLLISION WARNING"


def test_stable_sites_have_no_binding_constraint(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    assert rows["costa-mesa"]["binding_constraint"] == "none"
    # atlanta: both ceilings unknown (mid-buildout) -> pending, never a false breach
    atl = rows["atlanta-campus"]
    assert atl["binding_constraint"] == "none"
    assert atl["binding_status"] == "unknown — capacity data pending"


def test_reconciliation_status_view_reports_two_exceptions(built_db):
    conn, _ = built_db
    rec = db.query(conn, "SELECT * FROM vw_reconciliation_status")[0]
    assert rec["acquired_sites"] == 1
    assert rec["open_exceptions"] == 2     # CAD/USD conflict + tucson-line orphan


def test_project_is_pure_and_matches_the_phoenix_numbers():
    # power: 4,800 kW ceiling, 3,900 kW demand, +260 kW/quarter, last quarter 2026-Q3
    last_q = 2026 * 4 + 3 - 1
    p = scenario.project(4800, 3900, 260, last_q)
    assert p["quarters_to_wall"] == 1
    assert p["breach_quarter"] == "2026-Q4"
    assert p["status"] == "COLLISION WARNING"
    # flat growth -> no collision; unknown capacity -> pending
    assert scenario.project(4800, 3900, 0, last_q)["quarters_to_wall"] is None
    assert scenario.project(None, 3900, 260, last_q)["status"] == "unknown — capacity data pending"


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
    phx_1x = {r["site_id"]: r for r in scenario.apply(base, {})}["phoenix-line"]
    phx_3x = {r["site_id"]: r for r in scenario.apply(base, {"phoenix-line": 3.0})}["phoenix-line"]
    # floor breach moves earlier under faster growth (power was already next quarter)
    assert phx_3x["quarters_to_wall"] < phx_1x["quarters_to_wall"]
    # zero growth -> the site no longer collides at all (binding flips to 'none')
    phx_0x = {r["site_id"]: r for r in scenario.apply(base, {"phoenix-line": 0.0})}["phoenix-line"]
    assert phx_0x["binding_constraint"] == "none"


def test_relocation_recommends_a_same_region_site_with_slack(built_db):
    conn, _ = built_db
    rows = scenario.apply(db.query(conn, "SELECT * FROM vw_capacity_collision"), {})
    rec = scenario.recommend_relocation(rows, "phoenix-line")
    assert rec is not None
    assert rec["constraint"] == "power"        # measured on the binding constraint
    assert rec["same_region"] is True          # Phoenix and Costa Mesa are both West
    assert rec["site_id"] == "costa-mesa"       # most slack in-region
    assert rec["slack"] >= rec["overflow"]
