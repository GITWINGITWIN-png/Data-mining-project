"""Verify the loaded dimensions with questions whose answers are known upfront.

Deliberately separate from run_dims.py: code that checks itself with the same
logic it used to build the data will always pass, even when that logic is wrong.

    python verify_dims.py

Every [PASS] line is evidence that can be cited in the AI usage log.
Every [FAIL] line has to be fixed before starting on the fact tables.
"""

from __future__ import annotations

import duckdb
import pandas as pd

import clean
import config
import fetch_snapshots as fetch

PASS, FAIL, INFO = "[PASS]", "[FAIL]", "[NOTE]"


class Checker:
    def __init__(self) -> None:
        self.failed = 0
        self.passed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        tag = PASS if ok else FAIL
        self.passed += ok
        self.failed += not ok
        print(f"  {tag} {name}" + (f" — {detail}" if detail else ""))

    def note(self, name: str, detail: str) -> None:
        print(f"  {INFO} {name} — {detail}")


def main() -> int:
    config.setup_console()
    if not config.DB_PATH.exists():
        print("! no database yet — run run_dims.py first")
        return 1

    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    c = Checker()

    print("\n1. Row counts and the Unknown row of every dimension")
    for table, key in [
        ("Dim_Date", "date_key"),
        ("Dim_Facility", "facility_key"),
        ("Dim_Geography", "geography_key"),
        ("Dim_Ownership", "ownership_key"),
        ("Dim_Chain", "chain_key"),
        ("Dim_Penalty_Type", "penalty_type_key"),
    ]:
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        n_unknown = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {key} = -1"
        ).fetchone()[0]
        n_dup = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {key} FROM {table} GROUP BY 1 HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        c.check(
            f"{table}: {n:,} rows, unique key, has an Unknown row",
            n_unknown == 1 and n_dup == 0,
            f"unknown={n_unknown} duplicated={n_dup}",
        )

    print("\n2. Dim_Date — values that can be worked out by hand")
    row = con.execute(
        "SELECT year, quarter, month, us_fiscal_year, covid_period "
        "FROM Dim_Date WHERE date_key = 20261124"
    ).fetchone()
    c.check(
        "2026-11-24 is Q4, month 11, fiscal year 2027, Post-COVID",
        row == (2026, 4, 11, 2027, "Post-COVID"),
        str(row),
    )
    n_leap = con.execute(
        "SELECT COUNT(*) FROM Dim_Date WHERE month = 2 AND day(full_date) = 29"
    ).fetchone()[0]
    c.check("every leap day from 1960 to 2027 is present", n_leap == 17, f"{n_leap} days")
    span = con.execute(
        "SELECT min(full_date)::DATE, max(full_date)::DATE FROM Dim_Date WHERE date_key <> -1"
    ).fetchone()
    c.check(
        "date range matches the design",
        str(span[0]) == config.DATE_DIM_START and str(span[1]) == config.DATE_DIM_END,
        f"{span[0]} to {span[1]}",
    )

    print("\n3. Dim_Facility — SCD type 2")
    n_ccn, n_ver = con.execute(
        "SELECT COUNT(DISTINCT ccn), COUNT(*) FROM Dim_Facility WHERE facility_key <> -1"
    ).fetchone()
    c.check("more versions than CCN", n_ver > n_ccn, f"{n_ccn:,} CCN / {n_ver:,} versions")

    bad_current = con.execute(
        "SELECT COUNT(*) FROM (SELECT ccn FROM Dim_Facility "
        "WHERE facility_key <> -1 AND is_current GROUP BY 1 HAVING COUNT(*) <> 1)"
    ).fetchone()[0]
    c.check("every CCN has exactly one current row", bad_current == 0, f"{bad_current} bad CCN")

    overlap = con.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT ccn, effective_date,
                 LAG(expiry_date) OVER (PARTITION BY ccn ORDER BY effective_date) AS prev_expiry
          FROM Dim_Facility WHERE facility_key <> -1
        ) WHERE prev_expiry IS NOT NULL AND effective_date < prev_expiry
        """
    ).fetchone()[0]
    c.check("[effective_date, expiry_date) ranges never overlap", overlap == 0, f"{overlap} rows")

    dangling = con.execute(
        "SELECT COUNT(*) FROM Dim_Facility "
        "WHERE facility_key <> -1 AND NOT is_current AND expiry_date IS NULL"
    ).fetchone()[0]
    c.check("every closed row has an expiry_date", dangling == 0, f"{dangling} missing")

    print("\n4. Canary row CCN 015009 (always tied to a period)")
    canary = con.execute(
        "SELECT facility_key, provider_name, bed_size_band, effective_date, "
        "expiry_date, is_current FROM Dim_Facility WHERE ccn = ? ORDER BY effective_date",
        [config.CANARY_CCN],
    ).df()
    print(canary.to_string(index=False))
    c.check(
        "CCN 015009 is in Dim_Facility and its current row is BURNS NURSING HOME",
        len(canary) >= 1
        and bool(canary.loc[canary["is_current"], "provider_name"].str.contains("BURNS").any()),
    )
    c.check(
        "57 beds, so the band must be 'Medium (50-99 beds)'",
        (canary["bed_size_band"] == "Medium (50-99 beds)").all(),
        canary["bed_size_band"].unique().tolist(),
    )

    # Occupancy is checked back against the source file of a named period,
    # never as a free-floating number
    path = fetch.find_file(config.CANARY_SNAPSHOT, "provider_info")
    if path is None:
        c.note("canary occupancy", f"period {config.CANARY_SNAPSHOT} is not present locally")
    else:
        raw = pd.read_csv(path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
        raw["ccn"] = clean.normalize_ccn(raw["CMS Certification Number (CCN)"])
        row = raw[raw["ccn"] == config.CANARY_CCN].iloc[0]
        beds = float(row["Number of Certified Beds"])
        residents = float(row["Average Number of Residents per Day"])
        occupancy = residents / beds
        c.check(
            f"period {config.CANARY_SNAPSHOT}: {residents} / {beds} = {occupancy:.2%}",
            abs(occupancy - config.CANARY_OCCUPANCY) < 0.0005,
            f"expected {config.CANARY_OCCUPANCY:.2%} (the API's 90.53% is a different period)",
        )

    print("\n5. Dim_Ownership / Dim_Chain / Dim_Penalty_Type")
    groups = con.execute(
        "SELECT ownership_group, COUNT(*) FROM Dim_Ownership WHERE ownership_key <> -1 "
        "GROUP BY 1 ORDER BY 1"
    ).df()
    c.check(
        "ownership collapses into exactly three groups, none left Unknown",
        set(groups["ownership_group"]) == {"For profit", "Non profit", "Government"},
        dict(zip(groups["ownership_group"], groups["count_star()"])),
    )
    profit = con.execute(
        "SELECT COUNT(*) FROM Dim_Ownership "
        "WHERE is_for_profit <> (ownership_group = 'For profit')"
    ).fetchone()[0]
    c.check("is_for_profit always agrees with the group", profit == 0)

    indep = con.execute(
        "SELECT COUNT(*) FROM Dim_Chain WHERE is_independent"
    ).fetchone()[0]
    c.check("Dim_Chain keeps 'Independent' separate from 'Unknown'", indep == 1)

    monetary = con.execute(
        "SELECT penalty_type FROM Dim_Penalty_Type WHERE is_monetary"
    ).df()["penalty_type"].tolist()
    c.check("only Fine counts as monetary", monetary == ["Fine"], str(monetary))

    print("\n6. Dim_Geography")
    bad_region = con.execute(
        "SELECT COUNT(*) FROM Dim_Geography "
        "WHERE geography_key <> -1 AND census_region = 'Unknown'"
    ).fetchone()[0]
    c.check("every state maps to a region", bad_region == 0, f"{bad_region} unknown")
    zip_len = con.execute(
        "SELECT COUNT(*) FROM Dim_Geography WHERE geography_key <> -1 AND length(zip_code) <> 5"
    ).fetchone()[0]
    c.check("every ZIP is 5 characters (rule Q1)", zip_len == 0, f"{zip_len} bad")
    # Population lives in Ref_State_Population at (state, year) grain, never on
    # this table: at (zip, city, state) grain a state's population repeats on
    # every ZIP row and a plain SUM multiplies it by the ZIP count.
    has_pop_column = con.execute(
        "SELECT COUNT(*) FROM duckdb_columns() "
        "WHERE table_name = 'Dim_Geography' AND column_name = 'pop_65plus'"
    ).fetchone()[0]
    c.check(
        "population is not stored on Dim_Geography, whose grain would multiply it",
        has_pop_column == 0,
        "pop_65plus is back on Dim_Geography — see Ref_State_Population" if has_pop_column
        else "held in Ref_State_Population at (state, year) grain",
    )

    pop_states = con.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = 'Ref_State_Population'"
    ).fetchone()[0]
    if pop_states:
        states, years = con.execute(
            "SELECT COUNT(DISTINCT state_code), COUNT(DISTINCT year) FROM Ref_State_Population"
        ).fetchone()
        c.check(
            "Ref_State_Population covers the states BQ1 ranks",
            states >= 51,
            f"{states} states x {years} years",
        )
    else:
        c.check(
            "Ref_State_Population exists, or BQ1 cannot be answered",
            False,
            "table missing — run population.py (no API key needed, it falls back)",
        )

    print("\n7. Observations that affect the fact table design")
    dates = con.execute(
        "SELECT DISTINCT effective_date FROM Dim_Facility "
        "WHERE facility_key <> -1 ORDER BY 1"
    ).df()["effective_date"].tolist()
    c.note("time axis used by SCD2 (Processing Date)",
           str([str(pd.Timestamp(d).date()) for d in dates]))

    con.close()
    print("\n" + "=" * 62)
    print(f"  {c.passed} passed / {c.failed} failed")
    return 0 if c.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
