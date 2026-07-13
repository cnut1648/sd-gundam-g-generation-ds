#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dialogue_grind.py — the dialogue-advance freeze grind (~4 minutes).

The historical worst-in-class regression: advancing past a specific dialogue
line hard-hangs the game (the stage-script VM walks a corrupted inter-block
byte into a wild jump → ARM9 data abort → BIOS spin).  The static gate
(stage_script_integrity in run_static.py) models this; this LIVE test proves
the real engine advances dialogue without freezing.

    .venv/bin/python test/live/test_dialogue_grind.py <rom.nds> [options]

What it does: fresh boot → New Game → press A through the first stage's
dialogue/tutorial for --steps presses (default 150, which crosses the intro
scene, the ally-JOIN choice and the deploy transition), screenshotting every
press.  FAIL iff the window freezes: a run of >= --still-frames consecutive
effectively-identical frames (whole-window MAE < 0.5) while input is being
delivered.  Scene transitions/waits are far shorter than the threshold.

The JOIN choice box is answered (option 2) via the golden template so the
grind exercises the choice path too.

Options:
  --display N        X display (default auto)
  --steps N          A presses to grind (default 150)
  --still-frames N   consecutive identical frames that = FREEZE (default 14)
  --sav PATH         optional cartridge save to boot with (Continue flows)

Exit 0 = no freeze; 1 = freeze detected; 2 = harness failure.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--display", type=int, default=None)
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--still-frames", type=int, default=14)
    ap.add_argument("--sav", default=None)
    a = ap.parse_args()
    rom = Path(a.rom).resolve()
    if not rom.exists():
        print(f"ROM not found: {rom}", file=sys.stderr)
        return 2
    display = a.display if a.display is not None else hz.pick_display()
    emu = hz.Emulator(display)
    out = emu.workdir / "dialogue_grind"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    try:
        hz.boot_to_title(emu, rom, sav=Path(a.sav) if a.sav else None)
        if not hz.preflight_input(emu, out):
            print("input preflight failed: the environment is not accepting game input — "
                  "no ROM verdict (exit 3)", file=sys.stderr)
            emu.kill()
            return 3
        emu.key("A", hold_ms=250, pause=2.0)   # confirm はじめから on the menu
        hz.log(f"New Game confirmed; waiting ~{hz.INTRO_CRAWL_S}s intro crawl …")
        time.sleep(hz.INTRO_CRAWL_S)
    except Exception as e:
        print(f"harness failure during boot: {e}", file=sys.stderr)
        emu.kill()
        return 2

    tmpl = hz.load_gray(hz.GOLDEN / "join_choice_template.png")
    choice_box = (5, 168, 200, 210)
    prev = None
    still = 0
    worst = 0
    worst_at = -1
    joined = False
    frozen_at = None
    try:
        for i in range(a.steps):
            emu.key("A", pause=0.45)
            p = out / f"step_{i:03d}.png"
            if not emu.shot(p):
                continue
            g = hz.load_gray(p)
            if not joined and hz.template_mae(g, tmpl, choice_box) < 11.0:
                time.sleep(0.5)
                emu.key("DOWN", pause=0.5)
                emu.key("A", pause=0.6)
                joined = True
                hz.log(f"JOIN choice answered at step {i}")
            if prev is not None:
                d = hz.frame_delta(g, prev)
                if d < 0.5:
                    still += 1
                    if still > worst:
                        worst, worst_at = still, i
                    if still >= a.still_frames:
                        frozen_at = i
                        break
                else:
                    still = 0
            prev = g
            if i % 25 == 24:
                hz.log(f"step {i + 1}/{a.steps}  longest-still-run {worst}")
    finally:
        emu.kill()

    print(f"\n=== dialogue grind :: {rom.name} ===")
    print(f"  steps ground        : {a.steps if frozen_at is None else frozen_at + 1}")
    print(f"  JOIN choice reached : {joined}")
    print(f"  longest still run   : {worst} consecutive frames (limit {a.still_frames})"
          + (f" at step {worst_at}" if worst_at >= 0 else ""))
    if frozen_at is not None:
        print(f"  VERDICT: FREEZE (window stopped changing under input at step {frozen_at}) — FAIL")
        print(f"  frames: {out}")
        return 1
    print("  VERDICT: no freeze — dialogue advances end-to-end — PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
