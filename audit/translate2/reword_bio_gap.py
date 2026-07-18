#!/usr/bin/env python3
"""One-time reword pass for the phase-2 bio reflow rerun (REWORD_BRIEF rules).

The mint (mint_bio_gap.py) covered 12 of the 42 gap chars from the 109
phase1-edit records' staged texts; the remaining 30 are low-frequency
generic vocabulary reworded here with zero-loss synonyms (minimal span,
meaning/register/locked-terms preserved; every replacement recorded in the
file's notes as `reword: X→Y (reason)`).

Each replacement is validated: old span occurs exactly once in the file's
paragraphs, the new text fully encodes on the stage surface, and check_p23
still passes (run separately)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
from utils import text_codec  # noqa: E402

STG = HERE / "staging" / "out" / "lib"

# file -> [(old span, new span, gap char, reason)]
REWORDS = {
    "char_19.json": [("生死未卜", "生死成谜", "卜", "同义")],
    "char_24.json": [("毫不气馁", "毫不退缩", "馁", "同义")],
    "char_27.json": [("3年的蛰伏", "3年的潜伏", "蛰", "同义")],
    "char_35.json": [("他生性爱挖苦人、性格别扭", "他生性毒舌、性格别扭", "挖", "同义(爱挖苦人=毒舌)")],
    "char_36.json": [("去梵蒂冈采访", "去梵蒂冈取材", "访", "同义(记者取材)")],
    "char_39.json": [("实为双胞胎姐弟", "实为双生姐弟", "胎", "同义")],
    "char_192.json": [("「肌肤还是这么有弹性、", "「气色还是这么好、", "肤", "同义改述(年轻状态的调侃、语义不变)"),
                      ("暗中滋长着染指地球圈的野心", "暗中膨胀着染指地球圈的野心", "滋", "同义")],
    "char_199.json": [("只要炽烈便不会熄灭", "只要持续燃烧便不会熄灭", "炽", "贴合JP燃え続ければ")],
    "char_206.json": [("因缘匪浅", "因缘不浅", "匪", "同义")],
    "char_208.json": [("渐生情愫", "渐生情意", "愫", "同义")],
    "char_213.json": [("被迫在某座宅邸中出卖身体", "被迫在某处出卖身体", "宅邸", "同义(处所泛称)"),
                      ("立下辉煌战果", "立下显赫战果", "煌", "同义")],
    "char_217.json": [("为遏制战争而不懈奋斗", "为阻止战争而不懈奋斗", "遏", "同义")],
    "char_221.json": [("主动请缨执行", "主动请命执行", "缨", "同义")],
    "char_325.json": [("开始在台前崭露头角", "开始在台前初露锋芒", "崭", "同义")],
    "char_332.json": [("惨遭拷问", "饱受酷刑", "拷", "同义(1880已被著占用、拷不可铸)"),
                      ("蕾柯亚奉篡夺了提坦斯大权的西罗克之命", "蕾柯亚奉谋夺了提坦斯大权的西罗克之命", "篡", "同义(谋夺保留夺权贬义)")],
    "char_333.json": [("那凝聚了众人灵魂之力的", "那汇聚了众人灵魂之力的", "凝", "同义(phase-1同句亦用汇聚)")],
    "char_357.json": [("一度欣喜若狂", "一度狂喜不已", "欣", "同义")],
    "char_4.json": [("久而久之更萌生出爱情", "久而久之更生出爱慕之情", "萌", "同义"),
                    ("间接酿成了瑟蕾因之死", "间接造成了瑟蕾因之死", "酿", "同义")],
    "char_9.json": [("不得不兵戎相见", "不得不兵刃相向", "戎", "同义成语")],
    "char_37.json": [("凯喃喃自语。「阿姆罗", "凯低声自语。「阿姆罗", "喃", "同义"),
                     ("是源自小说版『机动战士高达』的角色", "是源自小说『机动战士高达』的角色", "版", "省略无损(小说即小说版)"),
                     ("与动画版相比", "与动画中的形象相比", "版", "同义改述")],
    "char_516.json": [("口中仍呢喃着诅咒", "口中仍低声念着诅咒", "喃", "同义")],
    "unit_13.json": [("规格已臻极限", "规格已达极限", "臻", "同义")],
    "unit_34.json": [("MS这一概念的范畴", "MS这一概念的范围", "畴", "同义")],
    "unit_70.json": [("由于忌惮新人类能力", "由于畏惧新人类能力", "惮", "同义")],
    "unit_91.json": [("包括希罗在内也寥寥无几", "包括希罗在内也屈指可数", "寥", "同义成语")],
    "unit_139.json": [("凝聚联邦军的技术精华", "汇聚联邦军的技术精华", "凝", "同义")],
}


def main():
    dry = "--write" not in sys.argv
    nrep = 0
    for fn, edits in sorted(REWORDS.items()):
        p = STG / fn
        d = json.loads(p.read_text())
        paras = d["paragraphs"]
        notes = []
        for old, new, ch, why in edits:
            hits = [i for i, t in enumerate(paras) if old in t]
            occurrences = sum(t.count(old) for t in paras)
            assert occurrences == 1 and len(hits) == 1, \
                f"{fn}: {old!r} occurs {occurrences}x (must be exactly 1)"
            for c in new:
                text_codec.encode(c, allow_low15=True, surface="stage")
            paras[hits[0]] = paras[hits[0]].replace(old, new)
            notes.append(f"reword: {old}→{new} ({ch}无字格、{why})")
            nrep += 1
            print(f"{fn}: {old} → {new}")
        if not dry:
            d["paragraphs"] = paras
            d["notes"] = (d.get("notes", "").rstrip()
                          + ("；" if d.get("notes") else "") + "；".join(notes))
            p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    print(f"\n{nrep} replacements in {len(REWORDS)} files"
          + (" (DRY RUN — pass --write)" if dry else " — written"))


if __name__ == "__main__":
    main()
