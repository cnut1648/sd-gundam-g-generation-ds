#!/usr/bin/env python3
"""Export per-ENTITY translation briefs for the v1.2 quote/unit retranslation fleet.

One staging JSON per entity:
  * character entity = one PERSON (all char-DB cids sharing the same JP name
    pointer, restricted to cids that appear on the guide's Quotes tab): the
    person's ID commands (name/summary/detail/cut-in) per card + all barks.
  * unit entity = one unique unit identity (name ptr, weapon ptrs, ability
    family, defense record): name + weapons + special ability/defense records.

Shared objects (detail didx, cut-in groups, bark records, 1df/1e0 records,
defense-name strings) are assigned ONE owning entity (first by cid/utid); other
entities see them as read-only context (owner=false) so the fleet can never
produce two competing translations for one record.

Usage: python audit/translate2/export_entities.py
Writes: audit/translate2/staging/chars/char_<cid>.json
        audit/translate2/staging/units/unit_<utid>.json
        audit/translate2/staging/index.json  (fleet manifest)
"""
import sys
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from utils.extract import layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from utils.extract.gamerom import GameROM, u16, u32  # noqa: E402
from utils import text_codec  # noqa: E402
from build.build_guide import decode_text, _dummy_name, _clean_cutin, _STUB_BARK_RE  # noqa: E402

import os
jp = GameROM(REPO / "0098 - SD Gundam G Generation DS (Japan).nds")
zh = GameROM(os.environ.get("ZH_ROM", str(REPO / "sd-gundam-g-generation-zh.nds")))

OUT = Path(os.environ.get("STAGING_OUT", str(REPO / "audit/translate2/staging")))
(OUT / "chars").mkdir(parents=True, exist_ok=True)
(OUT / "units").mkdir(parents=True, exist_ok=True)

CM = text_codec.load_charmap()


def dt(rom, b, surf, exp):
    return decode_text(rom, b, surf, exp, False) if b else ""


def px_of(rom, b, surface):
    """Rendered pixel width the way the trampoline draws it: one-byte = 8px,
    2-byte ZH-band = 12px, 2-byte JP-band = 8px (renderB), macros -> expand."""
    if not b:
        return 0
    w = 0
    i = 0
    n = len(b)
    while i < n:
        c = b[i]
        if c >= 0xF0 and i + 1 < n:
            sub = rom.expand_sys(((c << 8) | b[i + 1]) - 0xF000)
            w += px_of(rom, sub, surface) if sub else 8
            i += 2
        elif c >= 0xE0 and i + 1 < n:
            slot = ((c << 8) | b[i + 1]) - 0xE000 + 224
            w += 12 if slot >= 2196 else 8
            i += 2
        else:
            if c not in (0x00, 0x01):
                w += 8
            i += 1
    return w


# ---------------------------------------------------------------------------
# shared caches
# ---------------------------------------------------------------------------
jdetails = {d["didx"]: d for d in W.id_details(jp)}
zdetails = {d["didx"]: d for d in W.id_details(zh)}
jcut = W.cutin_records(jp)[0]
zcut = W.cutin_records(zh)[0]
jcut_by_rec = {r["record"]: r for r in jcut["records"]}
zcut_by_rec = {r["record"]: r for r in zcut["records"]}
jspec, zspec = W.special_records(jp), W.special_records(zh)


def _detail_bytes(rom, dmap, didx):
    d = dmap.get(didx)
    return bytes(rom.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]]) if d else None


def _cutin_bytes(rom, cutmap, rec_no):
    r = cutmap.get(rec_no) if rec_no else None
    if not r:
        return None
    dc = rom.file(L.CUTIN_FILE)
    raw = dc[int(r["start"], 16):int(r["end"], 16)]
    if raw[:2] == b"\x00\x05":
        raw = raw[4:]
    elif raw[:2] == b"\x00\x04":
        raw = raw[2:]
    k = raw.find(b"\x00\x03\x00\x01")
    if k >= 0:
        raw = raw[:k]
    return _clean_cutin(raw) or None


jbarks = W.barks(jp)
zbarks_by_rec = {(b["file"], b["record"]): b for b in W.barks(zh)}
jbark_by_cid, jbark_by_vs = {}, {}
for b in jbarks:
    jbark_by_cid.setdefault(b["cid"], []).append(b)
    jbark_by_vs.setdefault(b["voiceset"], []).append(b)


