#!/usr/bin/env python3
"""capture_briefing.py — drive a fresh boot to the first stage briefing and
screenshot it (settles the briefing render-path question empirically).

    .venv/bin/python test/live/capture_briefing.py <rom.nds> --out DIR
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--out", default="/tmp/briefing_cap")
    ap.add_argument("--presses", type=int, default=60)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    display = hz.pick_display()
    emu = hz.Emulator(display)
    try:
        hz.boot_to_title(emu, Path(args.rom))
        if not hz.goto_main_menu(emu, out):
            print("FAIL: no main menu")
            return 1
        # New Game -> intro -> ADV -> briefing; screenshot every A press
        emu.key("Return", count=2)          # confirm into new game
        time.sleep(30)                       # intro crawl
        for i in range(args.presses):
            emu.key("x" if hz.inject_available() else "Return")  # A button
            if i % 2 == 0:
                emu.shot(out / f"adv_{i:03d}.png")
            time.sleep(0.8)
        emu.shot(out / "final.png")
        print(f"captured {args.presses//2+1} frames -> {out}")
        return 0
    finally:
        emu.kill()
        hz.kill_display(display)


if __name__ == "__main__":
    raise SystemExit(main())
