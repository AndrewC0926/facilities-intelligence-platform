"""Stakeholder notification drafts — structured, copyable, and never auto-sent."""
from fip import db, notify, scenario


def _live(conn):
    srows = scenario.apply(db.query(conn, "SELECT * FROM vw_capacity_collision"), {})
    crows = db.query(conn, "SELECT * FROM vw_lease_cliff")
    return srows, crows


def test_at_risk_sites_includes_phoenix(built_db):
    conn, _ = built_db
    srows, crows = _live(conn)
    assert "phoenix-line" in notify.at_risk_sites(srows, crows)


def test_alert_has_all_required_fields(built_db):
    conn, _ = built_db
    srows, crows = _live(conn)
    owners = {a["site_id"]: a["owner"] for a in db.query(conn, "SELECT site_id, owner FROM actions")}
    alerts = notify.build_alerts(srows, crows, owners=owners)
    phx = next(a for a in alerts if a["site_id"] == "phoenix-line")
    text = phx["text"]
    for field in ("Site:", "Risk type:", "Binding constraint:", "Decision needed:",
                  "Decision owner:", "Deadline:", "Recommended action:"):
        assert field in text, field
    assert "Phoenix Production Line" in text
    assert "POWER" in text
    assert "VP Facilities" in text          # owner pulled from the action
    assert "FIP sends nothing" in text      # copy-paste only, never auto-send


def test_owner_placeholder_when_unknown(built_db):
    conn, _ = built_db
    srows, crows = _live(conn)
    alerts = notify.build_alerts(srows, crows, owners={})   # no owners supplied
    phx = next(a for a in alerts if a["site_id"] == "phoenix-line")
    assert "[ASSIGN DECISION OWNER]" in phx["text"]
