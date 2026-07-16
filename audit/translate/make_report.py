#!/usr/bin/env python3
"""Build a human-verifiable report of what the translation swarm CHANGED.

Compares, per field: JP original | OLD ZH (current shipped ROM) | NEW ZH (swarm).
Only fields that actually changed are listed.  Output: audit/translate/CHANGES.md
"""
import sys, json, glob
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent.parent
ST = REPO / "audit/translate"

units_in = {u["utid"]: u for u in json.load(open(ST / "units.json"))}
chars_in = {c["cid"]: c for c in json.load(open(ST / "characters.json"))}

units_new, chars_new = {}, {}
for p in glob.glob(str(ST / "units_zh_*.json")) + ([str(ST / "units_zh.json")] if (ST / "units_zh.json").exists() else []):
    for k, v in json.load(open(p)).items():
        units_new[int(k)] = v
for p in glob.glob(str(ST / "characters_zh_*.json")):
    for k, v in json.load(open(p)).items():
        chars_new[int(k)] = v

out = ["# 翻译改动对照报告 (swarm wave 1)",
       "",
       "每条格式：**日文原文** ｜ 旧译(当前ROM) ｜ **新译(本次)**。仅列出有改动的字段。",
       ""]
stats = {"unit_name": 0, "weapon": 0, "special": 0,
         "char_name": 0, "id_name": 0, "id_summary": 0, "id_detail": 0, "cutin": 0, "bark": 0}

def row(jp, old, new):
    return "- `%s` ｜ %s ｜ **%s**" % (jp or "∅", old or "∅", new or "∅")

# ---------------- UNITS ----------------
out.append("## 机体 Units\n")
for utid in sorted(units_new):
    u_in = units_in.get(utid)
    u_new = units_new[utid]
    if not u_in:
        continue
    lines = []
    if u_new.get("name") and u_new["name"] != u_in.get("name_zh"):
        lines.append("  - **机体名**: " + row(u_in.get("name_jp"), u_in.get("name_zh"), u_new["name"]))
        stats["unit_name"] += 1
    # weapons: input list [{slot,jp,zh}], output {slot: zh}
    nw = u_new.get("weapons") or {}
    for w in u_in.get("weapons", []):
        newz = nw.get(str(w["slot"]))
        if newz and newz != w.get("zh"):
            lines.append("  - 武器%d: " % w["slot"] + row(w.get("jp"), w.get("zh"), newz))
            stats["weapon"] += 1
    # specials: 1:1 by index
    ns = u_new.get("specials") or []
    for i, sp in enumerate(u_in.get("specials", [])):
        if i < len(ns) and ns[i].get("zh") and ns[i]["zh"] != sp.get("zh"):
            lines.append("  - 特殊[%s]: " % sp.get("kind", "?") + row(sp.get("jp"), sp.get("zh"), ns[i]["zh"]))
            stats["special"] += 1
    if lines:
        out.append("### #%d %s" % (utid, u_new.get("name") or u_in.get("name_zh") or ""))
        out += lines
        out.append("")

# ---------------- CHARACTERS ----------------
out.append("## 角色 Characters\n")
for cid in sorted(chars_new):
    c_in = chars_in.get(cid)
    c_new = chars_new[cid]
    if not c_in:
        continue
    lines = []
    if c_new.get("name") and c_new["name"] != c_in.get("name_zh"):
        lines.append("  - **角色名**: " + row(c_in.get("name_jp"), c_in.get("name_zh"), c_new["name"]))
        stats["char_name"] += 1
    nids = c_new.get("ids") or []
    for i, idc in enumerate(c_in.get("ids", [])):
        if i >= len(nids):
            continue
        ni = nids[i]
        for jf, of, nf, label, sk in [("name_jp", "name_zh", "name", "指令名", "id_name"),
                                       ("summary_jp", "summary_zh", "summary", "效果名", "id_summary"),
                                       ("detail_jp", "detail_zh", "detail", "效果", "id_detail"),
                                       ("cutin_jp", "cutin_zh", "cutin", "名台词", "cutin")]:
            if ni.get(nf) and ni[nf] != idc.get(of):
                lines.append("  - ID%d·%s: " % (idc.get("slot", i), label) + row(idc.get(jf), idc.get(of), ni[nf]))
                stats[sk] += 1
    nbk = c_new.get("barks") or []
    for i, bk in enumerate(c_in.get("barks", [])):
        if i < len(nbk) and nbk[i] and nbk[i] != bk.get("zh"):
            lines.append("  - 喊话%d: " % i + row(bk.get("jp"), bk.get("zh"), nbk[i]))
            stats["bark"] += 1
    if lines:
        out.append("### #%d %s" % (cid, c_new.get("name") or c_in.get("name_zh") or ""))
        out += lines
        out.append("")

hdr = ["## 改动统计", "",
       "| 类别 | 改动数 |", "|---|---|",
       "| 机体名 | %d |" % stats["unit_name"],
       "| 武器名 | %d |" % stats["weapon"],
       "| 机体特殊能力/防御 | %d |" % stats["special"],
       "| 角色名 | %d |" % stats["char_name"],
       "| ID指令名 | %d |" % stats["id_name"],
       "| ID效果名 | %d |" % stats["id_summary"],
       "| ID效果详情 | %d |" % stats["id_detail"],
       "| 名台词 | %d |" % stats["cutin"],
       "| 战斗喊话 | %d |" % stats["bark"],
       "| **合计** | **%d** |" % sum(stats.values()), ""]
report = "\n".join(out[:4] + hdr + out[4:])
Path(ST / "CHANGES.md").write_text(report, encoding="utf-8")
print("wrote audit/translate/CHANGES.md")
print("units changed:", len([1 for u in units_new if units_in.get(u)]),
      " chars changed:", len([1 for c in chars_new if chars_in.get(c)]))
print("total field changes:", sum(stats.values()))
for k, v in stats.items():
    print("  %-12s %d" % (k, v))
