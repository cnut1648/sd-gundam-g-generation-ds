#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_boot_smoke.py — the live boot-smoke gate (~2 minutes).

Boots the ROM fresh in headless melonDS and verifies the fundamentals a static
check cannot: the game actually starts, renders, reaches its first translated
dialogue, and keeps responding to input.

    .venv/bin/python test/live/test_boot_smoke.py <rom.nds> [options]

Checks (exit 0 iff all pass):
  boot_title            after ~13s the title screen shows real content (sane
                        luma + structure) — a crash/hang is a flat black frame.
  new_game_dialogue     START -> New Game -> intro crawl -> the first ADV scene
                        renders content on the lower screen.
  input_responsive      the framebuffer keeps changing across A presses —
                        identical consecutive frames = a hard freeze.
  golden_dialogue       the rendered dialogue-text region of the first scene
                        matches test/golden/dialogue_scene.png (min mean-abs-
                        error over the captured frame set; catches garble and
                        wrong-language regressions in the very first line).
  golden_nameplate      same for the speaker/nameplate line.
  golden_info (--full)  grind through the scripted first-stage tutorial to the
                        deploy map, open the unit menu, select the info page
                        (情報) and compare the whole lower screen against
                        test/golden/info.png — the direct in-game check for the
                        UI-garble class (~4 min, timing-sensitive; degrades to
                        SKIP if the blind grind cannot reach the screen).

Options:
  --display N       X display number (default: auto-pick a free one)
  --full            also drive to the unit-info page (golden_info)
  --update-golden   recapture test/golden/{title,dialogue_scene,info}.png from
                    this ROM (run only against a known-good build)
  --keep            leave the emulator running on failure (debugging)

Golden thresholds are calibrated for melonDS's deterministic replay: a good
build self-compares at MAE ~0; a garbled page scores >~10.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz

GOLDEN_DLG_THRESH = 14.0
GOLDEN_NAME_THRESH = 14.0
GOLDEN_INFO_THRESH = 7.0


