"""ElderCare Insight — interactive dashboard.

    streamlit run app.py

Structure follows the brief: a strip of summary measures, then one tab per
business question, then the insight panel, then a data-quality tab that shows
what the warehouse is missing rather than hiding it.

Every chart reads the views in 03_Data_Warehouse/views.sql through queries.py.
Nothing here re-derives a measure.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import insights
import queries

st.set_page_config(
    page_title="ElderCare Insight",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Colour-blind-safe qualitative palette, used for every categorical chart so a
# group keeps its colour as the reader moves between tabs.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00"]
SEQ = "Blues"


@st.cache_resource
def get_con():
    return queries.connect()


# Filters is hashed through its own key() — see queries.Filters.key. The
# connection is prefixed with an underscore so Streamlit skips hashing it; a
# DuckDB connection is a live handle, not a value, and caching on it is
# meaningless.
#
# `fn_name` must NOT start with an underscore. Streamlit excludes
# underscore-prefixed parameters from the cache key, so naming it `_fn_name`
# makes every call with the same filter collide: trend() would be handed back
# whatever kpis() returned. That failure is silent — a wrong shape reaches the
# chart, not an error at the call site.
@st.cache_data(ttl=600, show_spinner=False,
               hash_funcs={queries.Filters: lambda f: f.key()})
def cached(fn_name: str, _con, *args, **kwargs):
    return getattr(queries, fn_name)(_con, *args, **kwargs)


def money(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


def note(text: str) -> None:
    st.caption(f"ℹ️ {text}")


# =====================================================================
#  Connection
# =====================================================================
try:
    con = get_con()
except FileNotFoundError as exc:
    st.error("ยังไม่มีคลังข้อมูล")
    st.code(str(exc))
    st.stop()

opts = queries.filter_options(con)

# =====================================================================
#  Sidebar — interactive controls (the brief asks for at least 2)
# =====================================================================
st.sidebar.title("🏥 ElderCare Insight")
st.sidebar.caption("คลังข้อมูลสถานดูแลผู้สูงอายุ · ข้อมูลเปิด CMS")

period_labels = {k: v for k, v in opts["periods"]}
sel_periods = st.sidebar.multiselect(
    "งวดข้อมูล (Snapshot)",
    options=[k for k, _ in opts["periods"]],
    default=[k for k, _ in opts["periods"]],
    format_func=lambda k: period_labels[k],
    help="งวดที่โหลดเข้าคลัง อ้างอิงตาม Processing Date ของ CMS",
)
sel_states = st.sidebar.multiselect("รัฐ", options=opts["states"], default=[])
sel_own = st.sidebar.multiselect("รูปแบบการถือครอง", options=opts["ownership"], default=[])
sel_chain = st.sidebar.multiselect("ขนาดเครือ", options=opts["chain_bands"], default=[])

include_suspect = st.sidebar.toggle(
    "รวมแถวที่ติดธงน่าสงสัย (is_suspect)",
    value=True,
    help="กฎ Q4 ติดธงแถวที่ค่าผิดวิสัยแทนการลบทิ้ง ปิดสวิตช์นี้เพื่อดูว่าผลลัพธ์เปลี่ยนไปแค่ไหน",
)

f = queries.Filters(
    periods=sel_periods,
    states=sel_states,
    ownership=sel_own,
    chain_bands=sel_chain,
    include_suspect=include_suspect,
)

if not sel_periods:
    st.warning("เลือกอย่างน้อยหนึ่งงวดข้อมูลจากแถบด้านซ้าย")
    st.stop()

active = [
    f"{len(sel_states)} รัฐ" if sel_states else None,
    f"การถือครอง: {', '.join(sel_own)}" if sel_own else None,
    f"เครือ: {', '.join(sel_chain)}" if sel_chain else None,
    "ไม่รวมแถวน่าสงสัย" if not include_suspect else None,
]
st.sidebar.markdown("---")
st.sidebar.caption("ตัวกรองที่ใช้อยู่: " + (" · ".join(x for x in active if x) or "ทั้งหมด"))

# =====================================================================
#  Summary measures (the brief asks for at least 3)
# =====================================================================
st.title("ElderCare Insight — แดชบอร์ดคลังข้อมูลสถานดูแลผู้สูงอายุ")

k = cached("kpis", con, f)
if k.empty:
    st.warning("ไม่มีข้อมูลภายใต้ตัวกรองนี้")
    st.stop()

st.caption(f"ตัวเลขสรุปคำนวณจากงวดล่าสุดในตัวกรอง: **{k['latest_period']}**")

c = st.columns(6)
c[0].metric("สถานที่", f"{int(k['facilities']):,}")
c[1].metric("M1 อัตราการเข้าพัก", f"{k['m1_occupancy_rate']:.1%}",
            help="SUM(ผู้พักอาศัย) / SUM(เตียง) — ไม่ใช่ค่าเฉลี่ยของอัตราส่วนรายแห่ง")
c[2].metric("M3 ชม.พยาบาล/คน/วัน", f"{k['m3_nurse_hprd']:.3f}",
            help="ถ่วงน้ำหนักด้วย M2 วัน-ผู้พักอาศัย")
c[3].metric("M4 อัตราการลาออก",
            "n/a" if pd.isna(k["m4_turnover_pct"]) else f"{k['m4_turnover_pct']:.1f}%",
            help="ถ่วงน้ำหนักด้วย M2 · ยุค 2562 ไม่มีคอลัมน์นี้")
c[4].metric("M7 คะแนนดาวเฉลี่ย",
            "n/a" if pd.isna(k["m7_avg_overall_rating"]) else f"{k['m7_avg_overall_rating']:.2f}")
c[5].metric("M5 ค่าปรับสะสม", money(k["m5_fine_usd"]),
            help=f"{int(k['m6_penalty_events']):,} เหตุการณ์ · สะสมทุกปีในคลัง ไม่ใช่เฉพาะงวดนี้")

if int(k["suspect_rows"]):
    note(f"งวดนี้มีแถวติดธงน่าสงสัย {int(k['suspect_rows']):,} แถว "
         f"({k['suspect_rows'] / k['facilities']:.2%}) — ปิดสวิตช์ในแถบซ้ายเพื่อดูผลเมื่อไม่รวม")

st.markdown("---")

# =====================================================================
#  Tabs — one per business question
# =====================================================================
tabs = st.tabs([
    "BQ5 แนวโน้ม", "BQ7 ค่าปรับ", "BQ2 การถือครอง", "BQ8 เครือ",
    "BQ3 ชั่วโมงพยาบาล", "BQ4 การลาออก", "BQ1 ตลาดรายรัฐ", "BQ6 เฝ้าระวัง",
    "💡 Insight", "🔍 คุณภาพข้อมูล",
])

# ---------------------------------------------------------------------
#  BQ5 — time series
# ---------------------------------------------------------------------
with tabs[0]:
    st.subheader("BQ5 · แนวโน้มการเข้าพัก ชั่วโมงพยาบาล และคะแนนดาว")
    tr = cached("trend", con, f)
    if len(tr) < 2:
        st.info("ต้องเลือกอย่างน้อยสองงวดจึงจะเห็นแนวโน้ม")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=tr["snapshot_year_month"], y=tr["m1_occupancy_rate"] * 100,
            name="M1 อัตราการเข้าพัก (%)", mode="lines+markers+text",
            text=[f"{v:.1%}" for v in tr["m1_occupancy_rate"]], textposition="top center",
            line=dict(color=PALETTE[0], width=3), marker=dict(size=11)))
        fig.add_trace(go.Scatter(
            x=tr["snapshot_year_month"], y=tr["m3_nurse_hprd"] * 10,
            name="M3 ชม.พยาบาล (×10)", mode="lines+markers",
            line=dict(color=PALETTE[1], width=3, dash="dash"), marker=dict(size=11)))
        fig.add_trace(go.Scatter(
            x=tr["snapshot_year_month"], y=tr["m7_avg_overall_rating"] * 10,
            name="M7 ดาวเฉลี่ย (×10)", mode="lines+markers",
            line=dict(color=PALETTE[2], width=3, dash="dot"), marker=dict(size=11)))
        fig.update_layout(
            height=420, hovermode="x unified",
            yaxis_title="ค่าที่ปรับสเกลให้อยู่ในกราฟเดียวกัน", xaxis_title="งวด (Processing Date)",
            legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, width="stretch")

        st.warning(
            f"**อ่านกราฟนี้อย่างระวัง** คลังมีเพียง {len(tr)} จุดเวลา และจุดแรกห่างจุดถัดไปราว 7 ปี "
            "เส้นที่ลากเชื่อมกันไม่ได้แปลว่ารู้ว่าระหว่างนั้นเกิดอะไรขึ้น "
            "ต้องโหลด snapshot รายไตรมาสให้ครบก่อนจึงจะตอบเรื่องการฟื้นตัวจาก COVID ได้จริง"
        )
        st.dataframe(
            tr[["snapshot_year_month", "covid_period", "facilities", "m1_occupancy_rate",
                "m3_nurse_hprd", "m4_turnover_pct", "m7_avg_overall_rating", "m8_deficiencies"]],
            width="stretch", hide_index=True,
            column_config={
                "snapshot_year_month": "งวด", "covid_period": "ช่วง COVID",
                "facilities": st.column_config.NumberColumn("สถานที่", format="%d"),
                "m1_occupancy_rate": st.column_config.NumberColumn("M1 เข้าพัก", format="%.2f%%"),
                "m3_nurse_hprd": st.column_config.NumberColumn("M3 ชม.พยาบาล", format="%.3f"),
                "m4_turnover_pct": st.column_config.NumberColumn("M4 ลาออก %", format="%.1f"),
                "m7_avg_overall_rating": st.column_config.NumberColumn("M7 ดาว", format="%.2f"),
                "m8_deficiencies": st.column_config.NumberColumn("M8 ข้อบกพร่อง", format="%d"),
            })

# ---------------------------------------------------------------------
#  BQ7 — penalties: the real time series, plus enforcement intensity
# ---------------------------------------------------------------------
with tabs[1]:
    st.subheader("BQ7 · ค่าปรับกระจุกตัวที่ไหนและช่วงเวลาใด")
    py = cached("penalties_by_year", con, f)
    if py.empty:
        st.info("ไม่มีข้อมูลค่าปรับภายใต้ตัวกรองนี้")
    else:
        fig = px.bar(py, x="penalty_year", y="m5_fine_usd",
                     labels={"penalty_year": "ปีที่ถูกลงโทษ", "m5_fine_usd": "M5 มูลค่าค่าปรับ (USD)"},
                     color_discrete_sequence=[PALETTE[0]])
        fig.update_layout(height=380, xaxis=dict(dtick=1))
        st.plotly_chart(fig, width="stretch")

        years = sorted(py["penalty_year"].dropna().astype(int))
        gaps = [y for y in range(min(years), max(years)) if y not in years]
        if gaps:
            st.warning(
                f"**ช่องว่างปี {min(gaps)}–{max(gaps)} ไม่ใช่ช่วงที่ไม่มีการลงโทษ** "
                "แฟ้ม Penalties ของ CMS เป็นหน้าต่างหมุน 3 ปี คลังจึงเห็นเฉพาะช่วงที่งวดซึ่งโหลดไว้ครอบคลุม "
                "ปีที่หายไปคือปีที่ไม่มีงวดใดในคลังรายงานถึง — แก้ได้ด้วยการโหลดงวดให้ถี่ขึ้น"
            )

        st.markdown("#### ความเข้มของการบังคับใช้ — ค่าปรับต่อเตียง")
        ps = cached("penalties_by_state", con, f, top_n=15)
        if not ps.empty:
            fig2 = px.bar(ps.sort_values("fine_per_bed"), x="fine_per_bed", y="state_code",
                          orientation="h", color="fine_per_bed", color_continuous_scale=SEQ,
                          labels={"fine_per_bed": "ค่าปรับต่อเตียง (USD)", "state_code": "รัฐ"},
                          hover_data={"m5_fine_usd": ":,.0f", "total_beds": ":,.0f", "events": ":,.0f"})
            fig2.update_layout(height=520, coloraxis_showscale=False)
            st.plotly_chart(fig2, width="stretch")
            note("หารด้วยจำนวนเตียง ไม่ใช่จำนวนสถานที่ — รัฐที่มีสถานที่ขนาดใหญ่จะดูถูกกำกับน้อยเกินจริงถ้าหารด้วยจำนวนแห่ง")

# ---------------------------------------------------------------------
#  BQ2 — comparison chart
# ---------------------------------------------------------------------
with tabs[2]:
    st.subheader("BQ2 · รูปแบบการถือครองให้ผลต่างกันอย่างไร")
    ow = cached("by_ownership", con, f)
    ow = ow[ow["grp"] != "Unknown"] if not ow.empty else ow
    if ow.empty:
        st.info("ไม่มีข้อมูลภายใต้ตัวกรองนี้")
    else:
        col1, col2 = st.columns(2)
        with col1:
            fig = px.bar(ow, x="grp", y="m7_avg_overall_rating", color="grp",
                         color_discrete_sequence=PALETTE, text_auto=".2f",
                         labels={"grp": "", "m7_avg_overall_rating": "M7 คะแนนดาวเฉลี่ย"})
            fig.update_layout(height=340, showlegend=False, yaxis_range=[0, 5])
            st.plotly_chart(fig, width="stretch")
        with col2:
            fig = px.bar(ow, x="grp", y="m3_nurse_hprd", color="grp",
                         color_discrete_sequence=PALETTE, text_auto=".3f",
                         labels={"grp": "", "m3_nurse_hprd": "M3 ชม.พยาบาล/คน/วัน"})
            fig.update_layout(height=340, showlegend=False)
            st.plotly_chart(fig, width="stretch")

        col3, col4 = st.columns(2)
        with col3:
            fig = px.bar(ow, x="grp", y="m4_turnover_pct", color="grp",
                         color_discrete_sequence=PALETTE, text_auto=".1f",
                         labels={"grp": "", "m4_turnover_pct": "M4 อัตราการลาออก (%)"})
            fig.update_layout(height=340, showlegend=False)
            st.plotly_chart(fig, width="stretch")
        with col4:
            fig = px.bar(ow, x="grp", y="fine_per_bed", color="grp",
                         color_discrete_sequence=PALETTE, text_auto=".0f",
                         labels={"grp": "", "fine_per_bed": "ค่าปรับสะสมต่อเตียง (USD)"})
            fig.update_layout(height=340, showlegend=False)
            st.plotly_chart(fig, width="stretch")

        note("ค่าปรับเป็นยอดสะสมทุกปีในคลัง ส่วนเตียงมาจากงวดล่าสุด ใช้เปรียบเทียบระหว่างกลุ่มได้ แต่ไม่ใช่อัตราต่อปี")

# ---------------------------------------------------------------------
#  BQ8 — chain scale
# ---------------------------------------------------------------------
with tabs[3]:
    st.subheader("BQ8 · เครือขนาดใหญ่ได้เปรียบจริงหรือไม่")
    ch = cached("by_chain_band", con, f)
    ch = ch[ch["grp"].notna()] if not ch.empty else ch
    if ch.empty:
        st.info("ไม่มีข้อมูลภายใต้ตัวกรองนี้")
    else:
        fig = px.scatter(
            ch, x="m3_nurse_hprd", y="m7_avg_overall_rating",
            size="facilities", color="grp", color_discrete_sequence=PALETTE,
            text="grp", size_max=70,
            labels={"m3_nurse_hprd": "M3 ชม.พยาบาล/คน/วัน (แกนต้นทุนแรงงาน)",
                    "m7_avg_overall_rating": "M7 คะแนนดาวเฉลี่ย (แกนคุณภาพ)", "grp": "ขนาดเครือ"},
            hover_data={"facilities": ":,.0f", "m4_turnover_pct": ":.1f"})
        fig.update_traces(textposition="top center")
        fig.update_layout(height=480, showlegend=False)
        st.plotly_chart(fig, width="stretch")
        note("ขนาดวงกลม = จำนวนสถานที่ · มุมขวาบนคือ “จ้างมากและคุณภาพดี” มุมซ้ายล่างคือ “กดต้นทุนแล้วคุณภาพตก”")

        st.dataframe(
            ch[["grp", "facilities", "chains", "m1_occupancy_rate", "m3_nurse_hprd",
                "m4_turnover_pct", "m7_avg_overall_rating", "fine_per_bed"]]
            if "chains" in ch.columns else
            ch[["grp", "facilities", "m1_occupancy_rate", "m3_nurse_hprd",
                "m4_turnover_pct", "m7_avg_overall_rating", "fine_per_bed"]],
            width="stretch", hide_index=True)

# ---------------------------------------------------------------------
#  BQ3 — staffing payoff
# ---------------------------------------------------------------------
with tabs[4]:
    st.subheader("BQ3 · ชั่วโมงพยาบาลสัมพันธ์กับคะแนนและค่าปรับอย่างไร จุดคุ้มอยู่ตรงไหน")
    hb = cached("hprd_bands", con, f)
    if hb.empty:
        st.info("ไม่มีข้อมูลภายใต้ตัวกรองนี้")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=hb["hprd_band"], y=hb["facilities"], name="จำนวนสถานที่",
                             marker_color="#D9E4F0", yaxis="y2"))
        fig.add_trace(go.Scatter(x=hb["hprd_band"], y=hb["m7_avg_overall_rating"],
                                 name="M7 ดาวเฉลี่ย", mode="lines+markers+text",
                                 text=[f"{v:.2f}" for v in hb["m7_avg_overall_rating"]],
                                 textposition="top center",
                                 line=dict(color=PALETTE[0], width=3), marker=dict(size=11)))
        fig.update_layout(
            height=440, xaxis_title="แบนด์ชั่วโมงพยาบาลต่อผู้พักอาศัยต่อวัน",
            yaxis=dict(title="M7 คะแนนดาวเฉลี่ย", range=[0, 5]),
            yaxis2=dict(title="จำนวนสถานที่", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=1.12), hovermode="x unified")
        st.plotly_chart(fig, width="stretch")
        note("แท่งสีอ่อนคือจำนวนสถานที่ในแต่ละแบนด์ — แบนด์ปลายสองข้างมีตัวอย่างน้อยกว่า ค่าเฉลี่ยจึงแกว่งกว่า")

        st.dataframe(
            hb[["hprd_band", "facilities", "m1_occupancy_rate", "m4_turnover_pct",
                "m7_avg_overall_rating", "avg_health_rating", "m8_deficiencies"]],
            width="stretch", hide_index=True)

# ---------------------------------------------------------------------
#  BQ4 — turnover
# ---------------------------------------------------------------------
with tabs[5]:
    st.subheader("BQ4 · อัตราการลาออกสูงแค่ไหนจึงเริ่มฉุดคุณภาพและอัตราการเข้าพัก")
    tb = cached("turnover_bands", con, f)
    if tb.empty:
        st.info("ไม่มีข้อมูลอัตราการลาออกภายใต้ตัวกรองนี้ (ยุค 2562 ไม่มีคอลัมน์นี้)")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=tb["turnover_band"], y=tb["m7_avg_overall_rating"],
                                 name="M7 ดาวเฉลี่ย", mode="lines+markers",
                                 line=dict(color=PALETTE[0], width=3), marker=dict(size=11)))
        fig.add_trace(go.Scatter(x=tb["turnover_band"], y=tb["m1_occupancy_rate"] * 5,
                                 name="M1 อัตราการเข้าพัก (สเกล ×5)", mode="lines+markers",
                                 line=dict(color=PALETTE[1], width=3, dash="dash"),
                                 marker=dict(size=11)))
        fig.update_layout(height=420, xaxis_title="แบนด์อัตราการลาออกของพยาบาล",
                          yaxis_title="ค่าที่ปรับสเกลให้เทียบกันได้", hovermode="x unified",
                          legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, width="stretch")
        st.dataframe(
            tb[["turnover_band", "facilities", "m1_occupancy_rate", "m3_nurse_hprd",
                "m7_avg_overall_rating", "m8_deficiencies"]],
            width="stretch", hide_index=True)

# ---------------------------------------------------------------------
#  BQ1 — market
# ---------------------------------------------------------------------
with tabs[6]:
    st.subheader("BQ1 · รัฐใดน่าเข้าไปลงทุนที่สุด")
    mk = cached("market", con, f)
    if mk.empty:
        st.info("ไม่มีข้อมูลภายใต้ตัวกรองนี้")
    else:
        has_m10 = mk["m10_beds_per_1000_elderly"].notna().any()
        if not has_m10:
            st.error(
                "**M10 (เตียงต่อผู้สูงอายุพันคน) ยังคำนวณไม่ได้** — ยังไม่ได้โหลด "
                "`Ref_State_Population` หน้านี้จึงตอบ BQ1 ได้เพียงครึ่งเดียว: บอกได้ว่ารัฐไหน"
                "อุปทานตึง แต่บอกไม่ได้ว่ารัฐไหน **ยังมีที่ว่างสำหรับเตียงใหม่** "
                "เพราะอัตราการเข้าพักต่ำอาจแปลว่าอุปสงค์น้อยจริงก็ได้ "
                "· แก้ด้วยการรัน `02_ETL/population.py` (ไม่ต้องมี API key)"
            )
        big = mk[mk["facilities"] >= 20]
        fig = px.scatter(
            big, x="m1_occupancy_rate", y="m7_avg_overall_rating",
            size="total_certified_beds", color="census_region",
            color_discrete_sequence=PALETTE, hover_name="state_code", size_max=55,
            labels={"m1_occupancy_rate": "M1 อัตราการเข้าพัก (ความตึงของอุปทาน)",
                    "m7_avg_overall_rating": "M7 คะแนนดาวเฉลี่ยของคู่แข่ง",
                    "census_region": "ภูมิภาค"})
        fig.update_layout(height=480)
        st.plotly_chart(fig, width="stretch")
        note("มุมซ้ายล่าง = เข้าพักต่ำและคู่แข่งคุณภาพต่ำ คือช่องว่างที่น่าสนใจที่สุด "
             + ("· ยืนยันด้วย M10 ในตารางข้างล่าง: เข้าพักสูงคู่กับ M10 ต่ำ คือขาดเตียงจริง "
                if has_m10 else "แต่ยืนยันไม่ได้จนกว่าจะมีข้อมูลประชากร ")
             + "· แสดงเฉพาะรัฐที่มีอย่างน้อย 20 แห่ง")

        st.dataframe(
            mk[["state_code", "state_name", "census_region", "facilities", "total_certified_beds",
                "m1_occupancy_rate", "m3_nurse_hprd", "m7_avg_overall_rating",
                "pop_65plus", "m10_beds_per_1000_elderly"]],
            width="stretch", hide_index=True,
            column_config={
                "m1_occupancy_rate": st.column_config.NumberColumn("M1 เข้าพัก", format="%.2f%%"),
                "m10_beds_per_1000_elderly": st.column_config.NumberColumn(
                    "M10 เตียง/ผู้สูงอายุพันคน", format="%.2f",
                    help="เตียงที่รับรองแล้ว หารด้วยประชากร 65+ ต่อพันคน "
                         "จาก Ref_State_Population (ปีล่าสุดที่แหล่งข้อมูลมี)"),
            })

# ---------------------------------------------------------------------
#  BQ6 — watchlist
# ---------------------------------------------------------------------
with tabs[7]:
    st.subheader("BQ6 · สถานที่ใดกำลังเสื่อมคุณภาพและเสี่ยงถูกลงโทษ")
    s = cached("watchlist_summary", con, f)
    if not s["compared"]:
        st.info("ต้องมีอย่างน้อยสองงวดจึงจะเทียบการเปลี่ยนแปลงได้")
    else:
        c = st.columns(4)
        c[0].metric("เทียบได้", f"{s['compared']:,} แห่ง")
        c[1].metric("คะแนนลดลง", f"{s['declined']:,}", delta=f"-{s['declined'] / s['compared']:.1%}",
                    delta_color="inverse")
        c[2].metric("คะแนนดีขึ้น", f"{s['improved']:,}", delta=f"+{s['improved'] / s['compared']:.1%}")
        c[3].metric("เท่าเดิม", f"{s['unchanged']:,}")

        wl = cached("watchlist", con, f, limit=40)
        if not wl.empty:
            fig = px.scatter(
                wl, x="rating_delta", y="fines_usd", size="penalty_events",
                color="ownership_group", color_discrete_sequence=PALETTE,
                hover_name="provider_name", size_max=40,
                labels={"rating_delta": "คะแนนดาวที่เปลี่ยน (ติดลบ = แย่ลง)",
                        "fines_usd": "ค่าปรับสะสม (USD)", "ownership_group": "การถือครอง"})
            fig.update_layout(height=420)
            st.plotly_chart(fig, width="stretch")

            st.markdown("#### คิวตรวจเยี่ยม — เรียงตามคะแนนที่ร่วงและประวัติค่าปรับ")
            st.dataframe(
                wl[["ccn", "provider_name", "state_code", "ownership_group", "chain_name",
                    "rating_first", "rating_last", "rating_delta", "turnover_last",
                    "fines_usd", "penalty_events"]],
                width="stretch", hide_index=True,
                column_config={
                    "fines_usd": st.column_config.NumberColumn("ค่าปรับสะสม", format="$%.0f"),
                    "rating_delta": st.column_config.NumberColumn("Δ ดาว", format="%d"),
                })
            note("เทียบต่อ CCN ข้ามงวด ไม่ใช่ต่อ facility_key เพราะ facility_key เป็นคีย์แทนของ SCD2 "
                 "สถานที่ที่เปลี่ยนแอตทริบิวต์จะถือหลายค่าและจะจับคู่กับตัวเองไม่ได้")

# ---------------------------------------------------------------------
#  Insights
# ---------------------------------------------------------------------
with tabs[8]:
    st.subheader("💡 Business Insight และข้อเสนอแนะ")
    st.caption("คำนวณสดจากตัวกรองปัจจุบัน — ตัวเลขในข้อความมาจากคำสั่ง query เดียวกับกราฟด้านบน")

    items = insights.build(con, f)
    badge = {"solid": "🟢 หลักฐานหนักแน่น", "tentative": "🟡 หลักฐานยังบาง", "blocked": "🔴 ยังตอบไม่ได้"}
    for i, ins in enumerate(items, 1):
        with st.expander(f"**{i}. [{ins.bq}] {ins.title}**", expanded=(i <= 3)):
            st.markdown(f"<sub>{badge.get(ins.strength, '')}</sub>", unsafe_allow_html=True)
            st.markdown(ins.finding)
            st.success(f"**ข้อเสนอแนะ:** {ins.recommendation}")
            if ins.evidence:
                st.caption(f"ฐานข้อมูลที่ใช้: {ins.evidence}")
            for cav in ins.caveats:
                st.caption(f"⚠️ {cav}")

    st.info(f"รวม {len(items)} ข้อ (โจทย์กำหนดอย่างน้อย 5 ข้อ)")

# ---------------------------------------------------------------------
#  Data quality — what the warehouse does not have
# ---------------------------------------------------------------------
with tabs[9]:
    st.subheader("🔍 ขอบเขตและคุณภาพของข้อมูล")
    st.caption("หน้านี้มีไว้แสดงสิ่งที่คลัง **ไม่มี** ให้เห็นชัด ไม่ใช่ซ่อนไว้")

    st.markdown("#### คลังครอบคลุมแค่ไหน")
    st.dataframe(cached("coverage", con), width="stretch", hide_index=True)

    st.markdown("#### ทำไม M1 ต้องเป็น SUM/SUM ไม่ใช่ AVG ของอัตราส่วน")
    rc = cached("ratio_check", con)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=rc["snapshot_date"].astype(str).str[:7],
                         y=rc["correct_sum_over_sum"] * 100,
                         name="SUM/SUM (ถูก)", marker_color=PALETTE[0]))
    fig.add_trace(go.Bar(x=rc["snapshot_date"].astype(str).str[:7],
                         y=rc["wrong_avg_of_ratios"] * 100,
                         name="AVG ของอัตราส่วน (ผิด)", marker_color=PALETTE[5]))
    fig.update_layout(height=340, barmode="group", yaxis_title="อัตราการเข้าพัก (%)",
                      legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(rc, width="stretch", hide_index=True)
    note("ทั้งสองตัวเลข “ดูเหมือนอัตราการเข้าพัก” เท่ากัน นี่คือเหตุผลที่ไม่เก็บอัตราส่วนเป็นคอลัมน์ในตาราง Fact")

    st.markdown("#### กฎคุณภาพข้อมูลที่ทำงานจริงในการรัน ETL ครั้งล่าสุด")
    ql = cached("quality_log", con)
    if ql.empty:
        st.info("ไม่มีบันทึกกฎ Q ในการรันล่าสุด")
    else:
        st.dataframe(ql, width="stretch", hide_index=True)

    st.markdown("#### สิ่งที่ยังขาด")
    st.markdown("""
