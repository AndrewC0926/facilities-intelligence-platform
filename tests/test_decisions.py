"""Phase 5 decision layer — material changes, last-responsible-moment math, the
idempotent auto-queue, urgency bands, and the decision-latency KPI. Assert patterns."""
import datetime

from fip import db, decide, etl


def _fresh(tmp_path, name="d.db"):
    conn = db.connect(str(tmp_path / name))
    db.apply_schema(conn)
    etl.load_all(conn)
    db.apply_views(conn)
    return conn


# --- 1. material changes: exactly the seeded deltas -----------------------------

def test_material_changes_reports_exactly_the_seeded_deltas(built_db):
    conn, _ = built_db
    rows = db.query(conn, "SELECT * FROM vw_material_changes")
    got = {(r["site_id"], r["what_changed"], r["direction"]) for r in rows}
    assert got == {
        ("arsenal-campus", "breach_quarter", "worse"),   # 2027-Q1 -> 2026-Q4 (pulled earlier)
        ("srm-complex", "breach_cleared", "better"),      # 2026-Q4 -> none (breach cleared)
        ("boston-maritime", "utilization_band", "worse"), # 70% -> 92% (crossed above the band)
    }
    # both a worse and a better row are present
    assert {r["direction"] for r in rows} >= {"worse", "better"}
    # sites whose forecast did not move are not reported
    assert not any(r["site_id"] in ("hq-flagship", "seattle-hub") for r in rows)


def test_material_changes_empty_with_a_single_snapshot_date(tmp_path):
    conn = _fresh(tmp_path)
    latest = db.query(conn, "SELECT MAX(snapshot_date) m FROM forecast_snapshots")[0]["m"]
    conn.execute("DELETE FROM forecast_snapshots WHERE snapshot_date <> ?", (latest,))
    conn.commit()
    assert db.query(conn, "SELECT COUNT(*) c FROM vw_material_changes")[0]["c"] == 0  # never an error
    conn.close()


# --- 2. the decide-by date is physics: breach start minus lead time --------------

def test_decide_by_equals_breach_start_minus_lead_time(built_db):
    conn, _ = built_db
    for r in db.query(conn, "SELECT * FROM vw_last_responsible_moment"):
        y = int(r["breach_quarter"][:4]); q = int(r["breach_quarter"][6])
        breach_start = datetime.date(y, (q - 1) * 3 + 1, 1)
        expected = breach_start - datetime.timedelta(days=r["lead_time_days"])
        assert r["breach_date"] == breach_start.isoformat()
        assert r["decide_by_date"] == expected.isoformat(), r["site_id"]


# --- 3. the auto-queue is idempotent and agrees with the view --------------------

def test_auto_queue_is_idempotent_and_uses_the_view_deadline(tmp_path):
    conn = _fresh(tmp_path)
    lrm = {(r["site_id"], r["space_type_id"]): r
           for r in db.query(conn, "SELECT * FROM vw_last_responsible_moment")}
    before = {r["decision_id"] for r in db.query(conn, "SELECT decision_id FROM decisions")}

    first = decide.ensure_collision_decisions(conn)
    second = decide.ensure_collision_decisions(conn)
    third = decide.ensure_collision_decisions(conn)
    assert first > 0 and second == 0 and third == 0     # idempotent after the first run

    # never more than one OPEN decision per site + space + source
    dupes = db.query(conn,
        "SELECT COUNT(*) c FROM (SELECT site_id, space_type_id, source FROM decisions "
        "WHERE decided_at IS NULL AND source='collision' GROUP BY site_id, space_type_id, source "
        "HAVING COUNT(*) > 1)")
    assert dupes[0]["c"] == 0

    # the AUTO-QUEUED decisions (the new ones) carry the last-responsible-moment date
    new_rows = [d for d in db.query(conn, "SELECT * FROM decisions")
                if d["decision_id"] not in before]
    assert len(new_rows) == first
    for d in new_rows:
        key = (d["site_id"], d["space_type_id"])
        assert key in lrm and d["source"] == "collision" and d["decided_at"] is None
        assert d["decide_by_date"] == lrm[key]["decide_by_date"]   # agree by construction
    conn.close()


# --- 4. urgency bands fire on the seeded dates -----------------------------------

def test_urgency_bands(built_db):
    conn, _ = built_db
    q = {r["site_id"]: r for r in db.query(conn, "SELECT * FROM vw_decision_queue")}
    assert q["seattle-hub"]["urgency"] == "OVERDUE"     # decide-by 2026-05-01 (past)
    assert q["long-beach"]["urgency"] == "OPEN"          # decide-by 2027-03-01 (far)
    # the decided decision is not on the queue at all
    assert "arsenal-campus" not in q
    # the band is an exact function of days_remaining for every row
    for r in q.values():
        d = r["days_remaining"]
        expected = "OVERDUE" if d < 0 else "CLOSING" if d <= 30 else "OPEN"
        assert r["urgency"] == expected, (r["site_id"], d, r["urgency"])


# --- 5. the decision-latency KPI row ---------------------------------------------

def test_decision_latency_kpi_row(built_db):
    conn, _ = built_db
    row = db.query(conn, "SELECT * FROM vw_kpi_scorecard WHERE kpi_key='decision_latency'")
    assert len(row) == 1
    r = row[0]
    # one decided decision: 2026-01-10 -> 2026-02-20 = 41 days; median of one = 41
    assert r["value"] == 41
    assert r["unit"] == "days"
    assert "overdue" in r["detail"]
