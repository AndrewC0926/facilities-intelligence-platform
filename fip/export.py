"""
Tableau handoff — emit one clean CSV per semantic view into tableau_export/.

The demo line: "this folder is exactly what I'd point Tableau at on day one."
In production you'd point Tableau at the live views; the CSVs are the same shape,
so a Tableau extract and our dashboard are reading identical columns.
"""
import csv
import os

from fip import db

EXPORT_DIR = os.path.join(db.ROOT, "tableau_export")

# The semantic views that make up the published "data source" for Tableau.
PUBLISHED_VIEWS = [
    "vw_quality_by_site_quarter",
    "vw_cost_per_sqft",
    "vw_headcount_vs_seats",
    "vw_capacity_vs_demand",
    "vw_capacity_collision",
    "vw_reconciliation_status",
    "vw_open_actions",
    "vw_lease_cliff",
    "vw_site_health",
    "vw_program_facility_risk",
    "vw_integration_pipeline",
    "vw_space_demand",
    "vw_space_collision",
    "vw_time_to_seat",
    "vw_plan_reconciliation",
    "vw_space_capacity_effective",
    "vw_accreditation_pipeline",
    "vw_forecast_accuracy",
    "vw_cost_of_delay",
    "vw_incentive_compliance",
    "vw_day_one_readiness",
    "vw_kpi_scorecard",
]


def write_all(conn, export_dir=EXPORT_DIR):
    os.makedirs(export_dir, exist_ok=True)
    written = []
    for view in PUBLISHED_VIEWS:
        rows = db.query(conn, f"SELECT * FROM {view}")
        path = os.path.join(export_dir, f"{view}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            if rows:
                w.writerow(rows[0].keys())
                for r in rows:
                    w.writerow(r.values())
            else:
                w.writerow(["(no rows)"])
        written.append((view, len(rows), path))
    return written


def main():
    conn = db.connect()
    written = write_all(conn)
    print(f"Wrote {len(written)} view extracts to {os.path.relpath(EXPORT_DIR)}/:")
    for view, n, _ in written:
        print(f"  • {view}.csv ({n} rows)")
    conn.close()


if __name__ == "__main__":
    main()
