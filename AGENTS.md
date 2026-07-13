# Project rules (binding for every agent/session)

## THE philosophy: one-shot, not incremental

The repo is a **single-step transform**: `JP ROM + sources (data/, build/, utils/) -> ZH ROM`.
`build/build.py` must always produce the correct ROM in ONE pass from committed sources.

When you find a translation / font / encoding bug, you MUST fix it **at the source of
truth**, never by adding a downstream patch-over-a-patch step:

* wrong translation        -> edit the text in `data/` (stage JSON, bank JSON, barks…)
* wrong/missing glyph      -> fix `data/font/atlas12.bin` generation and `data/charmap.json`
* wrong encoding behavior  -> fix `utils/text_codec.py` / `utils/data_files.py` /
                              `utils/arm9_layout.py` so the encoder can not emit the bad
                              bytes again (per-surface rules live IN the encoder, not in
                              fixer scripts)
* wrong slot identity      -> fix the charmap + atlas together, then re-encode the
                              affected sources in place

**Never** structure work as "build the old/broken output first, then run a fixer over
it". One-off migration scripts (historically `audit/tools/fix_*.py`) may be used ONCE to rewrite the
committed sources, but the resulting repo state must build correctly stand-alone; the
fixer must not become part of the build path.

## Encoding safety rules (hard-won; do not relearn by shipping bugs)

Two fonts, one byte grammar (see `docs/TEXT_SYSTEM.md`):

* **renderA-direct surfaces** — every slot renders from the 12x12 atlas; one-byte
  codes 0x20..0xDF are fine (atlas low slots are ZH-patched): stage dialogue, barks,
  cut-ins, library/hangar banks, `data/arenas/briefing_blobs.json`,
  `data/arenas/idcmd_detail_pool.json` (and `event_text_blocks` payloads are script
  records, not text).
* **Trampoline surfaces** — slots < 2196 render from the renderB 8x16 JP UI font whose
  charset DIFFERS (atlas 206=兵, renderB 206=無; full table:
  `data/renderb_charset.json`, identified with word-level proof): the four battle
  effect banks (`1da/1db/1df/1e0` = `data/files/battle/{ability_cards,command_effects,
  special_abilities,special_defenses}.json`, zh_hex REQUIRED, build-enforced) and
  `data/arenas/{resident_caves,ui_names_bank,battle_name_pool,post_dict_labels}.json`.
  **This includes EVERY name pool / label arena**: unit, weapon, pilot and ability
  nameplates, ID-command names and summaries — all strings reached through table
  `ptr`s live in those arenas and render on the trampoline (proven the hard way:
  one-byte `0x11 'D'` rendered き on a nameplate; a JP-band-minted 佛 rendered 恩
  in 多佛炮). On these, plain text must encode as **two-byte ZH-band tokens
  (slot >= 2196)**; a one-byte value is legal only if the record's ORIGINAL
  (JP/HEAD) payload already used that byte (structure or renderB-meaning, e.g.
  0xD9=！ 0x7C=… 0xDB=・ 0xD8=？ 0x7A=、 0x92=近). A new one-byte CJK/ASCII token
  there = garble (wrong JP glyph) or a sunk/thin misaligned glyph.
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
  in `data/**` hex AND in the built ROM's text surfaces (token-scan of `data/**` hex fields + the built ROM's text surfaces —
  procedure in docs/LESSONS_LEARNED.md §G); (2) JP-band cells only for chars that will NEVER appear on a
  trampoline surface (dialogue/bark-only), and their JP tokens must be candidate-ROM
  token-free; (3) paint the cell surgically (WQY 12px raster + drop-shadow grammar per
  docs/TEXT_SYSTEM.md; historically `audit/tools/regen_atlas_v6.py --only-slots`)
  and verify the crop. If WQY renders a glyph badly at 12px
  (e.g. β descender), copy a proven bitmap cell byte-exact instead.
* Growing a pooled string: `apply_idquote_fixes.py --relocate` machinery — ui bank
  heap-safe gaps, resident-cave zero runs, then ledger-vacated spans
  (`data/arenas/relocation_ledger.json` is the anti-double-allocation record;
  NEVER hand out pool bytes without going through it).
* The `zh` fields of `data/names/*.json` are ANNOTATIONS mirroring pool bytes:
  regenerate them with the per-surface decoder (trampoline: renderB for one-byte,
  ZH-band for 2-byte) — a stage-decoder sync writes false 来/メ pollution.

## Verification (what "done" means)

`build/build.py` from a clean tree (strict manifest verify; refresh via
`build/refresh_manifest.py` after intended data changes), then:
1. `test/run_static.py <rom>` — ALL 29 gates, including the ratchets
   (`translation_coverage`, `unit_weapon_names`, `id_command_names`,
   `bank_onebyte_regression`) and `pool_trampoline_tokens` (zero JP-band 2-byte
   tokens in any referenced name-pool string);
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

* `data/` + `build/` + `utils/` — the one-step transform inputs (all that the build
  needs); `data/manifest.json` pins every component + output hash.
* `test/` — the full verification stack (static gates, pixel oracle + parity test,
  offline coverage renderer, live harnesses, `test/golden/` baselines).
* `docs/` — architecture references (TEXT_SYSTEM, ROM_STRUCTURE, STAGE_FORMAT…).
* `audit/` — OPTIONAL and fully REMOVABLE: nothing in `build/`, `utils/`, `test/`
  or `data/` depends on it. It holds the historical analysis/one-time-migration
  scripts and the applied-audit evidence. Its durable knowledge already lives in
  the docs: terminology rulings -> docs/TRANSLATION_GUIDE.md §2b; campaign
  lessons/procedures -> docs/LESSONS_LEARNED.md §G; binding rules -> this file.
  The one standing workflow tool was promoted to `build/refresh_manifest.py`.
