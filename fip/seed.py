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
    # Each headcount row now carries a worker ARCHETYPE (Phase 3). Archetype drives
    # which SPACE TYPES the person consumes (via archetype_space_map). The mix is what
    # makes desks rarely the binding constraint at industrial sites.
    rows = []
    # arsenal ramps hard as Building 1 fills — production workers (workstation + parking,
    # zero desks) -> parking is the space that binds, not desks.
    arsenal = {"Fury CCA": [2600, 2900, 3200, 3500], "Roadrunner": [1400, 1500, 1600, 1700]}
    for prog, vals in arsenal.items():
        for q, hc in zip(QUARTERS, vals):
            rows.append(["arsenal-campus", q, prog, "production", hc])
    # seattle engineering hub: an office site — engineers on desks, the one place the
    # commercial-office playbook applies (desks bind).
    for q, hc in zip(QUARTERS, [1100, 1200, 1300, 1400]):
        rows.append(["seattle-hub", q, "Lattice OS", "engineer", hc])
    # the deliberate duplicate: conflicting 2026-Q1 Lattice OS number, correct value later
    rows.append(["seattle-hub", "2026-Q1", "Lattice OS", "engineer", 9999])   # bad dupe (superseded)
    rows.append(["seattle-hub", "2026-Q1", "Lattice OS", "engineer", 1200])   # canonical value, kept
    # long-beach: a small standup crew while the campus is built (planned capacity)
    for q, hc in zip(QUARTERS, [40, 60, 80, 100]):
        rows.append(["long-beach", q, "Standup", "engineer", hc])

    steady = {
        # site: (program, archetype, [headcount per quarter])
        "hq-flagship":      ("Lattice OS",    "cleared",  [1900, 1950, 2000, 2050]),  # SCIF-seat bound
        "srm-complex":      ("SRM Supply",    "production", [260,  290,  320,  350]),
        "maritime-systems": ("Ghost Shark",   "engineer", [430,  450,  470,  490]),
        "composites-uav":   ("ALTIUS Series", "engineer", [470,  500,  530,  560]),
        "space-domain":     ("Space Ops",     "cleared",  [140,  160,  180,  200]),   # SCIF audit pending
        "boston-maritime":  ("Ghost Shark",   "engineer", [170,  175,  180,  185]),   # under-utilized vs 350 seats
    }
    for site, (prog, arch, vals) in steady.items():
        for q, hc in zip(QUARTERS, vals):
            # boston-maritime's first quarter arrives as a MM/YYYY date ('12/2025' -> 2025-Q4)
            qlabel = "12/2025" if (site == "boston-maritime" and q == "2025-Q4") else q
            rows.append([site, qlabel, prog, arch, hc])
    # acquired site headcount arrives under a messy code (routes to advanced-imaging)
    rows.append(["Advanced Imaging", "2026-Q3", "Sensor Integration", "engineer", 95])
    return _write("hris_export.csv", ["site_id", "quarter", "program", "archetype", "headcount"], rows)


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
    # Phase 4: collision-source actions carry cost-of-delay economics
    # (est_remedy_cost_usd, est_delay_cost_usd_per_quarter); others leave them blank.
    rows = [
        # site_id, source, title, owner, due_date, status, resolution_note, created_at, remedy_cost, delay_cost_per_q
        ["arsenal-campus", "collision",
         "Arsenal POWER breach 2026-Q4 — decide: substation upgrade vs. shift Roadrunner line",
         "VP Facilities", "2026-07-31", "open", "", "2026-03-15", 3500000, 1200000],   # ~red (>60d)
        ["advanced-imaging", "reconciliation",
         "Sign off advanced-imaging lease: USD $1.2M (kept) vs CAD $1.5M row",
         "Lease Admin", "2026-06-20", "open", "", "2026-04-25", "", ""],               # ~yellow (30-60d)
        ["",             "reconciliation",
         "Resolve orphan quality record for unknown site 'kona-test-range'",
         "Data Steward", "2026-06-18", "in_progress", "", "2026-04-30", "", ""],       # ~yellow, no canonical site
        ["maritime-systems", "quality",
         "Maritime pressure-hull weld porosity — root-cause the recurring scrap",
         "Quality Director", "2026-07-15", "open", "", "2026-05-20", "", ""],          # ~green (<30d)
    ]
    return _write("actions.csv",
                  ["site_id", "source", "title", "owner", "due_date", "status",
                   "resolution_note", "created_at", "est_remedy_cost_usd",
                   "est_delay_cost_usd_per_quarter"], rows)


