"""
Decision auto-queue — the pipeline step that turns physics into a to-do.

For every at-risk space collision, vw_last_responsible_moment already computes the
last day we can still decide and have the fix land before the wall (breach date
minus the space lead time). This module queues a decision for each such collision
that does not already have one open, using that same decide_by_date, so the queue
and the view agree by construction.

It is idempotent: it never opens a second decision for the same site + space_type +
source while one is still open. Called once per pipeline run.
"""
import datetime

from fip import db


def ensure_collision_decisions(conn, created_at=None):
    """Insert an open 'collision' decision for each at-risk binding space that has no
    open collision decision yet. Returns the number of decisions created."""
    created_at = created_at or datetime.date.today().isoformat()
    at_risk = db.query(conn, "SELECT * FROM vw_last_responsible_moment")
    created = 0
    for r in at_risk:
        existing = db.query(
            conn,
            "SELECT 1 FROM decisions WHERE site_id = ? AND space_type_id = ? "
            "AND source = 'collision' AND decided_at IS NULL LIMIT 1",
            (r["site_id"], r["space_type_id"]))
        if existing:
            continue
        nid = db.query(conn, "SELECT COALESCE(MAX(decision_id), 0) AS n FROM decisions")[0]["n"] + 1
        title = (f"{r['space_type']} wall at {r['site_name']}: decide the fix before "
                 f"{r['breach_quarter']}")
        options = "expand / relocate demand / accept the risk"
        conn.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (nid, r["site_id"], r["space_type_id"], "collision", title, options,
             None, r["decide_by_date"], None, None, created_at))
        created += 1
    conn.commit()
    return created


if __name__ == "__main__":
    conn = db.connect()
    try:
        n = ensure_collision_decisions(conn)
        print(f"Queued {n} new collision decision(s).")
    finally:
        conn.close()
