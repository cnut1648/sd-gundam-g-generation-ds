#!/usr/bin/env python3
"""Owner review settlement #2 (2026-07-17): flag rulings A(1-7) + B.

 A: cid455 炮台->强化型AI          (mislabel; relocate, grows)
    cid421 舰队司令->提坦斯士官     (SPLIT from cid551, which keeps 舰队司令)
    cid434 ->新吉翁资深士官         (SPLIT from 429/433 新吉翁士官)
    cid440 ->新吉翁老兵             (SPLIT from 435/439 新吉翁兵)
    cid443 ->新吉翁NT               (SPLIT from 435/439)
    cid404 金卡拉姆队大名->金卡拉姆队大大名 (solo ptr; grows)
    char_134 bark 圣母先锋号->先锋母舰 (staged edit; ship canon)
 B: cid46/47 卡缪（强化）->卡缪（觉醒）  (shared, same length)
    cid354 蕾兹->蕾珍 (+text refs 蕾兹专用机 etc.)
    cid205 米莎->米沙
    cid202/203 蜜安·法琳->蜜安·法伦 (shared, same length)
    cid466-group OZ特种兵->OZ特务队员 (check share at runtime)
    cid311/312 卡拉·苏恩->凯拉·辛 (shared, shorter)
    cid416 不死兵->僵尸兵 (needs the 僵 mint, done by mint_char())

Pilot names: pure ZH-band (A12).  New spans come from ledger-recorded
allocations; when the name band is full, proven-dead v1.1 residue spans
(zero arm9 word refs in the candidate ROM, zero json ptr refs, not in the
ledger) are used, recorded through the ledger like any allocation.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_apply import (open_roms, encode_bank, Pools, NAME_SAFE_RANGES,  # noqa: E402
                       RAM_BASE)
from apply_fleet import _surgical_write, load_json, dump_json  # noqa: E402

STG = HERE / "staging"
jp, zh = open_roms()

RENAMES = [  # (cids_to_rename, new_zh)  — sharers NOT listed keep the old name
    ([455], "强化型AI"),
    ([421], "提坦斯士官"),
    ([434], "新吉翁资深士官"),
    ([440], "新吉翁老兵"),
    ([443], "新吉翁NT"),
    ([404], "金卡拉姆大大名"),   # 7 cells (56px plate); rank 大大名 kept, 队 dropped
    ([46, 47], "卡缪（觉醒）"),
    ([354], "蕾珍"),
    ([205], "米沙"),
    ([202, 203], "蜜安·法伦"),
    ([311, 312], "凯拉·辛"),
    ([416], "僵尸兵"),
    ([466], "OZ特务队员"),
]

TEXT_SWEEPS = [  # game-text fields in staged outputs
    ("圣母先锋号", "先锋母舰"),
    ("蕾兹", "蕾珍"),
    ("米莎", "米沙"),
    ("卡拉·苏恩", "凯拉·辛"),
    ("不死兵", "僵尸兵"),
    ("蜜安·法琳", "蜜安·法伦"),
    ("OZ特种兵", "OZ特务队员"),
]

GAME_TEXT_KEYS = {"name_zh", "zh", "cutin_zh", "detail_effect_zh",
                  "defense_name_zh"}
GAME_TEXT_LIST_KEYS = {"ability_segments_zh", "defense_segments_zh"}


def mint_char(ch: str) -> int:
    """Mint one char into a census-clean ZH-band cell (charmap+atlas together)."""
    sys.path.insert(0, str(HERE))
    import mint_glyphs as MG
    from pcf_raster import PCF, raster12, apply_shadow, cell_bytes, show
    cm_path = HERE.parent.parent / "data/charmap.json"
    atlas_path = HERE.parent.parent / "data/font/atlas12.bin"
    cm = json.loads(cm_path.read_text())
    if ch in cm["two_byte_zh"]:
        return cm["two_byte_zh"][ch]
    cen = MG.census()
    cands = [f["slot"] for f in cen["free_cells"] if not f["used"]]
    cands += [r["slot"] for r in cen["reclaimable"]]
    cands = [s for s in cands
             if ((s - 224 + 0xE000) & 0xFF) not in (0x00, 0x15)]
    assert cands, "no ZH-band cell available"
    slot = cands[0]
    pcf = PCF()
    grid = apply_shadow(raster12(pcf, ch))
    atlas = bytearray(atlas_path.read_bytes())
    atlas[slot * 36:slot * 36 + 36] = cell_bytes(grid)
    for och, oslot in list(cm["two_byte_zh"].items()):
        if oslot == slot:
            del cm["two_byte_zh"][och]
    cm.get("slot_chars_extra", {}).pop(str(slot), None)
    cm["two_byte_zh"][ch] = slot
    atlas_path.write_bytes(bytes(atlas))
    cm_path.write_text(json.dumps(cm, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    print(f"minted {ch} -> slot {slot}")
    print(show(grid))
    return slot


def dead_name_band_spans(pools, chars_json, units_json, need: int):
    """Proven-dead cave spans inside the name band: entry RAM referenced by
    NOTHING (no arm9 u32 in the candidate ROM, no characters/units ptr, no
    ledger allocation).  Sorted smallest-first to minimize waste."""
    refs = set()
    a9 = zh.arm9
    for i in range(0, len(a9) - 3, 4):        # word-aligned scan (ptr words are)
        v = struct.unpack_from("<I", a9, i)[0]
        if 0x2186000 <= v < 0x2199000:
            refs.add(v)
    for c in chars_json["characters"]:
        if c.get("ptr"):
            refs.add(int(c["ptr"], 16))
        for i in c.get("ids", []):
            if i.get("name", {}).get("ptr"):
                refs.add(int(i["name"]["ptr"], 16))
    for u in units_json["units"]:
        if u.get("ptr"):
            refs.add(int(u["ptr"], 16))
        for w in u.get("weapons", []):
            if w.get("ptr"):
                refs.add(int(w["ptr"], 16))
    led = pools.ledger["allocations"] + pools._alloc_marks
    led_rams = {int(a["ram"], 16) for a in led if a.get("ram")}
    for a in led:                              # allocator-owned free pool
        for v in a.get("vacated", []) or []:
            if isinstance(v, dict) and v.get("ram"):
                led_rams.add(int(str(v["ram"]), 16))
    out = []
    caves = pools.caves
    for off in sorted(caves.entries):
        ram = caves.ram_of(off)
        foff = 0x186000 + off
        if not any(lo <= foff < hi for lo, hi in NAME_SAFE_RANGES):
            continue
        if ram in refs or ram in led_rams:
            continue
        p = bytes.fromhex(caves.entries[off]["payload_hex"])
        span = len(p)
        if span >= need:
            out.append((span, off, caves.entries[off].get("text", "")))
    return sorted(out)


def main():
    mint_char("僵")

    # -- staged-output sweeps ------------------------------------------------
    n = 0
    for p in sorted((STG / "out").rglob("*.json")):
        o = json.loads(p.read_text())

        def walk(obj):
            nonlocal_changed = 0
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    if k in GAME_TEXT_KEYS and isinstance(v, str):
                        w = v
                        for a, b in TEXT_SWEEPS:
                            w = w.replace(a, b)
                        if w != v:
                            obj[k] = w
                            nonlocal_changed += 1
                    elif k in GAME_TEXT_LIST_KEYS and isinstance(v, list):
                        for i, s in enumerate(v):
                            if isinstance(s, str):
                                w = s
                                for a, b in TEXT_SWEEPS:
                                    w = w.replace(a, b)
                                if w != s:
                                    v[i] = w
                                    nonlocal_changed += 1
                    else:
                        nonlocal_changed += walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    nonlocal_changed += walk(v)
            return nonlocal_changed

        c = walk(o)
        if c:
            p.write_text(json.dumps(o, ensure_ascii=False, indent=1) + "\n")
            n += c
    print(f"staged text sweeps: {n} field edits")

    # -- pilot renames -------------------------------------------------------
    pools = Pools(zh, jp)
    chars = load_json("data/zh/characters.json")
    units_json = load_json("data/zh/units.json")
    by_cid = {c["cid"]: c for c in chars["characters"]}
    by_ptr = {}
    for c in chars["characters"]:
        if c.get("ptr"):
            by_ptr.setdefault(c["ptr"], []).append(c["cid"])

    for cids, new_zh in RENAMES:
        c0 = by_cid[cids[0]]
        ptr = c0["ptr"]
        sharers = by_ptr.get(ptr, [])
        renaming_all_sharers = set(sharers) <= set(cids)
        old_ptr = int(ptr, 16)
        cur = zh.cstr(old_ptr) or b""
        enc = encode_bank(new_zh)           # pure ZH-band (A12)
        arena, off = pools.arena_of_ram(old_ptr)
        fix = f"settle2:pilot-name cid{cids[0]}"
        if renaming_all_sharers and arena is not None and len(enc) <= len(cur):
            _surgical_write(arena, off, enc + b"\x00", len(cur))
            for cid in cids:
                by_cid[cid]["zh"] = new_zh
            print(f"cid{cids}: in-place {new_zh} @{ptr}")
            continue
        # split or grow: allocate a NEW span (never touch the shared home)
        prior = next((a for a in pools.ledger["allocations"] + pools._alloc_marks
                      if a.get("fix") == fix), None)
        if prior is None:
            try:
                tgt, toff, ram = pools.allocate(
                    len(enc), fix, [f"cid{i}" for i in cids], new_zh,
                    name_band=True,
                    vacate=([{"pool": arena.rel, "offset": f"0x{off:X}",
                              "span": len(cur), "ram": ptr}]
                            if renaming_all_sharers and arena is not None
                            else None))
            except RuntimeError:
                dead = dead_name_band_spans(pools, chars, units_json,
                                            len(enc) + 1)
                assert dead, f"no dead name-band span for {new_zh}"
                span, doff, dtext = dead[0]
                tgt, toff, ram = pools.caves, doff, pools.caves.ram_of(doff)
                pools._alloc_marks.append({
                    "date": "2026-07-17", "fix": fix,
                    "ids": [f"cid{i}" for i in cids], "text": new_zh,
                    "pool": "data/zh/placements/resident_caves.json",
                    "offset": f"0x{toff:X}", "ram": f"0x{ram:X}",
                    "bytes": len(enc), "reserved": len(enc) + 1,
                    "note": f"proven-dead v1.1 residue span (was {dtext!r}, "
                            "0 arm9/json/ledger refs)"})
        else:
            tgt = pools.caves if "resident_caves" in prior["pool"] else pools.ui
            toff, ram = int(prior["offset"], 16), int(prior["ram"], 16)
        _surgical_write(tgt, toff, enc + b"\x00", len(enc) + 1)
        for cid in cids:
            by_cid[cid]["ptr"] = f"0x{ram:X}"
            by_cid[cid]["zh"] = new_zh
        print(f"cid{cids}: -> {new_zh} @0x{ram:X}"
              + ("" if renaming_all_sharers else f" (split; others keep {ptr})"))

    for arena in (pools.ui, pools.caves, pools.bnp, pools.pdl):
        arena.rebuild_json()
        dump_json(arena.rel, arena.data)
    dump_json("data/zh/placements/relocation_ledger.json", pools.commit_ledger())
    dump_json("data/zh/characters.json", chars)
    print("settle2 done.")


if __name__ == "__main__":
    main()
