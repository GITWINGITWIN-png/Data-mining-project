"""ElderCare Insight — the matplotlib dashboard, sharing code with the notebooks.

    cd Group01_ElderCareDW/04_Dashboard
    streamlit run app_mpl.py

**There are two dashboards in this folder and that is deliberate.** `app.py`
(Plotly, reading the SQL views in `03_Data_Warehouse/views.sql`) and this one
were built in parallel by two members before either had pushed. Both answer
BQ1-BQ8. Rather than delete one, the group kept both until it decides which to
submit — see `README.md` for the comparison.

What is specific to this one: every figure comes from `charts.py`, which the
BQ1/BQ2/BQ8 notebooks in this folder import as well, so a number verified in a
notebook cell is the same function call the app makes.

Nothing here computes a number. Every figure on screen comes from
`queries.py` and every chart from `charts.py`, which is what makes the
dashboard and the notebooks agree by construction rather than by luck: a
number verified in a notebook cell is literally the same function call the
app makes.

Layout follows the eight business questions from the design document, not
the shape of the warehouse. A CEO opening this wants "which state should we
buy into", not "here is Fact_Facility_Monthly".

Two things the caching does that are worth knowing before editing:

  * the DuckDB connection is a `cache_resource` — one read-only handle for
    the whole session, because opening one per rerun on every slider drag
    is what makes a Streamlit app feel broken;
  * query results are `cache_data` keyed on the filter arguments, so those
    arguments have to be hashable. That is why filters travel as tuples and
    are turned back into lists at the call site.
"""

from __future__ import annotations

import matplotlib
import pandas as pd
import streamlit as st

matplotlib.use("Agg")  # never try to open a window from a server process

import charts  # noqa: E402
import queries_mpl as queries  # noqa: E402

