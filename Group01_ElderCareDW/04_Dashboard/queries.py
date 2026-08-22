"""Every query the dashboard runs.

Two rules hold throughout this file:

1. **Read the views, never the fact tables.** `03_Data_Warehouse/views.sql`
   defines M1-M10 once. A dashboard that reaches past it and writes its own
   `AVG(residents/beds)` gets an occupancy figure roughly 1.7 points too high
   and no error to say so.

2. **The measure expressions appear exactly once**, in `MEASURES_SQL` below.
   The filters make this awkward: `v_measure_by_state_period` is pre-grouped
   by state, so it cannot answer "occupancy for these four states, non-profit
   only". The dashboard therefore has to aggregate the row-level view itself,
   which means the formulas exist in two places — here and in views.sql.
   That duplication is guarded rather than trusted: `verify_dashboard.py`
   re-computes every measure through this constant and asserts it matches the
   warehouse views to 1e-9. If someone edits one and not the other, that test
   fails.

The connection is opened read-only. The dashboard is a reader; anything that
would change the warehouse belongs in 02_ETL.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "03_Data_Warehouse" / "eldercare.duckdb"

# ---------------------------------------------------------------------
#  The measure definitions. Mirrors views.sql; verified against it.
# ---------------------------------------------------------------------
MEASURES_SQL = """
    COUNT(*)                                    AS facilities,
    SUM(CASE WHEN is_suspect THEN 1 ELSE 0 END) AS suspect_rows,

    -- M1: ratio of sums. Never AVG of per-facility ratios.
    SUM(avg_residents_per_day) / NULLIF(SUM(certified_beds), 0)   AS m1_occupancy_rate,
    -- M2: fully additive, and the weight for M3/M4
    SUM(resident_days)                                            AS m2_resident_days,
    -- M3/M4: weighted by M2 so a 20-bed home does not outvote a 300-bed one
    SUM(reported_total_nurse_hprd * resident_days)
        / NULLIF(SUM(CASE WHEN reported_total_nurse_hprd IS NOT NULL
                          THEN resident_days END), 0)             AS m3_nurse_hprd,
    SUM(reported_rn_hprd * resident_days)
        / NULLIF(SUM(CASE WHEN reported_rn_hprd IS NOT NULL
                          THEN resident_days END), 0)             AS m3b_rn_hprd,
    SUM(total_nursing_turnover_pct * resident_days)
        / NULLIF(SUM(CASE WHEN total_nursing_turnover_pct IS NOT NULL
                          THEN resident_days END), 0)             AS m4_turnover_pct,
    -- M7: ordinal scale, plain mean over facilities
    AVG(CAST(overall_rating AS DOUBLE))                           AS m7_avg_overall_rating,
    -- M8: fully additive
    SUM(cycle1_total_deficiencies)                                AS m8_deficiencies,

    SUM(certified_beds)                                           AS total_certified_beds,
    SUM(avg_residents_per_day)                                    AS total_residents
