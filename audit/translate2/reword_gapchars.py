#!/usr/bin/env python3
"""Demand-side glyph-gap resolution (G1: audit demand before minting).

The mint supply (58 cells + the 向@4257 promotion) cannot cover the full
107-char gap demand, so the 48 lowest-value x1 chars (generic vocabulary
with zero-loss synonyms) are reworded out of the fleet outputs; the 59
kept chars (all x2+ occurrences, idioms 姜辣/兔/羔/涯, famous-line words
拯/呐, proper nouns 橙/洪, canonical register 贩/炎/尸/稻/雏) get minted.

Every replacement is verified: chars ∈ charmap ∪ MINT_SET, bark budgets
respected (encode via the real stage encoder).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_apply import encode_stage  # noqa: E402

STG = HERE / "staging"

# chars that WILL be minted (x2+ demand, high-value x1) or promoted (向)
MINT_SET = set("窜嘞墙霉雕雁蚊擒鼬谬囊咱猖猫竿蜻蜓骋驰"
               "循绕辨渎亵俯卧肘磋惘膨胀拙戮侥羊腾沸灯挫拆丑荚蹦砾"
               "拯呐姜辣兔羔稻橙洪涯贩炎尸雏向")

# (entity, field_kind, key) -> replacement
#   bark key: (file, record_hex) ; cutin key: idn ; weapon key: slot
REWORDS = {
    ("char_106", "cutin", 319): "人类的可能性、岂能被区区自我满足所摧毁！",
    ("char_109", "bark", ("0.bin", "0xD5FC")): "……上当了！",
    ("char_123", "cutin", 369): "听吧……塞壬的歌声",
    ("char_123", "bark", ("0.bin", "0xFB20")): "我、我……要为艾因大人……啊啊！！",
    ("char_13", "bark", ("0.bin", "0x1660")): "纵使卡多倒下、吉翁荣光永不消逝！",
    ("char_13", "bark", ("1de.bin", "0x410")): "哼、那女人！",
    ("char_131", "cutin", 394): "您还是放弃为好吧？",
    ("char_134", "cutin", 407): "就在此再犯下一件大罪、展示暴走尽头的惨状吧！",
    ("char_139", "cutin", 417): "睁大眼睛、给我好好瞄准！！",
    ("char_141", "bark", ("0.bin", "0x127E0")): "哼！简直笑掉大牙！！",
    ("char_167", "bark", ("0.bin", "0x1601C")): "与我为敌、这就是你的墓场！！",
    ("char_167", "bark", ("0.bin", "0x163BC")): "你还不懂吗！？人类正在侵吞着地球！",
    ("char_173", "bark", ("0.bin", "0x16CF0")): "唔哦哦哦哦！",
    ("char_173", "bark", ("1.bin", "0x59B")): "纵使我灵魂转生百万次、也定要将你讨灭！！",
    ("char_216", "bark", ("0.bin", "0x1CF60")): "都说帅气女人出洋相才可爱！",
    ("char_216", "bark", ("0.bin", "0x1D038")): "别拖拖拉拉了、快点投降！",
    ("char_22", "bark", ("0.bin", "0x222C")): "嘿……！猎物送上门了！",
    ("char_22", "bark", ("0.bin", "0x2264")): "……真是的、都没空吃东西！",
    ("char_226", "cutin", 683): "我可是凭这身本事拼过战场的！绝不可能输！",
    ("char_226", "bark", ("0.bin", "0x1EB58")): "我可不是白白撑过一年战争的！",
    ("char_27", "bark", ("0.bin", "0x2CC8")): "这种攻击岂能动摇我们！",
    ("char_28", "bark", ("0.bin", "0x2E38")): "就算我不在了……也不必孤单……",
    ("char_311", "bark", ("0.bin", "0x234AC")): "通通砸个粉碎！",
    ("char_326", "bark", ("0.bin", "0x25E28")): "可、可恶啊！区区低微的市民兵！！",
    ("char_337", "bark", ("0.bin", "0x272B8")): "我的使命、便是回应这玫瑰的气息！",
    ("char_345", "bark", ("0.bin", "0x283A8")): "打法这么乱来、就别想赢！！",
    ("char_346", "bark", ("0.bin", "0x285B8")): "看来有只烦人的飞虫……在飞来飞去",
    ("char_346", "bark", ("0.bin", "0x28754")): "这点力量成不了事！",
    ("char_346", "bark", ("0.bin", "0x28874")): "人注定灭亡！被自己养出的黑暗吞没！",
    ("char_405", "bark", ("0.bin", "0x2B978")): "这力量！必须报告御大将！！",
    ("char_453", "bark", ("0.bin", "0x332B4")): "让开！虫子们！！",
    ("char_517", "bark", ("0.bin", "0x1BFDC")): "不愧是永恒号、毫不动摇！",
    ("char_517", "bark", ("1.bin", "0x850")): "咱们也别输给女孩子们！",
    ("char_64", "bark", ("0.bin", "0x75BC")): "胆敢抵抗、这烈火就将你们烧尽！",
    ("char_71", "bark", ("0.bin", "0x85BC")): "要恨就恨自己技不如人！",
    ("char_74", "bark", ("0.bin", "0x8AD0")): "我可不能白拿这份钱啊！",
    ("char_74", "bark", ("0.bin", "0x8B80")): "我也老了……",
    ("char_91", "bark", ("0.bin", "0xB1C8")): "我只想开创NT作为NT立世的道路！",
    ("char_99", "bark", ("0.bin", "0xBF48")): "……笨蛋吗？",
    ("unit_199", "defense_name", None): "ABC披风",
    ("unit_214", "weapon", 2): "射击长枪",
    ("unit_292", "weapon", 2): "闪光浮游炮",
}


def encodable_after_mint(text: str) -> list[str]:
    bad = []
    for ch in text:
        if ch in "{}" or ch in MINT_SET:
            continue
        try:
            encode_stage(ch)
        except ValueError:
            bad.append(ch)
    return bad


def main():
    errors, applied = [], 0
    by_ent: dict[str, list] = {}
    for (ent, kind, key), text in REWORDS.items():
        by_ent.setdefault(ent, []).append((kind, key, text))
    for ent, edits in sorted(by_ent.items()):
        side = "chars" if ent.startswith("char_") else "units"
        opath = STG / "out" / side / f"{ent}.json"
        out = json.loads(opath.read_text())
        brief = json.loads((STG / side / f"{ent}.json").read_text())
        budgets = {(b["file"], b["record"].lower()): b.get("budget")
                   for b in brief.get("barks", [])}
        for kind, key, text in edits:
            bad = encodable_after_mint(text)
            if bad:
                errors.append(f"{ent} {kind} {key}: unencodable {bad} in {text!r}")
                continue
            if kind == "bark":
                fname, rec = key
                hit = next((b for b in out.get("barks", [])
                            if b.get("file") == fname and
                            b.get("record", "").lower() == rec.lower()), None)
                if hit is None:
                    errors.append(f"{ent} bark {key}: record not in output")
                    continue
                budget = budgets.get((fname, rec.lower()))
                enc = encode_stage("".join(c for c in text if c not in MINT_SET))
                # budget check with minted chars counted as 2B each
                total = len(enc) + 2 * sum(1 for c in text if c in MINT_SET)
                if budget and total > budget:
                    errors.append(f"{ent} bark {key}: {total}B > budget {budget}B")
                    continue
                hit["zh"] = text
            elif kind == "cutin":
                hit = next((i for i in out.get("ids", []) if i.get("idn") == key), None)
                if hit is None or "cutin_zh" not in hit:
                    errors.append(f"{ent} cutin idn{key}: not in output")
                    continue
                hit["cutin_zh"] = text
            elif kind == "weapon":
                hit = next((w for w in out.get("weapons", [])
                            if w.get("slot") == key), None)
                if hit is None:
                    errors.append(f"{ent} weapon{key}: not in output")
                    continue
                hit["zh"] = text
            elif kind == "defense_name":
                out["defense_name_zh"] = text
            applied += 1
        opath.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"applied {applied} rewords; {len(errors)} errors")
    for e in errors:
        print("ERR:", e)


if __name__ == "__main__":
    main()
