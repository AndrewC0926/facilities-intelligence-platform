"""Exec brief generator — must render a dated one-pager from the live views with
the headline risk, the binding-constraint forecast, the reconciliation count, and
recommended actions."""
import datetime

from fip import brief


def test_brief_renders_all_sections(built_db):
    conn, _ = built_db
    md = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    for section in ("# Facilities Intelligence — Executive Brief",
                    "## Headline risk",
                    "## Capacity collision forecast",
                    "## Acquisition reconciliation",
                    "## Recommended actions"):
        assert section in md, section


def test_brief_is_dated(built_db):
    conn, _ = built_db
    md = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    assert "2026-06-10" in md


def test_brief_headline_names_the_binding_constraint(built_db):
    conn, _ = built_db
    md = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    assert "Phoenix Production Line" in md
    assert "POWER" in md
    assert "2026-Q1" in md          # the dated binding breach, not the floor 2026-Q2


def test_brief_reports_two_reconciliation_exceptions(built_db):
    conn, _ = built_db
    md = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    assert "**2** item(s) in the exceptions queue" in md
    assert "tucson-line" in md       # the orphan is named
    assert "CAD" in md               # the currency conflict is named


def test_brief_recommends_relocation(built_db):
    conn, _ = built_db
    md = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    assert "Relocation option" in md
    assert "Costa Mesa" in md


def test_brief_scenario_note_appears_under_multiplier(built_db):
    conn, _ = built_db
    plain = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    assert "Scenario applied" not in plain
    scen = brief.render(conn=conn, today=datetime.date(2026, 6, 10),
                        multipliers={"phoenix-line": 3.0})
    assert "Scenario applied" in scen


def test_brief_action_tracker_reports_count_and_oldest_age(built_db):
    conn, _ = built_db
    md = brief.render(conn=conn, today=datetime.date(2026, 6, 10))
    assert "## Action tracker" in md
    assert "**4** open action(s)" in md
    assert "**87 days**" in md          # oldest unresolved item age, relative to the fixed today
