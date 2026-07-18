# TEXT_SYSTEM — byte-level text format, fonts, dictionaries, budgets

Everything visible in this game (dialogue, barks, cut-ins, names, labels) flows through one
custom text encoding and two fixed-cell fonts. This document is the codec spec plus the
rendering model. Addresses referenced here are catalogued in `ROM_STRUCTURE.md`.

---

## 1. The byte grammar (one codec for the whole game)

A text stream is a byte sequence. Decode rule per byte `B`:

| pattern | meaning |
|---|---|
| `B < 0xE0` (1 byte) | **single-byte code** — indexes the low 224 glyph slots directly. Sub-ranges: `0x00` terminator/pad (a standalone `0x00` inside a block = soft page-break "▼"; `00 00` = end of block), `0x01` control/blank cell (also the leading filler in some layout-locked blocks), `0x02 =、`, `0x03 =。`, `0x04 =・/·`, `0x05 =？` (values are per the render path — see §3 pitfalls), `0x09 = '…'`, `0x15` is the dialogue-block *opcode* in stage scripts (never emit it inside text), `0x15..0x85` kana, `0x86..0xDB` ~150 narrow "renderB-shared" hanzi that are identical in JP and ZH (我来方事先…), plus digits/Latin/punctuation |
| `0xE0 ≤ hi < 0xF0` + `lo` (2 bytes) | **glyph token** — atlas slot = `((hi<<8)|lo) − 0xDF20` (equivalently `((hi−0xE0)<<8 | lo) + 224`). Covers slots 224..4319 |
| `0xF0 ≤ hi` + `lo` (2 bytes) | **dictionary macro reference (F-ref)** — entry index = `((hi<<8)|lo) − 0xF000` into the active dictionary; the decoder recurses into the entry's payload (depth-capped in tools; the JP data nests) |

Notes:
* Token/terminator scanning must be **token-aware**: a 2-byte token whose low byte is `0x00`
  or `0x15` would otherwise be misread as a separator / block start. The shipped encoder
  simply **refuses** to emit any token with low byte `0x00`/`0x15` (pick different wording).
* The JP text is heavily F-ref-compressed. The translation is (by final policy) **direct
  atlas-coded** — no ZH text uses F-refs — which simplifies safety but changes expansion
  accounting for the one consumer that cares (the cut-in codec, §5).

## 2. Dictionaries (compression macros, not fonts)

Two dictionaries, both `u16[4080] offsets || payload` structures inside arm9; selected by
the table at `0x0216B868` (`[+0]` primary, `[+4]` alt):

| dict | RAM | role |
|---|---|---|
| **primary** | `0x021444B4` | dialogue/system compression macros. ⚠ combat-critical: clobbering it freezes; treat as read-only |
| **alt** | `0x0212D770` | the canonical *name* store — pilot/unit/faction name macros (e.g. one entry = アムロ). Entries are referenced from data files and code; safe to re-encode entry payloads in place (byte-budgeted) |

A macro expands to a glyph sequence (possibly nested). Width and expansion computations must
expand F-refs recursively or JP lines get mis-measured (see §6).

## 3. Fonts and render paths

Two fixed-cell fonts; **rendered width = glyph count × advance** (no proportional metrics):

| font | location | cell | advance | consumers |
|---|---|---|---|---|
| **renderA** — CJK atlas | RAM `0x023027A0` (relocated autoload payload) | 12×12 px, 2bpp, **36 B/slot** | 12 px | dialogue, cut-ins, nameplates, all translated CJK |
| **renderB** — UI font | in-arm9 `0x02133F14` | 8×16 px, **32 B/slot** | 8 px | JP UI labels, digits, kana, narrow shared hanzi |

