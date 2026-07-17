#!/usr/bin/env python3
"""Screenshot specific cards of 攻略.html (tab + card index) via headless
chromium — the review-feedback diagnosis loop (units rendering / #670+ /
battle bitmaps / triplicates)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

REPO = Path(__file__).resolve().parent.parent.parent


def main():
    tab = sys.argv[1] if len(sys.argv) > 1 else "ggunits"
    anchor = sys.argv[2] if len(sys.argv) > 2 else "#397"
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/guide_shot.png"
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1000,1600")
    opts.binary_location = "/usr/bin/chromium"
    from selenium.webdriver.chrome.service import Service
    d = webdriver.Chrome(options=opts, service=Service("/usr/bin/chromedriver"))
    try:
        d.get("file://" + str(REPO / "攻略.html"))
        time.sleep(3)
        d.execute_script(
            "var b=[...document.querySelectorAll('#tabs button')]"
            f".find(x=>x.dataset.v==='{tab}'); if(b) b.click();")
        time.sleep(4)
        # let lazy grids build fully
        for _ in range(6):
            d.execute_script("window.scrollBy(0, 4000);")
            time.sleep(1)
        # find the card whose index label matches the anchor
        el = d.execute_script(
            "var cards=[...document.querySelectorAll('.gcard')];"
            f"var c=cards.find(x=>(x._idx||x.querySelector('.gcard-ix').textContent)==='{anchor}');"
            "if(c){c.scrollIntoView({block:'start'});return c.getBoundingClientRect().top;}"
            "return null;")
        time.sleep(3)
        d.save_screenshot(out)
        print("anchor found:", el is not None, "->", out)
    finally:
        d.quit()


if __name__ == "__main__":
    main()
