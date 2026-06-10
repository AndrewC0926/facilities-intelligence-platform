-- =============================================================================
-- Facilities Intelligence Platform — canonical schema
-- =============================================================================
-- One source of truth for a multi-site hardware company's facilities portfolio.
-- Five tables, each fed by a different upstream "system of record":
--   sites / leases ........ the canonical facilities registry + cost layer
--   headcount_snapshots ... HRIS export (who works where, by quarter)
--   production_demand ..... MRP export (what we plan to build, by quarter)
--   quality_issues ........ ERP/CMMS export + the 30-second intake form
--
-- Everything downstream (the SQL views, the dashboard, the Tableau extracts)
-- joins across these five tables. No business logic lives here — just the
-- normalized shape that the ETL reconciles dirty source exports into.
-- =============================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS quality_issues;
DROP TABLE IF EXISTS production_demand;
DROP TABLE IF EXISTS headcount_snapshots;
DROP TABLE IF EXISTS leases;
DROP TABLE IF EXISTS etl_exceptions;
DROP TABLE IF EXISTS sites;

-- Canonical facility registry. site_id is the join key the whole platform
-- agrees on; source_system records provenance so an acquired site that was
-- reconciled in is distinguishable from a clean canonical record.
CREATE TABLE sites (
    site_id            TEXT PRIMARY KEY,      -- e.g. 'costa-mesa'
    site_name          TEXT NOT NULL,         -- 'Costa Mesa HQ & Flagship Factory'
    region             TEXT,                  -- 'West' | 'Central' | 'Southeast'
    sq_ft              INTEGER,               -- gross square feet (NULL = unknown, e.g. mid-buildout)
    seat_capacity      INTEGER,               -- desks/stations the building supports
    power_kw_capacity  INTEGER,               -- electrical service ceiling, kW (NULL = unknown, e.g. mid-buildout)
    site_type          TEXT,                  -- 'factory' | 'campus' | 'office' | 'warehouse'
    status             TEXT,                  -- 'operational' | 'buildout' | 'acquired'
    source_system      TEXT NOT NULL DEFAULT 'canonical'  -- 'canonical' | 'acquired_import'
);

-- Cost layer. $/sq ft is derived downstream from (annual_rent + opex) / sq_ft.
CREATE TABLE leases (
    lease_id         INTEGER PRIMARY KEY,
    site_id          TEXT NOT NULL REFERENCES sites(site_id),
    annual_rent_usd  REAL,                    -- annualized base rent, USD (cleaned)
    opex_usd_yr      REAL,                    -- taxes, CAM, utilities, USD/yr
    start_date       TEXT,                    -- ISO 'YYYY-MM-DD'
    end_date         TEXT,
    lease_type       TEXT                     -- 'owned' | 'leased'
);

-- HRIS snapshot: assigned employees per site, per program, per quarter.
CREATE TABLE headcount_snapshots (
    snapshot_id  INTEGER PRIMARY KEY,
    site_id      TEXT NOT NULL REFERENCES sites(site_id),
    quarter      TEXT NOT NULL,               -- 'YYYY-Qn'
    program      TEXT NOT NULL,               -- 'Anvil' | 'Sentinel' | 'Forge' | ...
    headcount    INTEGER NOT NULL
);

-- MRP demand: planned production that consumes BOTH floor space and electrical
-- power. Demanded sq ft = units_planned * sqft_per_unit; demanded kW =
-- units_planned * kw_per_unit. This table drives the multi-constraint collision
-- detector — a site can hit its power ceiling before it runs out of floor space.
CREATE TABLE production_demand (
    demand_id      INTEGER PRIMARY KEY,
    site_id        TEXT NOT NULL REFERENCES sites(site_id),
    quarter        TEXT NOT NULL,             -- 'YYYY-Qn'
    program        TEXT NOT NULL,
    units_planned  INTEGER NOT NULL,
    sqft_per_unit  REAL NOT NULL,             -- floor space one unit's line/cell consumes
    kw_per_unit    REAL NOT NULL DEFAULT 0    -- electrical load one unit's line/cell draws, kW
);

-- ERP/CMMS quality issues + anything submitted via the 30-second intake form.
CREATE TABLE quality_issues (
    issue_id       INTEGER PRIMARY KEY,
    site_id        TEXT NOT NULL REFERENCES sites(site_id),
    quarter        TEXT NOT NULL,
    category       TEXT,                       -- 'facility' | 'equipment' | 'safety' | 'supply'
    severity       INTEGER,                    -- 1 (minor) .. 5 (critical)
    status         TEXT,                       -- 'open' | 'closed'
    reported_date  TEXT,                       -- ISO 'YYYY-MM-DD'
    description    TEXT
);

-- Quarantine table: rows the ETL could not safely place (e.g. an issue whose
-- site code matches no known site). Surfaced in the reconciliation report so a
-- human can resolve them — nothing is silently dropped.
CREATE TABLE etl_exceptions (
    exception_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file   TEXT,
    raw_row       TEXT,                        -- the offending row, as-received
    reason        TEXT                         -- why it was quarantined
);
