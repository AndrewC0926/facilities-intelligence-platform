"""
Seed generator — writes the simulated upstream "system exports" as CSVs into
seeds/. These stand in for what you'd actually receive from ERP, MRP, and HRIS:
mostly clean, but with deliberate, narratable dirt (especially the acquired
site) so the ETL's reconciliation has real work to do. The cleaning is the demo.

Deterministic: no randomness, so `make demo` produces the same portfolio every
time and the tests can assert exact outcomes.

The portfolio models a defense-hardware company at hyperscale — a flagship
mega-factory whose POWER service is the binding constraint, a campus under
construction, a rocket-motor complex ramping hard, and several recently-acquired
sites at different stages of integration.

10 sites:
  arsenal-campus   the flagship mega-factory (Bldg 1 of 7 operational); POWER is
                   the binding constraint — breaches in 2026-Q4, floor in 2027-Q1
  hq-flagship      HQ & flagship factory; healthy headroom
  long-beach       new campus under construction — capacity data NULL (buildout)
  srm-complex      solid rocket motor complex, ramping 600 -> 6,000 motors/yr
                   (a 2027 power 'watch', further out than Arsenal)
  maritime-systems undersea systems factory — the quality hotspot
  composites-uav   acquired (Area-I), integration complete (18 months in)
  advanced-imaging acquired recently and DIRTY — arrives via the messy dump
  space-domain     acquired 6 months ago, integration in progress
  seattle-hub      engineering hub — over its seat capacity
  boston-maritime  small maritime engineering office — under-utilized seats

The observation window is anchored so the projected breaches land in the FUTURE
relative to a mid-2026 "today".
"""
import csv
import os

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")
# Four planned quarters, anchored so Arsenal's POWER wall lands in 2026-Q4 and its
# FLOOR wall one quarter later in 2027-Q1 (both in the future relative to mid-2026).
QUARTERS = ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"]


def _write(name, header, rows):
    os.makedirs(SEED_DIR, exist_ok=True)
    path = os.path.join(SEED_DIR, name)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


# -- sites_master.csv : the 9 clean canonical sites. The 10th (advanced-imaging)
#    arrives dirty, separately, via the acquired dump and is reconciled in by the ETL.
#    Power is in kW throughout. NULLs are deliberate: long-beach is mid-construction;
#    arsenal's lease option deadline is set ~60 days before its 2026-Q4 power breach
#    to create an urgent lease-cliff flag.
def seed_sites():
    rows = [
        # site_id, site_name, region, sq_ft, seat_capacity, power_kw_capacity, site_type,
        #   status, site_status, integration_start_date, lease_expiration_date, lease_option_deadline, source_system
        ["arsenal-campus",   "Arsenal Campus",               "West",      980000,  6000, 28000, "factory", "operational", "operational",          "",           "2031-12-01", "2026-08-02", "canonical"],
        ["hq-flagship",      "HQ & Flagship Factory",        "West",      640000,  2500, 8500,  "factory", "operational", "operational",          "",           "",           "",           "canonical"],
        ["long-beach",       "Long Beach Campus",            "West",      "",      "",   "",    "campus",  "buildout",    "buildout",             "",           "",           "",           "canonical"],
        ["srm-complex",      "Solid Rocket Motor Complex",   "South",     92000,   400,  6200,  "factory", "operational", "operational",          "",           "",           "",           "canonical"],
        ["maritime-systems", "Maritime Systems Factory",     "Northeast", 140000,  600,  3200,  "factory", "operational", "operational",          "",           "2029-06-01", "2028-06-01", "canonical"],
        ["composites-uav",   "Composites & UAV Integration", "Southeast", 148000,  700,  2400,  "factory", "acquired",    "acquired_complete",    "2024-12-01", "2030-03-01", "2029-09-01", "canonical"],
        ["space-domain",     "Space Domain Campus",          "Mountain",  80000,   300,  4100,  "campus",  "acquired",    "acquired_integrating", "2025-12-01", "",           "",           "canonical"],
        ["seattle-hub",      "Seattle Engineering Hub",      "West",      95000,   1200, 1800,  "office",  "operational", "operational",          "",           "2029-01-01", "2028-07-01", "canonical"],
        ["boston-maritime",  "Boston Maritime Engineering",  "Northeast", 48000,   350,  1200,  "office",  "operational", "operational",          "",           "2028-03-01", "2027-09-01", "canonical"],
    ]
    return _write("sites_master.csv",
                  ["site_id", "site_name", "region", "sq_ft", "seat_capacity", "power_kw_capacity",
                   "site_type", "status", "site_status", "integration_start_date",
                   "lease_expiration_date", "lease_option_deadline", "source_system"],
                  rows)


