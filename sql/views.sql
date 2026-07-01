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
--                     EXEMPTION: buildout and acquired_integrating sites are NOT
--                     penalized on completeness — NULL fields there are expected, not
--                     negligence. Their real completeness is surfaced separately in
--                     vw_integration_pipeline.
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
        -- 4. data completeness over 5 critical fields. Buildout / acquired_integrating
        --    sites are EXEMPT (expected NULLs) -> not penalized; flagged separately
        --    in vw_integration_pipeline instead of dragging the health score.
        CASE
            WHEN s.site_status IN ('buildout', 'acquired_integrating') THEN 100.0
            ELSE 20.0 * (
                (s.sq_ft IS NOT NULL)
              + (s.seat_capacity IS NOT NULL)
              + (s.power_kw_capacity IS NOT NULL)
              + (s.region IS NOT NULL)
              + (s.site_type IS NOT NULL)
            )
        END                                                                   AS completeness_score
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


-- =============================================================================
-- PHASE 3 SCALE — PROGRAMS & INTEGRATION
-- =============================================================================


-- -----------------------------------------------------------------------------
-- vw_program_facility_risk   ★ the "so what" of the collision detector ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "A building is about to hit a wall — so WHICH PROGRAMS does
--                      that stop, how far short of their unit target, and how many
--                      quarters until it bites?"
-- WHO ASKS          : COO, Program leads, Capital planning.
-- REFRESH CADENCE   : Weekly (rides the collision + program feeds).
-- METHOD            : Join the program registry to the collision detector by the
--                     program's primary site. Programs at the site whose binding
--                     constraint is most urgent sort to the top — that is where a
--                     facilities limit becomes a delivery-target miss.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_program_facility_risk;
CREATE VIEW vw_program_facility_risk AS
SELECT
    p.program_name,
    p.program_type,
    p.status                                    AS program_status,
    p.primary_site_id                           AS site_id,
    s.site_name,
    p.units_per_quarter_current,
    p.units_per_quarter_target,
    c.binding_constraint,
    c.binding_status,
    c.binding_breach_quarter,
    c.binding_quarters_to_wall                  AS quarters_to_constraint
FROM programs p
JOIN sites s ON s.site_id = p.primary_site_id
LEFT JOIN vw_capacity_collision c ON c.site_id = p.primary_site_id
ORDER BY (c.binding_quarters_to_wall IS NULL), c.binding_quarters_to_wall, p.program_name;


-- -----------------------------------------------------------------------------
-- vw_integration_pipeline
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Which sites are still being stood up or folded in, how
--                      complete is their data, and which integrations are stalling
--                      (old but still missing the basics)?"
-- WHO ASKS          : VP Facilities, Corp Dev / M&A integration, Data governance.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : All non-operational sites (buildout + acquired_integrating).
--                     Critical fields = sq_ft, seat_capacity, power_kw_capacity,
--                     lease_expiration_date, lease_option_deadline. completeness_pct
--                     = non-null/5*100. stalled_flag fires when the integration is
--                     >12 months old AND completeness is still below 80% — a NULL
--                     here is expected early, but not a year in.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_integration_pipeline;
CREATE VIEW vw_integration_pipeline AS
WITH base AS (
    SELECT
        site_id,
        site_name,
        site_status,
        integration_start_date,
        ((sq_ft IS NULL) + (seat_capacity IS NULL) + (power_kw_capacity IS NULL)
         + (lease_expiration_date IS NULL) + (lease_option_deadline IS NULL))  AS null_critical_fields,
        20.0 * ((sq_ft IS NOT NULL) + (seat_capacity IS NOT NULL) + (power_kw_capacity IS NOT NULL)
                + (lease_expiration_date IS NOT NULL) + (lease_option_deadline IS NOT NULL))
                                                                               AS completeness_pct
    FROM sites
    WHERE site_status IN ('buildout', 'acquired_integrating')
)
SELECT
    site_id,
    site_name,
    site_status,
    integration_start_date,
    null_critical_fields,
    completeness_pct,
    CASE
        WHEN integration_start_date IS NOT NULL
         AND (julianday('now') - julianday(integration_start_date)) > 365
         AND completeness_pct < 80 THEN 1
        ELSE 0
    END                                                                        AS stalled_flag
FROM base
ORDER BY completeness_pct, site_id;


-- =============================================================================
-- PHASE 3 — OCCUPANCY & SEAT-DEMAND LAYER  (fully data-driven, per space_type)
-- =============================================================================
-- "Headcount" is not one number — it is N demand curves, one per worker archetype,
-- each consuming different SPACE TYPES at ratios that live in archetype_space_map.
-- These views read that configuration as DATA: no archetype, space type, ratio, or
-- lead time is hardcoded here. A site added tomorrow (any subset of space types)
-- flows through unchanged.
--
-- CLASSIFIED MODE (ICD 705): for space types flagged restricted_sensing = 1 (e.g.
-- scif_seat), sensor-based occupancy is NOT available in accredited space. By design
-- the model degrades to headcount + badge/booking-style counts only — every number
-- below for restricted space comes from headcount and the requisition pipeline, never
-- from a live occupancy sensor.