**The atlas**: 4,320 slots × 36 B = 155,520 B (`0x25F80`). Slots **0–2195** are the original
Japanese glyphs (the JP in-image atlas had exactly 2,196); slots **2196–4319** are the added
Chinese glyphs. The charmap (char ↔ slot, in the build data) is the single source of truth
for encoding; slot bitmaps in the ROM are the single source of truth for decoding (see
`LESSONS_LEARNED.md` D1). Added glyphs are rasterized from **WenQuanYi Bitmap Song 12px**
(`/usr/share/fonts/X11/misc/wenquanyi_9pt.pcf`, monochrome, 11x11 design box at the cell
origin) and carry the original Japanese raster grammar: stroke pixels = value 1, plus a
bottom-right L-shadow (value 2) equal to the stroke dilated one pixel right/down/down-right
(rule verified on 2194/2194 original glyphs). The atlas regeneration procedure (WQY raster + shadow grammar; historically `audit/tools/regen_atlas_v6.py`) regenerates
the atlas deterministically; the `glyph_style_uniformity` static gate enforces the grammar
on every slot. Keep that recipe for any new glyph or the weight/baseline mismatch is
visible on screen.

**Render dispatch**: the drawer (`0x02013220`) picks renderA or renderB per string context
(ctx+0x64 bit0). The renderB glyph fetch routes through the **trampoline** at `0x0211A2A0`
(installed in the dead space where the in-image atlas used to live):

```
if slot >= 0x894 (2196): render via renderA from the 12x12 atlas   ; ZH glyphs
else:                    glyph = renderB_font + slot*32            ; original JP UI font
```

This one hook is why Chinese renders on every "renderB-path" screen. Three practical
consequences:
1. **renderA-DIRECT surfaces** (combat/dialogue nameplates; identity records in the master
   table) fetch EVERY slot from the atlas — text there must be encoded ≥2196-only, or
   renderB-shared token numbers render unrelated atlas glyphs.
2. **Slot spaces differ between the fonts** (renderA slot 4 = '·', renderB slot 4 = digit
   '3'): never move glyphs between fonts by slot number; encode punctuation with the byte
   values the original JP used on that surface.
3. renderA has **no bounds check** — an out-of-range slot reads past the atlas and renders
   pixel noise ("sparkle"); the charmap consistency gate exists for this.

**The critical literals** (all catalogued in ROM_STRUCTURE.md): atlas base `0x1315C`
(→ `0x023027A0`), renderB font base `0x1321C` (must stay `0x02133F14`), decoder branch
`0x1322C` (must stay `11 d1`).

**The definitive per-surface table** (established empirically — on-screen garble
reproduction + shipped-byte analysis; enforced by `bank_onebyte_regression` and
`offline_coverage` gates, modeled by `test/render_oracle.py`):

| surface | path | one-byte codes |
|---|---|---|
| stage dialogue (`data/zh/stages/*`) | renderA-direct | safe (atlas low slots) |
| barks (`data/zh/files/barks/*`) | renderA-direct | safe |
| cut-ins (`1dc.bin`) | renderA-direct | safe |
| library/hangar banks (`324/c4b/31e/b6e/b6f`) | renderA-direct | safe |
| briefing blobs (`data/zh/placements/briefing_blobs.json`) | renderA-direct | safe (0x09 '…' padding is canonical) |
| idcmd detail pool (`data/zh/placements/idcmd_detail_pool.json`) | renderA-direct | safe |
| event text blocks | script/pointer records | n/a (text fields are annotations) |
| battle effect banks (`1da/1db/1df/1e0`) | **trampoline** | forbidden unless in the record's JP span (zh_hex required by the build) |
| resident caves / ui names / battle name pool / post dict labels | **trampoline** | forbidden unless in the record's HEAD payload |
| **char-DB pilot names** (any store) | trampoline **and** renderA-direct (the `0x2BCA6` speaker-plate patch) | forbidden entirely: translated pilot names must be pure ZH-band (≥2196, no one-bytes/F-refs/JP-band) or the plate draws different glyphs than the roster (LESSONS A12; gate `pool_trampoline_tokens`) |

