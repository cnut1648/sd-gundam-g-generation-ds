# data/ — translation & build data

Everything the build needs besides the original ROM. All JSON is UTF-8,
`indent=1`, `ensure_ascii=False`. Offsets/pointers are `"0x..."` strings;
`payload_hex` fields are the canonical encoded bytes (the builder writes them
verbatim, so a text edit only takes effect after re-encoding the payload).

## Code-binary (arm9) data — consumed by `utils/arm9_layout.py`

### names/
Semantic name tables. Every file records the table geometry (`table`) and one
entry per translated item: the original text (`jp`), the translation (`zh`),
and — when the record's pointer word was re-aimed at a relocated string — the
pointer value to write (`ptr`, absent for in-place edits). The string bytes
themselves live in the arenas (below).

| file | keyed by | table |
|---|---|---|
| `units.json` | `utid` | per-unit master records, name ptr at +0x00 |
| `weapons.json` | `utid`, `slot` | 6 weapon sub-records per unit at +0x2C |
| `pilots.json` | `char_id` | character DB, name ptr at +0x04 |
| `id_commands.json` | `id` (= character×3+slot) | name +0x00, summary +0x08, detail via a 256-entry u32 offset table |
| `abilities.json` | string pair | every pointer word (`sites`) re-aimed from `old_ptr` to `ptr` |
| `parts.json` | `index` | u32 start-offset table for the parts name bank (file `b6e`) |

### ui/
* `labels.json` — menu/status/info label pointer sites, grouped per
  (original string → translated string); `sites` lists every literal word.
* `dictionary.json` — the text-macro dictionary (F-token expansions: dialogue
  compression words and UI/name fragments): re-aimed offset-table slots plus
  in-place re-encoded entry strings.
* `cutin_quote_offsets.json` — 943-entry u32 offset table into the cut-in
  quote resource (file `1dc`) plus the resource-size word; must match the
  rebuilt file.
* `resource_offsets.json` — other offset words tied to rebuilt data files.

### arenas/
Encoded string storage: `{offset, text, payload_hex}` entries.
* In-place regions inside the code image: `battle_name_pool` (in-battle
  titles/quotes/ability texts), `idcmd_detail_pool` (effect details),
  `post_dict_labels` (menu descriptors), `resident_caves` (relocated names —
  the name arena and verified-free zero runs; the runtime heap never touches
  them).
* `event_text_blocks.json` — story/briefing text embedded in the code image:
  byte-length-locked `0x15 ... 00 00` blocks; briefing records carry inline
  pointers into the briefing bank instead of text.
* Boot-time autoload banks appended to the image: `ui_names_bank.json`
  (→ RAM 0x02328720) and `briefing_blobs.json` (→ RAM 0x023E7000); gaps
  between entries are zero, leading 0x01 bytes are pad.

### patches/
* `code_patches.json` — every non-data change: render-path detours + their
  code caves, text-decoder hooks, gameplay tweaks. `{file_offset, old_hex,
  new_hex, what, why}`; the builder asserts `old_hex` before writing.
* `raw_regions.json` — annotated residual byte regions not covered by any
  semantic table (currently empty — keep it that way).

### font/
* `atlas12.bin` — the 12×12 glyph atlas autoload payload (4320 slots × 36 B,
  2 bpp): original glyphs in slots 0–2195, added Chinese glyphs from 2196.
  Copied to RAM 0x023027A0 at boot. `charmap.json` maps characters to slots.

## Other components
* `charmap.json` — character ↔ glyph-slot tables used by `utils/text_codec.py`.
* `dialogue/` — per-stage dialogue for the `_STG*` files (`utils/stage_text.py`).
* `files/` — data for the misc NitroFS files.
* `manifest.json` — expected sha1 of every built component and the final ROM;
  every builder self-checks against it.
