"""Semantic-layer tests — each view returns rows and is null-safe on dirty data."""
from fip import db

VIEWS = [
    "vw_quality_by_site_quarter",
    "vw_cost_per_sqft",
    "vw_headcount_vs_seats",
    "vw_capacity_vs_demand",
    "vw_capacity_collision",
]


def test_every_view_is_populated(built_db):
    conn, _ = built_db
    for v in VIEWS:
        assert db.query(conn, f"SELECT COUNT(*) c FROM {v}")[0]["c"] > 0, v


def test_cost_per_sqft_null_safe_for_buildout(built_db):
    conn, _ = built_db
    rows = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_cost_per_sqft")}
    # atlanta is mid-buildout with unknown sq_ft -> NULL cost, not a crash
    assert rows["atlanta-campus"]["cost_per_sqft_usd"] is None
    # seattle is the cheap-per-sqft outlier
    assert rows["seattle-ops"]["cost_per_sqft_usd"] < rows["boston-rd"]["cost_per_sqft_usd"]


def test_seats_flags_over_and_under_capacity(built_db):
    conn, _ = built_db
    q4 = {r["site_id"]: r for r in
          db.query(conn, "SELECT * FROM vw_headcount_vs_seats WHERE quarter='2025-Q4'")}
    assert q4["costa-mesa"]["capacity_flag"] == "over capacity"     # ramp blew past seats
    assert q4["boston-rd"]["capacity_flag"] == "under-utilized"     # empty seats


def test_huntsville_is_the_quality_hotspot(built_db):
    conn, _ = built_db
    rows = db.query(conn,
        "SELECT site_id, SUM(open_count) opens FROM vw_quality_by_site_quarter "
        "GROUP BY site_id ORDER BY opens DESC")
    assert rows[0]["site_id"] == "huntsville"