# -- leases.csv : clean cost layer for the canonical sites. arsenal & hq are the
#    cheap-per-sqft outliers (huge footprints); the small offices are the dear ones;
#    long-beach has a big spend but NULL sq_ft -> cost/sqft NULL. (advanced-imaging's
#    lease comes from the acquired dump.)
def seed_leases():
    rows = [
        # site_id, annual_rent_usd, opex_usd_yr, start_date, end_date, lease_type
        ["arsenal-campus",   14000000, 5000000, "2023-01-01", "2031-12-01", "leased"],   # huge sqft -> low $/sqft
        ["hq-flagship",      9000000,  3000000, "2017-01-01", "2037-01-01", "owned"],
        ["long-beach",       30000000, 8000000, "2025-01-01", "2045-01-01", "owned"],    # big spend, sq_ft NULL -> cost NULL
        ["srm-complex",      4000000,  1500000, "2019-06-01", "2039-06-01", "owned"],
        ["maritime-systems", 3500000,  1200000, "2021-06-01", "2029-06-01", "leased"],
        ["composites-uav",   3000000,  1000000, "2020-03-01", "2030-03-01", "leased"],
        ["space-domain",     2500000,  900000,  "2022-01-01", "2032-01-01", "leased"],
        ["seattle-hub",      4200000,  1300000, "2021-01-01", "2029-01-01", "leased"],
        ["boston-maritime",  2400000,  700000,  "2022-03-01", "2028-03-01", "leased"],   # small office -> high $/sqft
    ]
    return _write("leases.csv",
                  ["site_id", "annual_rent_usd", "opex_usd_yr", "start_date", "end_date", "lease_type"],
                  rows)


# -- hris_export.csv : headcount per site/program/quarter.
#    Dirt: seattle-hub's 2026-Q1 Lattice OS row appears twice with conflicting totals
#    (dedupe test); boston-maritime's first quarter uses MM/YYYY date drift ('12/2025');
#    one acquired-site row uses a messy code ('Advanced Imaging').
def seed_hris():
    rows = []
    # arsenal ramps hard as Building 1 fills (engineering + production)
    arsenal = {"Fury CCA": [2600, 2900, 3200, 3500], "Roadrunner": [1400, 1500, 1600, 1700]}
    for prog, vals in arsenal.items():
        for q, hc in zip(QUARTERS, vals):
            rows.append(["arsenal-campus", q, prog, hc])
    # seattle engineering hub: crammed past its 1,200 seats by the last quarter
    for q, hc in zip(QUARTERS, [1100, 1200, 1300, 1400]):
        rows.append(["seattle-hub", q, "Lattice OS", hc])
    # the deliberate duplicate: conflicting 2026-Q1 Lattice OS number, correct value later
    rows.append(["seattle-hub", "2026-Q1", "Lattice OS", 9999])   # bad dupe (will be superseded)
    rows.append(["seattle-hub", "2026-Q1", "Lattice OS", 1200])   # canonical value, kept

    steady = {
        "hq-flagship":      ("Lattice OS",    [1900, 1950, 2000, 2050]),
        "srm-complex":      ("SRM Supply",    [260,  290,  320,  350]),
        "maritime-systems": ("Ghost Shark",   [430,  450,  470,  490]),
        "composites-uav":   ("ALTIUS Series", [470,  500,  530,  560]),
        "space-domain":     ("Space Ops",     [140,  160,  180,  200]),
        "boston-maritime":  ("Ghost Shark",   [170,  175,  180,  185]),   # under-utilized vs 350 seats
    }
    for site, (prog, vals) in steady.items():
        for q, hc in zip(QUARTERS, vals):
            # boston-maritime's first quarter arrives as a MM/YYYY date ('12/2025' -> 2025-Q4)
            qlabel = "12/2025" if (site == "boston-maritime" and q == "2025-Q4") else q
            rows.append([site, qlabel, prog, hc])
    # acquired site headcount arrives under a messy code (routes to advanced-imaging)
    rows.append(["Advanced Imaging", "2026-Q3", "Sensor Integration", 95])
    return _write("hris_export.csv", ["site_id", "quarter", "program", "headcount"], rows)