# =============================================================================
# PHASE 3 — OCCUPANCY & SEAT DEMAND (configuration as data)
# =============================================================================
# Everything below is DATA the views read — no archetype, space type, ratio, or
# lead time is hardcoded anywhere in SQL or Python. The demo data is engineered to
# exercise every code path across DIFFERENT KINDS of sites (industrial binds on
# parking, mixed binds on SCIF, office binds on desk, one audit-pending, one
# planned buildout), not to tell one flagship's story.

def seed_archetypes():
    rows = [
        # archetype_id, name, description
        [1, "production",  "Line/cell operators; no desk, need a workstation and a parking stall"],
        [2, "engineer",    "Design/test engineers; desk-based, some bench and parking"],
        [3, "cleared",     "Cleared staff working in accredited space; need a SCIF seat"],
        [4, "field",       "Field/deployment staff; rarely on-site, mostly need parking when in"],
        [5, "contractor",  "On-site contractors; a mix of workstation and desk"],
        [6, "corporate",   "Corporate/G&A; desk-based"],
    ]
    return _write("archetypes.csv", ["archetype_id", "name", "description"], rows)


def seed_space_types():
    rows = [
        # space_type_id, name, unit_label, lead_time_days, restricted_sensing, target_util_low, target_util_high
        [1, "desk",          "seat",    30,  0, 60, 85],
        [2, "bench",         "bench",   90,  0, 60, 85],
        [3, "workstation",   "station", 120, 0, 60, 85],
        [4, "parking_stall", "stall",   270, 0, 60, 85],
        [5, "scif_seat",     "seat",    540, 1, 60, 85],   # accredited: sensor occupancy unavailable (ICD 705)
    ]
    return _write("space_types.csv",
                  ["space_type_id", "name", "unit_label", "lead_time_days", "restricted_sensing",
                   "target_util_low", "target_util_high"], rows)


def seed_archetype_space_map():
    # ratio = units of a space type consumed per worker of an archetype. DATA, not
    # constants in a view. A missing pair means that archetype needs none of it.
    # Benchmark: production parking 0.67 = the ~2-stalls-per-3-workers peak-shift rule;
    # office/field archetypes sit below it. (SCIF lead 540d is within the 360-1080d range.)
    rows = [
        # archetype_id, space_type_id, ratio
        [1, 3, 1.0],  [1, 4, 0.67],                 # production: 1 workstation, 0.67 parking (~2:3 peak), 0 desk
        [2, 1, 1.0],  [2, 4, 0.5],  [2, 2, 0.25],   # engineer: 1 desk, 0.5 parking, 0.25 bench
        [3, 5, 1.0],  [3, 1, 0.2],  [3, 4, 0.5],    # cleared: 1 SCIF seat, 0.2 desk, 0.5 parking
        [4, 1, 0.1],  [4, 4, 0.3],                  # field: 0.1 desk, 0.3 parking (mostly deployed -> low peak on-site)
        [5, 3, 0.5],  [5, 1, 0.5],  [5, 4, 0.5],    # contractor: 0.5 workstation, 0.5 desk, 0.5 parking
        [6, 1, 1.0],  [6, 4, 0.4],                  # corporate: 1 desk, 0.4 parking
    ]
    return _write("archetype_space_map.csv", ["archetype_id", "space_type_id", "ratio"], rows)


