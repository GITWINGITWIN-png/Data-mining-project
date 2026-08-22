"""Apply the declared schema and the semantic layer to the ETL output.

    python build_warehouse.py            harden the schema, then create views
    python build_warehouse.py --report   also print what the warehouse holds

Why this step exists at all
---------------------------
The ETL writes with `CREATE OR REPLACE TABLE ... AS SELECT`, which keeps the
data and discards every guarantee about it. `schema.sql` is where the grain,
the keys and the value ranges are written down; this script is what makes
DuckDB enforce them.

How it works: a fresh database file is built beside the existing one, the
declared tables are created in it, and every row is inserted through the
constraints from the ETL output. Only once that succeeds is the new file swapped
into place, atomically. A row that violates a constraint aborts the run and the
temporary file is deleted, so the warehouse is never left half converted — the
original is not even opened for writing until the swap.

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


def build_into_new_file(source: Path, target: Path) -> list[tuple[str, int]]:
    """Create a fully constrained warehouse in a new file from `source`.

    Built fresh rather than converted in place. The in-place version dropped the
    tables and recreated them through schema.sql, which fails: DuckDB keeps a
    foreign key's reverse dependency alive across the drop sequence, so dropping
    a dimension is refused because a fact that was *already dropped* still
    claims it. Worse, the failure left the database half converted — staging
    copies present, fact tables gone — and the restore path hit the same catalog
    error trying to clean up.

    Building beside the original and swapping at the end removes the whole
    problem. Nothing is dropped, ordering cannot deadlock, and a failure leaves
    the original file untouched because it was never opened for writing.
    """
    if target.exists():
        target.unlink()

    con = duckdb.connect(str(target))
    try:
        con.execute(f"ATTACH '{source}' AS src (READ_ONLY)")

        missing = [
            t for t in TABLE_ORDER
            if not con.execute(
                "SELECT COUNT(*) FROM duckdb_tables() "
                "WHERE database_name = 'src' AND table_name = ?", [t]
            ).fetchone()[0]
        ]
        if missing:
            raise SystemExit(
                f"! these tables are not in the database yet: {missing}\n"
                f"  run 02_ETL/run_dims.py then 02_ETL/run_facts.py first"
            )

        con.execute(SCHEMA_SQL.read_text(encoding="utf-8"))

        results = []
        for name in TABLE_ORDER:
            cols = ", ".join(f'"{c}"' for c in _columns(con, name))
            # Columns named explicitly: if the ETL adds one and schema.sql has
            # not caught up, this fails naming the column rather than silently
            # shifting every value one position along.
            con.execute(f'INSERT INTO "{name}" ({cols}) SELECT {cols} FROM src."{name}"')
            rows = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            results.append((name, rows))

        # The run log carries no constraints and is not part of the star; copy
        # it across so the warehouse keeps its own audit trail.
        if con.execute(
            "SELECT COUNT(*) FROM duckdb_tables() "
            "WHERE database_name = 'src' AND table_name = 'etl_run_log'"
        ).fetchone()[0]:
            con.execute("CREATE TABLE etl_run_log AS SELECT * FROM src.etl_run_log")

        con.execute("DETACH src")
        con.execute(VIEWS_SQL.read_text(encoding="utf-8"))
        return results
    finally:
        con.close()


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

    # Clear staging copies left behind by the old in-place implementation, so a
    # database stranded by it can still be rebuilt.
    con = duckdb.connect(str(DB_PATH))
    try:
        leftovers = [t for t in _tables(con) if t.endswith(STAGE_SUFFIX)]
        for name in leftovers:
            con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if leftovers:
            print(f"Cleared {len(leftovers)} staging table(s) from an earlier failed run")
    finally:
        con.close()

    tmp = DB_PATH.with_suffix(".duckdb.building")
    print("\nBuilding a fresh warehouse — every row inserted through the constraints")
    try:
        results = build_into_new_file(DB_PATH, tmp)
    except duckdb.ConstraintException as exc:
        tmp.unlink(missing_ok=True)
        print("\n! a constraint rejected the data — the warehouse is unchanged")
        print(f"  {exc}")
        print("\n  This is the schema doing its job: it is refusing to store something the")
        print("  design document says cannot happen. Fix the ETL rule the message names,")
        print("  re-run run_dims.py and run_facts.py, then try again.")
        return 2
    except duckdb.Error as exc:
        tmp.unlink(missing_ok=True)
        print("\n! the rebuild failed — the warehouse is unchanged")
        print(f"  {type(exc).__name__}: {exc}")
        print("\n  This is not a data problem. Something is wrong with schema.sql itself")
        print("  or with the state of the database file.")
        return 3
    except SystemExit:
        tmp.unlink(missing_ok=True)
        raise

    for name, rows in results:
        print(f"  {name:<24} {rows:>9,} rows")

    # Swap only once the new file is complete and closed. os.replace is atomic
    # on the same filesystem, so there is no instant at which the warehouse is
    # missing or half written.
    tmp.replace(DB_PATH)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        print("\nViews")
        for name in sorted(_views(con)):
            print(f"  {name}")
        if args.report:
            report(con)
    finally:
        con.close()

    print(f"\nWarehouse ready: {DB_PATH}")
    print("Query the views, not the fact tables — measures are defined once, in views.sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
