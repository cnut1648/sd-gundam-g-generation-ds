# SD Gundam G Generation DS — Chinese translation build

A complete, self-contained build system for the Chinese fan-translation of
**SD Gundam G Generation DS** (Nintendo DS, Japan). One command turns the
Japanese cartridge dump into the fully translated ROM, byte-for-byte
reproducibly.

```
.
├── 0098 - SD Gundam G Generation DS (Japan).nds   # Japanese source ROM (input)
├── sd-gundam-g-generation-zh.nds                  # translated ROM (build output)
├── build/          # the build entry point (build.py) — one command, one pass
├── data/           # ALL translation & build data (names, dialogue, patches, font…)
├── utils/          # helper library (text codec, stage builder, arm9 layout, ROM io)
├── test/           # full test suite: static gates, live emulator, screenshot, VLM
└── docs/           # documentation: build guide, formats, addresses, lessons learned
```

## Quick start

```bash
# one-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# build (Japanese source in, translated ROM out)
.venv/bin/python build/build.py "0098 - SD Gundam G Generation DS (Japan).nds" sd-gundam-g-generation-zh.nds
```

Expected output:

```
[build] final ROM sha1 421896087f0f2b827529d1b1172b9ddcc5d219fc  (MATCHES the shipped translation)
[build] wrote sd-gundam-g-generation-zh.nds  (30,324,584 bytes)
```

Add `--pad32m PATH` to also write a 32 MiB padded image for flash carts that
want power-of-two sizes (sha1 `e0cfa1eba0e0b7c53d0b116202b820a93c71d364`).

The source ROM must be the Japanese cartridge dump with sha1
`12443b91297a57bcd2ace8da989c26ae635a79fd` (33,554,432 bytes) — the build
verifies this and every intermediate component against `data/manifest.json`,
so a wrong input or corrupted data fails loudly, never silently.

## Verifying a build

```bash
.venv/bin/python test/run_static.py sd-gundam-g-generation-zh.nds   # static gates, seconds
.venv/bin/python test/live/boot_smoke.py sd-gundam-g-generation-zh.nds   # emulator boot test
```

See `test/README.md` for the full test tiers (static → live emulator →
screenshot goldens → VLM screenshot judging) and their dependencies.

## What the build does

Single deterministic pass (details in `docs/BUILD_GUIDE.md`):

1. **Code binary (arm9)** — bakes the translated name tables (units, weapons,
   pilots, ID commands, abilities, parts), UI labels, the text-macro
   dictionary, string pools, and story/briefing text into the Japanese image;
   applies ~36 documented code patches (render-path fixes and gameplay
   tweaks); appends the 12×12 CJK glyph atlas and two relocated string banks
   as boot-time autoload payloads.
2. **Stage dialogue** — rebuilds all 101 `_STG*.bin` stage files: translated
   dialogue blocks spliced in with growth, every absolute pointer relocated,
   header tables kept 4-byte aligned.
3. **Data files** — rebuilds 20 miscellaneous files: battle barks, cut-in
   quotes, effect/ability text, encyclopedia biographies, part names, and a
   few repainted UI graphics.
4. **Container** — reassembles the ROM and verifies the final sha1.

## Documentation map

| doc | what's in it |
|---|---|
| `docs/BUILD_GUIDE.md` | step-by-step build walkthrough, component pipeline, how to change a translation |
| `docs/DATA_FORMATS.md` | schema reference for everything under `data/` |
| `docs/ROM_STRUCTURE.md` | NDS container, arm9 RAM map, autoload mechanics, **full address map** |
| `docs/TEXT_SYSTEM.md` | text encoding, glyph atlas, renderers, dictionaries, width budgets |
| `docs/STAGE_FORMAT.md` | stage-file format: blocks, pointers, growth, alignment |
| `docs/GAME_NOTES.md` | game structure and where every text surface lives |
| `docs/TRANSLATION_GUIDE.md` | translation conventions, terminology, QA process |
| `docs/TESTING_APPROACH.md` | testing philosophy: static gates, live tests, VLM judging |
| `docs/LESSONS_LEARNED.md` | the wrong-turn catalog: disproven theories, crashes, and the guards that prevent them |
| `data/README.md` | data folder layout and schemas at a glance |
| `test/README.md` | how to run every test tier |

## Requirements

* Python ≥ 3.12 with `ndspy` (build); `Pillow`/`numpy` (tests).
* Live tests additionally need melonDS, Xvfb and xdotool — see `test/README.md`.

## Legal

You must supply your own Japanese cartridge dump. This repository contains
only the translation data, tooling and documentation; ROM images are not
distributed (and are `.gitignore`d).
