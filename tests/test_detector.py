"""Capacity-collision detector — the wow moment must fire with a DATED warning."""
from fip import db


def test_phoenix_line_is_flagged_with_a_dated_two_quarter_warning(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_capacity_collision")}
    phx = rows["phoenix-line"]
    assert phx["collision_status"] == "COLLISION WARNING"
    assert phx["quarters_to_wall"] == 2                 # ~2 quarters out
    assert phx["projected_breach_quarter"] == "2026-Q2" # dated, not just "soon"
    assert phx["projected_util_2q_pct"] >= 85.0


def test_stable_sites_do_not_false_alarm(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_capacity_collision")}
    assert rows["costa-mesa"]["collision_status"] in ("stable", "ok")
    # atlanta has demand but unknown sq_ft -> reported as pending, never a false breach
    assert rows["atlanta-campus"]["collision_status"] == "unknown — capacity data pending"
