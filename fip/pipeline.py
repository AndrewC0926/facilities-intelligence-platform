"""
Pipeline orchestrator — one clean build of the whole platform:

    seed CSVs  ->  schema  ->  ETL + reconcile  ->  views  ->  Tableau extracts

Used by `make pipeline`. Builds the DB exactly once.
"""
import os

from fip import db, etl, reconcile, export, seed


def run():
    # ensure the simulated source exports exist (idempotent)
    if not os.path.exists(os.path.join(seed.SEED_DIR, "sites_master.csv")):
        seed.main()
    conn = db.connect()
    db.apply_schema(conn)
    report = etl.load_all(conn)
    db.apply_views(conn)
    conn.commit()

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