-- -----------------------------------------------------------------------------
-- vw_space_demand
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "How many units of each SPACE TYPE does each site demand, by
--                      quarter, once you account for who actually works there (by
--                      archetype) and who is being hired (the pipeline)?"
-- WHO ASKS          : Space Planning, VP Facilities, workforce planning.
-- REFRESH CADENCE   : Weekly (HRIS snapshot + live req pipeline).
-- METHOD            : headcount x archetype_space_map = current-staff demand per
--                      space type per observed quarter; pipeline open_reqs convert to
--                      FUTURE-quarter demand at fill quarter = req_quarter +
--                      ceil(time_to_fill / one_quarter). Current-staff demand is
--                      carried forward into pipeline quarters, so the curve rises
--                      with both the staffed trend and committed hiring.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_space_demand;
CREATE VIEW vw_space_demand AS
WITH hc AS (          -- current-staff demand per (site, space_type, quarter)
    SELECT
        h.site_id,
        m.space_type_id,
        CAST(substr(h.quarter, 1, 4) AS INTEGER) * 4
            + CAST(substr(h.quarter, 7, 1) AS INTEGER) - 1        AS q_index,
        SUM(h.headcount * m.ratio)                                AS units
    FROM headcount_snapshots h
    JOIN archetypes a          ON a.name = h.archetype
    JOIN archetype_space_map m ON m.archetype_id = a.archetype_id
    GROUP BY h.site_id, m.space_type_id, q_index
),
pipe AS (             -- pipeline reqs -> demand at their FUTURE fill quarter
    SELECT
        p.site_id,
        m.space_type_id,
        (CAST(substr(p.quarter, 1, 4) AS INTEGER) * 4
            + CAST(substr(p.quarter, 7, 1) AS INTEGER) - 1)
            + CAST(p.avg_time_to_fill_days / 91.0 + 0.999999 AS INTEGER)  AS q_index,
        SUM(p.open_reqs * m.ratio)                                AS units
    FROM requisition_pipeline p
    JOIN archetype_space_map m ON m.archetype_id = p.archetype_id
    GROUP BY p.site_id, m.space_type_id, q_index
),
spine AS (            -- every quarter where demand is defined, per (site, space_type)
    SELECT site_id, space_type_id, q_index FROM hc
    UNION
    SELECT site_id, space_type_id, q_index FROM pipe
)
SELECT
    d.site_id,
    d.space_type_id,
    st.name          AS space_type,
    st.unit_label,
    st.restricted_sensing,
    (d.q_index / 4) || '-Q' || (d.q_index % 4 + 1)  AS quarter,
    d.q_index,
    ROUND(d.demand_headcount, 2)                     AS demanded_from_headcount,
    ROUND(d.demand_pipeline, 2)                      AS demanded_from_pipeline,
    ROUND(d.demand_headcount + d.demand_pipeline, 2) AS demanded_units
FROM (
    SELECT
        sp.site_id,
        sp.space_type_id,
        sp.q_index,
        -- current-staff demand carried forward to future quarters (latest known <= q)
        COALESCE((SELECT hc2.units FROM hc hc2
                   WHERE hc2.site_id = sp.site_id AND hc2.space_type_id = sp.space_type_id
                     AND hc2.q_index <= sp.q_index
                   ORDER BY hc2.q_index DESC LIMIT 1), 0)          AS demand_headcount,
        -- pipeline reqs cumulatively filled by this quarter
        COALESCE((SELECT SUM(pp.units) FROM pipe pp
                   WHERE pp.site_id = sp.site_id AND pp.space_type_id = sp.space_type_id
                     AND pp.q_index <= sp.q_index), 0)             AS demand_pipeline
    FROM spine sp
) d
JOIN space_types st ON st.space_type_id = d.space_type_id;


