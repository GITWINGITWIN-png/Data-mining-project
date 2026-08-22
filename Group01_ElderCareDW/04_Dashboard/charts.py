"""Chart layer — shared by the notebook and the Streamlit app.

Every function takes a DataFrame and returns a matplotlib Figure. No querying
happens here and no numbers are computed here; that is `queries.py`. Keeping the
split means a chart cannot quietly disagree with the table beside it.

Design decisions worth knowing, because they are deliberate and easy to undo by
accident:

  * **Bars are one colour, with emphasis.** Bar length already encodes the
    magnitude, so shading each bar darker-where-bigger would spend the colour
    channel on information the chart already carries. The only colour distinction
    is "in the shortlist" versus "not", which is identity, not magnitude.
  * **The scatter colours a third measure**, not a repeat of an axis. x is supply,
    y is demand, colour is the competition — so all three measures BQ1 asks about
    are on one plot. It is a single-hue ordered ramp, validated for ordinal use.
  * **No dual axes anywhere.** Two measures of different scale get two charts.
  * Colours come from a validated palette; the ordinal ramp passed the lightness,
    step-gap, light-end contrast and single-hue checks against this surface.
"""

from __future__ import annotations

import io
import re

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

# ---------------------------------------------------------------------
#  Palette. Light-only on purpose: these render into a notebook and a PNG
#  for the report, both of which sit on white.
# ---------------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

ACCENT = "#2a78d6"        # categorical slot 1 — the shortlisted states
NEUTRAL = "#898781"       # everything else, deliberately recessive

# Single-hue ordered ramp, light -> dark. Validated for ordinal use on this
# surface: monotone lightness, all adjacent gaps >= 0.06, light end 2.06:1,
# hue spread 3 degrees.
RATING_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]


def apply_style() -> None:
    """House style: recessive chrome, no top/right spines, thin marks."""
    mpl.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.size": 10,
        "text.color": INK,
        "axes.labelcolor": INK_SECONDARY,
        "axes.edgecolor": BASELINE,
        "axes.linewidth": 0.8,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "xtick.labelcolor": INK_SECONDARY,
        "ytick.labelcolor": INK_SECONDARY,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "legend.frameon": False,
        "figure.dpi": 110,
    })


def to_svg(figure) -> str:
    """Serialise a figure as an SVG that scales to its container.

    `st.pyplot()` rasterises to PNG at the figure's DPI and Streamlit then
    stretches that bitmap to the column width, so every chart arrives as an
    enlarged image — soft edges, fuzzy text. Vector output has no resolution to
    outrun: the browser rasterises at whatever size it ends up, and at whatever
    zoom the reader picks.

    Two details make it behave inside a Streamlit column:

      * matplotlib writes fixed `width`/`height` in points on the `<svg>` tag,
        which pins the drawing to its figsize and makes the column scroll
        sideways on a narrow screen. Dropping both and keeping `viewBox` lets
        CSS size it while the aspect ratio holds.
      * text is emitted as outlines (matplotlib's default `svg.fonttype`), so
        the labels look identical on a machine that does not have the fonts —
        which matters here because the axis labels are set in a font the
        grader's laptop may not carry.
    """
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", bbox_inches="tight")
    svg = buffer.getvalue()
    svg = svg[svg.index("<svg"):]
    head_end = svg.index(">")
    head = re.sub(r'\s(width|height)="[^"]*"', "", svg[:head_end])
    return (
        '<div style="width:100%">'
        + head
        + ' style="width:100%;height:auto;display:block"'
        + svg[head_end:]
        + "</div>"
    )


def _titles(ax, title: str, subtitle: str) -> None:
    """Title above subtitle, spaced in points so it holds at any figure height.

    Placing the subtitle with axes-fraction coordinates is what makes it collide
    with the title on a tall figure and float away on a short one: the same
    fraction is a different number of points each time. Offsetting from the axes
    corner in points instead keeps the gap fixed.
    """
    ax.set_title(title, loc="left", pad=24)
    ax.annotate(
        subtitle,
        xy=(0, 1), xycoords="axes fraction",
        xytext=(0, 6), textcoords="offset points",
        color=INK_MUTED, fontsize=9, va="bottom", ha="left",
    )


def _place_labels(ax, points, fontsize=9):
    """Direct-label a few marks, nudging each one until it is not on top of another.

    Collisions are resolved in axes-fraction space, so the result does not depend
    on a renderer existing yet. Five labels on fifty marks is the point — a label
    on every state would be unreadable.
    """
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    offsets = [(0, 0.030), (0, -0.038), (0.045, 0.004), (-0.045, 0.004), (0, 0.058)]
    placed: list[tuple[float, float]] = []

    for x, y, text in points:
        fx = (x - x0) / (x1 - x0)
        fy = (y - y0) / (y1 - y0)
        for dx, dy in offsets:
            candidate = (fx + dx, fy + dy)
            if all(
                abs(candidate[0] - px) > 0.035 or abs(candidate[1] - py) > 0.028
                for px, py in placed
            ):
                break
        placed.append(candidate)
        # A hairline back to the mark. In a crowded region a floating label is
        # ambiguous — it reads as belonging to whichever dot happens to be nearest,
        # which in this chart put "SC" beside California.
        ax.annotate(
            text,
            xy=(x, y),
            xytext=(x0 + candidate[0] * (x1 - x0), y0 + candidate[1] * (y1 - y0)),
            ha="center", va="center", fontsize=fontsize, fontweight="bold",
            color=INK, zorder=5,
            arrowprops=dict(arrowstyle="-", color=INK_MUTED, linewidth=0.7,
                            shrinkA=2, shrinkB=7),
        )


# Three categorical slots — validated all-pairs for scatter/line use in both
# modes. Three is the cap for those forms; a fourth series folds into "Other".
GROUP_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]

# Four ordered steps for chain size bands. Validated as an ordinal ramp:
# monotone lightness, adjacent gaps >= 0.06, light end 2.06:1 on this surface.
BAND_RAMP = ["#86b6ef", "#3987e5", "#256abf", "#104281"]

