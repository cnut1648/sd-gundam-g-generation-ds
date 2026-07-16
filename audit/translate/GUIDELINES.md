# Translation guidelines (SD Gundam G Generation DS — JP → Simplified Chinese)

You refine the fan translation. Source of truth = the JP field; improve/complete the
current ZH (often incomplete — e.g. a unit special `ALICE：HP70%以下でパイロットの能力増強`
is only `ALICE系统`, dropping the whole effect: translate it FULLY).

## Voice & correctness
- Natural, fluent **Simplified Chinese** as the mainland Gundam community writes it. Never
  literal/machine phrasing. Read a whole item's fields together for consistent voice.
- **Use web_search** for proper nouns and lore. Prefer: bilibili 高达wiki
  (wiki.biligame.com), Baidu Baike (baike.baidu.com), 灰机wiki, 中文维基. Search unit names,
  weapon names, system names, pilot names, and famous lines when unsure.
- Standard terms: 高达 (Gundam), 吉翁 (Zeon), ν高达, 光束军刀 (beam saber), 光束步枪,
  米诺夫斯基, 月光蝶 (Moonlight Butterfly), 新人类 (NewType/NT), 强化人 (Cyber-Newtype),
  提坦斯 (Titans), 奥古 (AEUG), 扎夫特 (ZAFT), 联邦, 布莱特·诺亚 (Bright Noa),
  阿姆罗 (Amuro), 夏亚 (Char). Keep Japanese rank words (大佐/中佐/少佐…).
- Keep Latin model codes / system names as-is: ALICE, GP01, ∀, Ex-S, F91, VSBR, ID, NT,
  SEED, PLANT, MEPE. Use `·` between name parts, `、` for pauses, `……` for ellipsis.

## Length (important — game boxes are small)
- Chinese is denser than Japanese, which usually helps — but be **concise**. Do NOT pad.
- Do NOT drop meaning to save space; if a field has a name + effect, translate BOTH.
- For pilot/unit NAMES specifically: keep them SHORT (they sit in a narrow nameplate);
  translate the reading faithfully (e.g. アスラン・ザラ → 阿斯兰·萨拉).

## Output
- Read the assigned staging JSON (`units.json` or `characters.json`), work ONLY your index
  range, and write your slice to the given output file as JSON keyed by id (utid/cid string).
- Preserve list ORDER (weapons/specials/ids/barks) exactly 1:1 with the input for mapping.
- Also print a short note of notable corrections + the key web sources used.
- Do NOT edit any other file. Text only — encoding into the ROM is handled separately.
