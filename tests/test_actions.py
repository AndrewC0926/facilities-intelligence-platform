"""Workflow actions — deterministic age banding and the open-actions roll-up."""
import datetime

from fip import actions, db

TODAY = datetime.date(2026, 6, 10)


def test_age_band_boundaries_are_pure():
    assert actions.band(0) == "green"
    assert actions.band(29) == "green"
    assert actions.band(30) == "yellow"     # 30 is no longer < 30
    assert actions.band(60) == "yellow"
    assert actions.band(61) == "red"
    assert actions.band(None) == "unknown"


def test_age_days_is_relative_to_injected_today():
    assert actions.age_days("2026-03-15", TODAY) == 87     # red
    assert actions.age_days("2026-05-20", TODAY) == 21     # green
    assert actions.age_days(None, TODAY) is None


def test_open_actions_have_owner_due_date_and_band(built_db):
    conn, _ = built_db
    rows = actions.open_actions(conn, TODAY)
    assert len(rows) == 4
    for r in rows:                          # success criterion: every open item is owned + dated
        assert r["owner"]
        assert r["due_date"]
        assert r["age_band"] in ("green", "yellow", "red")
    # oldest-first ordering
    ages = [r["age_days"] for r in rows]
    assert ages == sorted(ages, reverse=True)


def test_phoenix_breach_action_is_the_oldest_and_red(built_db):
    conn, _ = built_db
    rows = {r["action_id"]: r for r in actions.open_actions(conn, TODAY)}
    phx = rows[1]
    assert phx["source"] == "collision"
    assert phx["age_days"] == 87
    assert phx["age_band"] == "red"


def test_orphan_action_has_no_canonical_site(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_open_actions WHERE site_id IS NULL")
    assert len(rows) == 1
    assert "tucson-line" in rows[0]["title"]
    assert rows[0]["site_name"] == "(no canonical site)"


def test_summary_reports_count_and_oldest_age(built_db):
    conn, _ = built_db
    s = actions.summary(conn, TODAY)
    assert s["open_count"] == 4
    assert s["oldest_age_days"] == 87
    assert s["oldest_site"] == "Phoenix Production Line"


def test_view_excludes_resolved_actions(built_db):
    conn, _ = built_db
    statuses = {r["status"] for r in db.query(conn, "SELECT status FROM vw_open_actions")}
    assert statuses <= {"open", "in_progress"}