#: (column, axis label, formatter, "higher is better"?) for the facet grids.
MEASURES = {
    "avg_rating": ("average star rating", "{:.2f}", True),
    "nurse_hprd": ("nurse hours per resident per day", "{:.2f}", True),
    "rn_share": ("registered nurse share of hours", "{:.1%}", True),
    "turnover_pct": ("nursing staff turnover %", "{:.1f}", False),
    "fines_per_bed": ("fines per bed, 3 years (USD)", "${:,.0f}", False),
    "occupancy": ("occupancy", "{:.1%}", True),
}


def segment_small_multiples(
    frame: pd.DataFrame,
    context: dict,
    measures: list[str] | None = None,
    palette: list[str] | None = None,
):
    """One small bar chart per measure, sharing the segment axis.

    Why not a single grouped bar with everything on it: star ratings run 0-5,
    turnover runs 0-100 and fines per bed run into the hundreds of dollars.
    Putting them on one axis needs a second scale, and a two-scale chart invents
    whatever relationship the scales happen to imply. One panel per measure keeps
    every comparison on a single honest axis.

    Each panel is annotated with the value, so the segment colours are decoration
    rather than the only way to read the number — which is also the relief the
    palette's contrast warning requires.
    """
    apply_style()
    measures = measures or list(MEASURES)
    palette = palette or GROUP_COLORS
    labels = [str(s) for s in frame["segment"]]
    colors = [palette[i % len(palette)] for i in range(len(labels))]

    ncols = 3
    nrows = -(-len(measures) // ncols)
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(4.1 * ncols, 3.1 * nrows + 0.6), squeeze=False
    )

    for index, column in enumerate(measures):
        ax = axes[index // ncols][index % ncols]
        title, fmt, higher_better = MEASURES[column]
        values = frame[column].astype("float64")
        ax.bar(labels, values, color=colors, width=0.62)

        headroom = values.max() * 1.18 if values.max() > 0 else 1
        ax.set_ylim(0, headroom)
        for x, value in enumerate(values):
            ax.annotate(fmt.format(value), xy=(x, value), xytext=(0, 4),
                        textcoords="offset points", ha="center",
                        fontsize=9, fontweight="bold", color=INK)

        arrow = "higher is better" if higher_better else "lower is better"
        ax.set_title(f"{title}\n", loc="left", fontsize=10.5, pad=6)
        ax.annotate(arrow, xy=(0, 1), xycoords="axes fraction",
                    xytext=(0, 4), textcoords="offset points",
                    color=INK_MUTED, fontsize=8.5, va="bottom")
        ax.yaxis.grid(True)
        ax.set_axisbelow(True)
        ax.set_yticks([])
        ax.tick_params(axis="x", length=0, labelsize=9)
        ax.spines["left"].set_visible(False)
        for label in ax.get_xticklabels():
            label.set_rotation(18)
            label.set_ha("right")

    for index in range(len(measures), nrows * ncols):
        axes[index // ncols][index % ncols].set_visible(False)

    # Figure-level titles positioned from the top in inches, then converted to a
    # fraction. Fractions alone collide on a tall figure and drift apart on a
    # short one, which is the same trap `_titles` avoids for a single axes.
    height = fig.get_figheight()
    fig.text(
        0.006, 1 - 0.30 / height,
        f"Operator segments compared — by {context['segment_label']}",
        ha="left", va="top", fontsize=13, fontweight="bold", color=INK,
    )
    fig.text(
        0.006, 1 - 0.56 / height,
        f"period {context['period']} · {context['facilities']:,} facilities · "
        f"fines window {context['fines_window']}",
        ha="left", va="top", color=INK_MUTED, fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.78 / height))
    return fig


#: Star ratings are whole numbers 1-5, so the ordinal ramp has one step per star.
RATING_LEVELS = [1, 2, 3, 4, 5]


def rating_mix_by_segment(
    facilities: pd.DataFrame,
    context: dict,
    order: list[str] | None = None,
):
    """Share of facilities at each star level, per segment.

    A box plot is the wrong instrument here and it is worth saying why: the star
    rating takes five whole values, so every quartile lands on an integer and each
    segment reports a median of exactly 3.00. The boxes come out nearly identical
    and the chart looks like there is no difference at all, when in fact the
    *mix* differs sharply — one segment can hold twice the share of one-star homes
    with the same median.

    A hundred-percent stacked bar shows that mix, and because the five levels are
    ordered it can use the ordinal ramp rather than five unrelated hues.
    """
    apply_style()
    order = order or list(facilities["segment"].dropna().unique())

    rows, labels, medians = [], [], []
    for segment in order:
        ratings = facilities.loc[facilities["segment"] == segment, "rating"].dropna()
        if ratings.empty:
            continue
        counts = ratings.round().astype(int).value_counts()
        total = counts.sum()
        rows.append([counts.get(level, 0) / total for level in RATING_LEVELS])
        labels.append(f"{segment}\nn={total:,}")
        medians.append(ratings.median())

    fig, ax = plt.subplots(figsize=(10, 0.72 * len(rows) + 2.7))
    left = [0.0] * len(rows)
    for index, level in enumerate(RATING_LEVELS):
        widths = [row[index] for row in rows]
        ax.barh(labels, widths, left=left, height=0.62,
                color=RATING_RAMP[index], edgecolor=SURFACE, linewidth=2,
                label=f"{level}★")
        for y, (width, start) in enumerate(zip(widths, left)):
            # Only label a segment wide enough to hold the text
            if width >= 0.07:
                ax.annotate(
                    f"{width:.0%}", xy=(start + width / 2, y),
                    ha="center", va="center", fontsize=9, fontweight="bold",
                    # the ramp darkens from step 2 onward, so ink flips there
                    color=SURFACE if index >= 2 else INK,
                )
        left = [start + width for start, width in zip(left, widths)]

    ax.set_xlim(0, 1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("share of facilities")
    ax.invert_yaxis()
    # Read off the data, never asserted: the medians differ between the ownership
    # groups but are identical across the chain bands, so a hard-coded sentence
    # about them is wrong in one of the two charts this function draws.
    span = (
        f"medians all {medians[0]:.0f}★ — the difference is only in the tails"
        if len(set(medians)) == 1
        else "medians " + ", ".join(f"{m:.0f}★" for m in medians)
    )
    _titles(
        ax,
        "Star rating mix, not just the average",
        f"period {context['period']} · {span}",
    )
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, labelsize=9)
    ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.16), ncols=5, fontsize=9,
        title="CMS overall rating", title_fontsize=9,
    )
    fig.tight_layout()
    return fig


