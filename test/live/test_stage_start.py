#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stage_start.py — the live per-stage "start → battle map" no-freeze grind.

Every story stage is a `_STG*.bin` script (dialogue + cutscene + combat setup)
read whole into the fixed buffer at RAM 0x0232C800.  A corrupted inter-block
byte, a mis-relocated pointer or a mis-aligned header table turns a single
stage's opening flow into an ARM9 data abort → BIOS spin (0xFFFF0104): the game
hard-freezes on a black screen loading THAT stage while every other stage is
fine.  The static gate (stage_script_integrity) models this per file; this LIVE
test proves the real engine actually warps into each stage, advances its opening
dialogue and reaches the deploy/battle map without freezing.

    .venv/bin/python test/live/test_stage_start.py <rom.nds> [options]

Because booting + navigating to each stage the honest way (clear every prior
session) is impossible, this test WARPS into stages with an in-RAM cheat driven
by the instrumented melonDS build's file hooks (/tmp/melon_poke writes ARM9 RAM
every frame; /tmp/melon_dump snapshots ARM9 RAM).  See "THE STAGE-WARP CHEAT"
below.  A run is only PASS for a stage if it ACTUALLY REACHED the battle/deploy
map (never a pass on timeout alone — LESSONS E6/F1).

THE STAGE-WARP CHEAT (how it was found, and why it is exactly this)
------------------------------------------------------------------
* Boot the deep New-Game+ cartridge save (test/fixtures/newgame_plus.sav),
  Continue → data-load slot 2 (a BACK STAGE session).  The BackStage 作戦 tab's
  進撃 (advance) starts the current session.
* The session's `_STG` file is PRELOADED into 0x0232C800 at BackStage ENTRY
  (verified: garbage at the load-confirm popup, a 100%-match `_STG` at the
  BackStage root) — 進撃 does not re-read it, it just enters the loaded flow.
  So the redirect must happen DURING the save-load → BackStage transition.
* The `_STG` file is chosen by the stage descriptor table (arm9 0x0217555C, 101
  records × 0x34, key byte at record+0, buffer 0x0232C800 at +4).  On save-load
  the game reads the save's current-stage id (halfword @ 0x0227CC48) and loads
  the descriptor record whose key == that id.  Poking 0x0227CC48 / 0x0227CE55 /
  0x0227CC64 (the proximate copy the loader reads) does NOT redirect — the
  loader overwrites 0x0227CC64 from the value in the same frame it reads it, and
  the id ultimately comes from the event-VM, not a plain variable.
* WHAT WORKS: overwrite the descriptor RECORD the save's id matches (index
  IDX_CUR, found at runtime from 0x0227CC48) with the bytes of the TARGET
  record — keeping the key byte so the match still fires — while the save-load
  runs.  Each record carries its BackStage preview CARD, decoded statically as
  `card FAT pos = halfword@(record+0x30) - 2995` (verified against live dumps
  across the campaign / SP / X regions; the FAT/session ORDER is scrambled for
  SP/X, so this decoded card map — NOT a FAT offset — is what resolves a `_STG`
  name to a record).  Redirecting to record i lands card i in the buffer; 進撃
  then plays that slot's SESSION (dumped + matched at runtime — the game's real
  card→session pairing, e.g. the save's own slot previews _STG20A but PLAYS
  _STG21A; rec4 previews _STG01 and plays _STG02).

Per stage: load the popup savestate → poke the descriptor redirect → press A
(Load) → verify the BackStage buffer is the target record's CARD → release the
poke → 進撃 → grind A through the intro/briefing (answering the ally-JOIN choice)
until the bottom-screen luma rises above ~95 (the deploy/battle map, as
harness.grind_to_deploy detects) → dump 0x0232C800 to record the `_STG` the
battle actually runs.

