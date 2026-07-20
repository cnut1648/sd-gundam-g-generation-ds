# test/ — the regression test suite

Three tiers, cheapest first.  Run the static tier on EVERY build; run the live
tier before shipping; run the VLM tier when render surfaces changed.

```
tier      entry                                   runtime   needs
-------   -------------------------------------  --------  -----------------------------
static    test/run_static.py <rom>                ~4 s      python venv (ndspy) only
live      test/live/test_*.py <rom>               1–25 min  Xvfb, fluxbox, xdotool,
                                                            xautomation, imagemagick,
                                                            melonDS (see below)
vlm       test/vlm/vlm_judge.py prepare/verdict   minutes   Pillow + a vision judge
                                                            (model or human)
```

All tiers compare against the **Japanese source ROM** (repo root) plus the
baselines in `test/golden/` — never against a previous translated build.

---

## 1. Static tier — `run_static.py`

```bash
.venv/bin/python test/run_static.py sd-gundam-g-generation-zh.nds
.venv/bin/python test/run_static.py <rom> --update-baselines   # refresh ratchets (good builds only!)
.venv/bin/python test/run_static.py <rom> --self-test          # prove the gates can fail
```

Exit 0 iff every gate passes.  One line on what each gate protects against:

| gate | protects against |
|---|---|
| `audio_header` | broken music/SFX — the sound-data streaming header word must stay retail |
| `ui_text_dispatch` | the unit-info/ID screen garble — a NOP at the UI decoder branch renders every UI string as raw glyphs |
| `nameplate_render_path` | illegible 8px speaker nameplates / stray bytes at the patched render-path site |
| `ui_font_atlas_dispatch` | 8px-mush Chinese on the UI-font path — the ZH→atlas trampoline must be intact (or absent) |
| `code_image_parity` | ANY unexplained code/data byte change vs the JP source (the combat-breakage class); allow-list + pointer-repoint rule, forbidden bands can never be allow-listed |
| `dialogue_dict_frozen` | the battle-entry freeze — the dialogue compression dictionary physically overlaps the UI font and must stay byte-identical to JP |
| `font_relocation` | boot crash / unreadable text from a malformed font autoload or an unraised heap floor |
| `relocated_pointer_sanity` | the off-by-N name-relocation (a valid-looking pointer in the wrong record field) that data-aborts mid-stage |
| `charmap_font_consistency` | encoding text to glyph slots the ROM font does not have (renders sparkle) |
| `stage_header_alignment` | the stage-load black screen — 32-bit-loaded stage header tables must stay 4-byte aligned (ARM9 rotates unaligned loads) |
| `stage_file_structure` | stage files overrunning the fixed script buffer / dangling internal pointers after a text grow |
| `stage_script_integrity` | the press-A and mid-stage event freezes — VM opcode model audited, every dialogue advance and reachable event jump stays in-buffer, every stage file control-flow-isomorphic to JP |
| `inline_dialogue_blocks` | overruns of code-embedded dialogue blocks corrupting adjacent event-script bytes (cutscene abort) |
| `event_script_pointers` | the ending/cutscene black screen — inline event jump pointers whose 2nd byte looks like a dialogue marker must survive |
| `battle_voice_structure` | combat voice crash/garble — bark record framing (sub-headers, terminators, head region) byte-identical to JP; only text runs may change |
| `bark_framing` | the garbled-bark class — a single stray byte in a bark inter-sub-line zero gap misparses the next line |
| `untranslated_dialogue` | whole stages silently shipping in Japanese — every reachable dialogue block must render Chinese (audited allowlist exempt) |
| `translation_coverage` | silent translation regression — kana-displacement ratchet vs the baseline floor |
| `glyph_width` | the too-wide-line blank/freeze class — re-encoded UI strings must fit the field the JP fit |
| `field_width_budgets` | ID-box title overflow past the engine's 64px cap and display pointers landing in runtime-heap windows (render live garbage) |
| `label_render_consistency` | mixed-store / mixed-size "floating" glyphs inside one label list |
| `unit_weapon_names` | unit/weapon name garbage (out-of-atlas tokens) or translated-count regression |
| `id_command_names` | ID-command name/summary/detail garbage, squad records reverting to Japanese, coverage regression |

`--self-test` mutates a copy of the ROM under test in seven targeted ways
(garble NOP, dictionary flip, combat-code flip, VM corruption, heap-window
pointer, stage CFG corruption, bark gap stray) and requires the matching gate
to go RED, then runs the translation gates on the JP ROM and requires them RED
— the guard for the guards.

