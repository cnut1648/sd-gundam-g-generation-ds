#!/usr/bin/env python3
"""Capture every BackStage root tab and submenu item.

Normal flow only:

  title -> Continue -> selected cartridge-save slot -> BackStage
        -> 作战 / 编成 / MS开发 / 系统
        -> all 12 submenu items and their help text

No savestate or RAM mutation is used.

Usage:
  .venv/bin/python test/live/capture_backstage_tabs.py ROM.nds \
      [--sav test/fixtures/assignment_slot1_newgame_plus.sav] \
      [--slot 1] [--out /tmp/backstage-tabs]
"""
from __future__ import annotations

import argparse
import hashlib
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


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cycles(emu: Any, count: int) -> None:
    for _ in range(count):
        emu.cycle(False)


def press(
    emu: Any,
    key_name: str,
    *,
    hold: int = 8,
    settle: int = 90,
    trace: list[dict[str, int | str]],
) -> None:
    key = getattr(Keys, f"KEY_{key_name}")
    mask = keymask(key)
    emu.input.keypad_add_key(mask)
    cycles(emu, hold)
    emu.input.keypad_rm_key(mask)
    cycles(emu, settle)
    trace.append({"key": key_name, "hold": hold, "settle": settle})


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument(
        "--sav",
        type=Path,
        default=TEST_DIR / "fixtures" / "assignment_slot1_newgame_plus.sav",
    )
    parser.add_argument("--slot", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    rom = args.rom.resolve()
    save = args.sav.resolve()
    if not rom.is_file() or not save.is_file():
        print(f"ROM/save not found: rom={rom}, sav={save}", file=sys.stderr)
        return 2
    out = (
        args.out.resolve()
        if args.out
        else Path(tempfile.mkdtemp(prefix="backstage-tabs-"))
    )
    out.mkdir(parents=True, exist_ok=True)

    trace: list[dict[str, int | str]] = []
    report: dict[str, Any] = {
        "rom": str(rom),
        "rom_sha1": sha1(rom),
        "sav": str(save),
        "sav_sha1": sha1(save),
        "slot": args.slot,
        "normal_flow": True,
        "savestate_used": False,
        "ram_mutation_used": False,
        "trace": trace,
        "states": [],
    }
    emu = None
    try:
        emu = open_fresh(rom, save)

        # The fixture is already post-ending. Confirm Continue/slot/load, wait
        # for BackStage, then use idempotent B presses to return to the tab root.
        press(emu, "START", settle=60, trace=trace)
        press(emu, "A", settle=90, trace=trace)
        for _ in range(args.slot - 1):
            press(emu, "DOWN", settle=20, trace=trace)
        press(emu, "A", settle=60, trace=trace)
        press(emu, "UP", settle=20, trace=trace)
        press(emu, "A", settle=1200, trace=trace)
        for _ in range(3):
            press(emu, "B", settle=90, trace=trace)

        frame_hashes: set[str] = set()

        def capture(state: str) -> None:
            frame = emu.screenshot().convert("RGB")
            if frame.size != (256, 384):
                raise RuntimeError(f"{state}: unexpected framebuffer size {frame.size}")
            path = out / f"{state}.png"
            frame.save(path)
            digest = hashlib.sha1(frame.tobytes()).hexdigest()
            frame_hashes.add(digest)
            report["states"].append(
                {"state": state, "screenshot": str(path), "frame_sha1": digest}
            )

        tabs = (
            ("0_operations", ("mission", "map", "search", "advance")),
            ("1_formation", ("assign", "list", "detachment")),
            ("2_development", ("hangar", "tree")),
            ("3_system", ("save", "load", "settings")),
        )
        expected_states = len(tabs) + sum(len(items) for _tab, items in tabs)
        for index, (tab, items) in enumerate(tabs):
            if index:
                press(emu, "DOWN", settle=90, trace=trace)
            capture(f"{tab}_root")
            press(emu, "A", settle=180, trace=trace)
            for item_index, item in enumerate(items):
                if item_index:
                    press(emu, "DOWN", settle=90, trace=trace)
                capture(f"{tab}_item_{item_index}_{item}")
            press(emu, "B", settle=120, trace=trace)

        reached = (
            len(report["states"]) == expected_states
            and len(frame_hashes) == expected_states
        )
        report["summary"] = {
            "states": len(report["states"]),
            "unique_frames": len(frame_hashes),
            "reached_all_tabs": reached,
            "pass": reached,
        }
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"=== BackStage tabs: {'PASS' if reached else 'FAIL'} — "
            f"{len(report['states'])} states / {len(frame_hashes)} unique frames ==="
        )
        print(f"artifacts: {out}")
        return 0 if reached else 2
    except Exception as exc:
        report["harness_error"] = f"{type(exc).__name__}: {exc}"
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"HARNESS ERROR: {type(exc).__name__}: {exc}\nartifacts: {out}",
            file=sys.stderr,
        )
        return 2
    finally:
        if emu is not None:
            close_silently(emu)


if __name__ == "__main__":
    raise SystemExit(main())
