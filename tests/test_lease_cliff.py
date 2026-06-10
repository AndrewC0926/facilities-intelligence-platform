"""Lease cliff calendar — decision_window_days and the AT RISK flag."""
from fip import db


def _by_id(conn):
    return {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_lease_cliff")}


def test_phoenix_is_at_risk_with_a_60_day_window(built_db):
    conn, _ = built_db
    phx = _by_id(conn)["phoenix-line"]
    # option deadline 2025-11-02, binding (power) breach 2026-Q1 -> 2026-01-01
    assert phx["binding_breach_quarter"] == "2026-Q1"
    assert phx["breach_date"] == "2026-01-01"
    assert phx["decision_window_days"] == 60       # 60 days of runway
    assert phx["cliff_status"] == "AT RISK"         # < 180 days


def test_owned_sites_have_no_lease_cliff(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    for sid in ("costa-mesa", "atlanta-campus"):
        assert rows[sid]["lease_option_deadline"] is None
        assert rows[sid]["decision_window_days"] is None
        assert rows[sid]["cliff_status"] == "no lease cliff"


def test_leased_sites_without_a_breach_are_not_at_risk(built_db):
    conn, _ = built_db
    rows = _by_id(conn)
    # austin has a lease option deadline but no projected collision -> no window, not AT RISK
    austin = rows["austin-fab"]
    assert austin["lease_option_deadline"] is not None
    assert austin["binding_breach_quarter"] is None
    assert austin["decision_window_days"] is None
    assert austin["cliff_status"] == "no breach projected"


def test_only_phoenix_is_flagged_at_risk(built_db):
    conn, _ = built_db
    at_risk = [r["site_id"] for r in db.query(conn, "SELECT * FROM vw_lease_cliff")
               if r["cliff_status"] == "AT RISK"]
    assert at_risk == ["phoenix-line"]
