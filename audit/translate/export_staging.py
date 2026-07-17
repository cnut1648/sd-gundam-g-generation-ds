#!/usr/bin/env python3
"""Export clean JP + current-ZH per translatable item (from the ROM decode) into
staging JSON, grouped by category, for the translation swarm.  Each item carries
a stable key so refined ZH can be mapped back to its source data record.

Built on the canonical extraction package (utils/extract/) — the same single
code path behind data/jp/ and the review guide — plus the guide's presentation
decode (▼ page breaks, □ unidentified glyphs)."""
import sys
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from utils.extract import layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from utils.extract.gamerom import GameROM, u16, u32  # noqa: E402
from build.build_guide import decode_text, _dummy_name, _clean_cutin  # noqa: E402

jp = GameROM(REPO / "0098 - SD Gundam G Generation DS (Japan).nds")
zh = GameROM(REPO / "sd-gundam-g-generation-zh.nds")


def dt(rom, b, surf, exp):
    return decode_text(rom, b, surf, exp, False) if b else ""


def _name_bytes(rom, rec):
    return rom.cstr(int(rec["name"]["ptr"], 16)) if rec.get("name") else None


def _seg_bytes(rom, fname, recmap, index, limit):
    for r in recmap:
        if r["index"] == index:
            data_f = rom.file(fname)
            o0, o1 = int(r["start"], 16), int(r["end"], 16)
            segs = []
            for part in data_f[o0:o1].split(b"\x00\x03"):
                part = part.strip(b"\x00")
                if part:
                    segs.append(part)
            return segs[:limit]
    return []


def unit_specials(utid):
    """(kind, jp_bytes, zh_bytes) rows for one unit — ability family segments
    + defense type name + defense description segments."""
    out = []
    jlink = W.unit_special_link(jp, utid)
    zlink = W.unit_special_link(zh, utid)
    fam = zlink.get("ability_family", -1)
    if fam >= 0:
        js = _seg_bytes(jp, L.SPECIAL_ABILITY_FILE, jspec["ability"], fam, 2)
        zs = _seg_bytes(zh, L.SPECIAL_ABILITY_FILE, zspec["ability"], fam, 2)
        for k in range(max(len(js), len(zs))):
            zb = zs[k] if k < len(zs) else None
            if zb:
                out.append(("ability", js[k] if k < len(js) else b"", zb))
    if "defense_name" in zlink:
        tj = jp.cstr(int(jlink["defense_name"]["ptr"], 16)) if "defense_name" in jlink else None
        tz = zh.cstr(int(zlink["defense_name"]["ptr"], 16))
        if tz:
            out.append(("defense", tj or b"", tz))
    rj, rz = jlink.get("defense_record", 0), zlink.get("defense_record", 0)
    dj = _seg_bytes(jp, L.SPECIAL_DEFENSE_FILE, jspec["defense"], rj, 3)
    dz = _seg_bytes(zh, L.SPECIAL_DEFENSE_FILE, zspec["defense"], rz, 3)
    for k in range(max(len(dj), len(dz))):
        zb = dz[k] if k < len(dz) else None
        if zb:
            out.append(("defense", dj[k] if k < len(dj) else b"", zb))
    return out


# ---- units (t2): name + weapons + specials ----
zu = {u["utid"]: u for u in W.units(zh)}
ju = {u["utid"]: u for u in W.units(jp)}
jspec, zspec = W.special_records(jp), W.special_records(zh)
units = []
for utid in sorted(zu):
    z = zu[utid]
    j = ju.get(utid, {})
    zn = _name_bytes(zh, z)
    if _dummy_name(zn):
        continue
    jw = {w["slot"]: jp.cstr(int(w["ptr"], 16)) for w in j.get("weapons", [])}
    weapons = [{"slot": w["slot"], "jp": dt(jp, jw.get(w["slot"]), "bank", jp.expand_sys),
                "zh": dt(zh, zh.cstr(int(w["ptr"], 16)), "bank", zh.expand_sys)}
               for w in z.get("weapons", [])]
    specials = [{"kind": kind, "jp": dt(jp, jb, "bank", jp.expand_sys),
                 "zh": dt(zh, zb, "bank", zh.expand_sys)}
                for kind, jb, zb in unit_specials(utid)]
    units.append({"utid": utid, "name_jp": dt(jp, _name_bytes(jp, j), "bank", jp.expand_sys),
                  "name_zh": dt(zh, zn, "bank", zh.expand_sys),
                  "weapons": weapons, "specials": specials})

