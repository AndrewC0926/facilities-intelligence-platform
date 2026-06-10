"""ETL / reconciliation tests — the cleaning is the demo, so we assert it works."""
from fip import db, etl


def test_site_code_canonicalization():
    known = set(etl.CANONICAL_SITES) | {"advanced-imaging"}
    assert etl.canonicalize_code("AIF", known) == "advanced-imaging"
    assert etl.canonicalize_code("Advanced Imaging", known) == "advanced-imaging"
    assert etl.canonicalize_code("Arsenal Campus", known) == "arsenal-campus"
    assert etl.canonicalize_code("kona-test-range", known) is None  # orphan


def test_money_and_quarter_parsing():
    assert etl.parse_money("$1,200,000") == 1200000.0
    assert etl.parse_money("") is None
    assert etl.to_quarter("12/2025") == "2025-Q4"   # the boston-maritime MM/YYYY drift value
    assert etl.to_quarter("2026-Q3") == "2026-Q3"


def test_acquired_site_reconciled_to_single_canonical_row(built_db):
    conn, _ = built_db
    acq = db.query(conn, "SELECT * FROM sites WHERE source_system='acquired_import'")
    assert len(acq) == 1
    assert acq[0]["site_id"] == "advanced-imaging"
    assert acq[0]["sq_ft"] == 34000            # backfilled from the sibling row
    assert acq[0]["power_kw_capacity"] is None  # audit pending -> NULL, not faked
    assert acq[0]["site_status"] == "acquired_integrating"


def test_headcount_dedupe_keeps_resolved_value(built_db):
    conn, report = built_db
    rows = db.query(conn,
        "SELECT headcount FROM headcount_snapshots "
        "WHERE site_id='seattle-hub' AND quarter='2026-Q1' AND program='Lattice OS'")
    assert len(rows) == 1 and rows[0]["headcount"] == 1200   # not the 9999 dupe
    assert any("9999" in d for d in report["dedupes"])


def test_orphan_row_quarantined_not_dropped(built_db):
    conn, report = built_db
    exc = db.query(conn, "SELECT * FROM etl_exceptions")
    assert any("kona-test-range" in e["raw_row"] for e in exc)
    # and it did NOT make it into the fact table
    assert db.query(conn, "SELECT COUNT(*) c FROM quality_issues WHERE site_id='kona-test-range'")[0]["c"] == 0


def test_messy_codes_routed_into_facts(built_db):
    conn, _ = built_db
    # the 'Advanced Imaging' HRIS row and the 'AIF' quality row both landed on advanced-imaging
    assert db.query(conn, "SELECT COUNT(*) c FROM headcount_snapshots WHERE site_id='advanced-imaging'")[0]["c"] >= 1
    assert db.query(conn, "SELECT COUNT(*) c FROM quality_issues WHERE site_id='advanced-imaging'")[0]["c"] >= 1