-- -----------------------------------------------------------------------------
-- vw_space_collision   ★ per-space-type collision, reports each site's binding one ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "For each site, which SPACE TYPE runs out first, and when?
--                      Desks are rarely the answer at industrial sites."
-- WHO ASKS          : VP Facilities, Space Planning, COO.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : The SAME projection as vw_capacity_collision (linear growth,
--                      85% wall, dated breach quarter) applied per (site, space_type)
--                      over vw_space_demand. capacity_status governs null-safety:
--                      'audit_pending'/NULL -> data pending (never a false breach);
--                      'planned' -> reports supportable units, not a breach;
--                      'confirmed' -> projected normally. is_binding = 1 marks the
--                      space type that hits its wall first at each site. Works for a
--                      site with one space type or ten, identically.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_space_collision;
CREATE VIEW vw_space_collision AS
WITH bounds AS (
    SELECT site_id, space_type_id, MIN(q_index) AS first_q, MAX(q_index) AS last_q
    FROM vw_space_demand
    GROUP BY site_id, space_type_id
),
trend AS (
    SELECT
        b.site_id, b.space_type_id, b.last_q,
        f.demanded_units AS first_demand,
        l.demanded_units AS last_demand,
        CASE WHEN b.last_q = b.first_q THEN 0
             ELSE (l.demanded_units - f.demanded_units) * 1.0 / (b.last_q - b.first_q)
        END AS growth_per_q
    FROM bounds b
    JOIN vw_space_demand f ON f.site_id = b.site_id AND f.space_type_id = b.space_type_id AND f.q_index = b.first_q
    JOIN vw_space_demand l ON l.site_id = b.site_id AND l.space_type_id = b.space_type_id AND l.q_index = b.last_q
),
proj AS (
    SELECT
        t.site_id, s.site_name,
        t.space_type_id, st.name AS space_type, st.unit_label,
        st.lead_time_days, st.restricted_sensing,
        cap.capacity, cap.capacity_status,
        (t.last_q / 4) || '-Q' || (t.last_q % 4 + 1)   AS latest_quarter,
        ROUND(t.last_demand, 1)                         AS latest_demanded_units,
        ROUND(t.growth_per_q, 2)                        AS growth_per_quarter,
        CASE WHEN cap.capacity IS NULL OR cap.capacity = 0 OR cap.capacity_status <> 'confirmed' THEN NULL
             ELSE ROUND(100.0 * t.last_demand / cap.capacity, 1) END           AS current_util_pct,
        CASE WHEN cap.capacity IS NULL OR cap.capacity = 0 OR cap.capacity_status <> 'confirmed' THEN NULL
             ELSE ROUND(100.0 * (t.last_demand + 2 * t.growth_per_q) / cap.capacity, 1) END AS projected_util_2q_pct,
        CASE
            WHEN cap.capacity IS NULL OR cap.capacity = 0 OR cap.capacity_status <> 'confirmed' THEN NULL
            WHEN t.growth_per_q <= 0 THEN NULL
            WHEN t.last_demand >= 0.85 * cap.capacity THEN 0
            ELSE CAST((0.85 * cap.capacity - t.last_demand) / t.growth_per_q + 0.999999 AS INTEGER)
        END                                             AS quarters_to_wall,
        CASE
            WHEN cap.capacity IS NULL OR cap.capacity = 0 OR cap.capacity_status <> 'confirmed' OR t.growth_per_q <= 0 THEN NULL
            ELSE
                CAST((t.last_q +
                      CASE WHEN t.last_demand >= 0.85 * cap.capacity THEN 0
                           ELSE CAST((0.85 * cap.capacity - t.last_demand) / t.growth_per_q + 0.999999 AS INTEGER)
                      END) / 4 AS INTEGER)
                || '-Q' ||
                CAST((t.last_q +
                      CASE WHEN t.last_demand >= 0.85 * cap.capacity THEN 0
                           ELSE CAST((0.85 * cap.capacity - t.last_demand) / t.growth_per_q + 0.999999 AS INTEGER)
                      END) % 4 + 1 AS INTEGER)
        END                                             AS breach_quarter,
        CASE
            WHEN cap.capacity IS NULL OR cap.capacity_status = 'audit_pending' THEN 'data pending — audit'
            WHEN cap.capacity_status = 'planned'                               THEN 'planned supply'
            WHEN cap.capacity = 0                                              THEN 'data pending — audit'
            WHEN t.growth_per_q <= 0                                           THEN 'stable'
            WHEN t.last_demand >= 0.85 * cap.capacity                          THEN 'AT THE WALL NOW'
            WHEN (0.85 * cap.capacity - t.last_demand) / t.growth_per_q <= 2   THEN 'COLLISION WARNING'
            WHEN (0.85 * cap.capacity - t.last_demand) / t.growth_per_q <= 4   THEN 'watch'
            ELSE 'ok'
        END                                             AS space_status,
        -- supportable units at the 85% planning wall (defined for confirmed + planned)
        CASE WHEN cap.capacity IS NULL OR cap.capacity = 0 OR cap.capacity_status = 'audit_pending' THEN NULL
             ELSE ROUND(0.85 * cap.capacity, 0) END     AS supportable_units
    FROM trend t
    JOIN sites s        ON s.site_id = t.site_id
    JOIN space_types st ON st.space_type_id = t.space_type_id
    JOIN space_capacity cap ON cap.site_id = t.site_id AND cap.space_type_id = t.space_type_id
)
SELECT
    proj.*,
    CASE WHEN quarters_to_wall IS NOT NULL
              AND ROW_NUMBER() OVER (PARTITION BY site_id
                    ORDER BY (quarters_to_wall IS NULL), quarters_to_wall, space_type_id) = 1
         THEN 1 ELSE 0 END                              AS is_binding
FROM proj;