def rating_box_by_segment(
    facilities: pd.DataFrame,
    context: dict,
    column: str = "rating",
    palette: list[str] | None = None,
    order: list[str] | None = None,
):
    """Distribution of one measure per segment, not just its average.

    A gap between two averages says nothing about whether the two groups can be
    told apart. Boxes show the overlap: if the middle halves sit on top of each
    other, "segment A is better" is a statement about the average and not about
    any particular facility.
    """
    apply_style()
    palette = palette or GROUP_COLORS
    order = order or [
        s for s in facilities["segment"].dropna().unique()
    ]
    data, labels, used = [], [], []
    for index, segment in enumerate(order):
        values = facilities.loc[facilities["segment"] == segment, column].dropna()
        if values.empty:
            continue
        data.append(values.to_numpy())
        labels.append(f"{segment}\nn={len(values):,}")
        used.append(palette[index % len(palette)])

    fig, ax = plt.subplots(figsize=(1.9 * max(len(data), 1) + 3.4, 5.4))

    # Every group can come back empty — the turnover column does not exist in
    # the 2019 era at all, so asking for a turnover box on that period leaves
    # nothing to draw. matplotlib's own failure here is
    # "Dimensions of labels and X must be compatible", which says nothing about
    # the cause; say what actually happened instead.
    if not data:
        ax.set_axis_off()
        ax.text(0.5, 0.5,
                f"no {MEASURES.get(column, (column,))[0]} reported\n"
                f"in period {context['period']}",
                ha="center", va="center", fontsize=11, color=INK_MUTED,
                transform=ax.transAxes)
        return fig

    box = ax.boxplot(
        data, tick_labels=labels, patch_artist=True, widths=0.55,
        showfliers=False, medianprops=dict(color=SURFACE, linewidth=2),
        whiskerprops=dict(color=BASELINE, linewidth=1.2),
        capprops=dict(color=BASELINE, linewidth=1.2),
    )
    for patch, color in zip(box["boxes"], used):
        patch.set_facecolor(color)
        patch.set_edgecolor(SURFACE)
        patch.set_linewidth(2)

    for index, values in enumerate(data, start=1):
        ax.annotate(
            f"median {pd.Series(values).median():.2f}",
            xy=(index, pd.Series(values).median()), xytext=(0, 10),
            textcoords="offset points", ha="center", fontsize=9,
            fontweight="bold", color=INK,
        )

    label = MEASURES.get(column, (column, "{:.2f}", True))[0]
    ax.set_ylabel(label)
    _titles(
        ax,
        f"How much do the segments actually overlap?",
        f"period {context['period']} · box = middle half · line = median "
        f"· outliers hidden for legibility",
    )
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", length=0, labelsize=9)
    fig.tight_layout()
    return fig


def controlled_lines(
    cross: pd.DataFrame,
    column: str,
    context: dict,
    min_facilities: int = 100,
):
    """The same measure across chain size, drawn once per ownership group.

    This is the chart that separates the two explanations. Chains are mostly
    for-profit, so a single line across chain size cannot tell "scale does this"
    apart from "for-profit does this". Three lines, one per ownership group, can:
    if each line slopes the same way on its own, scale is doing something that
    ownership does not explain.

    Cells built from very few facilities are drawn hollow — with ninety homes a
    single outlier moves the point, and a solid marker would overstate it.
    """
    apply_style()
    title, _fmt, higher_better = MEASURES.get(column, (column, "{:.2f}", True))
    bands = list(cross["chain_size"].cat.categories)
    fig, ax = plt.subplots(figsize=(8.6, 5.6))

    for index, (owner, part) in enumerate(cross.groupby("ownership", observed=True)):
        part = part.sort_values("chain_size")
        xs = [bands.index(b) for b in part["chain_size"]]
        ys = part[column].astype("float64")
        color = GROUP_COLORS[index % len(GROUP_COLORS)]
        ax.plot(xs, ys, color=color, linewidth=2, zorder=3, label=str(owner))
        for x, y, n in zip(xs, ys, part["facilities"]):
            thin = n < min_facilities
            ax.scatter(
                [x], [y], s=64, zorder=4, color=SURFACE if thin else color,
                edgecolor=color, linewidth=2,
            )
        ax.annotate(
            str(owner), xy=(xs[-1], ys.iloc[-1]), xytext=(9, 0),
            textcoords="offset points", va="center", fontsize=9.5,
            fontweight="bold", color=color,
        )

    ax.set_xticks(range(len(bands)))
    ax.set_xticklabels([b.replace(" chain", "\nchain") for b in bands], fontsize=9)
    ax.set_ylabel(title)
    _titles(
        ax,
        f"{title} across chain size, within each ownership group",
        f"period {context['period']} · {'higher' if higher_better else 'lower'} is better "
        f"· hollow markers are cells with fewer than {min_facilities} facilities",
    )
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.margins(x=0.16)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    return fig


