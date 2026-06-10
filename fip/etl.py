"""
ETL — ingest the messy source-system exports in seeds/ and reconcile them into
the canonical schema. This is the "work deeply in business systems" muscle.

What it cleans, and how:
  • Site codes      — a canonicalization map collapses 'QNTC' / 'quantico' /
                      'Quantico Acq.' onto the canonical id 'quantico-acq'.
  • Currency/format — rent like "$1,200,000" -> 1200000.0; CAD -> USD at a
                      documented rate.
  • Dates           — '03/2025' (MM/YYYY) -> '2025-Q1'; 'YYYY-Qn' passes through.
  • Duplicates      — conflicting headcount rows (same site+quarter+program) are
                      de-duped, keeping the last (resolved) value.
  • Orphans         — a fact row whose site code matches no known site is
                      QUARANTINED into etl_exceptions, never silently dropped.

`load_all()` returns a structured report so the reconciliation step (and the
tests) can see exactly what was changed.
"""
import csv
import os

from fip import db

SEED_DIR = os.path.join(db.ROOT, "seeds")

# CAD -> USD conversion used when an acquired record is denominated in CAD.
CAD_TO_USD = 0.73

# Canonical site ids the platform recognizes (the 7 clean ones; quantico-acq is
# added from the acquired dump during load_sites).
CANONICAL_SITES = [
    "costa-mesa", "atlanta-campus", "austin-fab", "huntsville",
    "boston-rd", "seattle-ops", "phoenix-line",
]

# Explicit aliases for codes that don't normalize cleanly to a canonical id.
SITE_ALIASES = {
    "qntc": "quantico-acq",
    "quantico": "quantico-acq",
    "quantico acq.": "quantico-acq",
    "quantico acquisition": "quantico-acq",
    "quantico-acq": "quantico-acq",
}


def _read_csv(name):
    with open(os.path.join(SEED_DIR, name), newline="") as f:
        return list(csv.DictReader(f))


def canonicalize_code(raw, known):
    """Map a raw site code to a canonical site_id, or None if it matches nothing."""
    if raw is None:
        return None
    key = raw.strip().lower()
    if key in SITE_ALIASES:
        return SITE_ALIASES[key]
    if key in known:
        return key
    # tolerate spacing/punctuation drift, e.g. 'Costa Mesa' -> 'costa-mesa'
    slug = key.replace(" ", "-").replace(".", "").replace("_", "-")
    return slug if slug in known else None


def parse_money(raw):
    """'$1,200,000' / '1,500,000' / '' -> float or None."""
    if raw is None:
        return None
    s = str(raw).strip().replace("$", "").replace(",", "")
    return float(s) if s else None


def parse_int(raw):
    s = (str(raw).strip() if raw is not None else "")
    return int(float(s)) if s else None


def to_quarter(raw):
    """Normalize a date-ish string to 'YYYY-Qn'. Handles 'YYYY-Qn' and 'MM/YYYY'."""
    s = str(raw).strip()
    if "-Q" in s:
        return s
    if "/" in s:                       # MM/YYYY -> quarter
        mm, yyyy = s.split("/")
        q = (int(mm) - 1) // 3 + 1
        return f"{yyyy}-Q{q}"
    return s


def load_sites(conn, report):
    """Load the clean canonical sites, then reconcile the acquired dump into one
    canonical quantico-acq site + lease."""
    for r in _read_csv("sites_master.csv"):
        conn.execute(
            "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?)",
            (r["site_id"], r["site_name"], r["region"],
             parse_int(r["sq_ft"]), parse_int(r["seat_capacity"]),
             r["site_type"], r["status"], r["source_system"]),
        )

    # --- reconcile acquired_site_dump.csv (two rows, one real site) -------------
    dump = _read_csv("acquired_site_dump.csv")
    report["acquired_codes"] = [r["facility_code"] for r in dump]
    sq_ft = seats = name = region = None
    rent_usd = opex_usd = None
    for r in dump:
        report["actions"].append(
            f"acquired code '{r['facility_code']}' -> canonical 'quantico-acq'")
        sq_ft = sq_ft or parse_int(r["gross_sq_ft"])
        seats = seats or parse_int(r["workstations"])
        name = name or r["facility_name"]
        region = region or r["loc_region"]
        rent = parse_money(r["annual_rent"])
        opex = parse_money(r["op_ex"])
        if r["currency"].upper() == "CAD":
            report["actions"].append(
                f"CAD amounts on '{r['facility_code']}' flagged for review "
                f"(converted at {CAD_TO_USD} for reference only)")
            # we prefer the USD-denominated row as authoritative; record the conflict
            report["conflicts"].append(
                "quantico-acq lease: USD row ($1,200,000) vs CAD row "
                "($1,500,000 ~= ${:,.0f}); kept USD, CAD row needs human sign-off"
                .format((rent or 0) * CAD_TO_USD))
        elif rent_usd is None:
            rent_usd, opex_usd = rent, opex
    if sq_ft:
        report["actions"].append(
            f"quantico-acq sq_ft/seats backfilled from sibling row ({sq_ft} sq ft, {seats} seats)")

    conn.execute(
        "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?)",
        ("quantico-acq", name or "Quantico Acquisition", region or "Mid-Atlantic",
         sq_ft, seats, "factory", "acquired", "acquired_import"),
    )
    conn.execute(
        "INSERT INTO leases (site_id, annual_rent_usd, opex_usd_yr, start_date, end_date, lease_type) "
        "VALUES (?,?,?,?,?,?)",
        ("quantico-acq", rent_usd, opex_usd, None, None, "leased"),
    )