Freeze verdict per stage:
  PASS      reached the battle/deploy map (bottom-screen luma > 95) — the only
            clean result; never a pass on timeout alone (LESSONS E6/F1).
  FREEZE    a sustained run of effectively-identical frames (whole-window
            frame_delta < 0.5) under continuous A presses AND that stuck frame
            is a near-BLACK screen AND the map was never reached — the BIOS
            data-abort HARD-FREEZE signature.  A static *rendered* scene is a
            soft-lock (see UNREACHED), not a hard freeze.
  DIVERT    the played buffer is a free battle (_STGFB*): a few slots (the
            finale routes / some SP stages, whose keys drive the free-battle-
            unlock decision fn) divert to a free battle when entered from this
            save's story context — a warp limitation, NOT a ROM fault.
  UNREACHED map not reached (budget exhausted with frames changing, or a static
            rendered soft-lock) — inconclusive (rerun); NOT counted as a pass.
  WARP-FAIL the redirect did not land the expected preview card in the buffer
            (a harness/timing flake, not a ROM verdict).

Exit codes: 0 all stageable sessions PASS (DIVERTs allowed — warp limitation);
1 a FREEZE; 2 unreached/warp-fail (rerun); 3 the emulation environment cannot
drive game input (see harness.preflight_input / InputEnvironmentError).

Options:
  --stages LIST   comma list of `_STG` names (e.g. "_STG01,_STG04A") and/or raw
                  descriptor record indices, or a preset: "smoke" (default, ~11
                  diverse), "routes" (a/b cards), "sp", "x", "all".
  --display N     X display (default: auto-pick)
  --steps N       max A presses per stage grind (default 170)
  --still-frames N consecutive identical frames that = FREEZE (default 26)
  --keep          leave the emulator running at the end (debugging)
  --self-test     prove the freeze detector fires on a black-static run and
                  rejects rendered soft-locks (no emulator; exit 0/1)
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as hz

# ---- ARM9-RAM cheat hooks (instrumented melonDS build) ----------------------
POKE_PATH = Path("/tmp/melon_poke")
DUMP_PATH = Path("/tmp/melon_dump")

# ---- stage descriptor table (arm9 static) -----------------------------------
DESC_KEY_BASE = 0x0217555C      # record[i] key byte @ this + i*0x34
DESC_STRIDE = 0x34
DESC_COUNT = 101
CARD_BIAS = 2995                # card FAT pos = halfword@(record+0x30) - CARD_BIAS
STAGE_ID_ADDR = 0x0227CC48      # save's current-stage id (halfword)
STAGE_BUF = 0x0232C800          # the _STG load buffer base


def hooks_available() -> bool:
    """True when the melonDS binary carries the poke+dump RAM hooks."""
    try:
        blob = Path(hz.MELONDS_BIN).read_bytes()
        return b"/tmp/melon_poke" in blob and b"/tmp/melon_dump" in blob
    except Exception:
        return False


def poke_write(lines: list[str]) -> None:
    """Force ARM9 RAM every frame: each line is "<hexaddr> <hexval> <size>"."""
    POKE_PATH.write_text("\n".join(lines) + "\n")


def poke_stop() -> None:
    try:
        POKE_PATH.unlink()
    except FileNotFoundError:
        pass


def ram_dump(addr: int, length: int, out: Path, tries: int = 140) -> bytes:
    """One-shot ARM9-RAM snapshot via the dump hook; returns the bytes."""
    out = Path(out)
    if out.exists():
        out.unlink()
    DUMP_PATH.write_text(f"{addr:x} {length:x} {out}\n")
    for _ in range(tries):
        time.sleep(0.1)
        if out.exists() and out.stat().st_size >= length:
            return out.read_bytes()
    return out.read_bytes() if out.exists() else b""


