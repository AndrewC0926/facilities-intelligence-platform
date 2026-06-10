-- =============================================================================
-- Facilities Intelligence Platform — SEMANTIC LAYER (the product)
-- =============================================================================
-- These views ARE the deliverable. All business logic lives here in plain,
-- readable SQL — never in the dashboard. In production, Tableau connects live
-- to these exact views (or pulls scheduled extracts); our Streamlit app reads
-- the same views. Swap the front end and nothing else changes.
--
-- Every view carries a comment block:  business question / who asks / cadence.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- vw_quality_by_site_quarter
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Which sites have quality problems, and are they getting
--                      better or worse over time?"
-- WHO ASKS          : VP Facilities, Quality Director, site GMs.
-- REFRESH CADENCE   : Daily (ERP/CMMS feed + live intake form writes).
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_quality_by_site_quarter;
CREATE VIEW vw_quality_by_site_quarter AS
SELECT
    s.site_id,
    s.site_name,
    s.region,
    q.quarter,
    COUNT(*)                                            AS issue_count,
    ROUND(AVG(q.severity), 2)                           AS avg_severity,
    SUM(CASE WHEN q.status = 'open' THEN 1 ELSE 0 END)  AS open_count,
    SUM(CASE WHEN q.severity >= 4 THEN 1 ELSE 0 END)    AS critical_count
FROM quality_issues q
JOIN sites s ON s.site_id = q.site_id
GROUP BY s.site_id, s.site_name, s.region, q.quarter;


-- -----------------------------------------------------------------------------
-- vw_cost_per_sqft
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "What does each site cost us per square foot, all-in?"
-- WHO ASKS          : CFO, VP Facilities, real-estate / lease admin.
-- REFRESH CADENCE   : Monthly (lease terms change slowly).
-- NOTE              : Null-safe — a mid-buildout site with unknown sq_ft yields
--                     NULL cost_per_sqft rather than a divide-by-zero error, so
--                     it shows up as "data pending" instead of breaking the tile.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_cost_per_sqft;
CREATE VIEW vw_cost_per_sqft AS
SELECT
    s.site_id,
    s.site_name,
    s.region,
    s.site_type,
    s.status,
    s.sq_ft,
    l.lease_type,
    ROUND(COALESCE(l.annual_rent_usd, 0) + COALESCE(l.opex_usd_yr, 0), 0) AS total_annual_cost_usd,
    CASE
        WHEN s.sq_ft IS NULL OR s.sq_ft = 0 THEN NULL
        ELSE ROUND((COALESCE(l.annual_rent_usd, 0) + COALESCE(l.opex_usd_yr, 0)) / s.sq_ft, 2)
    END AS cost_per_sqft_usd
FROM sites s
LEFT JOIN leases l ON l.site_id = s.site_id;


-- -----------------------------------------------------------------------------
-- vw_headcount_vs_seats
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Where are we over capacity (people with no desk) and
--                      where are we paying for empty seats?"
-- WHO ASKS          : VP Facilities, Space Planning, Finance.
-- REFRESH CADENCE   : Quarterly (tracks the HRIS snapshot cadence).
-- NOTE              : Headcount is summed across all programs at a site for the
--                     quarter, then compared to the building's seat_capacity.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_headcount_vs_seats;
CREATE VIEW vw_headcount_vs_seats AS
SELECT
    s.site_id,
    s.site_name,
    s.region,
    h.quarter,
    s.seat_capacity,
    SUM(h.headcount)                                       AS total_headcount,
    CASE
        WHEN s.seat_capacity IS NULL OR s.seat_capacity = 0 THEN NULL
        ELSE ROUND(100.0 * SUM(h.headcount) / s.seat_capacity, 1)
    END                                                    AS seat_utilization_pct,
    CASE
        WHEN s.seat_capacity IS NULL OR s.seat_capacity = 0 THEN 'unknown'
        WHEN SUM(h.headcount) > s.seat_capacity            THEN 'over capacity'
        WHEN SUM(h.headcount) < 0.6 * s.seat_capacity      THEN 'under-utilized'
        ELSE 'healthy'
    END                                                    AS capacity_flag
FROM headcount_snapshots h
JOIN sites s ON s.site_id = h.site_id
GROUP BY s.site_id, s.site_name, s.region, h.quarter, s.seat_capacity;