The renderB 8×16 charset (all 224 one-byte slots, kana ordering, the +4/+5 敵-skip
alignment law, punctuation tail) is catalogued in `data/renderb_charset.json` — decode
trampoline bytes with THAT table, never with the renderA charmap (the JSON `text`
fields of trampoline banks are renderA NOTATION, not what the player sees).

## 4. Block/segment grammar (stage dialogue)

A stage-file dialogue block is:

```
15 [seg1] 00 [seg2] 00 ... [segN] 00 00
```

* `0x15` = show-dialogue opcode; segments are 0x00-separated; `00 00` terminates.
* Segment prefix bytes from `{0x01, 0x15}` are control (blank cells / continuation); the
  rest is text. A standalone `0x00` renders the ▼ wait-for-input page break.
* Box geometry: **18 glyph cells × 2 lines** (12 px glyphs). Reflow fills line 1 then wraps;
  never end a line on an opening bracket (`0x0C 『` at page start is parsed as a *choice*
  marker — a reflow that orphaned 『 dropped the whole second line).
* **Padding rules**: pad a shortened NON-final segment with `0x09` ('…', keeps the separator
  structure intact); only final segments pad with `0x00`. Zero-padding a non-final segment
  creates a premature `00 00` terminator and collapses the block's segment count.
* Choice blocks (`0x0C..0x0D` wrapped, ≥2 option segments — 8 in the game) and blocks that
  interleave with combat scripts must not be rewritten structurally (see `STAGE_FORMAT.md`).
* Tutorial section headers are leading-`0x01` layout-locked blocks (kept JP by decision).

## 5. Surface-specific grammars

### Barks (battle voice) — files `0/1/1dd/1de/c4f.bin`
A "voice set" chains sub-lines:

```
[SL1 text][00 03 00 0X][00 pad ...][05 VV WW 00 06 SS TT][SL2 text] ... [00 03 00 01]
```

Each sub-line is introduced by a `05 .. 00 06 ..` sub-header; the gap between a sub-line's
terminator and the next sub-header is **all-0x00 in the original**. The live renderer does
not skip stray bytes there: a single non-zero gap byte makes it fold the next header's
`0x05` into a bogus 2-byte token and the following sub-line renders as JP-atlas garble.
Edits are size-locked in place; decode/verify per sub-line (header → its `00 03 00 0X`),
because a record's byte budget can span several sub-lines.

### Cut-ins (combat famous-line banners) — file `1dc.bin`
Record grammar: `00 05<id> 00 [line1] 00 03 [line2] 00 03 00 01`.
* `0x03` in *control position* (first byte of a content segment) = commit line & advance
  page; mid-segment `0x03/0x04/0x05` are the punctuation 。·？ — position matters.
* Head byte `0x01` at a record start is read as a "no quote" sentinel → blank banner; use
  `0x04` as the head filler.
* Records are addressed via the arm9 offset table (943 × u32 @ `0x16EEA8`); growing the file
  requires rewriting that table + the sentinel @ `0x16FD60` + the byte-size ref @ `0x16C444`.
* **Expansion accounting**: the cut-in consumer expands the whole file through the macro
  layer and keeps consuming input until it reaches the expected expanded size. Because ZH is
  literal-coded (no F-refs), naive replacement *shrinks* the expanded size and the codec
  over-reads past the file → wild memcpy → freeze. Keep expanded(ZH) ≥ expanded(JP)
  (a negative "deficit"); the shipped bank grew +4,508 B and satisfies this by construction.
* The cut-in banner box is the standard 2-line dialogue geometry.

### Names & labels (arm9 pools)
Strings are NUL-terminated, pointer-referenced from tables (char-DB, master table,
ID-command table, offset tables — see the address map). In-place edits must fit the original
byte budget (NUL-pad the tail); longer text is relocated to a proven-safe pool and the
pointer/offset repointed. Mind three things: the reader's accepted pointer ranges
(LESSONS C5), sequential NUL-walked stores that cannot be re-framed (LESSONS C4), and
alignment where tables are involved.

