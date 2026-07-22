#!/usr/bin/env python3
"""Live 配属 text-render regression using py-desmume and a normal cartridge save.

This is the runtime guard for the overlapping BG char-tile banks that corrupted
the third ID-command title on the lower screen.  It performs the owner-reported
flow without savestates or RAM mutation:

  title -> Continue -> selected slot -> BackStage -> 编成 -> 配属

It then visits all 24 selectable grid positions.  Every text draw on the lower
detail panel is captured at the shared helper (0x02012EFC).  Plain rows are
rendered independently with test/render_oracle.py and compared stroke-for-stroke
with the native 256x192 py-desmume framebuffer; rows carrying inline 0x01 layout
controls are recorded but excluded because the offline oracle does not model
that control's positioning semantics.  The historical overlap build fails the
affected plain row; a fixed build has complete oracle stroke recall there.

Usage:
  .venv/bin/python test/live/test_assignment_id_render.py ROM.nds \
      [--sav /path/to/matching-cartridge.sav] [--slot 1] [--out /tmp/assignment-id]

Exit 0 = pass, 1 = render mismatch, 2 = harness/navigation failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# These must be set before importing py-desmume.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_RENDER_DRIVER", "software")

from desmume.controls import Keys, keymask  # noqa: E402
from desmume.emulator import DeSmuME  # noqa: E402

TEST_DIR = Path(__file__).resolve().parents[1]
REPO = TEST_DIR.parent
sys.path.insert(0, str(TEST_DIR))
sys.path.insert(0, str(REPO))
from render_oracle import Oracle  # noqa: E402
from utils.text_codec import iter_tokens  # noqa: E402

DRAW_HELPER = 0x02012EFC
BOTTOM_Y = 192
PANEL_COORDS = {
    (24, 0), (24, 16), (24, 32), (24, 48),  # weapon names
    (32, 64),                                # unit name
    (80, 80),                                # pilot name
    (24, 96), (24, 128), (24, 160),          # ID-command titles
    (168, 104), (168, 136), (168, 168),      # ability names
}
THIRD_ID_COORD = (24, 160)


def cycles(emu: Any, count: int) -> None:
    for _ in range(count):
        emu.cycle(False)


def press(emu: Any, key_name: str, *, hold: int = 4, settle: int = 45,
          trace: list[dict[str, int | str]] | None = None) -> None:
    key = getattr(Keys, f"KEY_{key_name}")
    mask = keymask(key)
    emu.input.keypad_add_key(mask)
    cycles(emu, hold)
    emu.input.keypad_rm_key(mask)
    cycles(emu, settle)
    if trace is not None:
        trace.append({"key": key_name, "hold": hold, "settle": settle})


def read_c_string(emu: Any, address: int, limit: int = 96) -> bytes:
    """Read the game's token stream through its first 00 terminator.

    Two-byte text tokens are guaranteed not to use low byte 00, so the first
    zero is unambiguous for these name/title surfaces.
    """
    if not 0x02000000 <= address < 0x02400000:
        return b""
    out = bytearray()
    for i in range(limit):
        value = int(emu.memory.unsigned.read_byte(address + i))
        if value == 0:
            break
        out.append(value)
    return bytes(out)


def stroke_present(pixel: tuple[int, ...]) -> bool:
    # DeSmuME's native RGB framebuffer maps text-white to (248,248,248).
    return len(pixel) >= 3 and min(pixel[:3]) >= 240 and max(pixel[:3]) - min(pixel[:3]) <= 8


def compare_call(oracle: Oracle, frame: Any, call: dict[str, Any]) -> dict[str, Any]:
    raw = bytes.fromhex(call["raw"])
    expected = oracle.render_line(raw, "bank").convert("RGB")
    x0, y0 = call["x"], BOTTOM_Y + call["y"]
    expected_stroke = present_stroke = 0
    for y in range(expected.height):
        for x in range(expected.width):
            if expected.getpixel((x, y)) != (255, 255, 255):
                continue
            expected_stroke += 1
            sx, sy = x0 + x, y0 + y
            if sx < frame.width and sy < frame.height and stroke_present(frame.getpixel((sx, sy))):
                present_stroke += 1
    return {
        **call,
        # A low byte 0x01 inside a two-byte glyph token (for example E8 01 = 卡)
        # is text, not a layout control.  Only a standalone one-byte 0x01 makes
        # the row non-comparable with the offline oracle.
        "oracle_comparable": not any(tok == 0x01 and ln == 1
                                     for _off, tok, ln in iter_tokens(raw)),
        "expected_stroke": expected_stroke,
        "present_stroke": present_stroke,
        "recall": present_stroke / expected_stroke if expected_stroke else 1.0,
    }


def open_fresh(rom: Path, save: Path) -> Any:
    emu = DeSmuME()
    emu.volume_set(0)
    emu.open(str(rom))
    if not emu.backup.import_file(str(save)):
        emu.destroy()
        raise RuntimeError("py-desmume backup.import_file returned false")
    emu.reset()
    emu.volume_set(0)
    cycles(emu, 1800)
    return emu


def close_silently(emu: Any) -> None:
    emu.volume_set(0)
    try:
        emu.close()
    finally:
        emu.destroy()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", type=Path)
    ap.add_argument("--sav", type=Path,
                    default=TEST_DIR / "fixtures" / "assignment_slot1_newgame_plus.sav",
                    help="matching cartridge .sav (default: committed slot-1 assignment fixture)")
    ap.add_argument("--slot", type=int, choices=range(1, 4), default=1,
                    help="save slot to select (1-3; default: 1 for the committed fixture)")
    ap.add_argument("--out", type=Path,
                    help="artifact directory (default: a new /tmp directory)")
    args = ap.parse_args()
    rom, save = args.rom.resolve(), args.sav.resolve()
    if not rom.is_file() or not save.is_file():
        print(f"ROM/save not found: rom={rom}, sav={save}", file=sys.stderr)
        return 2
    out = args.out.resolve() if args.out else Path(tempfile.mkdtemp(prefix="assignment-id-render-"))
    out.mkdir(parents=True, exist_ok=True)

    trace: list[dict[str, int | str]] = []
    draws: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "rom": str(rom), "sav": str(save), "slot": args.slot, "out": str(out),
        "normal_flow": True, "savestate_used": False, "ram_mutation_used": False,
        "trace": trace, "states": [],
    }
    oracle = Oracle(rom)
    emu = None
    try:
        emu = open_fresh(rom, save)

        def on_draw(_address: int, _size: int) -> None:
            regs = emu.memory.register_arm9
            x, y, ptr = int(regs.r1), int(regs.r2), int(regs.r3)
            if (x, y) not in PANEL_COORDS:
                return
            raw = read_c_string(emu, ptr)
            if raw:
                draws.append({"x": x, "y": y, "ptr": ptr, "raw": raw.hex()})

        emu.memory.register_exec(DRAW_HELPER, on_draw)

        # Exact owner repro.  The long final settle is a wait for BackStage,
        # not an A-mash through story; this save is already post-ending.
        press(emu, "START", settle=60, trace=trace)
        press(emu, "A", settle=90, trace=trace)
        for _ in range(args.slot - 1):
            press(emu, "DOWN", settle=20, trace=trace)
        press(emu, "A", settle=60, trace=trace)
        press(emu, "UP", settle=20, trace=trace)
        press(emu, "A", settle=1200, trace=trace)
        for _ in range(3):
            press(emu, "B", hold=8, settle=90, trace=trace)
        press(emu, "DOWN", hold=8, settle=60, trace=trace)
        press(emu, "A", hold=8, settle=180, trace=trace)
        draws.clear()
        press(emu, "A", hold=8, settle=500, trace=trace)

        states: list[tuple[str, str | None]] = [("00_r0c0", None)]
        states += [(f"{i:02d}_r0c{i}", "RIGHT") for i in range(1, 7)]
        states += [("07_r1c6", "DOWN")]
        states += [(f"{13 - c:02d}_r1c{c}", "LEFT") for c in range(5, -1, -1)]
        states += [("14_r2c0", "DOWN")]
        states += [(f"{14 + c:02d}_r2c{c}", "RIGHT") for c in range(1, 7)]
        states += [("21_r3c2", "DOWN"), ("22_r3c1", "LEFT"), ("23_r3c0", "LEFT")]

        rows: list[dict[str, Any]] = []
        for state, movement in states:
            if movement is not None:
                draws.clear()
                press(emu, movement, hold=8, settle=120, trace=trace)
            frame = emu.screenshot().convert("RGB")
            frame.save(out / f"{state}.png")
            latest = {(d["x"], d["y"]): d for d in draws}
            state_rows = [compare_call(oracle, frame, latest[k]) for k in sorted(latest)]
            for row in state_rows:
                row["state"] = state
            rows.extend(state_rows)
            report["states"].append({
                "state": state,
                "screenshot": str((out / f"{state}.png").resolve()),
                "draw_calls": len(state_rows),
                "third_id_seen": THIRD_ID_COORD in latest,
            })
            comparable_state_rows = [r for r in state_rows if r["oracle_comparable"]]
            worst = min((r["recall"] for r in comparable_state_rows), default=1.0)
            print(f"  {state}: {len(comparable_state_rows):2d}/{len(state_rows):2d} comparable rows, "
                  f"worst stroke recall {worst:.3f}", flush=True)

        report["rows"] = rows
        comparable = [r for r in rows if r["oracle_comparable"]]
        control_rows = [r for r in rows if not r["oracle_comparable"]]
        mismatches = [r for r in comparable if r["present_stroke"] != r["expected_stroke"]]
        third_rows = [r for r in rows if (r["x"], r["y"]) == THIRD_ID_COORD]
        # At least 20 nonblank third-title draws proves the intended 24-slot
        # traversal was reached; a wrong screen must not become a vacuous pass.
        reached = len(report["states"]) == 24 and len(third_rows) >= 20 and len(rows) >= 200
        passed = reached and not mismatches
        report["summary"] = {
            "states": len(report["states"]), "rows_checked": len(rows),
            "third_id_rows_checked": len(third_rows), "mismatches": len(mismatches),
            "oracle_comparable_rows": len(comparable),
            "control_rows_recorded_not_compared": len(control_rows),
            "reached_assignment_grid": reached, "pass": passed,
        }
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if mismatches:
            print("\nMismatched rows (first 12):")
            for row in mismatches[:12]:
                print(f"  {row['state']} ({row['x']},{row['y']}) raw={row['raw']} "
                      f"stroke={row['present_stroke']}/{row['expected_stroke']} "
                      f"recall={row['recall']:.3f}")
        print(f"\n=== assignment ID render: {'PASS' if passed else 'FAIL'} — "
              f"{len(comparable)}/{len(rows)} oracle-comparable rows / "
              f"{len(third_rows)} third-ID rows, "
              f"{len(mismatches)} mismatch(es) ===")
        print(f"artifacts: {out}")
        return 0 if passed else (1 if reached else 2)
    except Exception as exc:
        report["harness_error"] = f"{type(exc).__name__}: {exc}"
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"HARNESS ERROR: {type(exc).__name__}: {exc}\nartifacts: {out}", file=sys.stderr)
        return 2
    finally:
        if emu is not None:
            close_silently(emu)


if __name__ == "__main__":
    raise SystemExit(main())
