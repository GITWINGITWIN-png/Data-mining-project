"""Query layer for the dashboard — every number the charts show comes from here.

Deliberately not inside the notebook. The notebook is for looking at results one
cell at a time; the Streamlit app is the thing that gets handed in. Both import
these functions, so a number verified in the notebook is the same number the app
draws, and nothing has to be rewritten when the app is built.

The aggregation rules the design document insists on are enforced here rather
than left to whoever writes the next chart:

  * occupancy is SUM(residents) / SUM(beds), never the average of per-facility
    ratios — a 20-bed home and a 300-bed home would otherwise carry equal weight
  * star ratings are averaged only over the facilities that have one; CMS
    withholds ratings from new homes, and counting those as zero drags a state
    down for having new buildings
  * population lives at state grain in its own table, so it is joined once per
    state and never summed over ZIP codes
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

# The ETL package holds the single source of truth for where the database is
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "02_ETL"))
import config  # noqa: E402


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open the warehouse. Read-only by default — a dashboard never writes."""
    if not config.DB_PATH.exists():
        raise SystemExit(
            f"! no warehouse at {config.DB_PATH}\n"
            "  run: cd 02_ETL && python run_dims.py && python run_facts.py"
        )
    return duckdb.connect(str(config.DB_PATH), read_only=read_only)


def available_periods(con) -> pd.DataFrame:
    """Every period in the fact table, newest last."""
    return con.execute(
        """
        SELECT f.snapshot_date_key AS period_key,
               d.full_date         AS period,
               d.year              AS year,
               COUNT(*)            AS facilities
        FROM Fact_Facility_Monthly f
        JOIN Dim_Date d ON f.snapshot_date_key = d.date_key
        GROUP BY 1, 2, 3
        ORDER BY 2
        """
    ).df()


def population_years(con) -> list[int]:
    return sorted(
        int(y) for (y,) in con.execute(
            f"SELECT DISTINCT year FROM {POPULATION_TABLE} ORDER BY 1"
        ).fetchall()
    )


POPULATION_TABLE = "Ref_State_Population"


def _pick_population_year(con, period_year: int) -> int:
    """Nearest available population year to the snapshot's year.

    The estimates run 2020-2024 while the snapshots reach 2026, so an exact match
    is not always possible. Choosing the nearest year and saying so is honest;
    silently joining on a year that happens to exist is not.
    """
    years = population_years(con)
    if not years:
        raise SystemExit(
            f"! {POPULATION_TABLE} is empty — run: cd 02_ETL && python population.py"
        )
    return min(years, key=lambda y: abs(y - period_year))


def bq1_state_market(
    con,
    period_key: int | None = None,
    exclude_suspect: bool = True,
    min_facilities: int = 10,
) -> tuple[pd.DataFrame, dict]:
    """BQ1 — which state is the most attractive to invest in?

    Three things the CEO asked for, per state:
      beds_per_1k_65plus  supply against demand. Low means underserved.
      occupancy           how full the existing supply already is. High means
                          the demand is real and not just demographic theory.
      avg_overall_rating  the quality of the competition. Low means a
                          well-run new entrant has room to win.

    Returns (frame, context). `context` records the choices this function made —
    which period, which population year, what was filtered out — so a chart can
    caption itself truthfully instead of implying the numbers are timeless.

    `min_facilities` keeps states with a handful of homes out of the ranking:
    with three facilities, one closure swings every ratio. They are still
    returned, flagged with `too_few`, rather than being dropped invisibly.
    """
    periods = available_periods(con)
    if period_key is None:
        period_key = int(periods["period_key"].iloc[-1])
    row = periods.loc[periods["period_key"] == period_key]
    if row.empty:
        raise ValueError(f"period {period_key} is not in the fact table")
    period_year = int(row["year"].iloc[0])
    pop_year = _pick_population_year(con, period_year)

    suspect_clause = "AND NOT f.is_suspect" if exclude_suspect else ""

    frame = con.execute(
        f"""
        WITH facility AS (
            SELECT g.state_code,
                   g.state_name,
                   g.census_region,
                   f.certified_beds,
                   f.avg_residents_per_day,
                   f.overall_rating,
                   f.reported_total_nurse_hprd
            FROM Fact_Facility_Monthly f
            JOIN Dim_Geography g ON f.geography_key = g.geography_key
            WHERE f.snapshot_date_key = ?
              AND g.state_code <> 'Unknown'
              {suspect_clause}
        ),
        by_state AS (
            SELECT state_code,
                   MAX(state_name)    AS state_name,
                   MAX(census_region)  AS census_region,
                   COUNT(*)            AS facilities,
                   SUM(certified_beds) AS beds,
                   -- SUM/SUM, not AVG of ratios: bed count is the weight
                   SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0)
                       AS occupancy,
                   -- averaged only over the homes CMS actually rated
                   AVG(overall_rating)            AS avg_overall_rating,
                   COUNT(overall_rating)          AS rated_facilities,
                   AVG(reported_total_nurse_hprd) AS avg_nurse_hprd
            FROM facility
            GROUP BY 1
        )
        SELECT s.state_code,
               s.state_name,
               s.census_region,
               s.facilities,
               s.beds,
               p.pop_65plus,
               s.beds / (p.pop_65plus / 1000.0) AS beds_per_1k_65plus,
               s.occupancy,
               s.avg_overall_rating,
               s.rated_facilities,
               s.avg_nurse_hprd
        FROM by_state s
        LEFT JOIN {POPULATION_TABLE} p
               ON p.state_code = s.state_code AND p.year = ?
        ORDER BY s.state_code
        """,
        [period_key, pop_year],
    ).df()

    frame["too_few"] = frame["facilities"] < min_facilities
    frame["no_population"] = frame["pop_65plus"].isna()

    # The national figure travels with the frame so a chart can draw its
    # reference line without the caller having to remember to fetch it
    usable = frame[frame["pop_65plus"].notna()]
    national_beds_per_1k = (
        float(usable["beds"].sum() / (usable["pop_65plus"].sum() / 1000.0))
        if len(usable) else float("nan")
    )

    context = {
        "period_key": period_key,
        "period": str(row["period"].iloc[0])[:10],
        "national_beds_per_1k": national_beds_per_1k,
        "period_year": period_year,
        "population_year": pop_year,
        "population_note": (
            "exact year match"
            if pop_year == period_year
            else f"nearest available year ({pop_year} vs {period_year})"
        ),
        "exclude_suspect": exclude_suspect,
        "min_facilities": min_facilities,
        "states_returned": len(frame),
        "states_ranked": int((~frame["too_few"] & ~frame["no_population"]).sum()),
        "states_excluded_too_few": frame.loc[frame["too_few"], "state_code"].tolist(),
        "states_excluded_no_population": frame.loc[
            frame["no_population"], "state_code"
        ].tolist(),
    }
    return frame, context


def bq1_opportunity_score(frame: pd.DataFrame) -> pd.DataFrame:
    """Rank the states BQ1 can rank, and say exactly how.

    Each of the three measures becomes a percentile from 0 to 1 where 1 is the
    more attractive end, then the three are averaged with equal weight:

        supply     low  beds per 1,000 elderly  -> underserved
        demand     high occupancy               -> the beds that exist are full
        contest    low  average star rating     -> beatable competition

    Equal weights are a stated assumption, not a finding. They are here so the
    ranking can be argued with: the three component percentiles stay in the
    output, so anyone who thinks quality matters twice as much as supply can
    reweight them without recomputing anything.

    States with too few facilities, or with no population figure, are excluded
    from the ranking but kept in the frame with a null score.
    """
    out = frame.copy()
    eligible = ~out["too_few"] & ~out["no_population"]
    pool = out.loc[eligible]

    # Float columns seeded with NaN rather than pd.NA: the ineligible states have
    # to stay in the frame, and a numeric column is what the charts and any
    # reweighting expect
    components = ["pct_supply", "pct_demand", "pct_contest"]
    for column in components:
        out[column] = float("nan")

    out.loc[eligible, "pct_supply"] = pool["beds_per_1k_65plus"].rank(
        pct=True, ascending=False
    )
    out.loc[eligible, "pct_demand"] = pool["occupancy"].rank(pct=True, ascending=True)
    out.loc[eligible, "pct_contest"] = pool["avg_overall_rating"].rank(
        pct=True, ascending=False
    )

    out["opportunity_score"] = out[components].mean(axis=1)
    out.loc[~eligible, "opportunity_score"] = float("nan")

    out["rank"] = out["opportunity_score"].rank(ascending=False, method="min")
    return out.sort_values("opportunity_score", ascending=False, na_position="last")


