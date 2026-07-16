#!/usr/bin/env python3
"""Export clean JP + current-ZH per translatable item (from the ROM decode) into
staging JSON, grouped by category, for the translation swarm.  Each item carries
a stable key so refined ZH can be mapped back to its source data record."""
import sys, json
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from build.build_guide import (GameROM, decode_text, extract_units, unit_specials,
    id_command, cutin_line, build_bark_index, CHARDB, CHARDB_STRIDE, PILOT_NAME_FIELD,
    CHARDB_VOICESET, CHARDB_COUNT, _dummy_name, TARGET_NAMES, u32)

jp = GameROM(REPO / "0098 - SD Gundam G Generation DS (Japan).nds")
zh = GameROM(REPO / "sd-gundam-g-generation-zh.nds")
def dt(rom, b, surf, exp): return decode_text(rom, b, surf, exp, False) if b else ""

# ---- units (t2): name + weapons + specials ----
zu = {u["utid"]: u for u in extract_units(zh)}
ju = {u["utid"]: u for u in extract_units(jp)}
units = []
for utid in sorted(zu):
    z = zu[utid]; j = ju.get(utid, {})
    if _dummy_name(z["name"]): continue
    jw = {s: b for s, _p, b in j.get("weapons", [])}
    weapons = [{"slot": s, "jp": dt(jp, jw.get(s), "bank", jp.expand_sys),
                "zh": dt(zh, wb, "bank", zh.expand_sys)} for s, _p, wb in z["weapons"]]
    specials = [{"kind": sp["kind"], "jp": dt(jp, sp.get("_jb", b""), "bank", jp.expand_sys),
                 "zh": dt(zh, sp.get("_zb", b""), "bank", zh.expand_sys)}
                for sp in unit_specials(jp, zh, utid)]
    units.append({"utid": utid, "name_jp": dt(jp, j.get("name"), "bank", jp.expand_sys),
                  "name_zh": dt(zh, z["name"], "bank", zh.expand_sys),
                  "weapons": weapons, "specials": specials})

# ---- characters (t1): name + 3 ID cmds + cutins + barks ----
jbc, jbv = build_bark_index(jp); zbc, zbv = build_bark_index(zh)
chars = []
for cid in range(CHARDB_COUNT):
    rec = CHARDB + cid * CHARDB_STRIDE
    zn = zh.cstr(u32(zh.arm9, rec + PILOT_NAME_FIELD))
    if _dummy_name(zn): continue
    jn = jp.cstr(u32(jp.arm9, rec + PILOT_NAME_FIELD))
    ids = []
    for slot in range(3):
        idn = cid*3+slot; zi = id_command(zh, idn); ji = id_command(jp, idn)
        if _dummy_name(zi["name"]): continue
        ids.append({"slot": slot, "target": TARGET_NAMES.get(zi["target"], ""),
                    "map": bool(zi["cond"] & 2),
                    "name_jp": dt(jp, ji["name"], "bank", jp.expand_sys), "name_zh": dt(zh, zi["name"], "bank", zh.expand_sys),
                    "summary_jp": dt(jp, ji["summary"], "bank", jp.expand_sys), "summary_zh": dt(zh, zi["summary"], "bank", zh.expand_sys),
                    "detail_jp": dt(jp, ji["detail"], "stage", jp.expand), "detail_zh": dt(zh, zi["detail"], "stage", zh.expand),
                    "cutin_jp": dt(jp, cutin_line(jp, idn), "stage", jp.expand), "cutin_zh": dt(zh, cutin_line(zh, idn), "stage", zh.expand)})
    zbl = zbc.get(cid) or zbv.get(u32(jp.arm9, rec+CHARDB_VOICESET), []) or []
    jbl = jbc.get(cid) or jbv.get(u32(jp.arm9, rec+CHARDB_VOICESET), []) or []
    barks = [{"i": k, "jp": dt(jp, jbl[k] if k < len(jbl) else b"", "stage", jp.expand),
              "zh": dt(zh, b, "stage", zh.expand)} for k, b in enumerate(zbl)]
    if not ids and not barks: continue
    chars.append({"cid": cid, "name_jp": dt(jp, jn, "bank", jp.expand_sys),
                  "name_zh": dt(zh, zn, "bank", zh.expand_sys), "ids": ids, "barks": barks})

out = REPO / "audit/translate"
(out/"units.json").write_text(json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8")
(out/"characters.json").write_text(json.dumps(chars, ensure_ascii=False, indent=1), encoding="utf-8")
print("units:", len(units), " characters:", len(chars))
# stats: units with under-translated specials (zh much shorter than jp)
short = sum(1 for u in units for s in u["specials"] if len(s["zh"]) < len(s["jp"])*0.5 and len(s["jp"])>8)
print("under-translated specials (zh < 50% jp len):", short)
