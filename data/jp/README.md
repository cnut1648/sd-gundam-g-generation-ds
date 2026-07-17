# data/jp — the extracted JP ground truth (machine-generated)

**Never edit these files.** They are the deterministic output of

```bash
python build/extract_data_from_game.py            # regenerate after extractor changes
python build/extract_data_from_game.py --check    # committed == fresh? (gate-enforced)
```

The JP ROM is the single source of truth for Japanese game data; this dump is
its structured, addressed view — every record carries the exact ROM location
(`ptr_site`/`off`/`record` + `len`) that the translation mapping keys on, plus
a loss-aware per-surface transcription (`{00}` separators, `{F0:n}` macros,
`{SLOT:n}`/`{B:n}` unidentified glyphs).

| file | contents |
|---|---|
| `characters.json` | per-cid block: name, voiceset, 3 ID commands (name/summary/effect detail/cut-in), barks, encyclopedia bio |
| `units.json` | per-utid block: name, 6 weapon names, special ability/defense linkage, encyclopedia bio |
| `stages/<_STGxx>.json` | per stage file: descriptors (label/title), briefing blocks, every dialogue block in console play order (scene/order/branch/speaker/choice) |
| `event_text.json` | all arm9-inline story-text blocks (briefing + route/event text) |
| `cutins.json` | the 942 cut-in famous-line records + the idn→record link map |
| `battle_effects.json` | 1da ability cards, 1db command effects, 1df/1e0 special ability/defense records |
| `parts.json` | hangar part names + captions |
| `library.json` | encyclopedia weapon-name list (31e) |
| `ui.json` | both macro dictionaries + the string-pointer graph (every code word aiming at a JP string, grouped by target) |

Extraction algorithm fixes go in `utils/extract/`; curated knowledge the bytes
cannot provide lives in `data/extraction/` (bio ownership, stage speaker
attribution).  Completeness is enforced two ways (`build/reconcile_extraction.py`):
every committed translation must map onto a record here, and the static gates'
independent ROM scan must find nothing this dump misses.
