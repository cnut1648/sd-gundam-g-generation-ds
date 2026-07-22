# LESSONS_LEARNED — the wrong-turn catalog

This project went through roughly a hundred iterations of reverse-engineering, translation,
breakage and repair. This file is the distilled catalog of **what we believed, why it seemed
right, what it broke or how it was disproven, what the truth is, and the guard that now
prevents it**. It is organized by theme. Read it before attempting anything similar — most of
these mistakes are *attractive*: they were made by careful people with plausible evidence.

Entry format:
> **Believed** → **Why it seemed right** → **Broken/Disproven by** → **Truth** → **Guard**

---

## A. Rendering & fonts

### A1. The "OBJ-text render wall" (names shown in Japanese believed render-limited)
* **Believed:** unit/weapon/ID-command names on the in-battle info screens could not render
  Chinese: a "pre-decoded glyph cache / OBJ-text path" (engine-A OBJ VRAM `0x06400000`)
  supposedly rendered only low glyph slots (<2196), making those screens a hard engine wall.
* **Why it seemed right:** editing the "name files" (`31e.bin`, encyclopedia banks) changed
  nothing on screen; expanding the atlas changed nothing; a garbled test render seemed to
  confirm a path that couldn't fetch high slots.
* **Disproven by:** a live probe that repointed one name accessor at a Chinese test string —
  it rendered perfectly on the exact "walled" screen. gdb tracing then showed those screens
  use the SAME decoder→drawer→trampoline→renderA pipeline as working dialogue.
* **Truth:** the render path was fully ZH-capable all along. The names were Japanese because
  the in-battle screens read **untranslated arm9 string pools** (ID-command table `0x020EC994`
  → arm9 quote pool; unit master table `0x020B94BC`; the alt-dictionary `0x0212D770`), while
  the bakes had targeted **encyclopedia copies** (`31e.bin` etc.) that battle never loads.
  Pure untranslated-data, zero render limitation.
* **Guard:** coverage detectors walk the *actual source tables* (`0x020EC994`, `0x020B94BC`,
  char-DB `0xDCF18`) and diff against the original ROM; a name that "reverts to JP" fails the
  build. Conceptually: **always locate the source the live screen reads (gdb/live probe)
  before declaring a render wall.**

### A2. The "atlas cap at 4246 slots" wall (savestate artifact)
* **Believed:** the info/ID panel could not render glyph slots ≥ 4246 — reads "sparkled".
* **Why it seemed right:** a reproducible test showed garbage for exactly those slots.
* **Disproven by:** ten independent analyses converging: there is **no bounds check, no cache,
  no cap** anywhere in the code; the renderA rasterizer reads `atlas + slot*36` unconditionally.
* **Truth:** the repro **savestate had been captured on an older build whose atlas had only
  4246 slots**. Reads past that snapshot's atlas ran into garbage *in the savestate*, not on a
  real boot. On fresh boot the full atlas renders every slot.