-- -----------------------------------------------------------------------------
-- vw_time_to_seat   ★ is facilities, not hiring, the bottleneck? ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "For each site and archetype, does it take LONGER to build the
--                      space than to hire the person? If so, facilities — not
--                      recruiting — is what caps growth."
-- WHO ASKS          : VP Facilities, Talent, COO.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : Compare the people-side lead time (avg_time_to_fill_days, from
--                      the pipeline) against the space-side lead time (lead_time_days)
--                      of the site's BINDING space type — the space that actually
--                      gates seats. If the archetype consumes that binding space and
--                      space_lead > fill_time, flag 'facilities_bottleneck'. Ample
--                      space never gates hiring, even if slow to build.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_time_to_seat;
CREATE VIEW vw_time_to_seat AS
WITH latest_pipe AS (
    SELECT
        p.site_id, p.archetype_id, p.avg_time_to_fill_days, p.open_reqs, p.quarter,
        ROW_NUMBER() OVER (PARTITION BY p.site_id, p.archetype_id ORDER BY p.quarter DESC) AS rn
    FROM requisition_pipeline p
),
binding AS (
    SELECT site_id, space_type_id, space_type, lead_time_days
    FROM vw_space_collision WHERE is_binding = 1
)
SELECT
    lp.site_id,
    s.site_name,
    a.name                                              AS archetype,
    lp.open_reqs,
    lp.avg_time_to_fill_days                            AS time_to_fill_days,
    b.space_type                                        AS binding_space_type,
    b.lead_time_days                                    AS time_to_seat_days,
    CASE
        WHEN b.space_type_id IS NOT NULL
         AND EXISTS (SELECT 1 FROM archetype_space_map m
                      WHERE m.archetype_id = lp.archetype_id
                        AND m.space_type_id = b.space_type_id AND m.ratio > 0)
         AND b.lead_time_days > lp.avg_time_to_fill_days
        THEN 'facilities_bottleneck'
        ELSE 'ok'
    END                                                 AS bottleneck_flag
FROM latest_pipe lp
JOIN archetypes a ON a.archetype_id = lp.archetype_id
JOIN sites s      ON s.site_id = lp.site_id
LEFT JOIN binding b ON b.site_id = lp.site_id
WHERE lp.rn = 1;


-- -----------------------------------------------------------------------------
-- vw_plan_reconciliation
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Three plans disagree — authorized headcount (HRIS), what the
--                      hiring pipeline implies, and what the SPACE can actually
--                      support. Where, and by how much?"
-- WHO ASKS          : COO, VP Facilities, Finance, workforce planning.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : authorized = latest HRIS total; pipeline_implied = authorized +
--                      open reqs; space_supportable = authorized scaled by the binding
--                      space's headroom to its 85% wall (0.85*capacity / current
--                      demand). Delta columns expose each disagreement. NULL binding
--                      (all space ample, or audit-pending) -> supportable unknown.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_plan_reconciliation;
CREATE VIEW vw_plan_reconciliation AS
WITH auth AS (
    SELECT site_id, SUM(headcount) AS authorized_headcount
    FROM headcount_snapshots
    WHERE quarter = (SELECT MAX(quarter) FROM headcount_snapshots h2 WHERE h2.site_id = headcount_snapshots.site_id)
    GROUP BY site_id
),
pipe AS (
    SELECT site_id, SUM(open_reqs) AS open_reqs
    FROM requisition_pipeline
    WHERE quarter = (SELECT MAX(quarter) FROM requisition_pipeline r2 WHERE r2.site_id = requisition_pipeline.site_id)
    GROUP BY site_id
),
binding AS (
    SELECT site_id, space_type, capacity, latest_demanded_units
    FROM vw_space_collision WHERE is_binding = 1
)
SELECT
    a.site_id,
    s.site_name,
    b.space_type                                        AS binding_space_type,
    a.authorized_headcount,
    a.authorized_headcount + COALESCE(p.open_reqs, 0)   AS pipeline_implied_headcount,
    CASE WHEN b.latest_demanded_units IS NULL OR b.latest_demanded_units = 0 THEN NULL
         ELSE ROUND(a.authorized_headcount * (0.85 * b.capacity) / b.latest_demanded_units, 0)
    END                                                 AS space_supportable_headcount,
    COALESCE(p.open_reqs, 0)                            AS delta_pipeline_vs_authorized,
    CASE WHEN b.latest_demanded_units IS NULL OR b.latest_demanded_units = 0 THEN NULL
         ELSE ROUND(a.authorized_headcount * (0.85 * b.capacity) / b.latest_demanded_units, 0)
              - (a.authorized_headcount + COALESCE(p.open_reqs, 0))
    END                                                 AS delta_supportable_vs_pipeline
FROM auth a
JOIN sites s ON s.site_id = a.site_id
LEFT JOIN pipe p    ON p.site_id = a.site_id
LEFT JOIN binding b ON b.site_id = a.site_id;


-- =============================================================================
-- PHASE 4 — CROSS-FUNCTIONAL & KPI (accountability) LAYER
-- =============================================================================
-- The platform grades itself here: scores its own aged forecasts, prices the cost
-- of waiting, holds sites to incentive and accreditation commitments, checks
-- day-one readiness, and rolls it all into a KPI scorecard. Phase 1-3 views are
-- untouched; these read on top of them. Config stays data; no site is named.