def seed_space_capacity():
    # Per-site supply. Tuned so each KIND of site binds on a different space type:
    #   arsenal (industrial)  -> parking_stall (production is parking-heavy)
    #   hq (mixed)            -> scif_seat (cleared staff, ample floor/power)
    #   seattle (office)      -> desk (the one place the commercial playbook applies)
    #   space-domain          -> scif_seat audit_pending (NULL capacity -> data pending)
    #   long-beach (buildout) -> planned capacity -> supportable headcount, not a breach
    # capacity omitted (NULL) only for audit_pending rows.
    rows = [
        # site_id, space_type_id, capacity, capacity_status
        # arsenal: parking tight (binds ~2026-Q4), workstation & desk ample
        ["arsenal-campus", 3, 7000, "confirmed"],   # workstation
        ["arsenal-campus", 4, 4300, "confirmed"],   # parking_stall  <- binds
        ["arsenal-campus", 1, 2000, "confirmed"],   # desk (production needs 0)
        # hq: SCIF tight (cleared capped), desk & parking ample
        ["hq-flagship", 5, 2150, "confirmed"],      # scif_seat  <- binds
        ["hq-flagship", 1, 3000, "confirmed"],      # desk
        ["hq-flagship", 4, 2500, "confirmed"],      # parking
        # seattle: desk tight, parking & bench ample
        ["seattle-hub", 1, 1550, "confirmed"],      # desk  <- binds
        ["seattle-hub", 4, 2000, "confirmed"],      # parking
        ["seattle-hub", 2, 2000, "confirmed"],      # bench
        # space-domain: SCIF accreditation audit in progress -> capacity NULL, pending
        ["space-domain", 5, "", "audit_pending"],   # scif_seat capacity unknown
        ["space-domain", 1, 500, "confirmed"],      # desk (ample)
        ["space-domain", 4, 500, "confirmed"],      # parking (ample)
        # long-beach: still under construction -> planned supply, not a real ceiling
        ["long-beach", 1, 1200, "planned"],         # desk (planned)
        ["long-beach", 3, 800,  "planned"],         # workstation (planned)
        # other operational sites: ample confirmed supply (stable, for coverage)
        ["srm-complex", 3, 1200, "confirmed"], ["srm-complex", 4, 1000, "confirmed"],
        ["maritime-systems", 1, 1000, "confirmed"], ["maritime-systems", 4, 900, "confirmed"], ["maritime-systems", 2, 900, "confirmed"],
        ["composites-uav", 1, 1500, "confirmed"], ["composites-uav", 4, 1200, "confirmed"],
        ["boston-maritime", 1, 900, "confirmed"], ["boston-maritime", 4, 700, "confirmed"],
        ["advanced-imaging", 1, 500, "confirmed"],
    ]
    return _write("space_capacity.csv",
                  ["site_id", "space_type_id", "capacity", "capacity_status"], rows)


def seed_requisition_pipeline():
    # The LEADING indicator. open_reqs become future-quarter seat demand once filled.
    # avg_time_to_fill_days is the PEOPLE side; compared against a space's lead time
    # it reveals where facilities (not hiring) is the bottleneck:
    #   arsenal production: fast to hire (45d) but parking takes 270d -> facilities bottleneck
    #   hq cleared:         slow to hire (200d) and SCIF takes 540d   -> facilities bottleneck
    #   seattle engineer:   hire in 75d, desks take 30d               -> NOT a bottleneck
    rows = [
        # site_id, archetype_id, quarter, open_reqs, avg_time_to_fill_days
        ["arsenal-campus", 1, "2026-Q3", 300, 45],    # production
        ["hq-flagship",    3, "2026-Q3", 120, 200],   # cleared
        ["seattle-hub",    2, "2026-Q3", 150, 75],    # engineer
        ["srm-complex",    1, "2026-Q3", 40,  60],
        ["maritime-systems", 2, "2026-Q3", 30, 70],
        ["long-beach",     2, "2026-Q3", 60,  80],     # standup hiring against planned space
    ]
    return _write("requisition_pipeline.csv",
                  ["site_id", "archetype_id", "quarter", "open_reqs", "avg_time_to_fill_days"], rows)


# =============================================================================
# PHASE 4 — CROSS-FUNCTIONAL & KPI (accountability) LAYER
# =============================================================================
# Deterministic data that exercises every Phase 4 path: two forecast run dates to
# score, one incentive AT RISK + one compliant, one accreditation mid-construction
# (stays unconfirmed) + one fully accredited (flips to confirmed), one onboarding
# cohort missing a readiness dimension one quarter out. No site is special-cased in
# any view — these just light up the code paths.

