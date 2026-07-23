# data/ — the translation mapping (plus the extracted JP ground truth)

The JP ROM is the single source of truth for Japanese game data.  This folder
holds the two sides of the transform, plus the shared glyph system:

```
jp/            GENERATED ground truth: build/extract_data_from_game.py dumps the
               JP game data (characters, units, stages, event text, gallery…) with
               every record's exact ROM address.  Never hand-edit; regenerate.
extraction/    curated extractor inputs the bytes can't provide (bio ownership,
               stage speaker attribution).
zh/            HUMAN-owned translation mapping, grouped like jp/ and keyed by the
               same addresses/ids.  This is what you edit.
charmap.json   glyph-slot registries (encode + decode) for the 12x12 atlas.
renderb_charset.json   identities of the 8x16 renderB UI font.
font/atlas12.bin       the rebuilt 12x12 atlas autoload payload.
patches/       code patches + annotated raw regions (old_hex asserted).
manifest.json  expected sha1 of every built component and the final ROM.
```

All JSON is UTF-8, `indent=1`, `ensure_ascii=False`; offsets/pointers are
`"0x…"` strings; `*_hex` fields are canonical encoded bytes (the builder writes
them verbatim — a text edit only takes effect after re-encoding the payload).
Where no hex sibling exists, `zh` re-encodes at build time via
`utils/text_codec.py` and must round-trip.

## zh/ — what the build consumes (per file)

Table geometry (bases, strides, field offsets) lives ONCE in
`utils/extract/layout.py`; the mapping carries only keys and values.

| file | keyed by | what the build writes |
|---|---|---|
| `gallery.json` | `event_no`, raw-keyed extracted `series_id` | 54 EV labels and 28 unique series labels for the six coupled gallery resources; character/unit names are joined from the canonical rosters, not duplicated; extracted JP strings are best-effort annotations, never identity keys |
| `units.json` | `utid` (+ weapon `slot`) | name/weapon pointer words re-aimed at relocated strings (`ptr`; absent = in-place pool rewrite); `carrier_capacity` gameplay override; optional `gallery_zh` is the width-bounded gallery display alias |
| `characters.json` | `cid`, `idn` = cid×3+slot, `didx` | pilot/ID-command name+summary pointer words; effect-detail offset-table slots; optional `gallery_zh` is the width-bounded gallery display alias |
| `ui.json` | pointer sites / dict index | label+ability literal `sites` (JP word asserted via `old_ptr`), dictionary offset slots + in-place entry rewrites (`payload_hex`), resource offset words |
| `stages/<_STGxx>.json` | `jp_offset` (JP-file coords) | dialogue/script byte-range replacements (`zh_hex` canonical, `jp_len` span, `built_size` asserted) — `utils/stage_text.py` |
| `event_text.json` | arm9 `offset` | byte-length-locked story/briefing block payloads |
| `files/**` | file + `offset`/`index`/`group` | the 20 NitroFS bank rebuilds (barks, battle effects, cut-ins, bios, parts, graphics) — `utils/data_files.py`; the four trampoline banks (1da/1db/1df/1e0) REQUIRE `zh_hex` |
| `placements/*.json` | bank + `offset` | encoded string bytes: in-place pools + verified-free caves inside the image, and the two appended autoload banks (`ui_names_bank` → RAM 0x02328720, `briefing_blobs` → RAM 0x023E7000); `relocation_ledger.json` is the anti-double-allocation record |

Every zh key must exist in the extracted JP universe — verified by
`build/reconcile_extraction.py` (and the JP text for any key is one lookup
away in `data/jp/`).  JP text is never duplicated here.
