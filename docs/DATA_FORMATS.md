# DATA_FORMATS — schema reference for `data/`

Everything the build consumes lives under `data/`. Global conventions:

* JSON is UTF-8, `ensure_ascii=False`, `indent=1`; offsets/pointers/ids are `"0x…"` hex
  strings unless noted; `*_hex` fields are raw bytes in lowercase hex.
* `zh`/`zh_text` fields are human-readable translation text; where a sibling
  `zh_hex`/`payload_hex` exists, the **hex is the canonical build input** (the encoded
  form is the artifact; the text is the view). Where no hex sibling exists, the text is
  re-encoded at build time through `utils/text_codec.py` and must round-trip.
* Text fields may contain escapes produced by the codec: `{00}` separator/pad, `{01}`,
  `{03}`, `{04}`… control bytes, `{F0:n}` dictionary macro n, `{SLOT:n}` glyph slot
  without a charmap character.
* Every builder self-checks its output sha1 against `manifest.json`.

## manifest.json

```jsonc
{
 "source_rom":        {"sha1": …, "size": …},   // the Japanese dump the build requires
 "output_rom":        {"sha1": …, "size": …},   // the translated ROM
 "output_rom_padded": {"sha1": …, "size": …},   // the 32 MiB padded variant
 "components": {"arm9": sha1, "_STG00.bin": sha1, …}   // all 122 built components
}
```

## charmap.json (→ `utils/text_codec.py`)

| key | mapping | role |
|---|---|---|
| `one_byte` | char → code 0x00–0xDF | 1-byte codes; the code IS the glyph slot |
| `two_byte_zh` | char → slot ≥ 2196 | added Chinese glyphs (encode + decode) |
| `jp_slot_chars` | slot 224–2195 → char | original Japanese glyphs (decode aid) |
| `slot_chars_extra` | slot → char | decode-side refinements established from in-game
  text evidence; **override decoding only** — encoding preferences are frozen so build
  output can't drift |

## font/atlas12.bin

The 12×12 glyph atlas autoload payload, verbatim: 4,320 slots × 36 bytes (2 bpp,
12 rows × 3 bytes). Slots 0–2195 = original glyphs, 2196+ = added Chinese glyphs.
Copied to RAM `0x023027A0` at boot. Slot semantics in `charmap.json`.

## dialogue/stages/<_STGxx>.json (→ `utils/stage_text.py`)

Per stage file. `edits` are byte-range replacements in ORIGINAL-file coordinates,
ascending and non-overlapping; `inserts` are pure insertions.

```jsonc
{
 "file": "_STG05.bin", "source_size": 73388, "built_size": 76180,
 "edits": [{
   "jp_offset": "0x111e", "jp_len": 9,
   "kind": "dialogue",            // exactly one 0x15…00 00 block ("script" = other ranges)
   "speaker": "夏亚",              // dialogue only, informative
   "jp_text": "…", "zh_text": "…", // decoded views, never enter the build
   "jp_hex": "…", "zh_hex": "…"    // zh_hex = canonical replacement bytes
 }],
 "inserts": [{"jp_offset": "0x41c", "hex": "0000", "reason": "table_alignment"}]
}
```

`script`-kind `zh_hex` may embed absolute pointers already relocated for the final
layout — don't change lengths by hand (see `BUILD_GUIDE.md` §3). Pointer relocation and
alignment rules: `STAGE_FORMAT.md`.

## names/ (→ `utils/arm9_layout.py`)

Semantic name tables; every file records its table geometry and per-record entries.

| file | keyed by | writes |
|---|---|---|
| `units.json` | unit-type id | name ptr at master-table `+0x00` (in-place re-encode or `ptr` re-aim) |
| `weapons.json` | unit-type id, weapon slot | the 6 weapon sub-records per unit |
| `pilots.json` | character id | char-DB name ptr `+0x04` |
| `id_commands.json` | command id | name `+0x00`, summary `+0x08`, `details` via the 256-entry offset table |
| `abilities.json` | string identity | 75 strings + all 583 pointer sites (`old_ptr` → `ptr`) |
| `parts.json` | part index | the u32 start-offset table for the parts name bank (`b6e.bin`) |

Entries carry `jp`, `zh`, plus either in-place `payload_hex` or a `ptr` into an arena
(the string bytes then live in `arenas/`).

## ui/

* `labels.json` — label strings with every literal pointer `site` that references them.
* `dictionary.json` — the text-macro dictionary at `0x12D770`: repointed offset-table
  slots + re-encoded entry strings (`{index, jp, zh, payload_hex}`).
* `cutin_quote_offsets.json` — the 943-entry u32 offset table into `1dc.bin` + the
  resource size word; **must be regenerated together with `files/battle/cutin_quotes.json`**.
* `resource_offsets.json` — offset words pinning other rebuilt files (`1da`, `1df`).

## arenas/

Encoded string storage, `{offset, text, payload_hex}` per entry (offsets are file
offsets into arm9, or bank-relative for the autoload banks):

* `battle_name_pool.json`, `idcmd_detail_pool.json`, `post_dict_labels.json`,
  `resident_caves.json` — in-place pools/caves inside the code image.
* `event_text_blocks.json` — the 1,267 story/briefing `0x15…00 00` blocks embedded in
  arm9 (byte-length-locked; briefing records point into pool B).
* `ui_names_bank.json` (→ RAM `0x02328720`) and `briefing_blobs.json`
  (→ RAM `0x023E7000`) — the two appended autoload banks. Note: only
  `[0x02328720, 0x0232C800)` of pool A is referenced at runtime; the remainder is
  retired data kept byte-exact on purpose (see `ROM_STRUCTURE.md` §2).

## patches/

* `code_patches.json` — 36 entries `{file_offset, old_hex, new_hex, what, why}`:
  render-path detours + caves, decoder hooks, one-byte render fixes, and the seven
  free-battle gameplay threshold windows. The builder asserts `old_hex` before writing.
* `raw_regions.json` — annotated residual regions not covered by any semantic table.
  **Currently empty; keep it that way** — a nonempty file means un-understood bytes.

## files/ (→ `utils/data_files.py`)

`data/files/README.md` documents each of the 20 files; four layouts:

| layout | used by | model |
|---|---|---|
| `edits` | bark banks `0/1/1dd/1de/c4f`, `1db`, `1df`, `1e0`, `31e`, `324`, `c4b`, `b6f`, `1da` | in-place runs: re-encode `zh` at `offset`, 0x00-pad to `size`; optional `append` block (grown `1da`) |
| `cutin_groups` | `1dc` | whole-file rebuild: per record `header` + encoded `zh` + terminator `00 03 00 01` + 4-byte alignment padding; offset table lives in arm9 (`ui/cutin_quote_offsets.json`) |
| `table` | `b6e` | fixed-total-size name table rebuilt from entries at explicit offsets |
| `graphics` | `388`, `3d3`, `3d5`, `478`, `48a` | raw-tile repaints `{offset, jp_hex, zh_hex}` with original-byte asserts (tiles, not text) |

## Cross-component couplings (regenerate together)

| if you change | also regenerate |
|---|---|
| `files/battle/cutin_quotes.json` layout | `ui/cutin_quote_offsets.json` (offsets + size word) |
| `files/battle/ability_cards.json` append | `ui/resource_offsets.json` (`1da` entry) |
| `files/hangar/part_names.json` layout | `names/parts.json` (offset table) |
| any arena string layout | every `ptr` that targets it (`names/*`, `ui/labels.json`) |
| `font/atlas12.bin` size | nothing manual — autoload list + heap floor are computed |