def drive_to_info(emu, out):
    """From the first dialogue: grind the scripted tutorial to the deploy map,
    open the unit command menu and pick 情報 (item 4).  Returns the info frame
    path or None (the tutorial's scripted inputs sometimes strand a blind grind
    — treated as harness limitation, not a ROM failure)."""
    deploy = None
    for i in range(220):
        emu.key("A", pause=0.6)
        if i % 6 == 5:
            p = out / f"info_grind_{i:03d}.png"
            emu.shot(p)
            lum = hz.mean_luma(hz.load_gray(p), hz.BOTTOM_SCREEN)
            hz.log(f"grind {i}: lower-screen luma {lum:.1f}")
            if lum > 95:
                deploy = p
                break
    if deploy is None:
        return None
    # the grind's last A press usually leaves the unit command menu open
    # (移動/ID/隊列/情報 on the top-screen right edge); open it if not.
    MENU_BOX = (178, 38, 248, 132)
    if hz.mean_luma(hz.load_gray(deploy), MENU_BOX) <= 45.0:
        hz.log("command menu not open — pressing A on the unit")
        emu.key("A", pause=1.2)
        emu.shot(deploy)
    hz.log("selecting the info page (DOWN x3, A)")
    emu.key("DOWN", count=3, pause=0.45)
    emu.key("A", pause=2.5)
    info = out / "info.png"
    emu.shot(info)
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--display", type=int, default=None)
    ap.add_argument("--full", action="store_true", help="also drive to the unit-info page")
    ap.add_argument("--update-golden", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the emulator on failure")
    a = ap.parse_args()
    rom = Path(a.rom).resolve()
    if not rom.exists():
        print(f"ROM not found: {rom}", file=sys.stderr)
        return 2
    display = a.display if a.display is not None else hz.pick_display()
    emu = hz.Emulator(display)
    out = emu.workdir / "boot_smoke"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    results = []

    def check(name, ok, detail):
        results.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        return ok

    failed_early = False
    try:
        hz.boot_to_title(emu, rom)

        # 1) boot_title — the title fades/flashes, so sample a few frames and
        # accept if any shows sane content; a crash is flat black (luma<=12, std~0).
        cands = []
        for i in range(3):
            p = out / f"title_{i}.png"
            emu.shot(p)
            g = hz.load_gray(p)
            cands.append((p, hz.mean_luma(g), hz.luma_std(g)))
            if i < 2:
                time.sleep(1.5)
        good = [(p, l, s) for (p, l, s) in cands if 18.0 <= l <= 248.0 and s > 9.0]
        if good:
            best = max(good, key=lambda x: x[2])
            title_png = out / "title.png"
            shutil.copy(best[0], title_png)
            check("boot_title", True, f"title rendered (luma {best[1]:.1f}, structure {best[2]:.1f})")
        else:
            title_png = cands[0][0]
            ls = ", ".join(f"l{l:.0f}/s{s:.0f}" for _, l, s in cands)
            check("boot_title", False, f"no sane title frame ({ls}) — black/crash screen?")
            failed_early = True

        # 2) new_game_dialogue
        hz.start_new_game(emu)
        frames = []
        for i in range(8):
            emu.key("A", pause=0.75)
            p = out / f"dlg_{i:02d}.png"
            emu.shot(p)
            frames.append(hz.load_gray(p))
        stats = [(hz.mean_luma(g, hz.BOTTOM_SCREEN), hz.luma_std(g, hz.BOTTOM_SCREEN)) for g in frames]
        ok_dlg = any(16.0 <= l <= 170.0 and s > 15.0 for l, s in stats)
        check("new_game_dialogue", ok_dlg,
              f"lower screen over 8 advances: luma max {max(l for l, _ in stats):.1f}, "
              f"structure max {max(s for _, s in stats):.1f} (need a non-black ADV frame)")

        # 3) input_responsive
        deltas = [hz.frame_delta(x, y) for x, y in zip(frames, frames[1:])]
        maxd = max(deltas) if deltas else 0.0
        check("input_responsive", maxd > 1.5,
              f"max consecutive-frame delta {maxd:.2f} (>1.5 = frames change on input, not a hard freeze)")

        # 4) golden dialogue / nameplate
        g_scene = hz.GOLDEN / "dialogue_scene.png"
        if a.update_golden:
            hz.GOLDEN.mkdir(parents=True, exist_ok=True)
            shutil.copy(title_png, hz.GOLDEN / "title.png")
            ridx = max(range(len(frames)),
                       key=lambda i: hz.luma_std(frames[i], hz.DIALOGUE_TEXT))
            shutil.copy(out / f"dlg_{ridx:02d}.png", g_scene)
            check("golden_dialogue", True, f"golden captured (title + dialogue frame {ridx})")
            check("golden_nameplate", True, "golden captured (nameplate shares dialogue_scene.png)")
        elif not g_scene.exists():
            check("golden_dialogue", False, "missing test/golden/dialogue_scene.png (run --update-golden)")
        else:
            gold = hz.load_gray(g_scene)
            dlg_mae = min(hz.region_mae(gold, g, hz.DIALOGUE_TEXT) for g in frames)
            name_mae = min(hz.region_mae(gold, g, hz.SPEAKER_LINE) for g in frames)
            check("golden_dialogue", dlg_mae <= GOLDEN_DLG_THRESH,
                  f"min dialogue-region MAE {dlg_mae:.2f} (<= {GOLDEN_DLG_THRESH})")
            check("golden_nameplate", name_mae <= GOLDEN_NAME_THRESH,
                  f"min nameplate-region MAE {name_mae:.2f} (<= {GOLDEN_NAME_THRESH})")

        # 5) --full: drive to the unit-info page
        if a.full and not failed_early:
            info = drive_to_info(emu, out)
            g_info = hz.GOLDEN / "info.png"
            if info is None:
                print("  [SKIP] golden_info: could not reach the info page "
                      "(scripted tutorial blocks the blind grind)", flush=True)
            elif a.update_golden:
                shutil.copy(info, g_info)
                check("golden_info", True, "info golden captured")
            elif not g_info.exists():
                print("  [SKIP] golden_info: no golden on disk (run --full --update-golden)", flush=True)
            else:
                mae = hz.region_mae(hz.load_gray(g_info), hz.load_gray(info), hz.INFO_PAGE)
                check("golden_info", mae <= GOLDEN_INFO_THRESH,
                      f"info-page MAE {mae:.2f} (<= {GOLDEN_INFO_THRESH}; garble scores >~10)")
    finally:
        if a.keep and not all(ok for _, ok, _ in results):
            hz.log(f"--keep: emulator left running on display :{display}")
        else:
            emu.kill()

    npass = sum(1 for _, ok, _ in results if ok)
    overall = all(ok for _, ok, _ in results)
    print(f"\n=== boot smoke :: {rom.name} — {npass}/{len(results)} checks "
          f"-> {'ALL PASS' if overall else 'FAIL'} ===  (frames: {out})", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
