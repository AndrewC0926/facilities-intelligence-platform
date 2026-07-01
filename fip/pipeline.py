"""
Pipeline orchestrator — one clean build of the whole platform:

    seed CSVs  ->  schema  ->  ETL + reconcile  ->  views  ->  Tableau extracts

Used by `make pipeline`. Builds the DB exactly once. Every run also appends the
CURRENT space-collision predictions to forecast_snapshots so the platform can
score its own forecasts over time (Phase 4, vw_forecast_accuracy).
"""
import datetime
import os

from fip import db, etl, reconcile, export, seed


def append_forecast_snapshot(conn, run_date=None):
    """Append today's space-collision predictions as a forecast snapshot. Idempotent
    per run_date: re-running the same date replaces that date's rows."""
    run_date = run_date or datetime.date.today().isoformat()
    conn.execute("DELETE FROM forecast_snapshots WHERE snapshot_date = ?", (run_date,))
    nid = db.query(conn, "SELECT COALESCE(MAX(snapshot_id), 0) AS n FROM forecast_snapshots")[0]["n"]
    preds = db.query(conn,
        "SELECT site_id, space_type_id, breach_quarter, current_util_pct "
        "FROM vw_space_collision WHERE current_util_pct IS NOT NULL")
    for p in preds:
        nid += 1
        conn.execute(
            "INSERT INTO forecast_snapshots VALUES (?,?,?,?,?,?)",
            (nid, run_date, p["site_id"], p["space_type_id"],
             p["breach_quarter"], p["current_util_pct"]))
    conn.commit()
    return len(preds)


def run():
    # ensure the simulated source exports exist (idempotent)
    if not os.path.exists(os.path.join(seed.SEED_DIR, "sites_master.csv")):
        seed.main()
    conn = db.connect()
    db.apply_schema(conn)
    report = etl.load_all(conn)
    db.apply_views(conn)
    conn.commit()

    append_forecast_snapshot(conn)   # self-scoring: record this run's predictions

    recon_path = reconcile.write_report(conn, report)
    written = export.write_all(conn)
    conn.close()

    print("Pipeline complete:")
    print(f"  • DB:              {os.path.relpath(db.DB_PATH)}")
    print(f"  • reconciliation:  {os.path.relpath(recon_path)} "
          f"({len(report['conflicts']) + len(report['exceptions'])} exceptions)")
    print(f"  • tableau_export:  {len(written)} view extracts")
    return report


if __name__ == "__main__":
    run()
