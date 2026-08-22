-- =====================================================================
--  ElderCare Insight — semantic layer
--
--  Every measure in the design document is defined exactly once, here.
--  The dashboard, the report, and any notebook read these views and never
--  aggregate the fact tables directly.
--
--  That is not tidiness. Three of the ten measures are wrong when computed
--  the obvious way, and the wrong answer is plausible enough to survive a
--  presentation:
--
--    M1  occupancy    AVG(residents/beds) reads 80.27% where the portfolio
--                     ratio is 79.45%. Both look like occupancy.
--    M3/M4 staffing   an unweighted AVG lets a 20-bed home outvote a
--                     300-bed one on what "the average nurse hour" is.
--    M9  fines per    joining the two facts row to row multiplies both
--        resident-day sides. Each fact is aggregated to a common grain
--                     first, then the aggregates are joined.
--
--  Applied by build_warehouse.py after schema.sql.
-- =====================================================================


-- =====================================================================
--  1. Flat views — the star, joined. No aggregation.
--     These are what the dashboard filters against.
-- =====================================================================

CREATE OR REPLACE VIEW v_facility_monthly AS
SELECT
    d.date_key                    AS snapshot_date_key,
    d.full_date                   AS snapshot_date,
    d.year                        AS snapshot_year,
    d.quarter                     AS snapshot_quarter,
    d.year_month                  AS snapshot_year_month,
    d.month_name_th               AS snapshot_month_th,
    d.covid_period,

    f.ccn,
    fac.provider_name,
    fac.provider_type,
    fac.bed_size_band,
    fac.special_focus_status,
    fac.abuse_icon,

    g.state_code,
    g.state_name,
    g.census_region,
    g.county_parish,
    g.city,
    g.zip_code,
    g.urban_rural,
    g.latitude,
    g.longitude,

    o.ownership_type,
    o.ownership_group,
    o.is_for_profit,

    c.chain_name,
    c.chain_size_band,
    c.is_independent,

    f.certified_beds,
    f.avg_residents_per_day,
    f.resident_days,
    f.reported_total_nurse_hprd,
    f.reported_rn_hprd,
    f.total_nursing_turnover_pct,
    f.overall_rating,
    f.staffing_rating,
    f.health_inspection_rating,
    f.cycle1_total_deficiencies,
    f.is_suspect
FROM Fact_Facility_Monthly f
JOIN Dim_Date       d   ON f.snapshot_date_key = d.date_key
JOIN Dim_Facility   fac ON f.facility_key      = fac.facility_key
JOIN Dim_Geography  g   ON f.geography_key     = g.geography_key
JOIN Dim_Ownership  o   ON f.ownership_key     = o.ownership_key
JOIN Dim_Chain      c   ON f.chain_key         = c.chain_key;


CREATE OR REPLACE VIEW v_penalty_event AS
SELECT
    p.penalty_event_key,
    d.full_date       AS penalty_date,
    d.year            AS penalty_year,
    d.quarter         AS penalty_quarter,
    d.year_month      AS penalty_year_month,
    d.us_fiscal_year,
    d.covid_period,

    p.ccn,
    fac.provider_name,
    fac.bed_size_band,

    g.state_code,
    g.state_name,
    g.census_region,

    o.ownership_group,
    c.chain_name,
    c.chain_size_band,

    pt.penalty_type,
    pt.is_monetary,

    p.fine_id,
    p.fine_id_source,
    p.fine_amount_usd,
    p.payment_denial_days,
    p.penalty_count
FROM Fact_Penalty_Event p
JOIN Dim_Date         d   ON p.penalty_date_key = d.date_key
JOIN Dim_Facility     fac ON p.facility_key     = fac.facility_key
JOIN Dim_Geography    g   ON p.geography_key    = g.geography_key
JOIN Dim_Ownership    o   ON p.ownership_key    = o.ownership_key
JOIN Dim_Chain        c   ON p.chain_key        = c.chain_key
JOIN Dim_Penalty_Type pt  ON p.penalty_type_key = pt.penalty_type_key;