def seed_forecast_snapshots():
    # Two run dates of past space-collision predictions. The LATER snapshot is treated
    # as the closer-to-truth 'actual'; the earlier one is scored against it. Space
    # type ids: desk=1, parking_stall=4, scif_seat=5.
    rows = [
        # snapshot_date, site_id, space_type_id, predicted_breach_quarter, predicted_util_pct
        # -- run 1 (older forecast) --
        ["2026-01-15", "arsenal-campus", 4, "2027-Q1", 78.0],   # forecast the parking wall a quarter LATE
        ["2026-01-15", "hq-flagship",    5, "2027-Q2", 95.0],   # SCIF forecast on the money
        ["2026-01-15", "seattle-hub",    1, "2026-Q4", 88.0],   # desk forecast on the money
        # -- run 2 (newer 'actual') --
        ["2026-04-15", "arsenal-campus", 4, "2026-Q4", 83.0],   # revised earlier -> run 1 was 1 quarter off
        ["2026-04-15", "hq-flagship",    5, "2027-Q2", 100.0],  # same quarter -> run 1 was a hit
        ["2026-04-15", "seattle-hub",    1, "2026-Q4", 100.0],  # same quarter -> run 1 was a hit
        # Phase 5 material-change fodder: a breach pushed LATER (better) and a
        # utilization that crossed OUT of its corridor band (worse), between the two dates.
        ["2026-01-15", "srm-complex",     4, "2026-Q4", 80.0],
        ["2026-04-15", "srm-complex",     4, "2027-Q3", 82.0],   # breach later -> better
        ["2026-01-15", "boston-maritime", 1, "",        70.0],   # in band (60-85)
        ["2026-04-15", "boston-maritime", 1, "",        92.0],   # above band -> corridor crossing (worse)
    ]
    return _write("forecast_snapshots.csv",
                  ["snapshot_date", "site_id", "space_type_id",
                   "predicted_breach_quarter", "predicted_util_pct"], rows)


def seed_incentive_agreements():
    # One AT RISK (measurement inside the 180-day window WITH a jobs shortfall) and
    # one compliant (met, far off). actual jobs are read from HRIS in the view.
    rows = [
        # site_id, authority, committed_jobs, committed_capex_usd, actual_capex_usd, measurement_date, clawback_risk_usd
        ["long-beach",     "CA GO-Biz",      500,  100000000, 60000000,  "2026-09-15", 8000000],   # AT RISK: ~76d out, 100 of 500 jobs
        ["arsenal-campus", "Ohio JobsOhio",  4000, 500000000, 540000000, "2027-06-01", 0],         # compliant: 5,200 jobs, capex met
    ]
    return _write("incentive_agreements.csv",
                  ["site_id", "authority", "committed_jobs", "committed_capex_usd",
                   "actual_capex_usd", "measurement_date", "clawback_risk_usd"], rows)


def seed_accreditation_milestones():
    # Two subjects (chosen so neither disturbs the Phase 3 collision story):
    #   space-domain scif_seat  -> mid-construction, final milestone unmet -> stays audit_pending
    #   composites-uav scif_seat -> fully accredited -> its (audit_pending) capacity flips to confirmed
    # (composites has no cleared staff, so this adds capacity with no demand -> no collision impact.)
    rows = [
        # site_id, space_type_id, milestone, planned_date, actual_date
        # space-domain SCIF — accreditation still in construction/inspection
        ["space-domain",  5, "design_approval", "2025-10-01", "2025-11-15"],
        ["space-domain",  5, "construction",    "2026-03-01", "2026-04-10"],
        ["space-domain",  5, "inspection",      "2026-08-01", ""],           # not yet met
        ["space-domain",  5, "accreditation",   "2026-12-01", ""],           # FINAL not met -> unconfirmed
        # composites-uav SCIF — fully accredited (all milestones have actual dates)
        ["composites-uav", 5, "design_approval", "2025-06-01", "2025-06-20"],
        ["composites-uav", 5, "construction",    "2025-12-01", "2026-01-10"],
        ["composites-uav", 5, "inspection",      "2026-04-01", "2026-04-15"],
        ["composites-uav", 5, "accreditation",   "2026-06-01", "2026-06-25"],  # FINAL met -> flips to confirmed
    ]
    return _write("accreditation_milestones.csv",
                  ["site_id", "space_type_id", "milestone", "planned_date", "actual_date"], rows)