def beds_per_1k_bar(ranked: pd.DataFrame, context: dict, shortlist: int = 5):
    """C5 — beds per 1,000 people aged 65+, by state.

    Sorted so the eye reads the ranking off the axis rather than hunting for it.
    Short bars are the interesting end for an investor: fewer beds per elderly
    person means the state is underserved, so the chart is sorted ascending and
    the shortlist sits at the top.
    """
    apply_style()
    # Descending, because barh draws the first row at the bottom: this puts the
    # fewest beds per elderly person — the underserved end an investor cares
    # about — at the top of the chart where the eye starts
    frame = (
        ranked.dropna(subset=["beds_per_1k_65plus"])
        .sort_values("beds_per_1k_65plus", ascending=False)
        .reset_index(drop=True)
    )
    top = set(
        ranked.dropna(subset=["opportunity_score"])
        .nlargest(shortlist, "opportunity_score")["state_code"]
    )
    colors = [ACCENT if s in top else NEUTRAL for s in frame["state_code"]]

    fig, ax = plt.subplots(figsize=(7.2, 11))
    ax.barh(frame["state_code"], frame["beds_per_1k_65plus"],
            color=colors, height=0.72)

    national = context["national_beds_per_1k"]
    ax.axvline(national, color=INK_SECONDARY, linestyle="--", linewidth=1.2, zorder=3)
    # Anchored to the top of the axes in fraction coordinates, not to a row index:
    # a row index past the last bar lands outside the y-limits and gets clipped away
    ax.annotate(
        f"national {national:.1f}",
        xy=(national, 1.0), xycoords=("data", "axes fraction"),
        # to the left of the line: the right of it is where the legend sits
        xytext=(-5, -12), textcoords="offset points",
        color=INK_SECONDARY, fontsize=9, va="top", ha="right",
    )

    # Direct labels only on the shortlist — a number on all 51 bars is noise
    for y, (state, value) in enumerate(
        zip(frame["state_code"], frame["beds_per_1k_65plus"])
    ):
        if state in top:
            ax.annotate(f"{value:.1f}", xy=(value, y), xytext=(4, 0),
                        textcoords="offset points", va="center",
                        fontsize=9, color=ACCENT, fontweight="bold")

    ax.set_xlabel("beds per 1,000 residents aged 65+")
    ax.set_ylabel("")
    _titles(
        ax,
        "Supply against demand, by state",
        f"period {context['period']} · population {context['population_year']} "
        f"· fewer beds per elderly person is nearer the top",
    )
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, labelsize=8)
    ax.margins(y=0.005)
    # Upper right: sorting descending puts the long bars at the bottom, so the
    # bottom-right corner is now the one place the legend cannot go
    ax.legend(
        handles=[
            Patch(facecolor=ACCENT, label=f"top {shortlist} by opportunity score"),
            Patch(facecolor=NEUTRAL, label="other states"),
        ],
        loc="upper right", fontsize=9,
    )
    fig.tight_layout()
    return fig


def market_scatter(ranked: pd.DataFrame, context: dict, shortlist: int = 5):
    """Supply against demand, coloured by the quality of the competition.

    Reading it: the attractive corner is **left and high** — few beds per elderly
    person, and the beds that exist are already full. Colour adds the third
    measure BQ1 asks for, so a pale dot in that corner is an underserved state
    where the incumbents are also weak.
    """
    apply_style()
    frame = ranked.dropna(subset=["beds_per_1k_65plus", "occupancy",
                                  "avg_overall_rating"]).copy()

    # Five ordered bins rather than a continuous scale: the ratings only span
    # about 2.0 to 3.7, and discrete steps are far easier to read off a legend
    edges = [1.0, 2.75, 3.0, 3.15, 3.3, 5.0]
    labels = ["< 2.75", "2.75–3.00", "3.00–3.15", "3.15–3.30", "> 3.30"]
    frame["rating_bin"] = pd.cut(frame["avg_overall_rating"], bins=edges,
                                 labels=labels, include_lowest=True)

    fig, ax = plt.subplots(figsize=(9, 6.4))
    for label, color in zip(labels, RATING_RAMP):
        part = frame[frame["rating_bin"] == label]
        if part.empty:
            continue
        ax.scatter(
            part["beds_per_1k_65plus"], part["occupancy"] * 100,
            s=part["facilities"].clip(upper=900) / 3 + 40,
            color=color, edgecolor=SURFACE, linewidth=2, label=label, zorder=3,
        )

    med_x = frame["beds_per_1k_65plus"].median()
    med_y = frame["occupancy"].median() * 100
    ax.axvline(med_x, color=BASELINE, linewidth=1, zorder=1)
    ax.axhline(med_y, color=BASELINE, linewidth=1, zorder=1)

    ax.set_xlabel("beds per 1,000 residents aged 65+  (fewer = underserved)")
    ax.set_ylabel("occupancy %  (higher = demand is real)")
    _titles(
        ax,
        "Where the room to grow is",
        f"period {context['period']} · the attractive corner is upper left "
        f"· marker size = number of facilities · lines are the medians",
    )
    ax.grid(True)
    ax.set_axisbelow(True)

    # Labels last, once the limits are final, so the collision maths is right
    top = set(
        ranked.dropna(subset=["opportunity_score"])
        .nlargest(shortlist, "opportunity_score")["state_code"]
    )
    shortlisted = frame[frame["state_code"].isin(top)]
    _place_labels(
        ax,
        list(zip(shortlisted["beds_per_1k_65plus"],
                 shortlisted["occupancy"] * 100,
                 shortlisted["state_code"])),
    )

    # Below the plot: five swatches will not fit in a corner without landing on
    # the data, and moving data out of the way to make room is not an option
    legend = ax.legend(
        title="competitor avg star rating (lower is weaker competition)",
        fontsize=9, title_fontsize=9, ncols=5,
        loc="upper center", bbox_to_anchor=(0.5, -0.13),
        columnspacing=1.2, handletextpad=0.4,
    )
    legend.get_title().set_color(INK_SECONDARY)
    # Marker size carries facility count in the plot, so the legend must not
    # imply a size meaning of its own — one size for every swatch
    for handle in legend.legend_handles:
        handle.set_sizes([70])
    fig.tight_layout()
    return fig


def score_components_bar(ranked: pd.DataFrame, top_n: int = 10):
    """The shortlist, broken into the three things that put each state there.

    A single composite score hides its own reasoning. Splitting it back into the
    three percentiles shows *why* a state ranks well, and lets a reader who
    weights quality above supply disagree with the ordering on the spot.
    """
    apply_style()
    frame = (
        ranked.dropna(subset=["opportunity_score"])
        .nlargest(top_n, "opportunity_score")
        .iloc[::-1]
    )
    parts = [
        ("pct_supply", "underserved", RATING_RAMP[0]),
        ("pct_demand", "already full", RATING_RAMP[2]),
        ("pct_contest", "weak competition", RATING_RAMP[4]),
    ]

    fig, ax = plt.subplots(figsize=(9, 0.52 * len(frame) + 2.2))
    left = pd.Series(0.0, index=frame.index)
    for column, label, color in parts:
        ax.barh(frame["state_code"], frame[column] / 3, left=left,
                color=color, height=0.7, label=label, edgecolor=SURFACE, linewidth=2)
        left += frame[column] / 3

    for y, (state, score) in enumerate(
        zip(frame["state_code"], frame["opportunity_score"])
    ):
        ax.annotate(f"{score:.2f}", xy=(score, y), xytext=(6, 0),
                    textcoords="offset points", va="center",
                    fontsize=9, fontweight="bold", color=INK)

    ax.set_xlabel("opportunity score  (mean of three percentiles, 0–1)")
    _titles(
        ax,
        f"What puts the top {top_n} where they are",
        "equal weights are an assumption, not a finding — "
        "the three parts are shown so they can be reweighted",
    )
    ax.set_xlim(0, 1.0)
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)
    ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.12), ncols=3, fontsize=9,
    )
    fig.tight_layout()
    return fig