# -- mrp_export.csv : production demand on TWO constraints — floor space and power.
#    MODELING NOTE: at this scale a site's power/floor demand is a FACILITY-level
#    figure (base load + energization dominates marginal per-unit draw), so each row
#    is a per-quarter facility "production bundle" (units_planned = 1; the per-bundle
#    footprint IS that quarter's demand, in sq ft and kW). Program-level detail lives
#    in the programs table. arsenal-campus is engineered to hit its POWER ceiling one
#    quarter BEFORE its floor ceiling — power is the binding constraint.
def seed_mrp():
    rows = []
    # ARSENAL — the binding-constraint site.
    #   POWER: 18,200 -> 22,400 kW (+1,400/q) vs 28,000 cap (85% wall = 23,800)
    #          => power breach 1 quarter out => 2026-Q4. (current 22,400 = 80% utilized)
    #   FLOOR: 646k -> 760k sq ft (+38k/q) vs 980k usable (Bldg 1; 85% wall = 833k)
    #          => floor breach 2 quarters out => 2027-Q1. One-quarter gap, power first.
    arsenal_floor = [646000, 684000, 722000, 760000]
    arsenal_power = [18200, 19600, 21000, 22400]
    for q, fl, pw in zip(QUARTERS, arsenal_floor, arsenal_power):
        rows.append(["arsenal-campus", q, "Arsenal Production", 1, fl, pw])
    # SRM COMPLEX — ramping 600 -> 6,000 motors/yr toward its 6,200 kW ceiling
    #   (85% wall = 5,270). POWER 4,600 -> 4,960 kW (+120/q) => breach 2027-Q2 (a
    #   'watch', further out than Arsenal). Floor stays flat (no floor collision).
    srm_power = [4600, 4720, 4840, 4960]
    for q, pw in zip(QUARTERS, srm_power):
        rows.append(["srm-complex", q, "SRM Production", 1, 40000, pw])
    # Everyone else operational: flat demand (no growth -> stable, no collision).
    flat = {            # site: (floor_demand_sqft, power_demand_kw)
        "hq-flagship":      (360000, 3400),
        "maritime-systems": (60000, 1280),
        "composites-uav":   (70000, 960),
        "space-domain":     (30000, 2050),
        "seattle-hub":      (40000, 720),
        "boston-maritime":  (20000, 480),
    }
    for site, (fl, pw) in flat.items():
        for q in QUARTERS:
            rows.append([site, q, "Production", 1, fl, pw])
    # long-beach is mid-construction: it already has a production PLAN, but its
    # capacity is not yet provisioned (sq_ft + power NULL) -> the collision detector
    # reports it as 'capacity data pending' instead of a false breach.
    for q in QUARTERS:
        rows.append(["long-beach", q, "Production (planned)", 1, 220000, 6000])
    # advanced-imaging (acquired, mid-integration) carries NO production plan yet
    # -> absent from the collision detector by design.
    return _write("mrp_export.csv",
                  ["site_id", "quarter", "program", "units_planned", "sqft_per_unit", "kw_per_unit"], rows)


