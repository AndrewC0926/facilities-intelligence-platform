"""
Seed generator — writes the simulated upstream "system exports" as CSVs into
seeds/. These stand in for what you'd actually receive from ERP, MRP, and HRIS:
mostly clean, but with deliberate, narratable dirt (especially the acquired
site) so the ETL's reconciliation has real work to do. The cleaning is the demo.

Deterministic: no randomness, so `make demo` produces the same portfolio every
time and the tests can assert exact outcomes.

8 sites, loosely modeled on a defense-hardware footprint:
  costa-mesa     5M sq ft HQ + flagship factory, headcount ramping past its seats
  atlanta-campus new ~$1B campus mid-buildout, capacity data still pending (NULLs)
  austin-fab     mature factory, healthy baseline
  huntsville     mature factory with a high quality-issue rate (the problem site)
  boston-rd      small R&D office, paying for empty seats (under-utilized)
  seattle-ops    big cheap warehouse — the $/sq ft outlier
  phoenix-line   factory whose MRP demand is ramping into the wall (collision!)
  quantico-acq   recently acquired — arrives as a messy dump with bad site codes
"""
import csv
import os

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds")
QUARTERS = ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]


def _write(name, header, rows):
    os.makedirs(SEED_DIR, exist_ok=True)
    path = os.path.join(SEED_DIR, name)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


# -- sites_master.csv : the 7 clean canonical sites (quantico arrives dirty, separately)
def seed_sites():
    rows = [
        # site_id, site_name, region, sq_ft, seat_capacity, power_kw_capacity, site_type, status, source_system
        ["costa-mesa",     "Costa Mesa HQ & Flagship Factory", "West",      5000000, 12000, 60000, "factory",   "operational", "canonical"],
        ["atlanta-campus", "Atlanta Innovation Campus",        "Southeast", "",      "",    "",    "campus",    "buildout",    "canonical"],
        ["austin-fab",     "Austin Fabrication",               "Central",   800000,  3000,  20000, "factory",   "operational", "canonical"],
        ["huntsville",     "Huntsville Integration",           "Southeast", 600000,  2500,  15000, "factory",   "operational", "canonical"],
        ["boston-rd",      "Boston R&D Lab",                   "Northeast", 60000,   400,   1500,  "office",    "operational", "canonical"],
        ["seattle-ops",    "Seattle Logistics & Warehouse",    "West",      1200000, 500,   8000,  "warehouse", "operational", "canonical"],
        ["phoenix-line",   "Phoenix Production Line",          "West",      200000,  1200,  4800,  "factory",   "operational", "canonical"],
    ]
    return _write("sites_master.csv",
                  ["site_id", "site_name", "region", "sq_ft", "seat_capacity", "power_kw_capacity", "site_type", "status", "source_system"],
                  rows)


# -- leases.csv : clean cost layer for the canonical sites.
#    seattle-ops is the cheap-per-sqft outlier; atlanta has huge rent but unknown sq_ft.
def seed_leases():
    rows = [
        # site_id, annual_rent_usd, opex_usd_yr, start_date, end_date, lease_type
        ["costa-mesa",     0,         18500000, "2018-01-01", "2038-01-01", "owned"],
        ["atlanta-campus", 42000000,  9000000,  "2024-06-01", "2044-06-01", "owned"],   # ~$1B campus, sq_ft NULL -> cost/sqft NULL
        ["austin-fab",     6200000,   2100000,  "2020-03-01", "2030-03-01", "leased"],
        ["huntsville",     4800000,   1700000,  "2019-09-01", "2029-09-01", "leased"],
        ["boston-rd",      2400000,   600000,   "2022-01-01", "2027-01-01", "leased"],
        ["seattle-ops",    3600000,   900000,   "2021-05-01", "2031-05-01", "leased"],  # huge sq_ft -> very low $/sqft
        ["phoenix-line",   2900000,   1100000,  "2023-02-01", "2033-02-01", "leased"],
    ]
    return _write("leases.csv",
                  ["site_id", "annual_rent_usd", "opex_usd_yr", "start_date", "end_date", "lease_type"],
                  rows)


