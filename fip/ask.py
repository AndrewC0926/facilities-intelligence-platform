"""
ask.py — scrappy, parameterized one-off queries against the semantic layer.

This is the "scrappy analysis on a short timeline" tool: a VP asks a question in
the hallway, you answer it in one line.

    python -m fip.ask quality --site huntsville
    python -m fip.ask cost
    python -m fip.ask seats --site costa-mesa --quarter 2025-Q4
    python -m fip.ask collision

And the "scalable long-term solution" move — promote any one-off into a permanent,
named view that the dashboard and Tableau can both use, in one command:

    python -m fip.ask collision --promote at_risk_sites

`--promote` appends a real CREATE VIEW to sql/views.sql and registers it, so the
scrappy query becomes part of the product.
"""
import argparse
import datetime
import re

from fip import db

# domain -> (view, allowed filter columns)
DOMAINS = {
    "quality":  ("vw_quality_by_site_quarter", {"site": "site_id", "quarter": "quarter"}),
    "cost":     ("vw_cost_per_sqft",           {"site": "site_id"}),
    "seats":    ("vw_headcount_vs_seats",      {"site": "site_id", "quarter": "quarter"}),
    "capacity": ("vw_capacity_vs_demand",      {"site": "site_id", "quarter": "quarter"}),
    "collision":("vw_capacity_collision",      {"site": "site_id", "status": "collision_status"}),
}

_SAFE = re.compile(r"^[A-Za-z0-9 _\-]+$")


def _build_sql(domain, filters):
    """Return a complete SELECT (literals inlined) so the same string can both run
    and be promoted verbatim into a view. Inputs are validated to simple tokens."""
    view, allowed = DOMAINS[domain]
    clauses = []
    for key, col in allowed.items():
        val = filters.get(key)
        if val:
            if not _SAFE.match(val):
                raise SystemExit(f"refusing unsafe filter value: {val!r}")
            clauses.append(f"{col} = '{val}'")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return f"SELECT * FROM {view}{where}"


def _print_rows(rows):
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def _promote(name, sql, domain, filters):
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        raise SystemExit("promote name must be lowercase letters/digits/underscores")
    view_name = f"vw_{name}"
    stamp = datetime.date.today().isoformat()
    desc = f"{domain}" + (f" filtered by {filters}" if any(filters.values()) else "")
    block = (
        f"\n\n-- -----------------------------------------------------------------------------\n"
        f"-- {view_name}   (promoted from ask.py on {stamp})\n"
        f"-- BUSINESS QUESTION : ad-hoc query promoted to a permanent view\n"
        f"-- SOURCE            : ask.py {desc}\n"
        f"-- REFRESH CADENCE   : inherits its base view's cadence\n"
        f"-- -----------------------------------------------------------------------------\n"
        f"DROP VIEW IF EXISTS {view_name};\n"
        f"CREATE VIEW {view_name} AS\n{sql};\n"
    )
    with open(db.VIEWS_SQL, "a") as f:
        f.write(block)
    conn = db.connect()
    db.apply_views(conn)
    conn.commit()
    conn.close()
    print(f"✓ promoted to permanent view '{view_name}' in {db.VIEWS_SQL.split('/')[-1]} "
          f"and registered in the database.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="ask", description="Scrappy one-off facilities queries.")
    p.add_argument("domain", choices=DOMAINS.keys())
    p.add_argument("--site")
    p.add_argument("--quarter")
    p.add_argument("--status")
    p.add_argument("--promote", metavar="NAME",
                   help="promote this query into a permanent view vw_NAME")
    args = p.parse_args(argv)

    filters = {"site": args.site, "quarter": args.quarter, "status": args.status}
    sql = _build_sql(args.domain, filters)

    conn = db.connect()
    _print_rows(db.query(conn, sql))
    conn.close()

    if args.promote:
        _promote(args.promote, sql, args.domain, filters)


if __name__ == "__main__":
    main()
