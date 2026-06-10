"""Capacity-collision detector — the wow moment must fire with a DATED warning."""
from fip import db


def test_arsenal_is_flagged_with_a_dated_two_quarter_floor_warning(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_capacity_collision")}
    arsenal = rows["arsenal-campus"]
    # the floor columns describe the FLOOR constraint (breaches one quarter after power)
    assert arsenal["collision_status"] == "COLLISION WARNING"
    assert arsenal["quarters_to_wall"] == 2                 # ~2 quarters out
    assert arsenal["projected_breach_quarter"] == "2027-Q1" # dated, not just "soon"
    assert arsenal["projected_util_2q_pct"] >= 85.0


def test_stable_sites_do_not_false_alarm(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_capacity_collision")}
    assert rows["hq-flagship"]["collision_status"] in ("stable", "ok")
    # long-beach has a production plan but unknown capacity (mid-buildout) ->
    # reported as pending, never a false breach
    assert rows["long-beach"]["collision_status"] == "unknown — capacity data pending"
