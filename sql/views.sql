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


-- =============================================================================
-- PHASE 2 — WORKFLOW LAYER
-- The views below turn analytics into a workflow: trackable actions, a lease-cliff
-- calendar, and a composite site-health score. Same rule as everything above —
-- all business logic lives here in plain SQL, never in the dashboard.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- vw_open_actions
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "What insights have become work that someone owns, and what
--                      is still open?"
-- WHO ASKS          : VP Facilities, the exec brief, every site GM.
-- REFRESH CADENCE   : Live (writes land in the actions table directly).
-- NOTE              : Age-banding (green/yellow/red) is time-relative, so it lives
--                     in fip/actions.py (with an injectable "today") rather than
--                     here — this view just exposes the open items and their dates.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_open_actions;
CREATE VIEW vw_open_actions AS
SELECT
    a.action_id,
    a.site_id,
    COALESCE(s.site_name, '(no canonical site)')  AS site_name,
    a.source,
    a.title,
    a.owner,
    a.due_date,
    a.status,
    a.created_at
FROM actions a
LEFT JOIN sites s ON s.site_id = a.site_id
WHERE a.status IN ('open', 'in_progress')
ORDER BY a.created_at;


-- -----------------------------------------------------------------------------
-- vw_lease_cliff   ★ the "decide before two walls converge" view ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "For each site, how much runway is there between the lease
--                      option deadline (when we must commit to renew/expand) and
--                      the quarter demand outgrows the building? If that window is
--                      short, the real-estate decision and the capacity decision
--                      collide."
-- WHO ASKS          : VP Facilities, CFO / real-estate, Special Projects.
-- REFRESH CADENCE   : Weekly (rides the collision feed + lease calendar).
-- METHOD            : Map the binding breach quarter ('YYYY-Qn') to the first day
--                     of that quarter, then decision_window_days = that date minus
--                     the lease option deadline. < 180 days => AT RISK (you'd be
--                     committing to a lease before you know if the site fits).
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_lease_cliff;
CREATE VIEW vw_lease_cliff AS
WITH cliff AS (
    SELECT
        s.site_id,
        s.site_name,
        s.lease_expiration_date,
        s.lease_option_deadline,
        c.binding_constraint,
        c.binding_breach_quarter,
        -- first day of the binding breach quarter: month = (q-1)*3 + 1
        CASE WHEN c.binding_breach_quarter IS NULL THEN NULL
             ELSE substr(c.binding_breach_quarter, 1, 4) || '-'
                  || substr('0' || ((CAST(substr(c.binding_breach_quarter, 7, 1) AS INTEGER) - 1) * 3 + 1), -2)
                  || '-01'
        END AS breach_date
    FROM sites s
    LEFT JOIN vw_capacity_collision c ON c.site_id = s.site_id
)
SELECT
    site_id,
    site_name,
    lease_expiration_date,
    lease_option_deadline,
    binding_constraint,
    binding_breach_quarter,
    breach_date,
    CASE WHEN lease_option_deadline IS NULL OR breach_date IS NULL THEN NULL
         ELSE CAST(julianday(breach_date) - julianday(lease_option_deadline) AS INTEGER)
    END AS decision_window_days,
    CASE
        WHEN lease_option_deadline IS NULL THEN 'no lease cliff'
        WHEN breach_date IS NULL           THEN 'no breach projected'
        WHEN CAST(julianday(breach_date) - julianday(lease_option_deadline) AS INTEGER) < 180
                                           THEN 'AT RISK'
        ELSE 'ok'
    END AS cliff_status
FROM cliff;