# -- programs.csv : the program registry. Maps each program to its primary (and
#    optional secondary) site, with current/target quarterly output and the per-unit
#    floor/power footprint of its line. Software programs carry NULL units. This is
#    what turns a capacity collision into a PROGRAM problem (see vw_program_facility_risk).
def seed_programs():
    rows = [
        # program_name, program_type, primary_site_id, secondary_site_id, status,
        #   units_per_quarter_current, units_per_quarter_target, kw_per_unit, sqft_per_unit
        ["Fury CCA",      "autonomous_aircraft",   "arsenal-campus",   "hq-flagship",  "production",  12,  37,  180, 8500],
        ["Roadrunner",    "munitions",             "arsenal-campus",   "",             "production",  45,  120, 40,  800],
        ["Barracuda",     "munitions",             "arsenal-campus",   "",             "development", 8,   60,  35,  700],
        ["Ghost Shark",   "autonomous_underwater", "maritime-systems", "boston-maritime", "production", 18, 50, 220, 4200],
        ["ALTIUS Series", "autonomous_aircraft",   "composites-uav",   "",             "development", 24,  80,  90,  2600],
        ["Bolt",          "munitions",             "arsenal-campus",   "",             "development", 0,   200, 25,  400],
        ["SRM Supply",    "munitions",             "srm-complex",      "",             "production",  480, 1500, 9,  120],
        ["Lattice OS",    "c2_software",           "hq-flagship",      "seattle-hub",  "production",  "",  "",  "",  ""],
    ]
    return _write("programs.csv",
                  ["program_name", "program_type", "primary_site_id", "secondary_site_id", "status",
                   "units_per_quarter_current", "units_per_quarter_target", "kw_per_unit", "sqft_per_unit"], rows)


# -- erp_quality.csv : quality/CMMS issues. maritime-systems is the hot site.
#    Dirt: one row points at 'kona-test-range', a site in no registry (orphan ->
#    quarantine); one acquired-site row uses the 'AIF' code.
def seed_quality():
    rows = []

    def add(site, q, cat, sev, status, date, desc):
        rows.append([site, q, cat, sev, status, date, desc])

    # maritime-systems — the quality hotspot (Ghost Shark production ramp pains)
    add("maritime-systems", "2025-Q4", "equipment", 5, "open",   "2025-11-12", "Autoclave temperature excursion on hull cure")
    add("maritime-systems", "2025-Q4", "safety",    4, "closed", "2025-12-03", "Crane load-test overdue in bay 2")
    add("maritime-systems", "2026-Q1", "facility",  4, "open",   "2026-02-18", "Seawater test tank liner leak")
    add("maritime-systems", "2026-Q1", "equipment", 5, "open",   "2026-03-05", "Ballast actuator failures, scrap up")
    add("maritime-systems", "2026-Q2", "supply",    3, "closed", "2026-05-20", "Titanium lot rejected at incoming QA")
    add("maritime-systems", "2026-Q2", "safety",    4, "open",   "2026-06-08", "Confined-space entry permit lapse")
    add("maritime-systems", "2026-Q3", "equipment", 5, "open",   "2026-08-14", "Pressure-hull weld porosity recurring")

    # the rest — lighter load, mostly closed
    add("arsenal-campus",  "2026-Q2", "facility",  3, "open",   "2026-05-04", "WIP staging cramped on Line 1")  # foreshadows collision
    add("arsenal-campus",  "2026-Q3", "equipment", 3, "open",   "2026-07-22", "Robotic riveter intermittent")
    add("srm-complex",     "2026-Q2", "safety",    4, "closed", "2026-04-15", "Propellant mix room interlock fault")
    add("hq-flagship",     "2026-Q1", "equipment", 2, "closed", "2026-01-30", "CMM calibration drift")
    add("composites-uav",  "2026-Q2", "facility",  2, "closed", "2026-05-06", "Layup room humidity excursion")
    add("space-domain",    "2026-Q3", "facility",  3, "open",   "2026-08-20", "Clean-room particle count high (integration)")

    # acquired site, messy code -> routes to advanced-imaging
    add("AIF", "2026-Q3", "facility", 4, "open", "2026-09-09", "UPS transfer switch unreliable (inherited)")
    # ORPHAN: site that exists in no registry -> ETL must quarantine, not crash
    add("kona-test-range", "2026-Q2", "equipment", 3, "open", "2026-06-30", "Telemetry rack overdue for PM (unknown site)")

    return _write("erp_quality.csv",
                  ["site_id", "quarter", "category", "severity", "status", "reported_date", "description"], rows)