# =====================================================================
#  BQ3-BQ7
#
#  One rule runs through all of these: when two measures answer the same
#  question at different scales, they get two stacked panels sharing an
#  x-axis rather than one panel with two y-axes. A twin axis lets whoever
#  set the limits decide where the lines appear to cross, and a reader has
#  no way to see that it happened.
# =====================================================================

WARN = "#c9422c"        # the regulatory floor, the danger band — used sparingly
GOOD = "#1baf7a"


def staffing_gradient(frame: pd.DataFrame, context: dict):
    """BQ3 — what more nursing hours actually buy.

    The top panel is the health-inspection star and the bottom is fines per
    bed, and the choice of those two is the argument. CMS builds the *staffing*
    star out of these very hours, so plotting the overall star against them
    would be partly circular. Health inspection is scored by surveyors on site
    with staffing hours playing no part in it, and a fine is money a regulator
    demanded — neither can be an artefact of the x-axis.

    The dashed line is the CMS minimum staffing floor. It is drawn from the
    regulation, not fitted to this data, which is what makes it quotable.
    """
    apply_style()
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(9.2, 7.4), sharex=True,
        gridspec_kw={"height_ratios": [1, 1], "hspace": 0.18},
    )
    xs = range(len(frame))
    floor_at = next(
        (i for i, b in enumerate(frame["band"]) if str(b).startswith("3.48")), None
    )

    top.plot(xs, frame["health_inspection_rating"], color=ACCENT, linewidth=2.2, zorder=3)
    top.scatter(xs, frame["health_inspection_rating"], s=52, color=ACCENT, zorder=4)
    top.set_ylabel("health inspection star")
    top.yaxis.grid(True); top.set_axisbelow(True)
    _titles(
        top,
        "More nursing hours, better inspections and smaller fines",
        f"period {context['period']} · {context['measured_facilities']:,} facilities with "
        f"reported staffing · the inspection star is scored on site and owes nothing to these hours",
    )

    bars = bottom.bar(xs, frame["fines_per_bed"], color=NEUTRAL, width=0.68, zorder=3)
    for i, bar in enumerate(bars):
        if floor_at is not None and i < floor_at:
            bar.set_color(WARN)
    bottom.set_ylabel("fines per bed, 3 years (USD)")
    bottom.yaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("${x:,.0f}"))
    bottom.yaxis.grid(True); bottom.set_axisbelow(True)
    bottom.set_xticks(list(xs))
    bottom.set_xticklabels(frame["band"], rotation=45, ha="right", fontsize=9)
    bottom.set_xlabel("total nurse hours per resident per day")

    if floor_at is not None:
        for ax in (top, bottom):
            ax.axvline(floor_at - 0.5, color=WARN, linestyle=(0, (4, 3)),
                       linewidth=1.3, zorder=2)
        top.annotate(
            f"CMS minimum staffing rule\n{context['cms_floor']} hours",
            xy=(floor_at - 0.5, top.get_ylim()[0]), xytext=(6, 12),
            textcoords="offset points", fontsize=9, color=WARN,
            fontweight="bold", ha="left", va="bottom",
        )
        bottom.annotate(
            f"{context['below_floor_pct']:.0f}% of facilities\nsit below the floor",
            xy=(floor_at - 0.5, bottom.get_ylim()[1]), xytext=(-8, -6),
            textcoords="offset points", fontsize=9, color=WARN,
            va="top", ha="right", fontweight="bold",
        )
    fig.tight_layout()
    return fig


def turnover_gradient(frame: pd.DataFrame, context: dict):
    """BQ4 — two thresholds, because the two curves have different shapes.

    Drawing quality and occupancy on one pair of axes would invite the reader
    to find one number where "the damage starts". There isn't one. The star
    rating falls from the very first band with no safe plateau, while occupancy
    holds flat and then breaks — so the answer to a nursing director and the
    answer to a CFO are different numbers, and the chart has to show that
    rather than average it away.
    """
    apply_style()
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(9.2, 7.4), sharex=True, gridspec_kw={"hspace": 0.18}
    )
    xs = list(range(len(frame)))

    top.plot(xs, frame["avg_rating"], color=ACCENT, linewidth=2.2, zorder=3)
    top.scatter(xs, frame["avg_rating"], s=52, color=ACCENT, zorder=4)
    top.set_ylabel("average star rating")
    top.yaxis.grid(True); top.set_axisbelow(True)
    _titles(
        top,
        "Quality erodes from the first band; occupancy holds, then breaks",
        f"period {context['period']} · {context['measured_facilities']:,} facilities reporting "
        f"turnover · national average {context['national_turnover_weighted']:.1f}%",
    )

    bottom.plot(xs, frame["occupancy"] * 100, color=GOOD, linewidth=2.2, zorder=3)
    bottom.scatter(xs, frame["occupancy"] * 100, s=52, color=GOOD, zorder=4)
    bottom.set_ylabel("occupancy %")
    bottom.yaxis.grid(True); bottom.set_axisbelow(True)
    bottom.set_xticks(xs)
    bottom.set_xticklabels(frame["band"], rotation=45, ha="right", fontsize=9)
    bottom.set_xlabel("total nursing staff turnover")

    # The knee: the first band that gives up more than a full point of
    # occupancy against the one before it. A looser rule catches the 0.3-point
    # wobble in the low bands, which is noise on this many facilities and would
    # put the line in the wrong place; a full point is a break in the curve.
    knee = next(
        (i for i, d in enumerate(frame["d_occupancy"].fillna(0) * 100) if d < -1.0),
        None,
    )
    if knee is not None:
        for ax in (top, bottom):
            ax.axvline(knee - 0.5, color=WARN, linestyle=(0, (4, 3)), linewidth=1.3, zorder=2)
        bottom.annotate(
            f"occupancy starts falling here ({frame['band'].iloc[knee]})",
            xy=(knee - 0.5, bottom.get_ylim()[0]), xytext=(8, 10),
            textcoords="offset points", fontsize=9, color=WARN,
            fontweight="bold", va="bottom", ha="left",
        )
    fig.tight_layout()
    return fig


