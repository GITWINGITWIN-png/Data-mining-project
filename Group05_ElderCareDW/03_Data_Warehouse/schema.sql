-- =====================================================================
--  ElderCare Insight — warehouse schema (star schema, 2 facts + 6 dimensions)
--
--  The ETL writes its tables with CREATE OR REPLACE TABLE ... AS SELECT,
--  which carries no keys at all: DuckDB infers the column types and stops
--  there. Every guarantee the design document makes about this model —
--  grain, primary keys, foreign keys, allowed value ranges — lives only in
--  Python assertions there, and Python assertions do not travel with the
--  .duckdb file. Anyone who opens the database in a notebook, or points
--  Streamlit at it, or hands it to the next group, gets none of them.
--
--  This file is the contract restated where the data actually lives.
--  build_warehouse.py applies it by re-inserting the ETL output through
--  these declarations, so a violated constraint aborts the load instead of
--  being discovered later on a dashboard.
--
--  Run:  python build_warehouse.py     (never psql-style by hand)
-- =====================================================================


-- =====================================================================
--  DIMENSIONS
-- =====================================================================

-- ---------------------------------------------------------------------
--  Dim_Date — conformed. Both facts date against this one table, which is
--  what lets a penalty and a staffing snapshot land on the same calendar.
--  date_key is YYYYMMDD as an integer, plus one row at -1 for Unknown.
--  full_date is NULL on that row only, so it cannot be declared NOT NULL.
-- ---------------------------------------------------------------------
CREATE TABLE Dim_Date (
    date_key        BIGINT       NOT NULL,
    full_date       TIMESTAMP,
    year            INTEGER,
    quarter         INTEGER,
    month           INTEGER,
    month_name_th   VARCHAR,
    year_month      VARCHAR,
    us_fiscal_year  INTEGER,
    covid_period    VARCHAR,

    PRIMARY KEY (date_key),
    CONSTRAINT dim_date_quarter_range CHECK (quarter IS NULL OR quarter BETWEEN 1 AND 4),
    CONSTRAINT dim_date_month_range   CHECK (month   IS NULL OR month   BETWEEN 1 AND 12)
);

-- ---------------------------------------------------------------------
--  Dim_Facility — SCD type 2. The one table in the model where a CCN
--  appears many times, once per version of itself.
--
--  No UNIQUE (ccn) here, deliberately: that is the whole point of type 2.
--  The uniqueness that does hold is (ccn, effective_date), and it is
--  declared, because two versions of one facility starting on the same day
--  is the corruption that a slice-at-a-date query cannot resolve.
--  The Unknown row (key -1) carries NULL dates and is exempt.
-- ---------------------------------------------------------------------
CREATE TABLE Dim_Facility (
    facility_key         BIGINT   NOT NULL,
    ccn                  VARCHAR  NOT NULL,
    provider_name        VARCHAR,
    legal_business_name  VARCHAR,
    provider_type        VARCHAR,
    resides_in_hospital  BOOLEAN,
    ccrc_flag            BOOLEAN,
    special_focus_status VARCHAR,
    abuse_icon           BOOLEAN,
    bed_size_band        VARCHAR,
    effective_date       TIMESTAMP,
    expiry_date          TIMESTAMP,
    is_current           BOOLEAN  NOT NULL,

    PRIMARY KEY (facility_key),
    UNIQUE (ccn, effective_date),
    -- A closed version must end after it began. Equality is rejected too:
    -- effective_date = expiry_date is a version no point in time can select,
    -- and it is exactly what republished periods produced before the ETL
    -- learned to collapse them (see sources.drop_republished_periods).
    CONSTRAINT dim_facility_range_forward
        CHECK (expiry_date IS NULL OR effective_date IS NULL OR expiry_date > effective_date)
);

-- ---------------------------------------------------------------------
--  Dim_Geography — SCD type 1, grain (zip_code, city, state_code).
--
--  Population aged 65+ deliberately does not live here. At this grain a
--  state's population would repeat across every ZIP row in that state, and
--  no constraint in SQL can stop someone summing it: the fan trap is
--  created by storing the column, not by the query that hits it. It lives
--  in Ref_State_Population at its own (state, year) grain instead, and
--  v_market_saturation joins the two on state.
-- ---------------------------------------------------------------------
CREATE TABLE Dim_Geography (
    geography_key  BIGINT   NOT NULL,
    zip_code       VARCHAR,
    city           VARCHAR,
    county_parish  VARCHAR,
    state_code     VARCHAR,
    state_name     VARCHAR,
    census_region  VARCHAR,
    urban_rural    VARCHAR,
    latitude       DOUBLE,
    longitude      DOUBLE,

    PRIMARY KEY (geography_key),
    UNIQUE (zip_code, city, state_code),
    -- Rule Q1: the leading zero on a ZIP survives only if it stays text.
    -- The Unknown row (key -1) is exempt: it carries the literal 'Unknown' in
    -- every text column, which is what lets a fact with no resolvable
    -- geography keep a foreign key instead of a NULL that would drop the row
    -- from every join. verify_dims.py exempts it the same way.
    CONSTRAINT dim_geography_zip_width
        CHECK (geography_key = -1 OR zip_code IS NULL OR length(zip_code) = 5),
    CONSTRAINT dim_geography_latitude
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CONSTRAINT dim_geography_longitude
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);