-- -----------------------------------------------------------------------------
-- vw_space_capacity_effective
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Which 'planned' / 'audit_pending' capacities may we now count
--                      on — i.e. which have actually cleared final accreditation?"
-- WHO ASKS          : VP Facilities, Security/accreditation, capital planning.
-- REFRESH CADENCE   : Weekly.
-- RULE              : a 'planned' or 'audit_pending' capacity counts as effectively
--                      'confirmed' ONLY once its final 'accreditation' milestone has an
--                      actual_date. Everything else keeps its raw status. (The Phase 3
--                      vw_space_collision still reads the raw table and is unchanged.)
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_space_capacity_effective;
CREATE VIEW vw_space_capacity_effective AS
SELECT
    sc.site_id,
    sc.space_type_id,
    st.name                                         AS space_type,
    sc.capacity,
    sc.capacity_status                              AS raw_status,
    CASE WHEN EXISTS (SELECT 1 FROM accreditation_milestones am
                       WHERE am.site_id = sc.site_id AND am.space_type_id = sc.space_type_id
                         AND am.milestone = 'accreditation' AND am.actual_date IS NOT NULL)
         THEN 1 ELSE 0 END                          AS accreditation_complete,
    CASE
        WHEN sc.capacity_status IN ('planned', 'audit_pending')
             AND EXISTS (SELECT 1 FROM accreditation_milestones am
                          WHERE am.site_id = sc.site_id AND am.space_type_id = sc.space_type_id
                            AND am.milestone = 'accreditation' AND am.actual_date IS NOT NULL)
        THEN 'confirmed'
        ELSE sc.capacity_status
    END                                             AS effective_status
FROM space_capacity sc
JOIN space_types st ON st.space_type_id = sc.space_type_id;


-- -----------------------------------------------------------------------------
-- vw_accreditation_pipeline
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "For each space still being stood up, what stage is it in, how
--                      far has it slipped, and is its capacity confirmable yet?"
-- WHO ASKS          : Security/accreditation, VP Facilities, capital planning.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : Order milestones design_approval -> construction -> inspection
--                      -> accreditation. current_stage = furthest with an actual_date;
--                      next_stage = earliest without one; max_slip_days = worst
--                      actual-minus-planned across met milestones.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_accreditation_pipeline;
CREATE VIEW vw_accreditation_pipeline AS
WITH m AS (
    SELECT
        site_id, space_type_id,
        SUM(CASE WHEN actual_date IS NOT NULL THEN 1 ELSE 0 END)  AS milestones_met,
        COUNT(*)                                                  AS milestones_total,
        MAX(CASE WHEN actual_date IS NOT NULL
                 THEN CAST(julianday(actual_date) - julianday(planned_date) AS INTEGER) END) AS max_slip_days,
        MAX(CASE WHEN milestone = 'accreditation' AND actual_date IS NOT NULL THEN 1 ELSE 0 END) AS accreditation_complete
    FROM accreditation_milestones
    GROUP BY site_id, space_type_id
)
SELECT
    m.site_id, s.site_name, m.space_type_id, st.name AS space_type,
    m.milestones_met, m.milestones_total,
    (SELECT am.milestone FROM accreditation_milestones am
      WHERE am.site_id = m.site_id AND am.space_type_id = m.space_type_id AND am.actual_date IS NOT NULL
      ORDER BY CASE am.milestone WHEN 'design_approval' THEN 1 WHEN 'construction' THEN 2
                                 WHEN 'inspection' THEN 3 WHEN 'accreditation' THEN 4 ELSE 5 END DESC
      LIMIT 1)                                                    AS current_stage,
    (SELECT am.milestone FROM accreditation_milestones am
      WHERE am.site_id = m.site_id AND am.space_type_id = m.space_type_id AND am.actual_date IS NULL
      ORDER BY CASE am.milestone WHEN 'design_approval' THEN 1 WHEN 'construction' THEN 2
                                 WHEN 'inspection' THEN 3 WHEN 'accreditation' THEN 4 ELSE 5 END ASC
      LIMIT 1)                                                    AS next_stage,
    COALESCE(m.max_slip_days, 0)                                  AS max_slip_days,
    m.accreditation_complete,
    CASE WHEN m.accreditation_complete = 1 THEN 'accredited — capacity confirmed'
         ELSE 'in progress — capacity unconfirmed' END           AS pipeline_status
FROM m
JOIN sites s        ON s.site_id = m.site_id
JOIN space_types st ON st.space_type_id = m.space_type_id;