## 2. Live tier — `test/live/`

Every test is standalone with `--help`; shared plumbing in
`test/live/harness.py` (Xvfb + fluxbox + melonDS, held-key input via xte,
window-relative touch via xdotool, `import` window captures, melonDS config
bootstrap: generate-then-patch `~/.config/melonDS/melonDS.toml` — DirectBoot,
JIT off, software renderer, the 12-button keyboard map, optional gdb stub).

```bash
# no-input render smoke (~1 min) — title renders, matches golden, emu at speed
.venv/bin/python test/live/test_boot_render.py <rom>

# interactive boot smoke (~2 min; +--full ~4 min drives to the unit-info page)
.venv/bin/python test/live/test_boot_smoke.py <rom> [--full] [--update-golden]

# dialogue-advance freeze grind (~4 min): New Game -> 150 A-presses through the
# first stage incl. the JOIN choice; FAIL on a frozen window under input
.venv/bin/python test/live/test_dialogue_grind.py <rom>

# 13-tile row-wrap glyph clip (~2 min): Profile char/unit lists (entry + kana-
# category redraw) and the MS development System Tree (entry + selection move);
# native-resolution lower-strip ink probes (an unfixed build scores 0/6)
.venv/bin/python test/live/test_row_clip.py <rom> [--sav PATH]

# in-combat ID cut-in freeze grind (~15-25 min, run 3x for a shipping verdict):
# fresh grind to combat, queue ID commands, battle start; frame-identity + gdb
# ARM9-PC oracles (BIOS abort spin = freeze)
.venv/bin/python test/live/test_combat_cutin.py <rom> [--gdb-port 3333]

# per-stage start->battle-map no-freeze grind (~1 min/stage): WARP into each
# story stage with the descriptor cheat, advance its opening dialogue to the
# deploy/battle map; FAIL if any stage hard-freezes (see below)
.venv/bin/python test/live/test_stage_start.py <rom> [--stages smoke|all|_STG01,_STG04A,...]
```

Exit codes: `0` pass, `1` fail, `2` harness/navigation flake (rerun), `3`
**environment cannot drive game input** (interactive tests only — see below).

### The stage-start warp (`test_stage_start.py`)

`test_dialogue_grind.py` only exercises the FIRST stage; `test_stage_start.py`
proves EVERY story `_STG*.bin` reaches its deploy/battle map without a hard
freeze — the per-file failure the static `stage_script_integrity` gate models
(a corrupted inter-block byte / mis-relocated pointer / mis-aligned header table
data-aborts one stage's opening flow into the BIOS spin at `0xFFFF0104`).

It cannot reach late stages the honest way (clear every prior session), so it
WARPS with a RAM cheat via the instrumented melonDS build's hooks
(`/tmp/melon_poke` forces ARM9 RAM every frame; `/tmp/melon_dump` snapshots it —
the same build that carries `/tmp/melon_inject`).  The cheat is a **stage
descriptor redirect**:

* Boot `test/fixtures/newgame_plus.sav`, Continue → data-load slot 2 (a
  BACK STAGE session).  The session's `_STG` is **preloaded into `0x0232C800`
  at BackStage entry** (not at 進撃), so the redirect must run during the
  save-load → BackStage transition (a savestate is stamped at the load-confirm
  popup and reloaded per stage — created *and* consumed inside one run, the only
  savestate use the harness permits).
* The stage descriptor table (arm9 `0x0217555C`, 101 records × `0x34`, key byte
  at record+0) is searched by the save's current-stage id (`0x0227CC48`); the
  matched record's file is loaded.  Poking the id copies (`0x0227CC48` /
  `0x0227CE55` / the proximate `0x0227CC64`) does **not** redirect — the loader
  overwrites `0x0227CC64` in the same frame it reads it, from an event-VM value.
  What works: overwrite the *matched record* with the target record's bytes
  (keeping its key byte) while the save loads.  Record `i` previews the `_STG`
  at FAT pos `i-2` (a session *card*) and 進撃 plays FAT pos `i-1` (the session)
  — the game's real card→session pairing — so to play session `_STGxx` (FAT pos
  P) the test redirects to record `P+1` and confirms the played buffer by
  dump+match.

