"""Load step — write the dimensions into DuckDB and validate afterwards.

Two principles from the design document:
  1. Re-runnable without manual fixes — tables are replaced whole, never
     appended to. All six dimensions are rebuilt from every snapshot on each
     run, so no stale state can accumulate.
  2. Re-running gives the same result — surrogate keys follow a fixed ordering.
"""

from __future__ import annotations

import duckdb
import pandas as pd

import config
from runlog import RunLog

# Build order — Dim_Date first, since everything else refers to dates
DIM_TABLES = [
    "Dim_Date",
    "Dim_Facility",
    "Dim_Geography",
    "Dim_Ownership",
    "Dim_Chain",
    "Dim_Penalty_Type",
]

# Primary key of each dimension, used by the post-load checks
PRIMARY_KEYS = {
    "Dim_Date": "date_key",
    "Dim_Facility": "facility_key",
    "Dim_Geography": "geography_key",
    "Dim_Ownership": "ownership_key",
    "Dim_Chain": "chain_key",
    "Dim_Penalty_Type": "penalty_type_key",
}

# Natural keys that must be unique. For Dim_Facility it is not ccn alone,
# because that table is a type 2 SCD.
NATURAL_KEYS = {
    "Dim_Date": ["full_date"],
    "Dim_Facility": ["ccn", "effective_date"],
    "Dim_Geography": ["zip_code", "city", "state_code"],
    "Dim_Ownership": ["ownership_type"],
    "Dim_Chain": ["chain_name"],
    "Dim_Penalty_Type": ["penalty_type"],
}


def write_tables(tables: dict[str, pd.DataFrame], log: RunLog) -> None:
    """Write every DataFrame into DuckDB, replacing what is there."""
    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    try:
        for name in DIM_TABLES:
            if name not in tables:
                continue
            frame = tables[name]
            con.register("_incoming", frame)
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _incoming")
            con.unregister("_incoming")
            log.add(
                step="load",
                rule="write_table",
                target=name,
                rows_affected=len(frame),
                detail=f"wrote {name} into {config.DB_PATH.name}",
            )

        # The run log has to accumulate across runs, so it is appended
        frame = log.to_frame()
        con.register("_runlog", frame)
        con.execute(
            "CREATE TABLE IF NOT EXISTS etl_run_log AS SELECT * FROM _runlog WHERE 1=0"
        )
        con.execute("INSERT INTO etl_run_log SELECT * FROM _runlog")
        con.unregister("_runlog")
    finally:
        con.close()


def validate(tables: dict[str, pd.DataFrame], log: RunLog) -> list[str]:
    """Post-load checks. Returns the problems found (empty means all passed).

    Four things: primary key unique, primary key not null, natural key unique,
    and SCD2 validity ranges that do not overlap.
    """
    problems: list[str] = []

    for name, frame in tables.items():
        pk = PRIMARY_KEYS.get(name)
        if pk is None:
            continue

        dup = int(frame[pk].duplicated().sum())
        if dup:
            problems.append(f"{name}: primary key {pk} duplicated on {dup} rows")
        null_pk = int(frame[pk].isna().sum())
        if null_pk:
            problems.append(f"{name}: primary key {pk} null on {null_pk} rows")

        nk = [c for c in NATURAL_KEYS.get(name, []) if c in frame.columns]
        if nk:
            dup_nk = int(frame.duplicated(subset=nk).sum())
            if dup_nk:
                problems.append(f"{name}: natural key {nk} duplicated on {dup_nk} rows")

        log.add(
            step="load",
            rule="post_load_check",
            target=name,
            rows_affected=len(frame),
            detail=f"checked {name}: {len(frame):,} rows, primary key unique={dup == 0}",
        )

    problems.extend(_validate_scd2(tables.get("Dim_Facility"), log))
    return problems


def _validate_scd2(facility: pd.DataFrame | None, log: RunLog) -> list[str]:
    """Check that the type 2 SCD is well formed.

    All three must hold at once:
      1. a CCN has exactly one row with is_current = True
      2. the ranges [effective_date, expiry_date) of one CCN never overlap
      3. every non-current row has an expiry_date
    """
    if facility is None or facility.empty:
        return []

    # The Unknown row (key = -1) is not a real facility, so SCD2 rules do not apply
    facility = facility[facility["facility_key"] != -1]
    if facility.empty:
        return []

    problems: list[str] = []

    current_per_ccn = facility[facility["is_current"]].groupby("ccn").size()
    bad = current_per_ccn[current_per_ccn > 1]
    if len(bad):
        problems.append(f"Dim_Facility: {len(bad)} CCN have more than one current row")

    stale_no_expiry = facility[(~facility["is_current"]) & facility["expiry_date"].isna()]
    if len(stale_no_expiry):
        problems.append(
            f"Dim_Facility: {len(stale_no_expiry)} non-current rows without an expiry_date"
        )

    ordered = facility.sort_values(["ccn", "effective_date"])
    prev_ccn = ordered["ccn"].shift()
    prev_expiry = ordered["expiry_date"].shift()
    overlap = (
        (ordered["ccn"] == prev_ccn)
        & prev_expiry.notna()
        & (ordered["effective_date"] < prev_expiry)
    )
    if int(overlap.sum()):
        problems.append(f"Dim_Facility: {int(overlap.sum())} rows with overlapping ranges")

    log.add(
        step="load",
        rule="scd2_check",
        target="Dim_Facility",
        rows_affected=len(facility),
        detail=(
            f"SCD2 check: {facility['ccn'].nunique():,} CCN, "
            f"{len(facility):,} versions, {len(problems)} problems"
        ),
    )
    return problems