-- -----------------------------------------------------------------------------
-- vw_forecast_accuracy   ★ the platform scores its own past forecasts ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "How good were our earlier breach forecasts? Forecast accuracy
--                      should be computable, not claimed."
-- WHO ASKS          : COO, VP Facilities (trust in the model).
-- REFRESH CADENCE   : Per pipeline run (each run appends a fresh snapshot).
-- METHOD            : Each aged snapshot is scored against the LATEST snapshot for the
--                      same site+space (the closest-to-truth 'actual'). error_quarters =
--                      |forecast breach - actual breach| in quarters; outcome = hit when
--                      the quarters match. Only snapshots older than the actual are scored.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_forecast_accuracy;
CREATE VIEW vw_forecast_accuracy AS
WITH actual AS (
    SELECT fs.* FROM forecast_snapshots fs
    WHERE fs.snapshot_date = (SELECT MAX(x.snapshot_date) FROM forecast_snapshots x
                               WHERE x.site_id = fs.site_id AND x.space_type_id = fs.space_type_id)
)
SELECT
    f.snapshot_id,
    f.snapshot_date                                 AS forecast_date,
    f.site_id, s.site_name,
    f.space_type_id, st.name                        AS space_type,
    f.predicted_breach_quarter                      AS forecast_breach_quarter,
    a.predicted_breach_quarter                      AS actual_breach_quarter,
    a.snapshot_date                                 AS actual_date,
    CASE WHEN f.predicted_breach_quarter IS NULL OR a.predicted_breach_quarter IS NULL THEN NULL
         ELSE ABS((CAST(substr(f.predicted_breach_quarter,1,4) AS INTEGER)*4 + CAST(substr(f.predicted_breach_quarter,7,1) AS INTEGER))
                - (CAST(substr(a.predicted_breach_quarter,1,4) AS INTEGER)*4 + CAST(substr(a.predicted_breach_quarter,7,1) AS INTEGER)))
    END                                             AS error_quarters,
    ROUND(ABS(f.predicted_util_pct - a.predicted_util_pct), 1) AS util_error_pct,
    CASE WHEN f.predicted_breach_quarter IS NULL OR a.predicted_breach_quarter IS NULL THEN NULL
         WHEN f.predicted_breach_quarter = a.predicted_breach_quarter THEN 'hit'
         ELSE 'miss' END                            AS outcome
FROM forecast_snapshots f
JOIN actual a       ON a.site_id = f.site_id AND a.space_type_id = f.space_type_id
JOIN sites s        ON s.site_id = f.site_id
JOIN space_types st ON st.space_type_id = f.space_type_id
WHERE f.snapshot_date < a.snapshot_date;


-- -----------------------------------------------------------------------------
-- vw_cost_of_delay
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "For each projected breach with a remedy on file, what does it
--                      cost to act NOW versus wait until the wall?"
-- WHO ASKS          : CFO, COO, VP Facilities.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : cost_now = est_remedy_cost_usd; cost_at_wall = remedy + delay cost
--                      per quarter x quarters_to_wall; cost_of_delay = the difference.
--                      Uses the site's binding capacity breach (vw_capacity_collision).
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_cost_of_delay;
CREATE VIEW vw_cost_of_delay AS
SELECT
    a.action_id, a.site_id, s.site_name, a.title,
    c.binding_constraint                            AS constraint_type,
    c.binding_breach_quarter                        AS breach_quarter,
    c.binding_quarters_to_wall                      AS quarters_to_wall,
    ROUND(a.est_remedy_cost_usd, 0)                 AS cost_act_now_usd,
    ROUND(a.est_remedy_cost_usd
          + a.est_delay_cost_usd_per_quarter * c.binding_quarters_to_wall, 0) AS cost_act_at_wall_usd,
    ROUND(a.est_delay_cost_usd_per_quarter * c.binding_quarters_to_wall, 0)   AS cost_of_delay_usd,
    a.est_delay_cost_usd_per_quarter                AS delay_cost_per_quarter_usd
FROM actions a
JOIN sites s              ON s.site_id = a.site_id
JOIN vw_capacity_collision c ON c.site_id = a.site_id
WHERE a.est_remedy_cost_usd IS NOT NULL
  AND c.binding_quarters_to_wall IS NOT NULL;


-- -----------------------------------------------------------------------------
-- vw_incentive_compliance
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Are we on track to meet the job and capex commitments behind our
--                      public incentives, and where is clawback money at risk?"
-- WHO ASKS          : CFO, Corp Dev, Site GMs.
-- REFRESH CADENCE   : Monthly.
-- METHOD            : actual jobs = latest HRIS total; shortfalls vs committed jobs/capex;
--                      days_to_measurement vs today. Reuses the lease-cliff window rule:
--                      inside 180 days WITH a shortfall => AT RISK.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_incentive_compliance;
CREATE VIEW vw_incentive_compliance AS
WITH actual_jobs AS (
    SELECT site_id, SUM(headcount) AS jobs
    FROM headcount_snapshots
    WHERE quarter = (SELECT MAX(quarter) FROM headcount_snapshots h2 WHERE h2.site_id = headcount_snapshots.site_id)
    GROUP BY site_id
)
SELECT
    ia.agreement_id, ia.site_id, s.site_name, ia.authority,
    ia.committed_jobs,
    COALESCE(aj.jobs, 0)                             AS actual_jobs,
    ia.committed_jobs - COALESCE(aj.jobs, 0)         AS jobs_shortfall,
    ia.committed_capex_usd, ia.actual_capex_usd,
    ia.committed_capex_usd - ia.actual_capex_usd     AS capex_shortfall_usd,
    ia.measurement_date,
    CAST(julianday(ia.measurement_date) - julianday('now') AS INTEGER) AS days_to_measurement,
    ia.clawback_risk_usd,
    CASE
        WHEN (ia.committed_jobs - COALESCE(aj.jobs, 0)) <= 0
             AND (ia.committed_capex_usd - ia.actual_capex_usd) <= 0 THEN 'met'
        WHEN CAST(julianday(ia.measurement_date) - julianday('now') AS INTEGER) < 180
             AND ((ia.committed_jobs - COALESCE(aj.jobs, 0)) > 0
                  OR (ia.committed_capex_usd - ia.actual_capex_usd) > 0) THEN 'AT RISK'
        ELSE 'on track'
    END                                             AS compliance_status
