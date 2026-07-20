#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_row_clip.py — live regression for the 13-tile row-wrap glyph clip (~2 minutes).

The shared 12px glyph plot rasterizer (`0x02012FE4`) draws into a 2-row tile
context with stride8=13, so row1[col0] aliases row0[col13]: a glyph write that
crosses the 13-tile row boundary wraps around and erases the lower strip of
the row's first glyph(s).  The static ``glyph_row_clip`` gate pins the scoped
clip cave bytes (hook `0x12FE6` -> cave `0x11C448`); this test proves the
three known surfaces render complete glyphs in the real engine
(issue #2, fix adopted from PR #3 and re-homed off the live resource-id table):

    * Extra -> Profile -> character list          (map 0x0600F800)
    * Extra -> Profile -> unit list               (map 0x0600F800)
    * Continue -> slot 3 -> MS development -> System Tree  (map 0x0600E000)

Both Profile lists are checked on entry and again after changing the kana
category, so the regression covers their redraw path as well as the original
issue frames.  The System Tree is checked on entry and after moving selection.

The verdict is deliberately not a whole-frame golden.  Each route first has
to match the surrounding screen structure (otherwise it is a navigation
flake), then a native-resolution ink probe checks exactly the lower glyph
strip erased by the bug.  This keeps animation and unrelated text changes out
of the oracle while making an unfixed build fail decisively (measured on the
pre-fix v1.2 ROM: lower-strip ink 0 on all three surfaces).

    .venv/bin/python test/live/test_row_clip.py <rom.nds> [options]

Options:
  --display N   X display number (default: auto-pick a free one)
  --sav PATH    deep-progress cartridge save (default: newgame_plus.sav)

Exit 0 = all three surfaces complete; 1 = row-clip regression; 2 = harness or
navigation failure; 3 = environment cannot drive game input.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz


# Native DS-screen coordinates.  ``split_screens`` also accepts raw 256x384
# captures, which lets the oracle be calibrated independently of the 19px
# melonDS menu bar.
PROFILE_FIRST_X = (128, 140)
PROFILE_ROWS = tuple(range(32, 176, 16))
PROFILE_LIST_BOX = (120, 28, 244, 180)
PROFILE_HEADER_BOX = (40, 0, 216, 18)

PROFILE_UPPER_MIN = 160
PROFILE_UPPER_MAX = 280
PROFILE_LIST_INK_MIN = 3500
PROFILE_LIST_INK_MAX = 6500
PROFILE_HEADER_INK = {
    "character": (215, 270),
    "unit": (150, 210),
}
PROFILE_LOWER_MIN = 100

TREE_FIRST_X = (4, 20)
TREE_FIRST_UPPER_Y = (176, 184)
TREE_FIRST_LOWER_Y = (184, 192)
TREE_BODY_BOX = (0, 26, 256, 170)

TREE_UPPER_MIN = 8
TREE_UPPER_MAX = 60
TREE_BODY_INK_MIN = 5000
TREE_BODY_INK_MAX = 12000
TREE_LUMA_MIN = 30.0
TREE_LUMA_MAX = 75.0
TREE_LOWER_MIN = 12


class NavigationError(RuntimeError):
    """The scripted route did not land on the surface under test."""


def split_screens(frame):
    """Return native top/bottom 256x192 arrays from a live or raw capture."""
    h, w = frame.shape
    if w != 256:
        raise ValueError(f"unexpected screenshot width {w}; want 256")
    if h == 384:
        return frame[0:192], frame[192:384]
    if h >= 403:
        return frame[19:211], frame[211:403]
    raise ValueError(f"unexpected screenshot height {h}; want 384 or >=403")


def ink_count(frame, box, threshold=180.0) -> int:
    return int((hz.crop(frame, box) > threshold).sum())


def profile_metrics(frame, kind: str) -> dict[str, float | int | bool]:
    top, _ = split_screens(frame)
    if kind not in PROFILE_HEADER_INK:
        raise ValueError(f"unknown Profile kind: {kind}")
    x0, x1 = PROFILE_FIRST_X
    upper = sum(ink_count(top, (x0, y, x1, y + 8)) for y in PROFILE_ROWS)
    # The bug erases these seven lower rows completely.  Exclude the eighth
    # row boundary, which carries unrelated one-pixel residue in a bad ROM.
    lower = sum(ink_count(top, (x0, y + 8, x1, y + 15)) for y in PROFILE_ROWS)
    list_ink = ink_count(top, PROFILE_LIST_BOX)
    header_ink = ink_count(top, PROFILE_HEADER_BOX)
    header_min, header_max = PROFILE_HEADER_INK[kind]
    scene_ok = (
        PROFILE_UPPER_MIN <= upper <= PROFILE_UPPER_MAX
        and PROFILE_LIST_INK_MIN <= list_ink <= PROFILE_LIST_INK_MAX
        and header_min <= header_ink <= header_max
    )
    return {
        "upper_ink": upper,
        "lower_ink": lower,
        "list_ink": list_ink,
        "header_ink": header_ink,
        "scene_ok": scene_ok,
        "clip_ok": lower >= PROFILE_LOWER_MIN,
    }


def tree_metrics(frame) -> dict[str, float | int | bool]:
    _, bottom = split_screens(frame)
    x0, x1 = TREE_FIRST_X
    upper = ink_count(
        bottom, (x0, TREE_FIRST_UPPER_Y[0], x1, TREE_FIRST_UPPER_Y[1])
    )
    lower = ink_count(
        bottom, (x0, TREE_FIRST_LOWER_Y[0], x1, TREE_FIRST_LOWER_Y[1])
    )
    body_ink = ink_count(bottom, TREE_BODY_BOX, threshold=80.0)
    luma = hz.mean_luma(bottom)
    scene_ok = (
        TREE_UPPER_MIN <= upper <= TREE_UPPER_MAX
        and TREE_BODY_INK_MIN <= body_ink <= TREE_BODY_INK_MAX
        and TREE_LUMA_MIN <= luma <= TREE_LUMA_MAX
    )
    return {
        "upper_ink": upper,
        "lower_ink": lower,
        "tree_body_ink": body_ink,
        "bottom_luma": luma,
        "scene_ok": scene_ok,
        "clip_ok": lower >= TREE_LOWER_MIN,
    }


def boot_to_menu(emu: hz.Emulator, rom: Path, sav: Path, out: Path) -> None:
    """Boot with the battery save and land on the 3-option main menu.

    With a cartridge save present the main menu is the 3-option layout
    (はじめから/つづきから/おまけ) that the stock ``menu_state`` bands do not
    match (see test_stage_start.py, same situation), so the input preflight
    here is frame-delta based: START must move the framebuffer off the title
    art."""
    hz.boot_to_title(emu, rom, sav=sav)
    out.mkdir(parents=True, exist_ok=True)
    moved = False
    base = None
    for i in range(8):
        emu.key("START", hold_ms=250, pause=2.0)
        p = out / f"preflight_{i}.png"
        emu.shot(p)
        g = hz.load_gray(p)
        if base is not None and hz.frame_delta(g, base) > 2.0:
            moved = True
            break
        base = g
    if not moved:
        raise hz.InputEnvironmentError(
            "the emulation environment did not accept START input"
        )


def capture_profile(
    emu: hz.Emulator,
    rom: Path,
    sav: Path,
    out: Path,
    kind: str,
):
    nav = out / f"{kind}_nav"
    boot_to_menu(emu, rom, sav, nav)

    # Main menu -> おまけ -> プロフィール -> character/unit list.
    emu.key("DOWN", pause=0.8)
    emu.key("A", pause=3.0)
    emu.key("A", pause=4.0)
    if kind == "unit":
        emu.key("DOWN", pause=1.5)
    emu.key("A", pause=5.0)

    initial = out / f"profile_{kind}_initial.png"
    if not emu.shot(initial):
        raise RuntimeError(f"failed to capture {initial}")
    initial_metrics = profile_metrics(hz.load_gray(initial), kind)

    # Switch kana category to force a second list composition with different
    # names.  This proves the fix survives the same redraw path that produced
    # the original issue frames, rather than merely preserving the first
    # cached frame.
    emu.key("DOWN", pause=1.2)
    emu.key("RIGHT", pause=3.0)
    alternate = out / f"profile_{kind}_alternate.png"
    if not emu.shot(alternate):
        raise RuntimeError(f"failed to capture {alternate}")
    alternate_metrics = profile_metrics(hz.load_gray(alternate), kind)
    return (
        (initial, initial_metrics),
        (alternate, alternate_metrics),
    )


def capture_system_tree(
    emu: hz.Emulator,
    rom: Path,
    sav: Path,
    out: Path,
):
    nav = out / "system_tree_nav"
    boot_to_menu(emu, rom, sav, nav)

    # Continue -> slot 3 -> confirm; clear the post-load messages and settle on
    # the back-stage strategy menu.  This is the fixture's documented route.
    emu.key("A", pause=1.5)
    emu.key("DOWN", count=2, pause=0.7)
    emu.key("A", pause=1.2)
    emu.key("UP", pause=0.7)
    emu.key("A", pause=10.0)
    emu.key("A", count=18, pause=0.5)
    time.sleep(10.0)

    # Back stage item 3 (MS开发) -> RIGHT -> DOWN (系统图) -> A.
    emu.key("DOWN", count=2, pause=0.9)
    emu.key("A", pause=3.0)
    emu.key("RIGHT", pause=2.5)
    emu.key("DOWN", pause=1.2)
    emu.key("A", pause=5.0)

    enter = out / "system_tree_enter.png"
    if not emu.shot(enter):
        raise RuntimeError(f"failed to capture {enter}")
    enter_metrics = tree_metrics(hz.load_gray(enter))

    # A second unit name exercises the same aliased row after selection moves.
    emu.key("DOWN", pause=3.0)
    after_down = out / "system_tree_after_down.png"
    if not emu.shot(after_down):
        raise RuntimeError(f"failed to capture {after_down}")
    down_metrics = tree_metrics(hz.load_gray(after_down))
    return ((enter, enter_metrics), (after_down, down_metrics))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("rom")
    ap.add_argument("--display", type=int, default=None)
    ap.add_argument("--sav", default=str(hz.FIXTURES / "newgame_plus.sav"))
    a = ap.parse_args()

    rom = Path(a.rom).resolve()
    sav = Path(a.sav).resolve()
    if not rom.exists():
        print(f"ROM not found: {rom}", file=sys.stderr)
        return 2
    if not sav.exists():
        print(f"save fixture not found: {sav}", file=sys.stderr)
        return 2

    display = a.display if a.display is not None else hz.pick_display()
    emu = hz.Emulator(display)
    out = emu.workdir / "row_clip"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.rglob("*.png"):
        old.unlink()

    results: list[tuple[str, bool, str]] = []

    def check(name: str, metrics: dict[str, float | int | bool], shot: Path):
        if not metrics["scene_ok"]:
            raise NavigationError(
                f"{name} did not match the expected scene: {metrics} (frame {shot})"
            )
        ok = bool(metrics["clip_ok"])
        detail = (
            f"lower-strip ink {metrics['lower_ink']} "
            f"(need >= {PROFILE_LOWER_MIN if name.startswith('profile') else TREE_LOWER_MIN}); "
            f"scene metrics {metrics}; frame {shot}"
        )
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    try:
        for kind in ("character", "unit"):
            for state, (shot, metrics) in zip(
                ("initial", "alternate"),
                capture_profile(emu, rom, sav, out, kind),
            ):
                check(f"profile_{kind}_{state}", metrics, shot)
        for label, (shot, metrics) in zip(
            ("system_tree_enter", "system_tree_after_down"),
            capture_system_tree(emu, rom, sav, out),
        ):
            check(label, metrics, shot)
    except hz.InputEnvironmentError as exc:
        print(f"input preflight failed: {exc} — no ROM verdict (exit 3)", file=sys.stderr)
        return 3
    except (NavigationError, RuntimeError, ValueError) as exc:
        print(f"harness/navigation failure: {exc}", file=sys.stderr)
        return 2
    finally:
        emu.kill()

    overall = all(ok for _, ok, _ in results)
    print(
        f"\n=== row clip :: {rom.name} — "
        f"{sum(ok for _, ok, _ in results)}/{len(results)} -> "
        f"{'ALL PASS' if overall else 'FAIL'} ===  (frames: {out})",
        flush=True,
    )
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