- **ข้อมูลเครือมีเพียง 6 งวดล่าสุดจาก 32 งวด** CMS เพิ่งเพิ่มคอลัมน์ `Chain Name` ในรุ่น 2025-07
  ก่อนหน้านั้น (2019-01 ถึง 2025-04) แฟ้ม ProviderInfo ไม่มีคอลัมน์เครือเลย
  ค่าปรับส่วนใหญ่ถูกพบครั้งแรกในงวดเหล่านั้น (การยุบซ้ำเก็บการพบครั้งแรกไว้) จึงมีราว 88%
  ที่เครือเป็น "ไม่ทราบ" — **ไม่ใช่ "ไม่สังกัดเครือ"**
  **BQ8 ทั้งข้อจึงตอบได้เฉพาะงวดล่าสุด** ไม่ใช่ตลอดช่วง 2019–2026
- **ยอดค่าปรับต่างจากคอลัมน์ของ CMS อยู่ 2 แห่ง** จาก 6,563 แห่ง รวม $4,126 จาก $459M
  (0.0009%) ยังหาสาเหตุไม่ได้ จึงปล่อยให้เทสต์ตกไว้แทนที่จะผ่อนเกณฑ์
- **ปีที่หายไปในกราฟค่าปรับ** เกิดจากหน้าต่างหมุน 3 ปีของแฟ้ม CMS ไม่ใช่ปีที่ไม่มีการลงโทษ
- **แหล่ง S3 (VBP) ยังไม่ได้ใช้**
    """)

st.markdown("---")
st.caption(
    "ข้อมูล: Centers for Medicare & Medicaid Services — Provider Data Catalog (Nursing Homes) · "
    "U.S. Census Bureau ACS · ทุก measure นิยามไว้ที่ `03_Data_Warehouse/views.sql` ที่เดียว"
)
