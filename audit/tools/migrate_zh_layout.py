#!/usr/bin/env python3
"""ONE-TIME migration: re-key data/ into the extractor-first zh mapping layout.

    data/names/* + data/ui/* + data/arenas/* + data/dialogue/* + data/files/*
        -> data/zh/** (translation mapping grouped like the data/jp dump,
           keys = extractor addresses/ids, ALL jp annotation fields dropped)

The migration only moves and re-keys committed values — no byte is re-encoded;
the rebuilt ROM must stay sha1-identical.  Applied once; kept as evidence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

DATA = REPO / "data"
ZH = DATA / "zh"


def load(rel: str) -> dict:
    return json.loads((DATA / rel).read_text())


def dump(rel: str, payload: dict):
    p = ZH / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")
    print(f"  wrote data/zh/{rel}")


def strip_keys(obj, drop: tuple[str, ...]):
    if isinstance(obj, dict):
        return {k: strip_keys(v, drop) for k, v in obj.items() if k not in drop}
    if isinstance(obj, list):
        return [strip_keys(v, drop) for v in obj]
    return obj


def main() -> int:
    ZH.mkdir(exist_ok=True)

    # ---- units.json: names/units + names/weapons ---------------------------
    u = load("names/units.json")
    w = load("names/weapons.json")
    wp: dict[int, list] = {}
    for e in w["entries"]:
        ent = {"slot": e["slot"], "zh": e["zh"]}
        if "ptr" in e:
            ent["ptr"] = e["ptr"]
        wp.setdefault(e["utid"], []).append(ent)
    units = []
    for e in u["entries"]:
        ent = {"utid": e["utid"], "zh": e["zh"]}
        if "ptr" in e:
            ent["ptr"] = e["ptr"]
        if "carrier_capacity" in e:
            ent["carrier_capacity"] = e["carrier_capacity"]
        if e["utid"] in wp:
            ent["weapons"] = sorted(wp.pop(e["utid"]), key=lambda x: x["slot"])
        units.append(ent)
    for utid, weapons in sorted(wp.items()):    # weapons of un-renamed units
        units.append({"utid": utid, "weapons": sorted(weapons, key=lambda x: x["slot"])})
    units.sort(key=lambda x: x["utid"])
    dump("units.json", {
        "_about": ("Unit translation mapping, keyed by utid (master table "
                   "0xB94BC — geometry lives in utils/extract/layout.py). 'zh' is "
                   "the translation; 'ptr' re-aims the name pointer word at the "
                   "relocated string (absent = in-place pool rewrite); weapon "
                   "slots follow the +0x2C sub-records. carrier_capacity is a "
                   "deliberate gameplay override (Eternal = 6). String bytes "
                   "live in data/zh/placements/."),
        "units": units})

    # ---- characters.json: names/pilots + names/id_commands -----------------
    p = load("names/pilots.json")
    idc = load("names/id_commands.json")
    ids_by_cid: dict[int, list] = {}
    for e in idc["entries"]:
        idn = e["id"]
        ent: dict = {"idn": idn}
        for part in ("name", "summary"):
            src = e.get(part) or {}
            if src:
                dst = {"zh": src["zh"]}
                if "ptr" in src:
                    dst["ptr"] = src["ptr"]
                ent[part] = dst
        ids_by_cid.setdefault(idn // 3, []).append(ent)
    chars = []
    cids = {e["char_id"] for e in p["entries"]} | set(ids_by_cid)
    by_cid = {e["char_id"]: e for e in p["entries"]}
    for cid in sorted(cids):
        ent = {"cid": cid}
        src = by_cid.get(cid)
        if src:
            ent["zh"] = src["zh"]
            if "ptr" in src:
                ent["ptr"] = src["ptr"]
        if cid in ids_by_cid:
            ent["ids"] = sorted(ids_by_cid[cid], key=lambda x: x["idn"])
        chars.append(ent)
    detail_offsets = [{"didx": e["index"], "offset": e["offset"]}
                      for e in idc.get("details", []) if "offset" in e]
    dump("characters.json", {
        "_about": ("Character translation mapping, keyed by cid (char-DB "
                   "0xDCF18) and idn = cid*3+slot (ID-command table 0xEC994); "
                   "geometry in utils/extract/layout.py. detail_offsets re-aim "
                   "slots of the 256-entry effect-detail offset table (didx); "
                   "detail text bytes live in data/zh/placements/"
                   "idcmd_detail_pool.json. Bark/bio text is mapped per bank "
                   "under data/zh/files/ (keys pair with data/jp/characters"
                   ".json)."),
        "characters": chars,
        "detail_offsets": detail_offsets})

    # ---- ui.json: labels + abilities + dictionary + resource_offsets --------
    lab = load("ui/labels.json")
    abil = load("names/abilities.json")
    dic = load("ui/dictionary.json")
    res = load("ui/resource_offsets.json")
    dump("ui.json", {
        "_about": ("UI translation mapping. labels/abilities: every literal "
                   "pointer word in `sites` is re-aimed from old_ptr (asserted "
                   "against the JP image) to ptr; keys pair with data/jp/ui.json "
                   "pointer_strings. dictionary: the 0x12D770 text-macro store — "
                   "offset_entries re-aim table slots, string_edits rewrite "
                   "entry strings in place (payload_hex canonical). "
                   "resource_offsets: offset words tied to rebuilt NitroFS "
                   "banks."),
        "labels": strip_keys(lab["entries"], ("jp",)),
        "abilities": strip_keys(abil["entries"], ("jp",)),
        "dictionary": {
            "offset_entries": strip_keys(dic["offset_entries"], ("jp",)),
            "string_edits": strip_keys(dic["string_edits"], ("jp",)),
        },
        "resource_offsets": res["entries"]})

    # ---- event_text.json ----------------------------------------------------
    ev = load("arenas/event_text_blocks.json")
    dump("event_text.json", {
        "_about": ("Story/briefing text embedded in the code image: byte-length-"
                   "locked replacements of the JP blocks (keys pair with "
                   "data/jp/event_text.json). payload_hex is canonical; briefing "
                   "records carry inline pointers into the briefing bank "
                   "(data/zh/placements/briefing_blobs.json)."),
        "region": ev.get("region"),
        "entries": strip_keys(ev["entries"], ("jp",))})

    # ---- stages/ -------------------------------------------------------------
    for src in sorted((DATA / "dialogue" / "stages").glob("*.json")):
        d = json.loads(src.read_text())
        d["edits"] = strip_keys(d["edits"], ("jp_text", "jp_hex", "speaker"))
        dump(f"stages/{src.name}", d)

    # ---- files/ ---------------------------------------------------------------
    for src in sorted((DATA / "files").rglob("*.json")):
        rel = src.relative_to(DATA / "files")
        d = json.loads(src.read_text())
        d = strip_keys(d, ("jp",))
        if str(rel) == "hangar/part_names.json":
            # the arm9 offset words mirroring the rebuilt b6e layout live WITH
            # the bank they index (were data/names/parts.json)
            pn = load("names/parts.json")
            d["name_offset_words"] = [{"index": e["index"], "offset": e["offset"]}
                                      for e in pn["entries"]]
        dump(f"files/{rel}", d)

    # ---- placements/ -----------------------------------------------------------
    for name in ("battle_name_pool", "idcmd_detail_pool", "post_dict_labels",
                 "resident_caves", "ui_names_bank", "briefing_blobs",
                 "relocation_ledger"):
        d = load(f"arenas/{name}.json")
        dump(f"placements/{name}.json", d)

    print("\nmigrated. Old files are NOT deleted by this script — delete after "
          "the loaders are re-pointed and the byte-identical build is proven.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
