# v1.2 retranslation fleet — shared brief (READ FULLY BEFORE TRANSLATING)

You are one translator agent in a fleet. You are assigned EXACTLY ONE entity
(one character-person or one unit) of the DS game **SDガンダム ジージェネレーション DS**
(SD高达G世纪DS). You translate Japanese → **natural mainland Simplified Chinese**.

Your entity file: `audit/translate2/staging/{chars|units}/<entity>.json`
Your output file: `audit/translate2/staging/out/{chars|units}/<entity>.json`
Write NOTHING else. Do not edit any repo file. Do not run builds. Output STRICT
JSON (UTF-8, no trailing commas, double quotes).

## 1. Voice & sources (mandatory)

* Fluent, natural 简体中文 as the mainland Gundam community writes it — never
  machine-literal, never telegraphic. Read the WHOLE entity first; keep one
  consistent voice for the person/unit across all its fields.
* **Use web_search extensively** for proper nouns, famous lines and lore:
  prefer 高达wiki (wiki.biligame.com), 萌娘百科 (zh.moegirl.org.cn), 百度百科
  (baike.baidu.com), 灰机wiki, bilibili usage. For a famous line (名台词/cut-in),
  the mainland-community canonical rendering WINS over your own wording — e.g.
  「νガンダムは伊達じゃない!」→「ν高达不是摆设！」-class renderings: search it,
  use the popular one, cite the source URL in `web_sources`.
* Established terms: 高达, 吉翁, 联邦, 提坦斯, 奥古, 新吉翁, 扎夫特, 新人类(NT),
  强化人, 月光蝶, 精神感应框架, 米诺夫斯基. Military ranks stay Japanese style
  (大佐/中佐/少佐/大尉…). Latin model codes stay Latin (ALICE, GP01, ∀, Ex-S,
  F91, VSBR, NT, SEED, PLANT, MEPE, ZERO).
* Canonical person names (do NOT re-litigate): 阿姆罗·雷, 夏亚·阿兹纳布尔,
  布莱特·诺亚, 卡缪·维丹, 捷多·亚西塔, 基拉·大和, 阿斯兰·萨拉, 哈曼·卡恩,
  阿纳贝尔·卡多, 强尼莱汀(无点), 希罗·尤尔, 迪安娜·索雷尔, 杜加奇, 玛莉梅亚,
  格雷米·普露兹, 多鲁基斯, 亚洲尊者(不是"大师亚洲"), 加洛德, 扎比涅, 夏克蒂.
  Japanese-kanji names use surname+given with NO dot (浦木宏, 小林隼人).
* G高达招式用社区通行译名: 石破天惊拳, 爆热神指(ゴッドフィンガー/God), 黑暗手指,
  十二王方牌大车轮. 武器译名跟 biligame 机体页: 光束军刀(不是光束剑), 光束步枪,
  光束薙刀, 榴弹发射器, 超级火箭炮, 龙骑兵系统, 线控炮(インコム), 多佛炮, 加里波第β.

## 2. Hard charset rules (violations cannot be built)

* NEVER use `，` — every pause is `、`. NEVER `～`. Ellipsis is `……` (even pairs).
  Full-width `！？`. `·` (U+00B7) between name parts. No spaces. No arrows ↑↓,
  no ✕/×, no ㍉ etc. Quotes 「」/“” must NOT be used.
* Dialogue-style fields (cut-in 名台词, battle barks, ID效果 detail): the full set
  `、 。 ！ ？ …… ・ （ ）` plus CJK, digits, Latin is fine.
* Name/label fields (ID指令名, unit name, weapon name, special-ability/defense
  text, defense name): avoid `、 。 ？ ・ ： ；`. Allowed there: CJK, `！ … · ％→%
  ＋→+ （ ）`, digits, Latin capitals. If you truly need a forbidden mark, put the
  wish in `notes` and give a clean alternative as the main value.

## 3. Field rules & budgets

### Characters (Quotes tab: 指令 name / 效果 detail / 名台词 cut-in / barks)

* `name_zh` (指令, the ID-command name shown in a tiny 64px box): **max 5
  glyph-cells** (each CJK/！/…/digit = 1 cell; using all 5 cells is fine).
  It may be a condensed phrase but MUST be natural Chinese sharing the cut-in
  quote's key words — e.g. これが私の戦争です！！→ quote 这就是我的战争！！ →
  name 我的战争！！. Stub/broken names (光说！, 珍贵, 灭) are DEFECTS — fix them.
  Punctuation ！/… only if it fits the 5 cells. NEVER pad; NEVER drop the core
  meaning word.
* `cutin_zh` (名台词, the big battle banner): the FULL famous line, community
  wording, web-verified. No length limit that matters (up to ~36 chars over 2
  lines; 3 lines exist). Do NOT truncate; do NOT append ellipsis that JP lacks.
  If JP is empty or 无 → output "".
