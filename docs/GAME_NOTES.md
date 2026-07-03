# GAME_NOTES — what this game is and where its text lives

## 1. The game

**SD Gundam G Generation DS** (Bandai, 2005, Nintendo DS, gamecode `ASGJ`, Japan-only,
32 MB). A turn-based tactics / unit-raising game spanning the whole Gundam multiverse
(0079 → Turn A → SEED, plus G-Generation originals). The player fields a mothership plus
squads of mobile suits across scripted story stages, develops/captures units, and recruits
pilots.

This repository is a **Japanese → Simplified-Chinese fan translation** of it: a single-pass,
data-driven rebuild that reproduces the shipped translated ROM byte-identically from the
Japanese ROM plus translation data. The owner of the project is the product owner and
play-tester; every text surface below was translated except a small owner-approved exempt
list (start screen, stage-name banners, staff-credit proper nouns, the squad sub-menu's
compressed tile text, session-number chrome).

## 2. Game structure (what an agent needs to navigate/test it)

* **Sessions/stages.** The campaign is a graph of "sessions": `00`, `00SP`, `01`…`24`, most
  with **a/b route variants** (e.g. `07a`/`07b`), interleaved **X1–X7 extra stages**
  (side-story arcs, some with `_1/_2` two-part scripts), a **tutorial** chain (`TR1–TR3`,
  plus the stage-00 scripted tutorial), **free-battle maps** (`FB1–FB6`), and a **special
  (SP) Jupiter arc** (`SP1a/b … SP7a/b/s`, `11SP`, …) culminating in the "その闇の名は木星"
  finale. Each stage's script is one `_STG*.bin` file (101 total; see `STAGE_FORMAT.md`).
* **The BackStage** (between-stage hub, bottom-screen map): tabs 作戦 (operations: 進撃
  advance / 索敵 free battle / briefing 作戦内容), 編成 (organize: 配属 assignment, 一覧
  roster lists), MS開発 (development: 格納庫 hangar, development trees), システム (system).
  Progression state lives in RAM: current stage id @ `0x0227CC48`, free-battle counter @
  `0x0227CC80` (the shipped ROM patches all seven 索敵-gated transitions from 3-or-4 free
  battles down to **1**, an owner gameplay tweak).
* **索敵 (free battle)** both levels the roster and *gates* the SP-arc transitions
  (24a→SP1a, 24b→SP1b, SP2b→SP3b, SP3a→SP4a, SP3b→SP4b, SP2b→SP4b, 11SP→SP4s).
* **二周目 / New Game+**: clear-count ≥1 unlocks route choices that a first playthrough
  forces (e.g. the 04b and 07b branches check *clear count*, not the SP-mode flag — cheating
  SP mode on does not open them), and an NG+ save can enter stages via the back-stage that a
  fresh save cannot. Two shipped crashes were **only reproducible from owner NG+ saves**
  (a stage-05 load via the X1 back-stage; the post-SP7 ending that unlocks 特别演习) —
  always keep NG+ saves in the test fixtures.
* **SP mode** (harder difficulty) is a separate flag from clear count.
* **In-stage flow**: session card → briefing/intro dialogue (ADV-style boxes with speaker
  nameplates) → deploy roster → player/enemy turns (command menu: 移動/ID/間接/情報 …) →
  mid-stage story demos triggered by turn events → combat scenes with barks and ID cut-ins
  → results. The tutorial (stage 00) narrates every one of these systems.
* **Saves**: normal battery saves plus in-stage suspend; the owner plays **no-cheat** with a
  partial roster (list-row ≠ record-id — a class of UI bug only visible that way).

## 3. Text systems and where each lives

