# TRANSLATION_GUIDE — how the translation content is organized and QA'd

This is the *content* side of the project: how translated text is stored, the terminology
system, the style rules the font imposes, and the QA pipeline that actually caught defects.
The byte-level mechanics live in `TEXT_SYSTEM.md`; build mechanics in the build docs.

---

## 1. Organizing principle: semantic tables keyed by meaning

Translation data lives as **JP→ZH tables keyed by stable game identities**, never as loose
byte patches:

* **Names** (units, weapons, pilots, ID commands, abilities, parts…) are keyed by their
  record identity (unit-type id, char-DB record, command index). This is non-negotiable —
  early name data was keyed by *string position* and a record→name mis-keying shipped
  wrong-name-on-wrong-character bugs across the whole roster (Athrun labeled as Cima, ∀
  labeled 精神感应高达) that survived until an all-unlocked playtest exposed them. A
  name-identity gate now checks "ZH == canonical translation of THIS record's real JP".
* **Dialogue** is keyed per stage file + block (JP offset & JP text as identity), with the
  authored ZH and its encoded payload stored together.
* **Variants are their own rows**: a renamed unit's `／BWS`-style loadout/transform
  siblings are separate master strings with their own JP keys — rename passes must sweep
  them (a base-name-only rename left the BWS form on the old name).
* **Growth budgets travel with the data**: every surface has a byte and/or pixel budget
  (see TEXT_SYSTEM §6); rows record the budget so re-translation respects it up front.
  Where budgets forced compaction (`完全回避→完回避`) and capacity later appeared, rows
  were re-expanded to natural phrasing — the compact form is a *fit artifact*, not the
  translation of record.

## 2. Terminology: the term library

A single terminology library (JP → 简体中文, with per-row provenance) governs every name and
recurring term. Its working rules, distilled:

* **Mainland-Simplified conventions**, verified against mainland sources (百度百科, 萌娘百科,
  灰机wiki, biligame, B站 usage); ≥2 independent signals required to change an entry.
  Taiwan/HK forms are explicitly avoided (e.g. 光束军刀 not 光束剑; 高达 not 鋼彈).
* **Unified transliterations** for characters — one canonical form per person, e.g.
  夏亚·阿兹纳布尔 (Char), 阿姆罗·雷 (Amuro), 布莱德·诺亚 (Bright), 卡缪·维丹 (Kamille),
  捷多·亚西塔 (Judau), 基拉·大和 (Kira), 哈曼·卡恩 (Haman), 阿纳贝尔·卡多 (Gato),
  强尼莱汀 (Johnny Ridden), 深村玲 (Rain Mikamura — official kanji), 迪安娜·索雷尔 (Dianna).
  Japanese-name characters use CJK surname+given order with **no** separator dot
  (浦木宏, 小林隼人, 天田士郎); non-Japanese names use `·` (U+00B7).
* **Faction/series canon**: 吉翁 (Zeon), 联邦 (Federation), 提坦斯 (Titans), 奥古 (AEUG),
  新吉翁, 隆德·贝尔, 预防者 (Preventer — 预防者·风 = Wind), 大天使号/主天使号 pairing.
* **Unit canon** examples: ドム→大魔 (cascades 里克·大魔), ゲルググ→勇士, ハイザック→高扎古,
  リ・ガズィ→灵格斯, イージス→圣盾高达, トールギス→多鲁基斯, ザク・ウォーリア→扎古勇士,
  セイバー→救世主高达, インコム→线控炮, サイコフレーム→精神感应框架.
* **Military ranks stay in the Japanese 佐/尉 system** (夏亚大佐, not 夏亚上校) — an owner
  decision after both styles shipped.
* **Owner-decided exceptions**: PLANT stays Latin "PLANT"; 穆 for Mu (short form);
  マスターユニット = MASTER机; a few NPC transliteration variants left as-is where mainland
  sources genuinely split.
* The library also carries a **decoder-noise appendix** (known misread JP forms → intended
  words, e.g. 副郷→復興, 搭降→投降) from the era when the decode map was homophone-garbled —
  when source JP looks weird, translate the *intent* and check that appendix.

## 3. Style rules (font-imposed + editorial)

Hard constraints (unrenderable if violated — the encoder rejects them):
* `、` for every pause — **the font has no `，`**; no `～`; `……` in even pairs; full-width
  `！？`; `·` between name parts; no spaces (use a blank cell only where a gap is required).
* Every character must exist in the glyph atlas; missing hanzi are added as new atlas glyphs
  (Noto Sans CJK SC, size 13, alpha 110 — the matched raster style) into verified-free
  slots, or the wording is changed. Translators may not assume a glyph exists — validation
  encodes every row.

Editorial:
* Boxes are 2 lines × 18 cells; write full natural lines to that budget — no telegraphic
  compaction, no forced JP line structure. `▼` marks page waits.
* Speech-register: drop Japanese politeness suffixes; person names without honorifics;
  standard renderings for battle idioms (くらえ！→接招！/看招！; 行くぞ！→上！),
  onomatopoeia conventions (くっ→唔).
* Famous quotes (cut-ins) use the community-canonical Chinese renderings — they were
  individually web-verified rather than freshly translated.
* Consistency sweeps run *across* surfaces: a name fixed in dialogue must also be fixed in
  nameplates, rosters, ID commands, encyclopedia and the guide (they are different stores).

## 4. The QA process that worked

Four escalating layers (details in `TESTING_APPROACH.md`):

1. **Static gates** — byte-level invariants over the built ROM: audio header, combat-byte
   safety, dialogue-block integrity, script-pointer & CFG checks, alignment, coverage
   ratchets, width budgets, name-identity vs. canonical terms, bark framing, charmap/font
   consistency. Fast, deterministic, run on every build (`test/run_static.py`).
2. **Emulator smoke + freeze grinds** — fresh-boot to title/new-game/first stage; scripted
   combat grinds through deploy→combat→cut-in; scenario drives on owner saves (NG+ entries,
   ending scenes). Frame-identity + abort-PC oracles.
3. **Screenshot + VLM judging** — every render-affecting change is verified by a vision
   judge on actual upscaled crops (full-screen + per-field) against an explicit defect
   vocabulary (overflow, clip, overlap, overpaint, baseline/size mismatch, stray glyphs,
   wrong script). Pixel metrics are pre-filters only.
4. **Owner live playtest** — the ultimate oracle. Owner reports arrive as screenshots +
   saves; each becomes (a) a root-cause investigation, (b) a fix, and (c) a **new gate**
   with a proven RED→GREEN self-test so the class never ships again.

Translation-specific QA within that frame:
* **JP-sanity passes before translating**: re-decode the source corpus and run a Japanese
  tokenizer over it (zero non-words) so no one translates decoder garble.
* **Budget/encodability validation per row**: every ZH row must encode all-atlas, fit its
  box/budget, and contain no forbidden punctuation — enforced by a validator, not by trust
  (fleet-translated rows routinely contained an unencodable char).
* **Untranslated-text audits by reachability**: the gate walks every reachable display
  block and flags JP tokens; the intentional-JP allowlist is explicit and audited.
* **Cross-checking numbers/effects against the engine tables** (the effect coefficient
  table is the source of truth — an intermediate JSON once mislabeled a stat and the wrong
  label shipped).

## 5. Working with translator fleets (if you regenerate content)

The content was produced by many parallel translation agents against shared instruction
files. Process learnings: give each agent the hard constraints (charset, budgets, canonical
names) *in the brief*; centralize validation (never trust agent output — validate encode +
budget + style per row); split large slices per stage with a merger; and keep the term
library authoritative — agents propose, the library disposes.