Per stage the verdict is **PASS** (reached the battle/deploy map — the only
clean result, never a pass on timeout alone), **FREEZE** (window static under
continuous A presses AND the map never reached — the hard-freeze signature),
**UNREACHED** (map not reached but frames kept changing — inconclusive, rerun)
or **WARP-FAIL** (the redirect flaked — not a ROM verdict).  `--stages` takes a
`_STG` name list or a preset (`smoke` default, `routes`, `sp`, `x`, `all`); the
warp/dump hooks are single global `/tmp` files so stages run sequentially in one
booted session (no parallel displays).  Needs the instrumented melonDS (else
exit 3, like the input preflight).

### The input preflight (exit 3)

Interactive tests first verify the environment can actually drive the game
(press START on the title until the menu appears).  Every host input layer can
be healthy — X delivers the key, melonDS maps it, the emulated KEYINPUT
register reflects it — and the game can still ignore input if the emulated
ARM7-side pad service never starts (an emulator/host fault that hits every
ROM including the untouched Japanese one).  In that state an interactive test
exits 3 WITHOUT a ROM verdict instead of failing the build; the no-input
`test_boot_render.py` still gates boot/render integrity.  Savestate-based
shortcuts are deliberately not used as a fallback: a savestate restores
another build's code+data into RAM and would test the wrong bytes.

### Fixtures

`test/fixtures/` ships two cartridge saves (early Session-00 sortie;
deep New-Game+ post-X1 at the strategy map) — see `test/fixtures/README.md`.

## 3. VLM tier — `test/vlm/`

The authoritative RENDER verdict is a strict vision judge (any strong
vision-language model, or a careful human) applying the fixed rubric in
`test/vlm/judge_prompt.md` to labelled, point-filter-upscaled crops.  The
runner needs no model access or API key:

```bash
# 1) turn raw window captures (any live test's out dir) into a judging bundle
.venv/bin/python test/vlm/vlm_judge.py prepare --shots <dir> --out <bundle> [--crops spec.json]
# 2) give <bundle>/prompt.txt + the images to the judge; collect one line per image:
#      <name>.png: CLEAN|BROKEN|RESIDUAL — <reason>
.venv/bin/python test/vlm/vlm_judge.py lines-to-json --lines answers.txt --out verdicts.json
# 3) gate: PASS iff every crop judged and none BROKEN (RESIDUAL = tracked-pass)
.venv/bin/python test/vlm/vlm_judge.py verdict --bundle <bundle> --verdicts verdicts.json
```

An unjudged/missing screen is FAIL, never skip (reproduce-then-gate).

---

## Dependencies

* **Python**: the repo venv (`.venv/bin/python`) with `ndspy`, `Pillow`,
  `numpy` (all tiers).
* **X/automation** (live tier):
  `apt install xvfb fluxbox xdotool xautomation imagemagick x11-utils gdb-multiarch`
* **melonDS 1.1** at `/usr/local/bin/melonDS` (override: `MELONDS_BIN`).
  Build from source (Ubuntu 22.04, needs Qt6):

  ```bash
  apt install build-essential cmake ninja-build git pkg-config \
      qt6-base-dev qt6-base-private-dev qt6-multimedia-dev libqt6svg6-dev \
      libsdl2-dev libslirp-dev libpcap-dev libarchive-dev libzstd-dev \
      libepoxy-dev libfaad-dev libenet-dev extra-cmake-modules libgl1-mesa-dev
  git clone --depth 1 -b 1.1 https://github.com/melonDS-emu/melonDS
  cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DENABLE_GDBSTUB=ON -DENABLE_WAYLAND=OFF -DUSE_SYSTEM_LIBSLIRP=ON
  cmake --build build && install -m755 build/melonDS /usr/local/bin/
  ```

  The harness generates-then-patches `~/.config/melonDS/melonDS.toml` on first
  run (a fully hand-written file crashes melonDS's config serializer; fresh
  configs leave every DS button unbound so synthesized keys would be dropped —
  the harness sets the 12-button map itself).

## Reproducibility notes

* melonDS with JIT off + software renderer + DirectBoot replays
  deterministically: a good build self-compares at MAE ≈ 0 against its own
  goldens; compare thresholds sit an order of magnitude above replay noise.
* Live tests always boot fresh from the cartridge image; each run gets a
  private workdir/display; the emulator is killed at the end of every run.
* Screenshot compares run at native resolution (a coarse downscale averages
  per-glyph garble away); frame-change/freeze detection uses a 64px downscale.
* The golden ROM sizes ~30 MB (trimmed).  A 32 MiB variant padded with 0xFF
  behaves identically under melonDS; flashcart users may need the padded one.
