"""Check the dashboard against the warehouse, and against the brief.

    python verify_dashboard.py

Two jobs.

**1. The measure formulas must not drift.** `queries.MEASURES_SQL` restates the
expressions that `03_Data_Warehouse/views.sql` already defines, because the
pre-grouped views cannot answer a filtered question. Duplicated logic drifts
unless something compares it, so this re-computes every measure through the
dashboard's constant and asserts it matches the warehouse view to 1e-9. Edit one
without the other and this fails.

**2. The brief's minimums must actually be met.** The counts are read out of
`app.py` rather than asserted from memory, and the insights are generated for
real rather than counted from a list.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

import insights
import queries

HERE = Path(__file__).resolve().parent
TOL = 1e-9

MEASURE_COLUMNS = [
    "facilities", "suspect_rows", "m1_occupancy_rate", "m2_resident_days",
    "m3_nurse_hprd", "m3b_rn_hprd", "m4_turnover_pct", "m7_avg_overall_rating",
    "m8_deficiencies", "total_certified_beds", "total_residents",
]


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"  [PASS] {label}" + (f" — {detail}" if detail else ""))
        else:
            self.failed += 1
            print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))

    def note(self, label: str, detail: str = "") -> None:
        print(f"  [NOTE] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    con = queries.connect()
    c = Checker()

    # -----------------------------------------------------------------
    print("\n1. Measure formulas match the warehouse views")
    # -----------------------------------------------------------------
    mine = con.execute(
        f"""SELECT snapshot_date_key, state_code, {queries.MEASURES_SQL}
            FROM v_facility_monthly GROUP BY ALL"""
    ).df().sort_values(["snapshot_date_key", "state_code"]).reset_index(drop=True)

    theirs = con.execute(
        """SELECT snapshot_date_key, state_code, facilities, suspect_rows,
                  m1_occupancy_rate, m2_resident_days, m3_nurse_hprd, m3b_rn_hprd,
                  m4_turnover_pct, m7_avg_overall_rating, m8_deficiencies,
                  total_certified_beds, total_residents
           FROM v_measure_by_state_period"""
    ).df().sort_values(["snapshot_date_key", "state_code"]).reset_index(drop=True)

    c.check("same number of groups", len(mine) == len(theirs), f"{len(mine)} vs {len(theirs)}")

    for col in MEASURE_COLUMNS:
        a, b = mine[col], theirs[col]
        both_null = a.isna() & b.isna()
        diff = (a.fillna(0) - b.fillna(0)).abs()
        worst = float(diff[~both_null].max()) if (~both_null).any() else 0.0
        c.check(f"{col} matches views.sql", worst < TOL, f"max diff {worst:.3g}")

    # -----------------------------------------------------------------
    print("\n2. Filters actually narrow the data")
    # -----------------------------------------------------------------
    all_f = queries.Filters(periods=[k for k, _ in queries.filter_options(con)["periods"]])
    k_all = queries.kpis(con, all_f)

    one_state = queries.Filters(periods=all_f.periods, states=["CA"])
    k_ca = queries.kpis(con, one_state)
    c.check("state filter reduces the facility count",
            0 < k_ca["facilities"] < k_all["facilities"],
            f"{int(k_ca['facilities']):,} of {int(k_all['facilities']):,}")

    np_only = queries.Filters(periods=all_f.periods, ownership=["Non profit"])
    k_np = queries.kpis(con, np_only)
    c.check("ownership filter reduces the facility count",
            0 < k_np["facilities"] < k_all["facilities"],
            f"{int(k_np['facilities']):,}")

    no_suspect = queries.Filters(periods=all_f.periods, include_suspect=False)
    k_ns = queries.kpis(con, no_suspect)
    c.check("suspect toggle changes the result",
            k_ns["facilities"] < k_all["facilities"],
            f"{int(k_all['facilities']):,} -> {int(k_ns['facilities']):,}")
    c.note("occupancy with vs without suspect rows",
           f"{k_all['m1_occupancy_rate']:.4%} vs {k_ns['m1_occupancy_rate']:.4%}")

    # -----------------------------------------------------------------
    print("\n3. Every tab returns data")
    # -----------------------------------------------------------------
    for name, fn in [
        ("BQ5 trend", lambda: queries.trend(con, all_f)),
        ("BQ7 penalties by year", lambda: queries.penalties_by_year(con, all_f)),
        ("BQ7 penalties by state", lambda: queries.penalties_by_state(con, all_f)),
        ("BQ2 ownership", lambda: queries.by_ownership(con, all_f)),
        ("BQ8 chain band", lambda: queries.by_chain_band(con, all_f)),
        ("BQ3 hprd bands", lambda: queries.hprd_bands(con, all_f)),
        ("BQ4 turnover bands", lambda: queries.turnover_bands(con, all_f)),
        ("BQ1 market", lambda: queries.market(con, all_f)),
        ("BQ6 watchlist", lambda: queries.watchlist(con, all_f)),
        ("coverage", lambda: queries.coverage(con)),
        ("ratio check", lambda: queries.ratio_check(con)),
    ]:
        df = fn()
        c.check(f"{name} returns rows", isinstance(df, pd.DataFrame) and not df.empty,
                f"{len(df)} rows")

    # -----------------------------------------------------------------
    print("\n3b. Every query survives awkward filter combinations")
    # A filter that narrows to almost nothing, or to an era missing a column,
    # must return an empty frame — not raise. `market()` used to raise here:
    # joining v_market_saturation made `state_code` ambiguous as soon as a state
    # filter was applied, and no unfiltered test could reach it.
    # -----------------------------------------------------------------
    all_functions = [
        "trend", "penalties_by_year", "penalties_by_state", "by_ownership",
        "by_chain_band", "hprd_bands", "turnover_bands", "market",
        "watchlist", "watchlist_summary", "kpis",
    ]
    periods = all_f.periods
    cases = {
        "single period": queries.Filters(periods=[periods[-1]]),
        "earliest period only": queries.Filters(periods=[periods[0]]),
        "one small state": queries.Filters(periods=periods, states=["AK"]),
        "state + ownership": queries.Filters(periods=periods, states=["CA"],
                                             ownership=["Non profit"]),
        "combination matching nothing": queries.Filters(
            periods=periods, states=["AK"], ownership=["Government"],
            chain_bands=["Large chain (50+)"]),
        "suspect rows excluded": queries.Filters(periods=periods, include_suspect=False),
    }
    for label, filt in cases.items():
        errors = []
        for name in all_functions:
            try:
                getattr(queries, name)(con, filt)
            except Exception as exc:                    # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}")
        c.check(f"no query raises under '{label}'", not errors, ", ".join(errors) or "clean")

    for label, filt in cases.items():
        items = insights.build(con, filt)
        crashed = [i for i in items if i.bq == "—"]
        c.check(f"no insight crashes under '{label}'", not crashed,
                f"{len(items)} built, {len(crashed)} crashed")

    # -----------------------------------------------------------------
    print("\n4. M1 is never computed as an average of ratios")
    # -----------------------------------------------------------------
    rc = queries.ratio_check(con)
    gap = rc["gap_percentage_points"].abs().max()
    c.check("the wrong method really does differ, so the guard is meaningful",
            gap > 0.5, f"largest gap {gap:.2f} points")
    src = (HERE / "queries.py").read_text(encoding="utf-8")
    c.check("no AVG(...residents.../...beds...) anywhere in queries.py",
            not re.search(r"AVG\s*\(\s*avg_residents_per_day\s*/", src))

    # -----------------------------------------------------------------
    print("\n5. The brief's minimums")
    # -----------------------------------------------------------------
    # ชุดตรวจนี้เคยอ่านแต่ app.py ทำให้ไม่เห็นว่าแท็บข้อเสนอแนะของ app_mpl.py
    # เป็นข้อความที่พิมพ์ตัวเลขฝังไว้ ไม่ได้คำนวณจากตัวกรอง — 56/56 จึงผ่านทั้งที่
    # แดชบอร์ดชุดที่ใช้ถ่ายภาพลงรายงานมีปัญหาอยู่
    app_mpl = (HERE / "app_mpl.py").read_text(encoding="utf-8")
    c.check("app_mpl.py สร้าง insight จากตัวกรอง ไม่ได้พิมพ์ฝังไว้",
            "insights.build(" in app_mpl or "load_insights(" in app_mpl,
            "เรียก insights.build ผ่าน load_insights()")

    app = (HERE / "app.py").read_text(encoding="utf-8")
    n_metrics = len(re.findall(r"\.metric\(", app))
    n_charts = len(re.findall(r"st\.plotly_chart\(", app))
    n_filters = len(re.findall(r"st\.sidebar\.(multiselect|toggle|selectbox|slider)\(", app))
    items = insights.build(con, all_f)

    c.check("summary measures >= 3", n_metrics >= 3, f"{n_metrics} st.metric calls")
    c.check("charts >= 5", n_charts >= 5, f"{n_charts} charts")
    c.check("time-series chart >= 1", "BQ5" in app and "penalty_year" in app,
            "BQ5 snapshot trend + BQ7 fines by year")
    c.check("comparison chart >= 1", "by_ownership" in app and "by_chain_band" in app,
            "BQ2 ownership + BQ8 chain size")
    c.check("interactive controls >= 2", n_filters >= 2, f"{n_filters} controls")
    c.check("insights >= 5", len(items) >= 5, f"{len(items)} generated")

    blocked = [i for i in items if i.strength == "blocked"]
    c.note("insights that honestly report they cannot answer",
           f"{len(blocked)} ({', '.join(i.bq for i in blocked) or 'none'})")
    c.check("every insight names a business question",
            all(i.bq.startswith("BQ") or i.bq == "—" for i in items))
    c.check("every insight carries a recommendation",
            all(i.recommendation.strip() for i in items))

    # -----------------------------------------------------------------
    print("\n6. Business questions covered")
    # -----------------------------------------------------------------
    covered = {i.bq for i in items if i.bq.startswith("BQ")}
    for bq in [f"BQ{n}" for n in range(1, 9)]:
        c.check(f"{bq} has a tab and an insight", f'"{bq}' in app and bq in covered)

    print("\n" + "=" * 62)
    print(f"  {c.passed} passed / {c.failed} failed")
    con.close()
    return 1 if c.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
