#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_boot_render.py — the no-input boot render smoke (~1 minute).

The subset of the boot smoke that needs NO game input, so it is immune to the
input-environment preflight: it proves the ROM boots on real emulation, reaches
the title screen, renders it correctly, and keeps running.

    .venv/bin/python test/live/test_boot_render.py <rom.nds> [--display N]
                                                   [--update-golden]

Checks (exit 0 iff all pass):
  boot_title      after ~13s the title shows real content (sane luma +
                  structure) — a boot crash/hang is a flat black frame.
  golden_title    the title matches test/golden/title.png (min mean-abs-error
                  over a small frame set, riding out the fade pulses).  The
                  title art is untouched by the translation, so this golden is
                  stable across builds; garble/corruption scores far above the
                  threshold.
  emulation_runs  the window keeps animating over several seconds and melonDS
                  reports full speed in its title bar ([60/60]) — catches an
                  early hard fault that still leaves a pretty frame up.

What this cannot catch (needs the interactive tier): anything past the title.
See test_boot_smoke.py.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz

GOLDEN_TITLE_THRESH = 8.0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--display", type=int, default=None)
    ap.add_argument("--update-golden", action="store_true")
    a = ap.parse_args()
    rom = Path(a.rom).resolve()
    if not rom.exists():
        print(f"ROM not found: {rom}", file=sys.stderr)
        return 2
    display = a.display if a.display is not None else hz.pick_display()
    emu = hz.Emulator(display)
    out = emu.workdir / "boot_render"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    results = []

    def check(name, ok, detail):
        results.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    try:
        hz.boot_to_title(emu, rom)
        frames = []
        for i in range(5):
            p = out / f"title_{i}.png"
            emu.shot(p)
            g = hz.load_gray(p)
            frames.append((p, g, hz.mean_luma(g), hz.luma_std(g)))
            time.sleep(1.5)
        good = [(p, g, l, s) for (p, g, l, s) in frames if 18.0 <= l <= 248.0 and s > 9.0]
        check("boot_title", bool(good),
              "title rendered " + (f"(best luma {max(good, key=lambda x: x[3])[2]:.1f}, "
                                   f"structure {max(good, key=lambda x: x[3])[3]:.1f})" if good
                                   else f"NO — {[(round(l), round(s)) for _, _, l, s in frames]}"))
        g_title = hz.GOLDEN / "title.png"
        if a.update_golden and good:
            shutil.copy(max(good, key=lambda x: x[3])[0], g_title)
            check("golden_title", True, "golden captured from this ROM")
        elif not g_title.exists():
            check("golden_title", False, "missing test/golden/title.png (run --update-golden)")
        else:
            gold = hz.load_gray(g_title)
            mae = min(hz.region_mae(gold, g, hz.WHOLE_WINDOW) for _, g, _, _ in frames)
            check("golden_title", mae <= GOLDEN_TITLE_THRESH,
                  f"min title MAE {mae:.2f} (<= {GOLDEN_TITLE_THRESH}; fade-timed frames vary, min over set)")
        deltas = [hz.frame_delta(frames[i][1], frames[i + 1][1]) for i in range(len(frames) - 1)]
        name = subprocess.run(["xdotool", "getwindowname", str(emu.state["wid"])],
                              env=emu.env, capture_output=True, text=True).stdout.strip()
        m = re.match(r"\[(\d+)/(\d+)\]", name)
        fps_ok = bool(m) and int(m.group(1)) >= 55
        check("emulation_runs", max(deltas) > 1.0 and fps_ok,
              f"max frame delta {max(deltas):.2f} (>1.0 = animating); window title {name!r} "
              f"(want [60/60]-ish)")
    finally:
        emu.kill()

    overall = all(ok for _, ok, _ in results)
    print(f"\n=== boot render :: {rom.name} — "
          f"{sum(ok for _, ok, _ in results)}/{len(results)} -> "
          f"{'ALL PASS' if overall else 'FAIL'} ===  (frames: {out})", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