CREATE TABLE Dim_Ownership (
    ownership_key   BIGINT   NOT NULL,
    ownership_type  VARCHAR  NOT NULL,
    ownership_group VARCHAR,
    is_for_profit   BOOLEAN,

    PRIMARY KEY (ownership_key),
    UNIQUE (ownership_type),
    -- BQ2 splits the world three ways and reports each group separately.
    -- A fourth spelling arriving from a new CMS era would silently create a
    -- fourth bar on that chart, so the allowed set is pinned here.
    CONSTRAINT dim_ownership_known_group
        CHECK (ownership_group IN ('For profit', 'Non profit', 'Government', 'Unknown'))
);

CREATE TABLE Dim_Chain (
    chain_key               BIGINT   NOT NULL,
    chain_id                VARCHAR,
    chain_name              VARCHAR  NOT NULL,
    num_facilities_in_chain INTEGER,
    chain_size_band         VARCHAR,
    is_independent          BOOLEAN,

    PRIMARY KEY (chain_key),
    UNIQUE (chain_name),
    CONSTRAINT dim_chain_size_nonneg
        CHECK (num_facilities_in_chain IS NULL OR num_facilities_in_chain >= 0)
);

CREATE TABLE Dim_Penalty_Type (
    penalty_type_key BIGINT   NOT NULL,
    penalty_type     VARCHAR  NOT NULL,
    is_monetary      BOOLEAN,

    PRIMARY KEY (penalty_type_key),
    UNIQUE (penalty_type)
);


-- ---------------------------------------------------------------------
--  Ref_State_Population — the one non-CMS source, grain (state_code, year).
--
--  Not a dimension: no fact has a foreign key to it, and nothing joins to it
--  by surrogate key. It is a reference table that v_market_saturation joins on
--  state_code to turn "how many beds" into "how many beds per elderly person",
--  which is the demand side of BQ1. CMS only ever describes providers.
--
--  Its own grain is what keeps it honest. Written onto Dim_Geography it would
--  repeat down every ZIP row in the state; here one state-year is one row, and
--  SUM(pop_65plus) over a year means exactly what it says.
-- ---------------------------------------------------------------------
CREATE TABLE Ref_State_Population (
    state_code   VARCHAR  NOT NULL,
    state_name   VARCHAR,
    year         INTEGER  NOT NULL,
    pop_65plus   BIGINT   NOT NULL,
    source       VARCHAR  NOT NULL,

    PRIMARY KEY (state_code, year),
    -- A population of zero means the parse dropped the age bands rather than
    -- that nobody lives there; negative is impossible either way.
    CONSTRAINT ref_state_population_positive CHECK (pop_65plus > 0)
);


-- =====================================================================
--  FACTS
-- =====================================================================