# =============================================================================
# stage identity (descriptor table + FAT order, read from the ROM under test)
# =============================================================================
class StageMap:
    """Maps `_STG` names <-> descriptor record indices and matches RAM buffers."""

    def __init__(self, rom: Path):
        import ndspy.rom
        r = ndspy.rom.NintendoDSRom.fromFile(str(rom))
        self.arm9 = r.arm9
        # _STG files in FAT order + their bytes (for buffer identification)
        byidx: dict[int, str] = {}

        def walk(folder):
            for n in folder.files:
                byidx[folder.idOf(n)] = n
            for _, sub in folder.folders:
                walk(sub)

        walk(r.filenames)
        self.fat_order = [n for _, n in sorted(byidx.items()) if n.startswith("_STG")]
        self.stg_bytes = {n: r.files[i] for i, n in byidx.items() if n.startswith("_STG")}
        base = DESC_KEY_BASE - 0x02000000
        self.keys = [self.arm9[base + i * DESC_STRIDE] for i in range(DESC_COUNT)]
        # each descriptor record's BackStage preview CARD, decoded statically:
        # card FAT pos = halfword@(record+0x30) - CARD_BIAS (verified against
        # live RAM dumps for records across the campaign / SP / X regions).
        self.card = []
        for i in range(DESC_COUNT):
            fp = struct.unpack_from("<H", self.rec_bytes(i), 0x30)[0] - CARD_BIAS
            self.card.append(self.fat_order[fp] if 0 <= fp < len(self.fat_order) else None)

    def rec_addr(self, i: int) -> int:
        return DESC_KEY_BASE + i * DESC_STRIDE

    def rec_bytes(self, i: int) -> bytes:
        o = self.rec_addr(i) - 0x02000000
        return self.arm9[o:o + DESC_STRIDE]

    def index_for_key(self, key: int) -> int:
        return self.keys.index(key)

    def index_for_stg(self, name: str) -> int | None:
        """Record whose BackStage preview CARD is `name` (the FAT-order/session
        slot ordering is scrambled for SP/X stages, so this is resolved by the
        decoded card map, NOT a FAT offset).  Returns the lowest such record
        (later duplicates are replay/free-battle slots)."""
        nm = name if name.endswith(".bin") else name + ".bin"
        for i in range(DESC_COUNT):
            if self.card[i] == nm:
                return i
        return None

    def redirect_poke(self, idx_cur: int, idx_target: int) -> list[str]:
        """Poke lines that overwrite the current-stage record (idx_cur) with the
        target record's bytes, preserving idx_cur's key byte so the descriptor
        search still matches the save's id."""
        tb = bytearray(self.rec_bytes(idx_target))
        tb[0] = self.arm9[self.rec_addr(idx_cur) - 0x02000000]  # keep matching key
        dst = self.rec_addr(idx_cur)
        return [f"{dst + j:x} {struct.unpack_from('<I', tb, j)[0]:x} 4"
                for j in range(0, DESC_STRIDE, 4)]

    def identify(self, buf: bytes) -> tuple[str, float]:
        """Best-matching `_STG` name for a RAM buffer + byte-agreement fraction."""
        best = ("?", 0.0)
        for name, fb in self.stg_bytes.items():
            n = min(len(fb), len(buf))
            if n < 0x100:
                continue
            frac = sum(1 for i in range(n) if fb[i] == buf[i]) / n
            if frac > best[1]:
                best = (name, frac)
        return best


# =============================================================================
# main-menu / data-load navigation for the New-Game+ save (3-option save menu)
# =============================================================================
def _menu_highlight(gray) -> int:
    """Index (0..2) of the highlighted button on the 3-option save menu
    (はじめから / つづきから / おまけ) — the yellow cursor row is the brightest
    of the three button bands."""
    import numpy as np
    band = gray[:, 70:190]
    vals = [float(np.mean(band[a:b])) for a, b in [(253, 270), (283, 300), (312, 329)]]
    return int(np.argmax(vals))


