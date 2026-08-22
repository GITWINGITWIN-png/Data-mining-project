"""Render every report figure from the warehouse.

    python make_figures.py

One entry point for all 17 PNGs so the images in the report cannot drift from
the numbers in `06_Report/BQ_answers.md` — both come from `queries.py`, and
re-running this after any query change refreshes the whole set at once.
"""

import matplotlib

matplotlib.use("Agg")

import charts  # noqa: E402
import queries_mpl as queries  # noqa: E402

DPI = 150


def save(figure, name: str) -> None:
    """Write each figure twice: PNG to look at, PDF to typeset.

    The PNG is for quick viewing and for anything that needs a bitmap. The PDF
    is vector, and it is the one the report should `\includegraphics` —
    XeLaTeX scales it without softening, where a 150-dpi PNG blown up to column
    width is exactly the fuzzy-chart problem this project already hit once on
    the dashboard.
    """
    figure.savefig(f"{name}.png", dpi=DPI, bbox_inches="tight")
    figure.savefig(f"{name}.pdf", bbox_inches="tight")
    matplotlib.pyplot.close(figure)
    print("wrote", f"{name}.png + .pdf")


def main() -> None:
    con = queries.connect()

    # BQ1 — market
    ranked, context = queries.bq1_state_market(con)
    scored = queries.bq1_opportunity_score(ranked)
    save(charts.market_scatter(scored, context), "bq1_market_scatter")
    save(charts.beds_per_1k_bar(scored, context), "bq1_c5_beds_per_1k")
    save(charts.score_components_bar(scored), "bq1_score_components")

    # BQ2 — ownership
    frame, context = queries.segment_compare(con, "ownership_group")
    rows = queries.segment_facilities(con, "ownership_group")
    save(charts.segment_small_multiples(frame, context), "bq2_segments")
    save(charts.rating_mix_by_segment(rows, context), "bq2_rating_mix")
    save(charts.rating_box_by_segment(rows, context, column="turnover"),
         "bq2_turnover_box")

    # BQ3 — staffing
    frame, context = queries.bq3_staffing_gradient(con)
    save(charts.staffing_gradient(frame, context), "bq3_staffing_gradient")
    save(charts.staffing_controlled(queries.bq3_controlled(con)), "bq3_controlled")

    # BQ4 — retention
    frame, context = queries.bq4_turnover_gradient(con)
    save(charts.turnover_gradient(frame, context), "bq4_turnover_gradient")
    controlled = queries.bq4_controlled(con)
    save(charts.turnover_staffing_heatmap(controlled, "avg_rating"),
         "bq4_controlled_heatmap")
    save(charts.turnover_staffing_heatmap(controlled, "occupancy"),
         "bq4_controlled_occupancy")

    # BQ5 — trend
    pairs, context = queries.bq5_matched_cohort(con)
    save(charts.matched_cohort_slopes(context), "bq5_cohort_slopes")
    save(charts.rating_migration(pairs, context), "bq5_rating_migration")

    # BQ6 — watchlist validation
    frame, context = queries.bq6_backtest(con)
    save(charts.risk_backtest(frame, context), "bq6_risk_backtest")

    # BQ7 — enforcement
    frame, context = queries.bq7_fines_timeline(con)
    save(charts.fines_timeline(frame, context), "bq7_fines_timeline")
    frame, context = queries.bq7_state_enforcement(con)
    save(charts.enforcement_by_state(frame, context), "bq7_state_enforcement")

    # BQ8 — chain scale
    frame, context = queries.segment_compare(con, "chain_size_band")
    rows = queries.segment_facilities(con, "chain_size_band")
    cross = queries.segment_cross(con)
    save(charts.segment_small_multiples(frame, context), "bq8_segments")
    save(charts.rating_mix_by_segment(rows, context), "bq8_rating_mix")
    for column, name in [("nurse_hprd", "bq8_controlled_nurse"),
                         ("avg_rating", "bq8_controlled_rating"),
                         ("turnover_pct", "bq8_controlled_turnover")]:
        save(charts.controlled_lines(cross, column, context), name)


if __name__ == "__main__":
    main()
