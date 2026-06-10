"""Site health score — composite 0-100 from four equally-weighted components."""
from fip import db

COMPONENTS = ["capacity_score", "quality_score", "cost_score", "completeness_score"]


def _by_id(conn):
    return {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_site_health")}


def test_every_site_scored_and_in_range(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_site_health")
    assert len(rows) == 8
    for r in rows:
        for col in COMPONENTS + ["health_score"]:
            assert r[col] is not None, (r["site_id"], col)
            assert 0.0 <= r[col] <= 100.0, (r["site_id"], col, r[col])


def test_composite_is_the_average_of_the_four_components(built_db):
    conn, _ = built_db
    for r in db.query(conn, "SELECT * FROM vw_site_health"):
        expected = round(sum(r[c] for c in COMPONENTS) / 4.0, 1)
        assert abs(r["health_score"] - expected) < 0.05, (r["site_id"], r["health_score"], expected)


def test_data_completeness_penalizes_incomplete_sites(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # atlanta (buildout): sq_ft, seat_capacity, power_kw_capacity all NULL -> 2/5 = 40
    assert rows["atlanta-campus"]["completeness_score"] == 40.0
    # quantico-acq: only power_kw_capacity NULL -> 4/5 = 80
    assert rows["quantico-acq"]["completeness_score"] == 80.0
    # a clean canonical site -> all 5 present -> 100
    assert rows["costa-mesa"]["completeness_score"] == 100.0


def test_quality_component_flags_the_hotspot(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # huntsville is the quality hotspot -> lowest quality component (floored at 0)
    assert rows["huntsville"]["quality_score"] == 0.0
    assert rows["huntsville"]["quality_score"] < rows["austin-fab"]["quality_score"]


def test_cost_component_uses_portfolio_median(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # boston is the expensive outlier (well above median) -> low cost score
    assert rows["boston-rd"]["cost_score"] < rows["seattle-ops"]["cost_score"]
    # seattle is cheap (below median) -> full marks
    assert rows["seattle-ops"]["cost_score"] == 100.0


def test_overall_ranking_is_sane(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # the capacity-crunched and the data-incomplete sites land at the bottom
    healthiest = max(rows.values(), key=lambda r: r["health_score"])["site_id"]
    weakest = min(rows.values(), key=lambda r: r["health_score"])["site_id"]
    assert healthiest in ("seattle-ops", "costa-mesa", "austin-fab")
    assert weakest == "atlanta-campus"
    assert rows["phoenix-line"]["health_score"] < rows["costa-mesa"]["health_score"]
