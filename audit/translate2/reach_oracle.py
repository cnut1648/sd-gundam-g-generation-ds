#!/usr/bin/env python3
"""Completeness oracle for stage reachability.

Ground truth = data/zh (v1.1), which was authored from IN-GAME PIXELS, so every
dialogue offset it edited is provably a real on-screen display block, immune to
the extractor's decoder/CFG bugs. A reachability algorithm is COMPLETE only if
it reaches every one of these offsets.

Usage:
    from reach_oracle import must_reach, check
    mr = must_reach()                       # {stage_fname: set(int offsets)}
    check({fname: set_of_reached_offsets})  # prints coverage + misses, returns dict

`check` accepts, per stage file, the set of block-start offsets your algorithm
reaches. It reports total coverage and, per stage, the still-missed offsets with
their JP text (so you can see what path you failed to follow).
"""
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from utils import stage_text                       # noqa: E402
from utils.extract.gamerom import GameROM          # noqa: E402
from utils.extract import walkers as W             # noqa: E402

JP_ROM = str(REPO / "0098 - SD Gundam G Generation DS (Japan).nds")


def must_reach() -> dict[str, set[int]]:
    """{stage_fname: set(offsets)} of every data/zh-edited dialogue block —
    the pixel-verified display set the algorithm MUST reach."""
    out: dict[str, set[int]] = {}
    for fname, sd in stage_text.iter_stage_data():
        offs = {int(e["jp_offset"], 16) for e in sd.get("edits", [])
                if e.get("kind") == "dialogue"}
        if offs:
            out[fname] = offs
    return out


def check(reached: dict[str, set[int]], show_missed: int = 12) -> dict:
    """Compare a candidate reached-set to the oracle. reached[fname] = set(offs)."""
    rom = GameROM(JP_ROM)
    mr = must_reach()
    tot = tot_hit = 0
    per = {}
    for fname, need in sorted(mr.items()):
        got = reached.get(fname, set())
        hit = need & got
        miss = need - got
        tot += len(need); tot_hit += len(hit)
        per[fname] = {"need": len(need), "hit": len(hit), "missed": sorted(miss)}
        if miss:
            blocks = {int(b["off"], 16): b for b in W.stage_blocks(rom, fname)}
            sample = []
            for off in sorted(miss)[:show_missed]:
                b = blocks.get(off)
                sample.append((hex(off), (b or {}).get("text", "<no block>")[:40]))
            per[fname]["sample"] = sample
    print(f"COVERAGE: {tot_hit}/{tot} pixel-verified offsets reached "
          f"({100*tot_hit/max(1,tot):.2f}%); missed {tot-tot_hit}")
    worst = sorted(((v["need"]-v["hit"], k) for k, v in per.items()), reverse=True)
    for missn, k in worst[:20]:
        if missn:
            print(f"  {k}: missed {missn}/{per[k]['need']}  e.g. {per[k].get('sample', [])[:4]}")
    return {"total": tot, "hit": tot_hit, "per_stage": per}


if __name__ == "__main__":
    # baseline: how complete is the CURRENT static walk?
    rom = GameROM(JP_ROM)
    cur = {}
    for fname in must_reach():
        cur[fname] = {int(b["off"], 16) for b in W.stage_blocks(rom, fname)
                      if "scene" in b}                # reachable:true only (the CFG walk)
    print("=== CURRENT static CFG walk (reachable:true) vs oracle ===")
    check(cur)