def seed_onboarding_cohorts():
    # Current quarter (latest HRIS) is 2026-Q3; "one quarter out" = 2026-Q4.
    #   arsenal production cohort: one quarter out, EQUIPMENT not ready -> flagged
    #   hq cleared cohort:         one quarter out, fully ready         -> not flagged
    #   seattle engineer cohort:   far out (2027-Q2)                    -> not imminent
    rows = [
        # site_id, archetype_id, start_quarter, headcount, seat_ready, equipment_ready, badge_ready, parking_ready
        ["arsenal-campus", 1, "2026-Q4", 200, 1, 0, 1, 1],   # missing equipment, one quarter out
        ["hq-flagship",    3, "2026-Q4", 50,  1, 1, 1, 1],   # fully ready
        ["seattle-hub",    2, "2027-Q2", 100, 0, 0, 1, 1],   # far out -> excluded from readiness window
    ]
    return _write("onboarding_cohorts.csv",
                  ["site_id", "archetype_id", "start_quarter", "headcount",
                   "seat_ready", "equipment_ready", "badge_ready", "parking_ready"], rows)


def seed_phase4_space_capacity():
    # An extra SCIF capacity at composites-uav that is 'audit_pending' but fully
    # accredited (see milestones) -> vw_space_capacity_effective flips it to confirmed.
    # composites has no cleared staff, so there is no scif demand -> no collision impact.
    rows = [["composites-uav", 5, 200, "audit_pending"]]
    return _write("space_capacity_phase4.csv",
                  ["site_id", "space_type_id", "capacity", "capacity_status"], rows)


# =============================================================================
# PHASE 5 — DECISION LAYER
# =============================================================================
# Deterministic decision queue: one OVERDUE, one CLOSING (within 30 days of a
# mid-2026 "today"), one OPEN (far out), and one already DECIDED (feeds the
# decision-latency KPI). No site is special-cased; these just light up the paths.
def seed_decisions():
    rows = [
        # site_id, space_type_id, source, title, options_summary, owner, decide_by_date, decided_at, decision_note, created_at
        ["seattle-hub", 1, "collision",
         "Seattle desk wall: expand floor or cap engineering hiring",
         "expand floor / relocate a team / cap hiring", "VP Facilities",
         "2026-05-01", "", "", "2026-04-01"],                                   # OVERDUE (decide-by in the past)
        ["hq-flagship", 5, "accreditation",
         "HQ SCIF accreditation: commit the build or defer cleared hiring",
         "commit build / defer hiring / lease accredited space", "Security Director",
         "2026-07-20", "", "", "2026-06-01"],                                   # CLOSING (within 30 days)
        ["long-beach", "", "incentive",
         "Long Beach incentive: confirm the hiring ramp to hold the grant",
         "accelerate hiring / renegotiate terms / accept clawback", "Corp Dev",
         "2027-03-01", "", "", "2026-06-15"],                                   # OPEN (far out)
        ["arsenal-campus", 4, "collision",
         "Arsenal parking wall: structured-deck expansion",
         "build deck / shuttle + offsite / stagger shifts", "VP Facilities",
         "2026-03-01", "2026-02-20", "Approved structured-deck expansion; funded FY26",
         "2026-01-10"],                                                          # DECIDED (latency 41 days)
    ]
    return _write("decisions.csv",
                  ["site_id", "space_type_id", "source", "title", "options_summary",
                   "owner", "decide_by_date", "decided_at", "decision_note", "created_at"], rows)


def main():
    paths = [
        seed_sites(), seed_leases(), seed_hris(), seed_mrp(),
        seed_programs(), seed_quality(), seed_acquired(), seed_actions(),
        seed_archetypes(), seed_space_types(), seed_archetype_space_map(),
        seed_space_capacity(), seed_requisition_pipeline(),
        seed_forecast_snapshots(), seed_incentive_agreements(),
        seed_accreditation_milestones(), seed_onboarding_cohorts(),
        seed_phase4_space_capacity(), seed_decisions(),
    ]
    print("Seeded source-system exports:")
    for p in paths:
        print("  •", os.path.relpath(p))


if __name__ == "__main__":
    main()
