"""
ETL — ingest the messy source-system exports in seeds/ and reconcile them into
the canonical schema. This is the "work deeply in business systems" muscle.

What it cleans, and how:
  • Site codes      — a canonicalization map collapses 'AIF' / 'advanced-imaging' /
                      'Advanced Imaging' onto the canonical id 'advanced-imaging'.
  • Currency/format — rent like "$1,200,000" -> 1200000.0; CAD -> USD at a
                      documented rate.
  • Dates           — '12/2025' (MM/YYYY) -> '2025-Q4'; 'YYYY-Qn' passes through.
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

# Canonical site ids the platform recognizes (the 9 clean ones; advanced-imaging
# is added from the dirty acquired dump during load_sites).
CANONICAL_SITES = [
    "arsenal-campus", "hq-flagship", "long-beach", "srm-complex",
    "maritime-systems", "composites-uav", "space-domain",
    "seattle-hub", "boston-maritime",
]

# Explicit aliases for codes that don't normalize cleanly to a canonical id.
# The recently-acquired Advanced Imaging Facility arrived under several spellings.
SITE_ALIASES = {
    "aif": "advanced-imaging",
    "advanced imaging": "advanced-imaging",
    "advanced imaging facility": "advanced-imaging",
    "advanced-imaging": "advanced-imaging",
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
    """Load the clean canonical sites, then reconcile the dirty acquired dump into
    one canonical advanced-imaging site + lease."""
    for r in _read_csv("sites_master.csv"):
        conn.execute(
            "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["site_id"], r["site_name"], r["region"],
             parse_int(r["sq_ft"]), parse_int(r["seat_capacity"]),
             parse_int(r["power_kw_capacity"]),
             r["site_type"], r["status"], r["site_status"],
             r["integration_start_date"] or None,
             r["lease_expiration_date"] or None, r["lease_option_deadline"] or None,
             r["source_system"]),
        )

    # --- reconcile acquired_site_dump.csv (two rows, one real site) -------------
    dump = _read_csv("acquired_site_dump.csv")
    report["acquired_codes"] = [r["facility_code"] for r in dump]
    sq_ft = seats = name = region = None
    rent_usd = opex_usd = None
    for r in dump:
        report["actions"].append(
            f"acquired code '{r['facility_code']}' -> canonical 'advanced-imaging'")
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
            # We prefer the USD-denominated row as authoritative and QUARANTINE the
            # CAD row to the exceptions queue: a human must sign off on the rent the
            # platform should carry. Persisted to etl_exceptions so the exec brief
            # and reconciliation report read the same live count.
            _quarantine(
                conn, report, "acquired_site_dump.csv", dict(r),
                "currency/value conflict: USD row ($1,200,000) vs CAD row "
                "($1,500,000 ~= ${:,.0f}); kept USD, CAD row needs human sign-off"
                .format((rent or 0) * CAD_TO_USD))
        elif rent_usd is None:
            rent_usd, opex_usd = rent, opex
    if sq_ft:
        report["actions"].append(
            f"advanced-imaging sq_ft backfilled from sibling row ({sq_ft} sq ft); "
            f"workstations + power capacity still NULL (audit pending)")

    # Recently acquired, integration still in progress: power capacity and seats are
    # not yet audited, so they land NULL — flagged in vw_integration_pipeline, not
    # penalized as negligence in vw_site_health.
    conn.execute(
        "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("advanced-imaging", name or "Advanced Imaging Facility", region or "Southeast",
         sq_ft, seats, None,                    # power capacity not in the acquired dump (audit pending)
         "factory", "acquired", "acquired_integrating",
         "2026-03-01",                          # integration clock started ~3 months ago
         None, None,                            # lease dates pending sign-off on the acquired site
         "acquired_import"),
    )
    conn.execute(
        "INSERT INTO leases (site_id, annual_rent_usd, opex_usd_yr, start_date, end_date, lease_type) "
        "VALUES (?,?,?,?,?,?)",
        ("advanced-imaging", rent_usd, opex_usd, None, None, "leased"),
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
                    int(r["units_planned"]), float(r["sqft_per_unit"]),
                    float(r.get("kw_per_unit") or 0)))
    for i, row in enumerate(out, start=1):
        conn.execute("INSERT INTO production_demand VALUES (?,?,?,?,?,?,?)", (i, *row))


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


def load_actions(conn, known):
    """Load the workflow actions. site_id is kept only if it resolves to a known
    canonical site; an orphan action (the orphan quality record) carries NULL,
    which is exactly why it still needs a human."""
    for i, r in enumerate(_read_csv("actions.csv"), start=1):
        raw_site = (r["site_id"] or "").strip()
        site = canonicalize_code(raw_site, known) if raw_site else None
        conn.execute(
            "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)",
            (i, site, r["source"], r["title"], r["owner"] or None,
             r["due_date"] or None, r["status"], r["resolution_note"] or None,
             r["created_at"] or None))


def load_programs(conn):
    """Load the program registry. Programs reference canonical sites; software
    programs carry NULL unit counts (they are tracked by headcount, not units)."""
    for i, r in enumerate(_read_csv("programs.csv"), start=1):
        conn.execute(
            "INSERT INTO programs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, r["program_name"], r["program_type"],
             r["primary_site_id"] or None, r["secondary_site_id"] or None,
             r["status"], parse_int(r["units_per_quarter_current"]),
             parse_int(r["units_per_quarter_target"]),
             float(r["kw_per_unit"]) if r["kw_per_unit"] else None,
             float(r["sqft_per_unit"]) if r["sqft_per_unit"] else None))


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
    load_actions(conn, known)
    load_programs(conn)
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