def turnover_staffing_heatmap(frame: pd.DataFrame, value: str = "avg_rating"):
    """BQ4's control — is turnover its own problem, or a symptom of thin staffing?

    Rows are staffing quartiles, columns are turnover bands. If turnover were
    only a proxy for understaffing, the colour would change down the rows and
    not across them. It changes across them in every row, which is the answer.
    """
    apply_style()
    label, fmt, higher_better = MEASURES.get(value, (value, "{:.2f}", True))
    grid = frame.pivot(index="hprd_quartile", columns="turnover_band", values=value)
    counts = frame.pivot(index="hprd_quartile", columns="turnover_band", values="facilities")

    fig, ax = plt.subplots(figsize=(8.0, 4.8), layout="constrained")
    ramp = mpl.colors.LinearSegmentedColormap.from_list(
        "ramp", RATING_RAMP if higher_better else RATING_RAMP[::-1]
    )
    image = ax.imshow(grid.values.astype("float64"), cmap=ramp, aspect="auto")

    # Text colour is chosen from the luminance of the fill each cell actually
    # got, not from where its value sits between the extremes. Those are not
    # the same thing: a mid-range value lands on a mid-tone blue where neither
    # the light nor the dark ink has enough contrast, and the small "n=" labels
    # are the first thing to disappear. Asking the colormap what colour it
    # produced and measuring it puts every label on the right side of the line.
    values = grid.values.astype("float64")
    low, high = values.min(), values.max()
    for r in range(grid.shape[0]):
        for c in range(grid.shape[1]):
            v = values[r, c]
            if pd.isna(v):
                continue
            red, green, blue, _ = ramp((v - low) / (high - low) if high > low else 0.5)
            luminance = 0.299 * red + 0.587 * green + 0.114 * blue
            ink = SURFACE if luminance < 0.55 else INK
            ax.text(c, r - 0.10, fmt.format(v), ha="center", va="center", fontsize=11,
                    fontweight="bold", color=ink)
            ax.text(c, r + 0.24, f"n={int(counts.values[r, c]):,}", ha="center",
                    va="center", fontsize=7.5, color=ink, alpha=0.85)

    ax.set_xticks(range(grid.shape[1])); ax.set_xticklabels(grid.columns)
    ax.set_yticks(range(grid.shape[0])); ax.set_yticklabels(grid.index)
    ax.set_xlabel("nursing staff turnover"); ax.set_ylabel("nurse hours quartile")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    _titles(
        ax, f"{label.capitalize()}: turnover and staffing hours are separate problems",
        "colour changes across every row, so churn costs quality even where hours are plentiful",
    )
    fig.colorbar(image, ax=ax, shrink=0.82, pad=0.02).outline.set_visible(False)
    return fig


def matched_cohort_slopes(context: dict):
    """BQ5 — the same homes measured twice, each measure indexed to its own start.

    Four measures on four different scales cannot share a y-axis honestly, so
    every one is indexed to 100 at 2019 and the slope carries the meaning. That
    also makes the headline visible at a glance: the commercial line ends above
    where it started and the two care lines end below.

    The x-axis is deliberately just two points. There is no 2020 or 2021
    snapshot, so anything drawn between them would be invented; a line with two
    ends and nothing in the middle is the honest shape for this data.
    """
    apply_style()
    series = [
        ("Occupancy", context["occupancy_from"], context["occupancy_to"], GOOD, "{:.1%}"),
        ("Nurse hours per resident/day", context["nurse_hprd_from"],
         context["nurse_hprd_to"], ACCENT, "{:.2f}"),
        ("Average star rating", context["rating_from"], context["rating_to"], WARN, "{:.2f}"),
        ("Deficiencies per facility", context["deficiencies_from"],
         context["deficiencies_to"], INK_MUTED, "{:.1f}"),
    ]
    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    ends = []
    for name, a, b, color, fmt in series:
        end = b / a * 100
        ax.plot([0, 1], [100, end], color=color, linewidth=2.4, zorder=3)
        ax.scatter([0, 1], [100, end], s=58, color=color, zorder=4)
        ends.append((end, name, a, b, color, fmt))

    # Occupancy and nurse hours both finish within a point and a half of the
    # baseline, so their labels have to be nudged apart or they overprint. The
    # leader line keeps each label attached to the series it belongs to.
    span = max(e[0] for e in ends) - min(e[0] for e in ends)
    min_gap = span * 0.11
    placed_at = None
    for end, name, a, b, color, fmt in sorted(ends, reverse=True):
        y = end if placed_at is None else min(end, placed_at - min_gap)
        placed_at = y
        ax.annotate(f"{name}\n{fmt.format(a)} → {fmt.format(b)}  ({b / a * 100 - 100:+.1f}%)",
                    xy=(1, end), xytext=(1.06, y), textcoords="data",
                    va="center", ha="left", fontsize=9.5, color=color, fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color=color, linewidth=0.8,
                                    shrinkA=1, shrinkB=6))

    ax.axhline(100, color=BASELINE, linewidth=1, zorder=2)
    ax.set_xticks([0, 1])
    ax.set_xticklabels([context["from_period"], context["to_period"]])
    ax.set_ylabel("indexed to 100 at the start")
    ax.set_xlim(-0.08, 1.62)
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    _titles(
        ax, "Occupancy recovered. Care quality did not.",
        f"the same {context['matched_facilities']:,} facilities measured in both periods · "
        "no 2020-2021 snapshot exists, so these are two endpoints, not a trend",
    )
    fig.tight_layout()
    return fig


