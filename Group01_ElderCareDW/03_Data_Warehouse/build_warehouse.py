"""Apply the declared schema and the semantic layer to the ETL output.

    python build_warehouse.py            harden the schema, then create views
    python build_warehouse.py --report   also print what the warehouse holds

Why this step exists at all
---------------------------
The ETL writes with `CREATE OR REPLACE TABLE ... AS SELECT`, which keeps the
data and discards every guarantee about it. `schema.sql` is where the grain,
the keys and the value ranges are written down; this script is what makes
DuckDB enforce them.

How it works: each table is renamed aside, the declared table is created empty,
and the rows are inserted back through the constraints. A row that violates one
aborts the run and the original table is restored, so a failed run leaves the
warehouse exactly as it was rather than half converted.

That makes the insert a real test. `load.validate()` in the ETL checks the same
keys in pandas, but it checks the frames it built in memory; this checks what is
actually on disk, which is the thing the dashboard will open.

Run order:
    run_dims.py  ->  run_facts.py  ->  build_warehouse.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "eldercare.duckdb"
SCHEMA_SQL = HERE / "schema.sql"
VIEWS_SQL = HERE / "views.sql"

# Parents before children: a foreign key cannot be declared against a table
# that does not exist yet, and rows cannot be inserted into a child before its
# parent has the keys they point at.
TABLE_ORDER = [
    "Dim_Date",
    "Dim_Facility",
    "Dim_Geography",
    "Dim_Ownership",
    "Dim_Chain",
    "Dim_Penalty_Type",
    "Fact_Facility_Monthly",
    "Fact_Penalty_Event",
]

STAGE_SUFFIX = "_stage"


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SHOW TABLES").fetchall()}


def _views(con) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal"
        ).fetchall()
    ]


def _columns(con, table: str) -> list[str]:
    return [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]


def drop_views(con) -> list[str]:
    """Views must go before the tables they read, or the rename is refused."""
    dropped = _views(con)
    for name in dropped:
        con.execute(f'DROP VIEW IF EXISTS "{name}"')
    return dropped


def clear_stages(con) -> None:
    """Remove leftovers from a run that died midway, so a retry starts clean."""
    for name in _tables(con):
        if name.endswith(STAGE_SUFFIX):
            con.execute(f'DROP TABLE IF EXISTS "{name}"')


def harden(con) -> list[tuple[str, int]]:
    """Rebuild every table through schema.sql. Returns [(table, rows)]."""
    present = _tables(con)
    missing = [t for t in TABLE_ORDER if t not in present]
    if missing:
        raise SystemExit(
            f"! these tables are not in the database yet: {missing}\n"
            f"  run 02_ETL/run_dims.py then 02_ETL/run_facts.py first"
        )

    # Copy aside, then drop — not rename. On the second run the tables already
    # carry the foreign keys this script added, and renaming a fact does not
    # release its grip on the dimensions it points at: DuckDB then refuses to
    # touch Dim_Penalty_Type because a renamed table still depends on it. The
    # copies are made with plain CREATE TABLE AS, which carries no constraints
    # and therefore no dependencies, so the originals can be dropped outright.
    for name in TABLE_ORDER:
        con.execute(f'CREATE TABLE "{name}{STAGE_SUFFIX}" AS SELECT * FROM "{name}"')
    for name in reversed(TABLE_ORDER):
        con.execute(f'DROP TABLE "{name}"')

    con.execute(SCHEMA_SQL.read_text(encoding="utf-8"))

    results = []
    for name in TABLE_ORDER:
        cols = ", ".join(f'"{c}"' for c in _columns(con, name))
        # Columns are named explicitly rather than SELECT *: if the ETL adds a
        # column and schema.sql has not been updated, this fails with the name
        # of the column instead of silently shifting every value one position.
        con.execute(f'INSERT INTO "{name}" ({cols}) SELECT {cols} FROM "{name}{STAGE_SUFFIX}"')
        rows = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        results.append((name, rows))

    for name in TABLE_ORDER:
        con.execute(f'DROP TABLE "{name}{STAGE_SUFFIX}"')

    return results


def restore(con) -> None:
    """Put the staged copies back after a failed harden.

    Whatever schema.sql managed to create is dropped children-first, then the
    unconstrained copies are renamed into place. The warehouse comes back
    exactly as the ETL left it, which is the state the fix will be re-run from.
    """
    present = _tables(con)
    staged = [n for n in TABLE_ORDER if f"{n}{STAGE_SUFFIX}" in present]
    if not staged:
        return
    for name in reversed(TABLE_ORDER):
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
    for name in staged:
        con.execute(f'ALTER TABLE "{name}{STAGE_SUFFIX}" RENAME TO "{name}"')


def build_views(con) -> list[str]:
    con.execute(VIEWS_SQL.read_text(encoding="utf-8"))
    return sorted(_views(con))


def report(con) -> None:
    """Print the numbers a reader would otherwise have to take on trust."""
    print("\nCoverage")
    for row in con.execute("SELECT * FROM v_warehouse_coverage").fetchall():
        table, periods, earliest, latest, rows, facilities = row
        print(f"  {table:<24} {rows:>8,} rows · {facilities:>6,} facilities "
              f"· {periods} periods · {earliest} .. {latest}")

    print("\nWhy occupancy is a view and not a column (M1)")
    for row in con.execute(
        "SELECT snapshot_date, correct_sum_over_sum, wrong_avg_of_ratios, "
        "gap_percentage_points FROM v_check_ratio_additivity"
    ).fetchall():
        date, correct, wrong, gap = row
        print(f"  {str(date)[:10]}  SUM/SUM {correct:.2%}   AVG of ratios {wrong:.2%}"
              f"   gap {gap:+.2f} pts")

    print("\nM10 / BQ1 readiness")
    filled = con.execute(
        "SELECT COUNT(*) FROM Dim_Geography WHERE pop_65plus IS NOT NULL"
    ).fetchone()[0]
    if filled:
        print(f"  pop_65plus populated on {filled:,} rows — BQ1 can be answered")
    else:
        print("  pop_65plus is empty — v_market_saturation returns NULL for M10, "
              "and BQ1 stays blocked until the Census load exists")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply schema and views to the warehouse")
    parser.add_argument("--report", action="store_true", help="print a summary afterwards")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not DB_PATH.exists():
        print(f"! no database at {DB_PATH}\n  run 02_ETL/run_dims.py then run_facts.py first")
        return 1

    con = duckdb.connect(str(DB_PATH))
    try:
        print("Dropping views so the tables underneath can be replaced")
        dropped = drop_views(con)
        print(f"  dropped {len(dropped)} view(s)")

        clear_stages(con)

        print("\nApplying schema.sql — every row re-inserted through the constraints")
        try:
            results = harden(con)
        except duckdb.ConstraintException as exc:
            restore(con)
            print("\n! a constraint rejected the data — nothing was changed")
            print(f"  {exc}")
            print("\n  This is the schema doing its job: the warehouse is refusing to store")
            print("  something the design document says cannot happen. Fix the ETL rule the")
            print("  message names, re-run run_dims.py and run_facts.py, then try again.")
            return 2
        except duckdb.Error as exc:
            restore(con)
            print("\n! the rebuild failed — nothing was changed")
            print(f"  {type(exc).__name__}: {exc}")
            print("\n  This is not a data problem. Something is wrong with schema.sql itself")
            print("  or with the state of the database file.")
            return 3

        for name, rows in results:
            print(f"  {name:<24} {rows:>9,} rows")

        print("\nApplying views.sql")
        views = build_views(con)
        for name in views:
            print(f"  {name}")

        if args.report:
            report(con)

        print(f"\nWarehouse ready: {DB_PATH}")
        print("Query the views, not the fact tables — measures are defined once, in views.sql")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