-- =====================================================================
--  2. The measures, defined once
--
--  v_measure_grain is the single definition of M1-M4, M7, M8. Everything
--  below it is this same expression list at a different GROUP BY. If a
--  measure definition has to change, it changes here and nowhere else.
--
--  Rows flagged is_suspect are KEPT. They are 0.21% of the table, they are
--  flagged rather than deleted by project rule, and SUM/SUM absorbs them
--  correctly — a facility recorded with 0 beds contributes 0 to both sides
--  instead of producing a division by zero the way a per-row ratio would.
--  suspect_rows is reported alongside so the count stays visible, and
--  v_facility_monthly.is_suspect is there for the dashboard's toggle.
-- =====================================================================

CREATE OR REPLACE VIEW v_measure_by_state_period AS
SELECT
    snapshot_date_key,
    snapshot_date,
    snapshot_year,
    snapshot_year_month,
    covid_period,
    state_code,
    state_name,
    census_region,

    COUNT(*)                                        AS facilities,
    SUM(CASE WHEN is_suspect THEN 1 ELSE 0 END)     AS suspect_rows,

    -- M1 occupancy — the ratio of the sums, never the mean of the ratios
    SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0)  AS m1_occupancy_rate,
    -- M2 resident-days, fully additive, and the weight for M3 and M4
    SUM(resident_days)                              AS m2_resident_days,
    -- M3 / M4 weighted by M2: a home's influence is its size, not its row
    SUM(reported_total_nurse_hprd * resident_days)
        / NULLIF(SUM(CASE WHEN reported_total_nurse_hprd IS NOT NULL
                          THEN resident_days END), 0)            AS m3_nurse_hprd,
    SUM(reported_rn_hprd * resident_days)
        / NULLIF(SUM(CASE WHEN reported_rn_hprd IS NOT NULL
                          THEN resident_days END), 0)            AS m3b_rn_hprd,
    SUM(total_nursing_turnover_pct * resident_days)
        / NULLIF(SUM(CASE WHEN total_nursing_turnover_pct IS NOT NULL
                          THEN resident_days END), 0)            AS m4_turnover_pct,
    -- M7 stars: an ordinal scale. Averaging it is already a compromise the
    -- design document accepts; weighting it by beds would compound the
    -- compromise, so this one stays a plain mean over facilities.
    AVG(CAST(overall_rating AS DOUBLE))             AS m7_avg_overall_rating,
    -- M8 fully additive
    SUM(cycle1_total_deficiencies)                  AS m8_deficiencies,

    SUM(certified_beds)                             AS total_certified_beds,
    SUM(avg_residents_per_day)                      AS total_residents
FROM v_facility_monthly
GROUP BY ALL;


CREATE OR REPLACE VIEW v_measure_by_ownership_period AS
SELECT
    snapshot_date_key,
    snapshot_date,
    snapshot_year_month,
    ownership_group,
    is_for_profit,

    COUNT(*)                                        AS facilities,
    SUM(CASE WHEN is_suspect THEN 1 ELSE 0 END)     AS suspect_rows,

    SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0)  AS m1_occupancy_rate,
    SUM(resident_days)                              AS m2_resident_days,
    SUM(reported_total_nurse_hprd * resident_days)
        / NULLIF(SUM(CASE WHEN reported_total_nurse_hprd IS NOT NULL
                          THEN resident_days END), 0)            AS m3_nurse_hprd,
    SUM(total_nursing_turnover_pct * resident_days)
        / NULLIF(SUM(CASE WHEN total_nursing_turnover_pct IS NOT NULL
                          THEN resident_days END), 0)            AS m4_turnover_pct,
    AVG(CAST(overall_rating AS DOUBLE))             AS m7_avg_overall_rating,
    SUM(cycle1_total_deficiencies)                  AS m8_deficiencies,
    SUM(certified_beds)                             AS total_certified_beds
FROM v_facility_monthly
GROUP BY ALL;


