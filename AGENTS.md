# Project rules (binding for every agent/session)

## THE philosophy: the JP ROM is the single source of truth

Everything the game shows comes from the JP ROM.  The repo is organized around
that fact as a **one-pass transform with one extraction path**:

```
JP ROM ──► build/extract_data_from_game.py ──► data/jp/   (generated ground truth:
                (utils/extract/ = THE                      every record + its ROM address)
                 extraction path)                              │
                                                               ▼
                                                 data/zh/  (human-owned mapping:
                                                            address/id -> translation)
                                                               │
             build/build.py  =  JP ROM + data/zh + charmap/font/patches ──► ZH ROM
             build/build_guide.py  =  extractor + both ROMs ──► 攻略.html (review)
```

* `utils/extract/` is the ONLY code that knows how to read game data out of the
  ROM: `layout.py` owns every table address/record geometry (single home — the
  write-side appliers in `utils/arm9_layout.py` patch with the SAME constants);
  walkers decode per-surface.  `data/jp/` is its committed, gate-verified dump —
  regenerate with the CLI, never hand-edit.
* `data/zh/` holds ONLY translation decisions, keyed by the extractor's
  addresses/ids, grouped like the dump (units / characters / stages / files /
  ui / event_text / placements).  JP text is NEVER duplicated there — it is one
  lookup away in `data/jp/` under the same key.
* Curated knowledge the bytes cannot provide (bio ownership, stage speaker
  attribution) lives in `data/extraction/` as explicit extractor inputs.

When you find a bug, fix it **at its source of truth**, never by a downstream
patch-over-a-patch step:

* wrong translation        -> edit `data/zh/` (the mapping)
* wrong/missing JP record, wrong address, missing category
                           -> fix `utils/extract/` and regenerate `data/jp/`
                              (the reconciliation contract below tells you when)
* wrong/missing glyph      -> fix `data/font/atlas12.bin` generation and
                              `data/charmap.json`
* wrong encoding behavior  -> fix `utils/text_codec.py` / `utils/data_files.py` /
                              `utils/arm9_layout.py` so the encoder can not emit
                              the bad bytes again (per-surface rules live IN the
                              encoder, not in fixer scripts)
* wrong slot identity      -> fix the charmap + atlas together, then re-encode the
                              affected sources in place

**Never** structure work as "build the old/broken output first, then run a fixer
over it".  One-off migration scripts (historically `audit/tools/*.py`) may be
used ONCE to rewrite the committed sources, but the resulting repo state must
build correctly stand-alone; the fixer must not become part of the build path.

## The reconciliation contract (nothing lost, nothing missed)

`build/reconcile_extraction.py` (gate `zh_reconciliation`) enforces BOTH
directions, permanently:

1. every committed translation key in `data/zh` maps onto an extracted JP
   record — an unmatched key means the extraction algorithm has a gap: **fix
   the extractor, never drop the record**;
2. the extractor's universe covers the static gates' own independent JP-ROM
   scan — text the gates can find but the dump misses = extractor bug.

Gate `extraction_fresh` keeps `data/jp/` honest (committed == fresh run).
After improving extraction: regenerate the dump, review the git diff, run the
reconciler, commit dump + code together.

## Encoding safety rules (hard-won; do not relearn by shipping bugs)

Two fonts, one byte grammar (see `docs/TEXT_SYSTEM.md`):

* **renderA-direct surfaces** — every slot renders from the 12x12 atlas; one-byte
  codes 0x20..0xDF are fine (atlas low slots are ZH-patched): stage dialogue, barks,
  cut-ins, library/hangar banks, `data/zh/placements/briefing_blobs.json`,
  `data/zh/placements/idcmd_detail_pool.json` (and `data/zh/event_text.json`
  payloads are script records, not text).
* **Trampoline surfaces** — slots < 2196 render from the renderB 8x16 JP UI font
  whose charset DIFFERS (atlas 206=兵, renderB 206=無; full table:
  `data/renderb_charset.json`, identified with word-level proof): the four battle
  effect banks (`1da/1db/1df/1e0` = `data/zh/files/battle/{ability_cards,
  command_effects,special_abilities,special_defenses}.json`, zh_hex REQUIRED,
  build-enforced) and `data/zh/placements/{resident_caves,ui_names_bank,
  battle_name_pool,post_dict_labels}.json`.
  **This includes EVERY name pool / label placement**: unit, weapon, pilot and
  ability nameplates, ID-command names and summaries — all strings reached
  through table `ptr`s live in those placements and render on the trampoline
  (proven the hard way: one-byte `0x11 'D'` rendered き on a nameplate; a
  JP-band-minted 佛 rendered 恩 in 多佛炮).  On these, plain text must encode as
  **two-byte ZH-band tokens (slot >= 2196)**; a one-byte value is legal only if
  the record's ORIGINAL (JP/HEAD) payload already used that byte (structure or
  renderB-meaning, e.g. 0xD9=！ 0x7C=… 0xDB=・ 0xD8=？ 0x7A=、 0x92=近).  A new
  one-byte CJK/ASCII token there = garble (wrong JP glyph) or a sunk/thin
  misaligned glyph.
  **Exception — translated char-DB pilot names**: they ALSO render renderA-direct
  on the 0x2BCA6-patched dialogue speaker plate, and the two fonts share no slot
  identity, so the original-payload one-byte allowance does NOT apply — pilot
  names must be pure ZH-band (no one-bytes, no F-refs, no JP-band; enforced by
  `pool_trampoline_tokens`).
  `text_codec.encode()` takes `surface=` + `allowed_one_bytes=`; `slot_of(surface=
  "bank")` refuses JP-band registrations; the `bank_onebyte_regression`,
  `pool_trampoline_tokens` and `offline_coverage` gates enforce all of this.
