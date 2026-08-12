"""Driver — build all six dimensions from the snapshots present locally.

    python run_dims.py                  every extracted period
    python run_dims.py --dates 2026-06-24 2026-07-29

fetch_snapshots.py must have run first; this file never downloads anything, so
a failure is unambiguously either an extract problem or a transform problem.
Run this before run_facts.py — the facts look their foreign keys up in these
tables.
"""

from __future__ import annotations

import argparse

import config
import dimensions
import load
import sources
from runlog import RunLog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build all six dimension tables")
    parser.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD",
                        help="explicit periods (default: every extracted period)")
    args = parser.parse_args(argv)

    config.setup_console()
    config.ensure_dirs()
    log = RunLog()

    frames = sources.read_all(args.dates, log)
    if not frames:
        print("! no usable period — run fetch_snapshots.py first")
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

    if problems:
        # Nothing is written: publishing rows we already know are broken would
        # leave the warehouse wrong and the warning buried in the terminal
        load.write_run_log(log)
        csv_path = log.save_csv()
        print("\n! problems found — nothing was written to the database")
        for p in problems:
            print(f"  - {p}")
        print(f"\n  run log:  {csv_path.name}")
        return 2

    print("\nWriting to the database")
    load.write_tables(tables, log)
    load.write_run_log(log)
    csv_path = log.save_csv()

    print("\n" + "=" * 62)
    for name, frame in tables.items():
        print(f"  {name:<22} {len(frame):>9,} rows")
    print(f"  database: {config.DB_PATH}")
    print(f"  run log:  {csv_path.name}")

    print("\nAll post-load checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
