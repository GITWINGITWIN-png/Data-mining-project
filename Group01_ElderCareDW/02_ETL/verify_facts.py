"""Verify the loaded fact tables against numbers whose answer is known upfront.

The strongest check here is the reconciliation in section 4: the fines this
pipeline stores per facility are compared against the "Total Amount of Fines in
Dollars" column CMS publishes in ProviderInfo. That column is computed by CMS
independently of anything we do, so agreement is real evidence rather than the
pipeline agreeing with itself.

    python verify_facts.py
"""

from __future__ import annotations

import duckdb
import pandas as pd

import clean
import columns
import config
import fetch_snapshots as fetch
from runlog import RunLog

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
        print("! no database yet — run run_dims.py then run_facts.py")
        return 1

    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    existing = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    missing = [t for t in ("Fact_Facility_Monthly", "Fact_Penalty_Event") if t not in existing]
    if missing:
        print(f"! fact tables missing: {missing} — run run_facts.py")
        return 1

    c = Checker()

    print("\n1. Fact_Facility_Monthly — grain and keys")
    n, n_periods, n_ccn = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT snapshot_date_key), COUNT(DISTINCT ccn) "
        "FROM Fact_Facility_Monthly"
    ).fetchone()
    c.note("size", f"{n:,} rows across {n_periods} periods and {n_ccn:,} CCN")

    dup = con.execute(
        "SELECT COUNT(*) FROM (SELECT snapshot_date_key, ccn FROM Fact_Facility_Monthly "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    c.check("grain holds: one row per (period, CCN)", dup == 0, f"{dup} duplicated")

    # A republished period must not become a second set of rows.
    # Derived from the data, never asserted against a constant: hard-coding the
    # expected period count makes this check pass while testing nothing, then fail
    # the moment another snapshot is loaded.
    per_period = con.execute(
        "SELECT snapshot_date_key, COUNT(*) FROM Fact_Facility_Monthly GROUP BY 1 ORDER BY 1"
    ).df()
    print(per_period.to_string(index=False))
    local_periods = len(fetch.local_snapshot_dates())
    c.check(
        "no period produced a second set of rows (periods <= snapshots extracted)",
        n_periods <= local_periods,
        f"{n_periods} periods in the fact vs {local_periods} snapshots on disk",
    )

    print("\n2. Foreign key integrity")
    for column, dim, key in [
        ("snapshot_date_key", "Dim_Date", "date_key"),
        ("facility_key", "Dim_Facility", "facility_key"),
        ("geography_key", "Dim_Geography", "geography_key"),
        ("ownership_key", "Dim_Ownership", "ownership_key"),
        ("chain_key", "Dim_Chain", "chain_key"),
    ]:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM Fact_Facility_Monthly f "
            f"LEFT JOIN {dim} d ON f.{column} = d.{key} WHERE d.{key} IS NULL"
        ).fetchone()[0]
        c.check(f"Fact_Facility_Monthly.{column} -> {dim}", orphans == 0, f"{orphans} orphans")

    for column, dim, key in [
        ("penalty_date_key", "Dim_Date", "date_key"),
        ("facility_key", "Dim_Facility", "facility_key"),
        ("penalty_type_key", "Dim_Penalty_Type", "penalty_type_key"),
    ]:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM Fact_Penalty_Event f "
            f"LEFT JOIN {dim} d ON f.{column} = d.{key} WHERE d.{key} IS NULL"
        ).fetchone()[0]
        c.check(f"Fact_Penalty_Event.{column} -> {dim}", orphans == 0, f"{orphans} orphans")

    chain_unknown = con.execute(
        "SELECT COUNT(*) FROM Fact_Facility_Monthly f JOIN Dim_Date d "
        "ON f.snapshot_date_key = d.date_key WHERE f.chain_key = -1 AND d.year = 2019"
    ).fetchone()[0]
    chain_unknown_total = con.execute(
        "SELECT COUNT(*) FROM Fact_Facility_Monthly WHERE chain_key = -1"
    ).fetchone()[0]
    c.check(
        "every Unknown chain key comes from the 2019 era, which has no chain column",
        chain_unknown == chain_unknown_total,
        f"{chain_unknown:,} of {chain_unknown_total:,}",
    )

    print("\n3. Canary row CCN 015009, period 2026-06-01")
    row = con.execute(
        "SELECT certified_beds, avg_residents_per_day, resident_days, is_suspect "
        "FROM Fact_Facility_Monthly WHERE ccn = ? AND snapshot_date_key = 20260601",
        [config.CANARY_CCN],
    ).fetchone()
    if row is None:
        c.check("canary row is present", False, "not found")
    else:
        beds, residents, resident_days, suspect = row
        occupancy = residents / beds
        c.check(
            f"{residents} / {beds} = {occupancy:.2%}",
            abs(occupancy - config.CANARY_OCCUPANCY) < 0.0005,
            f"expected {config.CANARY_OCCUPANCY:.2%} (the API's 90.53% is a different period)",
        )
        c.check(
            "resident_days = residents x 30 days in June",
            abs(resident_days - residents * 30) < 0.01,
            f"{resident_days:,.1f}",
        )
        c.check("not flagged suspect", not suspect)

    print("\n4. Reconciliation against the CMS-computed fines column")
    # CMS publishes a per-facility fine total covering a rolling 3-year window.
    # Restricting the fact table to that window must reproduce it exactly.
    pen_path = fetch.find_file(config.CANARY_SNAPSHOT, "penalties")
    pi_path = fetch.find_file(config.CANARY_SNAPSHOT, "provider_info")
    if pen_path is None or pi_path is None:
        c.note("reconciliation", f"period {config.CANARY_SNAPSHOT} is not present locally")
    else:
        pen = pd.read_csv(pen_path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
        window = pd.to_datetime(pen["Penalty Date"], errors="coerce")
        lo, hi = window.min().date(), window.max().date()
        c.note("rolling window of the Jun 2026 Penalties file", f"{lo} to {hi}")

        # Restricting by *date range* is not enough. A later period reports
        # penalties the Jun file had not yet listed (Fine ID 139504, $23,000,
        # dated 2026-04-15, appears only from Jul onward), so the warehouse
        # legitimately holds more than the Jun column covers. To compare like
        # with like, restrict to the events that file actually contained.
        jun_fine_ids = set(pen["Fine ID"].dropna().astype(str))
        fact_all = con.execute(
            """
            SELECT f.ccn, f.fine_id, f.fine_amount_usd
            FROM Fact_Penalty_Event f
            JOIN Dim_Date d ON f.penalty_date_key = d.date_key
            WHERE d.full_date BETWEEN ? AND ? AND f.fine_amount_usd IS NOT NULL
            """,
            [str(lo), str(hi)],
        ).df()
        in_file = fact_all["fine_id"].astype("string").isin(jun_fine_ids)
        newer = fact_all[~in_file]
        c.note(
            "reported after the Jun file was published",
            f"{len(newer):,} fines worth ${newer['fine_amount_usd'].sum():,.0f} "
            f"exist in the warehouse but not in that period's column",
        )
        fact = (
            fact_all[in_file]
            .groupby("ccn", as_index=False)
            .agg(fine_total=("fine_amount_usd", "sum"), events=("fine_id", "count"))
        )

        pi = pd.read_csv(pi_path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
        pi["ccn"] = clean.normalize_ccn(pi["CMS Certification Number (CCN)"])
        pi["cms_fine_total"] = clean.to_number(pi["Total Amount of Fines in Dollars"])
        pi["cms_penalty_count"] = clean.to_number(pi["Total Number of Penalties"])
        cms = pi[["ccn", "cms_fine_total", "cms_penalty_count"]]

        merged = cms.merge(fact, on="ccn", how="left")
        merged["fine_total"] = merged["fine_total"].fillna(0)
        merged["events"] = merged["events"].fillna(0)
        with_fines = merged[merged["cms_fine_total"] > 0]

        money_match = (
            (with_fines["fine_total"] - with_fines["cms_fine_total"]).abs() < 1.0
        )
        c.check(
            "per-facility fine totals match the CMS column",
            bool(money_match.all()),
            f"{int(money_match.sum()):,} of {len(with_fines):,} facilities agree",
        )
        c.note(
            "totals",
            f"pipeline ${with_fines['fine_total'].sum():,.0f} vs "
            f"CMS ${with_fines['cms_fine_total'].sum():,.0f}",
        )
        if not money_match.all():
            bad = with_fines[~money_match].head(5)
            print(bad[["ccn", "fine_total", "cms_fine_total"]].to_string(index=False))

    print("\n5. Fact_Penalty_Event — deduplication (rule Q2)")
    n_events, n_pen_ccn = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ccn) FROM Fact_Penalty_Event"
    ).fetchone()
    c.note("size", f"{n_events:,} events across {n_pen_ccn:,} CCN")

    dup_fine_id = con.execute(
        "SELECT COUNT(*) FROM (SELECT fine_id FROM Fact_Penalty_Event "
        "WHERE fine_id IS NOT NULL GROUP BY 1 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    c.check("no Fine ID appears twice", dup_fine_id == 0, f"{dup_fine_id} duplicated")

    sources = con.execute(
        "SELECT fine_id_source, COUNT(*) FROM Fact_Penalty_Event GROUP BY 1 ORDER BY 1"
    ).df()
    c.check(
        "both dedup systems are in use, as the era split requires",
        set(sources["fine_id_source"]) == {"fine_id", "natural_key"},
        dict(zip(sources["fine_id_source"], sources["count_star()"])),
    )

    # The check that would have caught the cross-era double counting. It needs no
    # Fine ID, so it works over the whole range: the rolling window lists every
    # penalty that existed at that moment, so the most rows any single period
    # reports for one identical group is the upper bound on how many truly exist.
    # Storing more than that means deduplication let a repeat through.
    quiet = RunLog()
    quiet.add = lambda **kwargs: None
    KEY = ["ccn", "pdate", "ptype", "amt"]
    per_file = []
    for snap in fetch.local_snapshot_dates():
        path = fetch.find_file(snap, "penalties")
        if path is None:
            continue
        raw = pd.read_csv(path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
        pen, _ = columns.canonicalize(raw, "penalties", snap, quiet)
        grouped = (
            pd.DataFrame({
                "ccn": clean.normalize_ccn(pen["ccn"]),
                "pdate": pd.to_datetime(pen["penalty_date"], errors="coerce"),
                "ptype": pen["penalty_type"].astype("string").str.strip(),
                # NaN never equals NaN in a join, so payment denials (no amount)
                # get a sentinel instead of being silently dropped
                "amt": clean.to_number(pen["fine_amount_usd"]).fillna(-1.0),
            })
            .groupby(KEY, dropna=False).size().rename("in_one_file").reset_index()
        )
        per_file.append(grouped)

    if per_file:
        source_max = (
            pd.concat(per_file, ignore_index=True)
            .groupby(KEY, dropna=False)["in_one_file"].max().reset_index()
        )
        stored = con.execute(
            """
            SELECT f.ccn AS ccn, d.full_date AS pdate, t.penalty_type AS ptype,
                   COALESCE(f.fine_amount_usd, -1.0) AS amt, COUNT(*) AS stored
            FROM Fact_Penalty_Event f
            JOIN Dim_Date d ON f.penalty_date_key = d.date_key
            JOIN Dim_Penalty_Type t ON f.penalty_type_key = t.penalty_type_key
            GROUP BY 1, 2, 3, 4
            """
        ).df()
        cmp = stored.merge(source_max, on=KEY, how="left")
        cmp["in_one_file"] = cmp["in_one_file"].fillna(0)
        over = cmp[cmp["stored"] > cmp["in_one_file"]]
        surplus = int((over["stored"] - over["in_one_file"]).sum())
        c.check(
            "no identical group holds more rows than any single period reported",
            surplus == 0,
            f"{len(over):,} groups over the bound, {surplus:,} surplus rows",
        )
        if surplus:
            print(over.sort_values("stored", ascending=False).head(5).to_string(index=False))
        c.note(
            "total fines stored",
            f"${con.execute('SELECT SUM(fine_amount_usd) FROM Fact_Penalty_Event').fetchone()[0]:,.0f}",
        )

    denial_with_id = con.execute(
        "SELECT COUNT(*) FROM Fact_Penalty_Event f JOIN Dim_Penalty_Type t "
        "ON f.penalty_type_key = t.penalty_type_key "
        "WHERE t.penalty_type = 'Payment Denial' AND f.fine_id IS NOT NULL"
    ).fetchone()[0]
    c.check(
        "no Payment Denial row carries a Fine ID, matching the source",
        denial_with_id == 0,
        f"{denial_with_id} rows",
    )

    monetary_mix = con.execute(
        """
        SELECT t.penalty_type,
               COUNT(*) AS events,
               COALESCE(SUM(f.fine_amount_usd), 0) AS fines,
               COALESCE(SUM(f.payment_denial_days), 0) AS denial_days
        FROM Fact_Penalty_Event f
        JOIN Dim_Penalty_Type t ON f.penalty_type_key = t.penalty_type_key
        GROUP BY 1 ORDER BY 1
        """
    ).df()
    print(monetary_mix.to_string(index=False))
    denial_money = float(monetary_mix.loc[
        monetary_mix["penalty_type"] == "Payment Denial", "fines"].sum())
    c.check(
        "payment denials carry no money, so M5 cannot mix the two units",
        denial_money == 0,
        f"${denial_money:,.0f}",
    )

    print("\n6. Rules Q4 and Q8 on Fact_Facility_Monthly")
    suspect = con.execute(
        "SELECT COUNT(*) FILTER (WHERE is_suspect), COUNT(*) FROM Fact_Facility_Monthly"
    ).fetchone()
    c.note("is_suspect", f"{suspect[0]:,} of {suspect[1]:,} rows flagged ({suspect[0]/suspect[1]:.2%})")
    kept = con.execute(
        "SELECT COUNT(*) FROM Fact_Facility_Monthly WHERE is_suspect "
        "AND avg_residents_per_day IS NOT NULL"
    ).fetchone()[0]
    c.check("flagged rows are kept, not deleted", kept > 0, f"{kept:,} still carry their values")

    turnover = con.execute(
        "SELECT MIN(total_nursing_turnover_pct), MEDIAN(total_nursing_turnover_pct), "
        "MAX(total_nursing_turnover_pct) FROM Fact_Facility_Monthly "
        "WHERE total_nursing_turnover_pct IS NOT NULL"
    ).fetchone()
    c.check(
        "turnover is stored as a percentage everywhere (rule Q8)",
        turnover[1] > 1.5,
        f"min {turnover[0]:.1f} median {turnover[1]:.1f} max {turnover[2]:.1f}",
    )

    print("\n7. Occupancy must be summed, never averaged")
    row = con.execute(
        """
        SELECT SUM(avg_residents_per_day) / SUM(certified_beds) AS correct,
               AVG(avg_residents_per_day / NULLIF(certified_beds, 0)) AS wrong
        FROM Fact_Facility_Monthly
        WHERE snapshot_date_key = 20260601 AND NOT is_suspect
        """
    ).fetchone()
    c.note(
        "period 2026-06-01",
        f"SUM/SUM = {row[0]:.2%} vs AVG of ratios = {row[1]:.2%} "
        f"(a {abs(row[0] - row[1]) * 100:.1f} point gap — this is why ratios are not stored)",
    )

    con.close()
    print("\n" + "=" * 62)
    print(f"  {c.passed} passed / {c.failed} failed")
    return 0 if c.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