-- -----------------------------------------------------------------------------
-- vw_capacity_vs_demand
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "How much floor space AND power does MRP's production plan
--                      demand at each site each quarter, versus what the building
--                      has on each constraint?"
-- WHO ASKS          : VP Facilities, Ops / Production planning, Program leads.
-- REFRESH CADENCE   : Weekly (MRP demand re-plans frequently).
-- NOTE              : This is the building block for the collision detector below.
--                     Two ceilings now: floor square footage and electrical kW.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_capacity_vs_demand;
CREATE VIEW vw_capacity_vs_demand AS
SELECT
    s.site_id,
    s.site_name,
    d.quarter,
    s.sq_ft                                               AS available_sqft,
    ROUND(SUM(d.units_planned * d.sqft_per_unit), 0)      AS demanded_sqft,
    CASE
        WHEN s.sq_ft IS NULL OR s.sq_ft = 0 THEN NULL
        ELSE ROUND(100.0 * SUM(d.units_planned * d.sqft_per_unit) / s.sq_ft, 1)
    END                                                   AS floor_utilization_pct,
    s.power_kw_capacity                                   AS available_kw,
    ROUND(SUM(d.units_planned * d.kw_per_unit), 0)        AS demanded_kw,
    CASE
        WHEN s.power_kw_capacity IS NULL OR s.power_kw_capacity = 0 THEN NULL
        ELSE ROUND(100.0 * SUM(d.units_planned * d.kw_per_unit) / s.power_kw_capacity, 1)
    END                                                   AS power_utilization_pct
FROM production_demand d
JOIN sites s ON s.site_id = d.site_id
GROUP BY s.site_id, s.site_name, d.quarter, s.sq_ft, s.power_kw_capacity;


