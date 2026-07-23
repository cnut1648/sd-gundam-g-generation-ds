# DATA_FORMATS — schema reference for `data/`

The JP ROM is the single source of truth; `data/` holds its two derived sides
plus the shared glyph system. Global conventions:

* JSON is UTF-8, `ensure_ascii=False`, `indent=1`; offsets/pointers/ids are `"0x…"` hex
  strings unless noted; `*_hex` fields are raw bytes in lowercase hex.
* `zh`/`zh_text` fields are human-readable translation text; where a sibling
  `zh_hex`/`payload_hex` exists, the **hex is the canonical build input** (the encoded
  form is the artifact; the text is the view). Where no hex sibling exists, the text is
  re-encoded at build time through `utils/text_codec.py` and must round-trip.
* Text fields may contain escapes produced by the codec: `{00}` separator/pad, `{01}`,
  `{03}`, `{04}`… control bytes, `{F0:n}` dictionary macro n, `{SLOT:n}` glyph slot
  without a charmap character (`{B:n}` = unidentified renderB glyph in data/jp).
* Table geometry (bases, strides, field offsets) lives ONCE in
  `utils/extract/layout.py` — data files carry only keys and values.
* Every builder self-checks its output sha1 against `manifest.json`.

## jp/ — the extracted ground truth (generated; never hand-edit)

Output of `build/extract_data_from_game.py` (see `data/jp/README.md` for the
per-file inventory). Every record carries its exact ROM location — `ptr_site`
(the pointer word's file offset), `off`/`len` (the string bytes), or
`record`/`index` (table keys) — plus a loss-aware per-surface transcription.
The `extraction_fresh` gate pins committed == fresh; `build/reconcile_extraction.py`
(gate `zh_reconciliation`) proves every zh key maps onto a record here.

## extraction/ — curated extractor inputs

Knowledge the bytes cannot provide: `library_bio_map.json` (encyclopedia bio
index → owning cid/utid; no in-ROM index table exists) and
`stage_speakers.json` (per-block speaker cid + real player forks).

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
| `slot_chars_extra` | slot → char | decode-side identity refinements (incl. the VLM-identified atlas cells); **override decoding only** — encoding preferences are frozen so build output can't drift |

## renderb_charset.json

`slots`: renderB 8x16 UI-font identities. Slots < 224 carry `{char, kind}` with
word-level proof; slots ≥ 224 (`kind: "ident"`) are the kanji-band identities
from the one-shot VLM contact-sheet campaign — decode aids that deliberately
never affect gate behavior (kana-leak detection keys on `kind == "kana"`).

## font/atlas12.bin

The 12×12 glyph atlas autoload payload, verbatim: 4,320 slots × 36 bytes (2 bpp,
12 rows × 3 bytes). Slots 0–2195 = original glyphs, 2196+ = added Chinese glyphs.
Copied to RAM `0x023027A0` at boot. Slot semantics in `charmap.json`.

## zh/stages/<_STGxx>.json (→ `utils/stage_text.py`)

Per stage file. `edits` are byte-range replacements in ORIGINAL-file coordinates
(`jp_offset` = the mapping key, pairing with `data/jp/stages/`), ascending and
non-overlapping; `inserts` are pure insertions.

```jsonc
{
 "file": "_STG05.bin", "source_size": 73388, "built_size": 76180,
 "edits": [{
   "jp_offset": "0x111e", "jp_len": 9,
   "kind": "dialogue",            // exactly one 0x15…00 00 block ("script" = other ranges)
   "zh_text": "…",                // decoded view, never enters the build
   "zh_hex": "…"                  // canonical replacement bytes
 }],
 "inserts": [{"jp_offset": "0x41c", "hex": "0000", "reason": "table_alignment"}]
}
```

`script`-kind `zh_hex` may embed absolute pointers already relocated for the final
layout — don't change lengths by hand (see `BUILD_GUIDE.md` §3). Pointer relocation and
alignment rules: `STAGE_FORMAT.md`. JP text/play order/speaker per key:
`data/jp/stages/<stage>.json`.

## zh/units.json + zh/characters.json (→ `utils/arm9_layout.py`)

The semantic name mapping, grouped per owner:

```jsonc
// units.json
{"utid": 1, "zh": "∀高达", "ptr": "0x218C0C0",
 "weapons": [{"slot": 0, "zh": "光束军刀", "ptr": "0x218E864"}]}
// characters.json
{"cid": 2, "zh": "爱娜·萨哈林", "ptr": "0x218E68F",
 "ids": [{"idn": 6, "name": {"zh": "我的战争！！", "ptr": "0x2328FA9"},
          "summary": {"zh": "放水", "ptr": "0x218FEE4"}}]}
```

* `ptr` re-aims the record's pointer word at a relocated string (bytes live in
  `zh/placements/`); absent `ptr` = the string was rewritten in place inside a
  placement pool. `zh` is the annotation mirroring those bytes (regenerate with
  the per-surface decoder — see AGENTS.md).
