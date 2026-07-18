# v1.2 Phase 2 — 资料馆/图鉴 (Library) retranslation fleet brief

READ FULLY BEFORE TRANSLATING. You are one translator agent in a fleet. You are
assigned EXACTLY ONE library entity — one character biography (by `cid`), OR one
unit (mobile-suit/warship) biography (by `utid`), OR one weapon-name batch (a
range of the weapon list) — of the DS game **SDガンダム ジージェネレーション DS**
(SD高达G世纪DS). You translate Japanese → **natural mainland Simplified Chinese**.

## THIS IS A PURE TEXT TASK — never open an image

You translate ONLY from the Japanese **text** in `data/jp/`. Do NOT open, render,
or reason about any picture: no ROM pixels, no font atlas, no emulator, no
screenshots, no `攻略.html`, no `.png`. The JP text has been decoder-audited to
match the game pixel-for-pixel, and the library records you read (character
bios, unit bios, weapon names) are verified free of undecoded markers, so the
text alone is authoritative and complete. Reading the JP string and the
terminology table is the entire input; anything visual is out of scope and
forbidden.

## 0. Read the JP straight from data/jp — it is the single source of truth

Open the source file directly and find YOUR record; do not copy the JP anywhere.

* **character bio** — you are given a `cid`. Open `data/jp/characters.json`, find
  the object whose `cid` matches, read `bio.text`. (Orphan bios live in the same
  file's `unassigned_bios[]`, addressed by index.)
* **unit bio** — you are given a `utid`. Open `data/jp/units.json`, find the
  object whose `utid` matches, read `bio.text`. (Orphans: `unassigned_bios[]`.)
* **weapon batch** — you are given an `off` range. Open `data/jp/library.json`,
  read `weapon_list[]`; translate your assigned entries, SKIPPING any with
  `"reachable": false` (that is the file's glyph-priming blob, not a name).

This JP is the **verified decode** of the cartridge — a full decoder audit
confirmed every glyph against the game's pixels, and every bio + weapon-name
record you translate is verified to carry no undecoded markers, so the text is
exactly what appears in-game. Translate it directly. You will NOT see any
`{SLOT:n}` / `{B:n}` / `{F0:n}` / `□` in a bio or weapon name; if one somehow
appears, STOP and report it (it means the extractor regressed) rather than
guessing. `{00}` / `{01}` are layout/structure markers (see §3), not garbage.

## 1. Voice & sources (mandatory)

* Fluent, natural 简体中文 as the mainland Gundam community writes encyclopedia
  prose — complete sentences, smooth narration, NOT machine-literal and NOT
  telegraphic. Read the WHOLE bio first; keep one consistent register (a 图鉴
  entry is descriptive third-person; a quoted line inside 「」 keeps the
  character's own voice).
* **Use web_search extensively** for proper nouns, mecha lore, pilots, factions,
  episodes: prefer 高达wiki (wiki.biligame.com), 萌娘百科 (zh.moegirl.org.cn),
  百度百科 (baike.baidu.com), 灰机wiki (huijiwiki.com), bilibili usage. Cite the
  pages you used in `web_sources` (2+ for anything you looked up).
* Established terms: 高达, 吉翁, 联邦, 提坦斯, 奥古(A.E.U.G.), 新吉翁, 扎夫特,
  地球联合, 新人类(NT), 强化人, 月光蝶, 精神感应框架, 米诺夫斯基粒子, 一年战争,
  宇宙世纪, 格里普斯战役. Ranks stay Japanese style (大佐/中佐/少佐/大尉/中尉/
  少尉/曹长…). Latin codes stay Latin (∀, GP01, F91, RX-78, MS, NT, SEED, DG细胞).

## 2. TERMINOLOGY IS LOCKED to phase 1 (the single binding convention)

Phase 1 (characters + units) is DONE and is the ONLY naming authority. Whenever a
character, mobile suit, warship, weapon, or special-system name appears in your
bio, you MUST use the exact phase-1 Chinese for it — never invent a variant.

* The naming table is `audit/translate2/staging/terms.md` (285 character names,
  227 unit/ship names, 228 weapon names, 32 special tags). Look up every proper
  noun your bio mentions there. (The JP name middle-dot is `・`; a bio may show
  it as `·` — same name.)
* Examples already fixed by owner ruling — do not deviate: 德卡契 (ドゥガチ,
  NOT 杜加奇), 先锋母舰 (マザー・バンガード), 强化型AI, 卡缪 (卡缪·维丹),
  夏亚·阿兹纳布尔, 阿姆罗·雷, 尊者高达/东方不败, 光束军刀 (NOT 光束剑).
* If a name you need is NOT in `terms.md`, search the wiki, use the
  mainland-canonical form, and record it in `web_sources` + `notes` so it can be
  folded into the convention.

## 3. LINE BREAKS: do NOT insert any — write flat prose

This is the most important mechanical rule and the reason phase 2 is separate.

* The `bio.text` still shows the game's ORIGINAL Japanese line breaks as `{00}`
  (line), `{00}·`/`{00}{04}` (continuation line), `{00}々`/`{00}{07}` (page
  break), `{00}{01}` (record end). **Ignore all of them.** They were laid out for
  Japanese width and are meaningless for Chinese.
* You output **flat Chinese prose with NO manual line breaks**. A downstream step
  measures the real pixel width of every glyph, wraps lines to the in-game text
  box, paginates, and inserts all the byte-level markers — then we verify by
  rendering it in the actual game and iterate. Manual breaks would fight that.
* **Preserve only PARAGRAPH/PAGE structure**, because that is content, not
  layout: the JP uses `々` (page break, i.e. the `{00}々` marker) to separate
  narrative sections (e.g. the opening description vs. an "after the war…"
  epilogue). Split your translation into an ordered `paragraphs` array, one entry
  per JP page-section. Within a paragraph, one continuous string, no breaks.
* Quoted speech: JP bios often open with a 「…」 line (the character's own words).
  Keep it as its own leading paragraph and KEEP the 「」 marks (they are allowed
  and encodable here; the applier gives quote lines their indent). Do not use “”.

## 4. Length & charset

* Keep each bio's total length roughly comparable to the JP (Chinese is denser,
  so a faithful translation normally fits). Do not pad or add lore the JP omits.
  The applier reports any record that overflows its in-game space; if yours is
  flagged you may be asked to tighten wording (never drop meaning).
* Charset (bios render on the renderA 12×12 atlas — the "stage" surface):
  allowed = CJK, `、 。 ！ ？ …… ・ （ ） 「 」 『 』`, half-width digits `0-9`,
  Latin letters, `·` (U+00B7) in names, `%` and `+` written HALF-WIDTH.
  NEVER use `，` (use `、`), never `～`, never `“ ”`, never fullwidth `％ ＋`,
  never arrows. Ellipsis is `……` in even pairs.

## 5. Output — translation decisions only, keyed to the JP record

Write ONE file per entity under `audit/translate2/staging/out/lib/`
(`char_<cid>.json`, `unit_<utid>.json`, or `weapon_<nn>.json`). STRICT JSON
(UTF-8, double quotes, no trailing commas). Do NOT edit any repo file, do not run
builds, and do NOT copy the JP text back — only your Chinese. (A later apply step
encodes these staging files into `data/cn/`, the v1.2 translation folder, keyed
by the same JP id/offset; you do not touch `data/cn` yourself.)

Character or unit bio:
```json
{
 "cid": 4,
 "kind": "char_bio",
 "web_sources": ["https://..."],
 "paragraphs": [
   "「都怪你把瑟拉一个人独占……才会变成这样啊——！！」",
   "被配属到吉翁军MS实验部队布拉德战队的新人类、精通MS工学与NT理论的研究员型驾驶员。",
   "在弗拉纳冈机关……"
 ],
 "notes": ""
}
```
(For a unit bio use `"utid": <n>` and `"kind": "unit_bio"`.)

Weapon batch — key each name by its `off` from `data/jp/library.json`:
```json
{
 "kind": "weapon_list",
 "web_sources": ["https://..."],
 "items": [ {"off": "0x1d2", "name_zh": "高达流星锤"} ],
 "notes": ""
}
```
For weapons, reuse the phase-1 weapon term from `terms.md` verbatim; only the
handful with no phase-1 match need a fresh, wiki-checked name.

(Hangar **parts** are NOT in this phase — they are a separate hangar UI surface,
not a library/图鉴 entry. They now decode cleanly in `data/jp/parts.json`
(ザク系変換パーツ, ムーバブルフレーム…) should they be scoped in later.)

## 6. Final self-check before writing

1. JSON parses; the id matches your assigned record; you output ONLY it; no JP
   copied back.
2. NO manual line breaks anywhere; `paragraphs` mirror the JP page (々) sections.
3. Every character/unit/weapon/system name equals the `terms.md` value.
4. No `，` / `～` / `“”` / fullwidth `％＋`; ellipsis is `……`; pauses are `、`.
5. Quotes kept as 「」; register is smooth encyclopedia Chinese.
6. `web_sources` lists the pages you actually used.