# -- acquired_site_dump.csv : the recently-acquired Advanced Imaging Facility's own
#    export. Different column names, and deliberately dirty: two rows for the SAME
#    real site under different codes (AIF vs 'advanced imaging'), one with NULL sq_ft,
#    rent formatted as a string, and one row denominated in CAD. The reconciliation muscle.
def seed_acquired():
    rows = [
        # facility_code, facility_name, loc_region, gross_sq_ft, workstations, annual_rent, op_ex, currency, lease_kind
        ["AIF",              "Advanced Imaging Facility", "Southeast", "",      "", "$1,200,000", "$340,000", "USD", "leased"],
        ["advanced imaging", "Advanced Imaging Fac.",     "Southeast", "34000", "", "1,500,000",  "410,000",  "CAD", "leased"],
    ]
    return _write("acquired_site_dump.csv",
                  ["facility_code", "facility_name", "loc_region", "gross_sq_ft",
                   "workstations", "annual_rent", "op_ex", "currency", "lease_kind"], rows)


# -- actions.csv : the workflow layer. Each insight that needs a human becomes a
#    trackable, OWNED, DATED action. Seeded open items: the Arsenal power-breach
#    decision, both reconciliation exceptions (the CAD/USD conflict on advanced-imaging
#    and the kona-test-range orphan, which has NO canonical site), and the maritime
#    quality hotspot. created_at dates span the age bands as viewed around mid-2026.
def seed_actions():
    rows = [
        # site_id, source, title, owner, due_date, status, resolution_note, created_at
        ["arsenal-campus", "collision",
         "Arsenal POWER breach 2026-Q4 — decide: substation upgrade vs. shift Roadrunner line",
         "VP Facilities", "2026-07-31", "open", "", "2026-03-15"],         # ~red (>60d)
        ["advanced-imaging", "reconciliation",
         "Sign off advanced-imaging lease: USD $1.2M (kept) vs CAD $1.5M row",
         "Lease Admin", "2026-06-20", "open", "", "2026-04-25"],           # ~yellow (30-60d)
        ["",             "reconciliation",
         "Resolve orphan quality record for unknown site 'kona-test-range'",
         "Data Steward", "2026-06-18", "in_progress", "", "2026-04-30"],   # ~yellow, no canonical site
        ["maritime-systems", "quality",
         "Maritime pressure-hull weld porosity — root-cause the recurring scrap",
         "Quality Director", "2026-07-15", "open", "", "2026-05-20"],      # ~green (<30d)
    ]
    return _write("actions.csv",
                  ["site_id", "source", "title", "owner", "due_date", "status",
                   "resolution_note", "created_at"], rows)


def main():
    paths = [
        seed_sites(), seed_leases(), seed_hris(), seed_mrp(),
        seed_programs(), seed_quality(), seed_acquired(), seed_actions(),
    ]
    print("Seeded source-system exports:")
    for p in paths:
        print("  •", os.path.relpath(p))


if __name__ == "__main__":
    main()