FROM incentive_agreements ia
JOIN sites s ON s.site_id = ia.site_id
LEFT JOIN actual_jobs aj ON aj.site_id = ia.site_id;


-- -----------------------------------------------------------------------------
-- vw_day_one_readiness
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "For cohorts starting within a quarter, is every day-one need
--                      (seat, equipment, badge, parking) actually in place?"
-- WHO ASKS          : VP Facilities, Onboarding/HR, Security.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : "Now" = the latest HRIS quarter. Cohorts starting this quarter or
--                      next are imminent; any missing readiness dimension flags them.
--                      Also carries the portfolio pct of imminent cohorts fully ready.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_day_one_readiness;
CREATE VIEW vw_day_one_readiness AS
WITH cur AS (
    SELECT MAX(CAST(substr(quarter,1,4) AS INTEGER)*4 + CAST(substr(quarter,7,1) AS INTEGER) - 1) AS cq
    FROM headcount_snapshots
)
SELECT
    c.cohort_id, c.site_id, s.site_name, a.name AS archetype,
    c.start_quarter, c.headcount,
    c.seat_ready, c.equipment_ready, c.badge_ready, c.parking_ready,
    (CAST(substr(c.start_quarter,1,4) AS INTEGER)*4 + CAST(substr(c.start_quarter,7,1) AS INTEGER) - 1)
        - (SELECT cq FROM cur)                      AS quarters_to_start,
    CASE WHEN (CAST(substr(c.start_quarter,1,4) AS INTEGER)*4 + CAST(substr(c.start_quarter,7,1) AS INTEGER) - 1)
              - (SELECT cq FROM cur) BETWEEN 0 AND 1 THEN 1 ELSE 0 END AS is_imminent,
    CASE WHEN (c.seat_ready AND c.equipment_ready AND c.badge_ready AND c.parking_ready) THEN 1 ELSE 0 END AS all_ready,
    TRIM((CASE WHEN c.seat_ready = 0 THEN 'seat ' ELSE '' END)
       || (CASE WHEN c.equipment_ready = 0 THEN 'equipment ' ELSE '' END)
       || (CASE WHEN c.badge_ready = 0 THEN 'badge ' ELSE '' END)
       || (CASE WHEN c.parking_ready = 0 THEN 'parking ' ELSE '' END))  AS not_ready_dimensions,
    CASE WHEN (CAST(substr(c.start_quarter,1,4) AS INTEGER)*4 + CAST(substr(c.start_quarter,7,1) AS INTEGER) - 1)
              - (SELECT cq FROM cur) BETWEEN 0 AND 1
              AND NOT (c.seat_ready AND c.equipment_ready AND c.badge_ready AND c.parking_ready)
         THEN 'NOT READY' ELSE 'ok' END             AS readiness_flag,
    (SELECT ROUND(100.0 * SUM(CASE WHEN (oc.seat_ready AND oc.equipment_ready AND oc.badge_ready AND oc.parking_ready) THEN 1 ELSE 0 END)
                  / COUNT(*), 1)
       FROM onboarding_cohorts oc
      WHERE (CAST(substr(oc.start_quarter,1,4) AS INTEGER)*4 + CAST(substr(oc.start_quarter,7,1) AS INTEGER) - 1)
            - (SELECT cq FROM cur) BETWEEN 0 AND 1)  AS portfolio_pct_fully_ready
FROM onboarding_cohorts c
JOIN sites s      ON s.site_id = c.site_id
JOIN archetypes a ON a.archetype_id = c.archetype_id;