| surface | seen where | storage | notes |
|---|---|---|---|
| **Story dialogue** | ADV boxes in stages | `_STG*.bin` display blocks (`15 … 00 00`) | ~29k lines; grown + reflowed to 18×2 boxes; see STAGE_FORMAT |
| **Speaker nameplates** | above dialogue box | arm9 char-DB `0xDCF18` (+0x04 name ptr), selected by script `06 <id>` | 563 records; renderA-direct |
| **Barks** (battle voice) | during combat/attacks | `0.bin, 1.bin, 1dd.bin, 1de.bin, c4f.bin` | ~8.6k records; sub-line framing; size-locked in place |
| **Cut-ins** (名台詞 famous lines) | ID-command banner in combat | `1dc.bin` + arm9 offset table `0x16EEA8` | 1,287 quotes; grown bank |
| **Unit / weapon names** | rosters, info panels, combat | arm9 master table `0x020B94BC` (+0x00 name, +0x2C weapons) | strings in arm9 pools; battle reads THESE, not the encyclopedia files |
| **Pilot/faction identity labels** | nameplates, rosters | master-table weaponless records (utids ~610–944) + char-DB | must be all-atlas (renderA-direct) |
| **ID commands** (per-pilot battle quotes + effects) | 情報/ID pages, combat | arm9 table `0x020EC994` (+0x00 name, +0x08 summary, +0x22→detail offtab `0xF9048`) | 1,410 records; three text fields per command |
| **ID abilities** | 情報 pages | `1da.bin` (+ arm9 offset table `0x021775C8`), ability names in resident caves | level-learn badges engine-composed |
| **Special ability/defense text** | unit SPECIAL pages | `1df.bin` (RAM-resident `0x0235992C`) / `1e0.bin` (`0x023594EC`) | offset tables in arm9 |
| **Command/effect labels** | ID compact boxes | `1db.bin` (ID-command) + `1da.bin` (ID-ability), offset tables `0x0217716C`/`0x021775C8` | numbers are engine-composed from the coeff table |
| **Parts names/captions** | 編成→一覧→パーツ | `b6e.bin` / `b6f.bin` + arm9 offset tables `0x16B474`/`0x16B518` | 30 real + 10 spare entries |
| **Briefings** (作戦内容) | BackStage operations tab | arm9 briefing table `0x1985A4..0x1A626B`, ZH blobs in the high autoload pool `0x023E7000` | 1,255 records, 2-line viewer |
| **Encyclopedia** (図鑑 bios/quotes) | collection menus | `324.bin`, `c4b.bin` (+ `31e.bin` weapon copy) | note: combat does NOT read these |
| **Menus / stat / info labels** | 情報 panels, HUD | arm9 label arenas (`0x14AC34..0x155B14`, `0x14B1C5+`, stat table `0x3FC30`) | some are raw tiles instead (below) |
| **Tile-graphic text** (not strings) | BackStage tabs, force-HUD, terrain badges, captain badge | `3d3/3d5.bin`, `478.bin`, `48a.bin`, `388.bin` | repainted as graphics |
| **Untranslatable by decision** | squad sub-menu 個別指示/全機… | compressed custom tile codec (expander `0x020A0A86`) | owner won't-fix |

Two byte-identical-text pitfalls worth remembering when *reading* the game:
the ~150 hanzi shared between JP and ZH ship as narrow single-byte codes (correct Chinese,
8 px look); and the in-battle screens read arm9 pools while near-identical text also exists
in encyclopedia data files — edit the pool the screen actually reads.

## 4. Gameplay-relevant engine facts

* One stage resident at a time in the fixed buffer `0x0232C800` (79,872 B cap per stage).
* The dialogue/event system is a bytecode VM (dispatcher `0x0209EBAC`; cutscene/ending
  driver with its own jump-table VM @ ctx `0x0227CD0C`). Mid-stage demos and endings are
  *event subroutine chains* — corrupting one CALL silently hangs the stage.
* Combat cut-ins, barks and the ID system key off the same pilot records the info pages
  show; a data error there manifests in combat, so combat-heavy tests double as data tests.
* The heap grows with roster size and campaign progress: bugs that need a "developed" save
  (big roster, late stage, NG+) will not reproduce on a fresh save (see LESSONS C1/E8).
* BIOS data-abort spin at `0xFFFF0104` is the universal "black screen" signature — any
  hard freeze investigation starts by confirming the PC parked there.

## 5. Owner-approved exemptions & conventions (summary)

Kept Japanese: start screen & stage-name banner art, staff-credit studio/person names,
session chrome (`セッション00` bottom-screen instance), squad sub-menu (tile codec), two
scream onomatopoeia, dev-only debug strings. Everything else is Simplified Chinese with
mainland-convention terminology (see `TRANSLATION_GUIDE.md`): 高达 for Gundam, 吉翁 for
Zeon, Japanese rank system retained (大佐/中佐/少佐…), `、` for pauses (the font has no
`，`), `……` ellipses, `·` name separators, PLANT kept Latin, and unified character-name
transliterations per the term library.
