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

DROP TABLE IF EXISTS requisition_pipeline;
DROP TABLE IF EXISTS space_capacity;
DROP TABLE IF EXISTS archetype_space_map;
DROP TABLE IF EXISTS space_types;
DROP TABLE IF EXISTS archetypes;
DROP TABLE IF EXISTS actions;
DROP TABLE IF EXISTS programs;
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
    site_id                TEXT PRIMARY KEY,      -- e.g. 'arsenal-campus'
    site_name              TEXT NOT NULL,         -- 'Arsenal Campus'
    region                 TEXT,                  -- 'West' | 'South' | 'Northeast' | 'Southeast' | 'Mountain'
    sq_ft                  INTEGER,               -- usable/operational gross sq ft (NULL = unknown, e.g. mid-buildout)
    seat_capacity          INTEGER,               -- desks/stations the building supports
    power_kw_capacity      INTEGER,               -- electrical service ceiling, kW (NULL = unknown / audit pending)
    site_type              TEXT,                  -- 'factory' | 'campus' | 'office' | 'warehouse'
    status                 TEXT,                  -- coarse: 'operational' | 'buildout' | 'acquired'
    site_status            TEXT,                  -- lifecycle: 'operational' | 'buildout' | 'acquired_integrating' | 'acquired_complete'
    integration_start_date TEXT,                  -- ISO 'YYYY-MM-DD' an acquisition's integration clock started (NULL if n/a)
    lease_expiration_date  TEXT,                  -- ISO 'YYYY-MM-DD' (NULL for owned sites / unknown)
    lease_option_deadline  TEXT,                  -- ISO 'YYYY-MM-DD': last day to exercise a renew/expand option
    source_system          TEXT NOT NULL DEFAULT 'canonical'  -- 'canonical' | 'acquired_import'
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
    archetype    TEXT,                         -- worker archetype (FK-ish to archetypes.name); NULL = unclassified
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

-- Workflow layer: trackable actions generated from insights. Every open item has
-- an owner and a due date, so an analytic finding (a collision warning, a
-- reconciliation exception, a quality hotspot) becomes a task someone owns rather
-- than a chart someone forgets. site_id is nullable: an orphan-record action
-- references no real site (that is exactly why it is an exception).
CREATE TABLE actions (
    action_id        INTEGER PRIMARY KEY,
    site_id          TEXT REFERENCES sites(site_id),  -- NULL = no canonical site (e.g. an orphan record)
    source           TEXT,                            -- 'collision' | 'reconciliation' | 'quality'
    title            TEXT NOT NULL,
    owner            TEXT,                             -- accountable person/role
    due_date         TEXT,                             -- ISO 'YYYY-MM-DD'
    status           TEXT,                             -- 'open' | 'in_progress' | 'resolved'
    resolution_note  TEXT,                             -- filled when resolved
    created_at       TEXT                              -- ISO 'YYYY-MM-DD' the action was opened
);