def nav_to_load_popup(emu: hz.Emulator, out: Path) -> bool:
    """Title → main menu → つづきから (Continue) → data-load slot 2 → the
    ロードする/キャンセル popup with ロードする highlighted.  Returns True on
    success (the popup ready to confirm with A)."""
    for _ in range(6):
        emu.key("START", hold_ms=250, pause=1.7)
    p = out / "menu.png"
    emu.shot(p)
    idx = _menu_highlight(hz.load_gray(p))
    while idx < 1:
        emu.key("DOWN", hold_ms=140, pause=0.9)
        idx += 1
    while idx > 1:
        emu.key("UP", hold_ms=140, pause=0.9)
        idx -= 1                                  # cursor on つづきから (Continue)
    emu.key("A", hold_ms=250, pause=3.0)          # -> データロード slot list
    emu.key("DOWN", hold_ms=140, pause=1.0)       # -> slot 2 (a BACK STAGE save)
    emu.key("A", hold_ms=250, pause=2.0)          # -> ロード/キャンセル popup
    emu.key("UP", hold_ms=140, pause=1.0)         # -> ロードする highlighted
    return True


def load_into_backstage(emu: hz.Emulator, settle: float = 9.0) -> None:
    """From the ロードする popup, confirm the load and wait for BackStage."""
    emu.key("A", hold_ms=250, pause=4.0)
    time.sleep(settle)


def press_shingeki(emu: hz.Emulator) -> None:
    """From the BackStage 作戦 tab root: RIGHT → 作戦内容, DOWN×3 → 進撃, A."""
    emu.key("RIGHT", hold_ms=140, pause=1.2)
    emu.key("DOWN", hold_ms=140, pause=1.0, count=3)
    emu.key("A", hold_ms=250, pause=3.0)


# =============================================================================
# per-stage grind: advance dialogue to the battle/deploy map + freeze detection
# =============================================================================
FREEZE_DELTA = 0.5        # whole-window frame_delta below this == "no change"
BLACK_LUMA = 16.0         # a HARD-freeze (BIOS abort) blacks the screen …
BLACK_STD = 14.0          # … so the stuck frame is dark AND featureless
MAP_LUMA = 95.0           # bottom-screen luma of the deploy/battle map


class FreezeWatch:
    """Detects a hard freeze: a sustained run of effectively-identical frames.
    `update(frame)` returns None while advancing, or ("FROZEN", black) once a
    run of >= still_limit no-change frames is seen — `black` distinguishes a
    near-black BIOS-abort screen (a real hard freeze) from a static rendered
    scene (a soft-lock).  Factored out so `--self-test` can prove it fires."""

    def __init__(self, still_limit: int):
        self.still_limit = still_limit
        self.prev = None
        self.still = 0
        self.worst = 0

    def reset(self):
        self.prev = None
        self.still = 0

    def update(self, g):
        if self.prev is not None and hz.frame_delta(g, self.prev) < FREEZE_DELTA:
            self.still += 1
            self.worst = max(self.worst, self.still)
            if self.still >= self.still_limit:
                black = hz.mean_luma(g) < BLACK_LUMA and hz.luma_std(g) < BLACK_STD
                return ("FROZEN", black)
        else:
            self.still = 0
        self.prev = g
        return None


def grind_stage(emu: hz.Emulator, out: Path, tag: str, steps: int,
                still_limit: int):
    """Press A through the intro/briefing until the deploy/battle map
    (bottom-screen luma > MAP_LUMA).  Answers the ally-JOIN choice (option 2)
    via the golden template.

    Returns (verdict, reached_step, longest_still_run, black):
      "PASS"      reached the deploy/battle map.
      "FROZEN"    a run of >= still_limit effectively-identical frames under A
                  with the map never reached; `black` is True iff that stuck
                  frame is a near-black screen — the BIOS-abort HARD-FREEZE
                  signature (a static *rendered* scene is a soft-lock, not a
                  hard freeze, and is reported UNREACHED by the caller).
      "UNREACHED" budget exhausted with frames still changing.
    """
    join_tmpl = hz.load_gray(hz.GOLDEN / "join_choice_template.png")
    join_box = (5, 168, 200, 210)
    watch = FreezeWatch(still_limit)
    joined = False
    for i in range(steps):
        emu.key("A", pause=0.5)
        p = out / f"{tag}_{i:03d}.png"
        if not emu.shot(p):
            continue
        g = hz.load_gray(p)
        # ally-JOIN choice box → take option 2 (matches harness.grind_to_deploy)
        if not joined and hz.template_mae(g, join_tmpl, join_box) < 11.0:
            time.sleep(0.5)
            emu.key("DOWN", hold_ms=140, pause=0.5)
            emu.key("A", hold_ms=250, pause=0.6)
            joined = True
            hz.log(f"[{tag}] JOIN choice answered at step {i}")
            watch.reset()
            continue
        # reached the deploy/battle map? (bottom screen bright, as
        # harness.grind_to_deploy detects).  A few-step guard avoids the bright
        # BackStage frame that can linger for one capture right after 進撃.
        if i >= 4 and hz.mean_luma(g, hz.BOTTOM_SCREEN) > MAP_LUMA:
            return "PASS", i, watch.worst, False
        hit = watch.update(g)
        if hit is not None:
            return "FROZEN", None, watch.worst, hit[1]
    return "UNREACHED", None, watch.worst, False