# ---- characters (t1): name + 3 ID cmds + cutins + barks ----
jdetails = {d["didx"]: d for d in W.id_details(jp)}
zdetails = {d["didx"]: d for d in W.id_details(zh)}
jcut = W.cutin_records(jp)[0]
zcut = W.cutin_records(zh)[0]
jcut_by_rec = {r["record"]: r for r in jcut["records"]}
zcut_by_rec = {r["record"]: r for r in zcut["records"]}


def _detail_bytes(rom, dmap, didx):
    d = dmap.get(didx)
    return rom.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]] if d else None


def _cutin_bytes(rom, cutmap, links, idn):
    rec = links.get(str(idn))
    r = cutmap.get(rec) if rec else None
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
    jb = _clean_cutin(jf[int(rec["body"], 16):int(rec["end"], 16)])
    zb = _clean_cutin(zf[int(zrec["body"], 16):int(zrec["end"], 16)]) if zrec else b""
    return jb, zb


chars = []
for cid in range(L.CHARDB_COUNT):
    rec = L.CHARDB + cid * L.CHARDB_STRIDE
    zn = zh.cstr(u32(zh.arm9, rec + L.PILOT_NAME_FIELD))
    if _dummy_name(zn):
        continue
    jn = jp.cstr(u32(jp.arm9, rec + L.PILOT_NAME_FIELD))
    ids = []
    for slot in range(3):
        idn = cid * 3 + slot
        if idn >= L.IDCMD_COUNT:
            continue
        zi, ji = W.id_command(zh, idn), W.id_command(jp, idn)
        zi_name = zh.cstr(int(zi["name"]["ptr"], 16)) if zi["name"] else None
        if _dummy_name(zi_name):
            continue
        ji_name = jp.cstr(int(ji["name"]["ptr"], 16)) if ji["name"] else None
        ids.append({
            "slot": slot, "target": L.IDCMD_TARGET_NAMES.get(zi["target"], ""),
            "map": bool(zi["cond"] & 2),
            "name_jp": dt(jp, ji_name, "bank", jp.expand_sys),
            "name_zh": dt(zh, zi_name, "bank", zh.expand_sys),
            "summary_jp": dt(jp, jp.cstr(int(ji["summary"]["ptr"], 16)) if ji["summary"] else None, "bank", jp.expand_sys),
            "summary_zh": dt(zh, zh.cstr(int(zi["summary"]["ptr"], 16)) if zi["summary"] else None, "bank", zh.expand_sys),
            "detail_jp": dt(jp, _detail_bytes(jp, jdetails, ji["didx"]), "stage", jp.expand),
            "detail_zh": dt(zh, _detail_bytes(zh, zdetails, zi["didx"]), "stage", zh.expand),
            "cutin_jp": dt(jp, _cutin_bytes(jp, jcut_by_rec, jcut["links"], idn), "stage", jp.expand),
            "cutin_zh": dt(zh, _cutin_bytes(zh, zcut_by_rec, zcut["links"], idn), "stage", zh.expand)})
    recs = jbark_by_cid.get(cid) or jbark_by_vs.get(u16(jp.arm9, rec + L.CHARDB_VOICESET), [])
    barks = []
    for k, brec in enumerate(recs):
        jb, zb = _bark_pair(brec)
        if not zb and not jb:
            continue
        barks.append({"i": k, "jp": dt(jp, jb, "stage", jp.expand),
                      "zh": dt(zh, zb, "stage", zh.expand)})
    if not ids and not barks:
        continue
    chars.append({"cid": cid, "name_jp": dt(jp, jn, "bank", jp.expand_sys),
                  "name_zh": dt(zh, zn, "bank", zh.expand_sys), "ids": ids, "barks": barks})

out = REPO / "audit/translate"
(out / "units.json").write_text(json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8")
(out / "characters.json").write_text(json.dumps(chars, ensure_ascii=False, indent=1), encoding="utf-8")
print("units:", len(units), " characters:", len(chars))
# stats: units with under-translated specials (zh much shorter than jp)
short = sum(1 for u in units for s in u["specials"]
            if len(s["zh"]) < len(s["jp"]) * 0.5 and len(s["jp"]) > 8)
print("under-translated specials (zh < 50% jp len):", short)
