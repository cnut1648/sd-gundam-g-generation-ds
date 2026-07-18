# v1.2 Phase 3 — 关卡剧情 (Stages) retranslation fleet brief

READ FULLY BEFORE TRANSLATING. You are one translator agent in a fleet. You are
assigned EXACTLY ONE stage of the DS game **SDガンダム ジージェネレーション DS**
(SD高达G世纪DS) — one `_STG*` scenario: its mission title, its pre-battle
briefing lines, and all of its in-battle / cut-scene dialogue. You translate
Japanese → **natural mainland Simplified Chinese**.

## THIS IS A PURE TEXT TASK — never open an image

You translate ONLY from the Japanese **text** in `data/jp/`. Do NOT open, render,
or reason about any picture: no ROM pixels, no font atlas, no emulator, no
screenshots, no `攻略.html`, no `.png`. The JP text in `data/jp/` has been
decoder-audited to match the game pixel-for-pixel and swept 100% clean of
undecoded garbage, so the text alone is authoritative and complete. Reading the
stage JSON and the terminology table is the entire input; anything visual is out
of scope and forbidden.

## 0. Read the JP straight from data/jp — it is the single source of truth

Your input is ONE file, read directly:  `data/jp/stages/<stage>.json`
(e.g. `data/jp/stages/_STG00.json`).  It is the **verified decode** of the
cartridge (a full decoder audit confirmed every glyph against the game's pixels
and a whole-folder sweep removed all undecoded garbage), so the JP is exactly
what appears in-game — translate it directly and do not copy the JP anywhere.
What you read and what to translate:

* `descriptors[0].title` — the mission title string. Translate it.
* `briefing[]` — `{off, text}` pre-battle mission lines. Translate every one.
* `blocks[]` — dialogue blocks `{off, text, speaker, ...}`. **Translate exactly
  the offsets in the `TRANSLATE_OFFSETS` list you are given for this stage** —
  that list is the authoritative set of real in-game display lines (it is derived
  from the pixel-verified v1.1 line set plus the VM-reached blocks, so it is
  immune to the extractor's fallible reachability guess). For each listed `off`,
  read that block's `text` and translate it.
* Do **NOT** decide inclusion from the `reachable` field. `reachable:false` does
  NOT mean "not shown in-game" — it only means the extractor's static VM walk did
  not reach that block. Many `reachable:false` blocks ARE shown (endings/
  epilogues, dynamically-dispatched scenes); others are non-text scan noise. Your
  `TRANSLATE_OFFSETS` list already separates the two, so you never guess.
* Blocks may also carry `"narration": true` and `"choice": true` (see below).

A line you are asked to translate should read as fluent Japanese. If a listed
block's `text` instead looks like a non-text smear or shows `{SLOT:n}` /
`{B:n}` / `{F0:n}` / `□`, do NOT translate it — flag that `off` in `notes` and
skip it (it means the offset list or the decoder needs a fix). `{00}` / `{01}`
are layout/structure markers (see §3), not garbage.

## 1. Voice & sources (mandatory)

* This is **spoken drama**, not encyclopedia prose. Write natural, idiomatic
  简体中文 dialogue that sounds like the character actually talking — punchy in
  combat, formal on the bridge, cold from a villain. Read the whole file first so
  each reply matches the line before it.
* **Match each speaker's voice.** A veteran ace, a green rookie, a scheming
  officer and a child pilot do not talk alike — keep each speaker's register
  consistent across the stage (resolve who is speaking via §2).
* **Narration is different.** Blocks flagged `"narration": true` are the
  omniscient narrator / on-screen setting text (opening scrolls, "宇宙世纪
  0079……", "地球、卫星轨道上……"): neutral, literary third-person, NO character
  coloring — even though a `speaker` cid is still attached.
* **Famous lines win.** Many stage lines are iconic anime quotes. When a line is
  a known quote, the mainland-community canonical rendering BEATS your own
  wording — **use web_search**: 高达wiki (wiki.biligame.com), 萌娘百科
  (zh.moegirl.org.cn), 百度百科 (baike.baidu.com), 灰机wiki (huijiwiki.com),
  bilibili. Cite what you used in `web_sources` (2+ whenever you looked
  something up). E.g. 「認めたくないものだな…」→「真是不想承认啊……」-class canon
  wordings; search the specific line rather than paraphrasing.
* Established terms: 高达, 吉翁, 联邦, 提坦斯, 奥古(A.E.U.G.), 新吉翁, 扎夫特,
  地球联合, 新人类(NT), 强化人, 月光蝶, 精神感应框架, 米诺夫斯基粒子, 一年战争,
  宇宙世纪, 格里普斯战役, 木马(白色基地的蔑称). Ranks stay Japanese style
  (大佐/中佐/少佐/大尉/中尉/少尉/曹长…). Latin codes stay Latin (∀, GP01, MS, NT).

## 2. Speakers & TERMINOLOGY are LOCKED to phase 1

Phase 1 (characters + units) is DONE and is the ONLY naming authority. Every
character, mobile suit, warship, weapon and system name MUST use the exact
phase-1 Chinese — never invent a variant.

* The naming table is `audit/translate2/staging/terms.md` (285 character names,
  227 unit/ship names, 228 weapon names, 32 special tags). This is a convention
  reference, not input to copy.
* **Resolve each block's `speaker`** (an integer char-DB `cid`) to a name:
  `data/jp/characters.json` maps `cid → name.text` (the JP name); look that JP
  name up in `terms.md` for the locked Chinese. Generic speakers resolve the same
  way (ジオン士官→吉翁士官, コンスコン→康斯柯, マリガン→马利根).
* If a proper noun is missing from `terms.md`, search the wiki, use the
  mainland-canonical form, and record it in `web_sources` + `notes` so it can be
  folded into the convention. Never leave romaji or a raw guess in the text.

## 3. LINE BREAKS

The JP carries the game's own inline markers: `{00}` (line break inside a box)
and `{01}` (page / record end). For ordinary text these are Japanese-width
layout — **ignore them and write flat prose**. A downstream step measures real
pixel width, wraps each string to its in-game box, paginates, and re-inserts
every byte marker; then we render it in the game and iterate. Manual breaks from
you would fight that step.

Two exceptions where a marker is STRUCTURE, not layout — keep it:

* **Choice records** (`"choice": true`) look like `『option A』{00}『option B』`
  (~8 in the whole game). The `{00}` there SEPARATES two selectable options.
  Translate each option and KEEP both `『』` pairs and the `{00}` between them.
* **Multi-field records** that split distinct items with `{01}` (rare staff
  credits, e.g. `片桐{01}圭一郎`): keep the `{01}` splits in place.

Keep each dialogue/briefing line roughly within its JP length so it still fits
its box; the applier flags any record that overflows so it can be tightened
later — never pre-chop a sentence yourself. The **title** is a tight name-pool
string: keep it short (aim ≤ the JP glyph count); a title's own `{01}` = an
intentional two-line split (e.g. `めぐりあい{01}宇宙`), keep it.

## 4. Charset (violations cannot be built)

* **Dialogue & briefing (renderA / stage surface):** allowed = CJK, `、 。 ！ ？
  …… ・ （ ） 「 」 『 』`, half-width digits `0-9`, Latin letters, `·` (U+00B7) in
  names, and `%` `+` written HALF-WIDTH. Ellipsis is `……` in even pairs. Keep an
  opening quoted line in 「」 if the JP has it. NEVER use `，` (use `、`), never
  `～`, never `“ ”`, never fullwidth `％ ＋`, never arrows.
* **Title (name-pool surface, strict):** CJK, digits, Latin, `·`, and `！ …` only;
  avoid `、 。 ？ ・ ： ； （ ）` and quotes. If you truly need a forbidden mark,
  put the ideal in `notes` and give a clean fitting value as `title_zh`.

## 5. Output — translation decisions only, keyed by the JP offsets

Write ONE file: `audit/translate2/staging/out/stages/<stage>.json`. STRICT JSON
(UTF-8, double quotes, no trailing commas). Do NOT edit any repo file, do not run
builds, and do NOT copy the JP text back — only your Chinese, keyed by the exact
`off` from `data/jp`. (A later apply step encodes these staging files into
`data/cn/`, the v1.2 translation folder, keyed by the same `off`; you never touch
`data/cn` yourself.)
```json
{
 "stage": "_STG00",
 "web_sources": ["https://..."],
 "title_zh": "桑吉巴尔追击",
 "briefing": [
   {"off": "0x1985a4", "zh": "若要对抗木马、以我军现有的战力还远远不够"}
 ],
 "blocks": [
   {"off": "0x111e", "zh": "……情况如何？"},
   {"off": "0x196c", "zh": "『下达指示（执行教程）』{00}『我也驾驶MS参战』"}
 ],
 "notes": ""
}
```

* Key every entry by its exact `off` from `data/jp/stages/<stage>.json`, same
  order. Include the title, EVERY `briefing[]` line, and exactly the `blocks[]`
  lines in your `TRANSLATE_OFFSETS` list. Never add, merge or reorder records.

## 6. Final self-check before writing

1. JSON parses; `stage` matches; you output ONLY this stage; no JP copied back.
2. Every `off` in `TRANSLATE_OFFSETS` present, in original order; nothing outside
   it added; inclusion never decided from the `reachable` field.
3. No manual `{00}`/`{01}` except in `『…』{00}『…』` choices and `{01}` splits.
4. Every character / unit / weapon / system name equals the phase-1 `terms.md`
   value (speakers resolved via cid); no romaji, no invented variant.
5. Charset: no `，` / `～` / `“”` / fullwidth `％＋`; ellipsis `……`; pauses `、`;
   quotes 「」/『』. Title on the strict name-pool charset.
6. Narration reads as neutral narration; each speaker's lines match their voice;
   famous lines use web-verified community wording.
7. `web_sources` lists the pages you actually used.