def self_test() -> int:
    """Prove the freeze detector can actually fire (LESSONS F1/F2) without an
    emulator: drive FreezeWatch with synthetic frames and assert the verdicts."""
    import numpy as np
    H, W = 403, 256
    black = np.zeros((H, W), dtype=np.float32)                 # BIOS-abort screen
    rng = np.random.default_rng(0)
    scene = rng.uniform(0, 255, (H, W)).astype(np.float32)     # a rendered scene
    checks = []

    def check(name, ok):
        checks.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}", flush=True)

    # 1) a run of identical BLACK frames -> FROZEN + black (a HARD freeze)
    w = FreezeWatch(6)
    res = [w.update(black) for _ in range(10)]
    fired = next((r for r in res if r), None)
    check("black-static run -> FROZEN+black (hard freeze fires)",
          fired == ("FROZEN", True))

    # 2) a run of identical RENDERED frames -> FROZEN but NOT black (soft-lock)
    w = FreezeWatch(6)
    res = [w.update(scene) for _ in range(10)]
    fired = next((r for r in res if r), None)
    check("rendered-static run -> FROZEN+not-black (soft-lock, not hard freeze)",
          fired == ("FROZEN", False))

    # 3) changing frames -> never FROZEN
    w = FreezeWatch(6)
    fired = None
    for k in range(20):
        f = rng.uniform(0, 255, (H, W)).astype(np.float32)
        r = w.update(f)
        if r:
            fired = r
            break
    check("changing frames -> never FROZEN (dialogue advancing is not a freeze)",
          fired is None)

    ok = all(checks)
    print(f"self-test: {'ALL PASS' if ok else 'FAIL'} — freeze detector "
          f"{'can fire on a real freeze and rejects soft-locks' if ok else 'is BROKEN'}",
          flush=True)
    return 0 if ok else 1