"""


def connect() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"no warehouse at {DB_PATH}\n"
            "build it first:\n"
            "  cd ../02_ETL && python run_dims.py && python run_facts.py\n"
            "  cd ../03_Data_Warehouse && python build_warehouse.py"
        )
    return duckdb.connect(str(DB_PATH), read_only=True)


# =====================================================================
#  Filters
# =====================================================================
class Filters:
    """The sidebar's state, and the WHERE clause it turns into.

    `include_suspect` is a real analytical choice, not a nicety: rule Q4 flags
    implausible rows instead of deleting them, and the design document asks the
    dashboard to expose the switch so the imperfection stays visible rather than
    being quietly cleaned away before anyone sees it.
    """

    def __init__(
        self,
        periods: list[int] | None = None,
        states: list[str] | None = None,
        ownership: list[str] | None = None,
        chain_bands: list[str] | None = None,
        include_suspect: bool = True,
    ):
        self.periods = periods or []
        self.states = states or []
        self.ownership = ownership or []
        self.chain_bands = chain_bands or []
        self.include_suspect = include_suspect

    def key(self) -> tuple:
        """A hashable summary of this filter, used as the cache key.

        Streamlit's cache hashes arguments by value and cannot hash an arbitrary
        object, so `app.py` registers this through `hash_funcs`. Sorted, because
        picking the same states in a different order is the same query and should
        hit the same cache entry.
        """
        return (
            tuple(sorted(self.periods)),
            tuple(sorted(self.states)),
            tuple(sorted(self.ownership)),
            tuple(sorted(self.chain_bands)),
            self.include_suspect,
        )

    def __hash__(self) -> int:
        return hash(self.key())

    def __eq__(self, other) -> bool:
        return isinstance(other, Filters) and self.key() == other.key()

    def _clauses(self, *, with_period: bool = True) -> tuple[list[str], list]:
        where, params = [], []
        if with_period and self.periods:
            where.append(f"snapshot_date_key IN ({','.join(['?'] * len(self.periods))})")
            params += self.periods
        if self.states:
            where.append(f"state_code IN ({','.join(['?'] * len(self.states))})")
            params += self.states
        if self.ownership:
            where.append(f"ownership_group IN ({','.join(['?'] * len(self.ownership))})")
            params += self.ownership
        if self.chain_bands:
            where.append(f"chain_size_band IN ({','.join(['?'] * len(self.chain_bands))})")
            params += self.chain_bands
        if not self.include_suspect:
            where.append("NOT is_suspect")
        return where, params

    def facility_where(self, with_period: bool = True) -> tuple[str, list]:
        where, params = self._clauses(with_period=with_period)
        return ("WHERE " + " AND ".join(where) if where else ""), params

    def penalty_where(self) -> tuple[str, list]:
        """Penalties carry no period or suspect flag — only the shared dimensions."""
        where, params = [], []
        if self.states:
            where.append(f"state_code IN ({','.join(['?'] * len(self.states))})")
            params += self.states
        if self.ownership:
            where.append(f"ownership_group IN ({','.join(['?'] * len(self.ownership))})")
            params += self.ownership
        if self.chain_bands:
            where.append(f"chain_size_band IN ({','.join(['?'] * len(self.chain_bands))})")
            params += self.chain_bands
        return ("WHERE " + " AND ".join(where) if where else ""), params


def filter_options(con) -> dict:
    periods = con.execute(
        "SELECT DISTINCT snapshot_date_key, snapshot_year_month "
        "FROM v_facility_monthly ORDER BY snapshot_date_key"
    ).fetchall()
    return {
        "periods": [(int(k), v) for k, v in periods],
        "states": [r[0] for r in con.execute(
            "SELECT DISTINCT state_code FROM v_facility_monthly "
            "WHERE state_code IS NOT NULL ORDER BY 1").fetchall()],
        "ownership": [r[0] for r in con.execute(
            "SELECT DISTINCT ownership_group FROM v_facility_monthly ORDER BY 1").fetchall()],
        "chain_bands": [r[0] for r in con.execute(
            "SELECT DISTINCT chain_size_band FROM v_facility_monthly "
            "WHERE chain_size_band IS NOT NULL ORDER BY 1").fetchall()],
    }


# =====================================================================
#  Summary measures (the KPI strip)
# =====================================================================
def kpis(con, f: Filters) -> pd.Series:
    """Headline measures for the latest period inside the current filter."""
    where, params = f.facility_where()
    latest = con.execute(
        f"SELECT MAX(snapshot_date_key) FROM v_facility_monthly {where}", params
    ).fetchone()[0]
    if latest is None:
        return pd.Series(dtype="float64")

    w2 = (where + " AND " if where else "WHERE ") + "snapshot_date_key = ?"
    row = con.execute(
        f"SELECT {MEASURES_SQL} FROM v_facility_monthly {w2}", params + [latest]
    ).df().iloc[0]

    pw, pp = f.penalty_where()
    fines = con.execute(
        f"SELECT COALESCE(SUM(fine_amount_usd), 0) AS m5_fine_usd, "
        f"COALESCE(SUM(penalty_count), 0) AS m6_events FROM v_penalty_event {pw}", pp
    ).df().iloc[0]

    row["m5_fine_usd"] = fines["m5_fine_usd"]
    row["m6_penalty_events"] = fines["m6_events"]
    row["latest_period_key"] = latest
    row["latest_period"] = con.execute(
        "SELECT DISTINCT snapshot_year_month FROM v_facility_monthly "
        "WHERE snapshot_date_key = ?", [latest]
    ).fetchone()[0]
    return row


# =====================================================================
#  BQ5 — trend over the loaded periods (time series)
# =====================================================================
def trend(con, f: Filters) -> pd.DataFrame:
    where, params = f.facility_where(with_period=False)
    return con.execute(
        f"SELECT snapshot_date_key, snapshot_year_month, covid_period, {MEASURES_SQL} "
        f"FROM v_facility_monthly {where} GROUP BY ALL ORDER BY snapshot_date_key",
        params,
    ).df()


# =====================================================================
#  BQ7 — penalties over time (the one deep time series) and by state
# =====================================================================
def penalties_by_year(con, f: Filters) -> pd.DataFrame:
    where, params = f.penalty_where()
    return con.execute(
        f"""
        SELECT penalty_year,
               SUM(CASE WHEN penalty_type = 'Fine' THEN penalty_count ELSE 0 END) AS fine_events,
               SUM(CASE WHEN penalty_type = 'Payment Denial' THEN penalty_count ELSE 0 END)
                                                                                  AS denial_events,
               SUM(COALESCE(fine_amount_usd, 0))                                  AS m5_fine_usd,
               SUM(COALESCE(payment_denial_days, 0))                              AS denial_days
        FROM v_penalty_event {where}
        GROUP BY ALL ORDER BY penalty_year
        """,
        params,
    ).df()


def penalties_by_state(con, f: Filters, top_n: int = 15) -> pd.DataFrame:
    """Fines per certified bed — the enforcement-intensity question in BQ7.

    Per bed, not per facility: a state of large facilities would otherwise look
    lightly policed simply for having fewer buildings. Exposure comes from the
    latest period so the denominator is one period, not a sum across periods
    that would count the same beds several times.
    """
    fw, fp = f.facility_where()
    pw, pp = f.penalty_where()
    return con.execute(
        f"""
        WITH latest AS (SELECT MAX(snapshot_date_key) k FROM v_facility_monthly {fw}),
        beds AS (
            SELECT state_code, SUM(certified_beds) total_beds, COUNT(*) facilities
            FROM v_facility_monthly
            {fw or 'WHERE TRUE'} AND snapshot_date_key = (SELECT k FROM latest)
            GROUP BY 1
        ),
        fines AS (
            SELECT state_code,
                   SUM(COALESCE(fine_amount_usd, 0)) m5_fine_usd,
                   SUM(penalty_count) events
            FROM v_penalty_event {pw}
            GROUP BY 1
        )
        SELECT b.state_code, b.total_beds, b.facilities,
               COALESCE(fi.m5_fine_usd, 0) AS m5_fine_usd,
               COALESCE(fi.events, 0)      AS events,
               COALESCE(fi.m5_fine_usd, 0) / NULLIF(b.total_beds, 0) AS fine_per_bed
        FROM beds b LEFT JOIN fines fi USING (state_code)
        WHERE b.total_beds > 0
        ORDER BY fine_per_bed DESC NULLS LAST
        LIMIT {int(top_n)}
        """,
        fp + fp + pp,
    ).df()


# =====================================================================
#  BQ2 / BQ8 — comparison across ownership and chain size
# =====================================================================
def _compare(con, f: Filters, dimension: str) -> pd.DataFrame:
    """Measures grouped by one dimension, at the latest period in the filter.

    Fines are attached per group by aggregating the penalty fact separately and
    joining on the group key. Joining the two facts row-level would repeat a
    facility's beds once per penalty it ever received.
    """
    fw, fp = f.facility_where()
    pw, pp = f.penalty_where()
    latest = con.execute(
        f"SELECT MAX(snapshot_date_key) FROM v_facility_monthly {fw}", fp
    ).fetchone()[0]
    if latest is None:
        return pd.DataFrame()

    w2 = (fw + " AND " if fw else "WHERE ") + "snapshot_date_key = ?"
    facts = con.execute(
        f"SELECT {dimension} AS grp, {MEASURES_SQL} FROM v_facility_monthly {w2} GROUP BY ALL",
        fp + [latest],
    ).df()
    fines = con.execute(
        f"SELECT {dimension} AS grp, SUM(COALESCE(fine_amount_usd, 0)) AS m5_fine_usd, "
        f"SUM(penalty_count) AS m6_events FROM v_penalty_event {pw} GROUP BY ALL",
        pp,
    ).df()

    out = facts.merge(fines, on="grp", how="left")
    out[["m5_fine_usd", "m6_events"]] = out[["m5_fine_usd", "m6_events"]].fillna(0)
    out["fine_per_bed"] = out["m5_fine_usd"] / out["total_certified_beds"].replace(0, pd.NA)
    return out.sort_values("facilities", ascending=False)


def by_ownership(con, f: Filters) -> pd.DataFrame:
    return _compare(con, f, "ownership_group")


def by_chain_band(con, f: Filters) -> pd.DataFrame:
    return _compare(con, f, "chain_size_band")


# =====================================================================
#  BQ3 — nurse hours against quality and penalties
# =====================================================================
def hprd_bands(con, f: Filters) -> pd.DataFrame:
    """Facilities bucketed by nurse hours per resident-day.

    Fixed clinical bands rather than deciles: the bands stay comparable when the
    filter changes, and BQ3 asks where the payoff flattens, which needs an axis
    that does not move under the reader.
    """
    where, params = f.facility_where()
    w2 = (where + " AND " if where else "WHERE ") + "reported_total_nurse_hprd IS NOT NULL"
    return con.execute(
        f"""
        WITH banded AS (
            SELECT CASE
                     WHEN reported_total_nurse_hprd < 3.0 THEN '< 3.0'
                     WHEN reported_total_nurse_hprd < 3.5 THEN '3.0-3.5'
                     WHEN reported_total_nurse_hprd < 4.0 THEN '3.5-4.0'
                     WHEN reported_total_nurse_hprd < 4.5 THEN '4.0-4.5'
                     WHEN reported_total_nurse_hprd < 5.0 THEN '4.5-5.0'
                     ELSE '>= 5.0' END AS hprd_band,
                   *
            FROM v_facility_monthly {w2}
        )
        SELECT hprd_band, {MEASURES_SQL},
               AVG(CAST(health_inspection_rating AS DOUBLE)) AS avg_health_rating
        FROM banded GROUP BY ALL
        ORDER BY MIN(reported_total_nurse_hprd)
        """,
        params,
    ).df()


# =====================================================================
#  BQ4 — turnover against quality and occupancy
# =====================================================================
def turnover_bands(con, f: Filters) -> pd.DataFrame:
    where, params = f.facility_where()
    w2 = (where + " AND " if where else "WHERE ") + "total_nursing_turnover_pct IS NOT NULL"
    return con.execute(
        f"""
        WITH banded AS (
            SELECT CASE
                     WHEN total_nursing_turnover_pct < 30 THEN '< 30%'
                     WHEN total_nursing_turnover_pct < 40 THEN '30-40%'
                     WHEN total_nursing_turnover_pct < 50 THEN '40-50%'
                     WHEN total_nursing_turnover_pct < 60 THEN '50-60%'
                     WHEN total_nursing_turnover_pct < 70 THEN '60-70%'
                     ELSE '>= 70%' END AS turnover_band,
                   *
            FROM v_facility_monthly {w2}
        )
        SELECT turnover_band, {MEASURES_SQL}
        FROM banded GROUP BY ALL
        ORDER BY MIN(total_nursing_turnover_pct)
        """,
        params,
    ).df()


# =====================================================================
#  BQ1 — market attractiveness by state
# =====================================================================
def market(con, f: Filters) -> pd.DataFrame:
    """Supply, occupancy and competitor quality per state.

    M10 (beds per 1,000 aged 65+) comes from v_market_saturation and is NULL
    until the Census load exists. The column is kept rather than hidden so the
    gap is visible on the dashboard instead of being discovered in the report.
    """
    fw, fp = f.facility_where()
    latest = con.execute(
        f"SELECT MAX(snapshot_date_key) FROM v_facility_monthly {fw}", fp
    ).fetchone()[0]
    if latest is None:
        return pd.DataFrame()
    w2 = (fw + " AND " if fw else "WHERE ") + "snapshot_date_key = ?"
    # Aggregate first, join second. Joining v_market_saturation in before the
    # WHERE makes `state_code` ambiguous the moment a state filter is applied,
    # because both sides carry that column.
    return con.execute(
        f"""
        WITH base AS (
            SELECT state_code, state_name, census_region, {MEASURES_SQL}
            FROM v_facility_monthly
            {w2}
            GROUP BY state_code, state_name, census_region
        )
        SELECT b.*, ms.pop_65plus, ms.m10_beds_per_1000_elderly
        FROM base b
        LEFT JOIN v_market_saturation ms ON b.state_code = ms.state_code
        ORDER BY b.m1_occupancy_rate DESC NULLS LAST
        """,
        fp + [latest],
    ).df()


# =====================================================================
#  BQ6 — facilities declining across periods
# =====================================================================
def watchlist(con, f: Filters, limit: int = 40) -> pd.DataFrame:
    """Facilities whose overall rating fell between the first and last period.

    Compared per CCN, not per facility_key: facility_key is the SCD2 surrogate,
    so a facility that changed any tracked attribute holds several of them and
    would never match itself across periods.
    """
    where, params = f.facility_where(with_period=False)
    return con.execute(
        f"""
        WITH ranked AS (
            SELECT ccn, provider_name, state_code, ownership_group, chain_name,
                   overall_rating, staffing_rating, total_nursing_turnover_pct,
                   snapshot_date_key,
                   ROW_NUMBER() OVER (PARTITION BY ccn ORDER BY snapshot_date_key)      AS rn_first,
                   ROW_NUMBER() OVER (PARTITION BY ccn ORDER BY snapshot_date_key DESC) AS rn_last
            FROM v_facility_monthly {where}
        ),
        change AS (
            SELECT f.ccn, l.provider_name, l.state_code, l.ownership_group, l.chain_name,
                   f.overall_rating AS rating_first,
                   l.overall_rating AS rating_last,
                   l.overall_rating - f.overall_rating AS rating_delta,
                   l.total_nursing_turnover_pct AS turnover_last
            FROM (SELECT * FROM ranked WHERE rn_first = 1) f
            JOIN (SELECT * FROM ranked WHERE rn_last = 1) l USING (ccn)
            WHERE f.snapshot_date_key <> l.snapshot_date_key
              AND f.overall_rating IS NOT NULL AND l.overall_rating IS NOT NULL
        ),
        fines AS (
            SELECT ccn, SUM(COALESCE(fine_amount_usd, 0)) AS fines_usd,
                   SUM(penalty_count) AS penalty_events
            FROM v_penalty_event GROUP BY 1
        )
        SELECT c.*, COALESCE(fi.fines_usd, 0) AS fines_usd,
               COALESCE(fi.penalty_events, 0) AS penalty_events
        FROM change c LEFT JOIN fines fi USING (ccn)
        WHERE c.rating_delta < 0
        ORDER BY c.rating_delta ASC, fines_usd DESC
        LIMIT {int(limit)}
        """,
        params,
    ).df()


def watchlist_summary(con, f: Filters) -> dict:
    """How many facilities rose, fell, or held — context for the watchlist."""
    where, params = f.facility_where(with_period=False)
    row = con.execute(
        f"""
        WITH ranked AS (
            SELECT ccn, overall_rating, snapshot_date_key,
                   ROW_NUMBER() OVER (PARTITION BY ccn ORDER BY snapshot_date_key)      AS rn_first,
                   ROW_NUMBER() OVER (PARTITION BY ccn ORDER BY snapshot_date_key DESC) AS rn_last
            FROM v_facility_monthly {where}
        ),
        change AS (
            SELECT l.overall_rating - f.overall_rating AS d
            FROM (SELECT * FROM ranked WHERE rn_first = 1) f
            JOIN (SELECT * FROM ranked WHERE rn_last = 1) l USING (ccn)
            WHERE f.snapshot_date_key <> l.snapshot_date_key
              AND f.overall_rating IS NOT NULL AND l.overall_rating IS NOT NULL
        )
        SELECT COUNT(*) FILTER (WHERE d < 0) AS declined,
               COUNT(*) FILTER (WHERE d > 0) AS improved,
               COUNT(*) FILTER (WHERE d = 0) AS unchanged,
               COUNT(*) AS compared
        FROM change
        """,
        params,
    ).df().iloc[0]
    return {k: int(v) for k, v in row.items()}


# =====================================================================
#  Data-quality panel — the ETL's own run log, surfaced
# =====================================================================
def coverage(con) -> pd.DataFrame:
    return con.execute("SELECT * FROM v_warehouse_coverage").df()


def ratio_check(con) -> pd.DataFrame:
    return con.execute("SELECT * FROM v_check_ratio_additivity").df()


def quality_log(con, limit: int = 200) -> pd.DataFrame:
    """The Q-rules as they actually fired during the last ETL run."""
    return con.execute(
        f"""
        SELECT rule, step, snapshot_date, target, rows_affected, detail
        FROM etl_run_log
        WHERE run_id = (SELECT MAX(run_id) FROM etl_run_log)
          AND rule LIKE 'Q%'
        ORDER BY rule, snapshot_date
        LIMIT {int(limit)}
        """
    ).df()
