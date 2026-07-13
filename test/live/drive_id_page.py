#!/usr/bin/env python3
"""drive_id_page.py — boot to the formation (編成) character list and page
into the ID COMMAND / ID ABILITY panels, screenshotting every step.

    .venv/bin/python test/live/drive_id_page.py <rom.nds> --out DIR \
        [--script "D,A,A,R,..."]

Keys map to harness key names (inject hook): A/B/X/Y/L/R/U/D/LEFT/RIGHT/
START/SELECT. Screenshots land in --out as step_###.png.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz

KEY = {"A": "x", "B": "z", "X": "s", "Y": "a", "L": "q", "R": "w",
       "U": "Up", "D": "Down", "LEFT": "Left", "RIGHT": "Right",
       "START": "Return", "SELECT": "shift"}
MASK = {"A": 0x1, "B": 0x2, "SELECT": 0x4, "START": 0x8, "R": 0x10, "L": 0x20,
        "U": 0x40, "D": 0x80, "RIGHT": 0x100, "LEFT": 0x200, "X": 0x400, "Y": 0x800}


def press(emu, name, hold=0.15, settle=0.7):
    if hz.inject_available():
        hz._inject_write(MASK[name])
        time.sleep(hold)
        hz._inject_clear()
    else:
        emu.key(KEY[name])
    time.sleep(settle)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--out", required=True)
    ap.add_argument("--script", default="")
    ap.add_argument("--sav", default=None)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    display = hz.pick_display()
    emu = hz.Emulator(display)
    try:
        hz.boot_to_title(emu, Path(args.rom),
                         Path(args.sav) if args.sav else None)
        # advance past the press-START title; don't trust the classifier
        press(emu, "START", settle=1.2)
        press(emu, "START", settle=1.2)
        emu.shot(out / "step_000_menu.png")
        steps = [s.strip().upper() for s in args.script.split(",") if s.strip()]
        for i, s in enumerate(steps, 1):
            if s.startswith("WAIT"):
                time.sleep(float(s[4:] or 2))
            elif s.startswith("TAP:"):
                _, x, y = s.split(":")
                if hz.inject_available():
                    hz._inject_write(0, 1, int(x), int(y))
                    time.sleep(0.15)
                    hz._inject_clear()
                else:
                    emu.tap(int(x), int(y))
                time.sleep(0.8)
            else:
                press(emu, s)
            emu.shot(out / f"step_{i:03d}_{s.replace(':','_')}.png")
        return 0
    finally:
        emu.kill()
        hz.kill_display(display)


if __name__ == "__main__":
    raise SystemExit(main())