-- -----------------------------------------------------------------------------
-- vw_site_health   ★ one number per site, with its four drivers ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "If I could see one health score per site — and what's
--                      dragging it down — which sites need attention?"
-- WHO ASKS          : VP Facilities, COO, site GMs.
-- REFRESH CADENCE   : Weekly (rides quality + capacity + cost feeds).
-- METHOD            : Composite 0-100 = the simple average of four equally-weighted
--                     components, each scored 0-100:
--                       1. capacity headroom = 100 - tightest utilization (floor or power)
--                       2. quality           = 100 - (12*open issues + 8*critical-open), floored at 0
--                       3. cost efficiency   = 100 at/below the portfolio MEDIAN $/sqft,
--                                              penalized above it (proportional to median)
--                       4. data completeness = non-null critical fields / 5 * 100, where the
--                                              critical fields are sq_ft, seat_capacity,
--                                              power_kw_capacity, region, site_type
--                     A component with no data (e.g. unknown utilization or cost)
--                     scores 0 — you can't credit headroom you can't see.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_site_health;
CREATE VIEW vw_site_health AS
WITH med AS (
    -- portfolio median $/sqft (avg of the middle one/two of the non-null costs)
    SELECT AVG(cost_per_sqft_usd) AS median_cost FROM (
        SELECT cost_per_sqft_usd
        FROM vw_cost_per_sqft
        WHERE cost_per_sqft_usd IS NOT NULL
        ORDER BY cost_per_sqft_usd
        LIMIT 2 - (SELECT COUNT(*) FROM vw_cost_per_sqft WHERE cost_per_sqft_usd IS NOT NULL) % 2
        OFFSET (SELECT (COUNT(*) - 1) / 2 FROM vw_cost_per_sqft WHERE cost_per_sqft_usd IS NOT NULL)
    )
),
quality AS (
    SELECT site_id,
           SUM(open_count)     AS open_issues,
           SUM(critical_count) AS critical_open
    FROM vw_quality_by_site_quarter
    GROUP BY site_id
),
util AS (
    -- tightest utilization (whichever constraint is closer to its wall)
    SELECT site_id, MAX(COALESCE(current_util_pct, 0), COALESCE(power_util_pct, 0)) AS tightest_util,
           (current_util_pct IS NULL AND power_util_pct IS NULL) AS util_unknown
    FROM vw_capacity_collision
),
comp AS (
    SELECT
        s.site_id,
        s.site_name,
        s.region,
        -- 1. capacity headroom (0 if utilization is unknown)
        CASE WHEN u.site_id IS NULL OR u.util_unknown THEN 0
             ELSE MAX(0.0, MIN(100.0, 100.0 - u.tightest_util)) END           AS capacity_score,
        -- 2. quality (no issues -> 100)
        MAX(0.0, 100.0 - (12.0 * COALESCE(q.open_issues, 0)
                          + 8.0 * COALESCE(q.critical_open, 0)))               AS quality_score,
        -- 3. cost efficiency vs portfolio median (0 if cost unknown)
        CASE
            WHEN cps.cost_per_sqft_usd IS NULL OR m.median_cost IS NULL THEN 0
            WHEN cps.cost_per_sqft_usd <= m.median_cost THEN 100.0
            ELSE MAX(0.0, 100.0 - 100.0 * (cps.cost_per_sqft_usd - m.median_cost) / m.median_cost)
        END                                                                   AS cost_score,
        -- 4. data completeness over 5 critical fields
        20.0 * (
            (s.sq_ft IS NOT NULL)
          + (s.seat_capacity IS NOT NULL)
          + (s.power_kw_capacity IS NOT NULL)
          + (s.region IS NOT NULL)
          + (s.site_type IS NOT NULL)
        )                                                                     AS completeness_score
    FROM sites s
    CROSS JOIN med m
    LEFT JOIN quality q   ON q.site_id   = s.site_id
    LEFT JOIN util u      ON u.site_id   = s.site_id
    LEFT JOIN vw_cost_per_sqft cps ON cps.site_id = s.site_id
)
SELECT
    site_id,
    site_name,
    region,
    ROUND(capacity_score, 1)     AS capacity_score,
    ROUND(quality_score, 1)      AS quality_score,
    ROUND(cost_score, 1)         AS cost_score,
    ROUND(completeness_score, 1) AS completeness_score,
    ROUND((capacity_score + quality_score + cost_score + completeness_score) / 4.0, 1)
                                 AS health_score
FROM comp;