-- Program registry: the products the facilities exist to build, mapped to the
-- sites that build them. A program runs at a primary site (and optionally a
-- secondary), ramps from a current to a target output, and carries the per-unit
-- floor and power footprint of its production line. This is what turns a capacity
-- collision from a building problem into a PROGRAM problem ("which program stops,
-- and how far short of its target") — see vw_program_facility_risk.
CREATE TABLE programs (
    program_id                INTEGER PRIMARY KEY,
    program_name              TEXT NOT NULL,
    program_type              TEXT,    -- autonomous_aircraft | autonomous_underwater | munitions | c2_software | directed_energy | sensor
    primary_site_id           TEXT REFERENCES sites(site_id),
    secondary_site_id         TEXT REFERENCES sites(site_id),   -- nullable
    status                    TEXT,    -- 'production' | 'development' | 'integration'
    units_per_quarter_current INTEGER, -- NULL for software programs (tracked by headcount, not units)
    units_per_quarter_target  INTEGER,
    kw_per_unit               REAL,    -- electrical load one unit's line/cell draws, kW
    sqft_per_unit             REAL     -- floor space one unit's line/cell consumes
);

-- =============================================================================
-- PHASE 3 — OCCUPANCY & SEAT-DEMAND LAYER (fully data-driven)
-- =============================================================================
-- The thesis: "headcount" is not one number, it is N demand curves. Worker
-- archetypes consume different SPACE TYPES at different ratios, with different
-- lead times. Everything here is CONFIGURATION AS DATA — archetypes, space types,
-- ratios, lead times, capacities — so the model works for any facility (current
-- or not-yet-built) with zero code changes. No archetype, space type, ratio, or
-- lead time is ever hardcoded in a view or in Python.

-- Worker archetypes. Seed six, but any set works.
CREATE TABLE archetypes (
    archetype_id  INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,   -- 'production' | 'engineer' | 'cleared' | ...
    description   TEXT
);

-- Space types, with the lead time to add ONE unit (per-row data, so any org can
-- tune them). restricted_sensing flags accredited space where sensor-based
-- occupancy is unavailable (ICD 705) and the model must degrade to badge/booking.
CREATE TABLE space_types (
    space_type_id      INTEGER PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,   -- 'desk' | 'bench' | 'workstation' | 'parking_stall' | 'scif_seat'
    unit_label         TEXT,                   -- how one unit is counted ('seat', 'stall', ...)
    lead_time_days     INTEGER,                -- days to provision one more unit of this space
    restricted_sensing INTEGER NOT NULL DEFAULT 0  -- 1 = accredited, sensor occupancy unavailable (badge/booking only)
);

-- How much of each space type one worker of an archetype consumes. Ratios are
-- DATA, never constants in a view. A missing (archetype, space_type) pair means
-- that archetype needs none of that space.
CREATE TABLE archetype_space_map (
    archetype_id   INTEGER NOT NULL REFERENCES archetypes(archetype_id),
    space_type_id  INTEGER NOT NULL REFERENCES space_types(space_type_id),
    ratio          REAL NOT NULL,          -- units of space per worker (e.g. 0.67 parking stalls)
    PRIMARY KEY (archetype_id, space_type_id)
);

-- Per-site supply of each space type. A site simply omits rows for space types it
-- doesn't have. capacity_status distinguishes real supply from pending/planned:
--   'confirmed'     -> a real ceiling, projected against normally
--   'audit_pending' -> capacity unknown (e.g. SCIF accreditation in progress);
--                      capacity is NULL and must report data-pending, never a breach
--   'planned'       -> future supply not yet built; reports supportable headcount,
--                      not a breach (same null-safe spirit as the power NULL handling)
CREATE TABLE space_capacity (
    site_id         TEXT NOT NULL REFERENCES sites(site_id),
    space_type_id   INTEGER NOT NULL REFERENCES space_types(space_type_id),
    capacity        INTEGER,                -- NULL when audit_pending
    capacity_status TEXT NOT NULL DEFAULT 'confirmed',
    PRIMARY KEY (site_id, space_type_id)
);

-- The LEADING indicator: open requisitions per site/archetype/quarter, and how
-- long they take to fill. HRIS headcount is trailing; these open reqs become
-- future-quarter seat demand once filled (fill quarter = req quarter + ceil(fill/quarter)).
CREATE TABLE requisition_pipeline (
    req_id                INTEGER PRIMARY KEY,
    site_id               TEXT NOT NULL REFERENCES sites(site_id),
    archetype_id          INTEGER NOT NULL REFERENCES archetypes(archetype_id),
    quarter               TEXT NOT NULL,     -- 'YYYY-Qn' the reqs are open
    open_reqs             INTEGER NOT NULL,
    avg_time_to_fill_days INTEGER            -- people-side lead time (vs. space-side lead_time_days)
);
