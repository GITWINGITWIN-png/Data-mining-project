"""Smoke and interaction tests for the matplotlib dashboard (app_mpl.py).

Run with:  python test_dashboard.py

`streamlit.testing.v1.AppTest` executes app.py the way the server does, so
this catches the failures that only appear once a real filter combination is
selected — which is most of them. Every bug it has caught so far came from a
period or a filter that leaves some column entirely empty:

  * the 2019 era has no turnover column and no chain columns at all
  * asking for turnover bands there produced a frame with no columns, and the
    marginal-difference step raised KeyError on a name the user never sees
  * comparing the 2019 period against itself left nobody in the "exited"
    bucket of the capacity analysis

Widgets are addressed through `session_state` keys rather than by position:
the tab bodies create their own selectboxes and sliders, so an index-based
lookup silently points at a different widget as soon as a tab renders one more
control.
"""

from streamlit.testing.v1 import AppTest

#: Filter combinations worth asserting on. Each is a state the dashboard can
#: actually be put into from the sidebar, not a synthetic input.
CASES = [
    ("baseline — latest period, no filters", {}),
    ("2019 period — no turnover, no chain data", {"f_period": 20190101}),
    ("2019 + chain comparison — nothing to compare",
     {"f_period": 20190101, "f_segment": "chain_size_band"}),
    ("2019 + 13 ownership types",
     {"f_period": 20190101, "f_segment": "ownership_type"}),
    ("single small state", {"f_states": ["VT"]}),
    ("tiny slice — one state, one ownership group",
     {"f_states": ["AK"], "f_ownership": ["Government"]}),
    ("large chains only", {"f_chains": ["Large chain (50+)"]}),
    ("suspect rows included", {"f_suspect": False}),
    ("chain size comparison", {"f_segment": "chain_size_band"}),
    ("13 ownership types", {"f_segment": "ownership_type"}),
    ("several states", {"f_states": ["IL", "TX", "CA"]}),
]


def _reco_text(app) -> list[str]:
    """ข้อความข้อเสนอแนะที่แท็บ 💡 พิมพ์ออกมา

    แท็บนั้นใช้ `st.success` ขึ้นต้นด้วย "ข้อเสนอแนะ:" ส่วน `st.success` อีกที่หนึ่ง
    คือแถบ "กำลังกรอง" ใน sidebar จึงต้องกรองด้วยคำขึ้นต้น ไม่ใช่เอาทั้งหมด
    """
    return [e.value for e in app.success if "ข้อเสนอแนะ:" in e.value]


def test_insights_move_with_the_filter() -> bool:
    """ข้อเสนอแนะต้องเปลี่ยนเมื่อตัวกรองเปลี่ยน

    เทสต์นี้มีเพราะแท็บนี้เคยเป็นลิสต์ข้อความที่พิมพ์ตัวเลขฝังไว้ ตัวเลขจึงนิ่งสนิท
    ไม่ว่าจะกรองอย่างไร และชุดตรวจเดิมไม่มีข้อไหนจับได้เลย — ทุกข้อดูแค่ว่า
    หน้าเรนเดอร์ผ่านไหม ซึ่งลิสต์ข้อความคงที่ก็ผ่านสบาย ๆ
    """
    base = AppTest.from_file("app_mpl.py", default_timeout=300).run()
    base_text = _reco_text(base)

    filtered = AppTest.from_file("app_mpl.py", default_timeout=300).run()
    filtered.session_state["f_states"] = ["IL"]
    filtered.session_state["f_ownership"] = ["For profit"]
    filtered.run()
    filtered_text = _reco_text(filtered)

    if not base_text:
        print("FAIL  insights move with the filter: ไม่มีข้อเสนอแนะเลยในกรณีฐาน")
        return False
    if base_text == filtered_text:
        print("FAIL  insights move with the filter: "
              f"กรอง IL + For profit แล้วข้อความ {len(base_text)} ข้อเหมือนเดิมทุกตัวอักษร "
              "— แปลว่าตัวเลขถูกพิมพ์ฝังไว้ ไม่ได้คำนวณจากตัวกรอง")
        return False

    print(f"OK    insights move with the filter: "
          f"{len(base_text)} ข้อในกรณีฐาน → {len(filtered_text)} ข้อหลังกรอง และข้อความต่างกัน")
    return True


def main() -> int:
    failures = 0
    for label, state in CASES:
        app = AppTest.from_file("app_mpl.py", default_timeout=300).run()
        for key, value in state.items():
            app.session_state[key] = value
        app.run()

        if app.exception:
            failures += 1
            print(f"FAIL  {label}")
            for error in app.exception:
                print(f"      {error.value}")
            continue

        # A page that renders no measures has silently stopped early — which is
        # exactly what `st.stop()` inside a tab used to do.
        if not app.metric:
            failures += 1
            print(f"FAIL  {label}: rendered no measures")
            continue

        print(f"OK    {label}: {len(app.metric)} measures, "
              f"{len(app.dataframe)} tables")

    total = len(CASES) + 1
    if not test_insights_move_with_the_filter():
        failures += 1

    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