* `detail_effect_zh` (效果): translate ONLY the effect clause (the text after
  （効果） in `detail_jp`). The 使用条件/对象 prefix is composed mechanically —
  do not include it. Keep EVERY number/percent/duration EXACTLY as JP
  (20%上昇 → +20% or 提升20%). Style: concise but complete sentence(s), e.g.
  攻撃対象を撃破しないように攻撃する → 攻击时手下留情、不击破目标.
  Current ZH like 放水 (dropping the whole sentence) is a DEFECT: retranslate fully.
* `barks[].zh`: short combat voice lines. HARD byte budget per record
  (`budget_bytes`): cost = 2 bytes per CJK char; 1 byte per 、。！？…・（） and
  digit 1/2/3; 2 bytes per other digit/Latin. If your natural line exceeds the
  budget, provide the best fitting phrasing (never a chopped sentence) — and you
  may put a longer ideal in `notes`. These are often famous barks — web-check the
  iconic ones (e.g. 弾幕薄いよ！何やってんの! ）.
* `summary` (效果名) is FIXED — do not output it.
* Character name is FIXED — if you believe it's wrong, say so in `name_flag`
  with evidence URL; do not output a replacement in any other field.
* Fields marked `"*_owner": false` / `"owned_by": ...` are ANOTHER agent's to
  translate — do NOT output those (they're your read-only context).
  `name_shared_with_idn` means the same string is one record shared across your
  own cards: give it the SAME translation.

### Units (Units tab: name / weapons / specials)

* `name_zh`: keep the current translation unless it is factually wrong per
  mainland wiki canon (biligame unit page is the reference). Budget 144px
  (12 CJK cells) — existing names fit; keep yours within the current shape.
  Variant suffixes like （月光蝶）/（∀99） keep their parentheses style.
* `weapons[].zh`: output ONLY the ones you propose to CHANGE (omit = keep).
  Weapon strings are SHARED across many units (`shared_by_n_units`) — change
  only for clear canon errors, citing the biligame page.
* `ability_segments_zh` (特殊能力, only if `"owner": true`): list of segments
  mirroring `jp_segments` 1:1. Segment 0 = system/ability NAME (keep Latin
  codes), following segments = effect description. Translate COMPLETELY — e.g.
  JP `ALICE：HP70%以下でパイロットの能力増強` must keep the trigger and effect:
  `ALICE：HP70%以下时增强驾驶员能力` (current `ALICE系统` alone is the defect
  class you're fixing). HARD budget: `budget_bytes` for the whole record; cost
  ≈ 2 bytes/char (CJK, Latin, digits, %／：all ≈2) + 2 bytes between segments.
  If it can't fit fully, compress wording but keep trigger+effect+numbers.
* `defense_name_zh` (only if owner): the barrier/system name (Iフィールド class);
  keep current unless canon-wrong.
* `defense_segments_zh` (only if owner): mirror `jp_segments` 1:1; the leading
  威力NNN number of a segment is drawn BY CODE — if `jp` segment starts with a
  number keep the same layout. Same byte budget rule.
* Weaponless "units" are pilot/faction identity labels — usually names only;
  keep unless canon-wrong.

## 4. Output schema (STRICT)

Character entity → `audit/translate2/staging/out/chars/<entity>.json`:
```json
{
 "entity": "char_1",
 "web_sources": ["https://..."],
 "name_flag": "",
 "ids": [
   {"idn": 3, "name_zh": "我的战争！！", "cutin_zh": "请看好了！这就是我的战争！！",
    "detail_effect_zh": "攻击时手下留情、不击破目标", "notes": ""}
 ],
 "barks": [
   {"file": "0.bin", "record": "0x10C", "zh": "不还手、就会被打倒……"}
 ],
 "notes": ""
}
```
Include EVERY `idn` you own a field of (owner flags); include name_zh always
(shared-name idns: repeat the same value). Include EVERY owned bark, keyed by
(file, record), in the input order. `cutin_zh`/`detail_effect_zh` only when you
are the owner (`cutin_owner` / `detail_owner` true); omit the key otherwise.

Unit entity → `audit/translate2/staging/out/units/<entity>.json`:
```json
{
 "entity": "unit_343",
 "web_sources": ["https://..."],
 "name_zh": "S高达",
 "weapons": [{"slot": 2, "zh": "光束智能枪"}],
 "ability_segments_zh": ["ALICE：HP70%以下时增强驾驶员能力", "非NT且能力越低效果越好"],
 "defense_name_zh": "",
 "defense_segments_zh": [],
 "notes": ""
}
```
Omit `ability_segments_zh`/`defense_*` keys entirely when you are not the owner
or the unit has none. Omit `defense_name_zh` when unchanged is fine (empty
string also = keep).

## 5. Final self-check before writing

1. JSON parses; entity id matches; owner-only fields respected.
2. No `，` `～` anywhere; ellipsis as ……; pause as 、.
3. ID names ≤5 cells and natural; cut-ins are FULL famous lines (web-checked).
4. Numbers identical to JP everywhere.
5. Bark/ability/defense budgets respected by the cost rule.
6. `web_sources` lists the pages you actually used (2+ for famous quotes).