def rating_migration(pairs: pd.DataFrame, context: dict):
    """BQ5 — where each 2019 star rating ended up in 2026.

    A single average hides that this is churn in both directions. Reading a row
    shows what happened to homes that started there; the mass below the diagonal
    is the story, and the bottom-left cell — one-star homes that are still one
    star seven years later — is the one that says the problems are structural.
    """
    apply_style()
    rated = pairs[pairs["rating_from"].notna() & pairs["rating_to"].notna()]
    grid = (pd.crosstab(rated["rating_from"], rated["rating_to"], normalize="index") * 100)

    fig, ax = plt.subplots(figsize=(7.4, 5.6), layout="constrained")
    ramp = mpl.colors.LinearSegmentedColormap.from_list("ramp", ["#f4f4f0"] + RATING_RAMP)
    image = ax.imshow(grid.values, cmap=ramp, aspect="auto", vmin=0)
    for r in range(grid.shape[0]):
        for c in range(grid.shape[1]):
            v = grid.values[r, c]
            ax.text(c, r, f"{v:.0f}%", ha="center", va="center", fontsize=10,
                    fontweight="bold", color=SURFACE if v > 22 else INK)
    ax.plot([-0.5, 4.5], [-0.5, 4.5], color=WARN, linewidth=1.2,
            linestyle=(0, (4, 3)), zorder=3)
    ax.set_xticks(range(5)); ax.set_xticklabels([f"{i}★" for i in grid.columns])
    ax.set_yticks(range(5)); ax.set_yticklabels([f"{i}★" for i in grid.index])
    ax.set_xlabel(f"rating in {context['to_period']}")
    ax.set_ylabel(f"rating in {context['from_period']}")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    _titles(
        ax, "Twice as many homes fell as rose",
        f"each row sums to 100% · {context['pct_declined']:.0f}% declined, "
        f"{context['pct_improved']:.0f}% improved, {context['pct_unchanged']:.0f}% held · "
        "below the dashed line is decline",
    )
    fig.colorbar(image, ax=ax, shrink=0.82, pad=0.02).outline.set_visible(False)
    return fig


def risk_backtest(frame: pd.DataFrame, context: dict, min_facilities: int = 100):
    """BQ6 — the watchlist score checked against what actually happened.

    A risk score with no validation is a ranked opinion. This scores the January
    2019 population, then looks up fines issued from July 2023 onward — the
    score cannot have seen them. Both outcome measures rise with the score
    across the whole range, which is what earns the list a place on a dashboard.

    Thin buckets are drawn hollow. The top scores hold ten to forty facilities,
    so their rates are real but noisy, and a solid marker would oversell them.
    """
    apply_style()
    usable = frame[frame["facilities_2019"] > 0]
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(9.0, 7.0), sharex=True, gridspec_kw={"hspace": 0.18}
    )
    xs = usable["risk_score"]

    for ax, col, color, label, base in [
        (top, "pct_fined_2023_26", ACCENT, "% fined 2023-2026", context["base_pct_fined"]),
        (bottom, "fines_per_bed_2023_26", WARN, "fines per bed 2023-2026 (USD)",
         context["base_fines_per_bed"]),
    ]:
        # The line is drawn only through buckets with enough facilities to
        # carry a rate. Scores 7-9 hold 10-41 homes between them, so their
        # points swing by tens of percentage points on a handful of cases;
        # letting the line run through them makes a monotone relationship look
        # erratic. They stay on the chart as hollow markers — visible, and
        # visibly not part of the trend.
        solid = usable[usable["facilities_2019"] >= min_facilities]
        ax.plot(solid["risk_score"], solid[col], color=color, linewidth=2.2, zorder=3)
        for x, y, n in zip(xs, usable[col], usable["facilities_2019"]):
            thin = n < min_facilities
            ax.scatter([x], [y], s=64, zorder=4, color=SURFACE if thin else color,
                       edgecolor=color, linewidth=2)
        ax.axhline(base, color=BASELINE, linewidth=1.2, linestyle=(0, (4, 3)), zorder=2)
        ax.annotate("all facilities", xy=(xs.max(), base), xytext=(4, 4),
                    textcoords="offset points", fontsize=8.5, color=INK_MUTED)
        ax.set_ylabel(label)
        ax.yaxis.grid(True); ax.set_axisbelow(True)

    bottom.yaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("${x:,.0f}"))
    bottom.set_xlabel("risk score assigned from the January 2019 snapshot")
    bottom.set_xticks(list(xs))
    _titles(
        top, "The watchlist score separated outcomes four years before they happened",
        f"scored on {context['scored_period']}, outcomes measured {context['outcome_window']} · "
        f"top group {context['lift_incidence']:.2f}x the fine rate and "
        f"{context['lift_dollars']:.2f}x the dollars · hollow markers are groups under "
        f"{min_facilities} facilities",
    )
    fig.tight_layout()
    return fig