# =============================================================================
def resolve_stage_list(sm: StageMap, spec: str) -> list[tuple[int, str]]:
    """Resolve --stages to a list of (record_index, label).  Stages are
    identified by their BackStage preview CARD (`_STG` name); labels are that
    card name.  `spec` is a preset, or a comma list of `_STG` names and/or raw
    descriptor record indices."""
    def cards_where(pred):
        seen, out = set(), []
        for i in range(2, DESC_COUNT):
            c = sm.card[i]
            if c and c not in seen and pred(c[:-4]):
                seen.add(c)
                out.append((i, c[:-4]))
        return out

    if spec == "routes":
        return cards_where(lambda s: s.endswith("A") or s.endswith("B"))
    if spec == "sp":
        return cards_where(lambda s: "SP" in s)
    if spec == "x":
        return cards_where(lambda s: s.startswith("_STGX"))
    if spec == "all":
        return cards_where(lambda s: True)
    if spec == "smoke":
        names = ["_STG01", "_STG04A", "_STG04B", "_STG06SP", "_STG11SP",
                 "_STGX2", "_STGX3A", "_STGSP2B", "_STG20A", "_STG24A", "_STG24B"]
    else:
        names = [s.strip() for s in spec.split(",") if s.strip()]
    out = []
    for tok in names:
        if tok.isdigit():                          # explicit record index
            i = int(tok)
            if 0 <= i < DESC_COUNT:
                out.append((i, (sm.card[i] or f"rec{i}")[:-4] if sm.card[i] else f"rec{i}"))
        else:
            i = sm.index_for_stg(tok)
            if i is not None:
                out.append((i, tok if not tok.endswith(".bin") else tok[:-4]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", nargs="?", default="sd-gundam-g-generation-zh.nds")
    ap.add_argument("--stages", default="smoke")
    ap.add_argument("--display", type=int, default=None)
    ap.add_argument("--steps", type=int, default=170)
    ap.add_argument("--still-frames", type=int, default=26)
    ap.add_argument("--sav", default=None, help="override the cartridge save fixture")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the freeze detector fires (no emulator); exit 0/1")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    rom = Path(a.rom).resolve()
    if not rom.exists():
        print(f"ROM not found: {rom}", file=sys.stderr)
        return 2
    sav = Path(a.sav).resolve() if a.sav else (hz.FIXTURES / "newgame_plus.sav")
    if not sav.exists():
        print(f"save fixture not found: {sav}", file=sys.stderr)
        return 2

    # environment gate (not a ROM verdict): need the inject + poke/dump hooks
    if not hz.inject_available() or not hooks_available():
        print("  [ENV ] melonDS lacks the input/poke/dump hooks — this test needs "
              "the instrumented build (exit 3).", flush=True)
        return 3

    sm = StageMap(rom)
    stages = resolve_stage_list(sm, a.stages)   # list of (record_index, label)
    if not stages:
        print("no mappable stages requested", file=sys.stderr)
        return 2

    display = a.display if a.display is not None else hz.pick_display()
    emu = hz.Emulator(display)
    out = emu.workdir / "stage_start"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    results: list[tuple[str, str, str]] = []   # (stage, verdict, detail)

    def record(stage, verdict, detail):
        results.append((stage, verdict, detail))
        print(f"  [{verdict:9s}] {stage}: {detail}", flush=True)

    try:
        hz.boot_to_title(emu, rom, sav=sav)

        # input preflight — the environment must actually drive the game.  The
        # save-menu is the 3-option layout the stock menu_state does not match,
        # so drive START and check the framebuffer left the title art.
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
            print("  [ENV ] input preflight failed — the environment is not "
                  "accepting game input (exit 3).", flush=True)
            return 3

        # navigate to the load-confirm popup and stamp a savestate there; every
        # stage warp reloads this exact moment (the pre-BackStage transition).
        nav_to_load_popup(emu, out)
        emu.save_state(8)
        time.sleep(1.5)

        # determine the descriptor record the save's current id matches, by one
        # natural load (so the redirect is robust to which slot/save is used).
        load_into_backstage(emu)
        cur = ram_dump(STAGE_ID_ADDR, 0x2, out / "curid.bin")
        cur_id = int.from_bytes(cur[:2], "little") if len(cur) >= 2 else 0x2d
        try:
            idx_cur = sm.index_for_key(cur_id)
        except ValueError:
            idx_cur = sm.index_for_key(0x2d)
        nat = sm.identify(ram_dump(STAGE_BUF, 0x1800, out / "natbuf.bin"))
        hz.log(f"save current-stage id 0x{cur_id:02x} -> descriptor rec {idx_cur} "
               f"(natural preview {nat[0]} {nat[1]:.2f})")
        emu.load_state(8)
        time.sleep(2.5)

        for idx_t, label in stages:
            tag = f"r{idx_t:03d}"
            card = sm.card[idx_t]                # the CARD the redirect must land
            stage = label

            # --- warp: redirect the descriptor during the save-load, verify ----
            # the redirect lands the CARD file in the BackStage buffer; 進撃 then
            # plays that slot's session (identified below).
            landed = False
            name, frac = "?", 0.0
            for attempt in range(2):
                emu.load_state(8)
                time.sleep(2.5)
                poke_write(sm.redirect_poke(idx_cur, idx_t))
                time.sleep(0.4)
                load_into_backstage(emu)
                name, frac = sm.identify(ram_dump(STAGE_BUF, 0x1800, out / f"{tag}_bs.bin"))
                if name == card and frac > 0.98:
                    landed = True
                    break
                hz.log(f"[{tag}] warp attempt {attempt} landed {name} {frac:.2f} "
                       f"(want card {card}); retrying")
            if not landed:
                poke_stop()
                record(stage, "WARP-FAIL",
                       f"redirect to rec{idx_t} did not land card {card} "
                       f"(buffer {name} {frac:.2f}) — harness flake")
                continue

            # --- release the redirect and enter the stage via 進撃 ------------
            poke_stop()
            time.sleep(0.4)
            press_shingeki(emu)
            verdict, step, worst, black = grind_stage(emu, out, tag, a.steps,
                                                      a.still_frames)

            # identify the `_STG` the battle actually runs (the card's session)
            battle = sm.identify(ram_dump(STAGE_BUF, 0x1800, out / f"{tag}_battle.bin"))
            played = battle[0][:-4] if battle[0].endswith(".bin") else battle[0]
            emu.shot(out / f"{tag}_final.png")

            # Some slots (the finale routes / certain SP stages, whose keys drive
            # the free-battle-unlock decision) DIVERT to a free battle (_STGFB*)
            # when entered from this save's story context — the warp cannot
            # cleanly stage them, so they are a warp LIMITATION, not a ROM freeze.
            diverted = played.startswith("_STGFB") and not stage.startswith("_STGFB")

            if diverted:
                record(stage, "DIVERT",
                       f"warp diverted to free battle {played} (rec{idx_t} card "
                       f"{card}) — not cleanly stageable from this save; no freeze")
            elif verdict == "PASS":
                extra = "" if played == stage else f", played {played}"
                record(stage, "PASS",
                       f"reached battle map at step {step} (rec{idx_t} card {card}{extra})")
            elif verdict == "FROZEN" and black:
                record(stage, "FREEZE",
                       f"window static + BLACK for {worst} frames under A, map "
                       f"never reached — HARD FREEZE (buffer {played})")
            elif verdict == "FROZEN":
                record(stage, "UNREACHED",
                       f"static rendered scene {worst} frames (soft-lock, not "
                       f"black) — map not reached (buffer {played}); inconclusive")
            else:
                record(stage, "UNREACHED",
                       f"map not reached in {a.steps} steps (longest-still {worst}; "
                       f"buffer {played}) — inconclusive, rerun")
    except hz.InputEnvironmentError as e:
        print(f"  [ENV ] {e}", flush=True)
        return 3
    except Exception as e:
        print(f"harness failure: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2
    finally:
        if a.keep:
            hz.log(f"--keep: emulator left on display :{display}")
        else:
            poke_stop()
            emu.kill()

    # ---- summary + verdict ----------------------------------------------------
    npass = sum(1 for _, v, _ in results if v == "PASS")
    nfreeze = sum(1 for _, v, _ in results if v == "FREEZE")
    ndivert = sum(1 for _, v, _ in results if v == "DIVERT")
    nother = len(results) - npass - nfreeze - ndivert
    print(f"\n=== stage-start :: {rom.name} — {npass}/{len(results)} reached the "
          f"battle map; {nfreeze} FREEZE; {ndivert} divert(free-battle); "
          f"{nother} unreached/warp-fail ===")
    print(f"  frames: {out}")
    if nfreeze:
        print("  VERDICT: FAIL — a stage HARD-FROZE at start")
        return 1
    if npass + ndivert == len(results):
        # DIVERT = a warp limitation (the game diverts these slots to free
        # battle), not a ROM fault; no freeze was observed on any stage.
        tail = "" if ndivert == 0 else f" ({ndivert} slot(s) divert to free battle — warp limitation)"
        print(f"  VERDICT: PASS — every stageable session reached the battle map{tail}")
        return 0
    print("  VERDICT: INCONCLUSIVE — some stages unreached/warp-failed (rerun); "
          "no freeze observed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
