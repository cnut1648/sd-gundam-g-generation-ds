#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_combat_cutin.py — the in-combat ID cut-in freeze grind (~15–25 minutes).

THE authoritative live freeze gate for the historically nastiest bug class:
queueing ID commands on a full squad and starting combat corrupted a cut-in
resource load → wild memcpy source → ARM9 data abort → hard freeze.  Static
data checks are necessary but NOT sufficient here (the abort is a runtime
over-read), so this test plays the real scenario on the real engine.

    .venv/bin/python test/live/test_combat_cutin.py <rom.nds> [options]

It boots FRESH (never from a savestate — a savestate restores foreign code and
data into RAM and MASKS the ROM under test), grinds New Game → JOIN → deploy,
moves the leader squad onto the enemy, queues an ID command + weapon on the
squad members (the cut-in trigger), starts the battle and then watches for a
freeze with two oracles:

  frame-identity   an 18s screenshot burst with periodic A taps; combat always
                   animates, so a still run >= --min-still seconds == hang.
  gdb PC           when melonDS's gdb stub is enabled (harness configures ports
                   3333/3334), the ARM9 PC is sampled: parked at the BIOS abort
                   spin 0xFFFF0104 (or the filesystem/memcpy abort cluster)
                   == frozen.  Skipped gracefully if the stub is busy.

Run it 3x for a shipping verdict (the freeze was non-deterministic).

Options:
  --display N     X display (default auto)
  --gdb-port N    ARM9 gdb stub port (default 3333; must match the melonDS
                  config the harness writes)
  --min-still S   still seconds that count as FREEZE (default 4.0)

Exit 0 = combat played through the cut-ins (CLEAN); 1 = FREEZE; 2 = the grind
could not reach combat (navigation flake — rerun; not a ROM verdict).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz

BIOS_ABORT_SPIN = 0xFFFF0104
FS_ABORT_CLUSTER = (0x020A2000, 0x020A3900)


def combat_nav(emu, out):
    """From the deploy map (cursor on the leader squad): move onto the enemy,
    attack, open individual orders, queue ID command + weapon on the squad
    members, confirm, start battle.  Mirrors the reproduced freeze scenario."""
    P = emu.key
    emu.shot(out / "nav_00_deploy.png")
    P("A", pause=1.5)                              # squad menu (move/ID/formation/info)
    emu.shot(out / "nav_01_squadmenu.png")
    P("A", pause=1.5)                              # move
    P("A", pause=1.5)                              # whole squad
    for _ in range(3):
        P("DOWN", pause=0.5)                       # toward the enemy row
    P("A", pause=1.8)                              # confirm move
    emu.shot(out / "nav_02_moved.png")
    P("A", pause=1.8)                              # attack
    P("A", pause=1.8)                              # confirm adjacent target
    emu.shot(out / "nav_03_target.png")
    P("A", pause=1.8)                              # individual orders -> unit 1
    emu.shot(out / "nav_04_orders.png")
    P("A", pause=1.3)                              # unit 1: weapon only (leader lacks SP)
    emu.shot(out / "nav_05_u1.png")
    for u in (2, 3):                               # units 2 & 3: ID command + weapon
        P("DOWN", pause=0.5)
        P("DOWN", pause=0.5)
        P("A", pause=1.3)                          # open the ID quote list
        P("A", pause=1.3)                          # pick quote 1 (the cut-in trigger)
        P("UP", pause=0.5)
        P("UP", pause=0.5)
        P("A", pause=1.3)                          # weapon -> next unit / done
        emu.shot(out / f"nav_06_u{u}.png")
    P("A", pause=1.8)                              # confirm
    emu.shot(out / "nav_07_confirm.png")
    P("A", pause=2.0)                              # battle start
    emu.shot(out / "nav_08_battlestart.png")