def fines_timeline(frame: pd.DataFrame, context: dict):
    """BQ7 over time — the assignment's time-series chart, with its two holes marked.

    The line breaks at the era boundary instead of spanning it. Joining 2018 Q3
    to 2023 Q2 would draw a five-year edge across a period for which no snapshot
    publishes penalties at all, and a reader would take that edge as a
    measurement. Quarters truncated by reporting lag are drawn hollow and left
    out of the trend line for the same reason: 2018 Q4 holds 12 events against a
    run rate near 600, which is a filing delay, not an enforcement collapse.
    """
    apply_style()
    fines = frame[frame["penalty_type"] == "Fine"].copy()
    fines = fines.sort_values(["year", "quarter"]).reset_index(drop=True)
    fines["x"] = range(len(fines))

    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(10.4, 7.2), sharex=True, gridspec_kw={"hspace": 0.2}
    )

    for _, era in fines.groupby("era"):
        solid = era[~era["incomplete"]]
        top.plot(solid["x"], solid["fines"] / 1e6, color=ACCENT, linewidth=2.2, zorder=3)
        top.scatter(solid["x"], solid["fines"] / 1e6, s=42, color=ACCENT, zorder=4)
        hollow = era[era["incomplete"]]
        top.scatter(hollow["x"], hollow["fines"] / 1e6, s=42, zorder=4,
                    color=SURFACE, edgecolor=ACCENT, linewidth=1.6)

        avg = era["fines"] / era["events"].replace(0, pd.NA) / 1000
        solid_avg = avg[~era["incomplete"]]
        bottom.plot(era.loc[~era["incomplete"], "x"], solid_avg,
                    color=WARN, linewidth=2.2, zorder=3)
        bottom.scatter(era.loc[~era["incomplete"], "x"], solid_avg, s=42,
                       color=WARN, zorder=4)
        bottom.scatter(era.loc[era["incomplete"], "x"], avg[era["incomplete"]],
                       s=42, color=SURFACE, edgecolor=WARN, linewidth=1.6, zorder=4)

    # Mark the gap between eras.
    boundaries = fines.index[fines["era"].diff() > 0].tolist()
    for b in boundaries:
        for ax in (top, bottom):
            ax.axvspan(b - 1.0, b - 0.0, color=GRID, zorder=1)
        top.annotate("no snapshot covers\n2019-2022", xy=(b - 0.5, top.get_ylim()[1]),
                     xytext=(0, -6), textcoords="offset points", ha="center", va="top",
                     fontsize=8.5, color=INK_MUTED)

    top.set_ylabel("fine dollars issued (millions)")
    top.yaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("${x:,.0f}M"))
    bottom.set_ylabel("average fine per event (thousands)")
    bottom.yaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("${x:,.0f}k"))
    for ax in (top, bottom):
        ax.yaxis.grid(True); ax.set_axisbelow(True)

    step = max(1, len(fines) // 16)
    bottom.set_xticks(fines["x"][::step])
    bottom.set_xticklabels(fines["year_quarter"][::step], rotation=45,
                           ha="right", fontsize=8.5)
    _titles(
        top, "Fewer penalties, each one larger",
        "quarterly, from the rolling three-year window each snapshot publishes · "
        "hollow markers are quarters still filling up, and are left out of the line",
    )
    fig.tight_layout()
    return fig


def enforcement_by_state(frame: pd.DataFrame, context: dict, top_n: int = 20,
                         highlight: list[str] | None = None):
    """BQ7 by place — fines per bed, which is the number an operator feels.

    Total dollars would rank states by how many beds they have and tell a CEO
    nothing. Per bed, the spread is 24-fold across states with the same federal
    rules, so where a home sits changes its regulatory exposure more than most
    things it can control.
    """
    apply_style()
    ranked = frame.nlargest(top_n, "fines_per_bed").sort_values("fines_per_bed")
    highlight = set(highlight or [])
    colors = [ACCENT if s in highlight else NEUTRAL for s in ranked["state_code"]]

    fig, ax = plt.subplots(figsize=(8.6, max(5.0, 0.30 * len(ranked) + 1.8)))
    ax.barh(range(len(ranked)), ranked["fines_per_bed"], color=colors, height=0.72, zorder=3)
    ax.axvline(context["national_fines_per_bed"], color=WARN,
               linestyle=(0, (4, 3)), linewidth=1.3, zorder=4)
    ax.annotate(f"national ${context['national_fines_per_bed']:,.0f}",
                xy=(context["national_fines_per_bed"], len(ranked) - 0.4),
                xytext=(6, 0), textcoords="offset points", fontsize=9,
                color=WARN, fontweight="bold", va="center")

    for i, (_, row) in enumerate(ranked.iterrows()):
        ax.annotate(f"${row['fines_per_bed']:,.0f}  ·  {row['pct_facilities_fined']:.0f}% fined",
                    xy=(row["fines_per_bed"], i), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=8.5,
                    color=INK_SECONDARY)

    ax.set_yticks(range(len(ranked)))
    ax.set_yticklabels(ranked["state_code"], fontsize=9)
    ax.set_xlabel("fines per certified bed, 3 years (USD)")
    ax.xaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("${x:,.0f}"))
    ax.xaxis.grid(True); ax.set_axisbelow(True)
    ax.margins(x=0.16)
    _titles(
        ax, f"Enforcement intensity varies {context['spread']:.0f}-fold between states",
        f"window {context['fines_window']} · beds from {context['period']} · "
        f"states with at least {context['min_facilities']} facilities",
    )
    fig.tight_layout()
    return fig


def staffing_controlled(frame: pd.DataFrame, column: str = "health_inspection_rating"):
    """BQ3's control — the staffing gradient drawn once per ownership group.

    Same logic as the chain-size control in BQ8: for-profit homes both staff
    thinly and score badly, so one line across staffing quartiles cannot tell
    "hours matter" apart from "ownership matters". Three lines can.
    """
    apply_style()
    label = {"health_inspection_rating": "health inspection star",
             "avg_rating": "average star rating",
             "fines_per_bed": "fines per bed, 3 years (USD)"}.get(column, column)
    quartiles = list(frame["hprd_quartile"].unique())

    fig, ax = plt.subplots(figsize=(8.4, 5.4))
    for index, (owner, part) in enumerate(frame.groupby("ownership_group", observed=True)):
        part = part.sort_values("hprd_quartile")
        color = GROUP_COLORS[index % len(GROUP_COLORS)]
        xs = [quartiles.index(q) for q in part["hprd_quartile"]]
        ax.plot(xs, part[column], color=color, linewidth=2.2, zorder=3)
        ax.scatter(xs, part[column], s=58, color=color, zorder=4)
        ax.annotate(str(owner), xy=(xs[-1], part[column].iloc[-1]), xytext=(9, 0),
                    textcoords="offset points", va="center", fontsize=9.5,
                    fontweight="bold", color=color)

    ax.set_xticks(range(len(quartiles)))
    ax.set_xticklabels(quartiles)
    ax.set_xlabel("nurse hours per resident per day, quartile")
    ax.set_ylabel(label)
    if column == "fines_per_bed":
        ax.yaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("${x:,.0f}"))
    ax.yaxis.grid(True); ax.set_axisbelow(True)
    ax.margins(x=0.22)
    _titles(
        ax, "The staffing gradient holds inside every ownership group",
        "so it is not the ownership gap from BQ2 wearing a different label",
    )
    fig.tight_layout()
    return fig
