#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_battle_info_pages.py — live behavioral guard for the in-battle 情報
pages' copy-width clamp (the v1.3 战场情报 ID COMMAND garble class, ~90 s).

Both in-battle info pages copy composed text strips to OBJ VRAM (0x68xxxxx)
through the engine-A clamp chain (0x1359A/0x134AE -> 0x1B3F0C -> cave
0x11C1FC).  They use the SAME odd tile rows and differ only in destination
column, which is exactly the scope law the cave must implement (LESSONS A16;
static pin: gate ``engine_a_clamp_scope``):

  * col 3  (x=24)          — SPECIAL DEFENSE/ABILITY description rows, the
                             one surface allowed to copy up to 0xD0 (208 px);
                             clamping these to 0x50 re-truncates the
                             descriptions (the pre-v1.3 owner report);
  * col 1  (x=8)           — ID COMMAND page FULL-STRIP compose copies that
                             span all three 80 px panels; letting these run
                             past 0x50 paves the neighbour panels with stale
                             compose bytes (the v1.3 owner report);
  * cols 11/21, row 9      — per-panel name-row copies, below both limits.

This test plays the honest scenario (New Game -> stage-1 grind -> deploy map
-> 情報; no savestate, no RAM mutation), hooks the cave entry/returns to log
every (col, row, width_in) -> width_out decision on both pages, and applies
the scope law as the verdict.  A pixel probe corroborates on the ID page: the
stage-1 flagship's panels 2/3 carry no detail lines (等级9习得 / 无指令), so
any text ink in their detail regions is paving (measured: v1.3 fails the
decision oracle on every odd col-1 strip AND shows stray ink; the fixed build
is decision-clean with zero stray ink).  Navigation is structure-checked —
a run that cannot PROVE it reached the pages exits 2 (flake), never PASS
(LESSONS E6/F1).

    .venv/bin/python test/live/test_battle_info_pages.py <rom.nds> [--out DIR]

Exit 0 = both pages obey the scope law; 1 = clamp regression (either
direction); 2 = harness/navigation failure.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# These must be set before importing py-desmume.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_RENDER_DRIVER", "software")

from desmume.controls import Keys, keymask  # noqa: E402
from desmume.emulator import DeSmuME  # noqa: E402

TEST_DIR = Path(__file__).resolve().parents[1]
GOLDEN = TEST_DIR / "golden"

CAVE = 0x0211C1FC
# the clamp chain is entered by BL from 0x1359A / 0x134AE: the clamped width
# is in r0 at these return sites
CLAMP_RETURN_SITES = (0x0201359E, 0x020134B2)

BOTTOM = (0, 192, 256, 384)
# ID page (top screen): panel detail regions, rows 0xd..0x15 -> y 104..176.
# Panels sit at x=8/88/168, 76 px of text each.
PANEL_DETAIL_BOXES = {1: (8, 104, 84, 176), 2: (88, 104, 164, 176),
                      3: (168, 104, 244, 176)}
# panel name/SP rows y 56..88 must carry ink in panel 1 (粒子散布！/SP row)
PANEL1_HEAD_BOX = (8, 56, 84, 96)


def cycles(emu, n):
    for _ in range(n):
        emu.cycle(False)


def press(emu, name, settle=60, hold=6):
    mask = keymask(getattr(Keys, f"KEY_{name}"))
    emu.input.keypad_add_key(mask)
    cycles(emu, hold)
    emu.input.keypad_rm_key(mask)
    cycles(emu, settle)


def mean_luma(img, box):
    g = img.convert("L").crop(box)
    h = g.histogram()
    return sum(i * c for i, c in enumerate(h)) / max(sum(h), 1)


def text_ink(img, box):
    """Count near-white text pixels (glyph strokes render ~(248,248,248))."""
    rgb = img.convert("RGB").crop(box)
    n = 0
    for px in rgb.getdata():
        if min(px) >= 230 and max(px) - min(px) <= 12:
            n += 1
    return n


