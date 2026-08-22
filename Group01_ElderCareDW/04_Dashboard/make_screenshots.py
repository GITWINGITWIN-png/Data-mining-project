"""Capture the dashboard screenshots the report needs.

    streamlit run app_mpl.py --server.port 8899 &
    python make_screenshots.py

Shots go to `screenshots/`. The assignment requires dashboard images in which
the chart titles, axes and numbers are legible, so each is taken full-page at
desktop width with a 2x device scale.

Two views are captured deliberately: the unfiltered national view, and the
same dashboard with filters applied, which is what evidences the interactive
controls. The filtered capture asserts that a headline measure actually
changed before it shoots — typing into a Streamlit multiselect without
clicking the option leaves the filter unset, and the screenshot would then
claim an interaction that never happened.
"""

import time

from playwright.sync_api import sync_playwright

URL = "http://localhost:8899"
OUT = "screenshots"

TABS = [
    ("01_overview", "📊 ภาพรวม"),
    ("02_market", "🗺️ ตลาด (BQ1)"),
    ("03_operator", "🏢 ผู้ประกอบการ (BQ2, BQ8)"),
    ("04_workforce", "👩‍⚕️ กำลังคน (BQ3, BQ4)"),
    ("05_trend", "📈 แนวโน้ม (BQ5)"),
    ("06_risk", "⚠️ ความเสี่ยง (BQ6, BQ7)"),
    ("07_reco", "💡 ข้อเสนอแนะ"),
]


def choose(page, label: str, option: str) -> None:
    """Pick one option from a Streamlit multiselect.

    Escape first: a dropdown left open from the previous selection overlays the
    sidebar and the next combobox then fails to resolve at all. The locator is
    re-queried every call because Streamlit reruns after each change and
    replaces the element any earlier handle pointed at.
    """
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    box = page.get_by_role("combobox", name=label)
    box.scroll_into_view_if_needed()
    box.click()
    box.fill(option)
    page.get_by_role("option", name=option, exact=True).first.click()
    page.keyboard.press("Escape")
    time.sleep(4)


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.firefox.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1200},
                                device_scale_factor=2)
        page.goto(URL, wait_until="networkidle", timeout=120_000)
        page.wait_for_selector("[data-testid='stMetricValue']", timeout=120_000)
        time.sleep(6)

        for name, label in TABS:
            page.get_by_role("tab", name=label).click()
            time.sleep(5)
            # Nudge the scroll so every matplotlib figure has been painted
            # before the full-page capture.
            page.mouse.wheel(0, 400)
            time.sleep(1)
            page.mouse.wheel(0, -400)
            time.sleep(2)
            page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
            print("captured", name)

        page.get_by_role("tab", name=TABS[0][1]).click()
        time.sleep(2)
        before = page.locator("[data-testid='stMetricValue']").first.inner_text()
        choose(page, "รัฐ", "IL")
        choose(page, "กลุ่มการถือครอง", "For profit")
        time.sleep(6)
        after = page.locator("[data-testid='stMetricValue']").first.inner_text()
        if before == after:
            raise SystemExit(
                f"filters did not take effect (occupancy stayed {before}) — "
                "the screenshot would claim an interaction that never happened"
            )
        print(f"filter applied: occupancy {before} -> {after}")
        page.screenshot(path=f"{OUT}/08_filtered_IL_forprofit.png", full_page=True)
        print("captured 08_filtered_IL_forprofit")

        page.get_by_role("tab", name="⚠️ ความเสี่ยง (BQ6, BQ7)").click()
        time.sleep(7)
        page.screenshot(path=f"{OUT}/09_filtered_watchlist.png", full_page=True)
        print("captured 09_filtered_watchlist")
        browser.close()


if __name__ == "__main__":
    main()