def gdb_pc(port, workdir):
    """One clean gdb attach (no stepping — stepping wedges the stub)."""
    script = workdir / "pc.gdb"
    script.write_text(
        "set pagination off\nset architecture armv5te\nset tcp connect-timeout 5\n"
        f"set remotetimeout 8\ntarget remote 127.0.0.1:{port}\n"
        'printf "PCLR %08x %08x\\n", $pc, $lr\ndetach\nquit\n')
    try:
        r = subprocess.run(["gdb-multiarch", "-q", "-x", str(script)],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    for ln in r.stdout.splitlines():
        if ln.startswith("PCLR "):
            return int(ln.split()[1], 16)
    return None


def gdb_frozen(port, workdir, samples=3):
    seen = []
    for _ in range(samples):
        pc = gdb_pc(port, workdir)
        if pc is not None:
            seen.append(pc)
        time.sleep(0.6)
    if not seen:
        return None, seen
    froz = all(pc == BIOS_ABORT_SPIN or FS_ABORT_CLUSTER[0] <= pc < FS_ABORT_CLUSTER[1]
               for pc in seen)
    return froz, seen


def burst(emu, outdir, dur_s, tap_every=6):
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    i = 0
    ts = []
    while time.time() - t0 < dur_s:
        emu.shot(outdir / f"b_{i:04d}.png", retries=1)
        ts.append(time.time() - t0)
        i += 1
        if tap_every and i % tap_every == 0:
            emu.key("A", pause=0.0)
    return ts


def longest_still(outdir, ts, thr=0.5):
    frames = sorted(outdir.glob("b_*.png"))
    if len(frames) < 2:
        return 0.0, 0
    prev = hz.load_gray(frames[0])
    best = cur = bi = 0
    for k in range(1, len(frames)):
        g = hz.load_gray(frames[k])
        if hz.region_mae(prev, g, hz.TOP_SCREEN) < thr:
            cur += 1
            if cur > best:
                best, bi = cur, k
        else:
            cur = 0
        prev = g
    t = lambda i: ts[i] if 0 <= i < len(ts) else i / 3.0
    return t(bi) - t(max(0, bi - best)), best + 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--display", type=int, default=None)
    ap.add_argument("--gdb-port", type=int, default=3333)
    ap.add_argument("--min-still", type=float, default=4.0)
    a = ap.parse_args()
    rom = Path(a.rom).resolve()
    if not rom.exists():
        print(f"ROM not found: {rom}", file=sys.stderr)
        return 2
    display = a.display if a.display is not None else hz.pick_display()
    hz.ensure_config(gdb_ports=(a.gdb_port, a.gdb_port + 1))
    emu = hz.Emulator(display)
    out = emu.workdir / "combat_cutin"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.rglob("*.png"):
        old.unlink()

    try:
        hz.boot_to_title(emu, rom)
        if not hz.preflight_input(emu, out):
            print("input preflight failed: the environment is not accepting game input — "
                  "no ROM verdict (exit 3)", file=sys.stderr)
            return 3
        emu.tap(*hz.NEWGAME_BUTTON)
        hz.log(f"New Game tapped; waiting ~{hz.INTRO_CRAWL_S}s intro crawl …")
        time.sleep(hz.INTRO_CRAWL_S)
        deploy = hz.grind_to_deploy(emu, out, tag="g")
        if deploy is None:
            print("could not reach the deploy map (navigation flake) — rerun", file=sys.stderr)
            return 2
        emu.key("R", pause=1.2)                     # cursor onto the leader squad
        combat_nav(emu, out)
        hz.log("battle started; watching for the cut-in freeze …")
        ts = burst(emu, out / "burst", 18)
        span, nfr = longest_still(out / "burst", ts)
        frame_frozen = span >= a.min_still and nfr >= 8
        print(f"[frame-identity] longest still run {nfr} frames / {span:.1f}s "
              f"(>= {a.min_still}s == FREEZE)", flush=True)
        pc_res, seen = gdb_frozen(a.gdb_port, out)
        if seen:
            print(f"[gdb] ARM9 PC samples: {' '.join(f'{p:08x}' for p in seen)} "
                  f"({'FROZEN' if pc_res else 'advancing'})", flush=True)
        else:
            print("[gdb] stub unreachable — relying on frame-identity", flush=True)
        emu.shot(out / "final.png")
    finally:
        emu.kill()

    frozen = frame_frozen or (pc_res is True)
    print(f"\n=== combat cut-in freeze grind :: {rom.name} ===")
    print(f"  frame-identity freeze: {frame_frozen}   gdb freeze: {pc_res is True}")
    print(f"  VERDICT: {'FREEZE (FAIL)' if frozen else 'CLEAN (PASS)'}   frames: {out}")
    return 1 if frozen else 0


if __name__ == "__main__":
    raise SystemExit(main())