# =====================================================================
#  BQ2 and BQ8 — segment comparisons
#
#  Both ask the same shape of question ("does this kind of operator behave
#  differently?") and both need the two fact tables at once, so they share one
#  engine. The two facts are aggregated **separately to the segment and then
#  joined on the segment** — never joined row to row. Fact_Facility_Monthly has
#  one row per facility per period and Fact_Penalty_Event has one row per
#  penalty; joining them directly would repeat a facility's beds once per penalty
#  and inflate every denominator.
# =====================================================================

#: How far back the fines window reaches. Three years matches the window CMS
#: itself publishes in the Penalties file, so "fines per bed" here means the same
#: thing as the figure CMS puts on a facility's own page.
FINES_WINDOW_YEARS = 3

SEGMENTS = {
    # BQ2. The three-group level, not the thirteen-type level: over 2019-2026
    # 3,777 facilities were reclassified from "For profit - Corporation" to
    # "For profit - Limited Liability company", which is a change of legal
    # wrapper and not of who runs the place. At the group level 89% of
    # facilities never move, so a comparison between groups measures operators
    # rather than paperwork.
    "ownership_group": {
        "table": "Dim_Ownership",
        "key": "ownership_key",
        "column": "ownership_group",
        "exclude": ["Unknown"],
        "order": ["For profit", "Non profit", "Government"],
        "label": "ownership group",
    },
    "ownership_type": {
        "table": "Dim_Ownership",
        "key": "ownership_key",
        "column": "ownership_type",
        "exclude": ["Unknown"],
        "order": None,
        "label": "ownership type",
    },
    # BQ8. Ordered bands, so charts may use an ordinal ramp.
    "chain_size_band": {
        "table": "Dim_Chain",
        "key": "chain_key",
        "column": "chain_size_band",
        "exclude": ["Unknown"],
        "order": ["Independent (1)", "Small chain (2-9)",
                  "Medium chain (10-49)", "Large chain (50+)"],
        "label": "chain size",
    },
}


