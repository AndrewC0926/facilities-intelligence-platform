"""Site health score — composite 0-100 from four equally-weighted components,
with buildout / acquired_integrating sites EXEMPT from the completeness penalty."""
from fip import db

COMPONENTS = ["capacity_score", "quality_score", "cost_score", "completeness_score"]


def _by_id(conn):
    return {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_site_health")}


def test_every_site_scored_and_in_range(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_site_health")
    assert len(rows) == 10
    for r in rows:
        for col in COMPONENTS + ["health_score"]:
            assert r[col] is not None, (r["site_id"], col)
            assert 0.0 <= r[col] <= 100.0, (r["site_id"], col, r[col])


def test_composite_is_the_average_of_the_four_components(built_db):
    conn, _ = built_db
    for r in db.query(conn, "SELECT * FROM vw_site_health"):
        # the view averages the UNROUNDED components, so reconstructing from the
        # rounded display values can drift by up to ~0.2 at a .x5 boundary
        expected = sum(r[c] for c in COMPONENTS) / 4.0
        assert abs(r["health_score"] - expected) < 0.2, (r["site_id"], r["health_score"], expected)


def test_integrating_and_buildout_sites_are_exempt_from_completeness_penalty(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # advanced-imaging (acquired_integrating) has NULL power + seats, and long-beach
    # (buildout) is almost entirely NULL — but neither is penalized on the health
    # score: expected NULLs, not negligence.
    assert rows["advanced-imaging"]["completeness_score"] == 100.0
    assert rows["long-beach"]["completeness_score"] == 100.0
    # their REAL (low) completeness is surfaced separately in the integration pipeline
    pipe = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_integration_pipeline")}
    assert pipe_completeness(pipe, "advanced-imaging") < 80
    assert pipe_completeness(pipe, "long-beach") < 80
    # a fully-populated operational site also scores 100 (nothing missing)
    assert rows["hq-flagship"]["completeness_score"] == 100.0


def pipe_completeness(pipe, sid):
    return pipe[sid]["completeness_pct"]


def test_quality_component_flags_the_hotspot(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # maritime-systems is the quality hotspot -> lowest quality component (floored at 0)
    assert rows["maritime-systems"]["quality_score"] == 0.0
    assert rows["maritime-systems"]["quality_score"] < rows["hq-flagship"]["quality_score"]


def test_cost_component_uses_portfolio_median(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # the small office is the expensive outlier (above median) -> low cost score
    assert rows["boston-maritime"]["cost_score"] < rows["hq-flagship"]["cost_score"]
    # the mega-factory is cheap per sq ft (at/below median) -> full marks
    assert rows["arsenal-campus"]["cost_score"] == 100.0
    # long-beach has unknown sq_ft -> no cost data -> 0 (can't credit what you can't see)
    assert rows["long-beach"]["cost_score"] == 0.0


def test_overall_ranking_is_sane(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    healthiest = max(rows.values(), key=lambda r: r["health_score"])["site_id"]
    weakest = min(rows.values(), key=lambda r: r["health_score"])["site_id"]
    # a well-rounded operational/complete site leads; the buildout shell trails
    assert healthiest in ("composites-uav", "hq-flagship", "space-domain")
    assert weakest == "long-beach"
    # the capacity-crunched flagship scores below the headroom-rich HQ
    assert rows["arsenal-campus"]["health_score"] < rows["hq-flagship"]["health_score"]