def _bark_pair(rec):
    zrec = zbarks_by_rec.get((rec["file"], rec["record"]))
    jf, zf = jp.file(rec["file"]), zh.file(rec["file"])
    b0, e0 = int(rec["body"], 16), int(rec["end"], 16)
    jb = _clean_cutin(jf[b0:e0])
    zb = _clean_cutin(zf[int(zrec["body"], 16):int(zrec["end"], 16)]) if zrec else b""
    return jb, zb, e0 - b0


def _glyphs(b):
    return sum(1 for _o, t, _l in text_codec.iter_tokens(b) if t not in (0, 1)) if b else 0


# ---------------------------------------------------------------------------
# characters -> person entities
# ---------------------------------------------------------------------------
def char_entities():
    # cards exactly as the guide builds them
    speakerless = None  # (not needed for translation)
    person_of = {}      # name_ptr -> entity dict
    order = []
    detail_owner = {}
    cutin_owner = {}
    bark_owner = {}
    idname_seen = {}    # name ptr -> (entity, idn) first
    for cid in range(L.CHARDB_COUNT):
        rec = L.CHARDB + cid * L.CHARDB_STRIDE
        jptr = u32(jp.arm9, rec + L.PILOT_NAME_FIELD)
        jn = jp.cstr(jptr)
        zn = zh.cstr(u32(zh.arm9, rec + L.PILOT_NAME_FIELD))
        if _dummy_name(zn):
            continue
        ids = []
        for slot in range(3):
            idn = cid * 3 + slot
            if idn >= L.IDCMD_COUNT:
                continue
            ji, zi = W.id_command(jp, idn), W.id_command(zh, idn)
            zi_name = zh.cstr(int(zi["name"]["ptr"], 16)) if zi["name"] else None
            if _dummy_name(zi_name):
                continue
            ji_name = jp.cstr(int(ji["name"]["ptr"], 16)) if ji["name"] else None
            if ji_name and decode_text(jp, ji_name, "bank", jp.expand_sys) == "なし":
                continue
            ji_sum = jp.cstr(int(ji["summary"]["ptr"], 16)) if ji["summary"] else None
            zi_sum = zh.cstr(int(zi["summary"]["ptr"], 16)) if zi["summary"] else None
            rec_no = jcut["links"].get(str(idn))
            cj = _cutin_bytes(jp, jcut_by_rec, rec_no)
            cz = _cutin_bytes(zh, zcut_by_rec, zcut["links"].get(str(idn)))
            ids.append({
                "idn": idn, "slot": slot,
                "target": L.IDCMD_TARGET_NAMES.get(zi["target"], ""),
                "map_command": bool(zi["cond"] & 0x02),
                "jp_name_ptr": ji["name"]["ptr"] if ji["name"] else None,
                "zh_name_ptr": zi["name"]["ptr"] if zi["name"] else None,
                "name_jp": dt(jp, ji_name, "bank", jp.expand_sys),
                "name_zh_current": dt(zh, zi_name, "bank", zh.expand_sys),
                "name_zh_px": px_of(zh, zi_name, "bank"),
                "name_budget_px": 64,
                "summary_jp": dt(jp, ji_sum, "bank", jp.expand_sys),
                "summary_zh_KEEP": dt(zh, zi_sum, "bank", zh.expand_sys),
                "didx": ji["didx"],
                "detail_jp": dt(jp, _detail_bytes(jp, jdetails, ji["didx"]), "stage", jp.expand),
                "detail_zh_current": dt(zh, _detail_bytes(zh, zdetails, zi["didx"]), "stage", zh.expand),
                "cutin_group": rec_no,
                "cutin_jp": dt(jp, cj, "stage", jp.expand),
                "cutin_zh_current": dt(zh, cz, "stage", zh.expand),
            })
        recs = jbark_by_cid.get(cid) or jbark_by_vs.get(u16(jp.arm9, rec + L.CHARDB_VOICESET), [])
        barks = []
        for brec in recs:
            jb, zb, size = _bark_pair(brec)
            if _glyphs(jb) <= 1 and _glyphs(zb) <= 1:
                continue
            if _STUB_BARK_RE.search(decode_text(jp, jb, "stage", jp.expand)):
                continue
            barks.append({
                "file": brec["file"], "record": brec["record"],
                "budget_bytes": size,
                "jp": dt(jp, jb, "stage", jp.expand),
                "zh_current": dt(zh, zb, "stage", zh.expand),
                "zh_current_bytes": len(zb.rstrip(b"\x00")) if zb else 0,
            })
        if not ids and not barks:
            continue
        ent = person_of.get(jptr)
        if ent is None:
            ent = {
                "entity": f"char_{cid}",
                "kind": "character",
                "cids": [],
                "name_jp": dt(jp, jn, "bank", jp.expand_sys),
                "name_zh_KEEP": dt(zh, zn, "bank", zh.expand_sys),
                "cards": [],
                "barks": [],
                "_bark_keys": set(),
            }
            person_of[jptr] = ent
            order.append(ent)
        ent["cids"].append(cid)
        # ownership marks
        for i in ids:
            key = i["jp_name_ptr"]
            first = idname_seen.setdefault(key, (ent["entity"], i["idn"]))
            i["name_owner"] = first[0] == ent["entity"] and (first[1] == i["idn"] or True)
            i["name_shared_with_idn"] = first[1] if first[1] != i["idn"] else None
            dfirst = detail_owner.setdefault(i["didx"], (ent["entity"], i["idn"]))
            i["detail_owner"] = dfirst == (ent["entity"], i["idn"])
            i["detail_owned_by"] = None if i["detail_owner"] else dfirst[0]
            if i["cutin_group"] is not None:
                cfirst = cutin_owner.setdefault(i["cutin_group"], (ent["entity"], i["idn"]))
                i["cutin_owner"] = cfirst == (ent["entity"], i["idn"])
                i["cutin_owned_by"] = None if i["cutin_owner"] else cfirst[0]
            else:
                i["cutin_owner"] = False
                i["cutin_owned_by"] = None
        ent["cards"].append({"cid": cid, "ids": ids})
        for b in barks:
            key = (b["file"], b["record"])
            if key in ent["_bark_keys"]:
                continue
            owner = bark_owner.setdefault(key, ent["entity"])
            if owner == ent["entity"]:
                ent["_bark_keys"].add(key)
                ent["barks"].append(b)
            else:
                b2 = dict(b)
                b2["owned_by"] = owner
                ent.setdefault("barks_shared_readonly", []).append(b2)
    for ent in order:
        ent.pop("_bark_keys", None)
    return order