def segment_compare(
    con,
    segment: str,
    period_key: int | None = None,
    exclude_suspect: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Compare operator segments on quality, labour and penalties.

    `segment` is a key of `SEGMENTS` — ownership group or type (BQ2) or chain
    size band (BQ8).

    Weighting, which is where this kind of comparison usually goes wrong:

      * occupancy is SUM(residents) / SUM(beds)
      * **nurse hours per resident per day are weighted by residents**, not
        averaged flat. HPRD is already a per-resident rate, so a flat average
        lets a 20-bed home pull as hard on the segment figure as a 300-bed one.
        The unweighted mean is returned alongside as `*_unweighted` so the gap
        between the two is visible rather than hidden.
      * star ratings are averaged only over facilities CMS actually rated
      * fines are attributed to the segment the facility sits in **at this
        period**, with beds from the same period, so numerator and denominator
        always describe the same set of homes. The alternative — attributing each
        fine to whatever the operator was on the day it was issued — is arguably
        more causal, but it would divide fines from one segment by beds from
        another for the half of facilities that were reclassified.
    """
    spec = SEGMENTS[segment]
    periods = available_periods(con)
    if period_key is None:
        period_key = int(periods["period_key"].iloc[-1])
    row = periods.loc[periods["period_key"] == period_key]
    if row.empty:
        raise ValueError(f"period {period_key} is not in the fact table")
    period_date = pd.Timestamp(row["period"].iloc[0])
    window_start = period_date - pd.DateOffset(years=FINES_WINDOW_YEARS)

    suspect_clause = "AND NOT f.is_suspect" if exclude_suspect else ""
    excluded = ", ".join(f"'{v}'" for v in spec["exclude"])

    frame = con.execute(
        f"""
        WITH roster AS (
            SELECT f.ccn,
                   dim.{spec['column']}          AS segment,
                   f.certified_beds              AS beds,
                   f.avg_residents_per_day       AS residents,
                   f.overall_rating              AS rating,
                   f.reported_total_nurse_hprd   AS nurse_hprd,
                   f.reported_rn_hprd            AS rn_hprd,
                   f.total_nursing_turnover_pct  AS turnover
            FROM Fact_Facility_Monthly f
            JOIN {spec['table']} dim ON f.{spec['key']} = dim.{spec['key']}
            WHERE f.snapshot_date_key = ?
              AND dim.{spec['column']} NOT IN ({excluded})
              {suspect_clause}
        ),
        operations AS (
            SELECT segment,
                   COUNT(*)        AS facilities,
                   SUM(beds)       AS beds,
                   SUM(residents) / NULLIF(SUM(beds), 0)          AS occupancy,
                   AVG(rating)                                     AS avg_rating,
                   COUNT(rating)                                   AS rated_facilities,
                   -- resident-weighted: the honest way to average a per-resident rate
                   SUM(nurse_hprd * residents) / NULLIF(SUM(CASE WHEN nurse_hprd IS NOT NULL
                       THEN residents END), 0)                      AS nurse_hprd,
                   SUM(rn_hprd * residents) / NULLIF(SUM(CASE WHEN rn_hprd IS NOT NULL
                       THEN residents END), 0)                      AS rn_hprd,
                   SUM(turnover * residents) / NULLIF(SUM(CASE WHEN turnover IS NOT NULL
                       THEN residents END), 0)                      AS turnover_pct,
                   AVG(nurse_hprd)                                 AS nurse_hprd_unweighted,
                   AVG(turnover)                                   AS turnover_pct_unweighted
            FROM roster
            GROUP BY 1
        ),
        penalties AS (
            SELECT r.segment,
                   SUM(p.fine_amount_usd)                       AS fine_total,
                   COUNT(*) FILTER (WHERE p.fine_amount_usd > 0) AS fine_events,
                   SUM(p.payment_denial_days)                   AS denial_days,
                   COUNT(DISTINCT p.ccn)                        AS fined_facilities
            FROM Fact_Penalty_Event p
            JOIN Dim_Date d ON p.penalty_date_key = d.date_key
            JOIN roster r   ON r.ccn = p.ccn
            WHERE d.full_date >= ? AND d.full_date <= ?
            GROUP BY 1
        )
        SELECT o.segment,
               o.facilities,
               o.beds,
               o.occupancy,
               o.avg_rating,
               o.rated_facilities,
               o.nurse_hprd,
               o.rn_hprd,
               o.rn_hprd / NULLIF(o.nurse_hprd, 0)      AS rn_share,
               o.turnover_pct,
               o.nurse_hprd_unweighted,
               o.turnover_pct_unweighted,
               COALESCE(p.fine_total, 0)                AS fine_total,
               COALESCE(p.fine_events, 0)               AS fine_events,
               COALESCE(p.denial_days, 0)               AS denial_days,
               COALESCE(p.fined_facilities, 0)          AS fined_facilities,
               COALESCE(p.fine_total, 0) / NULLIF(o.beds, 0)          AS fines_per_bed,
               COALESCE(p.fined_facilities, 0) * 1.0 / o.facilities   AS share_fined
        FROM operations o
        LEFT JOIN penalties p ON p.segment = o.segment
        """,
        [period_key, str(window_start.date()), str(period_date.date())],
    ).df()

    if spec["order"]:
        present = [s for s in spec["order"] if s in set(frame["segment"])]
        extra = [s for s in frame["segment"] if s not in set(spec["order"])]
        frame["segment"] = pd.Categorical(
            frame["segment"], categories=present + extra, ordered=True
        )
        frame = frame.sort_values("segment").reset_index(drop=True)
    else:
        frame = frame.sort_values("beds", ascending=False).reset_index(drop=True)

    context = {
        "segment": segment,
        "segment_label": spec["label"],
        "period_key": period_key,
        "period": str(row["period"].iloc[0])[:10],
        "fines_window": f"{window_start.date()} to {period_date.date()}",
        "fines_window_years": FINES_WINDOW_YEARS,
        "exclude_suspect": exclude_suspect,
        "facilities": int(frame["facilities"].sum()),
        "beds": int(frame["beds"].sum()),
    }
    return frame, context


def segment_facilities(
    con,
    segment: str,
    period_key: int | None = None,
    exclude_suspect: bool = True,
) -> pd.DataFrame:
    """One row per facility with its segment — for the per-facility scatter.

    The segment table answers "how do the groups differ on average"; this answers
    "how much do the groups overlap", which is the part an average hides. A 0.2
    star gap between segments means little if the two distributions sit on top of
    each other.
    """
    spec = SEGMENTS[segment]
    periods = available_periods(con)
    if period_key is None:
        period_key = int(periods["period_key"].iloc[-1])
    suspect_clause = "AND NOT f.is_suspect" if exclude_suspect else ""
    excluded = ", ".join(f"'{v}'" for v in spec["exclude"])

    return con.execute(
        f"""
        SELECT f.ccn,
               dim.{spec['column']}         AS segment,
               f.certified_beds             AS beds,
               f.avg_residents_per_day      AS residents,
               f.overall_rating             AS rating,
               f.reported_total_nurse_hprd  AS nurse_hprd,
               f.reported_rn_hprd           AS rn_hprd,
               f.total_nursing_turnover_pct AS turnover
        FROM Fact_Facility_Monthly f
        JOIN {spec['table']} dim ON f.{spec['key']} = dim.{spec['key']}
        WHERE f.snapshot_date_key = ?
          AND dim.{spec['column']} NOT IN ({excluded})
          {suspect_clause}
        """,
        [period_key],
    ).df()


def segment_cross(
    con,
    period_key: int | None = None,
    exclude_suspect: bool = True,
) -> pd.DataFrame:
    """Chain size crossed with ownership group — the control BQ8 needs.

    Chains are overwhelmingly for-profit, so "large chains staff more thinly"
    and "for-profit operators staff more thinly" would produce the same table
    even if only one of them were true. The only way to tell them apart with this
    data is to look at chain size *inside* one ownership group at a time: if
    staffing still falls as chain size rises among for-profit homes only, then
    scale is doing something ownership alone does not explain.
    """
    periods = available_periods(con)
    if period_key is None:
        period_key = int(periods["period_key"].iloc[-1])
    suspect_clause = "AND NOT f.is_suspect" if exclude_suspect else ""

    frame = con.execute(
        f"""
        SELECT o.ownership_group                AS ownership,
               c.chain_size_band                AS chain_size,
               COUNT(*)                         AS facilities,
               SUM(f.certified_beds)            AS beds,
               AVG(f.overall_rating)            AS avg_rating,
               SUM(f.reported_total_nurse_hprd * f.avg_residents_per_day)
                 / NULLIF(SUM(CASE WHEN f.reported_total_nurse_hprd IS NOT NULL
                     THEN f.avg_residents_per_day END), 0)          AS nurse_hprd,
               SUM(f.total_nursing_turnover_pct * f.avg_residents_per_day)
                 / NULLIF(SUM(CASE WHEN f.total_nursing_turnover_pct IS NOT NULL
                     THEN f.avg_residents_per_day END), 0)          AS turnover_pct
        FROM Fact_Facility_Monthly f
        JOIN Dim_Ownership o ON f.ownership_key = o.ownership_key
        JOIN Dim_Chain c     ON f.chain_key = c.chain_key
        WHERE f.snapshot_date_key = ?
          AND o.ownership_group <> 'Unknown'
          AND c.chain_size_band <> 'Unknown'
          {suspect_clause}
        GROUP BY 1, 2
        """,
        [period_key],
    ).df()

    chain_order = SEGMENTS["chain_size_band"]["order"]
    own_order = SEGMENTS["ownership_group"]["order"]
    frame["chain_size"] = pd.Categorical(frame["chain_size"], chain_order, ordered=True)
    frame["ownership"] = pd.Categorical(frame["ownership"], own_order, ordered=True)
    return frame.sort_values(["ownership", "chain_size"]).reset_index(drop=True)


def chain_data_periods(con) -> pd.DataFrame:
    """Which periods carry chain information at all.

    BQ8 cannot be answered from the 2019 era: the chain columns were added to
    ProviderInfo in 2025, so every 2019 row points at the Unknown chain. Asking
    this first turns a silently empty comparison into a clear message.
    """
    return con.execute(
        """
        SELECT d.full_date AS period,
               f.snapshot_date_key AS period_key,
               COUNT(*) AS facilities,
               SUM(CASE WHEN f.chain_key <> -1 THEN 1 ELSE 0 END) AS with_chain_data
        FROM Fact_Facility_Monthly f
        JOIN Dim_Date d ON f.snapshot_date_key = d.date_key
        GROUP BY 1, 2
        ORDER BY 1
        """
    ).df()


def bq1_national_baseline(con, period_key: int | None = None,
                         exclude_suspect: bool = True) -> dict:
    """The national figures a state should be read against.

    Without these, "1.9 beds per 1,000" means nothing to a reader.
    """
    frame, context = bq1_state_market(
        con, period_key=period_key, exclude_suspect=exclude_suspect, min_facilities=0
    )
    usable = frame[~frame["no_population"]]
    return {
        "facilities": int(frame["facilities"].sum()),
        "beds": int(frame["beds"].sum()),
        "pop_65plus": int(usable["pop_65plus"].sum()),
        "beds_per_1k_65plus": float(
            usable["beds"].sum() / (usable["pop_65plus"].sum() / 1000.0)
        ),
        "occupancy": float(
            (frame["occupancy"] * frame["beds"]).sum() / frame["beds"].sum()
        ),
        "avg_overall_rating": float(
            (frame["avg_overall_rating"] * frame["rated_facilities"]).sum()
            / frame["rated_facilities"].sum()
        ),
        **context,
    }


# =====================================================================
#  BQ3-BQ7 — staffing, retention, trend, watchlist, enforcement
#
#  Everything below is built on `facility_roster()` rather than on ad-hoc
#  SQL per question. One row per facility per period, with the penalty
#  fact already rolled up to CCN *before* it is joined. That rollup is not
#  a convenience: Fact_Facility_Monthly is one row per facility-period and
#  Fact_Penalty_Event is one row per penalty, so joining them directly
#  repeats a facility's beds once per penalty. Measured on the July 2026
#  period, the direct join reports 2,586,212 for-profit beds instead of
#  1,203,047 — inflated 2.1x. Collapsing to CCN first makes the mistake
#  impossible to write by accident.
# =====================================================================

#: Total nurse staffing floor in the CMS Minimum Staffing Rule finalised in
#: 2024 (3.48 total nurse hours per resident per day). Used as a reference
#: line rather than as a derived cut point — the gradient in the data is
#: continuous, and a threshold that comes from the regulator is defensible
#: in a way that a threshold fitted to the same data is not.
CMS_MIN_TOTAL_HPRD = 3.48

#: Band edges for BQ3. Deliberately straddle CMS_MIN_TOTAL_HPRD so the
#: chart can show what happens on either side of the regulatory floor.
HPRD_BANDS = [
    (0.00, 2.75, "< 2.75"),
    (2.75, 3.00, "2.75-3.00"),
    (3.00, 3.25, "3.00-3.25"),
    (3.25, 3.48, "3.25-3.48"),
    (3.48, 3.75, "3.48-3.75"),
    (3.75, 4.00, "3.75-4.00"),
    (4.00, 4.25, "4.00-4.25"),
    (4.25, 4.50, "4.25-4.50"),
    (4.50, 5.00, "4.50-5.00"),
    (5.00, 99.0, "5.00+"),
]

#: Band edges for BQ4, 5 percentage points wide across the bulk of the
#: distribution. The national figure is ~45%, so the bands either side of
#: it carry most of the facilities and the tails stay readable.
TURNOVER_BANDS = [
    (0, 30, "< 30%"), (30, 35, "30-35%"), (35, 40, "35-40%"),
    (40, 45, "40-45%"), (45, 50, "45-50%"), (50, 55, "50-55%"),
    (55, 60, "55-60%"), (60, 65, "60-65%"), (65, 101, "65%+"),
]


def _band(series: pd.Series, bands: list[tuple]) -> pd.Categorical:
    """Cut a numeric column into the named bands above, left-closed."""
    edges = [b[0] for b in bands] + [bands[-1][1]]
    labels = [b[2] for b in bands]
    return pd.cut(series, edges, labels=labels, right=False, ordered=True)


def facility_roster(
    con,
    period_key: int | None = None,
    exclude_suspect: bool = True,
    states: list[str] | None = None,
    ownership_groups: list[str] | None = None,
    chain_bands: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """One row per facility for a period, with fines already summed per CCN.

    The optional filters exist because the dashboard needs them; passing none
    gives the national picture. They are applied in SQL rather than in pandas
    so that a filtered dashboard page does not read 60,000 rows to show 300.

    Fines cover the same rolling window CMS itself publishes (three years back
    from the snapshot), so `fine_total` here means the same thing as the figure
    on a facility's own CMS page and can be checked against it.
    """
    periods = available_periods(con)
    if period_key is None:
        period_key = int(periods["period_key"].iloc[-1])
    row = periods.loc[periods["period_key"] == period_key]
    if row.empty:
        raise ValueError(f"period {period_key} is not in the fact table")
    period_date = pd.Timestamp(row["period"].iloc[0])
    window_start = period_date - pd.DateOffset(years=FINES_WINDOW_YEARS)

    where = ["f.snapshot_date_key = ?"]
    params: list = [period_key]
    if exclude_suspect:
        where.append("NOT f.is_suspect")
    if states:
        where.append("g.state_code IN (" + ",".join("?" * len(states)) + ")")
        params += list(states)
    if ownership_groups:
        where.append("o.ownership_group IN ("
                     + ",".join("?" * len(ownership_groups)) + ")")
        params += list(ownership_groups)
    if chain_bands:
        where.append("c.chain_size_band IN ("
                     + ",".join("?" * len(chain_bands)) + ")")
        params += list(chain_bands)

    frame = con.execute(
        f"""
        WITH roster AS (
            SELECT f.ccn,
                   fa.provider_name,
                   fa.special_focus_status,
                   fa.abuse_icon,
                   g.state_code, g.city, g.county_parish,
                   o.ownership_group, o.ownership_type,
                   c.chain_size_band, c.chain_name,
                   f.certified_beds             AS beds,
                   f.avg_residents_per_day      AS residents,
                   f.resident_days,
                   f.reported_total_nurse_hprd  AS nurse_hprd,
                   f.reported_rn_hprd           AS rn_hprd,
                   f.total_nursing_turnover_pct AS turnover_pct,
                   f.overall_rating             AS rating,
                   f.staffing_rating,
                   f.health_inspection_rating,
                   f.cycle1_total_deficiencies  AS deficiencies
            FROM Fact_Facility_Monthly f
            JOIN Dim_Facility  fa ON f.facility_key  = fa.facility_key
            JOIN Dim_Geography g  ON f.geography_key = g.geography_key
            JOIN Dim_Ownership o  ON f.ownership_key = o.ownership_key
            JOIN Dim_Chain     c  ON f.chain_key     = c.chain_key
            WHERE {' AND '.join(where)}
        ),
        -- collapsed to one row per CCN BEFORE it meets the roster
        fines AS (
            SELECT p.ccn,
                   SUM(p.fine_amount_usd)                        AS fine_total,
                   COUNT(*) FILTER (WHERE p.fine_amount_usd > 0) AS fine_events,
                   SUM(p.payment_denial_days)                    AS denial_days
            FROM Fact_Penalty_Event p
            JOIN Dim_Date d ON p.penalty_date_key = d.date_key
            WHERE d.full_date >= ? AND d.full_date <= ?
            GROUP BY 1
        )
        SELECT r.*,
               COALESCE(x.fine_total, 0)  AS fine_total,
               COALESCE(x.fine_events, 0) AS fine_events,
               COALESCE(x.denial_days, 0) AS denial_days
        FROM roster r
        LEFT JOIN fines x ON x.ccn = r.ccn
        """,
        params + [str(window_start.date()), str(period_date.date())],
    ).df()

    frame["fines_per_bed"] = frame["fine_total"] / frame["beds"].replace(0, pd.NA)
    frame["was_fined"] = frame["fine_total"] > 0

    context = {
        "period_key": period_key,
        "period": str(row["period"].iloc[0])[:10],
        "fines_window": f"{window_start.date()} to {period_date.date()}",
        "fines_window_years": FINES_WINDOW_YEARS,
        "facilities": len(frame),
        "beds": int(frame["beds"].sum()),
        "filters": {
            "states": states, "ownership_groups": ownership_groups,
            "chain_bands": chain_bands, "exclude_suspect": exclude_suspect,
        },
    }
    return frame, context


def _gradient(roster: pd.DataFrame, band_col: str) -> pd.DataFrame:
    """Roll a banded roster up to one row per band.

    Each measure is aggregated the way the design document requires for that
    measure, not uniformly: occupancy and fines-per-bed are ratios of sums,
    star ratings are averaged over rated facilities only, and the shares are
    proportions of facilities.
    """
    out = []
    for band, x in roster.groupby(band_col, observed=True):
        rated = x["rating"].notna()
        out.append({
            "band": band,
            "facilities": len(x),
            "beds": int(x["beds"].sum()),
            # quality, as CMS scores it
            "avg_rating": x.loc[rated, "rating"].mean(),
            "pct_1_2_star": (x.loc[rated, "rating"] <= 2).mean() * 100,
            "pct_4_5_star": (x.loc[rated, "rating"] >= 4).mean() * 100,
            # the two ratings that are NOT mechanically tied to staffing hours
            "health_inspection_rating": x["health_inspection_rating"].mean(),
            "deficiencies": x["deficiencies"].mean(),
            # regulatory cost
            "fines_per_bed": x["fine_total"].sum() / max(x["beds"].sum(), 1),
            "pct_fined": x["was_fined"].mean() * 100,
            # commercial
            "occupancy": x["residents"].sum() / max(x["beds"].sum(), 1),
            # labour
            "nurse_hprd": (
                (x["nurse_hprd"] * x["residents"]).sum()
                / max(x.loc[x["nurse_hprd"].notna(), "residents"].sum(), 1)
            ),
            "turnover_pct": (
                (x["turnover_pct"] * x["residents"]).sum()
                / max(x.loc[x["turnover_pct"].notna(), "residents"].sum(), 1)
            ),
        })
    columns = [
        "band", "facilities", "beds", "avg_rating", "pct_1_2_star", "pct_4_5_star",
        "health_inspection_rating", "deficiencies", "fines_per_bed", "pct_fined",
        "occupancy", "nurse_hprd", "turnover_pct",
    ]
    # An empty roster is a real case, not a bug: ask for turnover bands on the
    # 2019 era and there is nothing to band, because CMS had not added the
    # column yet. `pd.DataFrame([])` would have no columns at all and the diff
    # below would raise KeyError on a name the caller never sees, so give the
    # empty frame the same shape a full one has and let the caller check
    # `facilities` instead of catching an exception.
    frame = pd.DataFrame(out, columns=columns if not out else None)

    # marginal change between adjacent bands — the knee is easier to see in
    # the differences than in the levels
    for col in ["avg_rating", "health_inspection_rating", "fines_per_bed", "occupancy"]:
        frame[f"d_{col}"] = frame[col].diff()
    return frame


def bq3_staffing_gradient(con, **kwargs) -> tuple[pd.DataFrame, dict]:
    """BQ3 — how nurse hours per resident per day track quality and fines.

    A caveat the chart has to carry, because it decides how the result may be
    read: CMS builds the **staffing star** out of these very hours, so
    `avg_rating` rises with HPRD partly by construction. The two columns that
    are *not* circular are `health_inspection_rating` — scored by state
    surveyors from on-site inspections, with staffing hours playing no part in
    it — and `fines_per_bed`, which is money a regulator actually demanded.
    Those two carry the argument; the overall star is shown alongside because
    it is what families see.
    """
    roster, context = facility_roster(con, **kwargs)
    staffed = roster[roster["nurse_hprd"].notna()].copy()
    staffed["band"] = _band(staffed["nurse_hprd"], HPRD_BANDS)
    frame = _gradient(staffed, "band")

    below = staffed[staffed["nurse_hprd"] < CMS_MIN_TOTAL_HPRD]
    above = staffed[staffed["nurse_hprd"] >= CMS_MIN_TOTAL_HPRD]
    context |= {
        "measured_facilities": len(staffed),
        "missing_hprd": len(roster) - len(staffed),
        "cms_floor": CMS_MIN_TOTAL_HPRD,
        "below_floor": len(below),
        "below_floor_pct": len(below) / len(staffed) * 100 if len(staffed) else float("nan"),
        "below": {
            "health_inspection_rating": below["health_inspection_rating"].mean(),
            "fines_per_bed": below["fine_total"].sum() / max(below["beds"].sum(), 1),
            "pct_fined": below["was_fined"].mean() * 100,
            "avg_rating": below["rating"].mean(),
            "deficiencies": below["deficiencies"].mean(),
        },
        "above": {
            "health_inspection_rating": above["health_inspection_rating"].mean(),
            "fines_per_bed": above["fine_total"].sum() / max(above["beds"].sum(), 1),
            "pct_fined": above["was_fined"].mean() * 100,
            "avg_rating": above["rating"].mean(),
            "deficiencies": above["deficiencies"].mean(),
        },
    }
    return frame, context


def bq3_controlled(con, **kwargs) -> pd.DataFrame:
    """BQ3 with ownership held constant.

    For-profit homes both staff more thinly and score worse (BQ2), so a raw
    staffing gradient could be nothing but the ownership gap wearing a
    different label. Splitting HPRD into quartiles *within* each ownership
    group separates the two: if the gradient survives inside every group, then
    hours are doing something ownership does not explain.
    """
    roster, _ = facility_roster(con, **kwargs)
    d = roster[roster["nurse_hprd"].notna() & (roster["ownership_group"] != "Unknown")].copy()
    d["hprd_quartile"] = pd.qcut(
        d["nurse_hprd"], 4, labels=["Q1 thinnest", "Q2", "Q3", "Q4 richest"]
    )
    rows = []
    for (own, q), x in d.groupby(["ownership_group", "hprd_quartile"], observed=True):
        rows.append({
            "ownership_group": own,
            "hprd_quartile": q,
            "facilities": len(x),
            "nurse_hprd": (x["nurse_hprd"] * x["residents"]).sum()
                          / max(x["residents"].sum(), 1),
            "health_inspection_rating": x["health_inspection_rating"].mean(),
            "avg_rating": x["rating"].mean(),
            "fines_per_bed": x["fine_total"].sum() / max(x["beds"].sum(), 1),
        })
    frame = pd.DataFrame(rows)
    frame["ownership_group"] = pd.Categorical(
        frame["ownership_group"], SEGMENTS["ownership_group"]["order"], ordered=True
    )
    frame["hprd_quartile"] = pd.Categorical(
        frame["hprd_quartile"], d["hprd_quartile"].cat.categories, ordered=True
    )
    return frame.sort_values(["ownership_group", "hprd_quartile"]).reset_index(drop=True)


def bq4_turnover_gradient(con, **kwargs) -> tuple[pd.DataFrame, dict]:
    """BQ4 — at what turnover level quality and occupancy start to suffer.

    Two different thresholds come out of this and they are not the same number,
    which is the point of the question: quality erodes from the very first band
    with no safe plateau, while occupancy holds flat and then breaks. Reporting
    a single "the threshold is X" would hide that.
    """
    roster, context = facility_roster(con, **kwargs)
    d = roster[roster["turnover_pct"].notna()].copy()
    d["band"] = _band(d["turnover_pct"], TURNOVER_BANDS)
    frame = _gradient(d, "band")

    context |= {
        "measured_facilities": len(d),
        "missing_turnover": len(roster) - len(d),
        "national_turnover_weighted": (d["turnover_pct"] * d["residents"]).sum()
                                      / max(d["residents"].sum(), 1),
        "national_turnover_median": d["turnover_pct"].median(),
    }
    return frame, context


def bq4_split_scan(con, cuts: range | list | None = None, **kwargs) -> pd.DataFrame:
    """Where to draw the line — every candidate cut point, scored.

    Rather than eyeballing the band table, this splits the population at each
    candidate turnover level and reports the gap either side. The cut worth
    quoting is the one where the *occupancy* gap starts to widen, because that
    is the one with a number attached to it in dollars.
    """
    roster, _ = facility_roster(con, **kwargs)
    d = roster[roster["turnover_pct"].notna()]
    cuts = cuts if cuts is not None else range(25, 71, 5)
    rows = []
    for cut in cuts:
        lo, hi = d[d["turnover_pct"] < cut], d[d["turnover_pct"] >= cut]
        if len(lo) < 200 or len(hi) < 200:
            continue
        occ_lo = lo["residents"].sum() / lo["beds"].sum()
        occ_hi = hi["residents"].sum() / hi["beds"].sum()
        rows.append({
            "cut": cut,
            "facilities_below": len(lo),
            "pct_below": len(lo) / len(d) * 100,
            "rating_below": lo["rating"].mean(),
            "rating_above": hi["rating"].mean(),
            "rating_gap": lo["rating"].mean() - hi["rating"].mean(),
            "occupancy_below": occ_lo,
            "occupancy_above": occ_hi,
            "occupancy_gap_pts": (occ_lo - occ_hi) * 100,
            "fines_per_bed_below": lo["fine_total"].sum() / lo["beds"].sum(),
            "fines_per_bed_above": hi["fine_total"].sum() / hi["beds"].sum(),
        })
    return pd.DataFrame(rows)


def bq4_controlled(con, **kwargs) -> pd.DataFrame:
    """BQ4 with staffing held constant — is turnover its own effect?

    Thinly staffed homes also churn staff, so the obvious objection is that
    turnover is just a symptom of understaffing and fixing it would change
    nothing. Crossing turnover bands with HPRD quartiles answers that
    objection with the data rather than with an assertion.
    """
    roster, _ = facility_roster(con, **kwargs)
    d = roster[roster["turnover_pct"].notna() & roster["nurse_hprd"].notna()].copy()
    d["hprd_quartile"] = pd.qcut(
        d["nurse_hprd"], 4, labels=["Q1 thinnest", "Q2", "Q3", "Q4 richest"]
    )
    d["turnover_band"] = pd.cut(
        d["turnover_pct"], [0, 40, 50, 60, 101],
        labels=["< 40%", "40-50%", "50-60%", "60%+"], right=False, ordered=True
    )
    rows = []
    for (q, tb), x in d.groupby(["hprd_quartile", "turnover_band"], observed=True):
        rows.append({
            "hprd_quartile": q,
            "turnover_band": tb,
            "facilities": len(x),
            "avg_rating": x["rating"].mean(),
            "occupancy": x["residents"].sum() / max(x["beds"].sum(), 1),
            "fines_per_bed": x["fine_total"].sum() / max(x["beds"].sum(), 1),
        })
    frame = pd.DataFrame(rows)
    # Rebuilding a frame from dicts drops the categorical dtype, and with it the
    # band order — a pivot then sorts "< 40%" alphabetically to the far right,
    # which reverses the direction the chart is supposed to read in. Put the
    # order back before anything downstream pivots on these columns.
    frame["hprd_quartile"] = pd.Categorical(
        frame["hprd_quartile"], d["hprd_quartile"].cat.categories, ordered=True)
    frame["turnover_band"] = pd.Categorical(
        frame["turnover_band"], d["turnover_band"].cat.categories, ordered=True)
    return frame.sort_values(["hprd_quartile", "turnover_band"]).reset_index(drop=True)


def bq5_period_trend(con, exclude_suspect: bool = True) -> pd.DataFrame:
    """BQ5 — the headline measures at every period, composition as it fell.

    This is the view that is easiest to draw and easiest to misread, which is
    why `bq5_matched_cohort` exists next to it. Between 2019 and 2026 the
    industry lost 1,282 facilities; any measure computed over "whoever was open
    at the time" mixes a change in performance with a change in who is being
    measured.
    """
    suspect_clause = "WHERE NOT f.is_suspect" if exclude_suspect else ""
    return con.execute(
        f"""
        SELECT d.full_date                AS period,
               f.snapshot_date_key        AS period_key,
               d.year                     AS year,
               COUNT(*)                   AS facilities,
               SUM(f.certified_beds)      AS beds,
               SUM(f.avg_residents_per_day) AS residents,
               SUM(f.avg_residents_per_day) / NULLIF(SUM(f.certified_beds), 0)
                                          AS occupancy,
               SUM(f.reported_total_nurse_hprd * f.avg_residents_per_day)
                 / NULLIF(SUM(CASE WHEN f.reported_total_nurse_hprd IS NOT NULL
                     THEN f.avg_residents_per_day END), 0)      AS nurse_hprd,
               SUM(f.reported_rn_hprd * f.avg_residents_per_day)
                 / NULLIF(SUM(CASE WHEN f.reported_rn_hprd IS NOT NULL
                     THEN f.avg_residents_per_day END), 0)      AS rn_hprd,
               AVG(f.overall_rating)      AS avg_rating,
               AVG(f.cycle1_total_deficiencies) AS deficiencies
        FROM Fact_Facility_Monthly f
        JOIN Dim_Date d ON f.snapshot_date_key = d.date_key
        {suspect_clause}
        GROUP BY 1, 2, 3
        ORDER BY 1
        """
    ).df()


def bq5_matched_cohort(
    con,
    from_period: int = 20190101,
    to_period: int | None = None,
    exclude_suspect: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """BQ5 done properly — the same facilities measured twice.

    Restricting to CCNs open in both periods removes the composition change, so
    what is left is a change in performance at homes that lived through it.
    Returns the per-facility pairs (for the migration matrix and for drilling
    in) plus the cohort-level summary.

    The gap in the middle is real and cannot be papered over: there is no 2020
    or 2021 snapshot, so this is a comparison of two endpoints seven years
    apart, not a trend line. It can say where the industry ended up relative to
    where it started. It cannot show the COVID trough or say when the recovery
    happened.
    """
    periods = available_periods(con)
    if to_period is None:
        to_period = int(periods["period_key"].iloc[-1])
    suspect_clause = "AND NOT is_suspect" if exclude_suspect else ""

    pairs = con.execute(
        f"""
        WITH a AS (SELECT * FROM Fact_Facility_Monthly
                   WHERE snapshot_date_key = ? {suspect_clause}),
             b AS (SELECT * FROM Fact_Facility_Monthly
                   WHERE snapshot_date_key = ? {suspect_clause})
        SELECT a.ccn,
               g.state_code, o.ownership_group,
               a.certified_beds AS beds_from,       b.certified_beds AS beds_to,
               a.avg_residents_per_day AS res_from, b.avg_residents_per_day AS res_to,
               a.reported_total_nurse_hprd AS hprd_from,
               b.reported_total_nurse_hprd AS hprd_to,
               a.reported_rn_hprd AS rn_from,       b.reported_rn_hprd AS rn_to,
               a.overall_rating AS rating_from,     b.overall_rating AS rating_to,
               a.cycle1_total_deficiencies AS defic_from,
               b.cycle1_total_deficiencies AS defic_to
        FROM a JOIN b ON a.ccn = b.ccn
        JOIN Dim_Ownership o  ON b.ownership_key  = o.ownership_key
        JOIN Dim_Geography g  ON b.geography_key  = g.geography_key
        """,
        [from_period, to_period],
    ).df()

    def weighted(value_col: str, weight_col: str) -> float:
        ok = pairs[value_col].notna() & pairs[weight_col].notna()
        return float((pairs.loc[ok, value_col] * pairs.loc[ok, weight_col]).sum()
                     / pairs.loc[ok, weight_col].sum())

    occ_from = pairs["res_from"].sum() / pairs["beds_from"].sum()
    occ_to = pairs["res_to"].sum() / pairs["beds_to"].sum()
    hprd_from, hprd_to = weighted("hprd_from", "res_from"), weighted("hprd_to", "res_to")
    rn_from, rn_to = weighted("rn_from", "res_from"), weighted("rn_to", "res_to")

    rated = pairs["rating_from"].notna() & pairs["rating_to"].notna()
    context = {
        "from_period": str(periods.loc[periods["period_key"] == from_period,
                                       "period"].iloc[0])[:10],
        "to_period": str(periods.loc[periods["period_key"] == to_period,
                                     "period"].iloc[0])[:10],
        "matched_facilities": len(pairs),
        "beds_from": int(pairs["beds_from"].sum()),
        "beds_to": int(pairs["beds_to"].sum()),
        "residents_from": float(pairs["res_from"].sum()),
        "residents_to": float(pairs["res_to"].sum()),
        "occupancy_from": occ_from, "occupancy_to": occ_to,
        "occupancy_change_pts": (occ_to - occ_from) * 100,
        "nurse_hprd_from": hprd_from, "nurse_hprd_to": hprd_to,
        "nurse_hprd_change_pct": (hprd_to / hprd_from - 1) * 100,
        "rn_hprd_from": rn_from, "rn_hprd_to": rn_to,
        "rn_share_from": rn_from / hprd_from * 100,
        "rn_share_to": rn_to / hprd_to * 100,
        "rating_from": float(pairs["rating_from"].mean()),
        "rating_to": float(pairs["rating_to"].mean()),
        "rating_change": float(pairs["rating_to"].mean() - pairs["rating_from"].mean()),
        "deficiencies_from": float(pairs["defic_from"].mean()),
        "deficiencies_to": float(pairs["defic_to"].mean()),
        "pct_improved": float((pairs.loc[rated, "rating_to"]
                               > pairs.loc[rated, "rating_from"]).mean() * 100),
        "pct_unchanged": float((pairs.loc[rated, "rating_to"]
                                == pairs.loc[rated, "rating_from"]).mean() * 100),
        "pct_declined": float((pairs.loc[rated, "rating_to"]
                               < pairs.loc[rated, "rating_from"]).mean() * 100),
    }
    return pairs, context


def bq5_capacity_exit(
    con,
    from_period: int = 20190101,
    to_period: int | None = None,
) -> dict:
    """Who left the market, and what they looked like before they left.

    This is the finding that decides how the occupancy number in BQ5 may be
    read. If the homes that closed were the empty ones, then national occupancy
    can rise while demand falls, because the denominator left the market. That
    is a different world from a demand recovery and it points at a different
    investment decision, so it is computed rather than assumed.
    """
    periods = available_periods(con)
    if to_period is None:
        to_period = int(periods["period_key"].iloc[-1])
    frame = con.execute(
        """
        WITH a AS (SELECT * FROM Fact_Facility_Monthly
                   WHERE snapshot_date_key = ? AND NOT is_suspect),
             b AS (SELECT ccn FROM Fact_Facility_Monthly WHERE snapshot_date_key = ?)
        SELECT (b.ccn IS NULL)                    AS exited,
               COUNT(*)                           AS facilities,
               SUM(a.certified_beds)              AS beds,
               SUM(a.avg_residents_per_day)       AS residents,
               SUM(a.avg_residents_per_day) / NULLIF(SUM(a.certified_beds), 0)
                                                  AS occupancy_before,
               AVG(a.overall_rating)              AS rating_before,
               AVG(a.reported_total_nurse_hprd)   AS nurse_hprd_before
        FROM a LEFT JOIN b ON a.ccn = b.ccn
        GROUP BY 1
        """,
        [from_period, to_period],
    ).df()
    # Either group can be empty — comparing a period against itself leaves
    # nobody in the "exited" bucket, and `.iloc[0]` on that would raise an
    # IndexError that says nothing about the cause. A zero-filled row keeps the
    # return shape stable so callers can render it either way.
    empty = pd.Series({c: 0.0 for c in frame.columns})
    matching = frame[frame["exited"]]
    remaining = frame[~frame["exited"]]
    left = matching.iloc[0] if len(matching) else empty
    stayed = remaining.iloc[0] if len(remaining) else empty
    total = left["facilities"] + stayed["facilities"]
    return {
        "exited_facilities": int(left["facilities"]),
        "exited_beds": int(left["beds"]),
        "exited_share_pct": float(left["facilities"] / total * 100) if total else 0.0,
        "exited_occupancy_before": float(left["occupancy_before"]),
        "stayed_occupancy_before": float(stayed["occupancy_before"]),
        "exited_rating_before": float(left["rating_before"]),
        "stayed_rating_before": float(stayed["rating_before"]),
        "exited_hprd_before": float(left["nurse_hprd_before"]),
        "stayed_hprd_before": float(stayed["nurse_hprd_before"]),
    }


# ---------------------------------------------------------------------
#  BQ6 — the watchlist
#
#  Weights are integers and the components are visible in the output. A
#  fitted model would score better on the back-test and would be useless
#  here: a COO who cannot see why a home is on the list cannot act on it,
#  and cannot defend putting it there. The point of the back-test below is
#  not to prove the weights are optimal — it is to show the ranking they
#  produce actually separated future outcomes on data it never saw.
# ---------------------------------------------------------------------

RISK_WEIGHTS = {
    "flag_special_focus": 3,   # CMS already has the home under special watch
    "flag_low_rating": 2,      # 1-2 stars overall
    "flag_prior_fine": 2,      # in the worst quartile of fines per bed
    "flag_deficiencies": 1,    # in the worst quartile of deficiency count
    "flag_thin_staffing": 1,   # below the CMS 3.48 HPRD floor
}
RISK_MAX = sum(RISK_WEIGHTS.values())


def _risk_flags(roster: pd.DataFrame) -> pd.DataFrame:
    """Attach the five back-tested risk flags and the score they sum to.

    Quartile cut points are taken from whatever roster is passed in, so a
    filtered dashboard view scores facilities against their own peer group
    rather than against a national threshold. That is the behaviour a regional
    manager wants: the question is "which of *my* homes", not "which of my
    homes would look bad next to Illinois".
    """
    out = roster.copy()
    fines_cut = out["fines_per_bed"].quantile(0.75)
    defic_cut = out["deficiencies"].quantile(0.75)
    out["flag_special_focus"] = out["special_focus_status"].isin(["SFF", "SFF Candidate"])
    out["flag_low_rating"] = (out["rating"] <= 2).fillna(False)
    out["flag_prior_fine"] = (out["fines_per_bed"] >= fines_cut).fillna(False)
    out["flag_deficiencies"] = (out["deficiencies"] >= defic_cut).fillna(False)
    out["flag_thin_staffing"] = (out["nurse_hprd"] < CMS_MIN_TOTAL_HPRD).fillna(False)
    out["risk_score"] = sum(
        out[flag].astype(int) * weight for flag, weight in RISK_WEIGHTS.items()
    )
    out["risk_reasons"] = out.apply(
        lambda r: ", ".join(
            label for flag, label in [
                ("flag_special_focus", "special focus"),
                ("flag_low_rating", "1-2 stars"),
                ("flag_prior_fine", "heavily fined"),
                ("flag_deficiencies", "many deficiencies"),
                ("flag_thin_staffing", "below 3.48 HPRD"),
            ] if r[flag]
        ) or "-",
        axis=1,
    )
    return out


def bq6_watchlist(con, top_n: int = 50, **kwargs) -> tuple[pd.DataFrame, dict]:
    """BQ6 — homes that are deteriorating and are likely to be penalised next.

    The score answers "which homes look like the homes that get fined". The
    trajectory columns answer the other half of the question — "which are
    getting *worse*" — by comparing this period against the earliest 2026
    period, three months back. A home can be bad and stable, or decent and
    falling; the COO needs to tell those apart, so both are shown rather than
    blended into one number.
    """
    roster, context = facility_roster(con, **kwargs)
    scored = _risk_flags(roster)

    # recent trajectory: this period vs the first period of the same year
    periods = available_periods(con)
    same_year = periods[periods["year"] == periods.loc[
        periods["period_key"] == context["period_key"], "year"].iloc[0]]
    baseline_key = int(same_year["period_key"].iloc[0])
    if baseline_key != context["period_key"]:
        before = con.execute(
            """
            SELECT ccn,
                   overall_rating            AS rating_before,
                   cycle1_total_deficiencies AS deficiencies_before
            FROM Fact_Facility_Monthly WHERE snapshot_date_key = ?
            """,
            [baseline_key],
        ).df()
        scored = scored.merge(before, on="ccn", how="left")
        scored["rating_change"] = scored["rating"] - scored["rating_before"]
        scored["deficiency_change"] = scored["deficiencies"] - scored["deficiencies_before"]
        scored["declining"] = (scored["rating_change"] < 0).fillna(False)
        context["trajectory_baseline"] = str(
            same_year["period"].iloc[0])[:10]
    else:
        scored["rating_change"] = pd.NA
        scored["deficiency_change"] = pd.NA
        scored["declining"] = False
        context["trajectory_baseline"] = None

    ranked = scored.sort_values(
        ["risk_score", "declining", "fines_per_bed"], ascending=[False, False, False]
    ).reset_index(drop=True)

    context |= {
        "risk_weights": RISK_WEIGHTS,
        "risk_max": RISK_MAX,
        "flagged_any": int((ranked["risk_score"] > 0).sum()),
        "flagged_high": int((ranked["risk_score"] >= 5).sum()),
        "declining_count": int(ranked["declining"].sum()),
        "special_focus_count": int(ranked["flag_special_focus"].sum()),
        "below_floor_count": int(ranked["flag_thin_staffing"].sum()),
    }
    columns = [
        "ccn", "provider_name", "city", "state_code", "ownership_group",
        "chain_size_band", "beds", "rating", "rating_change", "deficiencies",
        "deficiency_change", "nurse_hprd", "turnover_pct", "fine_total",
        "fines_per_bed", "special_focus_status", "abuse_icon",
        "risk_score", "risk_reasons",
    ]
    return ranked[columns].head(top_n), context


def bq6_backtest(con, exclude_suspect: bool = True) -> tuple[pd.DataFrame, dict]:
    """Score the 2019 population, then look up what actually happened by 2026.

    This is the only honest way to claim the watchlist is worth reading. The
    score is built from the January 2019 snapshot and its own three-year fine
    window (2016-2019); the outcome is fines issued between July 2023 and July
    2026 and the star rating in July 2026. No outcome information reaches the
    score, and more than four years separate the two.

    A caveat the numbers carry rather than hide: the abuse icon was only
    populated for 40% of facilities in the January 2019 file, so it is left out
    of the score entirely. Adding a signal that exists in one era and not the
    other would make the back-test flatter than the live list.
    """
    suspect_clause = "AND NOT is_suspect" if exclude_suspect else ""
    scored_2019, _ = facility_roster(con, period_key=20190101,
                                     exclude_suspect=exclude_suspect)
    scored_2019 = _risk_flags(scored_2019)

    outcome = con.execute(
        f"""
        WITH later AS (
            SELECT ccn, overall_rating AS rating_2026, certified_beds AS beds_2026
            FROM Fact_Facility_Monthly
            WHERE snapshot_date_key = 20260701 {suspect_clause}
        ),
        fines AS (
            SELECT p.ccn, SUM(p.fine_amount_usd) AS fines_2023_26
            FROM Fact_Penalty_Event p
            JOIN Dim_Date d ON p.penalty_date_key = d.date_key
            WHERE d.full_date BETWEEN '2023-07-01' AND '2026-07-01'
            GROUP BY 1
        )
        SELECT l.ccn, l.rating_2026, l.beds_2026,
               COALESCE(f.fines_2023_26, 0) AS fines_2023_26
        FROM later l LEFT JOIN fines f ON f.ccn = l.ccn
        """
    ).df()

    merged = scored_2019.merge(outcome, on="ccn", how="left")
    merged["exited"] = merged["beds_2026"].isna()
    merged["fined_later"] = merged["fines_2023_26"].fillna(0) > 0

    survivors = merged[~merged["exited"]]
    base_rate = survivors["fined_later"].mean() * 100
    base_fpb = (survivors["fines_2023_26"].sum() / survivors["beds_2026"].sum())

    rows = []
    for score, x in merged.groupby("risk_score"):
        alive = x[~x["exited"]]
        rows.append({
            "risk_score": int(score),
            "facilities_2019": len(x),
            "pct_fined_2023_26": alive["fined_later"].mean() * 100,
            "fines_per_bed_2023_26": (alive["fines_2023_26"].sum()
                                      / max(alive["beds_2026"].sum(), 1)),
            "avg_rating_2026": alive["rating_2026"].mean(),
            "pct_exited_by_2026": x["exited"].mean() * 100,
            "avg_rating_2019": x["rating"].mean(),
        })
    frame = pd.DataFrame(rows)

    cut = merged["risk_score"].quantile(0.95)
    top = merged[merged["risk_score"] >= cut]
    top_alive = top[~top["exited"]]
    context = {
        "scored_period": "2019-01-01",
        "outcome_window": "2023-07-01 to 2026-07-01",
        "population": len(merged),
        "base_pct_fined": base_rate,
        "base_fines_per_bed": float(base_fpb),
        "top_decile_cut": float(cut),
        "top_facilities": len(top),
        "top_pct_fined": float(top_alive["fined_later"].mean() * 100),
        "top_fines_per_bed": float(top_alive["fines_2023_26"].sum()
                                   / max(top_alive["beds_2026"].sum(), 1)),
        "lift_incidence": float(top_alive["fined_later"].mean() * 100 / base_rate),
        "lift_dollars": float((top_alive["fines_2023_26"].sum()
                               / max(top_alive["beds_2026"].sum(), 1)) / base_fpb),
        "top_pct_exited": float(top["exited"].mean() * 100),
        "base_pct_exited": float(merged["exited"].mean() * 100),
    }
    return frame, context


def bq7_fines_timeline(
    con,
    states: list[str] | None = None,
    trailing_quarters_incomplete: int = 2,
) -> tuple[pd.DataFrame, dict]:
    """BQ7, the time half — penalty dollars by quarter and penalty type.

    Two things about this series have to be said out loud or every reading of
    it is wrong.

    **The gap.** Each Penalties file covers three years back from its snapshot,
    so the 2019 snapshot yields 2016-2018 and the 2026 snapshots yield
    2023-2026. Nothing covers 2019-2022. The chart must break the line there
    rather than draw through it, or it invents a COVID-era collapse in
    enforcement that the data does not show either way.

    **The tails.** Penalties reach the published file months after they are
    issued, so the last quarters before each snapshot are undercounts, not
    declines: 2018 Q4 holds 12 events against a 600-700 run rate. Those
    quarters are flagged here and drawn hollow rather than dropped, because
    dropping them hides that the truncation exists.
    """
    where = ""
    params: list = []
    if states:
        where = ("JOIN Dim_Geography g ON p.geography_key = g.geography_key "
                 "AND g.state_code IN (" + ",".join("?" * len(states)) + ")")
        params += list(states)

    frame = con.execute(
        f"""
        SELECT d.year, d.quarter,
               d.year || '-Q' || d.quarter                    AS year_quarter,
               t.penalty_type,
               COUNT(*)                                       AS events,
               COALESCE(SUM(p.fine_amount_usd), 0)            AS fines,
               COALESCE(SUM(p.payment_denial_days), 0)        AS denial_days,
               COUNT(DISTINCT p.ccn)                          AS facilities
        FROM Fact_Penalty_Event p
        JOIN Dim_Date d ON p.penalty_date_key = d.date_key
        JOIN Dim_Penalty_Type t ON p.penalty_type_key = t.penalty_type_key
        {where}
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 4
        """,
        params,
    ).df()

    # Label contiguous runs of quarters as eras, then flag each era's tail.
    quarters = (frame[["year", "quarter"]].drop_duplicates()
                .sort_values(["year", "quarter"]).reset_index(drop=True))
    quarters["index"] = quarters["year"] * 4 + quarters["quarter"]
    quarters["era"] = (quarters["index"].diff() > 1).cumsum()
    tails = (quarters.groupby("era").tail(trailing_quarters_incomplete)
             [["year", "quarter"]].assign(incomplete=True))
    frame = frame.merge(tails, on=["year", "quarter"], how="left")
    frame["incomplete"] = frame["incomplete"].fillna(False).astype(bool)
    frame = frame.merge(quarters[["year", "quarter", "era"]], on=["year", "quarter"])

    fines_only = frame[frame["penalty_type"] == "Fine"]
    by_year = fines_only.groupby("year").agg(
        events=("events", "sum"), fines=("fines", "sum")).reset_index()
    by_year["avg_fine"] = by_year["fines"] / by_year["events"]

    context = {
        "eras": int(quarters["era"].nunique()),
        "covered": [f"{int(q.year)}Q{int(q.quarter)}"
                    for q in quarters.itertuples()][:1]
                   + ["..."] +
                   [f"{int(q.year)}Q{int(q.quarter)}"
                    for q in quarters.itertuples()][-1:],
        "gap_years": "2019-2022 — no snapshot publishes penalties for these years",
        "avg_fine_by_year": by_year.set_index("year")["avg_fine"].round(0).to_dict(),
        "events_by_year": by_year.set_index("year")["events"].to_dict(),
        "total_fines": float(fines_only["fines"].sum()),
    }
    return frame, context


def bq7_state_enforcement(
    con,
    period_key: int | None = None,
    exclude_suspect: bool = True,
    min_facilities: int = 10,
) -> tuple[pd.DataFrame, dict]:
    """BQ7, the place half — which regulator hits hardest per bed.

    Total dollars answers "where is the money", which mostly restates which
    states are large. Dollars per certified bed answers "where is the risk to
    an operator", which is the question a CEO deciding where to buy actually
    has. Both are returned; the dashboard leads with the second.

    Beds come from the same period the fines window ends at, so numerator and
    denominator describe the same set of homes.
    """
    periods = available_periods(con)
    if period_key is None:
        period_key = int(periods["period_key"].iloc[-1])
    period_date = pd.Timestamp(
        periods.loc[periods["period_key"] == period_key, "period"].iloc[0])
    window_start = period_date - pd.DateOffset(years=FINES_WINDOW_YEARS)
    suspect_clause = "AND NOT f.is_suspect" if exclude_suspect else ""

    frame = con.execute(
        f"""
        WITH beds AS (
            SELECT g.state_code, g.state_name, g.census_region,
                   COUNT(*)                     AS facilities,
                   SUM(f.certified_beds)        AS beds,
                   SUM(f.avg_residents_per_day) AS residents,
                   AVG(f.overall_rating)        AS avg_rating
            FROM Fact_Facility_Monthly f
            JOIN Dim_Geography g ON f.geography_key = g.geography_key
            WHERE f.snapshot_date_key = ? {suspect_clause}
            GROUP BY 1, 2, 3
        ),
        pen AS (
            SELECT g.state_code,
                   SUM(p.fine_amount_usd)                        AS fines,
                   COUNT(*) FILTER (WHERE p.fine_amount_usd > 0) AS fine_events,
                   COUNT(DISTINCT p.ccn)                         AS fined_facilities,
                   SUM(p.payment_denial_days)                    AS denial_days
            FROM Fact_Penalty_Event p
            JOIN Dim_Date d      ON p.penalty_date_key = d.date_key
            JOIN Dim_Geography g ON p.geography_key    = g.geography_key
            WHERE d.full_date >= ? AND d.full_date <= ?
            GROUP BY 1
        )
        SELECT b.state_code, b.state_name, b.census_region,
               b.facilities, b.beds, b.avg_rating,
               COALESCE(pen.fines, 0)             AS fines,
               COALESCE(pen.fine_events, 0)       AS fine_events,
               COALESCE(pen.fined_facilities, 0)  AS fined_facilities,
               COALESCE(pen.denial_days, 0)       AS denial_days,
               COALESCE(pen.fines, 0) / NULLIF(b.beds, 0)          AS fines_per_bed,
               COALESCE(pen.fined_facilities, 0) * 100.0 / b.facilities
                                                                   AS pct_facilities_fined,
               COALESCE(pen.fines, 0) / NULLIF(pen.fine_events, 0) AS avg_fine
        FROM beds b LEFT JOIN pen ON pen.state_code = b.state_code
        WHERE b.facilities >= ?
        ORDER BY fines_per_bed DESC
        """,
        [period_key, str(window_start.date()), str(period_date.date()), min_facilities],
    ).df()

    top5 = frame.nlargest(5, "fines")
    context = {
        "period_key": period_key,
        "period": str(period_date.date()),
        "fines_window": f"{window_start.date()} to {period_date.date()}",
        "states": len(frame),
        "min_facilities": min_facilities,
        "total_fines": float(frame["fines"].sum()),
        "national_fines_per_bed": float(frame["fines"].sum() / frame["beds"].sum()),
        "national_pct_fined": float(frame["fined_facilities"].sum()
                                    / frame["facilities"].sum() * 100),
        "top5_states": list(top5["state_code"]),
        "top5_share_of_dollars": float(top5["fines"].sum() / frame["fines"].sum() * 100),
        "top5_share_of_beds": float(top5["beds"].sum() / frame["beds"].sum() * 100),
        "hardest": frame.iloc[0]["state_code"],
        "hardest_per_bed": float(frame.iloc[0]["fines_per_bed"]),
        "softest": frame.iloc[-1]["state_code"],
        "softest_per_bed": float(frame.iloc[-1]["fines_per_bed"]),
        "spread": float(frame.iloc[0]["fines_per_bed"] / max(frame.iloc[-1]["fines_per_bed"], 1)),
    }
    return frame, context


def kpi_summary(con, **kwargs) -> dict:
    """The measures that sit across the top of the dashboard.

    Every one of these is a ratio of sums or an average over the facilities
    that actually carry the value — never an average of per-facility ratios.
    That rule is the whole reason this function exists instead of the dashboard
    calling `.mean()` on a column.
    """
    roster, context = facility_roster(con, **kwargs)
    rated = roster["rating"].notna()
    staffed = roster["nurse_hprd"].notna()
    churned = roster["turnover_pct"].notna()
    return {
        **context,
        # M-numbers refer to the measure table in the design document
        "facilities": len(roster),
        "beds": int(roster["beds"].sum()),
        "residents": float(roster["residents"].sum()),
        "occupancy": float(roster["residents"].sum() / roster["beds"].sum()),        # M1
        "nurse_hprd": float((roster.loc[staffed, "nurse_hprd"]
                             * roster.loc[staffed, "residents"]).sum()
                            / roster.loc[staffed, "residents"].sum()),               # M3
        "rn_share": float(
            (roster.loc[staffed, "rn_hprd"] * roster.loc[staffed, "residents"]).sum()
            / (roster.loc[staffed, "nurse_hprd"] * roster.loc[staffed, "residents"]).sum()
        ),
        "turnover_pct": float((roster.loc[churned, "turnover_pct"]
                               * roster.loc[churned, "residents"]).sum()
                              / roster.loc[churned, "residents"].sum()),             # M4
        "avg_rating": float(roster.loc[rated, "rating"].mean()),                     # M7
        "pct_1_2_star": float((roster.loc[rated, "rating"] <= 2).mean() * 100),
        "fine_total": float(roster["fine_total"].sum()),                             # M5
        "fine_events": int(roster["fine_events"].sum()),                             # M6
        "deficiencies": float(roster["deficiencies"].mean()),                        # M8
        "fines_per_bed": float(roster["fine_total"].sum() / roster["beds"].sum()),
        "pct_fined": float(roster["was_fined"].mean() * 100),
        "below_cms_floor_pct": float(
            (roster.loc[staffed, "nurse_hprd"] < CMS_MIN_TOTAL_HPRD).mean() * 100),
    }