def template_mae(frame, template, box):
    import numpy as np
    f = np.asarray(frame.convert("L"), dtype=float)
    t = np.asarray(template.convert("L"), dtype=float)
    l, tp, r, b = box
    crop = f[tp:b, l:r]
    if crop.shape != t.shape:
        return 999.0
    return float(abs(crop - t).mean())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or Path(tempfile.mkdtemp(prefix="battle-info-pages-"))
    out.mkdir(parents=True, exist_ok=True)

    from PIL import Image
    join_tmpl = Image.open(GOLDEN / "join_choice_template.png")

    emu = DeSmuME()
    emu.volume_set(0)
    emu.open(str(args.rom))
    emu.reset()
    cycles(emu, 2000)

    mem = emu.memory.unsigned
    regs = emu.memory.register_arm9
    pend: list[tuple[int, int, int]] = []
    decisions: list[tuple[int, int, int, int]] = []
    phase = ["boot"]
    page_decisions: dict[str, list] = {"id": [], "special": []}

    def on_cave(_addr, _size):
        r5 = regs.r5
        if (mem.read_long(r5 + 0x5C) >> 20) == 0x68:
            pend.append((mem.read_short(r5 + 4), mem.read_short(r5 + 6),
                         mem.read_short(r5 + 0x12) + 7))

    def on_ret(_addr, _size):
        if pend:
            col, row, w_in = pend.pop()
            rec = (col, row, w_in, regs.r0)
            decisions.append(rec)
            if phase[0] in page_decisions:
                page_decisions[phase[0]].append(rec)

    emu.memory.register_exec(CAVE, on_cave)
    for a in CLAMP_RETURN_SITES:
        emu.memory.register_exec(a, on_ret)

    # --- honest navigation: title -> New Game -> stage-1 grind -> deploy ---
    for _ in range(3):
        press(emu, "START", settle=150)
    press(emu, "A", settle=150)
    press(emu, "A", settle=150)
    joined = deploy = False
    box = (5, 149, 200, 191)
    for i in range(500):
        press(emu, "A", settle=30)
        frame = emu.screenshot()
        if not joined and template_mae(frame, join_tmpl, box) < 11.0:
            cycles(emu, 40)
            press(emu, "A", settle=50)     # option 1
            joined = True
            continue
        if joined and mean_luma(frame, BOTTOM) > 95.0:
            deploy = True
            break
    if not deploy:
        emu.screenshot().save(out / "fail_nav.png")
        print("deploy map not reached (navigation flake) — rerun", file=sys.stderr)
        return 2
    cycles(emu, 30)

    # --- unit menu -> 情報 -> ID page / SPECIAL page ---
    press(emu, "A", settle=90)
    for _ in range(3):
        press(emu, "DOWN", settle=40)
    press(emu, "A", settle=140)            # info root
    root = emu.screenshot()
    root.save(out / "info_root.png")
    phase[0] = "id"
    press(emu, "DOWN", settle=160)         # ID COMMAND page
    id_frame = emu.screenshot()
    id_frame.save(out / "id_page.png")
    phase[0] = "back"
    press(emu, "UP", settle=140)           # back to root
    phase[0] = "special"
    press(emu, "UP", settle=160)           # SPECIAL page
    sp_frame = emu.screenshot()
    sp_frame.save(out / "special_page.png")
    phase[0] = "done"

    # --- structure proof: the pages actually rendered ---
    head_ink = text_ink(id_frame, PANEL1_HEAD_BOX)
    p1_ink = text_ink(id_frame, PANEL_DETAIL_BOXES[1])
    if head_ink < 40 or p1_ink < 40:
        print(f"ID page structure not proven (head_ink={head_ink}, "
              f"p1_ink={p1_ink}) — navigation flake, rerun", file=sys.stderr)
        return 2
    if not page_decisions["id"] or not page_decisions["special"]:
        print("clamp chain never fired on a page (id=%d special=%d) — flake"
              % (len(page_decisions["id"]), len(page_decisions["special"])),
              file=sys.stderr)
        return 2

    # --- the verdict: the scope law, both directions ---
    bad: list[str] = []
    widened_col3 = 0
    for page, recs in page_decisions.items():
        for col, row, w_in, w_out in recs:
            odd = bool(row & 1)
            if row < 0xA or w_in < 0x58:
                continue                     # below the clamp's thresholds
            if col == 3 and odd:
                want = min(w_in, 0xD0)
                if w_out != want:
                    bad.append(f"{page}: col3 row{row} w{w_in} -> {w_out} "
                               f"(SPECIAL description re-truncated; want {want})")
                elif w_in >= 0xD0:
                    widened_col3 += 1
            else:
                if w_out != 0x50:
                    bad.append(f"{page}: col{col} row{row} w{w_in} -> {w_out} "
                               "(must clamp to 0x50 — the ID-panel paving class)")
    if widened_col3 == 0:
        bad.append("no col-3 odd-row copy >= 0xD0 observed on the SPECIAL page "
                   "(widen not exercised — layout change? recalibrate)")

    # --- corroborating pixel probe: no paving in the empty panels 2/3 ---
    p2 = text_ink(id_frame, PANEL_DETAIL_BOXES[2])
    p3 = text_ink(id_frame, PANEL_DETAIL_BOXES[3])
    if p2 > 25 or p3 > 25:
        bad.append(f"stray detail ink in empty ID panels (p2={p2}, p3={p3}) "
                   "— compose paving visible")

    for rec in sorted(set(decisions)):
        print("decision col=%2d row=%2d w_in=%3d -> w_out=%3d" % rec)
    print(f"panel ink: head={head_ink} p1={p1_ink} p2={p2} p3={p3}; "
          f"col3 widens exercised: {widened_col3}")
    if bad:
        print("\n=== battle info pages: FAIL ===")
        for b in bad:
            print("  " + b)
        print("artifacts:", out)
        return 1
    print("\n=== battle info pages: PASS (scope law held on both pages) ===")
    print("artifacts:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