-- BQ8 — do large chains actually deliver better care, or just cheaper labour?
-- Independent homes are kept as their own band rather than dropped, because
-- the comparison is meaningless without the baseline it is measured against.
CREATE OR REPLACE VIEW v_measure_by_chain_period AS
SELECT
    snapshot_date_key,
    snapshot_date,
    snapshot_year_month,
    chain_size_band,
    is_independent,

    COUNT(*)                                        AS facilities,
    COUNT(DISTINCT chain_name)                      AS chains,

    SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0)  AS m1_occupancy_rate,
    SUM(resident_days)                              AS m2_resident_days,
    SUM(reported_total_nurse_hprd * resident_days)
        / NULLIF(SUM(CASE WHEN reported_total_nurse_hprd IS NOT NULL
                          THEN resident_days END), 0)            AS m3_nurse_hprd,
    SUM(total_nursing_turnover_pct * resident_days)
        / NULLIF(SUM(CASE WHEN total_nursing_turnover_pct IS NOT NULL
                          THEN resident_days END), 0)            AS m4_turnover_pct,
    AVG(CAST(overall_rating AS DOUBLE))             AS m7_avg_overall_rating,
    SUM(cycle1_total_deficiencies)                  AS m8_deficiencies
FROM v_facility_monthly
GROUP BY ALL;


-- ---------------------------------------------------------------------
--  M5 / M6 — penalties. Fine and Payment Denial are split by CASE rather
--  than summed together: one is dollars, the other is days, and
--  penalty_count over both answers "how many enforcement actions", which
--  is a different question from "how often was this facility fined".
--  BQ7 asks where and when penalties concentrate.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_penalty_by_state_year AS
SELECT
    penalty_year,
    state_code,
    state_name,
    census_region,

    SUM(penalty_count)                                              AS m6_penalty_events,
    SUM(CASE WHEN penalty_type = 'Fine' THEN penalty_count ELSE 0 END)
                                                                    AS m6_fine_events,
    SUM(CASE WHEN penalty_type = 'Payment Denial' THEN penalty_count ELSE 0 END)
                                                                    AS m6_denial_events,
    SUM(COALESCE(fine_amount_usd, 0))                               AS m5_fine_usd,
    SUM(COALESCE(payment_denial_days, 0))                           AS total_denial_days,
    COUNT(DISTINCT ccn)                                             AS facilities_penalised
FROM v_penalty_event
GROUP BY ALL;


-- =====================================================================
--  3. M9 — the cross-fact measure, done safely
--
--  M9 = fines / resident-days. The two numbers live in different facts at
--  different grains: Fact 1 is one row per facility per period, Fact 2 is
--  one row per penalty. Joining them on facility fans out — a facility with
--  12 penalties repeats its bed count and nurse hours 12 times, and every
--  additive measure on the Fact 1 side inflates twelvefold.
--
--  So each side is collapsed to (state, year) on its own, and only then
--  joined. FULL JOIN, not INNER: a state-year with penalties but no loaded
--  snapshot, or a snapshot with no penalties, must stay visible rather than
--  disappear into a silent inner join.
--
--  READ THIS BEFORE QUOTING M9: the denominator is only as complete as the
--  periods actually loaded. With 3 snapshots covering Jan 2019, Jun 2026 and
--  Jul 2026, m2_resident_days for 2019 is one month of that year, not
--  twelve, while the fines are every fine of that year. The ratio is
--  comparable across states within one year and NOT comparable across years
--  until the full quarterly set is loaded. periods_loaded is exposed so the
--  gap is impossible to miss.
-- =====================================================================

CREATE OR REPLACE VIEW v_fines_per_resident_day AS
WITH exposure AS (
    SELECT
        snapshot_year          AS year,
        state_code,
        SUM(resident_days)     AS m2_resident_days,
        COUNT(DISTINCT snapshot_date_key) AS periods_loaded,
        SUM(certified_beds)    AS total_certified_beds
    FROM v_facility_monthly
    GROUP BY ALL
),
penalties AS (
    SELECT
        penalty_year           AS year,
        state_code,
        SUM(COALESCE(fine_amount_usd, 0)) AS m5_fine_usd,
        SUM(penalty_count)                AS m6_penalty_events
    FROM v_penalty_event
    GROUP BY ALL
)
SELECT
    COALESCE(e.year, p.year)             AS year,
    COALESCE(e.state_code, p.state_code) AS state_code,
    e.periods_loaded,
    e.m2_resident_days,
    e.total_certified_beds,
    p.m5_fine_usd,
    p.m6_penalty_events,
    -- M9 itself. NULL rather than 0 when there is no exposure to divide by:
    -- a state with fines and no loaded snapshot has an unknown rate, not a
    -- zero one.
    p.m5_fine_usd / NULLIF(e.m2_resident_days, 0) AS m9_fine_per_resident_day,
    -- BQ7's "which state enforces hardest per bed"
    p.m5_fine_usd / NULLIF(e.total_certified_beds, 0) AS fine_per_certified_bed
