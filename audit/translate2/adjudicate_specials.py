#!/usr/bin/env python3
"""Adjudicate 1df/1e0 special-record segment wordings across the whole fleet.

The 1df/1e0 records are hard byte-capped in place (offtab spans; the composer
mirrors the game's 00 03 framing), and the fleet produced both overshoots and
inconsistent wordings for the same JP tag (大型ユニット -> 大型/大型单位/大型机/
大型机体/巨大).  This sweep normalizes every owned ability/defense record:

  * per JP segment text, an ordered candidate chain (fullest form first);
  * per record, the combination with the lowest total chain rank that
    physically fits (composed via the REAL composer, which reuses the JP
    record's macros/one-byte forms) wins; ties prefer a fuller left segment;
  * segments without a chain keep the fleet proposal (else current ZH).

Writes updated staging/out/units/<ent>.json only when segments change and
prints a JP -> old -> new report.  Report-driven; exits 0.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_apply import open_roms  # noqa: E402
from apply_fleet import compose_record, _record_span  # noqa: E402
from utils.extract import walkers as W  # noqa: E402

STG = HERE / "staging"
jp, zh = open_roms()
jspec = W.special_records(jp)
JAB = {r["index"]: r for r in jspec["ability"]}
JDF = {r["index"]: r for r in jspec["defense"]}

# Ordered candidate chains: fullest/preferred form first.  Every form keeps
# the tag's full meaning (trigger + effect + numbers); compaction only drops
# redundant morphemes, never content.
# NT is spelled {F0:94} (system-dict macro, renders renderB "NT") — the
# SHIPPED convention for these records (e.g. ability34 raw f05e01… =
# {F0:94}{01}宇宙适应) and 2B instead of 4B, unlocking fuller wordings.
CHAINS: dict[str, list[str]] = {
    "ニュータイプ対応機": ["{F0:94}对应机", "{F0:94}机"],
    "大型ユニット": ["大型机"],   # uniform across all 16 records (fits r6 exactly)
    "可変機構": ["可变形"],
    "地形適正・宇宙": ["宇宙适应"],
    "地形適正・地上": ["地面适应"],
    "地形適正・水": ["水域适应"],
    "試作機": ["试作机"],
    # fused double tag; {01} line break inside the segment is the shipped
    # convention for it (current ZH: {F0:94}{01}宇宙适应)
    "ニュータイプ対応機地形適正・宇宙": ["{F0:94}对应机{01}宇宙适应",
                                         "{F0:94}机{01}宇宙适应"],
    "バイオセンサー搭載（ニュータイプ対応機）": [
        "生体感应器（{F0:94}对应机）", "生体感应器（{F0:94}机）",
        "生体感应器（{F0:94}）"],
    "フラッシュシステム（ニュータイプ対応機）": [
        "闪光系统（{F0:94}对应机）", "闪光系统（{F0:94}机）", "闪光系统（{F0:94}）"],
    "バイオコンピュータ搭載（ニュータイプ対応機）": [
        "生体电脑（{F0:94}对应机）", "生体电脑（{F0:94}机）", "生体电脑（{F0:94}）"],
    "核バズーカ装備（使用不可）": ["装备核火箭炮（禁用）", "核火箭炮（禁用）"],
    "I・フィールドキャンセラー": ["力场抵消"],
    "ハリー・オード専用機": ["哈利专用机", "哈利机"],
    "クワトロ・バジーナ専用機": ["克瓦特罗专用机", "克瓦特罗机"],
    "シャア・アズナブル専用機": ["夏亚专用机", "夏亚机"],
    "フルアーマーシステム（解除可能）": ["全装甲系统（可解除）", "全装甲（可解除）"],
    "フルアーマーシステム装着可能": ["可装备全装甲"],
    "バックウェポンシステム装着可能": ["可装背部武器系统", "可装备背部武器"],
    "I・フィールドキャンセラー自動修復：300": ["力场抵消自愈300"],
    "月光蝶システム：周囲1マスにダメージ": ["月光蝶系统伤周围1格", "月光蝶伤周围1格"],
    "全ての攻撃を25%の確率で、0に回避する": ["有25%概率完全回避所有攻击",
                                             "25%概率完全回避所有攻击"],
    "ニュータイプ／強化人間のみ作動可能": ["仅{F0:94}及强化人可用"],
    "それを超える場合ダメージを20%軽減する": ["超过则伤害减轻20%", "超过则伤害减20%"],
    "それを超える場合ダメージを50%軽減する": ["超过则伤害减轻50%", "超过则伤害减50%"],
    "威力900までの実弾属性の攻撃を無効化する": [
        "威力900以下的实弹属性攻击无效", "{F0:876}900以下的实弹属性攻击无效"],
    "（ヴァリアブルフェイズシフト装甲）": ["（可变相位转移装甲）", "（可变相位装甲）"],
    "ディフェンサーユニット装着可能": ["可装备G防卫机"],
    "ディフェンサーユニット": ["G防卫机"],
    "ALICE：HP70%以下でパイロットの能力増強": ["ALICE：HP70%以下增强机师能力"],
    # 能力が低いほど効果が高い gradient: fullest fitting form wins per record
    "非NTで能力が低いほど効果が高い": ["非{F0:94}能力越低越有效",
                                       "非{F0:94}越弱越有效", "非{F0:94}效果更大"],
    "デウス・エクス・マキナ：広域破壊攻撃": ["机械降神：广域破坏攻击",
                                             "机械降神：广域破坏"],
}


def best_fit(jp_segs, prop_segs, cur_segs, raw):
    """Lowest-total-rank chain combination that composes into the record."""
    chains = []
    for i, js in enumerate(jp_segs):
        c = CHAINS.get(js)
        if c is None:
            p = prop_segs[i] if prop_segs and i < len(prop_segs) and prop_segs[i] else None
            c = [p or (cur_segs[i] if i < len(cur_segs) else "")]
        chains.append(c)
    combos = sorted(itertools.product(*(range(len(c)) for c in chains)),
                    key=lambda t: (sum(t), t))
    for ranks in combos:
        segs = [chains[i][r] for i, r in enumerate(ranks)]
        try:
            compose_record(segs, raw)
            return segs
        except ValueError:
            continue
    return None


def main():
    changed, failed = [], []
    for bf in sorted((STG / "units").glob("unit_*.json")):
        ent = bf.stem
        of = STG / "out" / "units" / f"{ent}.json"
        if not of.exists():
            continue
        brief = json.loads(bf.read_text())
        out = json.loads(of.read_text())
        sp = brief.get("specials", {})
        touched = False
        for kind, jmap, fname, okey, ikey in (
                ("ability", JAB, "1df.bin", "ability_segments_zh", "family"),
                ("defense", JDF, "1e0.bin", "defense_segments_zh", "record")):
            info = sp.get(kind)
            if not info or not info.get("owner"):
                continue
            idx = info.get(ikey)
            jr = jmap.get(idx)
            if jr is None or not any(s in CHAINS for s in info["jp_segments"]):
                continue
            s, e = int(jr["start"], 16), int(jr["end"], 16)
            raw = jp.file(fname)[s:e]
            cur = info.get("zh_segments_current") or []
            prop = out.get(okey)
            pick = best_fit(info["jp_segments"], prop, cur, raw)
            if pick is None:
                failed.append(f"{ent} {kind}{idx}: no combination fits "
                              f"{info['jp_segments']}")
                continue
            if pick != (prop or None):
                changed.append((ent, kind, idx, info["jp_segments"], prop, pick))
                out[okey] = pick
                touched = True
        if touched:
            of.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    for ent, kind, idx, jsegs, old, new in changed:
        print(f"{ent} {kind}{idx}")
        print(f"   JP : {jsegs}")
        print(f"   old: {old}")
        print(f"   new: {new}")
    print(f"\n{len(changed)} records adjudicated, {len(failed)} failures")
    for f in failed:
        print("FAIL:", f)


if __name__ == "__main__":
    main()
