"""Integration pipeline — non-operational sites, their data completeness, and the
>12-month-and-still-incomplete stalled flag."""
from fip import db


def _by_id(conn):
    return {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_integration_pipeline")}


def test_only_non_operational_sites_appear(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # buildout + acquired_integrating sites only
    assert set(rows) == {"long-beach", "advanced-imaging", "space-domain"}
    # an operational site and an acquired_COMPLETE site are NOT in the pipeline
    assert "arsenal-campus" not in rows
    assert "composites-uav" not in rows


def test_completeness_and_null_counts_are_exact(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # long-beach (buildout): every critical field NULL -> 0%
    assert rows["long-beach"]["null_critical_fields"] == 5
    assert rows["long-beach"]["completeness_pct"] == 0.0
    # advanced-imaging (dirty acquired): only sq_ft backfilled -> 1 of 5 -> 20%
    assert rows["advanced-imaging"]["completeness_pct"] == 20.0
    # space-domain: sq_ft + seats + power present, lease dates pending -> 3 of 5 -> 60%
    assert rows["space-domain"]["completeness_pct"] == 60.0


def test_young_integrations_below_80pct_are_not_yet_stalled(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # space-domain is below 80% complete but only ~6 months in -> not stalled (age gate)
    assert rows["space-domain"]["completeness_pct"] < 80
    assert rows["space-domain"]["stalled_flag"] == 0
    # long-beach has no integration clock (buildout) -> never stalled
    assert rows["long-beach"]["stalled_flag"] == 0
    # nothing is stalled yet in this portfolio
    assert sum(r["stalled_flag"] for r in rows.values()) == 0
