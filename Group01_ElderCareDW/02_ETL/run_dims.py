"""Driver — build all six dimensions from the snapshots present locally.

    python run_dims.py                  every extracted period
    python run_dims.py --dates 2026-06-24 2026-07-29

fetch_snapshots.py must have run first; this file never downloads anything, so
a failure is unambiguously either an extract problem or a transform problem.
"""

from __future__ import annotations

import argparse

import pandas as pd

import clean
import columns
import config
import dimensions
import fetch_snapshots as fetch
import load
from dimensions import SnapshotFrame
from runlog import RunLog


def read_snapshot(snapshot_date: str, log: RunLog) -> SnapshotFrame | None:
    """Read and clean one period. Returns None if the period is unusable.

    Periods with missing files are normal (2026-08-06 holds only 4 files and no
    Penalties). The rule is: **a missing file skips only that part, not the
    whole period** — except ProviderInfo, without which no dimension can be built.
    """
    pi_path = fetch.find_file(snapshot_date, "provider_info")
    if pi_path is None:
        log.add(
            step="extract",
            rule="missing_file",
            snapshot_date=snapshot_date,
            target="provider_info",
            detail="no ProviderInfo file — skipping the whole period",
        )
        return None

    raw = pd.read_csv(pi_path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
    df, era = columns.canonicalize(raw, "provider_info", snapshot_date, log)

    # Rule Q1 has to run before anything else, or every later join is unreliable
    df["ccn"] = clean.normalize_ccn(df["ccn"])
    df["zip_code"] = clean.normalize_zip(df["zip_code"])
    clean.check_ccn_quality(df, snapshot_date, log)

    processing = clean.parse_date(df["processing_date"], "processing_date", snapshot_date, log)
    stamp = processing.dropna().iloc[0] if processing.notna().any() else pd.Timestamp(snapshot_date)
    if processing.notna().any() and processing.nunique() > 1:
        log.add(
            step="clean",
            rule="Q6",
            snapshot_date=snapshot_date,
            target="processing_date",
            rows_affected=int(processing.nunique()),
            detail=f"one period holds several Processing Dates, using the first: {stamp.date()}",
        )
    if str(stamp.date()) != snapshot_date:
        log.add(
            step="extract",
            rule="date_axis",
            snapshot_date=snapshot_date,
            target="processing_date",
            detail=(
                f"archive date ({snapshot_date}) differs from Processing Date "
                f"({stamp.date()}) — Processing Date is the SCD2 time axis"
            ),
        )

    clean.report_nulls(
        df,
        ["overall_rating", "special_focus_status", "chain_name", "latitude",
         "total_nursing_turnover_pct"],
        snapshot_date,
        log,
    )

    penalties = None
    pen_path = fetch.find_file(snapshot_date, "penalties")
    if pen_path is None:
        log.add(
            step="extract",
            rule="missing_file",
            snapshot_date=snapshot_date,
            target="penalties",
            detail="no Penalties file in this period — skipping only the penalty part",
        )
    else:
        raw_pen = pd.read_csv(pen_path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
        penalties, _ = columns.canonicalize(raw_pen, "penalties", snapshot_date, log)
        penalties["ccn"] = clean.normalize_ccn(penalties["ccn"])

    return SnapshotFrame(
        snapshot_date=snapshot_date,
        processing_date=stamp,
        era=era,
        provider_info=df,
        penalties=penalties,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build all six dimension tables")
    parser.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD",
                        help="explicit periods (default: every extracted period)")
    args = parser.parse_args(argv)

    config.setup_console()
    config.ensure_dirs()
    log = RunLog()

    available = fetch.local_snapshot_dates()
    wanted = [d for d in available if not args.dates or d in set(args.dates)]
    if not wanted:
        print("! no extracted snapshots found — run fetch_snapshots.py first")
        return 1

    print(f"Using {len(wanted)} periods: {wanted}\n")

    frames: list[SnapshotFrame] = []
    for snapshot_date in wanted:      # already oldest first — SCD2 relies on this
        print(f"Reading period {snapshot_date}")
        frame = read_snapshot(snapshot_date, log)
        if frame is not None:
            frames.append(frame)

    if not frames:
        print("! no usable period")
        return 1

    print("\nBuilding dimensions")
    tables = {
        "Dim_Date": dimensions.build_dim_date(log),
        "Dim_Facility": dimensions.build_dim_facility(frames, log),
        "Dim_Geography": dimensions.build_dim_geography(frames, log),
        "Dim_Ownership": dimensions.build_dim_ownership(frames, log),
        "Dim_Chain": dimensions.build_dim_chain(frames, log),
        "Dim_Penalty_Type": dimensions.build_dim_penalty_type(frames, log),
    }

    print("\nValidating before writing to the database")
    problems = load.validate(tables, log)

    print("\nWriting to the database")
    load.write_tables(tables, log)
    csv_path = log.save_csv()

    print("\n" + "=" * 62)
    for name, frame in tables.items():
        print(f"  {name:<18} {len(frame):>8,} rows")
    print(f"  database: {config.DB_PATH}")
    print(f"  run log:  {csv_path.name}")

    if problems:
        print("\n! problems found")
        for p in problems:
            print(f"  - {p}")
        return 2

    print("\nAll post-load checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