* `characters.json detail_offsets` re-aims slots of the 256-entry effect-detail
  offset table (`didx`-keyed).
* `units.json carrier_capacity` is a deliberate gameplay override (Eternal = 6).

## zh/ui.json (→ `utils/arm9_layout.py`)

* `labels` / `abilities` — `{zh, old_ptr, ptr, sites[]}`: every literal pointer
  word in `sites` is re-aimed from `old_ptr` (asserted against the JP image) to
  `ptr`. Keys pair with `data/jp/ui.json pointer_strings`.
* `dictionary` — the text-macro store at `0x12D770`: `offset_entries`
  (re-aimed table slots) + `string_edits` (`{offset, zh, payload_hex}` in-place
  entry rewrites; hex canonical).
* `resource_offsets` — offset words pinning rebuilt files (`1da`, `1df`), with
  `old_value` asserts.

The cut-in quote offset table is NOT data: it is derived at build time from
`zh/files/battle/cutin_quotes.json` (geometry in `utils/extract/layout.py`).

## zh/event_text.json (→ `utils/arm9_layout.py`)

The story/briefing `0x15…00 00` blocks embedded in arm9: `{offset, length, zh,
payload_hex}`, byte-length-locked (briefing records point into pool B). Keys
pair with `data/jp/event_text.json`.

## zh/placements/ (→ `utils/arm9_layout.py`)

Encoded string storage, `{offset, text, payload_hex}` per entry (offsets are file
offsets into arm9, or bank-relative for the autoload banks):

* `battle_name_pool.json`, `idcmd_detail_pool.json`, `post_dict_labels.json`,
  `resident_caves.json` — in-place pools/caves inside the code image.
* `ui_names_bank.json` (→ RAM `0x02328720`) and `briefing_blobs.json`
  (→ RAM `0x023E7000`) — the two appended autoload banks. Note: only
  `[0x02328720, 0x0232C800)` of pool A is referenced at runtime; the remainder is
  retired data kept byte-exact on purpose (see `ROM_STRUCTURE.md` §2).
* `relocation_ledger.json` — the anti-double-allocation record for pool bytes
  (never hand out placement space without going through it).

## patches/

* `code_patches.json` — 36 entries `{file_offset, old_hex, new_hex, what, why}`:
  render-path detours + caves, decoder hooks, one-byte render fixes, and the seven
  free-battle gameplay threshold windows. The builder asserts `old_hex` before writing.
* `raw_regions.json` — annotated residual regions not covered by any semantic table.
  **Currently empty; keep it that way** — a nonempty file means un-understood bytes.

## zh/files/ (→ `utils/data_files.py`)

`data/zh/files/README.md` documents each of the 25 files; five layouts:

| layout | used by | model |
|---|---|---|
| `edits` | bark banks `0/1/1dd/1de/c4f`, `1db`, `1df`, `1e0`, `31e`, `324`, `c4b`, `b6f`, `1da` | in-place runs: re-encode `zh` at `offset`, 0x00-pad to `size`; optional `append` block (grown `1da`) |
| `cutin_groups` | `1dc` | whole-file rebuild: per record `header` + encoded `zh` + terminator `00 03 00 01` + 4-byte alignment padding; the arm9 offset table is derived from this file at build time |
| `table` | `b6e` | fixed-total-size name table rebuilt from entries at explicit offsets; `name_offset_words` patches the mirroring arm9 table |
| `graphics` | `42d`, `388`, `478`, `48a`, `c31` | raw-tile repaints `{offset, jp_hex, zh_hex}` with original-byte asserts (tiles, not text) |
| `atlas_graphics` | `3d3`–`3d7` | static BG labels rebuilt from committed 12x12 atlas cells; clear boxes and clean donor rows are explicit, and shared-tile resources are copy-on-write repacked/deduplicated within their original capacity |

## Cross-component couplings (regenerate together)

| if you change | also regenerate |
|---|---|
| `zh/files/battle/cutin_quotes.json` layout | nothing manual — the arm9 offset table + size word are build-derived |
| `zh/files/battle/ability_cards.json` append | `zh/ui.json resource_offsets` (`1da` entry) |
| `zh/files/hangar/part_names.json` layout | its own `name_offset_words` section |
| any placement string layout | every `ptr` that targets it (`zh/units.json`, `zh/characters.json`, `zh/ui.json`) |
| `font/atlas12.bin` size | nothing manual — autoload list + heap floor are computed |
| `utils/extract/` behavior | `data/jp/` (rerun the dump CLI; `extraction_fresh` enforces) |