FROM exposure e
FULL JOIN penalties p
       ON e.year = p.year
      AND e.state_code = p.state_code;


-- =====================================================================
--  4. BQ1 / M10 — market saturation.
--
--  M10 = certified beds per 1,000 residents aged 65+.
--
--  Population comes from Ref_State_Population, which is already at (state,
--  year) grain, so the join is a plain equi-join on state_code and there is
--  nothing to aggregate. This used to sum pop_65plus off Dim_Geography over
--  DISTINCT ZIP codes — a workaround for storing a state figure at ZIP grain,
--  which only ever produced NULL because the column was never loaded.
--
--  The year is the latest the population source covers, not the year of the
--  facility period. They differ: CMS publishes quarterly and the Census
--  estimates lag by a year or more. Pinning M10 to the newest population is
--  the honest reading of "beds per elderly person today", and the year is
--  returned alongside so the dashboard can say which one it used.
-- =====================================================================

CREATE OR REPLACE VIEW v_market_saturation AS
WITH latest_period AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key FROM Fact_Facility_Monthly
),
supply AS (
    SELECT
        v.state_code,
        v.state_name,
        v.census_region,
        SUM(v.certified_beds)      AS total_certified_beds,
        SUM(v.avg_residents_per_day) AS total_residents,
        COUNT(*)                   AS facilities
    FROM v_facility_monthly v
    JOIN latest_period lp ON v.snapshot_date_key = lp.snapshot_date_key
    GROUP BY ALL
),
population AS (
    -- Already one row per (state, year); take the most recent year the source
    -- covers. No aggregation, because the grain is right to begin with.
    SELECT state_code, pop_65plus, year AS population_year
    FROM Ref_State_Population
    WHERE year = (SELECT MAX(year) FROM Ref_State_Population)
)
SELECT
    s.state_code,
    s.state_name,
    s.census_region,
    s.facilities,
    s.total_certified_beds,
    s.total_residents,
    s.total_residents / NULLIF(s.total_certified_beds, 0) AS m1_occupancy_rate,
    p.pop_65plus,
    p.population_year,
    s.total_certified_beds / NULLIF(p.pop_65plus / 1000.0, 0) AS m10_beds_per_1000_elderly
FROM supply s
LEFT JOIN population p ON s.state_code = p.state_code;


-- =====================================================================
--  5. Guard views — these exist to be looked at, not to feed charts
-- =====================================================================

-- The reason M1 is a view and not a stored column. If the two columns ever
-- read the same, the argument in the report needs re-checking; they should
-- differ, and by enough to change a ranking.
CREATE OR REPLACE VIEW v_check_ratio_additivity AS
SELECT
    snapshot_date_key,
    snapshot_date,
    SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0) AS correct_sum_over_sum,
    AVG(avg_residents_per_day / NULLIF(certified_beds, 0))      AS wrong_avg_of_ratios,
    (AVG(avg_residents_per_day / NULLIF(certified_beds, 0))
     - SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0)) * 100
                                                                AS gap_percentage_points
FROM v_facility_monthly
GROUP BY ALL
ORDER BY snapshot_date_key;


-- How much history the warehouse actually holds. Every trend claim in the
-- report (BQ5 above all) has to be read against this: three points, one of
-- them seven years from the others, is not yet a time series.
CREATE OR REPLACE VIEW v_warehouse_coverage AS
SELECT
    'Fact_Facility_Monthly'                  AS table_name,
    COUNT(DISTINCT snapshot_date_key)        AS periods,
    MIN(snapshot_date)                       AS earliest,
    MAX(snapshot_date)                       AS latest,
    COUNT(*)                                 AS rows,
    COUNT(DISTINCT ccn)                      AS facilities
FROM v_facility_monthly
UNION ALL
SELECT
    'Fact_Penalty_Event',
    COUNT(DISTINCT penalty_year),
    MIN(penalty_date),
    MAX(penalty_date),
    COUNT(*),
    COUNT(DISTINCT ccn)
FROM v_penalty_event;
