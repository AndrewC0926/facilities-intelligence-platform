"""Program ↔ facility risk — the collision detector's "so what": which programs
sit behind the most urgent binding constraint, and how far short of target."""
from fip import db


def _rows(conn):
    return db.query(conn, "SELECT * FROM vw_program_facility_risk")


def test_view_has_a_row_per_program(built_db):
    conn, _ = built_db
    rows = _rows(conn)
    assert len(rows) == db.query(conn, "SELECT COUNT(*) c FROM programs")[0]["c"] == 8


def test_most_urgent_constraint_sorts_to_the_top(built_db):
    conn, _ = built_db
    rows = _rows(conn)
    # the top rows are the Arsenal programs — power binds there first (2026-Q4, 1 quarter out)
    top = rows[0]
    assert top["site_id"] == "arsenal-campus"
    assert top["binding_constraint"] == "power"
    assert top["binding_breach_quarter"] == "2026-Q4"
    assert top["quarters_to_constraint"] == 1


def test_arsenal_programs_carry_their_unit_target(built_db):
    conn, _ = built_db
    by_prog = {r["program_name"]: r for r in _rows(conn)}
    # Bolt is a 0 -> 200/qtr ambition sitting behind the Arsenal power wall
    bolt = by_prog["Bolt"]
    assert bolt["site_id"] == "arsenal-campus"
    assert bolt["units_per_quarter_current"] == 0
    assert bolt["units_per_quarter_target"] == 200
    assert bolt["quarters_to_constraint"] == 1


def test_srm_supply_is_a_later_watch(built_db):
    conn, _ = built_db
    by_prog = {r["program_name"]: r for r in _rows(conn)}
    srm = by_prog["SRM Supply"]
    assert srm["site_id"] == "srm-complex"
    assert srm["binding_breach_quarter"] == "2027-Q2"   # further out than Arsenal
    assert srm["quarters_to_constraint"] == 3


def test_programs_at_stable_sites_have_no_constraint(built_db):
    conn, _ = built_db
    by_prog = {r["program_name"]: r for r in _rows(conn)}
    # Lattice OS is software at a healthy site -> no binding facilities constraint
    lattice = by_prog["Lattice OS"]
    assert lattice["program_type"] == "c2_software"
    assert lattice["binding_constraint"] in ("none", None)
    assert lattice["quarters_to_constraint"] is None
    # a software program carries NULL units
    assert lattice["units_per_quarter_current"] is None