* **Guard:** never verify atlas/glyph coverage on a savestate whose atlas predates the
  candidate build; atlas-dependent render checks use a fresh boot (or poke the runtime atlas
  at `0x023027A0` to the candidate's before capturing).

### A3. The renderB font relocation (fifteen rounds of wrong garble theories)
* **Believed (in sequence):** the UI garble came from out-of-atlas dictionary slots; from a
  too-small atlas; from a load-time cache. Each theory got its own failed fix.
* **Why it seemed right:** the sparkle metric used to judge renders flagged even *clean
  Japanese* as garbage (original ROM scored above the "garbage" threshold), so every
  experiment "confirmed" whatever theory was being tested.
* **Disproven by:** recalibrating the metric to *extra*-sparkle vs. the original-JP golden
  (original = 0.00), then bracketing builds: the garble appeared exactly when an early build
  **relocated the renderB 8×16 font to a heap bank (`0x02326E00`)** — that copy was corrupt
  for JP kanji slots. Reverting only the font-base literal fixed everything.
* **Truth:** one word. File `0x1321C` must point at the in-image font `0x02133F14`. The
  relocation was also *unnecessary*: the dispatch trampoline already routes ZH (slot ≥2196)
  to renderA, so renderB only ever needs the original JP glyphs.
* **Guard:** static check on the `0x1321C` literal; and the meta-lesson — **calibrate the
  metric on known-good input before trusting a single verdict from it.**

### A4. Same slot number ≠ same glyph across fonts (the "·"→"8"→"3" chain)
* **Believed:** overwriting renderB font slot 4 with "·" was a safe fix for the pilot-name
  middle dot rendering as a displaced "8" on info panels.
* **Broken by:** every number containing the digit **3** started rendering "·" ("430" → "4·0",
  "LV 3" → "·") — renderB slot 4 IS the digit "3"; renderA slot 4 is "·". Two fonts, same
  index, different glyphs.
* **Truth:** never bridge fonts by slot number. Encode "·" as an atlas token (slot 4139,
  ≥2196) so every path routes it through renderA; leave renderB's digits alone. The same
  confusion in the other direction produced the classic "1db garble": text encoded with
  *atlas-convention* slot numbers <2196 rendered through the renderB font = wrong glyphs.
* **Guard:** the encoder emits ≥2196 atlas tokens for all CJK; punctuation/digits reuse the
  byte values the original JP used on that surface; a consistency check verifies every
  charmap slot < the ROM's actual slot count.

### A5. "18 of 19 analysts agreed" — and the one dissenter was right (効/效 sparkle)
* **Believed:** the「（効果）」label sparkled because 効 was baked to an out-of-range atlas
  slot; remapping it in-range would fix it. 18/19 independent static analyses concurred.
* **Broken by:** the live test still showed garbage after the fix.
* **Truth:** at that time that label was drawn by a path that rendered only low slots
  correctly; the working fix was to encode 効 as the JP shinjitai glyph at slot 756 (<2196).
  (The path itself was later understood fully — see A1.)
* **Guard:** *consensus is not proof*. The live render (screenshot + vision judge) is the
  gate for every render-affecting change.

### A6. Char-tile aliasing ("ghost names") — clamp to the layout budget, not the buffer stride
* **Believed:** ghost glyph fragments next to translated names ("罗冂", "田古") were stale
  VRAM needing a field-clear; later, that clamping the copy width to the scratch stride
  (`ctx[8]`) would fix it.
* **Why it seemed right:** the fragments looked like un-erased leftovers; the stride seemed
  like "the field width".
* **Broken/Disproven by:** a global copy-width change fixed the ghost but corrupted a
  different list (the helper is generic, 107 call sites); the stride clamp was a no-op
  (`ctx[8]`=32 is the scratch stride, not the 10-tile field budget).
* **Truth:** ZH glyphs are 12 px on an 8 px tile grid, so a name's tile count
  `r7=(penPx+7)>>3` can exceed the panel's per-name tile budget; the overflowing tiles alias
  the NEXT name's char-tiles (that's why the "ghost" is always the *next* row's glyphs).
  Fix = clamp `r7` to the layout budget, **scoped by the draw-context signature**
  (map base + tile-origin fields, e.g. `ctx[0x5C]==0x0620F000 && ctx[4]∈{0x11,0x12}`), via a
  code cave, leaving all other call sites byte-identical.
* **Guard:** every scoped render patch ships with an *enumerated-equivalence* proof (all
  non-target draw calls produce byte-identical output) plus live before/after captures.

### A7. Fix-vs-fix conflict: blanking a fixed VRAM cell
* **Believed:** unconditionally blanking the two tilemap cells `0x0620F1B0/B2` (the squad
  panel's trailing ghost) was a safe cosmetic fix.
* **Broken by:** a different page (ID-ability list) legitimately renders glyph bottom-halves
  in those exact cells — the "fix" chopped 力/U in half there.
* **Truth:** fixed-address VRAM writes are shared across screens. The safe generalization is
  *state-driven*: blank a trailing cell only if its char-tile is all-zero at plot time.
* **Guard:** when a fix writes a fixed VRAM address, audit every screen that uses that
  address; prefer content-conditional patches.

### A8. Forced per-frame redraw starves input
* **Believed:** re-decoding the portrait every frame it was on screen was a robust
  "auto-heal" for a stale-portrait bug.
* **Broken by:** ~60 redraws/s starved the input loop and raced panel composition — the
  cursor stuck, buttons dropped, the bottom panel intermittently wiped.
* **Truth:** force expensive work only on state *change* (compare the settled cursor row and
  re-arm on transitions).
* **Guard:** live nav test (cursor moves N times, panel intact, 0 wiped frames).

### A9. Glyph re-baseline silently reverts newer glyphs
* **Believed:** a glyph-cell baker that re-derives the atlas from an older reference build
  and writes only its own new cells is safe.
* **Broken by:** 25 previously fixed glyph bitmaps silently reverted to stale bitmaps (容
  rendered as 叽, 钥 as 训, …), caught only by an owner screenshot much later.
* **Truth:** re-baselining discards every glyph fixed *between* that base and the current
  tip. Atlas edits must start from the current tip, or re-apply every glyph pass.
* **Guard:** an atlas-vs-reference-font bit-exactness check over every *added* glyph that
  still owns its slot. Note the subtlety: **IoU vs. the reference font is not an absolute
  correctness signal** — bulk older glyphs legitimately score 0.1–0.6 (different rasterizer)
  while rendering fine; only differential comparison (tip vs. candidate) separates
  regressions from style variance.

### A10. New-glyph mechanisms: prefer native free slots over render-path patches
* **Believed:** the way to add glyphs beyond a "full" atlas was a cleverer render-dispatch
  cave (re-routing some renderB slots).
* **Broken by:** the cave clobbered two registers on the renderB (slot <2196) path and
  hard-froze at the first player-phase command menu. The briefing screens used for
  validation are all-atlas, so they never exercised the broken path — a frame-identity
  "proof" over the wrong screen cleared a broken build.
* **Truth:** the atlas had ~66 zero-text-reference slots that could be reclaimed natively;
  placing new glyphs in native slots keeps the render path byte-identical (zero freeze risk).
  A "full atlas" usually still contains reclaimable dead slots — count *references*, not slots.
* **Guard:** validate render-path changes on every phase that uses each path (briefing AND
  command UI AND combat); prefer data-only mechanisms; reference-count before growing.

### A11. Fonts must be *uniform*, not just correct
Recurring cosmetic class: mixed 8 px renderB CJK next to 12 px atlas CJK ("floating"
glyphs, baseline steps, thin/bold mixing +31–39% ink, two Latin raster styles, one broken
hand-drawn 士 that rendered like 十). Truth: encode all CJK all-atlas; re-raster added glyphs
with the same font/size/alpha as the originals (the project settled on Noto Sans CJK SC,
size 13, alpha 110, after bit-matching the original style); keep Latin/digits in one style.
Guard: a uniformity detector (no label mixes atlas-CJK and renderB-CJK) + vision judging.

### A12. Identity records render renderA-DIRECT (the 吉恩十我 bug)
* **Believed:** re-encoding pilot/faction nameplate strings with renderB-shared tokens was
  fine because the info panels render them fine.
* **Broken by:** the combat/dialogue nameplate draws the SAME string renderA-DIRECT (every
  slot fetched from the 12×12 atlas), where renderB token numbers show unrelated atlas
  glyphs: 士官 rendered 十我.
* **Truth:** weaponless master-table records (utids ~610–944, pilots/factions/roles) and
  char-DB names must be encoded **≥2196-only** so both paths agree.
* **Guard:** static scan of those pools for any token <2196 that isn't shared-safe.
* **Recurred 2026-07:** 49 translated pilot names still carried the JP originals'
  renderB-meaning bytes/F-refs for their ASCII/paren tails (（弹尽）as one-byte 7D/7E,
  ＳＥＥＤ as F-ref 0x2C7, Ａ/Ｂ as JP-band e008/e009, DC/OZ/MD as one-byte+JP-band
  letters) — legal on the trampoline, but the `0x2BCA6`-patched speaker plate drew
  阿斯兰モー「「？ャ for 阿斯兰（SEED）.  There are ZERO shared-identical slots between
  the two fonts (computed), so "shared-safe" is the empty set: translated pilot names
  must be **pure ZH-band**.  All 49 re-encoded in place; gate `pool_trampoline_tokens`
  now enforces pilot names contain no one-byte/F-ref/JP-band glyphs at all.

### A13. A "width cap" patched on the wrong row family is dead arithmetic (the 编成 plate 80px clip)
* **Believed:** the 编成 detail-plate name clip at 80px was governed by the BG
  tile-copy clamp cave (0x11C222) — its `tile_x==8 && tile_y>=5 → cap 11` branch
  was shipped as "the 88px widen".
* **Why it seemed right:** the cave demonstrably clamps rows of the same map
  (0x0620F000), and 11 tiles = 88px matched the desired budget.
* **Disproven by:** A/B ROMs toggling the cap 10↔11 produced **byte-identical
  frames**; forcing 11 on all tile_x==8 rows garbled the STATS rows while the
  name row never changed.  OBJ-VRAM + OAM dumps then found the name pixels in
  engine-B **OBJ tiles 0x83..0x96** behind 32+32+16px sprites — the plate name
  is **OBJ text**, drawn by 0x2014470 with a width-in-tiles argument; the BG
  rows the clamp touches are the digit caches and stat labels.
* **Truth:** that plate's name budget lives in TWO code immediates that must
  stay EQUAL: the widget create (`movs r2,#N` @0x54BE0 — OAM sprite arrangement,
  tile block, commit chunking) and the redraw render (`movs r1,#N` @0x5487E —
  compose scratch stride).  Patching only one interleaves the tile columns
  (stride-10 scratch read as 11-tile rows).  N=11 → 88px; the sprite arranger
  and commit chunker generalize to 4+4+2+1 tiles (32+32+16+8px sprites) and the
  neighbouring OBJ tiles 0x97/0x98 are free on every state of that page.
* **Guard:** the two immediates are one logical patch (documented in both
  entries of code_patches.json); live A/B graft proof with a full-frame pixel
  diff (only the widened-name pixels may differ).  Meta-lesson: before widening
  any "field", locate the surface's ACTUAL render path (BG compose vs OBJ text)
  — a budget patch that survives an A/B pixel-diff unchanged is patching the
  wrong path.
* **Recurred 2026-07 (owner issue #19, the torn IF-battle nameplate):** the
  create/render split bit again ACROSS MODULES.  The speaker-plate widen
  (PR #4 family) patched the ADV dialogue module's create sites (0x2BED2
  width 10→14, 0x2BEF6 body base +0x14→+0x1C) together with the SHARED
  renderer 0x2BC74 — but the **IF-battle conversation module** (rival-pair
  barks from bank `1.bin` shown on the combat 演出 screen, e.g. 卡多/浦木宏
  in `_STG08A`) is a structural clone with its OWN create sites (0x652E4 /
  0x65308, same widget geometry, same global 0x0227D3C4) that still carried
  the JP 10-tile layout while the shared renderer composed and committed a
  14-tile surface: glyph half-rows landed in the wrong sprites (torn 卡多 /
  浦木宏 plates) and the body text shifted 8 tiles right.  When a shared
  renderer is widened, EVERY module that registers widgets for it is part of
  the same logical patch — enumerate them by finding all creators that store
  widget ids into the dialogue global (+0xc/+0xd), not by fixing the module
  you happened to be debugging.  Fix: mirror the two create-side immediates
  (2 bytes); gate `dialogue_nameplate_geometry` now pins BOTH modules'
  create sites against the renderer's surface (red-tested by reverting one
  byte).  The third widget-creating module (0x2A1AC, x=0x100 y=0x42,
  body 24 tiles @+0x1E) composes inline via 0x2013BE0 — NOT through the
  shared renderer — and stays JP-layout by design.  Deferred cosmetics for
  8-9-glyph names on the IF plate (WIN1 edge 0x65186/0x6544C, frame-row
  extension for 0x651AA/0x65404): the ADV frame wrapper 0x12D680 CANNOT be
  reused blindly — it extends fixed map rows 0x0601E440/0x480, while the
  battle scene's dialogue-system y ([0x0227D3C4+0x18], set per scene:
  literal 0x320 @0x648DC vs the ADV scene's runtime value) resolves its
  frame rows elsewhere; wiring it without a live map-address proof is the A7
  fixed-VRAM class.  Verify on a save that reaches an IF conversation before
  extending.

### A14. Narrow glyph advance = advance patch + ink-left-of-advance repaint, in ALL width models
* **Believed:** minting narrow-paren glyphs (half-cell ink) was enough to make
  （SEED）-style names look right; and later, that changing only the trampoline
  pen advance would be enough to make them fit.
* **Truth:** a glyph is narrow only if BOTH move together: (1) the trampoline
  advance-select cave (0x11A362 ext: slots 4156/4253 → 6px, 4222/4223/4214 →
  8px; the delta rides r7 across the blit because the drawer's epilogue pops
  the caller's r7) and (2) the cell's ink repainted LEFT of the new advance
  (`(` ink cols 8–10 → 2–4; S/E → cols 0–6; D → cols 0–7), or the ink of one
  glyph lands under its neighbour's box.  The blitter writes per-pixel nibbles
  (no opaque 12px rectangle), so overlapping boxes composite — but ink past the
  advance still looks wrong on screen.  And every WIDTH MODEL must adopt the
  same per-slot advances in the same commit — `run_static` gates, the render
  oracle, and the 攻略.html JS — or the preview, the gates and the live game
  disagree about what fits (the owner-reported html≠game class).
* **Guard:** `TRAMPOLINE_SLOT_ADVANCE` exists in exactly three mirrored homes
  (oracle / run_static / build_guide JS) all documented against the cave entry;
  bank-surface oracle-vs-live stroke IoU 1.0 verified on the 阿斯兰(SEED) plate.

### A15. Separate sequential char-tile banks by worst-case reservation (the 配属 third-ID corruption)
* **Looked like:** another issue-2 row-wrap defect: unit icons were correct, while
  ID/weapon/ability text in the lower panel showed wrong or missing glyph parts.
* **Disproven by:** an oracle-vs-live sweep of all 24 配属 slots found 266 text
  draws; 244 were pixel-exact.  The only systematic failures were 22 nonblank
  third ID-command titles at `(x=24,y=160)`.  Their expected lower strokes were
  present at only 52.8–91.5%; weapon, unit, pilot, the first two ID titles and
  every right ability name were exact.
* **Truth:** this is the same broad VRAM-aliasing family as issue #2, but not its
  13-tile plot-row wrap.  The three left titles allocate sequentially from char
  tile `0x263`; their registered worst case is 3 rows × 2 tile rows × 12 tiles,
  ending at `0x2AA`.  The JP right-ability bank literal was `0x29F`, inside that
  reservation.  Ability rendering then overwrote tiles `0x29F..0x2A2`, exactly
  the later glyph bottoms of title 3.  Move the ability bank to the first safe
  tile `0x2AB`; its own 72-tile maximum ends at `0x2F2`, below `0x300`.
* **Guard:** `assignment_id_tile_partition` pins both JP anchor literals, the ZH
  non-overlap boundary and the `0x300` upper limit.  The py-desmume live test uses
  the committed `assignment_slot1_newgame_plus.sav` and normal
  title→Continue→slot-1→BackStage→编成→配属 flow (no savestate/RAM mutation),
  visits all 24 slots and compares every plain panel draw with the independent
  pixel oracle.  A row carrying a standalone `0x01` layout control is recorded
  but not oracle-compared; the fixed build passes all 265 comparable rows.

### A16. A clamp widen scoped by row parity alone hit the sibling page (the 战场情报 ID garble)
* **Believed:** the in-battle 情報 SPECIAL description truncation (威力450以下的光束属性
  射击无效 → 威力450以下的) lived in fns `0x20376d0`/`0x20377d0`, so the fix raised their
  maxGlyphs 9→26 and widened the `0x11C1FC` engine-A copy clamp for ALL odd tile-rows
  (0x50→0xD0); the SPECIAL page verified clean live and shipped.
* **Broken by:** an owner screenshot of the SAME 情報 flow one page down — the ID COMMAND
  page's three 80px panels showed paved/half-erased detail glyphs.  The page dispatch at
  `0x39162` proves `0x20377d0`+`0x20376d0` are the ID COMMAND page (case 3), NOT the
  SPECIAL renderer (`0x2038360`, whose description drawer `0x2037ad4` already passes
  maxGlyphs 0x1a natively) — the raises were dead for SPECIAL and the cave widen was the
  entire fix AND the entire regression: a py-desmume exec-hook trace of the cave showed
  BOTH pages copy on odd rows ≥`0xa`, differing only in destination tile column —
  SPECIAL descriptions at col 3 (x=24, one 208px box), ID pages as FULL-STRIP compose
  copies at col 1 spanning all three panels, which the widen let bleed 208px of stale
  compose content across the panel seams.
* **Truth:** on shared copy paths, row parity does not identify a surface; scope clamp
  changes by the full destination signature (here: col 3 AND odd row → 0xD0, everything
  else → the original 0x50), and bound the change by two proven states (fix==pre for
  col≠3, fix==shipped-widen for col==3 — verified by logging the cave's r0 decisions
  per copy on all three ROMs).
* **Guard:** gate `engine_a_clamp_scope` pins the whole clamp chain byte-exact
  (hooks `0x1359A`/`0x134AE`, the `0x1B3F0C` dispatch, the `0x11C1FC` cave — red-tested
  in both failure directions: descoped widen AND lost widen); the behavior itself is
  proven live by `test/live/test_battle_info_pages.py`, which plays New Game → deploy →
  情報, logs every cave decision on the ID and SPECIAL pages and applies the scope law
  as the verdict (v1.3 fails the ID rows + shows paving ink; the pre-widen build fails
  the SPECIAL rows; the fixed build passes both).  This closed the audit gap that let
  the v1.3 widen ship through 44/44 gates: `code_image_parity` whitelists the whole
  cave band, so cave SEMANTICS need their own pin + live oracle.
* **Procedure:** before shipping ANY shared-cave scope change, drive EVERY page of the
  affected screen family live (情報 root/SPECIAL/STATUS/ID COM) — the py-desmume
  `register_exec` trace of (dest VRAM, col, row, width→decision) per page costs minutes
  and names the discriminator for free.

---

## B. Data growth, pointers, alignment

### B1. "_STG stage files are size-locked" (the founding myth of the dialogue work)
* **Believed:** growing any stage-file dialogue block hangs the game — for dozens of
  iterations every translation was crammed into the original byte budget (compressed,
  clipped, unnatural phrasing).
* **Why it seemed right:** an early probe inserted bytes mid-file and the stage black-screened.
* **Disproven by:** re-analysis showed the probe had shifted the file's contents **without
  relocating its ~2,700 absolute internal pointers** (stage files are compiled for the fixed
  buffer `0x0232C800`). Growing a block AND relocating every pointer ≥ the insertion point
  boots, renders two full lines, and passes the freeze grind.
* **Truth:** stage files grow freely up to the load-buffer cap (0x13800 = 79,872 B; keep a
  ~1 KB margin). See `STAGE_FORMAT.md` for the relocation rules.
* **Guard:** the grow machinery validates every pointer resolves in-range and every
  non-grown block decodes byte-identically; per-file size cap enforced at build time.

### B2. A `0x15` byte inside a pointer operand is not a dialogue block (two disasters)
* **Believed:** every `0x15 … 00 00` byte pattern in a stage file / arm9 cluster is a
  dialogue block that can be re-encoded.
* **Broken by (twice):**
  1. In stage files, event-script CALL/GOTO operands whose 4-byte absolute target contains
     `0x15` (e.g. `0x023315xx` addresses) were misread as block starts; re-encoding "the
     block" overwrote the operand and the following event code → in-range control-flow
     divergence → mid-stage hangs and a hallucinated speakerless line ("我只是个……装置")
     where a real cutscene belonged. 25 of 101 stage files were corrupted this way.
  2. In the arm9-embedded event table, jump-table entries `13 <ABS ptr>` with pointers in the
     `0x__15__` range (2nd byte 0x15) got Chinese text baked over them — 23 corrupted jump
     pointers; the game read ZH glyph bytes as a jump target (`0x19E915B4`) → data abort →
     **black screen at the ending cutscene** (the New-Game+ 特别演习 unlock).
* **Truth:** display blocks must be identified by **CFG reachability of the 0x15 opcode**
  (walking the script from the scene-entry table through GOTO/CALL/CGOTO), never by a linear
  byte scan. A real dialogue payload never contains `13 <valid arm9 ptr>`.
* **Guard:** script-pointer integrity (every `13 <ptr>` valid in JP must remain valid) +
  CFG-isomorphism vs. JP for every stage file + bakers that skip any 0x15 inside a pointer.

### B3. "Static-clean" ≠ "hang-free": in-range CFG divergence
* **Believed:** a freeze gate that flags out-of-buffer jump targets catches dialogue-bake
  corruption.
* **Broken by:** a whole corruption class stays **in-range**: a dropped/mangled event CALL
  doesn't produce a wild pointer, it produces a silent logic hang (the game waits forever).
  Gap- and reachability-models both read 0 while the ship froze mid-stage.
* **Truth:** the only robust oracle is **control-flow-graph isomorphism against the
  Japanese original** (NOP/skip-tolerant lockstep from the shared scene entries; the JP ROM
  is hang-free, so isomorphic ⇒ hang-free by construction).
* **Guard:** CFG-iso check across all 101 stage files on every build.

### B4. The unaligned-`ldr` rotation crash (why 4-byte alignment is load-bearing)
* **Believed:** after growth, valid & correctly relocated pointers ⇒ the stage loads.
* **Broken by:** a **+2-byte** dialogue grow shifted one stage's name table (header slot 8)
  to an address ≡2 (mod 4). ARMv5 does **not fault** an unaligned 32-bit `ldr` — it reads the
  *aligned* word and **rotates** it: the engine read `0xCC0A0000 ROR 16 = 0x0000CC0A` as a
  string pointer → data abort → black screen entering one stage from a New-Game+ save. All
  static pointers were valid; RAM content was correct; the corruption existed only inside
  the rotated load — invisible to every static pointer check and "transient" to postmortem
  RAM dumps.
* **Truth:** every header table the engine reads with 32-bit `ldr`s (header slots
  0x4/0x8/0x10/0x14/0x18) must stay 4-byte aligned; the JP original aligns all of them in
  101/101 files (the dialogue section, slot 0xC, is byte-accessed and exempt). Fix = insert
  `(-off)&3` pad bytes before a misaligned table and relocate pointers.
* **Guard:** a header-alignment gate over all five slots × all 101 files, every build.

### B5. Padding non-final segments with 0x00 collapses the segment count
* **Believed:** zero-padding a shortened segment is the natural filler.
* **Broken by:** in a `15 [seg1] 00 [seg2] 00 00` block, a shortened NON-final segment padded
  with 0x00 abuts the separator → a premature `00 00` = block terminator → the block loses
  its later segments (and combat-safety diffs light up).
* **Truth:** pad non-final segments with **0x09 ('…', benign ellipsis)**; only final segments
  pad with 0x00. (Corollary: visible mid-text 0x09 padding renders as stray "…" — the final
  pipeline reflows text so padding only ever lands at block ends; see B6.)
* **Guard:** encoder rule + block-integrity check (original terminator must survive).

### B6. Respecting the JP segment layout produced "artificial line breaks"
* **Believed:** keeping the exact Japanese segment/page structure (and padding the slack)
  was the safest re-encode.
* **Broken by:** Chinese lines broke mid-word where the JP happened to page, and pad bytes
  rendered as stray ellipses — the owner called it "artificial line breaks + …".
* **Truth:** reflow to the box geometry (18 glyphs/line × 2 lines), fill-then-wrap; collapse
  surplus JP pages via an explicit allowlist; never end a line on an opening bracket (the
  dialogue VM treats `0x0C 『` at a page start as a *choice* marker and drops the second
  line — a one-character reflow bug that truncated real dialogue).
* **Guard:** reflow validator (≤18 cells × ≤2 lines, no orphan opener, no mid-segment pad).

### B7. Cut-in bank growth and the "whole-file expansion" freeze
* **Believed (early):** the combat cut-in bank (`1dc.bin`) must never be touched — edits
  froze combat, so it was blacklisted ("deliver quotes via the arm9 pool only").
* **Why it seemed right:** the freeze reproduced 100% and reverting only `1dc.bin` cured it.
* **Truth (found later):** the cut-in codec performs **whole-file macro expansion**: it keeps
  consuming input until it has produced the *expected expanded size*. Replacing compact
  dictionary macro refs with literal tokens SHRANK the expanded size, so the codec over-read
  past the file end and computed a wild memcpy source (`ldrh` from `0x1F1BF8D0`) → abort.
  It was never "editing 1dc" that froze; it was shrinking its expansion. The durable rules:
  keep the whole-file expansion deficit non-positive (expanded-ZH ≥ expanded-JP is the safe
  direction), and grow records via the arm9 offset table (943 entries @ `0x16EEA8`, sentinel
  @ `0x16FD60`, size ref @ `0x16C444`).
* **Guard:** expansion accounting in the cut-in baker + the live freeze grind (drive an
  actual combat cut-in).
* **Corollary:** the sibling banks `1db`/`1da`/`1df` were *never* freeze-dangerous — they use
  the normal decoder. And the "in-place only, no relocation table" beliefs about them were
  wrong too: `1db`/`1da`/`1df`/`1e0`/parts all have arm9 offset tables (see the address map).

### B8. Cut-in page-break bytes are context-sensitive (0x03 vs 0x04)
* **Believed:** the first byte(s) of each cut-in line could be uniformly overwritten with a
  filler prefix; separately, that all `0x03` bytes could be normalized.
* **Broken by:** captions that should page ("line1 ▼ line2") instead concatenated and
  overflowed — a baker had overwritten the continuation line's leading `0x03` (commit-page
  control) with `0x04`; meanwhile mid-segment `0x03/0x04/0x05` are the *punctuation glyphs*
  。·？, so a blanket flip corrupts text.
* **Truth:** in control position (first byte of a content segment) `0x03` = "commit line &
  advance page"; the same values mid-segment are text. Break edits must be position-aware
  and validated against the JP structure line-INDEX-wise (records re-segment between JP/ZH).
* **Guard:** cut-in decode-back comparison: text and breaks reproduce; only intended flips.

### B9. The one-word off-by-4 that froze one stage (relocation must be field-exact)
* **Believed:** name-pointer relocation was mechanically safe — write the new pointer where
  the old name pointer was.
* **Broken by:** ONE record had its pointer written to `+0x0C` (a packed-data field) instead
  of `+0x08` (the name field) in a stride-0x10 table. The clobbered data steered a mid-stage
  demo into a NULL sub-resource → data abort. It shipped for dozens of builds; static gates
  passed because the bytes *are* a valid pointer inside an allow-listed table. Two prior
  root-cause attempts (stage-file corruption; heap exhaustion) were plausible and wrong —
  only live differential bisection (JP-data baseline vs. full-ZH, forward-restore /
  reverse-revert halves, at matched emulator load) isolated the word (`0xE70C4`).
* **Truth:** relocation writes must be validated by *value semantics*: a pointer into the
  relocated string pool may only replace a word that pointed into the original's string/data
  section. A global scan under that invariant found exactly the one corruption.
* **Guard:** relocation-pointer integrity check (JP-anchored, 0 false positives on 5,435
  legit relocations); never ship a freeze fix that wasn't reproduced and bisected live.

### B10. Cave allocators and terminators (off-by-one)
* **Believed:** "free space starts where the previous string ends".
* **Broken by:** a relocation window began exactly ON the NUL terminator of the last
  ID-ability name — the first relocated blob overwrote the terminator, and the ability list
  rendered with an appended stray effect string.
* **Truth:** free-space windows start *after* the terminator; allocators skip non-zero bytes.
* **Guard:** allocator asserts the byte before its window is 0x00 and re-decodes neighbors.

### B11. Rebuilds silently drop or regress content
Three related process failures during full-pipeline rebuilds:
1. A "rebuild from the JP skeleton" repair transplanted only ZH it could positionally anchor
   and **fell back to JP** where pairing broke — 590 translated blocks reverted to Japanese,
   unnoticed (freeze gates check control flow, not language; global coverage ratios don't
   move for a few hundred blocks).
2. A full re-bake dropped a whole content pass (the stage-opening "preamble tail" lines) —
   93 first-lines regressed to JP.
3. A block-detection predicate that required a 2-byte token (`any(b >= 0xE0)`) to call
   something "real dialogue" silently excluded **all-kana** lines (「ならば、私は……」,
   「ン」…) from the corpus, the bakers *and* the gates — 380 reachable blocks shipped
   Japanese in every build until an owner screenshot.
* **Truth/Guard:** an untranslated-text gate that walks every CFG-reachable display block
  and flags JP-only tokens (kana / JP-dict macro refs / JP-band kanji), with an audited
  intentional-JP allowlist (credits, layout-locked tutorial headers, onomatopoeia). Its
  false-positive design point: the ~150 hanzi identical in JP+ZH ship as narrow single-byte
  codes 0x86..0xDB and are correct ZH — never flag them. Rebuild pipelines must re-encode
  from the corpus (semantic source), not from positional anchoring alone.

### B12. Growth manifests must be complete, and manifest *collisions* lie
* A combat-safety checker that validates "every changed dialogue block is a known grow"
  needs the **complete** grow list (built from the previous ship's manifest + the new edits);
  building it from a CFG walk missed text-matched grown blocks → false "malformed" flags.
* Separately: when several historical manifests are searched by file-length match, a stale
  entry with the same length shadows the correct one and produces hundreds of phantom
  failures. When a gate flags one stage file with a huge defect count, suspect manifest
  collision before ROM corruption.
* **Guard (new pipeline):** single-pass build with ONE authoritative grow manifest.

### B13. B6 recurred on the library bios via applier precedence ("kept-committed" shadowing)
* **Believed:** the bio banks were fully modernized — the phase-2 fleet had produced flat
  full retranslations for every record and the applier reflowed them to the measured
  18/17-cell × 6-line box.
* **Broken by:** an owner screenshot of a half-empty profile box (9/6/12/10/11/2-cell
  lines).  109 early "phase-1" records — in-place byte-fitted edits that kept the JP
  originals' break positions AND abridged wording — were silently preserved by the
  applier's *kept-committed-over-staged* precedence branch, shadowing their finished
  phase-2 retranslations for months.
* **Truth:** B6 all over again (JP layout structure ≠ ZH layout structure), plus a
  process trap: when a migration produces a better source for records that already have
  a committed form, "keep committed" defaults leave the WORST records permanently frozen.
* **Guard:** gate `bio_line_geometry` (18/17-cell, ≤6 lines/page, greedy-fill ratchet —
  a premature-break record cannot re-ship regardless of which pipeline produced it).

---

## C. Memory safety (heap, caves, buffers)

### C1. "Dead" RAM is stage-dependent — the heap grows into it
* **Believed:** high-RAM bands that dump as zero/garbage ("dead banks") are free real estate
  for relocated strings. A band was even "measured safe" on a save file.
* **Broken by:** deep into the campaign the runtime work-buffer/heap (floor `0x0232C800`)
  had grown past `0x0233F400`, engulfing bands that were empty early on. ID-ability names
  turned to heap garbage at stage 5 (`、的1D` instead of 领导); ID-command detail text
  garbled only in combat; a briefing-blob placement froze only with a 97-character roster.
  All static-clean: the *bytes* in ROM are fine, the *destination address* is the bug.
* **Truth:** the only always-safe homes for display strings are (a) the resident arm9 image
  (< `0x021B6DB8`), and (b) the dedicated autoload pool ABOVE arena-hi (`0x023E7000`).
  Between the stage buffer and arena-lo there is **no** roster-independent safe band.
* **Guard:** a pointer-destination audit: no name/summary/detail pointer may resolve into
  `[heap floor, arena-lo)`; plus live checks on the owner's *latest/biggest* save, not an
  early one.

### C2. When one pool is fixed, audit every sibling pool
The summary-string pool was relocated out of the volatile band and verified — the detail
pool with the identical overflow shipped broken and was found by owner playtest. The
fix-time rule: scan **all** records' name/summary/detail pointers for `≥ heap floor`, not
just the pool you were looking at.

### C3. The heap-collision hypothesis that wasn't
When one stage froze, the relocated font at `0x023027A0` "sitting inside the old heap zone"
was the leading theory (it explained everything except the facts). Live tests that restored
the JP arena bounds (larger heap) and even moved arena-hi still froze; grafting JP data with
ZH banks was clean. The real cause was B9's off-by-4. **Plausibility is not causality —
bisect.**

### C4. Sequential NUL-walked stores cannot be re-framed
An autoloaded render-store block (ID-ability names + stat labels) is consumed by walking
NUL-separated strings in order. An edit that shrank one string and padded with extra NULs
shifted the walk for every later string → all downstream plates garbled. In-place edits of
such stores must preserve exact framing (same NUL count/positions); relocation is the only
way to change lengths.

### C5. Readers differ in which pointer ranges they accept
The affinity/nameplate name readers accept only the resident name arena (`0x0218xxxx`) and
render blank on high-RAM pointers; the ID-command/master-table readers happily follow
autoload-pool pointers. Before relocating any string, identify its **reader** and prove the
destination range is accepted (live probe with one record first).

### C6. "Dead" donor bytes may be live data — audit references before paving (the D4 bug)
* **Believed:** the dead SJIS dev-string band (`0x1B3E22..0x1B6DA0`) was pure never-read
  donor space; caves could pave any of it.
* **Broken by:** every in-battle focus-plate HP readout silently rendered EMPTY for
  multiple releases.  The paved span contained the OBJ-text **number-format strings**
  `"D4"`/`"/D4"` (`0x1B3E90/0x1B3E94`) — referenced from live literal pools
  (`0x23640/0x23648/0x240A8/0x240B0`) and passed to the OBJ-text drawer `0x02013BE0`
  by the plate composers.  With the format string paved by cave code, the parser found
  no directive and drew nothing: no garble, no crash, just missing digits — invisible
  to every parity/pointer gate (the cave region was allow-listed).
* **Truth:** "dead" is a *reference-count* property, not a *content* property.  Debug
  strings for a compiled-out printf are dead; format strings mixed into the same band
  are not.  Isolating the cause took a full live component bisection (patches-only
  build still failed → single-entry builds → the cave body itself).
* **Guard:** gate `patch_literal_safety` — any patch that paves non-zero JP bytes must
  have zero un-retargeted JP-image literal references into its span (documented
  allowlist only for referencers proven dead, e.g. argument pools of the compiled-out
  debug printf `0x020A3ECC`).

### C7. Cave scratch state must live in the resident image, never in buffer bands
* **Believed:** a single scratch byte for a cave (last roster cursor row) could be
  parked at an arbitrary quiet RAM address (`0x0233F800`).
* **Broken by (latent):** `0x0233F800` is stage-buffer offset `0x13000`, and two grown
  stage files exceed `0x13000` — in `_STG98` that offset is a REACHABLE script position
  (the story-all-clear dialogue's press-A advance point).  Browse the roster (cave
  writes the row byte into the loaded script), enter the stage, finish the dialogue:
  row values 0x02/0x13/0x16 become a wild GOTO/CALL → data abort at the most
  celebratory moment of the game.  Static gates cannot see it (the ROM byte is clean;
  the corruption happens at runtime).
* **Truth:** the only safe homes for cave-written state are bytes the build itself
  owns inside the resident arm9 image (cave-body tails, the dead-string band verified
  per C6, existing cave-global precedent `0x18F47C/0x18F4CC`).
* **Guard:** gate `patch_literal_safety` forbids any patch literal targeting
  `[0x0232C800, 0x023489AC)` (stage + work buffer up to arena-lo).

### C8. Zero-valued LIVE tables — "all zeros in JP" does not mean free (the 暴击-vanish bug)
* **Believed:** a run of zero bytes in the resident arm9, not covered by any
  known table, was free space for relocated strings (the v1.2 `fleet-v12:idname`
  batch planted three ID-skill names at `0x19095D/0x190979/0x190999`).
* **Broken by:** the owner's hardest bug report — units hit by a critical (暴击)
  "just vanish" with no knockback and no effect popup.  A 20-agent clean-room
  fleet (10 ZH + 10 JP, headless instrumented emulator, deterministic replays)
  isolated it: the "free" zeros were the **battle-scene knock-anim geometry
  tables** — s16 flight vectors (`0x190930..0x190967`, consumer `0x0206A6E4`)
  and s8 shake deltas (`0x190968..0x1909A7`, consumers `0x02069D8C/DB8`) whose
  JP values are legitimately (0,0).  String bytes there became flight vectors
  that flung the crit-survivor's sprite **~20,000 px off-screen**; the hit
  counter, クリティカル/暴击 popup and damage number are sprite-anchored, so
  the whole reaction beat rendered off-screen.  Only crit-survivors spawn the
  flight object — hence "only on 暴击, only sometimes".  The unit record was
  never touched: purely presentation, invisible to every text/pointer gate.
* **Truth:** free space is a *reference/consumer* property, never a *content*
  property (C6's data twin).  A zero span is dead only if no consumer's index
  range can reach it.  Fix = relocate the strings into the declared free run
  (NUL-preserving — B10: the first attempt started ON the previous string's
  terminator and black-screened), restore the cells to JP zeros.
* **Guard:** gate `placement_span_safety` (no placement may intersect the known
  live-zero bands; zero-space placements must preserve the preceding
  terminator), plus a consumer-audit of every zero-space placement band.
* **Meta:** the isolation followed F5 verbatim — deterministic repro, 26
  one-variable graft replays (which *exonerated* the plausible suspects: the
  暴击 label, the name pool, every cart bank), RAM algebra matching the
  corrupted sprite position exactly, and a state-spliced fix proof.  The
  owner's insistence against two wrong "normal behavior" verdicts is what kept
  the investigation honest: **an owner report outranks a green gate wall.**

---

## D. Encoding, decoding & translation data

### D1. The decode map was built on homophones — translators translated garble
* **Believed:** the slot→character map recovered early in the project was accurate; odd JP
  in the corpus was "just OCR noise".
* **Broken by:** ~400+ kanji slots were mapped to same-*reading* homophones (状況→状狂,
  砂漠→砂縛, 女王蜂→女王泡…). Whole passages were translated from subtly-wrong Japanese,
  producing "compact/nonsense" Chinese the owner flagged repeatedly.
* **Truth:** the ROM's **glyph bitmaps are the ground truth** for what a slot means. The map
  was rebuilt by rendering every slot's 12×12 bitmap and verifying by eye, then all source
  text re-decoded and re-translated where it changed.
* **Guard:** decode maps carry bitmap-verified fix layers; JP-sanity passes (a Japanese
  tokenizer finding zero non-words) before translation waves.

### D2. Encoder slot desync bands
A JP-frozen-atlas fallback in one encoder had bands where `real[S] = map[S-3]` — a bark
picked the glyph three slots away (对空哨 showed 償). An exhaustive audit of 68,965 tokens
found exactly 2 such defects; both repointed. Guard: bark glyph-fidelity self-test.
Related: hand-typed token hex is banned — 闪 is `0xEA44` but a near-miss hand-hex `0xEA24`
is 属, a perfectly-valid wrong glyph no gate can flag; always derive tokens from the charmap.

### D3. Structural bytes may not appear inside encoded text
Two-byte tokens whose LOW byte is `0x00` or `0x15` inject phantom separators/block starts
into the byte-wise stage-file grammar. The encoder must reject such tokens (choose another
glyph/wording). This is also why block scanning must be **token-aware** (skip 2-byte tokens
when searching for `00 00` terminators — a token's low byte 0x00 is not a separator).

### D4. JP-keyed renames miss variant strings
Renaming a unit by exact JP match missed its transform/loadout variants (`／BWS`, `／下降`
…) — each is a separate master string with its own JP. Rename passes must sweep every
record whose JP *starts with* the renamed base (and its ZH siblings). Same family: a "fix"
keyed to an old charmap couldn't even decode strings containing late-added glyphs (W=4316)
— always match/encode via the *current* charmap.

### D5. Width budgets are pixel budgets (the freeze-adjacent one)
"Overflow" is rendered glyph *width*, not byte length — the sister-project post-mortem that
seeded this rule described whole lines vanishing and even crashes from over-wide text. Here:
ZH renders 12 px/glyph vs. JP's 8 px on many UI fields, so equal byte length can still be
50% wider. Field budgets that were measured and enforced: ID-command list summary **64 px**
(clip at the selection bracket x240), detail box ~76 px, box titles ≤6 hanzi, nameplates
7 glyphs, BackStage weapon-name field 80→104 px (after a 1-byte field-width patch), squad
carried-name field 6 glyphs (longer names clamped), bark text byte-locked AND
sub-line-framed. The general invariant: **rendered ZH width ≤ rendered JP width** per field
("ZH ≤ JP"), enforced by a width gate that expands dictionary macros before counting.

### D6. Terse-then-restore: over-compaction is a defect too
Early byte-budget pressure produced compacted translations (完全回避→完回避, dropped
clauses). Once growth existed, the owner explicitly asked for full natural phrasing — the
"walls" that forced terseness were mostly false (B1, B7, A1). Lesson: revisit every
compaction after a capacity breakthrough; keep the natural translation in the data files and
let the builder decide fit.

### D7. Intentional Japanese is a curated allowlist, not a shrug
Credits (Japanese studio/staff proper nouns), tutorial section headers (layout-locked,
dict-compressed byte budget), two scream onomatopoeia, a few UI micro-fragments, and the
squad sub-menu (custom compressed tile codec) stay Japanese **by decision**, recorded in an
allowlist the untranslated-text gate honors. Everything else must be ZH — "mostly done" is
not a state the gates accept.

### D8. Empty-segment separators are load-bearing (the NT对应机/NT对应机 bleed)
* **Believed:** when a re-encoded special-ability/defense record (`1df`/`1e0`) had fewer
  display lines than its slot, padding the freed tail with `0x00` was the natural filler.
* **Broken by:** the drawers (`0x02055AB4`/`0x02055BD8`) draw a FIXED 2/3 lines by
  scanning byte-wise for the k-th `00 03` stop **with no record-end check** — a record
  that ships fewer stops than the drawer draws lines makes line k render the NEXT
  record's first segment: duplicate "NT对应机/NT对应机" lines, and units showing
  abilities/defenses they do not have (79 records shipped this way).
* **Truth:** JP's trailing `00 03` pairs after the content are not padding — they are
  the *empty lines* the drawer will draw.  The JP stop topology (2 per 1df record,
  3 per 1e0 record) is part of the record contract, exactly like bark sub-line framing
  (§5 barks) and non-final `0x09` segment padding (B5).
* **Guard:** gate `effect_line_stops` (JP-anchored stop counts + the drawer's 26-glyph
  /208px line budget on every re-encoded line).

---

## E. Emulator & debugging tooling

### E1. Hardware watchpoints do not fire — instrument the emulator
gdb `watch` on both melonDS and DeSmuME never triggers (tested repeatedly; a watch +
continue sails past the write). Every "who writes this VRAM/RAM?" question was ultimately
answered by **patching the emulator**: env-gated write loggers in the VRAM write paths
(engine-A OBJ, engine-A BG, engine-B BG with window mode + SIGUSR1 bank dumps), RAM-window
watches, and abort-time register/memory dumps in the CPU's data-abort handler. Budget for a
custom emulator build early; it converts week-long mysteries into hour-long traces.

### E2. The gdb stub is fragile at the interesting moments
Single reads work; bulk/repeated operations wedge the stub, especially post-freeze (one
clean read per crash, then dead). Main-RAM mirrors crash it on write; engine-B BG VRAM and
I/O regs aren't readable at all; DTCM (`0x027Cxxxx` — where the render contexts live) is
invisible to main-RAM dumps. Workarounds that stuck: file-hook pokes/dumps compiled into the
emulator loop (see E3), savestate parsing for offline RAM/VRAM, and register-redirect
injection via the instrumented build.

### E3. Headless input: the Xvfb keyboard wall
Under Xvfb+fluxbox, melonDS (Qt) never receives synthetic keyboard events regardless of
focus tricks — but mouse/touch works. The robust solution was a tiny emulator patch polling
`/tmp` files each frame: input-mask injection, RAM dump, RAM poke. It doubles as the
RAM-inspection tool and sidesteps the flaky gdb stub. (Also: real-time key presses must be
HELD ~120 ms; instantaneous synthetic taps are dropped by the game loop.)

### E4. Instrumentation overhead can hide the bug (Heisenbug barks)
A garbled bark could not be traced under gdb or an in-RAM logging cave: per-instruction
overhead perturbed the RNG so a different bark fired every time. The fix was a separately
compiled emulator with zero-overhead native hooks (`#ifdef`-gated), letting the buggy path
run at full speed. Keep the stock binary byte-identical (hooks compiled out) so results
compare.

### E5. Savestates mask patches (four distinct ways)
1. A savestate restores the OLD arm9 image → arm9 fixes invisible (and a freeze-fix
   "verified" on a checkpoint was actually testing the unpatched code).
2. It restores RAM-resident data files (`1df`, stage buffer) → data fixes invisible.
3. It froze the menu coroutine state → navigation impossible from some states.
4. Its atlas is the atlas of the build that made it (see A2).
Rules: fresh-boot for any render/timing/code verification; if a savestate must be used,
splice the arm9/data delta into its RAM image; rebuild hub savestates after every ship.

### E6. Emulator-in-the-loop tests are load-sensitive
Real-time input + a frame-limited emulator means system load changes how many emulated
frames elapse per keypress: a freeze that reproduces 100% at ≥8 parallel instances may not
reproduce at 4. Freeze verification runs at fixed high load with an in-batch known-bad
control, and classifies a run "clean" only if it actually *reached* the scene (scene-reach
detection, e.g. screen-change metrics), never by timeout.

### E7. Misc tooling traps that cost real time
* `cp` onto a ship *symlink* overwrites the symlink's target (a historical reference build
  was destroyed this way); promote by re-pointing symlinks.
* Blind whole-ROM byte remaps corrupt binary files (a remap once broke 1,489 files including
  the sound archive — audio gates existed for a reason); scope every transform to its store.
* Parallel agents must own scoped X displays (`pkill -f "Xvfb :N "` with the trailing space);
  a blanket pkill killed a sibling's run.
* Reused /tmp capture dirs served stale screenshots to later judges — timestamp and isolate.
* Two vision judges disagreeing about a glyph is settled by decoding ROM bytes, not voting.
* An owner bug report may be from a stale build — verify the build hash before chasing.

### E8. The owner's play pattern is the test matrix
The owner plays **without cheats** (partial roster, so list-row ≠ record-id — a mapping bug
class the full-roster dev savestate can never show) and **deep into the campaign** (heap
grown; New-Game+ flags set). Several bugs reproduced *only* on owner saves. Keep owner saves
as fixtures; test roster-sensitive UI no-cheat; and for state diffing, same-session saves
(N vs N+1 free-battles) differ in ~24 bytes while unrelated saves differ in thousands —
diff the former.

---

## F. Process & verification philosophy

### F1. The tests themselves were the biggest bug
At one point every gate was green while the owner kept finding "out of place" text. Audit
findings: no check measured glyph *position*; the width model priced ZH at 8 px when it
renders 12 px; whole tables were exempt; the "VLM gate" never actually consumed the vision
verdicts; live checks ran on stale savestates. The rewrite established: position-aware
pixel checks against on-screen controls; full-screen crops for the judge (tight crops hide
overflow into neighbors — the judge literally couldn't see the bug class being reported);
enforced machine-readable verdicts; fresh-boot reproduce-then-gate (an unreached screen is
FAIL, not SKIP). **A test that cannot fail on the reported defect is worse than no test.**

### F2. Every gate must prove teeth (self-test RED→GREEN)
Each new gate ships with a self-test that runs it against a known-bad historical build
(expect RED with the exact defect) and the fixed build (expect GREEN). Gates without a
demonstrated failure case repeatedly turned out to test nothing.

### F3. JP-anchored invariants give 0-false-positive gates
The strongest checks compare the candidate to the *Japanese original* as the oracle:
bark gap bytes are 0x00 in JP ⇒ any non-zero gap byte is corruption; JP script pointers
valid ⇒ must stay valid; JP aligns header tables ⇒ candidate must; a relocated pointer must
replace a JP string-section pointer; CFG must be isomorphic to JP. Absolute thresholds and
heuristics false-positive; JP-anchoring doesn't.

### F4. Ratchets beat snapshots
Coverage (how much JP remains) is enforced as a floor that only ever rises, captured only
from builds that themselves passed everything. One-off "looks translated" checks regress
silently; ratchets cannot.

### F5. Live differential bisection is the freeze endgame
Every hard freeze that mattered was closed the same way: reproduce on the owner's save →
swap components between JP and ZH (files, arm9, data halves) at matched load → bisect to
the byte(s). Static theories (two at once, both wrong — see C3) shipped "fixes" that fixed
nothing. Never ship a freeze fix that wasn't live-reproduced before and live-cleared after.

### F6. Fix the source, not just the artifact
Every repair baker (false blocks, alignment, framing strays) also hardened the *upstream*
producer so a from-scratch rebuild doesn't regenerate the defect. In the clean pipeline this
becomes: builders enforce the invariants at generation time; gates re-verify them at build
time.

### F7. Verify claims against the data before "fixing" them
Several owner reports were correct observations of *correct* behavior: 机炮 was the weapon
column (accurate translation), the 3-line condition box is engine-composed per command
enums (`rec+0x0E`/`+0x23`, faithful to JP), a "missing" cut-in was an intentional
placeholder, and one "wrong pilot name" was the JP dummy 欠番 shown only under cheats.
Root-cause first; the correct resolution is sometimes documentation, not bytes. Conversely,
"renders fine for me" is not a rebuttal to an owner screenshot — three of those turned out
to be savestate masking (E5).

### F8. Text sources of truth
Effect text must be derived from the game's own coefficient table (the efftab @ `0xEBC25`),
not from an intermediate JSON — one such file mislabeled 反応 (reaction, flat value) as
防御 (+%), a stat that doesn't exist. Same for nameplates: resolve through the char-DB on
the CURRENT ROM (an intermediate corpus carried stale speaker labels from an older build).

### F9. Scoping decisions are owner decisions
"Won't-fix" (squad sub-menu codec, session-number chrome, start screen, stage-name banners)
and "keep as-is" calls (PLANT stays Latin, 穆 for Mu, keeping Japanese rank suffixes 大佐…)
came from the owner, are recorded, and are excluded from gates via allowlists — so the
gates stay 100%-green *and* honest.

### F10. A width gate that prices ZH at the JP advance measures nothing (F1 recurrence)
* **Believed:** `gate_glyph_width` protected pilot names — it compared ZH vs JP widths.
* **Broken by:** owner screenshots of 艾帕·西纳普斯 clipped on the 编成 plate and
  多蒙（明镜止水）'s `）` printed over the battle-plate LV badge.  The gate priced BOTH
  sides at 8 px/glyph (translated names are pure ZH-band = **12 px**) and expanded JP
  F-refs via the WRONG dictionary (`0x12D770`; pilot names macro through the system
  dict `0x1444B4`) — it had degenerated to a cell-count compare, so 96–108 px names
  passed against 80–84 px fields.  Two of the four surfaces (roster list 88 px, 编成
  plate 80 px window) had never been measured at all.
* **Truth:** width checks must use the true per-surface advance and the true dictionary,
  against MEASURED field budgets (TEXT_SYSTEM §6), not against a same-unit JP compare.
* **Guard:** `gate_glyph_width` now decodes render slots (12 px ZH-band / 8 px renderB,
  DICT_SYS expansion) and enforces the 84 px pilot-name cap; the budgets are documented
  in TEXT_SYSTEM §6.  Meta-lesson = F1: re-audit what a green gate actually measures.

---

## G. The full-audit campaign (charset wall, pass-2 fleet, release hardening)

This section distills everything the audit campaign learned, so the repo stays
self-explanatory even without the (optional, removable) `audit/` tree. The durable
rules live here and in AGENTS.md / TEXT_SYSTEM.md; the terminology rulings are
transcribed in TRANSLATION_GUIDE §2b.

### G1. "Unencodable" was a demand-side illusion — audit the demand before minting
* **Believed:** ~31 simplified hanzi could not be encoded (the "charset wall"), so
  translations had to be reworded around them.
* **Why it seemed right:** the atlas' ZH band looked full; a raw scan of every zh
  field genuinely failed to encode those chars.
* **Broken by:** the coverage ratchet dropping 0.01% after two "promotions", and the
  demand list containing chars that only appeared in kana-bearing warmup blocks.
* **Truth:** a third of the "demand" was **notation, not text** — glyph-priming
  warmup rows, ptr-less never-built master records, stale decode annotations. Real
  demand was 15 chars, all satisfiable by minting. Rephrase mitigations needed: 0.
* **Guard:** `charset_wall.py` demand rules (kana-bearing zh = annotation; ptr-less
  master entries never build); `encode_sweep.py` proves 0 unencodable per surface.

### G2. Dual-use slots poison the coverage denominator
* **Believed:** if a JP-band slot's bitmap IS the needed hanzi (従/償), registering
  it as encodable is free.
* **Truth:** those tokens also appear in real JP dialogue; a shared registration
  makes the JP-source scan count original Japanese as "translated", silently
  deflating the ratchet baseline. Same-form chars (九僚厄奏婚屋幅廉昂殴遂) must be
  MINTED into token-free cells, never dual-registered; the JP-side coverage scan
  counts minted slots as kanji (`minted_as_zh=False`).

### G3. Every name pool is a trampoline surface (the 多佛炮→多恩炮 class)
* **Believed:** label arenas (unit/weapon/pilot/ability nameplates, ID names and
  summaries) were renderA-direct, so JP-band mints and one-byte codes were safe.
* **Broken by:** shipped garbles — 多佛炮 rendering 多恩炮 (JP-band mint), a
  nameplate `D` (one-byte 0x11) rendering き, β at JP slot 345 rendering garbage,
  ・ as one-byte 0x04 rendering renderB '3'.
* **Truth:** every string reached through a table `ptr` renders on the 8×16
  trampoline: only ZH-band 2-byte tokens plus the record's ORIGINAL renderB bytes
  are safe. Minting for name surfaces must target ZH-band cells (reclaim
  zero-demand or junk kana/latin cells; both scans token-free).
* **Guard:** `slot_of(surface="bank")` refuses JP-band registrations; gate
  `pool_trampoline_tokens` = zero JP-band 2-byte tokens in any referenced pool
  string; `bank_onebyte_regression` ratchets one-byte inventory.

### G4. Trampoline glyph geometry: anchor at penY+3, embed atlas digits
12×12 atlas cells top-anchored in the 16px renderB line box float 3px above the
JP ink-bottom (row 14). The cave patch biases penY by +3 around the 12×12 renderer
call (restoring after — penY is persistent ctx). The reverse case is the sunk-digit
class: runtime/embedded renderB digits next to atlas glyphs sit low — per-level
label strings must embed ZH-band digit tokens (NT等级N, 指挥N; there is NO runtime
digit append — the digits are baked in the five 指揮レベル strings). The offline
oracle mirrors the +3 anchor so screenshots and oracle renders agree by construction.

The dialogue speaker plate is the renderA-direct counterpart of the same geometry:
it does NOT pass through the trampoline cave (its `0x2013C00` call is not detoured),
so the `0x2BCA6` style fix alone left the plate's own penY at 0 and 12px name ink
rode the plate's top border, 3px above the JP 8×16 rows. Patch the plate's OWN
anchor at `0x2BCE8` (`movs r2,#3`; 11+3=14 < 16 cannot clip) — never move the
shared dialogue-body renderer, whose call sites are separate (PR #4).

### G5. Growing pooled strings: ledger-mediated relocation, three arenas
Order: ui-bank heap-safe gaps → resident-cave zero runs → ledger-vacated spans
(old homes of relocated strings; provably dead = no table ptr and no arm9 word
references them). Everything goes through `data/zh/placements/relocation_ledger.json`;
allocation marks must be written ONLY on committed success (a reject after alloc
must not leak the span), and re-runs must be idempotent — the one double-allocation
(人类的梦想 twice) came from exactly that leak and scrambled two records.
Exact-span in-place rewrites are legal when the old string already fills the span
with no in-record NUL (the terminator is the next record's framing).

### G6. Annotations must be decoded per-surface
The `zh` fields of `data/zh/units.json` / `characters.json` mirror pool bytes.
Syncing them with the STAGE decoder wrote 来 for 0xD9 (！) and メ for 0x7C (…)
across ~150 records — self-inflicted "garble" that then misled audits. Any
annotation sync must use the trampoline decoder (renderB for one-byte, ZH-band
for 2-byte) for pool surfaces.
`render-vs-annotation` diffing (trampoline-decode every referenced string, compare
to its zh) is the cheap detector for the whole class — keep it zero.

### G7. Audit fleets work; reconciliation without evidence does not
The scaled pattern that held up: chunk manifests with flags (short/leftover/
changed) + a shared brief with binding style rules + websearch-armed subagents
producing schema-fixed reports with per-fix evidence + validating appliers with
stale-old idempotency + adjudication of cross-chunk conflicts by recorded owner
rules (mainland-wiki form wins; 特殊演習 and 特別演習 are two different menu
items — direction decided per-line by the JP). What failed: a "reconciler" agent
compacting names without evidence (光说！/珍贵/灭) — over-compaction is a defect
(D6); names may only be condensed into natural phrases sharing the quote's terms,
名台词 and effect text are NEVER truncated.

### G8. Release hygiene
`README.md`'s expected sha1s (main + pad32m) are part of the deliverable — every
data/code change re-syncs them (a stale hash makes every user think their build
failed). Byte-reproducibility is proven by a clean-copy rebuild
(rsync minus .git/.venv → build → identical sha1), not by rebuilding in place.
The build path must never depend on `audit/`; one-time migrations rewrite sources
and retire.

### G9. Narrowing ASCII/parens in ZH names via JP-token reuse (never mint)
Chinese names re-typed ASCII/parenthesis/dash runs as WIDE ZH-band atlas tokens
(`基拉（SEED）`, `EX-S高达`), 12px per glyph — visibly bigger than the JP UI font's
8px-advance renderB forms. Fix at source: reuse the **same record's JP/HEAD original
bytes** for the identical character run (`（SEED）` = one-byte `0x7d` + the system-dict
macro `f2c7` "SEED" + `0x7e`). Reusing the JP original's own tokens can NEVER garble
(renders exactly the glyph the JP game drew) and stays gate-legal:
`pool_trampoline_tokens` already exempts non-`zh_minted` (pristine-JP) slots, and
one-byte parens are proven bytes so they only need a `bank_onebyte_regression`
baseline refresh (recording the JP-proven `0x7d`/`0x7e`, never removing anything).
Rules that keep it safe: (1) match whole target-runs of consecutive whole tokens
(a `SEED` dict macro is one token) between ZH and JP by decoded char, normalising
fullwidth↔halfwidth (the ZH atlas paren decodes `(`, the reused JP paren `（` — same
glyph class); (2) NEVER narrow digits/`+`/`%` (G4 sunk-digit class — keep them
ZH-band atlas); (3) never let a name fully revert to its JP bytes (that reclassifies
a translated name as JP and trips `unit_weapon_names`/`id_command_names`); (4) build
the JP↔ZH bytemap from an all-WIDE ZH ROM, else a partially-narrowed build pollutes
the keys. Verify with 32/32 gates + coverage (0) + a live before/after oracle sheet
(the E-band letter identities lean on the VLM-identified `kind: "ident"` entries in
`data/renderb_charset.json` — eyeball them).

### G10. Candidate free-space lists must be edge-audited at cell granularity ("zero-in-JP ≠ free", third and fourth instances)
The BtlS_Crea relocation fleet (10 clean-room agents, 2026-07-19,
/tmp/reloc_fleet) unanimously approved moving the 49 attract-demo-table
squatters, but broke the CANDIDATE LIST itself in four places — all on span
EDGES, none in the interiors the applied plan actually used:
* **B1 lo `0x183DBD` / C1 lo `0x18409D` are off-by-one INTO live bark-map
  cells**: each is byte 1 (the consumed LOW half — the sole accessor
  `0x020646F4` u16-truncates every u32 cell, so bytes 2–3 are dead but bytes
  0–1 are live) of the k=22 cell of a real, voiced, attract-demo-deployed
  character (cid 6 ev22 rank 0x9A; cid 14 = アナベル・ガトー ev22 rank 0xF2;
  map = u32[471×23] @ `0x02183B3C`, key `cid*23+event`).  A string planted at
  the listed span start corrupts a real bark rank.  Safe starts are byte 2 of
  the edge cell (B1 → `0x183DBE+`, C1 → `0x1840A0`) — and a placement must
  never write the trailing word (next row's k0 cell) either.
* **A2's listed edges `0x1945B3` / `0x194852` each trespass on live
  develop-grid rows** (row 180 col-1 high byte = the Qubeley family anchor;
  row 201 col 0): the dead space is ONLY the id-hole interior rows 181..200 =
  `[0x1945D0,0x194850)`.  Three v1.1-era strings shipped ON row 180's cells
  2..15 (col 12 read back as a plausible utid 0x38) — the develop-UI readers
  consume cols 12/13/14 unconditionally once a Qubeley-family unit is owned;
  rescued 2026-07-19 into 欠番 bark-row core tails.
Truth (extends C8): a candidate span is not an interval, it is a claim about
an OWNING TABLE — adjudicate per-row/per-cell against the owner's accessor
(u16 masks, row strides, id domains), and audit BOTH edges: every error the
fleet found sat exactly on a span boundary.  "All zeros in JP" plus "inside a
documented free run" is still not dead — knock-anim (C8), BtlS_Crea, dev-row
180 and the bark-map low halves are four instances of the same class.
Guards: `RESIDENT_LIVE_ZERO_BANDS` now fences the FULL BtlS_Crea table
`[0x190BFC,0x19175C)` and the develop grid outside its id-hole
(`[0x192F30,0x1945D0)`, `[0x194850,0x194E90)`); ledger hygiene: 50 superseded
2026-07-12 rows (strings re-homed without updating them) are now explicitly
RETIRED — a stale ledger row is a future double-allocation, mark rows
retired/moved in the same commit that re-homes their strings.

### G11. "Dead" bark rows are a LIVENESS claim about the whole cid domain — enumerate every writer (fifth and sixth instances)
The freezeproof write-map audit (agent W, 2026-07-19, /tmp/freezeproof/W)
classified EVERY byte the build writes into the resident image and found the
"row is dead-by-evidence" prior for bark id-map holes broken twice more, both
missed by every earlier audit because the cid enumeration was incomplete:
* **row 511 (コンスコン)** — `_STG01`'s setup records deploy cid 511 (the max
  cid any stage record produces; the bark map physically extends to row 571 =
  `0x190870`, so row 511 is addressable), yet 12 v1.1-era pilot-arena strings
  (杰刚/吉翁号/西斯库德/浮游炮/吉姆…) squatted its cells: the first bark event
  from Conscon's slot would have fed string bytes to the rank fetch;
* **row 238** — the char-DB record is literally 欠番 (dummy name, voiceset 0),
  but the **97-pair roster init map @ `0x192DA8` assigns cid 238 to roster
  slot 91** (loop `0x0200F108` → the same `strh 0x0200F744` that feeds the
  bark row cache).  "欠番 ⇒ never deployable" is FALSE — 欠番 status must be
  cross-checked against every cid source, per cid.
Truth: a bark-map row is dead iff its cid appears in NO writer of the
per-char state cid field — and that domain has SIX enumerable sources (all
11 BL callers of `0x0200F744` traced): stage setup records, per-stage
header[0x14] roster-availability tables, the roster init map, the story-swap
table @ `0x118F58`, the BtlS_Crea demo table, and event-VM native 0x80
(`set_pilot_cid`, zero static call sites; residual ceiling = bounded bark
garble, not a freeze: u16 rank keeps the offtab read in mapped RAM, len is
u8-truncated, the record parser is header-checked).  Both rows were vacated
into the 欠番 pair-row core `[0x187AD7,0x187B6D)` (rows 177/178 — verified
absent from ALL six sources), 4 reference-free orphan duplicates deleted
(0 referrers by data-scan AND whole-image aligned+unaligned word scan).
Guard: gate `bark_map_row_liveness` — image-level, recomputes the cid domain
from the candidate ROM's own stage files + arm9 tables every run, forbids
(a) any low-half change to a JP-nonzero cell, (b) any squat on cols 1..22 of
a deployable-cid row, (c) any diff in knock/BtlS_Crea/dev-grid-live/roster
bands; red-tested for all four classes in `--self-test`.  The audit's write
map + per-byte classification (writemap.json / adjudication.json, zero
UNKNOWN bytes) is the reusable no-freeze evidence base.

### G12. CODE caves obey the same liveness law as strings — and "JP zeros" in patch space bit us too (seventh instance)
Agent B's live sweep (2026-07-19, /tmp/freezeproof/B) found 23 utids
(610–630, 670–671) hard-aborting (data abort → `pc=FFFF0108`, the v1.1 freeze
phenotype) on the hangar 処分 **detail-panel render**, ZH-only.  Root cause
(agent W): four render-fix **code caves** were parked on JP-zero runs at
`0x1B35F8/0x1B3620/0x1B3664/0x1B3B00` — which are the **予備-family sentinel
rows of the LIVE unit resource-id table** `u32[256 fams × 7] @0x021B1FA8`
(reader `0x02011E48`: `id = base + 7*fam(utid)`; value → resource-pack word
via `0x0201F678`, whose `ldr u32[0x0216BCD4 + val*4]` aborts on a wild id).
Rendering any fam-204..210/250..252 unit's detail panel read cave CODE BYTES
as resource ids.  The old `patch_literal_safety` paved-donor check only
audited JP-NONZERO donor spans — zero-space caves were assumed free, the
exact C8/G10/G11 fallacy in patch space.  Truth: cave donors need the same
owner-table adjudication as string placements; reachability ≠ obtainability
(utids 610–630 are 予備 with dev-row 0 and zero acquisition references, yet
their PANEL RENDER is one save-poke away and the failure is a hard freeze).
Fix: caves block-relocated into the dead in-image atlas (`0x11C330..0x11C444`;
internal pc-relative branches preserved by keeping block layout, the one
external BL re-encoded, 3 veneer BLs + 1 literal re-aimed); the table is
byte-exact JP again and gate-pinned (`bark_map_row_liveness` rule 5,
red-tested).  Empirical close: B's 23 minimal repros re-run green on the
fixed build; positive control (old ROM) still aborts.
Related finding, same subsystem (B FAIL#2, adjudicated NOT-A-ZH-BUG): the
BtlS_Crea demo loader has a FIXED 0x10000-byte resource-pool budget (cursor
base ptr @`0x021B1E1C`, budget literal @file `0x7EAF8`); its overflow check
feeds only the compiled-out printf `0x020A3ECC` and then keeps going with
wild pack pointers (`0x0200E79A/AA` aborts).  Hand-poked 6-unit×2-team demo
records of unusual units exceed it **on BOTH ROMs**: W's controlled twins
(byte-identical poked pairs, same script, same battery, same runner) abort
batch-for-batch on JP exactly as on ZH — B's original "JP twin clean"
verdicts were VACUOUS because those JP runs never left the title screen
within their 14,000-frame window (screenshot-proven), a lesson about
adjudication controls: **a clean control run must be shown to REACH the
surface under test**.  Retail bound: the demo table is gate-pinned byte-exact
JP; the natural table's pool usage is < 0x8000 (half budget — budget-bisect
probe, demo confirmed on-screen) on the ZH build; real stages use the stage
loader (98/101 proven live by agent S).  Treat the 0x10000 budget as a hard
constraint if the demo table is ever legitimately edited.

### G13. A replaced range is not a pointer-free zone — baked operands freeze ONE layout into the data (BUG-1, the _STG20A replay soft-lock)
Agent S's all-stages sweep (2026-07-19, /tmp/freezeproof/S) found `_STG20A`
input-deadlocking on an EMPTY dialogue box at the 4th advance — only when the
session is the save's own current/replayed card (`rec046.byte1:=0x2D` warp);
forward-context clean on both ROMs, JP control plays through byte-identical
inputs.  Root cause: stage edits replace whole byte ranges, and the builder's
pointer relocation EXCLUDED replaced ranges ("the replacement carries its own
already-correct values").  But some edits are MIXED — dialogue payload plus
live event bytecode: `_STG20A`'s `0x1780+181` edit contains two `13`-operand
forks (`FORK file+0xD3A4/file+0xD24C`, the dialogue-pump/wait sub-VMs of the
replay branch) whose absolute operands the zh_hex had BAKED at the layout of
their authoring day (shift +0xFAC).  The PLANT re-encode grew two earlier
blocks (+23 B): the real targets moved to +0xFC3, the baked operands didn't,
the forks entered their routines 23 bytes short (one hit an immediate exit,
one spawned a wait-loop VM at file+0xE15D), and the main VM parked forever on
the `05 ff ff` wait at file+0xE171 — CPU healthy, buttons dead.  Why S-wave
context only: the fork pair lives in bytecode that only the replay-branch
scene program executes; and why the gates were blind: the operands sit inside
a LEXICAL `15..00 00` span, so the CFG walkers skip them as display payload.
The corpus audit found the same class already SHIPPED at −4 in `_STG10B`
(two fork pairs, latent since v1.1 — the S sweep's contexts never executed
that branch).  Fixes at source: (1) `apply_edits` now relocates operand
windows inside/straddling replaced ranges and REFUSES in-buffer baked values
(zh_hex must carry the JP operand bytes; migration
`audit/tools/migrate_stage_operands.py` normalized 1,102 windows across 49
stages — built output changed ONLY at the 6 defective operands); (2) new
image-level gate `stage_operand_relocation` re-derives every operand's
landing site from the JP 12-byte signature (shift-corrected, mask-aware) on
every candidate ROM — it flags exactly the 6 stale operands on the old build
and is red-tested by re-baking one 0x17 short.  The general law: **data may
carry only translation decisions, never layout** — any absolute value inside
a replacement is a bug waiting for the first upstream size change ("works
today" just means "the layouts happen to agree today").