-- -----------------------------------------------------------------------------
-- vw_capacity_collision   ★ the predictive "wow" view ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Which sites will outgrow their building, on WHICH constraint,
--                      and WHEN? A site can run out of POWER before it runs out of
--                      floor space — warn me ~2 quarters before whichever ceiling
--                      binds first, while there's still time to lease, upgrade the
--                      electrical service, expand, or shift a program."
-- WHO ASKS          : VP Facilities, COO, Special Projects / capital planning.
-- REFRESH CADENCE   : Weekly (rides the MRP feed).
-- METHOD            : For each site, take the linear quarter-over-quarter growth in
--                     MRP-demanded sq ft AND in MRP-demanded kW, project both
--                     forward, and compute the quarter in which each crosses 85% of
--                     that ceiling ("the wall"). The BINDING constraint is whichever
--                     wall is hit first. Quarter labels are real calendar quarters,
--                     so the warning is DATED, not just "soon".
-- COLUMNS           : The floor-space columns (current_util_pct, quarters_to_wall,
--                     projected_breach_quarter, collision_status, ...) are unchanged
--                     and still describe the FLOOR constraint. Parallel power_* columns
--                     describe the POWER constraint. binding_* columns report whichever
--                     of the two binds first — that is the number to act on.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_capacity_collision;
CREATE VIEW vw_capacity_collision AS
WITH demand AS (
    -- demanded sq ft AND kW per site per quarter, plus an absolute quarter index
    -- (year*4 + quarter-1) so we can do arithmetic on quarters
    SELECT
        d.site_id,
        d.quarter,
        CAST(substr(d.quarter, 1, 4) AS INTEGER) * 4
            + CAST(substr(d.quarter, 7, 1) AS INTEGER) - 1   AS q_index,
        SUM(d.units_planned * d.sqft_per_unit)               AS demanded_sqft,
        SUM(d.units_planned * d.kw_per_unit)                 AS demanded_kw
    FROM production_demand d
    GROUP BY d.site_id, d.quarter
),
bounds AS (
    -- earliest & latest observed quarter per site, and the span between them
    SELECT
        site_id,
        MIN(q_index) AS first_q,
        MAX(q_index) AS last_q
    FROM demand
    GROUP BY site_id
),
trend AS (
    -- linear growth on each constraint: (latest - earliest) / quarters elapsed
    SELECT
        b.site_id,
        b.last_q,
        last_d.quarter                                       AS last_quarter,
        first_d.demanded_sqft                                AS first_demand,
        last_d.demanded_sqft                                 AS last_demand,
        CASE WHEN b.last_q = b.first_q THEN 0
             ELSE (last_d.demanded_sqft - first_d.demanded_sqft) * 1.0
                  / (b.last_q - b.first_q)
        END                                                  AS growth_per_q,
        first_d.demanded_kw                                  AS first_kw,
        last_d.demanded_kw                                   AS last_kw,
        CASE WHEN b.last_q = b.first_q THEN 0
             ELSE (last_d.demanded_kw - first_d.demanded_kw) * 1.0
                  / (b.last_q - b.first_q)
        END                                                  AS growth_kw_per_q
    FROM bounds b
    JOIN demand first_d ON first_d.site_id = b.site_id AND first_d.q_index = b.first_q
    JOIN demand last_d  ON last_d.site_id  = b.site_id AND last_d.q_index  = b.last_q
),
proj AS (
    SELECT
        s.site_id,
        s.site_name,
        s.region,
        -- ---- FLOOR constraint (unchanged columns) ----------------------------
        s.sq_ft                                                  AS available_sqft,
        t.last_quarter                                           AS latest_quarter,
        ROUND(t.last_demand, 0)                                  AS latest_demanded_sqft,
        ROUND(t.growth_per_q, 0)                                 AS growth_sqft_per_quarter,
        CASE WHEN s.sq_ft IS NULL OR s.sq_ft = 0 THEN NULL
             ELSE ROUND(100.0 * t.last_demand / s.sq_ft, 1) END  AS current_util_pct,
        CASE WHEN s.sq_ft IS NULL OR s.sq_ft = 0 THEN NULL
             ELSE ROUND(100.0 * (t.last_demand + 2 * t.growth_per_q) / s.sq_ft, 1) END
                                                                 AS projected_util_2q_pct,
        CASE
            WHEN s.sq_ft IS NULL OR s.sq_ft = 0 THEN NULL
            WHEN t.growth_per_q <= 0 THEN NULL
            WHEN t.last_demand >= 0.85 * s.sq_ft THEN 0
            ELSE CAST((0.85 * s.sq_ft - t.last_demand) / t.growth_per_q + 0.999999 AS INTEGER)
        END                                                      AS quarters_to_wall,
        CASE
            WHEN s.sq_ft IS NULL OR s.sq_ft = 0 OR t.growth_per_q <= 0 THEN NULL
            ELSE
                CAST((t.last_q +
                      CASE WHEN t.last_demand >= 0.85 * s.sq_ft THEN 0
                           ELSE CAST((0.85 * s.sq_ft - t.last_demand) / t.growth_per_q + 0.999999 AS INTEGER)
                      END) / 4 AS INTEGER)
                || '-Q' ||
                CAST((t.last_q +
                      CASE WHEN t.last_demand >= 0.85 * s.sq_ft THEN 0
                           ELSE CAST((0.85 * s.sq_ft - t.last_demand) / t.growth_per_q + 0.999999 AS INTEGER)
                      END) % 4 + 1 AS INTEGER)
        END                                                      AS projected_breach_quarter,
        CASE
            WHEN s.sq_ft IS NULL OR s.sq_ft = 0          THEN 'unknown — capacity data pending'
            WHEN t.growth_per_q <= 0                     THEN 'stable'
            WHEN t.last_demand >= 0.85 * s.sq_ft         THEN 'AT THE WALL NOW'
            WHEN (0.85 * s.sq_ft - t.last_demand) / t.growth_per_q <= 2 THEN 'COLLISION WARNING'
            WHEN (0.85 * s.sq_ft - t.last_demand) / t.growth_per_q <= 4 THEN 'watch'
            ELSE 'ok'
        END                                                      AS collision_status,
        -- ---- POWER constraint (parallel columns) -----------------------------
        s.power_kw_capacity                                      AS available_kw,
        ROUND(t.last_kw, 0)                                      AS latest_demanded_kw,
        ROUND(t.growth_kw_per_q, 0)                              AS growth_kw_per_quarter,
        CASE WHEN s.power_kw_capacity IS NULL OR s.power_kw_capacity = 0 THEN NULL
             ELSE ROUND(100.0 * t.last_kw / s.power_kw_capacity, 1) END  AS power_util_pct,
        CASE WHEN s.power_kw_capacity IS NULL OR s.power_kw_capacity = 0 THEN NULL
             ELSE ROUND(100.0 * (t.last_kw + 2 * t.growth_kw_per_q) / s.power_kw_capacity, 1) END
                                                                 AS projected_power_util_2q_pct,
        CASE
            WHEN s.power_kw_capacity IS NULL OR s.power_kw_capacity = 0 THEN NULL
            WHEN t.growth_kw_per_q <= 0 THEN NULL
            WHEN t.last_kw >= 0.85 * s.power_kw_capacity THEN 0
            ELSE CAST((0.85 * s.power_kw_capacity - t.last_kw) / t.growth_kw_per_q + 0.999999 AS INTEGER)
        END                                                      AS power_quarters_to_wall,
        CASE
            WHEN s.power_kw_capacity IS NULL OR s.power_kw_capacity = 0 OR t.growth_kw_per_q <= 0 THEN NULL
            ELSE
                CAST((t.last_q +
                      CASE WHEN t.last_kw >= 0.85 * s.power_kw_capacity THEN 0
                           ELSE CAST((0.85 * s.power_kw_capacity - t.last_kw) / t.growth_kw_per_q + 0.999999 AS INTEGER)
                      END) / 4 AS INTEGER)
                || '-Q' ||
                CAST((t.last_q +
                      CASE WHEN t.last_kw >= 0.85 * s.power_kw_capacity THEN 0
                           ELSE CAST((0.85 * s.power_kw_capacity - t.last_kw) / t.growth_kw_per_q + 0.999999 AS INTEGER)
                      END) % 4 + 1 AS INTEGER)
        END                                                      AS power_breach_quarter,
        CASE
            WHEN s.power_kw_capacity IS NULL OR s.power_kw_capacity = 0  THEN 'unknown — capacity data pending'
            WHEN t.growth_kw_per_q <= 0                                  THEN 'stable'
            WHEN t.last_kw >= 0.85 * s.power_kw_capacity                 THEN 'AT THE WALL NOW'
            WHEN (0.85 * s.power_kw_capacity - t.last_kw) / t.growth_kw_per_q <= 2 THEN 'COLLISION WARNING'
            WHEN (0.85 * s.power_kw_capacity - t.last_kw) / t.growth_kw_per_q <= 4 THEN 'watch'
            ELSE 'ok'
        END                                                      AS power_status
    FROM trend t
    JOIN sites s ON s.site_id = t.site_id
)
SELECT
    proj.*,
    -- BINDING constraint = whichever wall is hit first. A NULL quarters_to_wall
    -- means that constraint has no projected collision, so the other one binds.
    CASE
        WHEN quarters_to_wall IS NULL AND power_quarters_to_wall IS NULL THEN 'none'
        WHEN power_quarters_to_wall IS NULL                              THEN 'floor'
        WHEN quarters_to_wall IS NULL                                    THEN 'power'
        WHEN power_quarters_to_wall <= quarters_to_wall                  THEN 'power'
        ELSE 'floor'
    END                                                          AS binding_constraint,
    CASE
        WHEN quarters_to_wall IS NULL AND power_quarters_to_wall IS NULL THEN NULL
        WHEN power_quarters_to_wall IS NULL                              THEN quarters_to_wall
        WHEN quarters_to_wall IS NULL                                    THEN power_quarters_to_wall
        WHEN power_quarters_to_wall <= quarters_to_wall                  THEN power_quarters_to_wall
        ELSE quarters_to_wall
    END                                                          AS binding_quarters_to_wall,
    CASE
        WHEN quarters_to_wall IS NULL AND power_quarters_to_wall IS NULL THEN NULL
        WHEN power_quarters_to_wall IS NULL                              THEN projected_breach_quarter
        WHEN quarters_to_wall IS NULL                                    THEN power_breach_quarter
        WHEN power_quarters_to_wall <= quarters_to_wall                  THEN power_breach_quarter
        ELSE projected_breach_quarter
    END                                                          AS binding_breach_quarter,
    CASE
        WHEN quarters_to_wall IS NULL AND power_quarters_to_wall IS NULL THEN collision_status
        WHEN power_quarters_to_wall IS NULL                              THEN collision_status
        WHEN quarters_to_wall IS NULL                                    THEN power_status
        WHEN power_quarters_to_wall <= quarters_to_wall                  THEN power_status
        ELSE collision_status
    END                                                          AS binding_status
FROM proj;


-- -----------------------------------------------------------------------------
-- vw_reconciliation_status
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Where do we stand on folding the acquired site in — how many
--                      records auto-reconciled, and how many items still need a human
--                      decision?"
-- WHO ASKS          : VP Facilities, Special Projects, the exec brief.
-- REFRESH CADENCE   : Per ETL run (rides every pipeline build).
-- NOTE              : Reads the live DB, so the exec brief and RECONCILIATION.md
--                     report the same numbers. Every un-reconcilable row (orphans
--                     AND the CAD/USD currency conflict) is persisted to
--                     etl_exceptions, so open_exceptions is the single source of truth.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_reconciliation_status;
CREATE VIEW vw_reconciliation_status AS
SELECT
    (SELECT COUNT(*) FROM sites WHERE source_system = 'acquired_import') AS acquired_sites,
    (SELECT COUNT(*) FROM etl_exceptions)                                AS open_exceptions;