def load_leases(conn):
    for r in _read_csv("leases.csv"):
        conn.execute(
            "INSERT INTO leases (site_id, annual_rent_usd, opex_usd_yr, start_date, end_date, lease_type) "
            "VALUES (?,?,?,?,?,?)",
            (r["site_id"], parse_money(r["annual_rent_usd"]), parse_money(r["opex_usd_yr"]),
             r["start_date"], r["end_date"], r["lease_type"]),
        )


def _quarantine(conn, report, source, raw_row, reason):
    conn.execute(
        "INSERT INTO etl_exceptions (source_file, raw_row, reason) VALUES (?,?,?)",
        (source, str(raw_row), reason))
    report["exceptions"].append({"source": source, "row": raw_row, "reason": reason})


def load_headcount(conn, known, report):
    rows = _read_csv("hris_export.csv")
    seen = {}   # (site, quarter, program) -> [values in file order]
    for r in rows:
        site = canonicalize_code(r["site_id"], known)
        if site is None:
            _quarantine(conn, report, "hris_export.csv", dict(r),
                        f"unknown site code '{r['site_id']}'")
            continue
        key = (site, to_quarter(r["quarter"]), r["program"])
        seen.setdefault(key, []).append(int(r["headcount"]))
    # de-dupe: keep the last (most recently received / resolved) value per key
    resolved = {}
    for key, vals in seen.items():
        resolved[key] = vals[-1]
        dropped = [v for v in vals[:-1] if v != vals[-1]]
        if dropped:
            report["dedupes"].append(
                f"headcount {key}: kept {vals[-1]} (dropped conflicting: {dropped})")
    for i, ((site, quarter, prog), hc) in enumerate(resolved.items(), start=1):
        conn.execute(
            "INSERT INTO headcount_snapshots VALUES (?,?,?,?,?)",
            (i, site, quarter, prog, hc))


def load_demand(conn, known, report):
    out = []
    for r in _read_csv("mrp_export.csv"):
        site = canonicalize_code(r["site_id"], known)
        if site is None:
            _quarantine(conn, report, "mrp_export.csv", dict(r),
                        f"unknown site code '{r['site_id']}'")
            continue
        out.append((site, to_quarter(r["quarter"]), r["program"],
                    int(r["units_planned"]), float(r["sqft_per_unit"])))
    for i, row in enumerate(out, start=1):
        conn.execute("INSERT INTO production_demand VALUES (?,?,?,?,?,?)", (i, *row))


def load_quality(conn, known, report):
    out = []
    for r in _read_csv("erp_quality.csv"):
        site = canonicalize_code(r["site_id"], known)
        if site is None:
            _quarantine(conn, report, "erp_quality.csv", dict(r),
                        f"orphan: site '{r['site_id']}' exists in no registry")
            continue
        out.append((site, to_quarter(r["quarter"]), r["category"],
                    parse_int(r["severity"]), r["status"], r["reported_date"], r["description"]))
    for i, row in enumerate(out, start=1):
        conn.execute("INSERT INTO quality_issues VALUES (?,?,?,?,?,?,?,?)", (i, *row))


def load_all(conn):
    """Run the full ingest into a freshly-schema'd connection. Returns a report dict."""
    report = {"actions": [], "conflicts": [], "exceptions": [],
              "dedupes": [], "acquired_codes": []}
    load_sites(conn, report)
    load_leases(conn)
    known = set(r["site_id"] for r in db.query(conn, "SELECT site_id FROM sites"))
    load_headcount(conn, known, report)
    load_demand(conn, known, report)
    load_quality(conn, known, report)
    conn.commit()
    return report


def run():
    conn = db.connect()
    db.apply_schema(conn)
    report = load_all(conn)
    db.apply_views(conn)
    conn.commit()
    n_sites = db.query(conn, "SELECT COUNT(*) c FROM sites")[0]["c"]
    print(f"ETL complete: {n_sites} sites loaded into {os.path.relpath(db.DB_PATH)}")
    print(f"  • {len(report['actions'])} reconciliation actions on acquired data")
    print(f"  • {len(report['dedupes'])} duplicate row(s) resolved")
    print(f"  • {len(report['exceptions'])} row(s) quarantined to etl_exceptions")
    conn.close()
    return report


if __name__ == "__main__":
    run()
