# BUILD_GUIDE — how the translated ROM is built, step by step

This is the practical companion to `ROM_STRUCTURE.md` (layout + address map),
`TEXT_SYSTEM.md` (encoding), and `STAGE_FORMAT.md` (stage files). It explains how to run
the build, what each phase does and in what order, how to make a change safely, and — for
the worst case — how to rebuild the whole thing from first principles without this repo's
code, using only the documented formats and addresses.

---

## 1. Building with this repo (the normal path)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python build/build.py "0098 - SD Gundam G Generation DS (Japan).nds" sd-gundam-g-generation-zh.nds
```

* Input: the Japanese cartridge dump, sha1 `12443b91297a57bcd2ace8da989c26ae635a79fd`.
* Output: `sd-gundam-g-generation-zh.nds`, 30,361,448 B, sha1
  `6907e944fd699e33d04700ae3fb4b4f44461fe0c`; with `--pad32m` also the 32 MiB 0xFF-padded
  image (sha1 `81d285e1dc2c06c13b157db71e5a9cd952983b62`). (`data/manifest.json` is the
  authoritative record of all three hashes.)
* The build is a **single deterministic pass** (~5 s). Every component is verified against
  `data/manifest.json`; the final ROM hash is verified last. `--skip-verify` downgrades
  hash mismatches to warnings — the mode you use while editing translations.

Then verify:

```bash
.venv/bin/python test/run_static.py sd-gundam-g-generation-zh.nds          # static gates (50)
.venv/bin/python test/live/test_boot_render.py sd-gundam-g-generation-zh.nds  # emulator boot
```

The JP side of every translation key is browsable in `data/jp/` — the committed
dump of `build/extract_data_from_game.py` (the single extraction path; the
`extraction_fresh` gate keeps it honest, `build/reconcile_extraction.py` proves
the mapping complete in both directions).

## 2. What the build does, in order

`build/build.py` orchestrates five component modules (all in `utils/`):

### Phase 1 — code binary (`utils/arm9_layout.py`)

Starting from the Japanese arm9 image (1,797,560 B):

1. **Validate input** — ModuleParams words, list layout, expected size (§3 of
   `ROM_STRUCTURE.md`). A wrong or already-built image is rejected.
2. **Bake semantic tables** (from `data/zh/units.json`, `zh/characters.json`,
   `zh/ui.json`; geometry from `utils/extract/layout.py`):
   unit names → master table `0xB94BC`; weapon names → the 6 sub-records per unit;
   pilot names → character DB `0xDCF18`; ID-command names/summaries/details → table
   `0xEC994` + detail offset table `0xF9048`; ability strings + their 583 pointer sites;
   parts-name offsets `0x16B474`; UI label literal sites; the text-macro dictionary at
   `0x12D770` (offset repoints + re-encoded entries).
3. **Write string pools** (`data/zh/placements/` + `zh/event_text.json`): in-place pools (battle names, detail pool,
   menu descriptors, resident caves), the 1,297 event/briefing text blocks in
   `0x1985A4..0x1AD745`, and the two relocated banks that later become autoload payloads.
4. **Apply code patches** (`data/patches/code_patches.json`, 39 entries, then
   `data/patches/raw_regions.json`, currently empty): render-path trampolines + caves,
   decoder hooks, one-byte fixes, gameplay threshold tweaks. Every patch asserts its
   recorded `old_hex` before writing — a shifted base fails loudly. Both files are
   audited by the same gates (`patch_literal_safety` scans raw-region writes for paved
   live bytes and forbidden literals exactly like cave bodies; `placement_span_safety`
   includes them in the writer-overlap scan; the `hp_format_liveness` invariant pins the
   ISSUE-6 "D4"/"/D4" HP format strings and their four consumer literals byte-exact JP).
5. **Append the autoload tail**: 12×12 glyph atlas (`data/font/atlas12.bin`, `0x25F80` B)
   + pool A (`0x2028C` B) + pool B (`0x98FC` B) + the new 5-entry autoload list; patch
   ModuleParams (`0xB0C/0xB10`), the renderer atlas pointer (`0x1315C` → `0x023027A0`)
   and the heap floor (`0xA48F8` → `0x02348A00`). Mechanism: `ROM_STRUCTURE.md` §3.
6. **Self-check** — sha1 vs `data/manifest.json`.

Order matters only within arm9 in one respect: pools must be written before pointer
tables are verified against them, and the appended tail goes last (it defines the final
image size). The builder handles this internally.

### Phase 2 — stage dialogue (`utils/stage_text.py`)

For each of the 101 `_STG*.bin` files (data in `data/zh/stages/<name>.json`):

1. **Splice** all byte-range edits (translated `0x15 … 00 00` dialogue blocks, plus
   `script`-kind ranges) and inserts (alignment padding) in original-file coordinates.
2. **Relocate pointers**: every genuine absolute pointer (value in
   `[0x0232C800, base+len)`, classification rules in `STAGE_FORMAT.md`) whose target sits
   at/after an insertion point is bumped by the accumulated growth.
3. **Assert invariants**: built size, fit in the `0x13800` RAM buffer, all pointers
   resolve in-file, header tables at `0x04/0x08/0x10/0x14/0x18` stay 4-byte aligned
   (misalignment = rotated `ldr` on ARMv5 = black screen at stage load).

### Phase 3 — misc data files (`utils/data_files.py`)

Twenty-one files, four data layouts (schemas in `DATA_FORMATS.md`): bark banks (in-place
re-encoded runs), the grown cut-in quote bank (whole-file rebuild; its offset table lives
in arm9 and was already written in phase 1 — the two must agree), fixed-size name tables,
and raw-tile graphics repaints (asserted against original bytes).

### Phase 4 — gallery resources (`utils/gallery_titles.py`)

Repack the three coupled metadata/string-bank pairs (`43f/440`, `322/323`, `b38/b39`).
The writer joins character and unit names by runtime roster ID, rewrites only owned offset
words, preserves all other metadata and fixed tails, reads back every record, and enforces
the mode0/renderB-trampoline glyph identity plus 6/8/12px width budgets.

### Phase 5 — container assembly (`utils/rom.py` + ndspy)

Replace `rom.arm9` and the 128 changed NitroFS files **by index** in the loaded Japanese
ROM, `rom.save()`, verify final sha1. ndspy reproduces the container byte-identically
(header CRC16 recomputed automatically; the stale secure-area CRC is correct behaviour —
see `ROM_STRUCTURE.md` §1). The 12-byte nitrocode footer rides along as `arm9PostData`.

## 3. Changing a translation (the edit loop)

The canonical build inputs are **encoded payloads** (`zh_hex` / `payload_hex`); the
`zh`/`zh_text` fields are the human-readable view. That means:

* **Text-table entries that re-encode at build time** (`data/zh/files/*` banks): edit the `zh` text, rebuild with `--skip-verify`, run `test/run_static.py`.
  The text must encode (all chars must exist in the glyph atlas — the codec raises on
  unknown chars) and fit the recorded size budget (builders assert).
* **Entries with canonical hex** (`data/zh/stages/*.json` dialogue edits,
  `data/zh/placements/*`): re-encode the new text with `utils/text_codec.encode()` (stage
  dialogue convention: prefer the Chinese-region atlas slot for any char that has one —
  see `TEXT_SYSTEM.md`), keep length ≤ the recorded replacement length (pad with trailing
  `0x00`), keep the block terminator clean, and update both `zh_text` and `zh_hex`.
  LENGTH changes move you off the verified layout: growth shifts pointers everywhere
  after the edit, so `script`-kind edits' embedded pre-relocated pointers would desync.
  Prefer equal-length rewrites; for real growth, regenerate the stage's edit list.
* **Width rule**: translated text must render no wider than the Japanese it replaces
  (12 px/glyph on the dialogue path, 8 px on the UI path, macros expanded). The static
  suite enforces this; violating it clips or freezes UI (`LESSONS_LEARNED.md`).
* **New glyphs**: if a character isn't in the atlas, add a 12×12 bitmap to a free slot ≥
  4,320 would require growing the atlas (size is pinned by the autoload list — the build
  computes it) and mapping the char in `data/charmap.json` (`two_byte_zh`). Check first
  whether the char already exists (`slot_chars_extra` names late-added slots).

After edits, expect `component hash mismatch` warnings (that's the point); when the new
state is intended, refresh that component's hash in `data/manifest.json` and re-run the
full test ladder (`test/README.md`).

## 4. Rebuilding from first principles (no streamlined code)

If you ever have to reproduce this work from scratch — or do the same for another game —
this is the distilled recipe. Addresses below are this game's (see `ROM_STRUCTURE.md` §4
for the full map); the *method* generalizes.

1. **Split the container.** `ndspy` (or `ndstool -x`) → header, arm9(+footer), arm7,
   FNT/FAT, files. Establish the byte-identity baseline: load + save must round-trip.
2. **Find the text.** Dump printable-looking byte runs; correlate with on-screen text in
   an emulator. Here: dialogue lives in `_STG*.bin` as `0x15 … 00 00` blocks; names live
   in arm9 pools pointed at by fixed-stride tables (units `0xB94BC`/0xD8, pilots
   `0xDCF18`/0x48, ID commands `0xEC994`/0x24); barks/cut-ins/encyclopedia in flat
   `.bin` files with u16/u32 offset tables (often in arm9, e.g. cut-ins `0x16EEA8`).
3. **Crack the encoding.** This game: 1-byte codes < 0xE0 (code == glyph slot),
   2-byte `0xE0xx` glyph tokens (slot = token − 0xDF20), `0xF0xx` dictionary macros
   (u16-offset-table dicts at `0x12D770` (dialogue/UI) and `0x1444B4` (frozen)). Build a
   slot→char map by rendering the glyph atlas (each slot = 36 B, 12×12 2 bpp) and
   OCR/eyeballing; verify by decoding known screens. **Never hand-type token hex.**
4. **Plan glyph capacity.** The stock atlas has 2,196 slots; Chinese needs ~2,000 more.
   Append a grown atlas (4,320 slots) as a boot-time autoload payload at the BSS-clear
   boundary (`0x023027A0`), bump the heap floor literal above it, and repoint the ONE
   renderer atlas-base literal (`0x1315C`). The 8×16 UI-font path can't hold CJK — add a
   trampoline at its glyph-blit start (`0x131D8`) that routes slots ≥ 2,196 through the
   12×12 path. (Both mechanisms in `ROM_STRUCTURE.md` §2–4.)
5. **Translate into structure, not over bytes.** Keep JP→ZH tables keyed by record
   identity (unit id, command id, block offset). Encode with a deterministic convention
   (here: ZH-region slot first, then 1-byte code, then JP slot; skip tokens whose low
   byte would be 0x00/0x15). Respect per-site byte budgets, or implement growth:
   * stage files: splice + relocate absolute pointers + keep header tables 4-aligned;
   * arm9 strings: longer text goes to relocated pools (dead caves, or new autoload
     banks above the heap ceiling) with the table pointer re-aimed; **verify the target
     region is truly dead in EVERY game state** — "unused" RAM is stage-dependent.
6. **Patch code only where data can't reach**: render clamps/dispatch, decoder hooks,
   bounds checks. Document every patch {offset, old, new, why} and assert `old` when
   applying. Gameplay tweaks are code patches too — keep them explicit and separate.
7. **Gate everything.** Byte-level static gates first (audio header `0x60`, structural
   byte-identity outside known-safe frames, token-aware block integrity, pointer validity,
   alignment, width budgets, coverage ratchets) — they catch 90% of regressions in
   seconds. Then emulator boot/freeze tests, then screenshot + vision-model judging of
   actual rendered crops (pixel metrics alone mislead). Details: `TESTING_APPROACH.md`.
8. **Byte-identity as the finish line.** When refactoring a working hack (what this repo
   is), extract each changed region into semantic data + builder code, rebuild, and
   compare hashes per component. `diff → classify → semanticize → re-verify` until the
   residual patch set is empty.

## 5. Key insights (the short list)

* **The container round-trips**: per-component byte-identity ⇒ whole-ROM byte-identity.
  Work component-by-component; never hand-patch the assembled image.
* **One codec everywhere**: dialogue, names, barks, cut-ins, briefings all share the
  glyph-token grammar; only the surrounding framing differs (blocks vs pools vs offset
  tables). Token-aware scanning is mandatory — a `0xE0xx/0xF0xx` low byte may be
  `0x00`/`0x15`, so byte-wise scans corrupt (this caused two shipped crashes: false
  dialogue blocks over script jump pointers, and mis-sliced block frames).
* **Growth is a relocation problem**: stage files and arm9 both tolerate text growth
  fine — as long as every absolute pointer is re-aimed and alignment-sensitive tables
  stay 4-aligned. ARMv5 rotates unaligned `ldr` loads instead of faulting; the resulting
  wild pointers crash far from the cause.
* **RAM "free space" must be proven, not assumed**: the BSS clear, the heap arena, and
  the stage work buffer each silently destroy bytes parked in the wrong band; the only
  always-safe homes are the resident image, the BSS-boundary autoload block (with the
  heap floor bumped), and above the heap ceiling.
* **Width and memory budgets are the real translation constraints** — Chinese is denser
  than Japanese, which usually helps, but every UI field has a pixel budget and every
  block a byte budget; the static gates encode both.
* **Trust only the actual rendered screen** (emulator + vision judgment) for render
  correctness; trust only byte-level gates for structural safety. Each catches what the
  other can't.
