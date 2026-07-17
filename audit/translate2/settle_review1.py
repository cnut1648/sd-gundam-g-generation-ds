#!/usr/bin/env python3
"""Owner review settlement #1 (2026-07-17).

 1. Weapon adjudications (owner ruling):
      ミョルニル       -> 雷神锤        (revert to v1.1)
      エクスカリバー   -> 王者之剑      (revert)
      デファイアント   -> 挑战者        (revert)
      ローエングリン   -> 阳电子破城炮  (revert)
      ゴットフリート   -> 光束加农炮    (revert)
      デリュージー     -> 洪流炮        (owner wording)
 2. ドゥガチ = 德卡契 (owner ruling; 杜加奇 retired):
      staged outputs (barks/cutins/ID text): 杜加奇 -> 德卡契
      pilot names: cid317 杜加奇克隆 -> 德卡契克隆 (same-size in-place)
                   cid316 格拉克斯·德卡 -> 格拉克斯·德卡契 (grows; ledger)
    (stage/event/briefing occurrences are phase-3 scope.)

Staged-output edits feed the normal validate->apply pipeline; the pilot
names are applied here directly (characters.json ptr + placement bytes via
the Pools/ledger machinery, pure ZH-band per A12).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_apply import open_roms, encode_bank, Pools  # noqa: E402
from apply_fleet import _surgical_write, load_json, dump_json  # noqa: E402
from build.build_guide import decode_text  # noqa: E402

STG = HERE / "staging"
jp, zh = open_roms()

WEAPON_RULINGS = {   # (entity, slot) -> zh
    ("unit_547", 1): "雷神锤",
    ("unit_553", 0): "王者之剑",
    ("unit_556", 3): "挑战者",
    ("unit_556", 1): "洪流炮",
    ("unit_632", 0): "阳电子破城炮",
    ("unit_632", 1): "光束加农炮",
}

GAME_TEXT_KEYS = {"name_zh", "zh", "cutin_zh", "detail_effect_zh",
                  "defense_name_zh"}
GAME_TEXT_LIST_KEYS = {"ability_segments_zh", "defense_segments_zh"}


def sweep_text(obj):
    """杜加奇->德卡契 in game-text fields only (notes stay historical)."""
    changed = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in GAME_TEXT_KEYS and isinstance(v, str) and "杜加奇" in v:
                obj[k] = v.replace("杜加奇", "德卡契")
                changed += 1
            elif k in GAME_TEXT_LIST_KEYS and isinstance(v, list):
                for i, s in enumerate(v):
                    if isinstance(s, str) and "杜加奇" in s:
                        v[i] = s.replace("杜加奇", "德卡契")
                        changed += 1
            else:
                changed += sweep_text(v)
    elif isinstance(obj, list):
        for v in obj:
            changed += sweep_text(v)
    return changed


def main():
    # -- 1+2a. staged outputs ------------------------------------------------
    n_w = 0
    for (ent, slot), zh_text in WEAPON_RULINGS.items():
        p = STG / "out/units" / f"{ent}.json"
        o = json.loads(p.read_text())
        hit = next(w for w in o["weapons"] if w.get("slot") == slot)
        if hit["zh"] != zh_text:
            hit["zh"] = zh_text
            n_w += 1
        p.write_text(json.dumps(o, ensure_ascii=False, indent=1) + "\n")
    n_s = 0
    for p in sorted((STG / "out").rglob("*.json")):
        o = json.loads(p.read_text())
        c = sweep_text(o)
        if c:
            p.write_text(json.dumps(o, ensure_ascii=False, indent=1) + "\n")
            n_s += c
    print(f"staged: {n_w} weapon rulings, {n_s} 杜加奇->德卡契 field edits")

    # -- 2b. pilot names -----------------------------------------------------
    pools = Pools(zh, jp)
    chars = load_json("data/zh/characters.json")
    by_cid = {c["cid"]: c for c in chars["characters"]}

    def rename(cid, new_zh, target_off=None):
        """target_off: explicit cave span (proven-dead residue, e.g. the stale
        v1.1 '格拉克斯·德卡ァ' @0x3FA9: zero arm9 word refs, 17B to the next
        entry) — recorded through the ledger like any allocation (G5)."""
        c = by_cid[cid]
        old_ptr = int(c["ptr"], 16)
        cur = zh.cstr(old_ptr) or b""
        enc = encode_bank(new_zh)          # pure ZH-band (A12: no jp payload)
        arena, off = pools.arena_of_ram(old_ptr)
        if arena is not None and len(enc) <= len(cur):
            _surgical_write(arena, off, enc + b"\x00", len(cur))
            c["zh"] = new_zh
            print(f"cid{cid}: in-place {new_zh} @0x{old_ptr:X}")
            return
        fix = f"settle1:pilot-name cid{cid}"
        vacate = None
        if arena is not None:
            vacate = [{"pool": arena.rel, "offset": f"0x{off:X}",
                       "span": len(cur), "ram": c["ptr"]}]
        if target_off is not None and not any(
                a.get("fix") == fix for a in pools.ledger["allocations"]):
            tgt, toff = pools.caves, target_off
            ram = tgt.ram_of(toff)
            mark = {"date": "2026-07-17", "fix": fix, "ids": [f"cid{cid}"],
                    "text": new_zh, "pool": "data/zh/placements/resident_caves.json",
                    "offset": f"0x{toff:X}", "ram": f"0x{ram:X}",
                    "bytes": len(enc), "reserved": len(enc) + 1,
                    "note": "proven-dead v1.1 residue span (0 arm9 refs)"}
            if vacate:
                mark["vacated"] = vacate
            pools._alloc_marks.append(mark)
        else:
            tgt, toff, ram = pools.allocate(
                len(enc), fix, [f"cid{cid}"], new_zh,
                name_band=True, vacate=vacate)
        _surgical_write(tgt, toff, enc + b"\x00", len(enc) + 1)
        c["ptr"] = f"0x{ram:X}"
        c["zh"] = new_zh
        print(f"cid{cid}: relocated {new_zh} -> 0x{ram:X}")

    rename(317, "德卡契克隆")
    rename(316, "格拉克斯德卡契", target_off=0x3FA9)  # 7 cells: plate bound 56px (interpunct dropped)

    # -- 1b. weapon reverts in data/zh (the validate->apply pipeline is a
    # baseline-diff: proposal == v1.1 baseline is a no-op there, so reverting
    # an APPLIED fleet change must edit the applied state directly) ---------
    units_json = load_json("data/zh/units.json")
    by_utid = {u["utid"]: u for u in units_json["units"]}
    ENT = {"unit_547": 547, "unit_553": 553, "unit_556": 556, "unit_632": 632}

    def revert_weapon(ent, slot, new_zh):
        u = by_utid[ENT[ent]]
        w = next(x for x in u["weapons"] if x["slot"] == slot)
        old_ptr = int(w["ptr"], 16)
        arena, off = pools.arena_of_ram(old_ptr)
        cur_now = _entry_span(arena, off) if arena else None
        enc = encode_bank(new_zh)
        # in-place when the new text fits the CURRENT arena payload at ptr
        if arena is not None and cur_now is not None and len(enc) <= cur_now:
            _surgical_write(arena, off, enc + b"\x00", cur_now)
            new_ptr = old_ptr
        else:
            tgt, toff, ram = pools.allocate(
                len(enc), f"settle1:weapon {ent} slot{slot}", [ent], new_zh,
                vacate=None)
            _surgical_write(tgt, toff, enc + b"\x00", len(enc) + 1)
            new_ptr = ram
        # re-aim EVERY weapon entry sharing the old ptr (632/654 share strings)
        n = 0
        for uu in units_json["units"]:
            for ww in uu.get("weapons", []):
                if int(ww["ptr"], 16) == old_ptr:
                    ww["ptr"] = f"0x{new_ptr:X}"
                    ww["zh"] = new_zh
                    n += 1
        print(f"{ent} slot{slot}: -> {new_zh} @0x{new_ptr:X} ({n} shared entries)")

    def _entry_span(arena, off):
        e = arena.entry_at(off)
        if e is None:
            # find covering entry
            for s in sorted(arena.entries):
                p = bytes.fromhex(arena.entries[s]["payload_hex"])
                if s <= off < s + len(p):
                    return len(p) - (off - s)
            return None
        p = bytes.fromhex(e["payload_hex"])
        return len(p)

    for (ent, slot), zh_text in WEAPON_RULINGS.items():
        revert_weapon(ent, slot, zh_text)

    for arena in (pools.ui, pools.caves, pools.bnp, pools.pdl):
        arena.rebuild_json()
        dump_json(arena.rel, arena.data)
    dump_json("data/zh/placements/relocation_ledger.json", pools.commit_ledger())
    dump_json("data/zh/characters.json", chars)
    dump_json("data/zh/units.json", units_json)
    print("pilot names + weapon reverts applied.")


if __name__ == "__main__":
    main()