* Never emit 2-byte tokens with low byte 0x00; 0x15-low only outside stage scripts.
* Digits/`+`/`%` on trampoline surfaces: use the ZH-band atlas glyphs
  (`+`=4158 `2`=4159 `0`=2680 `4`=2939 `%`=2969 …), NOT renderB digit bytes, or they
  render baseline-sunk next to 12px glyphs (the "NT等级4 / +20 misalignment" class).
* Trampoline 12x12 glyphs are baseline-anchored at **penY+3** by the cave patch
  (`data/patches/code_patches.json` @0x11A2A0) so atlas ink-bottom == renderB
  ink-bottom (row 14 of the 16px line box); `test/render_oracle.py` mirrors this.

## Glyph minting / slot registry (charmap + atlas move together)

* `data/charmap.json two_byte_zh` = the ENCODABLE registry; `slot_chars_extra` =
  decode-only bitmap identities (never encode targets until promoted);
  `jp_slot_chars` = the original JP identities (coverage denominator — a JP-band
  slot must keep its truthful JP label even when reclaimed).
* A new hanzi gets a cell by: (1) preferring a **ZH-band (>= 2196)** cell — reclaim a
  zero-demand registered char or a junk cell (stray kana/latin) whose token is unused
  in `data/**` hex AND in the built ROM's text surfaces (token-scan of `data/**` hex
  fields + the built ROM's text surfaces — procedure in docs/LESSONS_LEARNED.md §G);
  (2) JP-band cells only for chars that will NEVER appear on a trampoline surface
  (dialogue/bark-only), and their JP tokens must be candidate-ROM token-free;
  (3) paint the cell surgically (WQY 12px raster + drop-shadow grammar per
  docs/TEXT_SYSTEM.md) and verify the crop.  If WQY renders a glyph badly at 12px
  (e.g. β descender), copy a proven bitmap cell byte-exact instead.
* Growing a pooled string: ui bank heap-safe gaps, resident-cave zero runs, then
  ledger-vacated spans (`data/zh/placements/relocation_ledger.json` is the
  anti-double-allocation record; NEVER hand out pool bytes without going through it).
* The `zh` fields of `data/zh/units.json` / `characters.json` are ANNOTATIONS
  mirroring placement bytes: regenerate them with the per-surface decoder
  (trampoline: renderB for one-byte, ZH-band for 2-byte) — a stage-decoder sync
  writes false 来/メ pollution.

## Verification (what "done" means)

`build/build.py` from a clean tree (strict manifest verify; refresh via
`build/refresh_manifest.py` after intended data changes), then:
1. `test/run_static.py <rom>` — ALL 32 gates, including the ratchets
   (`translation_coverage`, `unit_weapon_names`, `id_command_names`,
   `bank_onebyte_regression`), `pool_trampoline_tokens` (zero JP-band 2-byte
   tokens in any referenced name-pool string), and the architecture gates
   (`extraction_fresh`, `zh_reconciliation`);
2. `test/coverage_render.py <rom> --out /tmp/coverage` — offline render of EVERY text
   line via the pixel oracle (`test/render_oracle.py`, parity-anchored to live emulator
   by `test/test_render_oracle_parity.py`); zero new algorithmic findings;
3. `test/live/test_boot_smoke.py <rom>` — live boot + golden dialogue (×2 for release);
4. for text-surface changes: a live screenshot of the affected surface
   (`test/live/drive_id_page.py` for ID/ability pages), or an oracle render when the
   surface is not reachable by harness navigation.
The deliverable must be byte-reproducible, and `README.md`'s expected sha1s (main +
pad32m) must match the actual build output — they are the user's verification anchor.
Scaled judgment (glyph identity, naturalness) fans out to subagent fleets over
`coverage_render.py --sheets` output — never to a human playthrough.
See docs/TESTING_APPROACH.md §3.5.

## Repo layout (what ships and why)

* `data/jp/` — GENERATED JP ground truth (extractor output; gate-pinned fresh).
* `data/zh/` + `data/charmap.json` + `data/renderb_charset.json` + `data/font/` +
  `data/patches/` + `data/extraction/` + `data/manifest.json` — the transform
  inputs; `manifest.json` pins every component + output hash.
* `build/` — `build.py` (the ROM build), `extract_data_from_game.py` (THE dump
  CLI), `reconcile_extraction.py` (the completeness contract),
  `refresh_manifest.py`, `build_guide.py` (攻略.html review guide).
* `utils/` — `extract/` (the extraction package incl. `layout.py`, the single
  geometry home), `text_codec.py`, `arm9_layout.py`, `stage_text.py`,
  `data_files.py`, `rom.py`.
* `test/` — the full verification stack (static gates, pixel oracle + parity test,
  offline coverage renderer, live harnesses, `test/golden/` baselines).
* `docs/` — architecture references (TEXT_SYSTEM, ROM_STRUCTURE, STAGE_FORMAT…).
* `audit/` — REMOVED (purged from all history 2026-07-22).  It held OPTIONAL,
  fully-removable historical analysis / one-time-migration scripts (incl.
  `migrate_zh_layout.py`, the applied data/zh migration) and the applied-audit
  evidence; nothing in `build/`, `utils/`, `test/` or `data/` ever depended on
  it.  Its durable knowledge lives in the docs: terminology rulings ->
  docs/TRANSLATION_GUIDE.md §2b; campaign lessons/procedures ->
  docs/LESSONS_LEARNED.md §G; binding rules -> this file.

Follow these instructions exactly. When working in subdirectories not listed above, check for additional project instruction files (AGENTS.md, Claude.md, etc.).