# -- hris_export.csv : headcount per site/program/quarter.
#    Dirt: costa-mesa Q2 Anvil appears twice with conflicting totals (dedupe test);
#    boston-rd Q1 uses MM/YYYY date drift ('03/2025'); one quantico row uses a bad code.
def seed_hris():
    rows = []
    ramp = {  # costa-mesa ramps past its 12,000 seats by Q4
        "Anvil":    [5000, 5800, 6600, 7600],
        "Sentinel": [4000, 4400, 5000, 5800],
    }
    for prog, vals in ramp.items():
        for q, hc in zip(QUARTERS, vals):
            rows.append(["costa-mesa", q, prog, hc])
    # the deliberate duplicate: conflicting Q2 Anvil number, correct value comes later in file
    rows.append(["costa-mesa", "2025-Q2", "Anvil", 9999])   # bad dupe (will be superseded)
    rows.append(["costa-mesa", "2025-Q2", "Anvil", 5800])   # canonical value, kept

    steady = {
        "austin-fab":   ("Forge",      [2400, 2420, 2450, 2480]),
        "huntsville":   ("Sentinel",   [2000, 2050, 2100, 2150]),
        "boston-rd":    ("Lab",        [180,  185,  190,  195]),   # under-utilized vs 400 seats
        "seattle-ops":  ("Logistics",  [300,  310,  320,  330]),
        "phoenix-line": ("Anvil",      [600,  700,  800,  900]),
    }
    for site, (prog, vals) in steady.items():
        for q, hc in zip(QUARTERS, vals):
            # boston-rd Q1 arrives with a MM/YYYY date instead of a quarter label
            qlabel = "03/2025" if (site == "boston-rd" and q == "2025-Q1") else q
            rows.append([site, qlabel, prog, hc])
    # atlanta campus just beginning to staff up (partial data)
    rows.append(["atlanta-campus", "2025-Q3", "Sentinel", 50])
    rows.append(["atlanta-campus", "2025-Q4", "Sentinel", 200])
    # acquired site headcount arrives with a messy code
    rows.append(["Quantico Acq.", "2025-Q4", "Recon", 850])
    return _write("hris_export.csv", ["site_id", "quarter", "program", "headcount"], rows)


# -- mrp_export.csv : production demand on TWO constraints — floor space and power.
#    phoenix-line is engineered to ramp into the wall, and to hit its POWER ceiling
#    one quarter BEFORE its floor ceiling (the binding constraint is power).
def seed_mrp():
    rows = []
    # phoenix FLOOR: 500 sqft/unit, units 240->300 => demanded 120k->150k vs 200k sq ft.
    #   85% wall = 170k; growth = 10k/q => floor breach ~2 quarters out => 2026-Q2.
    # phoenix POWER: 13 kW/unit, units 240->300 => demanded 3,120->3,900 kW vs 4,800 kW.
    #   85% wall = 4,080 kW; growth = 260 kW/q => power breach ~1 quarter out => 2026-Q1.
    #   => POWER is the binding constraint, hitting the wall before floor space.
    for q, units in zip(QUARTERS, [240, 260, 280, 300]):
        rows.append(["phoenix-line", q, "Anvil", units, 500, 13])
    # everyone else: comfortably within both ceilings, flat (no collision on either)
    flat = {
        "costa-mesa":  ("Anvil",     1200, 1000, 10),   # 1.2M sqft / 12,000 kW
        "austin-fab":  ("Forge",     1500, 200,  5),    # 300k sqft / 7,500 kW
        "huntsville":  ("Sentinel",  1000, 150,  6),    # 150k sqft / 6,000 kW
        "seattle-ops": ("Logistics", 2000, 50,   1),    # 100k sqft / 2,000 kW
        "boston-rd":   ("Lab",       100,  100,  5),     # 10k sqft / 500 kW
    }
    for site, (prog, units, spu, kw) in flat.items():
        for q in QUARTERS:
            rows.append([site, q, prog, units, spu, kw])
    # atlanta campus has demand but its sq_ft AND power capacity are unknown
    # (mid-buildout) -> 'capacity data pending' on both constraints
    for q, units in zip(["2025-Q3", "2025-Q4"], [100, 200]):
        rows.append(["atlanta-campus", q, "Sentinel", units, 400, 30])
    return _write("mrp_export.csv",
                  ["site_id", "quarter", "program", "units_planned", "sqft_per_unit", "kw_per_unit"], rows)