-- ---------------------------------------------------------------------
--  Fact 1 — Fact_Facility_Monthly (periodic snapshot)
--
--  GRAIN: one row = one facility (CCN) as reported on one CMS Processing
--         Date. A row exists whether or not anything changed.
--
--  The primary key is (snapshot_date_key, ccn) and NOT
--  (snapshot_date_key, facility_key). facility_key is an SCD2 surrogate,
--  so one facility can hold several of them; declaring the key on it would
--  accept two rows for the same facility in the same period as long as a
--  version boundary fell between them, which is precisely the duplicate the
--  grain forbids. ccn is the identity that survives versioning, so ccn is
--  what the key is built from. It is carried as a degenerate dimension for
--  exactly this reason.
--
--  Additivity, and why no ratio is stored:
--    fully additive   certified_beds, resident_days, cycle1_total_deficiencies
--    semi-additive    avg_residents_per_day (a rate — add across facilities,
--                     never across periods)
--    non-additive     every rating, turnover, and occupancy
--  Occupancy is absent on purpose. Stored per row it would invite AVG(),
--  and the average of per-facility ratios is not the portfolio ratio —
--  measured at 80.27% against a true 79.45% on the Jun 2026 period. It is
--  computed once, as SUM/SUM, in views.sql.
-- ---------------------------------------------------------------------
CREATE TABLE Fact_Facility_Monthly (
    snapshot_date_key          BIGINT  NOT NULL,
    facility_key               BIGINT  NOT NULL,
    geography_key              BIGINT  NOT NULL,
    ownership_key              BIGINT  NOT NULL,
    chain_key                  BIGINT  NOT NULL,

    ccn                        VARCHAR NOT NULL,   -- degenerate dimension

    certified_beds             BIGINT,
    avg_residents_per_day      DOUBLE,
    resident_days              DOUBLE,
    reported_total_nurse_hprd  DOUBLE,
    reported_rn_hprd           DOUBLE,
    total_nursing_turnover_pct DOUBLE,
    overall_rating             BIGINT,
    staffing_rating            BIGINT,
    health_inspection_rating   BIGINT,
    cycle1_total_deficiencies  BIGINT,
    is_suspect                 BOOLEAN NOT NULL,

    PRIMARY KEY (snapshot_date_key, ccn),

    FOREIGN KEY (snapshot_date_key) REFERENCES Dim_Date (date_key),
    FOREIGN KEY (facility_key)      REFERENCES Dim_Facility (facility_key),
    FOREIGN KEY (geography_key)     REFERENCES Dim_Geography (geography_key),
    FOREIGN KEY (ownership_key)     REFERENCES Dim_Ownership (ownership_key),
    FOREIGN KEY (chain_key)         REFERENCES Dim_Chain (chain_key),

    -- Ratings are the CMS 1-5 star scale. A 0 or a 6 means a footnote column
    -- was read as a value, which is rule Q3's failure mode.
    CONSTRAINT fact1_overall_rating_range
        CHECK (overall_rating IS NULL OR overall_rating BETWEEN 1 AND 5),
    CONSTRAINT fact1_staffing_rating_range
        CHECK (staffing_rating IS NULL OR staffing_rating BETWEEN 1 AND 5),
    CONSTRAINT fact1_health_rating_range
        CHECK (health_inspection_rating IS NULL OR health_inspection_rating BETWEEN 1 AND 5),
    CONSTRAINT fact1_beds_nonneg
        CHECK (certified_beds IS NULL OR certified_beds >= 0),
    CONSTRAINT fact1_residents_nonneg
        CHECK (avg_residents_per_day IS NULL OR avg_residents_per_day >= 0),
    -- Rule Q8. The percent/proportion drift is repaired per period in the
    -- ETL; this is the assertion that the repair actually happened. A column
    -- left as a proportion would sit far below 1 and pass unnoticed into a
    -- weighted average that reads 0.45% instead of 45%.
    CONSTRAINT fact1_turnover_is_percent
        CHECK (total_nursing_turnover_pct IS NULL
               OR total_nursing_turnover_pct BETWEEN 0 AND 200)
);

-- ---------------------------------------------------------------------
--  Fact 2 — Fact_Penalty_Event (transaction)
--
--  GRAIN: one row = one penalty imposed on one facility on one date.
--         Rows accumulate across periods and are deduplicated, because the
--         source Penalties file is a rolling 3-year window that reports the
--         same penalty again in every period it remains inside.
--
--  penalty_count is stored as a literal 1 so that COUNT and SUM agree after
--  any filter. It counts *events*, and a Fine and a Payment Denial are not
--  the same kind of event: summing it across both types answers a question
--  nobody asked. views.sql keeps the two apart.
--
--  fine_amount_usd is money and payment_denial_days is time. Neither is ever
--  null-filled to 0 across the other's rows, so SUM over a mixed set is safe
--  in the arithmetic and still wrong in the meaning.
-- ---------------------------------------------------------------------
CREATE TABLE Fact_Penalty_Event (
    penalty_event_key   BIGINT  NOT NULL,

    penalty_date_key    BIGINT  NOT NULL,
    facility_key        BIGINT  NOT NULL,
    geography_key       BIGINT  NOT NULL,
    ownership_key       BIGINT  NOT NULL,
    chain_key           BIGINT  NOT NULL,
    penalty_type_key    BIGINT  NOT NULL,

    ccn                 VARCHAR NOT NULL,   -- degenerate dimension
    fine_id             VARCHAR,            -- descriptive only, never the key
    fine_id_source      VARCHAR NOT NULL,

    fine_amount_usd     BIGINT,
    payment_denial_days BIGINT,
    penalty_count       BIGINT  NOT NULL,

    PRIMARY KEY (penalty_event_key),

    FOREIGN KEY (penalty_date_key) REFERENCES Dim_Date (date_key),
    FOREIGN KEY (facility_key)     REFERENCES Dim_Facility (facility_key),
    FOREIGN KEY (geography_key)    REFERENCES Dim_Geography (geography_key),
    FOREIGN KEY (ownership_key)    REFERENCES Dim_Ownership (ownership_key),
    FOREIGN KEY (chain_key)        REFERENCES Dim_Chain (chain_key),
    FOREIGN KEY (penalty_type_key) REFERENCES Dim_Penalty_Type (penalty_type_key),

    CONSTRAINT fact2_count_is_one
        CHECK (penalty_count = 1),
    CONSTRAINT fact2_fine_nonneg
        CHECK (fine_amount_usd IS NULL OR fine_amount_usd >= 0),
    CONSTRAINT fact2_denial_days_nonneg
        CHECK (payment_denial_days IS NULL OR payment_denial_days >= 0),
    -- Which of the two identity systems claimed this row. Recorded because
    -- the era split means both are legitimately in use at once.
    CONSTRAINT fact2_fine_id_source_known
        CHECK (fine_id_source IN ('fine_id', 'natural_key'))
);
