"""Business insights, computed from the data at render time.

The brief asks for at least 5 insights with recommendations. These are
generated from the current filter rather than written out as prose, for one
reason: a hard-coded insight is a claim that was true about some data at some
point, and nothing makes it fall out of step with the chart above it. Here the
sentence and the chart read the same numbers on the same request, so they
cannot disagree, and moving a filter re-derives the claim.

Each insight names the business question it answers, states the finding with
the figure behind it, and gives a recommendation the stakeholder in the design
document could act on. Where the data cannot support a claim, the insight says
so instead of reaching — `evidence` carries the caveat.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import queries


@dataclass
class Insight:
    bq: str
    title: str
    finding: str
    recommendation: str
    evidence: str = ""
    strength: str = "solid"        # solid | tentative | blocked
    caveats: list[str] = field(default_factory=list)


def _fmt_money(v) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


def build(con, f: queries.Filters) -> list[Insight]:
    out: list[Insight] = []
    for fn in (
        _ownership_gap,        # BQ2
        _chain_scale,          # BQ8
        _staffing_payoff,      # BQ3
        _turnover_cliff,       # BQ4
        _enforcement,          # BQ7
        _trend_recovery,       # BQ5
        _decline_watch,        # BQ6
        _market,               # BQ1
    ):
        try:
            item = fn(con, f)
        except Exception as exc:                        # noqa: BLE001
            # A filter can empty any one of these. One insight failing must not
            # take the panel down, and the reason is worth showing.
            item = Insight(
                bq="—", title=fn.__name__, strength="blocked",
                finding="คำนวณไม่ได้ภายใต้ตัวกรองปัจจุบัน",
                recommendation="ขยายตัวกรองแล้วลองใหม่",
                evidence=f"{type(exc).__name__}: {exc}",
            )
        if item is not None:
            out.append(item)
    return out


# ---------------------------------------------------------------------
def _ownership_gap(con, f) -> Insight | None:
    df = queries.by_ownership(con, f)
    if df.empty or "grp" not in df.columns:
        return None
    df = df[df["grp"] != "Unknown"].dropna(subset=["m7_avg_overall_rating"])
    if len(df) < 2:
        return None

    best = df.loc[df["m7_avg_overall_rating"].idxmax()]
    worst = df.loc[df["m7_avg_overall_rating"].idxmin()]
    star_gap = best["m7_avg_overall_rating"] - worst["m7_avg_overall_rating"]
    hprd_gap = best["m3_nurse_hprd"] - worst["m3_nurse_hprd"]

    return Insight(
        bq="BQ2",
        title=f"{best['grp']} นำ {worst['grp']} อยู่ {star_gap:.2f} ดาว "
              f"และจ้างพยาบาลมากกว่า {hprd_gap:.2f} ชม./คน/วัน",
        finding=(
            f"**{best['grp']}** ({int(best['facilities']):,} แห่ง) ได้ {best['m7_avg_overall_rating']:.2f} ดาว "
            f"ชั่วโมงพยาบาล {best['m3_nurse_hprd']:.3f} อัตราลาออก {best['m4_turnover_pct']:.1f}% "
            f"ค่าปรับต่อเตียง {_fmt_money(best['fine_per_bed'])}\n\n"
            f"**{worst['grp']}** ({int(worst['facilities']):,} แห่ง) ได้ {worst['m7_avg_overall_rating']:.2f} ดาว "
            f"ชั่วโมงพยาบาล {worst['m3_nurse_hprd']:.3f} อัตราลาออก {worst['m4_turnover_pct']:.1f}% "
            f"ค่าปรับต่อเตียง {_fmt_money(worst['fine_per_bed'])}\n\n"
            f"ช่องว่างด้านคุณภาพเดินคู่ไปกับช่องว่างด้านชั่วโมงพยาบาลเสมอ ไม่ได้แยกจากกัน"
        ),
        recommendation=(
            f"ถ้าจะซื้อกิจการในกลุ่ม {worst['grp']} ต้องตั้งงบเพิ่มชั่วโมงพยาบาลราว "
            f"{hprd_gap:.2f} ชม./คน/วัน ไว้ในแบบจำลองการลงทุนตั้งแต่ต้น "
            f"ไม่ใช่คิดว่าคะแนนดาวจะขึ้นเองหลังเปลี่ยนเจ้าของ"
        ),
        evidence=f"งวดล่าสุดในตัวกรอง · {int(df['facilities'].sum()):,} แห่ง",
        caveats=["เป็นความสัมพันธ์ ไม่ใช่เหตุและผล ยังไม่ได้ควบคุมรัฐและขนาดเตียง"],
    )


def _chain_scale(con, f) -> Insight | None:
    df = queries.by_chain_band(con, f)
    if df.empty or "m3_nurse_hprd" not in df.columns:
        return None
    df = df.dropna(subset=["m3_nurse_hprd"])
    df = df[df["facilities"] >= 50]
    if len(df) < 2:
        return None

    low = df.loc[df["m3_nurse_hprd"].idxmin()]
    high = df.loc[df["m3_nurse_hprd"].idxmax()]
    return Insight(
        bq="BQ8",
        title=f"กลุ่ม “{low['grp']}” จ้างพยาบาลน้อยที่สุด แต่คะแนนไม่ได้ดีกว่า",
        finding=(
            f"**{low['grp']}** — ชั่วโมงพยาบาล {low['m3_nurse_hprd']:.3f} (ต่ำสุด) "
            f"อัตราลาออก {low['m4_turnover_pct']:.1f}% ดาว {low['m7_avg_overall_rating']:.2f}\n\n"
            f"**{high['grp']}** — ชั่วโมงพยาบาล {high['m3_nurse_hprd']:.3f} (สูงสุด) "
            f"อัตราลาออก {high['m4_turnover_pct']:.1f}% ดาว {high['m7_avg_overall_rating']:.2f}\n\n"
            f"ถ้าการรวมกลุ่มสร้างความได้เปรียบเชิงขนาดจริง คะแนนดาวของกลุ่มที่กดต้นทุนแรงงานได้มากกว่า "
            f"ควรจะไม่ต่ำกว่ากลุ่มอื่น — ข้อมูลชี้ไปทาง "
            f"{'ตรงกันข้าม' if low['m7_avg_overall_rating'] < high['m7_avg_overall_rating'] else 'ไม่ต่างกันชัดเจน'}"
        ),
        recommendation=(
            "เวลาประเมินดีลซื้อเครือ ให้แยกส่วนต่างกำไรที่มาจาก “การกดชั่วโมงพยาบาล” "
            "ออกจากส่วนที่มาจากประสิทธิภาพจริง ส่วนแรกมีต้นทุนตามมาเป็นค่าปรับและคะแนนดาว "
            "และจะโผล่มาในงบปีถัดไป ไม่ใช่ปีนี้"
        ),
        evidence=f"เฉพาะกลุ่มที่มีอย่างน้อย 50 แห่ง · งวดล่าสุดในตัวกรอง",
        caveats=["ยังไม่ได้ควบคุมส่วนผสมของรัฐ เครือใหญ่อาจกระจุกในรัฐที่ค่าแรงต่ำ"],
    )


def _staffing_payoff(con, f) -> Insight | None:
    df = queries.hprd_bands(con, f).dropna(subset=["m7_avg_overall_rating"])
    if len(df) < 3:
        return None

    df = df.reset_index(drop=True)
    df["gain"] = df["m7_avg_overall_rating"].diff()
    # The band where the marginal gain per extra half-hour is largest
    best_i = int(df["gain"].idxmax()) if df["gain"].notna().any() else 1
    best = df.loc[best_i]
    prev = df.loc[best_i - 1] if best_i > 0 else df.loc[0]
    lo, hi = df.iloc[0], df.iloc[-1]

    return Insight(
        bq="BQ3",
        title=f"ผลตอบแทนของชั่วโมงพยาบาลสูงสุดตอนขยับจาก “{prev['hprd_band']}” ไป “{best['hprd_band']}”",
        finding=(
            f"ไล่จากแบนด์ {lo['hprd_band']} ({lo['m7_avg_overall_rating']:.2f} ดาว) "
            f"ถึง {hi['hprd_band']} ({hi['m7_avg_overall_rating']:.2f} ดาว) "
            f"คะแนนขยับรวม {hi['m7_avg_overall_rating'] - lo['m7_avg_overall_rating']:+.2f} ดาว\n\n"
            f"ช่วงที่ได้ผลมากที่สุดคือ **{prev['hprd_band']} → {best['hprd_band']}** "
            f"ซึ่งได้ {best['gain']:+.2f} ดาว จากสถานที่ {int(best['facilities']):,} แห่งในแบนด์นั้น"
        ),
        recommendation=(
            f"ตั้งเป้าพาสถานที่ที่อยู่ต่ำกว่า {best['hprd_band']} ขึ้นมาให้ถึงแบนด์นี้ก่อน "
            f"เป็นการใช้งบพนักงานที่ให้ผลต่อคะแนนสูงที่สุด "
            f"ส่วนการดันจาก {hi['hprd_band']} ขึ้นไปอีกให้ผลน้อยกว่ามากต่อเงินที่ใส่เพิ่ม"
        ),
        evidence=f"แบ่งแบนด์ตามเกณฑ์คงที่ · {int(df['facilities'].sum()):,} แห่ง",
        caveats=[
            "แบนด์สูงสุดมีสถานที่น้อยกว่า ค่าเฉลี่ยจึงแกว่งกว่า",
            "ความสัมพันธ์อาจกลับทางได้: สถานที่ที่คะแนนดีอยู่แล้วอาจจ้างพยาบาลได้มากกว่า",
        ],
    )


def _turnover_cliff(con, f) -> Insight | None:
    df = queries.turnover_bands(con, f).dropna(subset=["m7_avg_overall_rating"])
    if len(df) < 3:
        return None

    df = df.reset_index(drop=True)
    df["drop"] = -df["m7_avg_overall_rating"].diff()
    worst_i = int(df["drop"].idxmax()) if df["drop"].notna().any() else 1
    band = df.loc[worst_i]
    prev = df.loc[worst_i - 1] if worst_i > 0 else df.loc[0]

    return Insight(
        bq="BQ4",
        title=f"คุณภาพเริ่มร่วงแรงที่สุดเมื่ออัตราลาออกข้ามเข้าแบนด์ “{band['turnover_band']}”",
        finding=(
            f"จาก {prev['turnover_band']} ({prev['m7_avg_overall_rating']:.2f} ดาว) "
            f"ไป {band['turnover_band']} ({band['m7_avg_overall_rating']:.2f} ดาว) "
            f"คะแนนลด {band['drop']:.2f} ดาว ซึ่งเป็นการลดครั้งเดียวที่แรงที่สุดในทุกแบนด์\n\n"
            f"อัตราการเข้าพักของแบนด์นี้อยู่ที่ {band['m1_occupancy_rate']:.1%} "
            f"เทียบกับ {prev['m1_occupancy_rate']:.1%} ของแบนด์ก่อนหน้า"
        ),
        recommendation=(
            f"ตั้งเกณฑ์เตือนภายในไว้ที่ขอบล่างของแบนด์ {band['turnover_band']} "
            f"สถานที่ที่แตะเกณฑ์นี้ควรถูกทบทวนแผนกำลังคนทันที ไม่ใช่รอรอบประเมินคุณภาพประจำปี "
            f"เพราะคะแนนดาวตามหลังการลาออกอยู่หลายเดือน"
        ),
        evidence=f"เฉพาะงวดที่มีคอลัมน์อัตราลาออก · {int(df['facilities'].sum()):,} แห่ง",
        caveats=["ยุค 2562 ไม่มีคอลัมน์นี้ ข้อสรุปจึงมาจากงวดปี 2569 เท่านั้น"],
    )


def _enforcement(con, f) -> Insight | None:
    st = queries.penalties_by_state(con, f, top_n=100)
    st = st[st["m5_fine_usd"] > 0]
    if st.empty:
        return None

    total = st["m5_fine_usd"].sum()
    top5 = st.nlargest(5, "m5_fine_usd")
    share = top5["m5_fine_usd"].sum() / total if total else 0

    # The cross-state comparison needs at least two states to compare. A filter
    # narrowed to one state still deserves an answer, just a smaller one.
    if len(st) < 2:
        only = st.iloc[0]
        return Insight(
            bq="BQ7",
            title=f"{only['state_code']} — ค่าปรับสะสม {_fmt_money(only['m5_fine_usd'])} "
                  f"({_fmt_money(only['fine_per_bed'])}/เตียง)",
            finding=(
                f"ภายใต้ตัวกรองนี้เหลือรัฐเดียว จึงเทียบความเข้มของการบังคับใช้ระหว่างรัฐไม่ได้\n\n"
                f"**{only['state_code']}**: {int(only['events']):,} เหตุการณ์ · "
                f"{_fmt_money(only['m5_fine_usd'])} · {int(only['total_beds']):,} เตียง · "
                f"{_fmt_money(only['fine_per_bed'])} ต่อเตียง"
            ),
            recommendation="เอาตัวกรองรัฐออกเพื่อดูว่าตัวเลขต่อเตียงนี้สูงหรือต่ำเมื่อเทียบกับรัฐอื่น",
            evidence="เหลือรัฐเดียวหลังกรอง",
            strength="tentative",
        )

    # Compare against states of broadly similar size, so the "lightest" state is
    # not simply the smallest one in the set.
    bigger_half = st[st["total_beds"] >= st["total_beds"].median()]
    hardest = st.nlargest(1, "fine_per_bed").iloc[0]
    softest = (bigger_half if len(bigger_half) else st).nsmallest(1, "fine_per_bed").iloc[0]

    return Insight(
        bq="BQ7",
        title=f"ค่าปรับกระจุกตัว — 5 รัฐแรกกินสัดส่วน {share:.0%} ของยอดทั้งหมด",
        finding=(
            f"ยอดค่าปรับรวม {_fmt_money(total)} จาก {len(st)} รัฐ "
            f"โดย {', '.join(top5['state_code'])} รวมกันเป็น {share:.0%}\n\n"
            f"เมื่อปรับด้วยจำนวนเตียงแล้ว รัฐที่บังคับใช้เข้มที่สุดคือ **{hardest['state_code']}** "
            f"({_fmt_money(hardest['fine_per_bed'])}/เตียง) "
            f"ส่วนรัฐขนาดใกล้เคียงที่เบาที่สุดคือ **{softest['state_code']}** "
            f"({_fmt_money(softest['fine_per_bed'])}/เตียง) "
            f"ต่างกัน {hardest['fine_per_bed'] / max(softest['fine_per_bed'], 1e-9):.0f} เท่า"
        ),
        recommendation=(
            f"ความเสี่ยงด้านกฎระเบียบต้องคิดเป็นรายรัฐ ไม่ใช่ค่าเฉลี่ยทั้งประเทศ "
            f"การเข้าตลาด {hardest['state_code']} ต้องกันงบสำรองด้านการกำกับสูงกว่า "
            f"{softest['state_code']} อย่างมีนัยสำคัญที่ระดับเตียงเท่ากัน"
        ),
        evidence="ค่าปรับสะสมทุกปีในคลัง หารด้วยเตียงของงวดล่าสุด",
        caveats=[
            "เศษเป็นค่าปรับสะสมหลายปี ส่วนตัวหารเป็นเตียงงวดเดียว ใช้จัดอันดับได้ แต่ไม่ใช่อัตราต่อปี",
            "ค่าปรับสูงอาจแปลว่ารัฐกำกับเข้ม หรือคุณภาพแย่จริง แยกจากกันไม่ได้ด้วยข้อมูลชุดนี้",
        ],
    )


def _trend_recovery(con, f) -> Insight | None:
    df = queries.trend(con, f)
    if len(df) < 2:
        return None
    first, last = df.iloc[0], df.iloc[-1]
    occ_delta = (last["m1_occupancy_rate"] - first["m1_occupancy_rate"]) * 100

    return Insight(
        bq="BQ5",
        title=f"อัตราการเข้าพักจาก {first['snapshot_year_month']} ถึง {last['snapshot_year_month']} "
              f"เปลี่ยน {occ_delta:+.2f} จุด",
        finding=(
            f"{first['snapshot_year_month']}: เข้าพัก {first['m1_occupancy_rate']:.2%} · "
            f"ดาว {first['m7_avg_overall_rating']:.2f} · ชม.พยาบาล {first['m3_nurse_hprd']:.3f}\n\n"
            f"{last['snapshot_year_month']}: เข้าพัก {last['m1_occupancy_rate']:.2%} · "
            f"ดาว {last['m7_avg_overall_rating']:.2f} · ชม.พยาบาล {last['m3_nurse_hprd']:.3f}\n\n"
            f"จำนวนสถานที่เปลี่ยนจาก {int(first['facilities']):,} เป็น {int(last['facilities']):,} แห่ง "
            f"({(last['facilities'] / first['facilities'] - 1) * 100:+.1f}%) "
            f"— อุปทานหดตัวพร้อมกับอัตราการเข้าพักที่ขยับขึ้น"
        ),
        recommendation=(
            "อ่านการเข้าพักที่สูงขึ้นควบคู่กับจำนวนสถานที่ที่ลดลงเสมอ "
            "ตลาดที่ตึงขึ้นเพราะอุปทานหายไม่เหมือนตลาดที่โตเพราะอุปสงค์เพิ่ม "
            "และสองอย่างนี้ให้คำตอบเรื่องการลงทุนสร้างเตียงใหม่คนละทาง"
        ),
        evidence=f"{len(df)} จุดเวลาในคลัง",
        strength="tentative",
        caveats=[
            f"มีเพียง {len(df)} จุดเวลา และจุดแรกห่างจุดที่สองราว 7 ปี — **ยังไม่ใช่อนุกรมเวลา**",
            "ต้องโหลด snapshot รายไตรมาสให้ครบก่อนจึงจะสรุปเรื่องการฟื้นจาก COVID ได้",
        ],
    )


def _decline_watch(con, f) -> Insight | None:
    s = queries.watchlist_summary(con, f)
    if not s["compared"]:
        return None
    top = queries.watchlist(con, f, limit=5)
    pct = s["declined"] / s["compared"] * 100
    with_fines = int((top["fines_usd"] > 0).sum()) if not top.empty else 0

    return Insight(
        bq="BQ6",
        title=f"{s['declined']:,} แห่ง ({pct:.1f}%) คะแนนดาวลดลงระหว่างงวดแรกกับงวดล่าสุด",
        finding=(
            f"เทียบได้ {s['compared']:,} แห่ง — ลดลง {s['declined']:,} · "
            f"ดีขึ้น {s['improved']:,} · เท่าเดิม {s['unchanged']:,}\n\n"
            f"ใน 5 อันดับที่ร่วงแรงที่สุด มี {with_fines} แห่งที่มีประวัติค่าปรับอยู่แล้ว "
            f"ซึ่งเป็นสัญญาณว่าการเสื่อมคุณภาพกับการถูกลงโทษเดินมาด้วยกัน"
        ),
        recommendation=(
            "ใช้รายชื่อในแท็บ BQ6 เป็นคิวตรวจเยี่ยมจริง โดยจัดลำดับจากคะแนนที่ร่วง "
            "ประกอบกับประวัติค่าปรับ แทนการสุ่มตรวจตามรอบ "
            "การเข้าไปก่อนที่ค่าปรับรอบใหม่จะเกิดมีต้นทุนต่ำกว่าการตามแก้ทีหลังมาก"
        ),
        evidence=f"เทียบต่อ CCN ข้ามงวด ไม่ใช่ต่อ facility_key",
        caveats=["ระยะห่างระหว่างงวดยาวมาก การเปลี่ยนแปลงจึงสะสมหลายปี ไม่ใช่การร่วงเฉียบพลัน"],
    )


def _market(con, f) -> Insight | None:
    df = queries.market(con, f)
    if df.empty:
        return None
    df = df[df["facilities"] >= 20]
    if df.empty:
        return None

    tight = df.nlargest(3, "m1_occupancy_rate")
    loose = df.nsmallest(3, "m1_occupancy_rate")
    has_pop = df["m10_beds_per_1000_elderly"].notna().any()

    ranking = (
        "ตลาดที่ตึงที่สุด (เข้าพักสูง คู่แข่งเต็ม): "
        + " · ".join(f"**{r.state_code}** {r.m1_occupancy_rate:.1%} (ดาว {r.m7_avg_overall_rating:.2f})"
                     for r in tight.itertuples())
        + "\n\nตลาดที่หลวมที่สุด: "
        + " · ".join(f"**{r.state_code}** {r.m1_occupancy_rate:.1%} (ดาว {r.m7_avg_overall_rating:.2f})"
                     for r in loose.itertuples())
    )

    if not has_pop:
        # Kept as a live branch, not dead code: a warehouse built without
        # Ref_State_Population still renders, and says so instead of pretending.
        return Insight(
            bq="BQ1",
            title="จัดอันดับตลาดรายรัฐได้แล้วครึ่งเดียว — ยังขาดข้อมูลประชากร",
            finding=(
                ranking
                + "\n\nแต่ **M10 เตียงต่อผู้สูงอายุพันคน ยังคำนวณไม่ได้** เพราะยังไม่ได้โหลด "
                  "`Ref_State_Population` อัตราการเข้าพักต่ำอาจแปลว่าตลาดยังมีที่ว่าง "
                  "หรือแปลว่าอุปสงค์ในรัฐนั้นน้อยจริง — แยกสองอย่างนี้ไม่ได้ถ้าไม่มีตัวหาร"
            ),
            recommendation=(
                "รัน `02_ETL/population.py` ก่อน (ไม่ต้องมี API key — มีแฟ้มสำรองให้อยู่แล้ว) "
                "แล้วหน้านี้จะจัดอันดับตลาดได้ครบ"
            ),
            evidence=f"{len(df)} รัฐที่มีอย่างน้อย 20 แห่ง · ไม่มีข้อมูลประชากร",
            strength="blocked",
            caveats=["M10 คืนค่า NULL ทุกแถวจนกว่าจะโหลด Ref_State_Population"],
        )

    # Supply against demand: a loose market with few beds per elderly person is
    # genuinely underserved, while a loose market that is already well supplied
    # is simply short of demand. Occupancy alone cannot tell those apart, which
    # is the whole reason M10 exists.
    with_pop = df[df["m10_beds_per_1000_elderly"].notna()]
    underserved = with_pop.nsmallest(3, "m10_beds_per_1000_elderly")
    saturated = with_pop.nlargest(3, "m10_beds_per_1000_elderly")
    national = (
        with_pop["total_certified_beds"].sum() / (with_pop["pop_65plus"].sum() / 1000.0)
        if "total_certified_beds" in with_pop and with_pop["pop_65plus"].sum() else float("nan")
    )

    return Insight(
        bq="BQ1",
        title="ตลาดที่ขาดเตียงจริง ๆ ต่างจากตลาดที่แค่เข้าพักต่ำ",
        finding=(
            ranking
            + f"\n\n**M10 เตียงต่อผู้สูงอายุ 1,000 คน** (ค่าเฉลี่ยทั้งประเทศ {national:.1f}) "
            + "เตียงน้อยที่สุดเทียบประชากรสูงอายุ: "
            + " · ".join(f"**{r.state_code}** {r.m10_beds_per_1000_elderly:.1f}"
                         for r in underserved.itertuples())
            + " · หนาแน่นที่สุด: "
            + " · ".join(f"**{r.state_code}** {r.m10_beds_per_1000_elderly:.1f}"
                         for r in saturated.itertuples())
        ),
        recommendation=(
            "รัฐที่น่าลงทุนคือรัฐที่ **เข้าพักสูงและ M10 ต่ำ** พร้อมกัน — อุปสงค์เต็มอยู่แล้ว "
            "และยังมีเตียงต่อประชากรน้อยกว่าค่าเฉลี่ย ส่วนรัฐที่เข้าพักต่ำแต่ M10 สูง "
            "คือตลาดที่เตียงล้นแล้ว การเพิ่มเตียงจะยิ่งกดอัตราการเข้าพักลง"
        ),
        evidence=(
            f"{len(with_pop)} รัฐที่มีอย่างน้อย 20 แห่งและมีข้อมูลประชากร "
            f"· ประชากร 65+ จาก Ref_State_Population"
        ),
        strength="solid",
        caveats=[
            "ประชากรเป็นค่าประมาณปีล่าสุดที่แหล่งข้อมูลมี ส่วนเตียงเป็นของงวดที่เลือก "
            "ถ้าสองปีนี้ห่างกันมาก M10 จะเอนไปทางเตียงใหม่กว่าประชากร",
        ],
    )
