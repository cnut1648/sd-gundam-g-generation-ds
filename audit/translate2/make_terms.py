#!/usr/bin/env python3
"""Terminology digest for phases 2-3: JP -> final ZH from the APPLIED state.

The character & unit translations (commit e531313) are the binding
convention.  This tool joins data/jp (ground truth) with data/zh (applied
mapping) and emits:

  staging/terms.json   machine digest {units, weapons, characters, tags}
  staging/terms.md     human/subagent-readable tables (injected into briefs)

Weapons dedupe by JP text; conflicting ZH for the same JP string are all
listed (callers must keep per-unit context).  Tag table = the adjudicated
1df/1e0 wordings.
"""
from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

STG = HERE / "staging"


def main():
    ju = json.loads((REPO / "data/jp/units.json").read_text())["units"]
    zu = {u["utid"]: u for u in
          json.loads((REPO / "data/zh/units.json").read_text())["units"]}
    jc = json.loads((REPO / "data/jp/characters.json").read_text())["characters"]
    zc = {c["cid"]: c for c in
          json.loads((REPO / "data/zh/characters.json").read_text())["characters"]}

    units, weapons = OrderedDict(), OrderedDict()
    for u in ju:
        z = zu.get(u["utid"])
        if not z:
            continue
        jp_name = u["name"]["text"]
        zh_name = z.get("zh")
        if jp_name and zh_name and jp_name not in units:
            units[jp_name] = zh_name
        zw = {w["slot"]: w.get("zh") for w in z.get("weapons", [])}
        for w in u.get("weapons", []):
            jt, zt = w.get("text"), zw.get(w.get("slot"))
            if not jt or not zt:
                continue
            weapons.setdefault(jt, [])
            if zt not in weapons[jt]:
                weapons[jt].append(zt)

    chars = OrderedDict()
    for c in jc:
        z = zc.get(c["cid"])
        if not z:
            continue
        jn, zn = c.get("name", {}).get("text"), z.get("zh")
        if jn in (None, "", "欠番") or not zn:   # 欠番 = vacant slot, junk decode
            continue
        if jn not in chars:
            chars[jn] = zn

    # adjudicated special-tag conventions (single source: adjudicate_specials)
    from adjudicate_specials import CHAINS
    tags = {jp: forms[0] for jp, forms in CHAINS.items()}

    out = {"units": units, "weapons": weapons, "characters": chars,
           "special_tags": tags}
    (STG / "terms.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# v1.2 术语表（阶段1定稿 = 后续阶段必须遵循的唯一约定）",
          "", "凡出现下列日文名词，必须使用对应中文译名，禁止另译。", ""]
    md.append("## 角色名 (%d)" % len(chars))
    md.append("| 日文 | 中文 |")
    md.append("|---|---|")
    for j, z in chars.items():
        md.append(f"| {j} | {z} |")
    md.append("")
    md.append("## 机体/舰船名 (%d)" % len(units))
    md.append("| 日文 | 中文 |")
    md.append("|---|---|")
    for j, z in units.items():
        md.append(f"| {j} | {z} |")
    md.append("")
    md.append("## 武器名 (%d)" % len(weapons))
    md.append("| 日文 | 中文 |")
    md.append("|---|---|")
    for j, zs in weapons.items():
        md.append(f"| {j} | {' / '.join(zs)} |")
    md.append("")
    md.append("## 特殊能力/防御标签 (%d)" % len(tags))
    md.append("| 日文 | 中文 |")
    md.append("|---|---|")
    for j, z in tags.items():
        md.append(f"| {j} | {z} |")
    (STG / "terms.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"units={len(units)} weapons={len(weapons)} chars={len(chars)} "
          f"tags={len(tags)} -> staging/terms.json + terms.md")


if __name__ == "__main__":
    main()
