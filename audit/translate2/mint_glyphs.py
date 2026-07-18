#!/usr/bin/env python3
"""Glyph mint tool (docs/LESSONS_LEARNED §G procedure).

--census          classify every ZH-band cell: usage (token-scan of ALL data/**
                  hex fields, token-aware), text demand (chars in every data/zh
                  string field + fleet staging outputs), junk/free cells.
--mint            mint the demanded chars (from staging/glyph_gap.txt) into
                  free/junk cells first, then zero-usage zero-demand reclaimed
                  cells; paints WQY-12px + L-shadow; updates charmap+atlas.
--report          text-art crops of every cell minted this run (eyeball check).

Charmap & atlas move together in ONE run; reclaimed chars lose their
registration and their slot_chars_extra identity in the same edit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from utils import text_codec  # noqa: E402
from pcf_raster import PCF, raster12, apply_shadow, cell_bytes, cell_to_grid, show  # noqa: E402

STG = HERE / "staging"
HEX_KEYS = {"payload_hex", "zh_hex", "new_hex", "hex", "header"}
SKIP_KEYS = {"old_hex", "jp_hex"}
PROTECT = set("0123456789ABCDEFGHIJKLMNOPRSTUVWXZ+%·！…（）∀νβα")


def iter_json_values(obj, key=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_json_values(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_json_values(v, key)
    elif isinstance(obj, str):
        yield key, obj


def _encoded_text_fields(path, d):
    """Yield the string fields that the BUILD actually encodes (zh with no
    sibling zh_hex in data-file tables); annotation-only fields don't create
    glyph demand (they're re-synced from payloads)."""
    out = []

    def walk(obj):
        if isinstance(obj, dict):
            if "zh" in obj and isinstance(obj["zh"], str) and "zh_hex" not in obj:
                out.append(obj["zh"])
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    if "data/zh/files" in str(path):
        walk(d)
    return out


def census():
    cm = json.loads((REPO / "data/charmap.json").read_text())
    used_tokens = set()          # ZH-band slots referenced by committed bytes
    text_chars = set()           # chars the build (or the fleet) will encode
    for p in sorted((REPO / "data").rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        for k, v in iter_json_values(d):
            if k in SKIP_KEYS:
                continue
            if k in HEX_KEYS and len(v) % 2 == 0 and len(v) >= 2:
                try:
                    b = bytes.fromhex(v)
                except ValueError:
                    pass
                else:
                    for _o, tok, ln in text_codec.iter_tokens(b):
                        if ln == 2 and tok < 0xF000:
                            slot = tok - 0xE000 + 224
                            if slot >= 2196:
                                used_tokens.add(slot)
                    continue
        for t in _encoded_text_fields(p, d):
            text_chars.update(t)
    # fleet future demand — GAME-TEXT fields only (notes/web_sources are
    # agent prose, never encoded; protecting their chars starves the mint)
    GAME_TEXT_KEYS = {"name_zh", "zh", "cutin_zh", "detail_effect_zh",
                      "defense_name_zh"}
    GAME_TEXT_LIST_KEYS = {"ability_segments_zh", "defense_segments_zh"}
    for p in sorted((STG / "out").rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue

        def walk_out(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in GAME_TEXT_KEYS and isinstance(v, str):
                        text_chars.update(v)
                    elif k in GAME_TEXT_LIST_KEYS and isinstance(v, list):
                        for s in v:
                            if isinstance(s, str):
                                text_chars.update(s)
                    else:
                        walk_out(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk_out(v)
        walk_out(d)
    zh2 = cm["two_byte_zh"]
    zh_band = {ch: s for ch, s in zh2.items() if s >= 2196}
    slots_reg = {s: ch for ch, s in zh_band.items()}
    extra = {int(k): v for k, v in cm.get("slot_chars_extra", {}).items()
             if int(k) >= 2196}
    unregistered = [s for s in range(2196, 4320) if s not in slots_reg]
    reclaimable = []
    for s, ch in sorted(slots_reg.items()):
        if s in used_tokens or ch in text_chars or ch in PROTECT:
            continue
        lo = (s - 224 + 0xE000) & 0xFF
        reclaimable.append({"slot": s, "char": ch, "token_lo": f"0x{lo:02X}"})
    free = []
    for s in unregistered:
        lo = (s - 224 + 0xE000) & 0xFF
        free.append({"slot": s, "identity": extra.get(s), "used": s in used_tokens,
                     "token_lo": f"0x{lo:02X}"})
    return {"free_cells": free, "reclaimable": reclaimable,
            "n_used_tokens": len(used_tokens), "n_text_chars": len(text_chars),
            "data_used_tokens": used_tokens}


# ---------------------------------------------------------------------------
# JP-band census (AGENTS.md: JP-band cells only for chars that will NEVER
# appear on a trampoline surface — dialogue/bark/cutin-only — and their JP
# tokens must be candidate-ROM token-free).
#
# Token-free proof, conservative by construction:
#   * the candidate ROM == JP ROM + data/zh transform, so its text universe is
#     (residual JP text) ∪ (data/** payloads).  data/** hex fields are scanned
#     token-aware by census(); residual JP text is a subset of the JP ROM's
#     text surfaces, which we over-approximate with a sliding BYTE-PAIR scan
#     (every 0xE0..0xEF byte + successor marks a token, any alignment) over
#     every text-bearing file + every arm9 text band + both macro dictionaries.
#     Over-marking only shrinks the candidate set — it can never garble.
# ---------------------------------------------------------------------------
TEXT_FILES = ["0.bin", "1.bin", "1da.bin", "1db.bin", "1dc.bin", "1dd.bin",
              "1de.bin", "1df.bin", "1e0.bin", "31e.bin", "324.bin", "388.bin",
              "3d3.bin", "3d5.bin", "478.bin", "48a.bin", "b6e.bin", "b6f.bin",
              "c4b.bin", "c4f.bin"]


def _mark_pairs(buf: bytes, marked: set):
    for i in range(len(buf) - 1):
        b = buf[i]
        if 0xE0 <= b <= 0xEF:
            marked.add(((b << 8) | buf[i + 1]) - 0xE000 + 224)


def jp_band_census(data_used_tokens: set, exclude_files: tuple = ()):
    """Candidate-ROM token scan per AGENTS.md §G: the proof universe is
    data/** hex (in data_used_tokens) + the BUILT ZH ROM's text surfaces.
    ``exclude_files`` lists banks the pending apply replaces wholesale (their
    residual JP text vanishes from the candidate ROM), e.g. the library bio
    banks — their kana-heavy JP prose is what pins most JP-band cells."""
    import utils.extract.layout as L
    from utils.extract.gamerom import GameROM
    jp = GameROM(str(REPO / "sd-gundam-g-generation-zh.nds"))
    cm = json.loads((REPO / "data/charmap.json").read_text())
    marked: set = set(data_used_tokens)
    # every text-bearing NitroFS file (incl. all 101 stage scripts)
    from utils import rom as romlib
    nds = romlib.load_rom(str(REPO / "sd-gundam-g-generation-zh.nds"))
    stage_files = [n for n in nds.filenames.files if n.startswith("_STG")]
    for fn in TEXT_FILES + stage_files:
        if fn in exclude_files:
            continue
        _mark_pairs(jp.file(fn), marked)
    # arm9 text bands: JP string pools, inline story text, both dictionaries,
    # the UI dictionary region (0x12D770..renderB font) and post-dict labels
    a9 = jp.arm9
    bands = list(L.JP_STRING_BANDS) + [
        (L.EVENT_TEXT_LO, L.EVENT_TEXT_HI),
        (L.DICT_TEXT, L.RENDERB_OFF),          # dialogue dict + UI dict region
        (L.DICT_SYS, 0x14AC34),                # system dict up to label band
    ]
    for lo, hi in bands:
        _mark_pairs(a9[lo:hi], marked)
    jp_ids = {int(k): v for k, v in cm.get("jp_slot_chars", {}).items()}
    registered = set(cm["two_byte_zh"].values())
    cands = []
    for s in range(224, 2196):
        if s in marked or s in registered:
            continue
        cands.append({"slot": s, "jp_identity": jp_ids.get(s)})
    return cands


def load_demand():
    out = []
    gg = (STG / "glyph_gap.txt")
    if gg.exists():
        for line in gg.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                out.append((parts[1], int(parts[2]), parts[0]))
    # dedupe keep max count
    agg = {}
    for ch, n, surf in out:
        agg[ch] = (agg.get(ch, (0,))[0] + n, surf)
    return sorted(agg.items(), key=lambda kv: -kv[1][0])


def mint(dry=False):
    cen = census()
    demand = load_demand()
    cm_path = REPO / "data/charmap.json"
    atlas_path = REPO / "data/font/atlas12.bin"
    cm = json.loads(cm_path.read_text())
    atlas = bytearray(atlas_path.read_bytes())
    pcf = PCF()
    # candidate slots: free junk cells (token-unused) then reclaimable
    cands = [f["slot"] for f in cen["free_cells"] if not f["used"]]
    cands += [r["slot"] for r in cen["reclaimable"]]
    # avoid token-hazard lows: 0x00 forbidden, 0x15 avoided (stage scripts)
    def ok_slot(s):
        lo = (s - 224 + 0xE000) & 0xFF
        return lo != 0x00 and lo != 0x15
    cands = [s for s in cands if ok_slot(s)]
    # stage-only overflow tier: token-free JP-band cells (never encodable on
    # bank surfaces by construction — slot_of(surface="bank") refuses < 2196)
    # this apply replaces the three library banks wholesale; their residual
    # JP text is not part of the candidate ROM (bio_apply rewrites every byte)
    jb = [c["slot"] for c in jp_band_census(
        cen["data_used_tokens"], exclude_files=("324.bin", "c4b.bin", "31e.bin"))]
    jb = [s for s in jb if ok_slot(s)]
    # promotions: demanded chars whose atlas identity is already EXACTLY the
    # demanded character — register as encodable, no repaint.  Identity-true
    # reuse cannot garble: any other referent of the token renders the same
    # glyph it always did.  Sources: slot_chars_extra (decode-only bitmap
    # identities) and jp_slot_chars (original JP identities whose unicode char
    # equals the demanded simplified char, e.g. 香/柏/墨).  0x00-low tokens
    # remain unusable as encode targets.
    PROMOTE = {"向": 4257}
    extra_ids = {v: int(k) for k, v in cm.get("slot_chars_extra", {}).items()}
    jp_ids2 = {v: int(k) for k, v in cm.get("jp_slot_chars", {}).items()}
    promoted = []
    for ch, (n, surf) in demand:
        if ch in cm["two_byte_zh"]:
            continue
        slot = PROMOTE.get(ch, extra_ids.get(ch, jp_ids2.get(ch)))
        if slot is None or not ok_slot(slot):
            continue
        ident = (cm.get("slot_chars_extra", {}).get(str(slot))
                 or cm.get("jp_slot_chars", {}).get(str(slot)))
        if ident == ch:
            promoted.append((ch, slot))
    plan, fails = [], []
    for ch, (n, surf) in demand:
        if ch in cm["two_byte_zh"] or any(ch == pc for pc, _ in promoted):
            continue
        g = raster12(pcf, ch)
        if g is None:
            fails.append((ch, "no WQY glyph"))
            continue
        # stage-only chars go to JP-band cells first, preserving the scarce
        # ZH-band cells for chars that may ever need a trampoline surface
        if surf == "stage" and jb:
            slot = jb.pop(0)
        elif cands:
            slot = cands.pop(0)
        else:
            fails.append((ch, "no free slot"))
            continue
        plan.append((ch, slot, n))
    print(f"demand {len(demand)} chars; plan {len(plan)}; fails {fails}")
    if dry:
        for ch, slot in promoted:
            print(f"  {ch} -> slot {slot} (PROMOTED, no repaint)")
        for ch, slot, n in plan:
            old = next((r["char"] for r in cen["reclaimable"] if r["slot"] == slot), None)
            print(f"  {ch} (x{n}) -> slot {slot}" + (f" (reclaims {old!r})" if old else " (free cell)"))
        return
    rep_lines = []
    for ch, slot in promoted:
        cm["slot_chars_extra"].pop(str(slot), None)
        cm["two_byte_zh"][ch] = slot
        rep_lines.append(f"=== {ch} -> slot {slot} (PROMOTED from slot_chars_extra, "
                         "bitmap already correct)")
    for ch, slot, n in plan:
        grid = apply_shadow(raster12(pcf, ch))
        atlas[slot * 36:slot * 36 + 36] = cell_bytes(grid)
        # unregister the reclaimed char & stale identity
        for och, oslot in list(cm["two_byte_zh"].items()):
            if oslot == slot:
                del cm["two_byte_zh"][och]
        cm.get("slot_chars_extra", {}).pop(str(slot), None)
        cm["two_byte_zh"][ch] = slot
        rep_lines.append(f"=== {ch} -> slot {slot} (demand x{n})\n" + show(grid))
    atlas_path.write_bytes(bytes(atlas))
    cm_path.write_text(json.dumps(cm, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    (STG / "mint_report.txt").write_text("\n".join(rep_lines) + "\n", encoding="utf-8")
    print(f"minted {len(plan)} glyphs; report at staging/mint_report.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--mint", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if args.census:
        cen = census()
        print(f"used ZH-band tokens: {cen['n_used_tokens']}")
        print(f"free cells: {len(cen['free_cells'])}")
        for f in cen["free_cells"]:
            print("  ", f)
        print(f"reclaimable (zero usage + zero demand): {len(cen['reclaimable'])}")
        for r in cen["reclaimable"][:40]:
            print("  ", r)
    if args.mint:
        mint(dry=args.dry)


if __name__ == "__main__":
    main()
