"""Driver — build both fact tables from the snapshots present locally.

    python run_facts.py                  every extracted period
    python run_facts.py --dates 2026-06-24 2026-07-29

run_dims.py must have run first: the dimensions are read back out of the
database so the facts point at exactly the keys that were stored, not at a
second in-memory copy that might differ.
"""

from __future__ import annotations

import argparse

import duckdb
import pandas as pd

import config
import facts
import load
import sources
from runlog import RunLog


def read_dimensions() -> dict[str, pd.DataFrame]:
    """Load the six dimensions back out of DuckDB."""
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    try:
        existing = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        missing = [t for t in load.DIM_TABLES if t not in existing]
        if missing:
            raise SystemExit(f"! dimensions missing from the database: {missing} — run run_dims.py")
        return {name: con.execute(f"SELECT * FROM {name}").df() for name in load.DIM_TABLES}
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build both fact tables")
    parser.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD",
                        help="explicit periods (default: every extracted period)")
    args = parser.parse_args(argv)

    config.setup_console()
    config.ensure_dirs()
    log = RunLog()

    if not config.DB_PATH.exists():
        print("! no database yet — run run_dims.py first")
        return 1

    frames = sources.read_all(args.dates, log)
    if not frames:
        print("! no usable period — run fetch_snapshots.py first")
        return 1

    print("\nReading dimensions from the database")
    dims = read_dimensions()
    for name, frame in dims.items():
        print(f"  {name:<22} {len(frame):>9,} rows")

    print("\nBuilding facts")
    tables = {
        "Fact_Facility_Monthly": facts.build_fact_facility_monthly(frames, dims, log),
        "Fact_Penalty_Event": facts.build_fact_penalty_event(frames, dims, log),
    }

    print("\nValidating before writing to the database")
    problems = load.validate({**dims, **tables}, log)

    print("\nWriting to the database")
    load.write_tables(tables, log)
    csv_path = log.save_csv()

    print("\n" + "=" * 62)
    for name, frame in tables.items():
        print(f"  {name:<22} {len(frame):>9,} rows")
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