## 6. Width budgets (the "too-wide ZH freezes/blanks" rule)

Overflow is about rendered **pixel width**, not byte count. ZH hanzi advance 12 px where JP
UI text advanced 8 px, so same-byte-length text can be 50% wider. The enforced invariant on
every re-encoded string: **rendered ZH width ≤ rendered JP width it replaces** (the JP fit
its field; staying ≤ it cannot overflow). Width counting expands dictionary macros
recursively (JP lines are macro-compressed; counting a macro as one cell undercounts JP and
false-flags the ZH).

Measured field budgets (px unless noted):

| surface | budget |
|---|---|
| dialogue / cut-in box | 18 cells × 2 lines (12 px cells) |
| ID-command LIST summary | **64 px** (column x176, clipped by the selection bracket at x240) — ≈5 hanzi |
| ID-command detail box | ~76 px (≈6 hanzi + margin) |
| ID-command box titles | ≤6 hanzi (≤72 px) |
| speaker nameplate | 7 glyphs hard |
| **pilot names (char-DB), all surfaces** | **≤84 px (7 cells) hard cap; ≤72 px (6 cells) fully clean.** The binding fields: battle focus/formation plates (name pen x=51, fixed LV badge x=132 → ~81 px; an exactly-7-cell name touches the badge by 3 px — accepted residual), the 编成 detail-plate window (88 px, see below), the roster list (name x=8, LV badge x=96 → 88 px), the speaker plate (84 px). Gate: `glyph_width` |
| 编成 detail-plate name window | **88 px** (11 tiles; widened from the JP-design 80 px — the JP max name was exactly 80 px — via the clamp-cave budget) |
| BackStage weapon-name field | 104 px (was 80 px; widened by a 1-byte field patch, scoped to names ≥14 cells natural width) |
| unit-list carried-name field | 6 glyphs (longer names clamp; trailing cells blanked) |
| special ability/defense box (1df/1e0) | lines pre-broken by `00 03` stops (2 lines/record in 1df, 3 in 1e0 — trailing stops are the drawer's empty lines, NEVER pad them away, see LESSONS D8); ≤26 glyphs / 208 px per line (drawer buffer + JP max) |
| library bio box (324/c4b) | **18 cells/line** (17 on `{00}·{01}` indented quote continuations) × **6 lines/page**; explicit break grammar (`00 04` continuation, `00 04 01` indent, `00 07` page, `00 01` end); fill-then-wrap, no no-start punct at a line head, no line ending on an opener. Gate: `bio_line_geometry` |
| parts/caption viewer | fixed 5-line window; ≥3 trailing blank lines needed or the next record bleeds in |
| barks | byte-length-locked in place + sub-line framing (§5) |

## 7. Encoding conventions for the Chinese text

These are hard constraints from the glyph inventory plus owner style decisions (see
`TRANSLATION_GUIDE.md` for the full style guide):

* Pause = `、` — the font has **no `，`**. New text must never contain `，`.
* No tilde (`～`/`〜` absent); compensate with `……`.
* Ellipsis = `……` in pairs (single `…` only as structural pad, §4).
* Full-width `！？`; interpunct `·` (U+00B7); Japanese `・` is normalized to `·`.
* `▼` (encoded as the standalone `0x00` page break) is the wait-for-input marker.
* All CJK all-atlas (≥2196); shared narrow hanzi (0x86..0xDB) acceptable only where the JP
  surface already used them (they are correct ZH, 8 px).
* No spaces in the all-atlas scheme (no space glyph); a `0x01` blank cell is the visible-gap
  device where needed.
* Line breaks: fill-then-wrap at 18 cells; don't orphan opening brackets.