-- -----------------------------------------------------------------------------
-- vw_kpi_scorecard   ★ the COO's one-screen accountability view ★
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION : "Against the KPIs a COO would actually set — facilities never the
--                      bottleneck, forecasts graded, capital spent on time, space in its
--                      corridor, people ready on day one — how are we doing?"
-- WHO ASKS          : COO, CEO staff, VP Facilities.
-- REFRESH CADENCE   : Weekly.
-- METHOD            : One row per KPI, each rolled up from a Phase 3/4 view. Every value
--                      is non-null (COALESCE'd) so the scorecard is always complete.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_kpi_scorecard;
CREATE VIEW vw_kpi_scorecard AS
SELECT * FROM (
    -- 1. worst-case time-to-seat vs time-to-fill by archetype
    SELECT 1 AS sort_order, 'worst_case_seat_gap' AS kpi_key,
        'Worst-case seat-vs-fill gap (facilities as bottleneck)' AS kpi_label,
        COALESCE(MAX(CASE WHEN bottleneck_flag = 'facilities_bottleneck'
                          THEN time_to_seat_days - time_to_fill_days END), 0) AS value,
        'days' AS unit, 'target <= 0' AS target,
        CASE WHEN COALESCE(MAX(CASE WHEN bottleneck_flag = 'facilities_bottleneck'
                                    THEN time_to_seat_days - time_to_fill_days END), 0) > 0
             THEN 'AT RISK' ELSE 'ok' END AS status,
        COALESCE((SELECT archetype || ' @ ' || site_name || ' (' || binding_space_type || ')'
                    FROM vw_time_to_seat WHERE bottleneck_flag = 'facilities_bottleneck'
                    ORDER BY (time_to_seat_days - time_to_fill_days) DESC LIMIT 1), 'none') AS detail
    FROM vw_time_to_seat

    UNION ALL
    -- 2. forecast accuracy (hit rate of aged forecasts)
    SELECT 2, 'forecast_accuracy', 'Forecast accuracy (aged-forecast hit rate)',
        COALESCE(ROUND(100.0 * SUM(CASE WHEN outcome = 'hit' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1), 0),
        'pct', 'target >= 70%',
        CASE WHEN COALESCE(100.0 * SUM(CASE WHEN outcome = 'hit' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 0) >= 70
             THEN 'ok' ELSE 'watch' END,
        COUNT(*) || ' aged forecast(s) scored'
    FROM vw_forecast_accuracy WHERE outcome IS NOT NULL

    UNION ALL
    -- 3. pct of collision actions opened with lead time remaining
    SELECT 3, 'actions_with_lead_time', 'Actions opened with lead time remaining',
        COALESCE(ROUND(100.0 * SUM(CASE WHEN c.binding_quarters_to_wall >= 1 THEN 1 ELSE 0 END)
                       / NULLIF(COUNT(*), 0), 1), 100.0),
        'pct', 'target = 100%',
        CASE WHEN COALESCE(100.0 * SUM(CASE WHEN c.binding_quarters_to_wall >= 1 THEN 1 ELSE 0 END)
                           / NULLIF(COUNT(*), 0), 100) >= 100 THEN 'ok' ELSE 'watch' END,
        COUNT(*) || ' open collision action(s)'
    FROM actions a
    JOIN vw_capacity_collision c ON c.site_id = a.site_id
    WHERE a.source = 'collision' AND a.status IN ('open', 'in_progress')
      AND c.binding_quarters_to_wall IS NOT NULL

    UNION ALL
    -- 4. utilization corridor compliance (space types inside their target band)
    SELECT 4, 'util_corridor_compliance', 'Utilization corridor compliance (in target band)',
        COALESCE(ROUND(100.0 * SUM(CASE WHEN sc.current_util_pct BETWEEN st.target_util_low AND st.target_util_high
                                        THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1), 0),
        'pct', 'target >= 60% in-band',
        CASE WHEN COALESCE(100.0 * SUM(CASE WHEN sc.current_util_pct BETWEEN st.target_util_low AND st.target_util_high
                                            THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 0) >= 60
             THEN 'ok' ELSE 'watch' END,
        COUNT(*) || ' confirmed space(s) measured'
    FROM vw_space_collision sc
    JOIN space_types st ON st.space_type_id = sc.space_type_id
    WHERE sc.current_util_pct IS NOT NULL

    UNION ALL
    -- 5. day-one readiness (imminent cohorts fully ready)
    SELECT 5, 'day_one_readiness', 'Day-one readiness (imminent cohorts fully ready)',
        COALESCE(MAX(portfolio_pct_fully_ready), 100.0),
        'pct', 'target = 100%',
        CASE WHEN COALESCE(MAX(portfolio_pct_fully_ready), 100) >= 100 THEN 'ok' ELSE 'watch' END,
        (SELECT COUNT(*) || ' imminent cohort(s)' FROM vw_day_one_readiness WHERE is_imminent = 1)
    FROM vw_day_one_readiness

    UNION ALL
    -- 6. open plan-reconciliation deltas (space can't hold the pipeline)
    SELECT 6, 'plan_reconciliation_gaps', 'Sites where space cannot hold the hiring pipeline',
        COALESCE(SUM(CASE WHEN delta_supportable_vs_pipeline < 0 THEN 1 ELSE 0 END), 0),
        'sites', 'target = 0',
        CASE WHEN COALESCE(SUM(CASE WHEN delta_supportable_vs_pipeline < 0 THEN 1 ELSE 0 END), 0) > 0
             THEN 'AT RISK' ELSE 'ok' END,
        COUNT(*) || ' site(s) reconciled'
    FROM vw_plan_reconciliation
) ORDER BY sort_order;