# ---------------------------------------------------------------------------
# units -> unique-identity entities
# ---------------------------------------------------------------------------
def _seg_records(rom, fname, recmap, index):
    """(start,end,segments) for one 1df/1e0 record index."""
    for r in recmap:
        if r["index"] == index:
            f = rom.file(fname)
            o0, o1 = int(r["start"], 16), int(r["end"], 16)
            segs = []
            for part in f[o0:o1].split(b"\x00\x03"):
                part = part.strip(b"\x00")
                if part:
                    segs.append(part)
            return o0, o1, segs
    return None, None, []


def unit_entities():
    ju = {u["utid"]: u for u in W.units(jp)}
    zu = {u["utid"]: u for u in W.units(zh)}
    ident = {}
    order = []
    ability_owner = {}
    defense_owner = {}
    defname_owner = {}
    weapon_shared = {}
    for u in W.units(zh):
        for w in u.get("weapons", []):
            weapon_shared[w["ptr"]] = weapon_shared.get(w["ptr"], 0) + 1
    for utid in sorted(zu):
        z, j = zu[utid], ju.get(utid, {})
        zn = zh.cstr(int(z["name"]["ptr"], 16)) if z.get("name") else None
        if _dummy_name(zn):
            continue
        jlink = W.unit_special_link(jp, utid)
        zlink = W.unit_special_link(zh, utid)
        key = (j.get("name", {}).get("ptr"),
               tuple(w["ptr"] for w in j.get("weapons", [])),
               zlink.get("ability_family", -1), zlink.get("defense_record", 0),
               jlink.get("defense_name", {}).get("ptr"))
        ent = ident.get(key)
        if ent is not None:
            ent["utids"].append(utid)
            continue
        jn = jp.cstr(int(j["name"]["ptr"], 16)) if j.get("name") else None
        jw = {w["slot"]: jp.cstr(int(w["ptr"], 16)) for w in j.get("weapons", [])}
        weapons = []
        for w in z.get("weapons", []):
            wb = zh.cstr(int(w["ptr"], 16))
            weapons.append({
                "slot": w["slot"], "ptr": w["ptr"],
                "jp": dt(jp, jw.get(w["slot"]), "bank", jp.expand_sys),
                "zh_current": dt(zh, wb, "bank", zh.expand_sys),
                "shared_by_n_units": weapon_shared.get(w["ptr"], 1),
            })
        specials = {}
        fam = zlink.get("ability_family", -1)
        if fam >= 0:
            o0, o1, jsegs = _seg_records(jp, L.SPECIAL_ABILITY_FILE, jspec["ability"], fam)
            zo0, zo1, zsegs = _seg_records(zh, L.SPECIAL_ABILITY_FILE, zspec["ability"], fam)
            if zsegs:
                owner = ability_owner.setdefault(fam, f"unit_{utid}")
                specials["ability"] = {
                    "family": fam, "record_offset": hex(zo0) if zo0 is not None else None,
                    "budget_bytes": (o1 - o0) if o0 is not None else None,
                    "jp_segments": [dt(jp, s, "bank", jp.expand_sys) for s in jsegs],
                    "zh_segments_current": [dt(zh, s, "bank", zh.expand_sys) for s in zsegs],
                    "owner": owner == f"unit_{utid}",
                    "owned_by": None if owner == f"unit_{utid}" else owner,
                }
        if "defense_name" in zlink:
            tj = jp.cstr(int(jlink["defense_name"]["ptr"], 16)) if "defense_name" in jlink else None
            tz = zh.cstr(int(zlink["defense_name"]["ptr"], 16))
            if tz:
                ptr = jlink.get("defense_name", {}).get("ptr")
                owner = defname_owner.setdefault(ptr, f"unit_{utid}")
                specials["defense_name"] = {
                    "ptr": ptr,
                    "jp": dt(jp, tj, "bank", jp.expand_sys),
                    "zh_current": dt(zh, tz, "bank", zh.expand_sys),
                    "owner": owner == f"unit_{utid}",
                    "owned_by": None if owner == f"unit_{utid}" else owner,
                }
        rz = zlink.get("defense_record", 0)
        rjj = jlink.get("defense_record", 0)
        o0, o1, dsegs_j = _seg_records(jp, L.SPECIAL_DEFENSE_FILE, jspec["defense"], rjj)
        zo0, zo1, dsegs_z = _seg_records(zh, L.SPECIAL_DEFENSE_FILE, zspec["defense"], rz)
        if dsegs_z:
            owner = defense_owner.setdefault(rz, f"unit_{utid}")
            specials["defense"] = {
                "record": rz, "record_offset": hex(zo0) if zo0 is not None else None,
                "budget_bytes": (o1 - o0) if o0 is not None else None,
                "jp_segments": [dt(jp, s, "bank", jp.expand_sys) for s in dsegs_j],
                "zh_segments_current": [dt(zh, s, "bank", zh.expand_sys) for s in dsegs_z],
                "owner": owner == f"unit_{utid}",
                "owned_by": None if owner == f"unit_{utid}" else owner,
            }
        ent = {
            "entity": f"unit_{utid}",
            "kind": "unit",
            "utids": [utid],
            "name_jp": dt(jp, jn, "bank", jp.expand_sys),
            "name_zh_current": dt(zh, zn, "bank", zh.expand_sys),
            "name_zh_px": px_of(zh, zn, "bank"),
            "name_budget_px": 144,
            "weapons": weapons,
            "specials": specials,
        }
        ident[key] = ent
        order.append(ent)
    return order


def main():
    chars = char_entities()
    units = unit_entities()
    index = {"chars": [], "units": []}
    for ent in chars:
        p = OUT / "chars" / f"{ent['entity']}.json"
        p.write_text(json.dumps(ent, ensure_ascii=False, indent=1), encoding="utf-8")
        index["chars"].append({
            "entity": ent["entity"], "cids": ent["cids"], "name_jp": ent["name_jp"],
            "name_zh": ent["name_zh_KEEP"],
            "n_ids": sum(len(c["ids"]) for c in ent["cards"]),
            "n_barks": len(ent["barks"])})
    for ent in units:
        p = OUT / "units" / f"{ent['entity']}.json"
        p.write_text(json.dumps(ent, ensure_ascii=False, indent=1), encoding="utf-8")
        index["units"].append({
            "entity": ent["entity"], "utids": ent["utids"], "name_jp": ent["name_jp"],
            "name_zh": ent["name_zh_current"],
            "n_weapons": len(ent["weapons"]), "n_specials": len(ent["specials"])})
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    print(f"chars: {len(chars)} entities, units: {len(units)} entities")


if __name__ == "__main__":
    main()