# -- erp_quality.csv : quality/CMMS issues. huntsville is the hot site.
#    Dirt: one row points at 'tucson-line', a site that does not exist (orphan -> quarantine);
#    one quantico row uses the 'QNTC' code.
def seed_quality():
    rows = []

    def add(site, q, cat, sev, status, date, desc):
        rows.append([site, q, cat, sev, status, date, desc])

    # huntsville — many issues, high severity, several still open
    add("huntsville", "2025-Q1", "equipment", 5, "open",   "2025-02-11", "CNC spindle failure halting line 3")
    add("huntsville", "2025-Q1", "safety",    4, "closed", "2025-03-02", "Forklift near-miss in receiving")
    add("huntsville", "2025-Q2", "facility",  4, "open",   "2025-05-19", "Roof leak over clean assembly bay")
    add("huntsville", "2025-Q2", "equipment", 5, "open",   "2025-06-04", "Test chamber HVAC out of spec")
    add("huntsville", "2025-Q3", "supply",    3, "closed", "2025-08-21", "Solder paste lot rejected at incoming QA")
    add("huntsville", "2025-Q3", "safety",    4, "open",   "2025-09-09", "Repeated eyewash station pressure fault")
    add("huntsville", "2025-Q4", "equipment", 5, "open",   "2025-11-13", "Robotic arm calibration drift, scrap rate up")
    add("huntsville", "2025-Q4", "facility",  3, "closed", "2025-12-01", "Loading dock door off track")

    # the rest — lighter load, mostly closed
    add("costa-mesa",   "2025-Q3", "facility",  2, "closed", "2025-08-04", "HVAC zone imbalance, west wing")
    add("costa-mesa",   "2025-Q4", "equipment", 3, "open",   "2025-10-22", "Conveyor sensor intermittent")
    add("austin-fab",   "2025-Q2", "supply",    2, "closed", "2025-04-30", "Anodize bath chemistry off")
    add("phoenix-line", "2025-Q4", "facility",  3, "open",   "2025-11-28", "Insufficient floor space staging WIP")  # foreshadows collision
    add("seattle-ops",  "2025-Q3", "safety",    2, "closed", "2025-07-15", "Racking inspection finding")
    add("boston-rd",    "2025-Q2", "equipment", 1, "closed", "2025-05-06", "Fume hood airflow low")

    # acquired site, messy code
    add("QNTC", "2025-Q4", "facility", 4, "open", "2025-12-09", "Generator transfer switch unreliable (inherited)")
    # ORPHAN: site that exists in no registry -> ETL must quarantine, not crash
    add("tucson-line", "2025-Q3", "equipment", 3, "open", "2025-09-30", "Press brake overdue for PM (unknown site)")

    return _write("erp_quality.csv",
                  ["site_id", "quarter", "category", "severity", "status", "reported_date", "description"], rows)


# -- acquired_site_dump.csv : the recently-acquired company's own facilities export.
#    Different column names, and deliberately dirty: two rows for the SAME real site
#    under different codes (QNTC vs quantico), one with NULL sq_ft/seats, rent as a
#    formatted string, and one row denominated in CAD. This is the reconciliation muscle.
def seed_acquired():
    rows = [
        # facility_code, facility_name, loc_region, gross_sq_ft, workstations, annual_rent, op_ex, currency, lease_kind
        ["QNTC",     "Quantico Acq.",        "Mid-Atlantic", "",       "",    "$1,200,000", "$340,000", "USD", "leased"],
        ["quantico", "Quantico Acquisition", "Mid-Atlantic", "145000", "850", "1,500,000",  "410,000",  "CAD", "leased"],
    ]
    return _write("acquired_site_dump.csv",
                  ["facility_code", "facility_name", "loc_region", "gross_sq_ft",
                   "workstations", "annual_rent", "op_ex", "currency", "lease_kind"], rows)


def main():
    paths = [
        seed_sites(), seed_leases(), seed_hris(),
        seed_mrp(), seed_quality(), seed_acquired(),
    ]
    print("Seeded source-system exports:")
    for p in paths:
        print("  •", os.path.relpath(p))


if __name__ == "__main__":
    main()