st.set_page_config(
    page_title="ElderCare Insight",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Thai text needs a font stack that actually has Thai in it; the Streamlit
# default stack falls back to a serif on Linux and the numerals stop matching
# the labels beside them.
st.markdown(
    """
    <style>
      html, body, [class*="css"] {
        font-family: "Noto Sans Thai", "Sarabun", "IBM Plex Sans Thai",
                     system-ui, -apple-system, sans-serif;
      }
      [data-testid="stMetricValue"] { font-size: 1.55rem; }
      [data-testid="stMetricLabel"] { font-size: 0.82rem; }
      .insight {
        border-left: 3px solid #2a78d6; padding: 0.35rem 0 0.35rem 0.9rem;
        margin-bottom: 0.9rem;
      }
      .caveat {
        border-left: 3px solid #c9422c; padding: 0.35rem 0 0.35rem 0.9rem;
        margin-bottom: 0.9rem; background: #fdf6f4;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------
#  Data access. Everything is cached; nothing is recomputed on a rerun
#  unless one of its arguments changed.
# ---------------------------------------------------------------------

@st.cache_resource
def get_connection():
    return queries.connect(read_only=True)


@st.cache_data(show_spinner=False)
def load_periods() -> pd.DataFrame:
    return queries.available_periods(get_connection())


@st.cache_data(show_spinner=False)
def load_states() -> list[str]:
    return [
        s for (s,) in get_connection().execute(
            "SELECT DISTINCT state_code FROM Dim_Geography "
            "WHERE state_code <> 'Unknown' ORDER BY 1"
        ).fetchall()
    ]


def _filter_kwargs(period_key, states, ownership, chains, exclude_suspect) -> dict:
    """Turn the sidebar's tuples back into the lists the query layer wants."""
    return {
        "period_key": period_key,
        "states": list(states) or None,
        "ownership_groups": list(ownership) or None,
        "chain_bands": list(chains) or None,
        "exclude_suspect": exclude_suspect,
    }


@st.cache_data(show_spinner=False)
def kpis(period_key, states, ownership, chains, exclude_suspect):
    return queries.kpi_summary(
        get_connection(),
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def staffing(period_key, states, ownership, chains, exclude_suspect):
    return queries.bq3_staffing_gradient(
        get_connection(),
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def staffing_controlled(period_key, states, chains, exclude_suspect):
    return queries.bq3_controlled(
        get_connection(), period_key=period_key, states=list(states) or None,
        chain_bands=list(chains) or None, exclude_suspect=exclude_suspect,
    )


@st.cache_data(show_spinner=False)
def turnover(period_key, states, ownership, chains, exclude_suspect):
    return queries.bq4_turnover_gradient(
        get_connection(),
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def turnover_controlled(period_key, states, ownership, chains, exclude_suspect):
    return queries.bq4_controlled(
        get_connection(),
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def turnover_scan(period_key, states, ownership, chains, exclude_suspect):
    return queries.bq4_split_scan(
        get_connection(),
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def watchlist(period_key, states, ownership, chains, exclude_suspect, top_n):
    return queries.bq6_watchlist(
        get_connection(), top_n=top_n,
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def market(period_key, exclude_suspect, min_facilities):
    return queries.bq1_state_market(
        get_connection(), period_key=period_key,
        exclude_suspect=exclude_suspect, min_facilities=min_facilities,
    )


@st.cache_data(show_spinner=False)
def segments(segment, period_key, exclude_suspect):
    return queries.segment_compare(
        get_connection(), segment, period_key=period_key,
        exclude_suspect=exclude_suspect,
    )


@st.cache_data(show_spinner=False)
def segment_rows(segment, period_key, exclude_suspect):
    return queries.segment_facilities(
        get_connection(), segment, period_key=period_key,
        exclude_suspect=exclude_suspect,
    )


@st.cache_data(show_spinner=False)
def cross_section(period_key, exclude_suspect):
    return queries.segment_cross(
        get_connection(), period_key=period_key, exclude_suspect=exclude_suspect)


@st.cache_data(show_spinner=False)
def cohort(to_period, exclude_suspect):
    return queries.bq5_matched_cohort(
        get_connection(), to_period=to_period, exclude_suspect=exclude_suspect)


@st.cache_data(show_spinner=False)
def exits(to_period):
    return queries.bq5_capacity_exit(get_connection(), to_period=to_period)


@st.cache_data(show_spinner=False)
def period_trend(exclude_suspect):
    return queries.bq5_period_trend(get_connection(), exclude_suspect=exclude_suspect)


@st.cache_data(show_spinner=False)
def enforcement(period_key, exclude_suspect, min_facilities):
    return queries.bq7_state_enforcement(
        get_connection(), period_key=period_key,
        exclude_suspect=exclude_suspect, min_facilities=min_facilities,
    )


@st.cache_data(show_spinner=False)
def timeline(states):
    return queries.bq7_fines_timeline(get_connection(), states=list(states) or None)


@st.cache_data(show_spinner=False)
def backtest(exclude_suspect):
    return queries.bq6_backtest(get_connection(), exclude_suspect=exclude_suspect)


@st.cache_data(show_spinner=False)
def roster(period_key, states, ownership, chains, exclude_suspect):
    return queries.facility_roster(
        get_connection(),
        **_filter_kwargs(period_key, states, ownership, chains, exclude_suspect),
    )


@st.cache_data(show_spinner=False)
def quality_log() -> pd.DataFrame:
    return get_connection().execute(
        """
        SELECT rule, step, COUNT(*) AS entries,
               SUM(rows_affected) AS rows_affected
        FROM etl_run_log
        WHERE rule LIKE 'Q%' AND rows_affected > 0
        GROUP BY 1, 2 ORDER BY 4 DESC
        """
    ).df()


# ---------------------------------------------------------------------
#  Small presentation helpers
# ---------------------------------------------------------------------

def insight(text: str) -> None:
    st.markdown(f'<div class="insight">{text}</div>', unsafe_allow_html=True)


def caveat(text: str) -> None:
    st.markdown(f'<div class="caveat">⚠️ {text}</div>', unsafe_allow_html=True)


def show(figure) -> None:
    """Draw a matplotlib figure and release it.

    Streamlit reruns the whole script on every widget change. Without the
    close, each rerun leaves its figures in pyplot's registry and the process
    grows until matplotlib starts warning about open figures.
    """
    st.pyplot(figure, width="stretch")
    matplotlib.pyplot.close(figure)


# ---------------------------------------------------------------------
#  Sidebar — the interactive controls
# ---------------------------------------------------------------------

periods = load_periods()
# The count here is every row the warehouse holds for that period. The header
# further down reports facilities *after* the suspect-row toggle and the
# filters, so the two numbers differ by design — label this one for what it is
# rather than letting it read as a contradiction.
period_labels = {
    int(r.period_key): f"{str(r.period)[:10]}  ({r.facilities:,} แถวในคลัง)"
    for r in periods.itertuples()
}

with st.sidebar:
    st.title("🏥 ElderCare Insight")
    st.caption("Mini Data Warehouse & Analytics Dashboard · Group 01")

    st.subheader("ตัวกรอง")

    period_key = st.selectbox(
        "งวดข้อมูล (snapshot)",
        options=list(period_labels),
        index=len(period_labels) - 1,
        format_func=lambda k: period_labels[k],
        key="f_period",
        help="ทุกตัวเลขบนหน้าจอมาจากงวดนี้ ยกเว้นกราฟแนวโน้มที่ระบุไว้เอง",
    )

    selected_states = st.multiselect(
        "รัฐ", options=load_states(), default=[], key="f_states",
        help="ว่างไว้ = ทั้งประเทศ · เลือกหลายรัฐได้",
    )

    selected_ownership = st.multiselect(
        "กลุ่มการถือครอง", options=["For profit", "Non profit", "Government"],
        default=[], key="f_ownership",
    )

    selected_chains = st.multiselect(
        "ขนาดเครือ",
        options=["Independent (1)", "Small chain (2-9)",
                 "Medium chain (10-49)", "Large chain (50+)"],
        default=[], key="f_chains",
        help="ข้อมูลเครือมีเฉพาะงวดปี 2569 เท่านั้น",
    )

    exclude_suspect = st.toggle(
        "ตัดแถวที่ ETL ตั้งค่าสงสัยออก", value=True, key="f_suspect",
        help="แถวที่ผ่านกฎคุณภาพ Q1-Q8 แล้วยังมีค่าผิดปกติ เช่น "
             "ผู้พักอาศัยมากกว่าเตียงที่รับรอง",
    )

    st.divider()
    filters_on = bool(selected_states or selected_ownership or selected_chains)
    if filters_on:
        st.success(
            "กำลังกรอง: "
            + " · ".join(filter(None, [
                f"{len(selected_states)} รัฐ" if selected_states else "",
                "/".join(selected_ownership) if selected_ownership else "",
                f"{len(selected_chains)} ขนาดเครือ" if selected_chains else "",
            ]))
        )
    else:
        st.info("ยังไม่ได้กรอง — กำลังแสดงภาพรวมทั้งประเทศ")

    st.divider()
    st.caption(
        "**ที่มา** CMS Nursing Home Compare (4 งวด) · "
        "SNF VBP · US Census ประชากร 65+\n\n"
        "**คลัง** DuckDB · 6 Dimension · 2 Fact · 87,194 แถวข้อเท็จจริง"
    )

# Tuples, because `st.cache_data` hashes the arguments.
S = tuple(selected_states)
O = tuple(selected_ownership)
C = tuple(selected_chains)

k = kpis(period_key, S, O, C, exclude_suspect)

if k["facilities"] == 0:
    st.error("ตัวกรองชุดนี้ไม่เหลือสถานพยาบาลเลย — ลองลดเงื่อนไขลง")
    st.stop()


# ---------------------------------------------------------------------
#  Header — the measures that summarise business performance
# ---------------------------------------------------------------------

st.title("ElderCare Insight — คลังข้อมูลและแดชบอร์ดสถานดูแลผู้สูงอายุ")
st.caption(
    f"งวด {k['period']} · {k['facilities']:,} สถานพยาบาล · {k['beds']:,} เตียง · "
    f"ค่าปรับนับย้อนหลัง {k['fines_window_years']} ปี ({k['fines_window']})"
)

row1 = st.columns(4)
row1[0].metric("M1 อัตราการเข้าพัก", f"{k['occupancy']:.1%}",
               help="SUM(ผู้พักอาศัย) / SUM(เตียง) — ไม่ใช่ค่าเฉลี่ยของอัตราส่วนรายแห่ง")
row1[1].metric("M3 ชั่วโมงพยาบาล/คน/วัน", f"{k['nurse_hprd']:.2f}",
               delta=f"{k['nurse_hprd'] - queries.CMS_MIN_TOTAL_HPRD:+.2f} เทียบเกณฑ์ CMS 3.48",
               help="ถ่วงน้ำหนักด้วยจำนวนผู้พักอาศัย เพราะ HPRD เป็นอัตราต่อหัวอยู่แล้ว")
row1[2].metric("M4 อัตราการลาออกพยาบาล", f"{k['turnover_pct']:.1f}%",
               help="ถ่วงน้ำหนักด้วยจำนวนผู้พักอาศัยเช่นกัน")
row1[3].metric("M7 คะแนนดาวเฉลี่ย", f"{k['avg_rating']:.2f}",
               delta=f"{k['pct_1_2_star']:.0f}% ได้ 1-2 ดาว", delta_color="off",
               help="เฉลี่ยเฉพาะแห่งที่ CMS ให้คะแนน — บ้านเปิดใหม่ยังไม่มีคะแนน")

row2 = st.columns(4)
row2[0].metric("M5 มูลค่าค่าปรับ", f"${k['fine_total']/1e6:,.1f}M",
               help="รวมจาก Fact_Penalty_Event ตรง ๆ — เป็น measure ที่รวมค่าได้")
row2[1].metric("M6 จำนวนครั้งที่ถูกปรับ", f"{k['fine_events']:,}",
               delta=f"{k['pct_fined']:.0f}% ของสถานพยาบาลเคยถูกปรับ", delta_color="off")
row2[2].metric("M9 ค่าปรับต่อเตียง", f"${k['fines_per_bed']:,.0f}",
               help="M5 / จำนวนเตียง — ทำให้เทียบข้ามขนาดได้อย่างเป็นธรรม")
row2[3].metric("M8 ข้อบกพร่องเฉลี่ย/แห่ง", f"{k['deficiencies']:.1f}",
               delta=f"{k['below_cms_floor_pct']:.0f}% จัดพยาบาลต่ำกว่าเกณฑ์ CMS",
               delta_color="off")

st.divider()

tab_overview, tab_market, tab_operator, tab_workforce, tab_trend, tab_risk, tab_reco = (
    st.tabs([
        "📊 ภาพรวม",
        "🗺️ ตลาด (BQ1)",
        "🏢 ผู้ประกอบการ (BQ2, BQ8)",
        "👩‍⚕️ กำลังคน (BQ3, BQ4)",
        "📈 แนวโน้ม (BQ5)",
        "⚠️ ความเสี่ยง (BQ6, BQ7)",
        "💡 ข้อเสนอแนะ",
    ])
)


# ---------------------------------------------------------------------
with tab_overview:
    st.subheader("คำถามทางธุรกิจแปดข้อ และคำตอบสั้น ๆ")

    answers = pd.DataFrame([
        ("BQ1", "รัฐใดน่าลงทุนที่สุด",
         "New Mexico, Virginia, South Carolina — อุปทานต่ำ เข้าพักสูง คู่แข่งอ่อน"),
        ("BQ2", "รูปแบบการถือครองต่างกันอย่างไร",
         "For-profit ต่ำกว่าทุกมิติคุณภาพ และเสี่ยงค่าปรับสูงกว่า 1.57 เท่า"),
        ("BQ3", "ชั่วโมงพยาบาลกับคุณภาพ จุดคุ้มอยู่ที่ใด",
         "จุดหักอยู่ที่ 3.48 ชม. ซึ่งตรงกับเกณฑ์ขั้นต่ำของ CMS พอดี"),
        ("BQ4", "ลาออกเท่าไรจึงเริ่มฉุดคุณภาพ",
         "คุณภาพตกทันทีไม่มีระดับปลอดภัย · อัตราเข้าพักเริ่มตกที่ 40% และพังที่ 60%"),
        ("BQ5", "ฟื้นจาก COVID แล้วหรือยัง",
         "ฟื้นเชิงพาณิชย์ แต่ไม่ฟื้นเชิงคุณภาพ — และการฟื้นมาจากอุปทานหดตัว"),
        ("BQ6", "แห่งใดกำลังเสื่อมและเสี่ยงถูกลงโทษ",
         "คะแนนเสี่ยงจัดอันดับได้จริง ผ่านการทดสอบย้อนหลัง 4 ปี"),
        ("BQ7", "ค่าปรับกระจุกที่ใด",
         "ต่างกัน 24 เท่าระหว่างรัฐ · ค่าปรับต่อครั้งโตขึ้น 77% ตั้งแต่ปี 2566"),
        ("BQ8", "เครือใหญ่ได้เปรียบจริงหรือไม่",
         "ไม่ได้เปรียบด้านคุณภาพ — เป็นการกดต้นทุนแรงงาน"),
    ], columns=["#", "คำถาม", "คำตอบ"])
    st.dataframe(answers, hide_index=True, width="stretch")

    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown("#### การกระจายตัวของคุณภาพ")
        st.caption("ตามตัวกรองด้านซ้าย")
        # Built from the filtered roster rather than from `segment_compare`, so
        # this panel moves with the sidebar the way the measures above it do.
        # A panel that silently stayed national while the KPIs beside it changed
        # would be read as the same population, and it is not.
        overview_rows, overview_context = roster(
            period_key, S, O, C, exclude_suspect)
        overview_rows = overview_rows.rename(columns={"ownership_group": "segment"})
        overview_context = overview_context | {"period": k["period"]}
        show(charts.rating_mix_by_segment(overview_rows, overview_context))
    with right:
        st.markdown("#### ความเข้มของการบังคับใช้กฎ")
        st.caption(
            "จัดอันดับทุกรัฐเสมอ — การกรองรัฐจะเน้นสีแทน "
            "เพราะการจัดอันดับที่เหลือรัฐเดียวไม่มีความหมาย"
        )
        state_frame, state_context = enforcement(period_key, exclude_suspect, 10)
        show(charts.enforcement_by_state(
            state_frame, state_context, top_n=15, highlight=list(selected_states)))

    with st.expander("คุณภาพข้อมูล — กฎ Q1-Q8 ที่ ETL บังคับใช้จริง (นับจาก etl_run_log)"):
        st.caption(
            "ตารางนี้อ่านจาก `etl_run_log` ในคลังข้อมูลโดยตรง ไม่ได้พิมพ์ลงไปเอง "
            "ทุกครั้งที่รัน ETL ใหม่ตัวเลขจะเปลี่ยนตาม"
        )
        st.dataframe(quality_log(), hide_index=True, width="stretch")


# ---------------------------------------------------------------------
with tab_market:
    st.subheader("BQ1 — รัฐใดน่าเข้าไปลงทุนที่สุด")
    st.caption(
        "ผู้ใช้: CEO · ตอบจาก Fact_Facility_Monthly × Dim_Geography × Ref_State_Population"
    )
    if filters_on:
        caveat(
            "หน้านี้จัดอันดับ *ทุกรัฐ* เสมอ เพราะการจัดอันดับที่เหลือรัฐเดียวไม่มีความหมาย "
            "ตัวกรองรัฐจะใช้เน้นสีบนกราฟแทน"
        )

    min_facilities = st.slider(
        "ตัดรัฐที่มีสถานพยาบาลน้อยกว่า", 0, 60, 10, step=5,
        help="รัฐเล็กมากทำให้อันดับแกว่งจากบ้านไม่กี่หลัง",
    )
    ranked, market_context = market(period_key, exclude_suspect, min_facilities)
    baseline = queries.bq1_national_baseline(
        get_connection(), period_key=period_key, exclude_suspect=exclude_suspect)
    scored = queries.bq1_opportunity_score(ranked)

    cols = st.columns(3)
    cols[0].metric("เตียงต่อผู้สูงอายุ 1,000 คน (ทั้งประเทศ)",
                   f"{baseline['beds_per_1k_65plus']:.1f}")
    cols[1].metric("อัตราการเข้าพักทั้งประเทศ", f"{baseline['occupancy']:.1%}")
    cols[2].metric("คะแนนดาวเฉลี่ยของคู่แข่ง", f"{baseline['avg_overall_rating']:.2f}")

    show(charts.market_scatter(scored, market_context))
    left, right = st.columns(2)
    with left:
        show(charts.beds_per_1k_bar(scored, market_context))
    with right:
        show(charts.score_components_bar(scored))

    insight(
        f"<b>New Mexico นำเป็นอันดับหนึ่ง</b> — 16.1 เตียงต่อผู้สูงอายุพันคน "
        f"เทียบกับ {baseline['beds_per_1k_65plus']:.1f} ทั้งประเทศ ขณะที่อัตราการเข้าพัก "
        f"83.4% สูงกว่าค่าเฉลี่ย และคะแนนคู่แข่งเพียง 2.85 ดาว "
        "คือมีอุปสงค์เหลือ ตลาดยังไม่อิ่มตัว และคู่แข่งอ่อน ครบทั้งสามเงื่อนไข"
    )
    insight(
        "<b>ควรเลี่ยง Iowa, Indiana, Arkansas</b> — เตียงต่อประชากรสูงและอัตราการเข้าพักต่ำ "
        "พร้อมกัน แปลว่าตลาดล้นแล้ว การเพิ่มเตียงเข้าไปจะแย่งผู้พักอาศัยจากบ้านที่มีอยู่"
    )

    with st.expander("ตารางเต็ม 51 รัฐ (drill-down)"):
        st.dataframe(
            scored.style.format({
                "beds_per_1k_65plus": "{:.1f}", "occupancy": "{:.1%}",
                "avg_overall_rating": "{:.2f}", "opportunity_score": "{:.3f}",
                "pop_65plus": "{:,.0f}", "beds": "{:,.0f}",
            }),
            hide_index=True, width="stretch", height=420,
        )


# ---------------------------------------------------------------------
with tab_operator:
    st.subheader("BQ2 และ BQ8 — ใครเป็นเจ้าของ และขนาดเครือสำคัญไหม")

    segment_choice = st.radio(
        "เปรียบเทียบตาม", options=["ownership_group", "chain_size_band", "ownership_type"],
        format_func=lambda s: {
            "ownership_group": "กลุ่มการถือครอง 3 กลุ่ม (BQ2)",
            "chain_size_band": "ขนาดเครือ 4 ระดับ (BQ8)",
            "ownership_type": "ประเภทการถือครอง 13 ประเภท",
        }[s],
        horizontal=True, key="f_segment",
    )

    # Chain columns only entered ProviderInfo in 2025, so on a 2019 period every
    # facility points at the Unknown chain and this comparison has nothing to
    # say. `st.stop()` would be the obvious guard and it is the wrong one: it
    # halts the whole script, blanking every tab below this one too. Skipping
    # just this section keeps the rest of the dashboard usable.
    chain_data_missing = False
    if segment_choice == "chain_size_band":
        chain_periods = queries.chain_data_periods(get_connection())
        usable = chain_periods[chain_periods["with_chain_data"] > 0]
        chain_data_missing = period_key not in set(usable["period_key"])

    if chain_data_missing:
        caveat(
            "งวดที่เลือกไม่มีข้อมูลเครือ — คอลัมน์เครือเพิ่งเพิ่มเข้ามาในปี 2568 "
            "กรุณาเลือกงวดปี 2569 จากแถบด้านซ้าย แล้วหน้านี้จะกลับมาแสดงผลเอง"
        )
    else:
        frame, context = segments(segment_choice, period_key, exclude_suspect)
        rows = segment_rows(segment_choice, period_key, exclude_suspect)

        show(charts.segment_small_multiples(frame, context))

        left, right = st.columns(2)
        with left:
            show(charts.rating_mix_by_segment(rows, context))
        with right:
            # 2019 predates the turnover column entirely, so fall back to the star
            # distribution rather than showing an empty panel for that period.
            box_column = "turnover" if rows["turnover"].notna().any() else "rating"
            show(charts.rating_box_by_segment(rows, context, column=box_column))

        st.markdown("#### ตัวเลขที่กราฟด้านบนอ่านมา")
        st.dataframe(
            frame[["segment", "facilities", "beds", "occupancy", "avg_rating",
                   "nurse_hprd", "rn_share", "turnover_pct", "fines_per_bed",
                   "share_fined"]].style.format({
                "beds": "{:,.0f}", "occupancy": "{:.1%}", "avg_rating": "{:.2f}",
                "nurse_hprd": "{:.2f}", "rn_share": "{:.1%}",
                "turnover_pct": "{:.1f}", "fines_per_bed": "${:,.0f}",
                "share_fined": "{:.1%}",
            }),
            hide_index=True, width="stretch",
        )

        if segment_choice == "ownership_group":
            insight(
                "<b>For-profit ต่ำกว่าในทุกมิติคุณภาพ</b> — คะแนนดาวห่าง 0.77 "
                "ชั่วโมงพยาบาลน้อยกว่า 16% และค่าปรับต่อเตียงสูงกว่า Non-profit 1.57 เท่า "
                "($351 เทียบกับ $224)"
            )
            insight(
                "<b>อัตราการเข้าพักของ Government ต่ำกว่าชัดเจน (72.8%)</b> "
                "ซึ่งเป็นเรื่องเชิงพาณิชย์ ไม่ใช่คุณภาพ — คะแนนดาวของกลุ่มนี้สูงกว่า For-profit"
            )
            caveat(
                "เปรียบเทียบที่ระดับ 3 กลุ่ม ไม่ใช่ 13 ประเภท เพราะระหว่างปี 2562-2569 "
                "มี 3,777 แห่งเปลี่ยนจาก Corporation เป็น Limited Liability company "
                "ซึ่งเป็นการเปลี่ยนรูปแบบนิติบุคคล ไม่ใช่เปลี่ยนผู้บริหาร"
            )
        elif segment_choice == "chain_size_band":
            st.markdown("#### การควบคุมตัวแปร — เครือใหญ่เกือบทั้งหมดเป็น for-profit")
            st.caption(
                "ถ้าไม่คุมตัวแปรนี้ กราฟ 'เครือใหญ่จัดพยาบาลบางกว่า' กับ "
                "'for-profit จัดพยาบาลบางกว่า' จะหน้าตาเหมือนกันทุกประการ"
            )
            cross = cross_section(period_key, exclude_suspect)
            measure = st.selectbox(
                "ดูมาตรวัด", ["nurse_hprd", "avg_rating", "turnover_pct"],
                format_func=lambda c: charts.MEASURES[c][0],
            )
            show(charts.controlled_lines(cross, measure, context))
            insight(
                "<b>ชั่วโมงพยาบาลลดลงทุกขั้นตามขนาดเครือ ในทั้งสามกลุ่มเจ้าของ</b> — "
                "For profit 3.75→3.54, Non profit 4.38→3.47, Government 4.56→3.34 "
                "ขนาดเครือจึงมีผลที่รูปแบบการถือครองอธิบายไม่ได้"
            )
            insight(
                "<b>สิ่งที่เครือใหญ่ได้เปรียบจริงคือด้านพาณิชย์</b> — อัตราการเข้าพักสูงกว่า "
                "1.6 จุด ขณะที่คะแนนดาวต่ำกว่า 0.32 และอัตราลาออกสูงกว่า 4.6 จุด"
            )
            caveat(
                "ข้อมูลเครือมีเฉพาะปี 2569 จึงยังแยกไม่ได้ว่าเครือทำให้บ้านแย่ลง "
                "หรือเครือไปซื้อบ้านที่แย่อยู่แล้ว"
            )


# ---------------------------------------------------------------------
with tab_workforce:
    st.subheader("BQ3 และ BQ4 — ชั่วโมงพยาบาลและการรักษาคน")

    bq3_tab, bq4_tab = st.tabs(["BQ3 · ชั่วโมงพยาบาล", "BQ4 · อัตราการลาออก"])

    with bq3_tab:
        frame, context = staffing(period_key, S, O, C, exclude_suspect)
        cols = st.columns(3)
        cols[0].metric("ต่ำกว่าเกณฑ์ CMS 3.48 ชม.",
                       f"{context['below_floor_pct']:.1f}%",
                       delta=f"{context['below_floor']:,} แห่ง", delta_color="off")
        cols[1].metric("ค่าปรับต่อเตียง — ต่ำกว่าเกณฑ์",
                       f"${context['below']['fines_per_bed']:,.0f}",
                       delta=f"เทียบ ${context['above']['fines_per_bed']:,.0f} เมื่อถึงเกณฑ์",
                       delta_color="inverse")
        cols[2].metric("ดาวตรวจสุขภาพ — ต่ำกว่าเกณฑ์",
                       f"{context['below']['health_inspection_rating']:.2f}",
                       delta=f"เทียบ {context['above']['health_inspection_rating']:.2f} เมื่อถึงเกณฑ์")

        show(charts.staffing_gradient(frame, context))

        st.markdown("#### คุมตัวแปรการถือครอง")
        show(charts.staffing_controlled(
            staffing_controlled(period_key, S, C, exclude_suspect)))

        insight(
            f"<b>จุดคุ้มอยู่ที่เกณฑ์ 3.48 ชั่วโมงพอดี</b> — ใต้เส้นนี้ค่าปรับต่อเตียง "
            f"${context['below']['fines_per_bed']:,.0f} เหนือเส้น "
            f"${context['above']['fines_per_bed']:,.0f} ต่างกัน "
            f"{context['below']['fines_per_bed']/context['above']['fines_per_bed']:.2f} เท่า "
            f"และ {context['below_floor_pct']:.0f}% ของสถานพยาบาลยังอยู่ใต้เส้น"
        )
        insight(
            "<b>ผลตอบแทนลดลงชัดเจนเหนือ 3.75 ชั่วโมง</b> — ค่าปรับต่อเตียงระหว่างช่วง "
            "3.48 ถึง 4.25 แทบไม่ต่างกัน ($289, $264, $283) การเติมชั่วโมงเกินจุดนี้ "
            "ซื้อคะแนนดาวได้ แต่แทบไม่ลดความเสี่ยงค่าปรับเพิ่ม"
        )
        caveat(
            "CMS นำชั่วโมงพยาบาลไปคิดเป็น 'ดาวด้านการจัดพยาบาล' ซึ่งเป็นองค์ประกอบหนึ่ง "
            "ของคะแนนรวมอยู่แล้ว ความสัมพันธ์กับคะแนนรวมจึงเป็นวงกลมบางส่วน "
            "ข้อสรุปข้างต้นจึงอ้างอิง <b>ดาวการตรวจสุขภาพ</b> (ผู้ตรวจให้คะแนนหน้างาน "
            "ไม่เกี่ยวกับชั่วโมง) และ <b>ค่าปรับ</b> แทน"
        )
        with st.expander("ตารางช่วงชั้นชั่วโมงพยาบาล"):
            st.dataframe(
                frame.style.format({
                    "beds": "{:,.0f}", "avg_rating": "{:.2f}",
                    "health_inspection_rating": "{:.2f}", "deficiencies": "{:.1f}",
                    "fines_per_bed": "${:,.0f}", "pct_fined": "{:.1f}%",
                    "occupancy": "{:.1%}", "nurse_hprd": "{:.2f}",
                    "turnover_pct": "{:.1f}", "pct_1_2_star": "{:.1f}%",
                    "pct_4_5_star": "{:.1f}%",
                }, na_rep="—"),
                hide_index=True, width="stretch",
            )

    with bq4_tab:
        frame4, context4 = turnover(period_key, S, O, C, exclude_suspect)
        if context4["measured_facilities"] == 0:
            caveat(
                "งวดที่เลือกไม่มีข้อมูลอัตราการลาออก — CMS เพิ่มคอลัมน์นี้หลังปี 2562 "
                "กรุณาเลือกงวดปี 2569"
            )
        else:
            cols = st.columns(3)
            cols[0].metric("อัตราลาออกเฉลี่ย",
                           f"{context4['national_turnover_weighted']:.1f}%")
            cols[1].metric("มัธยฐาน", f"{context4['national_turnover_median']:.1f}%")
            cols[2].metric("สถานพยาบาลที่ลาออกเกิน 60%",
                           f"{int(frame4[frame4['band'].isin(['60-65%', '65%+'])]['facilities'].sum()):,}")

            show(charts.turnover_gradient(frame4, context4))

            st.markdown("#### คุมตัวแปรชั่วโมงพยาบาล")
            measure4 = st.selectbox(
                "ดูมาตรวัด", ["avg_rating", "occupancy", "fines_per_bed"],
                format_func=lambda c: charts.MEASURES[c][0], key="bq4_measure",
            )
            show(charts.turnover_staffing_heatmap(
                turnover_controlled(period_key, S, O, C, exclude_suspect), measure4))

            insight(
                "<b>คุณภาพไม่มีระดับปลอดภัย</b> — คะแนนดาวลดลงตั้งแต่ช่วงแรกสุด "
                "ทุก 5 จุดของอัตราลาออกแลกมาด้วยคะแนนราว 0.2 ดาว ไม่มีจุดที่ 'ยังไม่เป็นไร'"
            )
            insight(
                "<b>แต่อัตราการเข้าพักมีจุดหักชัดเจนที่ 40%</b> — ต่ำกว่านั้นทรงตัวราว 83.5% "
                "จากนั้นเริ่มไหลลง และพังที่ 65% ขึ้นไปเหลือ 71.7% "
                "ฝ่ายบุคคลกับ CFO จึงมีเส้นคนละเส้น"
            )
            insight(
                "<b>อัตราลาออกไม่ใช่แค่เงาของการจัดพยาบาลบาง</b> — ในกลุ่มที่จัดพยาบาลดีที่สุด "
                "(Q4) การลาออกจาก &lt;40% ไปเป็น 60%+ ยังทำให้คะแนนตกจาก 4.15 เหลือ 2.81 "
                "การแก้ที่การรักษาคนจึงเป็นคานงัดอิสระ และถูกกว่าการจ้างเพิ่ม"
            )
            with st.expander("ตารางสแกนจุดตัด — ถ้าจะขีดเส้นควรขีดตรงไหน"):
                st.dataframe(
                    turnover_scan(period_key, S, O, C, exclude_suspect).style.format({
                        "pct_below": "{:.1f}%", "rating_below": "{:.2f}",
                        "rating_above": "{:.2f}", "rating_gap": "{:.2f}",
                        "occupancy_below": "{:.1%}", "occupancy_above": "{:.1%}",
                        "occupancy_gap_pts": "{:.2f}",
                        "fines_per_bed_below": "${:,.0f}",
                        "fines_per_bed_above": "${:,.0f}",
                    }),
                    hide_index=True, width="stretch",
                )


# ---------------------------------------------------------------------
with tab_trend:
    st.subheader("BQ5 — แนวโน้ม 2562 ถึง 2569 ฟื้นจาก COVID แล้วหรือยัง")
    caveat(
        "ไม่มี snapshot ของปี 2563-2564 เลย หน้านี้จึงเป็นการเทียบ "
        "<b>สองปลาย</b> ไม่ใช่เส้นแนวโน้ม — บอกได้ว่าลงเอยตรงไหนเทียบกับจุดเริ่ม "
        "แต่บอกไม่ได้ว่าก้นเหวอยู่ตรงไหนหรือฟื้นตอนไหน"
    )

    # This tab is a two-endpoint comparison against the 2019 era, so selecting
    # the 2019 period in the sidebar would ask it to compare that period with
    # itself. Fall forward to the latest period instead and say so, rather than
    # rendering a page of zeros.
    first_period = int(periods["period_key"].iloc[0])
    last_period = int(periods["period_key"].iloc[-1])
    trend_to = period_key if period_key != first_period else last_period
    if trend_to != period_key:
        st.info(
            f"งวดที่เลือก ({period_labels[period_key].split()[0]}) เป็นจุดตั้งต้นของการเทียบอยู่แล้ว "
            f"หน้านี้จึงเทียบกับงวดล่าสุด ({period_labels[trend_to].split()[0]}) แทน"
        )

    pairs, cohort_context = cohort(trend_to, exclude_suspect)
    exit_stats = exits(trend_to)

    cols = st.columns(4)
    cols[0].metric("อัตราการเข้าพัก",
                   f"{cohort_context['occupancy_to']:.1%}",
                   delta=f"{cohort_context['occupancy_change_pts']:+.1f} จุด")
    cols[1].metric("ชั่วโมงพยาบาล/คน/วัน",
                   f"{cohort_context['nurse_hprd_to']:.2f}",
                   delta=f"{cohort_context['nurse_hprd_change_pct']:+.1f}%")
    cols[2].metric("คะแนนดาวเฉลี่ย", f"{cohort_context['rating_to']:.2f}",
                   delta=f"{cohort_context['rating_change']:+.2f}")
    cols[3].metric("ข้อบกพร่องเฉลี่ย", f"{cohort_context['deficiencies_to']:.1f}",
                   delta=f"{cohort_context['deficiencies_to'] - cohort_context['deficiencies_from']:+.1f}",
                   delta_color="inverse")

    left, right = st.columns(2)
    with left:
        show(charts.matched_cohort_slopes(cohort_context))
    with right:
        show(charts.rating_migration(pairs, cohort_context))

    insight(
        f"<b>ฟื้นเชิงพาณิชย์ ไม่ฟื้นเชิงคุณภาพ</b> — วัดจากสถานพยาบาลชุดเดียวกัน "
        f"{cohort_context['matched_facilities']:,} แห่ง อัตราการเข้าพักกลับมาสูงกว่าปี 2562 "
        f"เล็กน้อย ({cohort_context['occupancy_from']:.1%} → {cohort_context['occupancy_to']:.1%}) "
        f"แต่คะแนนดาวตก {abs(cohort_context['rating_change']):.2f} และข้อบกพร่องเพิ่ม "
        f"{(cohort_context['deficiencies_to']/cohort_context['deficiencies_from']-1)*100:.0f}%"
    )
    insight(
        f"<b>การฟื้นของอัตราการเข้าพักมาจากอุปทานหดตัว ไม่ใช่อุปสงค์กลับมา</b> — "
        f"มี {exit_stats['exited_facilities']:,} แห่ง ({exit_stats['exited_share_pct']:.1f}%) "
        f"ปิดตัวไป พาเตียงออกจากตลาด {exit_stats['exited_beds']:,} เตียง "
        f"และแห่งที่ปิดมีอัตราการเข้าพักเดิมเพียง {exit_stats['exited_occupancy_before']:.1%} "
        f"เทียบกับ {exit_stats['stayed_occupancy_before']:.1%} ของแห่งที่อยู่รอด "
        "ตัวหารออกจากตลาดไป อัตราส่วนจึงดูดีขึ้น"
    )
    insight(
        f"<b>ตกมากกว่าขึ้นเกือบสองเท่า</b> — {cohort_context['pct_declined']:.0f}% "
        f"คะแนนลดลง เทียบกับ {cohort_context['pct_improved']:.0f}% ที่ดีขึ้น "
        "และบ้านที่ได้ 1 ดาวในปี 2562 ยังได้ 1 ดาวอยู่ 38.6% ในปี 2569 "
        "ปัญหาคุณภาพจึงเป็นเรื่องเชิงโครงสร้าง ไม่ใช่เรื่องชั่วคราว"
    )

    st.markdown("#### ทุกงวดที่มีในคลัง (ไม่ได้คุมองค์ประกอบ)")
    st.caption(
        "ตารางนี้คือมุมที่คำนวณง่ายและอ่านผิดง่ายที่สุด — จำนวนสถานพยาบาลเปลี่ยนไปด้วย "
        "จึงต้องอ่านคู่กับกราฟด้านบนที่ล็อกชุดสถานพยาบาลไว้แล้ว"
    )
    st.dataframe(
        period_trend(exclude_suspect).style.format({
            "beds": "{:,.0f}", "residents": "{:,.0f}", "occupancy": "{:.2%}",
            "nurse_hprd": "{:.3f}", "rn_hprd": "{:.3f}", "avg_rating": "{:.3f}",
            "deficiencies": "{:.2f}", "facilities": "{:,.0f}",
        }),
        hide_index=True, width="stretch",
    )


# ---------------------------------------------------------------------
with tab_risk:
    st.subheader("BQ6 และ BQ7 — เฝ้าระวังรายแห่ง และการบังคับใช้กฎรายรัฐ")

    bq6_tab, bq7_tab = st.tabs(["BQ6 · รายชื่อเฝ้าระวัง", "BQ7 · ค่าปรับตามรัฐและเวลา"])

    with bq6_tab:
        top_n = st.slider("แสดงกี่แห่ง", 10, 200, 50, step=10)
        listing, watch_context = watchlist(
            period_key, S, O, C, exclude_suspect, top_n)

        cols = st.columns(4)
        cols[0].metric("คะแนนเสี่ยง ≥ 5", f"{watch_context['flagged_high']:,}")
        cols[1].metric("คะแนนตกในงวดล่าสุด", f"{watch_context['declining_count']:,}",
                       help=f"เทียบกับงวด {watch_context['trajectory_baseline']}")
        cols[2].metric("อยู่ในบัญชี Special Focus",
                       f"{watch_context['special_focus_count']:,}")
        cols[3].metric("จัดพยาบาลต่ำกว่าเกณฑ์ CMS",
                       f"{watch_context['below_floor_count']:,}")

        st.markdown("#### คะแนนเสี่ยงนี้เชื่อได้แค่ไหน — ทดสอบย้อนหลัง")
        st.caption(
            "ให้คะแนนจากข้อมูลงวดมกราคม 2562 แล้วไปดูว่าเกิดอะไรขึ้นจริงในปี 2566-2569 "
            "คะแนนไม่เคยเห็นผลลัพธ์เหล่านั้นเลย"
        )
        backtest_frame, backtest_context = backtest(exclude_suspect)
        show(charts.risk_backtest(backtest_frame, backtest_context))

        insight(
            f"<b>คะแนนแยกผลลัพธ์ได้จริง ล่วงหน้ากว่า 4 ปี</b> — กลุ่มคะแนน 0 ถูกปรับ "
            f"{backtest_frame.iloc[0]['pct_fined_2023_26']:.0f}% คิดเป็นค่าปรับ "
            f"${backtest_frame.iloc[0]['fines_per_bed_2023_26']:,.0f} ต่อเตียง "
            f"ส่วนกลุ่มคะแนน 6 ถูกปรับ {backtest_frame[backtest_frame.risk_score==6].iloc[0]['pct_fined_2023_26']:.0f}% "
            f"คิดเป็น ${backtest_frame[backtest_frame.risk_score==6].iloc[0]['fines_per_bed_2023_26']:,.0f} ต่อเตียง "
            f"— กลุ่มเสี่ยงสูงสุดมีอัตราถูกปรับ {backtest_context['lift_incidence']:.2f} เท่า "
            f"และมูลค่าค่าปรับ {backtest_context['lift_dollars']:.2f} เท่าของค่าเฉลี่ย"
        )
        insight(
            f"<b>ความเสี่ยงคงทน ไม่ใช่เรื่องชั่วคราว</b> — บ้านที่คะแนนเสี่ยง 6 ในปี 2562 "
            f"ยังได้คะแนนดาวเฉลี่ยเพียง 2.21 ในปี 2569 และปิดตัวไปแล้ว "
            f"{backtest_frame[backtest_frame.risk_score==6].iloc[0]['pct_exited_by_2026']:.0f}% "
            "การซื้อกิจการที่ติดธงเหล่านี้จึงต้องตั้งงบพลิกฟื้นไว้ ไม่ใช่หวังว่าจะดีขึ้นเอง"
        )

        st.markdown(f"#### รายชื่อเฝ้าระวัง {len(listing)} อันดับแรก")
        st.caption(
            "น้ำหนัก: Special Focus 3 · 1-2 ดาว 2 · เคยถูกปรับหนัก 2 · "
            "ข้อบกพร่องมาก 1 · ต่ำกว่า 3.48 ชม. 1 (เต็ม 9) — "
            "คอลัมน์ `risk_reasons` บอกว่าแต่ละแห่งติดธงอะไรบ้าง"
        )
        st.dataframe(
            listing.style.format({
                "beds": "{:,.0f}", "nurse_hprd": "{:.2f}", "turnover_pct": "{:.1f}",
                "fine_total": "${:,.0f}", "fines_per_bed": "${:,.0f}",
                "rating_change": "{:+.0f}", "deficiency_change": "{:+.0f}",
            }, na_rep="—"),
            hide_index=True, width="stretch", height=460,
        )
        st.download_button(
            "ดาวน์โหลดรายชื่อเฝ้าระวังเป็น CSV",
            listing.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"watchlist_{watch_context['period']}.csv",
            mime="text/csv",
        )

    with bq7_tab:
        state_frame, state_context = enforcement(period_key, exclude_suspect, 10)

        cols = st.columns(4)
        cols[0].metric("ค่าปรับรวมทั้งประเทศ",
                       f"${state_context['total_fines']/1e6:,.0f}M")
        cols[1].metric("ค่าปรับต่อเตียง (ประเทศ)",
                       f"${state_context['national_fines_per_bed']:,.0f}")
        cols[2].metric("รัฐที่เข้มที่สุด",
                       f"{state_context['hardest']} ${state_context['hardest_per_bed']:,.0f}")
        cols[3].metric("ช่วงห่างสูงสุด-ต่ำสุด",
                       f"{state_context['spread']:.0f} เท่า",
                       delta=f"ต่ำสุด {state_context['softest']} "
                             f"${state_context['softest_per_bed']:,.0f}",
                       delta_color="off")

        st.markdown("#### ค่าปรับตามช่วงเวลา (การวิเคราะห์ตามช่วงเวลา)")
        timeline_frame, timeline_context = timeline(S)
        show(charts.fines_timeline(timeline_frame, timeline_context))

        st.markdown("#### ความเข้มของการบังคับใช้กฎรายรัฐ")
        show(charts.enforcement_by_state(
            state_frame, state_context, top_n=20, highlight=list(selected_states)))

        insight(
            f"<b>ค่าปรับกระจุกตัวมาก</b> — 5 รัฐ ({', '.join(state_context['top5_states'])}) "
            f"กินค่าปรับ {state_context['top5_share_of_dollars']:.0f}% "
            f"ของทั้งประเทศ ทั้งที่มีเตียงรวมกันเพียง "
            f"{state_context['top5_share_of_beds']:.0f}%"
        )
        insight(
            f"<b>รัฐเดียวกัน กฎเดียวกัน แต่ความเสี่ยงต่างกัน {state_context['spread']:.0f} เท่า</b> — "
            f"Vermont ${state_context['hardest_per_bed']:,.0f} ต่อเตียง เทียบกับ Indiana "
            f"${state_context['softest_per_bed']:,.0f} ทั้งที่ใช้ระเบียบกลางชุดเดียวกัน "
            "ที่ตั้งจึงกำหนดความเสี่ยงด้านกฎระเบียบมากกว่าหลายสิ่งที่ผู้บริหารควบคุมได้เอง"
        )
        insight(
            "<b>บทลงโทษน้อยครั้งลง แต่หนักขึ้นมาก</b> — ค่าปรับเฉลี่ยต่อครั้งเพิ่มจาก "
            f"${timeline_context['avg_fine_by_year'][2023]:,.0f} ในปี 2566 เป็น "
            f"${timeline_context['avg_fine_by_year'][2025]:,.0f} ในปี 2568 (+77%) "
            f"ขณะที่จำนวนครั้งลดจาก {timeline_context['events_by_year'][2023]:,} เหลือ "
            f"{timeline_context['events_by_year'][2025]:,} ครั้ง"
        )
        caveat(
            "ไตรมาสท้ายของแต่ละยุคเป็นจุดกลวงบนกราฟ เพราะค่าปรับทยอยเข้าแฟ้มช้ากว่าวันที่ออกคำสั่ง "
            "ไม่ใช่การบังคับใช้ที่ลดลงจริง · ช่วงปี 2562-2565 ไม่มี snapshot ใดครอบคลุมเลย"
        )

        st.markdown("#### เจาะรายรัฐ (drill-down)")
        drill_state = st.selectbox(
            "เลือกรัฐเพื่อดูสถานพยาบาลที่ถูกปรับหนักที่สุด",
            options=list(state_frame["state_code"]),
            index=list(state_frame["state_code"]).index(state_context["hardest"]),
        )
        drill, _ = queries.facility_roster(
            get_connection(), period_key=period_key, states=[drill_state],
            exclude_suspect=exclude_suspect)
        drill = drill.nlargest(25, "fine_total")[[
            "ccn", "provider_name", "city", "ownership_group", "chain_size_band",
            "beds", "rating", "nurse_hprd", "turnover_pct",
            "fine_total", "fine_events", "fines_per_bed"]]
        st.dataframe(
            drill.style.format({
                "beds": "{:,.0f}", "nurse_hprd": "{:.2f}", "turnover_pct": "{:.1f}",
                "fine_total": "${:,.0f}", "fines_per_bed": "${:,.0f}",
            }, na_rep="—"),
            hide_index=True, width="stretch", height=380,
        )
        st.dataframe(
            state_frame.style.format({
                "beds": "{:,.0f}", "fines": "${:,.0f}",
                "fines_per_bed": "${:,.0f}", "avg_fine": "${:,.0f}",
                "pct_facilities_fined": "{:.1f}%", "avg_rating": "{:.2f}",
            }),
            hide_index=True, width="stretch", height=320,
        )


# ---------------------------------------------------------------------
with tab_reco:
    st.subheader("ข้อเสนอแนะทางธุรกิจ")
    st.caption(
        "ทุกข้อผูกกับคำถามที่คำนวณไว้แล้วในแท็บก่อนหน้า และระบุตัวเลขที่ใช้ตัดสินใจกำกับไว้"
    )

    recommendations = [
        ("1", "เข้าตลาด New Mexico, Virginia, South Carolina ก่อน",
         "BQ1", "ทั้งสามรัฐมีเตียงต่อผู้สูงอายุพันคนต่ำกว่าค่าเฉลี่ยประเทศ (25.6) "
                "อัตราการเข้าพักสูงกว่า 83% และคะแนนคู่แข่งต่ำกว่า 3.00 ดาว "
                "ครบทั้งอุปสงค์เหลือและคู่แข่งอ่อน · เลี่ยง Iowa, Indiana, Arkansas "
                "ที่เตียงล้นและเข้าพักต่ำพร้อมกัน"),
        ("2", "ตั้งเพดานการจัดพยาบาลไว้ที่ 3.48-3.75 ชั่วโมง ไม่ใช่สูงกว่านั้น",
         "BQ3", "ใต้ 3.48 ชั่วโมง ค่าปรับต่อเตียง $404 เทียบกับ $268 เมื่อถึงเกณฑ์ "
                "แต่เหนือ 3.75 ชั่วโมง เส้นค่าปรับแบนแล้ว ($264, $283, $232) "
                "งบก้อนถัดไปควรไปที่บ้านที่ยังอยู่ใต้ 3.48 (36% ของทั้งหมด) "
                "ไม่ใช่เติมชั่วโมงให้บ้านที่ผ่านเกณฑ์แล้ว"),
        ("3", "ทำโครงการรักษาคนโดยตั้งเป้าอัตราลาออกต่ำกว่า 40%",
         "BQ4", "อัตราการเข้าพักทรงตัวที่ราว 83.5% ตราบใดที่ลาออกต่ำกว่า 40% "
                "แล้วเริ่มไหลลงทันทีเหนือเส้นนั้น ที่ 65% ขึ้นไปเหลือ 71.7% "
                "และผลนี้เป็นอิสระจากจำนวนชั่วโมง — แม้ในกลุ่มที่จัดพยาบาลดีที่สุด "
                "การลาออกสูงก็ยังฉุดคะแนนจาก 4.15 เหลือ 2.81"),
        ("4", "ตั้งงบพลิกฟื้นทุกครั้งที่ซื้อกิจการที่ติดธงเสี่ยง",
         "BQ6", "คะแนนเสี่ยงที่ให้ไว้ในปี 2562 ทำนายค่าปรับปี 2566-2569 ได้จริง "
                "กลุ่มเสี่ยงสูงถูกปรับ 1.29 เท่าและจ่ายค่าปรับต่อเตียง 1.52 เท่าของค่าเฉลี่ย "
                "และบ้านที่เสี่ยงสูงยังได้เพียง 2.21 ดาวหลังผ่านไป 7 ปี "
                "ราคาซื้อที่ดูถูกจึงมักมาพร้อมภาระที่ยังไม่หมดอายุ"),
        ("5", "ถ่วงน้ำหนักความเสี่ยงด้านกฎระเบียบตามรัฐเวลาประเมินดีล",
         "BQ7", "ค่าปรับต่อเตียงต่างกัน 24 เท่าระหว่างรัฐที่ใช้กฎกลางชุดเดียวกัน "
                "Vermont $1,364 เทียบ Indiana $57 · Illinois เป็นกรณีที่ต้องระวังที่สุด "
                "เพราะใหญ่ด้วยและเข้มด้วย ($81.7M, 71% ของสถานพยาบาลเคยถูกปรับ)"),
        ("6", "อย่าใช้อัตราการเข้าพักที่ฟื้นตัวเป็นสัญญาณว่าอุปสงค์กลับมา",
         "BQ5", "อัตราการเข้าพักกลับไปเหนือระดับปี 2562 ได้เพราะมี 1,282 แห่ง "
                "(98,725 เตียง) ออกจากตลาด และแห่งที่ออกมีอัตราการเข้าพักเดิมเพียง 64.9% "
                "จำนวนผู้พักอาศัยจริงยังลดลง การขยายกำลังการผลิตจึงต้องดูอุปสงค์รายรัฐ "
                "ไม่ใช่ตัวเลขรวมของประเทศ"),
        ("7", "ต่อรองราคาซื้อกิจการ for-profit ด้วยต้นทุนการยกระดับที่คำนวณได้",
         "BQ2", "For-profit ต่ำกว่า Non-profit 0.77 ดาว จัดพยาบาลน้อยกว่า 16% "
                "และจ่ายค่าปรับต่อเตียง 1.57 เท่า การยกให้เท่าค่าเฉลี่ยจึงมีต้นทุนที่ประเมินได้ "
                "และควรสะท้อนอยู่ในราคา ไม่ใช่รับมาแล้วค่อยพบทีหลัง"),
        ("8", "อย่าคาดหวังผลประหยัดต่อขนาดด้านคุณภาพจากการควบรวมเป็นเครือใหญ่",
         "BQ8", "ชั่วโมงพยาบาลลดลงทุกขั้นตามขนาดเครือใน<b>ทั้งสาม</b>กลุ่มเจ้าของ "
                "จึงไม่ใช่ผลของการเป็น for-profit · สิ่งที่เครือใหญ่ได้เปรียบจริงคือ "
                "อัตราการเข้าพักที่สูงกว่า 1.6 จุด ซึ่งเป็นความได้เปรียบด้านการตลาด "
                "ไม่ใช่ด้านการดูแล"),
    ]

    for number, title, question, body in recommendations:
        with st.container(border=True):
            st.markdown(f"**{number}. {title}**  &nbsp;`{question}`")
            st.markdown(body, unsafe_allow_html=True)

    st.divider()
    st.subheader("ข้อจำกัดที่ต้องบอกไปพร้อมกับข้อเสนอแนะ")
    for item in [
        "**ไม่มี snapshot ปี 2563-2565** — วิเคราะห์แนวโน้มได้แค่สองปลาย "
        "บอกจุดต่ำสุดหรือจังหวะการฟื้นไม่ได้",
        "**ข้อมูลเครือมีเฉพาะปี 2569** — แยกไม่ได้ว่าเครือทำให้บ้านแย่ลง "
        "หรือเครือไปซื้อบ้านที่แย่อยู่แล้ว",
        "**คะแนนดาวเป็นไม้บรรทัดที่ CMS ปรับเกณฑ์เองระหว่างทาง** — "
        "คะแนนที่ตก 0.39 จึงปนการเปลี่ยนเกณฑ์กับการเปลี่ยนคุณภาพจริง "
        "ข้อสรุปเรื่องคุณภาพจึงยืนบนจำนวนข้อบกพร่องและค่าปรับเป็นหลัก",
        "**ค่าปรับเป็นหน้าต่างหมุน 3 ปี ไม่ใช่ยอดสะสม** — ค่าปรับ 'ลดลง' "
        "ระหว่างงวดได้เองโดยไม่ได้แปลว่าข้อมูลผิด",
        "**อัตราการลาออกมีเฉพาะยุค 2569** — BQ4 จึงตอบได้เฉพาะภาพตัดขวาง "
        "ไม่ใช่การเปลี่ยนแปลงตามเวลา",
    ]:
        st.markdown(f"- {item}")

st.divider()
st.caption(
    "ElderCare Insight · Group 01 · ข้อมูล CMS Nursing Home Compare, SNF VBP "
    "และ US Census · คลังข้อมูล DuckDB สร้างจาก `02_ETL/run_dims.py` และ "
    "`run_facts.py` · ทุกตัวเลขบนหน้านี้เรียกผ่าน `queries.py` ซึ่งโน้ตบุ๊กใน "
    "`04_Dashboard/` ใช้ร่วมกัน"
)
