# TESTING_APPROACH — the testing philosophy this project converged on

Nearly a hundred iterations of shipping, breaking and repairing this translation produced a
very specific testing doctrine. It is written here as *what must exist and why*; the
concrete suite lives under `test/` (`test/run_static.py` static gates, `test/live/` emulator
tests, `test/vlm/` the screenshot-judge harness, `test/golden/` baselines, `test/fixtures/`
saves/input scripts).

The one-line doctrine: **static byte gates prove structure, live emulator runs prove
behavior, and the render verdict is the VLM judge on the actual crop — never a static/byte
check alone, and never a bare pixel metric.**

---

## 1. Static gates (fast, deterministic, every build)

Byte-level invariants over the built ROM, JP-anchored wherever possible (the Japanese
original is a known-good oracle, giving 0-false-positive checks):

* **Container/audio**: ROM header `[0x60:0x64] == 57 66 41 00` (the audio-critical ROMCTRL
  field); arm7/banner/untouched-file identity; component sha1s vs. the build manifest.
* **Combat safety (byte identity where it matters)**: every arm9 byte outside the
  documented translation/patch regions must equal the original; every changed dialogue block
  must be an intended, well-formed change; the battle-voice files change only inside known
  record budgets. "Documented region" means an explicit, annotated allowlist — an arm9 diff
  outside it fails.
* **Dialogue block integrity**: every `0x15` block opens correctly and its terminator
  survives; token-aware scanning; non-final-segment padding rules respected.
* **Script-pointer integrity**: every `13 <abs ptr>` jump valid in JP remains valid; no
  text baked over VM operands.
* **CFG isomorphism**: each stage file's script control-flow graph is isomorphic to the JP
  original's (the only reliable anti-hang check — out-of-range-jump scans miss in-range
  corruption).
* **Alignment**: the five `ldr`-accessed stage-header tables are 4-byte aligned in all 101
  stage files (unaligned loads *rotate* on ARM9 — a class invisible to pointer validation).
* **Relocation-pointer semantics**: any arm9 word pointing into a relocated string pool must
  correspond to a JP word that pointed into the original string/data section (catches
  off-by-N relocations that are "valid pointers in the wrong field").
* **Pointer-destination safety**: no display-string pointer resolves into the volatile
  RAM band (the heap/work-buffer growth zone) — strings live resident or in the safe pool.
* **Width budgets**: every re-encoded string's rendered width ≤ the JP it replaces
  (12 px/atlas glyph vs 8 px renderB, dictionary macros expanded before counting), plus
  per-field pixel budgets for the known-tight surfaces.
* **Charmap/font consistency**: every encoded slot < the ROM's actual atlas slot count
  (the rasterizer has no bounds check); added glyph bitmaps bit-match the canonical font
  render; no token whose low byte is 0x00/0x15.
* **Bark framing**: all-zero inter-sub-line gaps and intact `05 .. 00 06 ..` sub-headers,
  anchored to the JP framing.
* **Untranslated-text (reachability-based)**: every CFG-reachable display block free of
  JP-only tokens (kana, JP dict refs, JP-band kanji) outside the audited intentional-JP
  allowlist. Note the false-positive trap this dodges: shared narrow single-byte hanzi are
  correct Chinese.
* **Name identity**: every displayed name equals the canonical translation *of that
  record's* real JP name (catches right-string-wrong-record mis-keying).
* **Coverage ratchets**: translated-fraction floors (chars displaced, kana remaining) that
  only ever rise, captured only from fully-gated builds.

Design rules for gates, learned the hard way:
* **Every gate ships with a RED→GREEN self-test** against a preserved known-bad artifact —
  a gate that never demonstrably failed usually tests nothing.
* **Anchor to JP, not to thresholds** (thresholds mis-calibrated on clean input caused
  fifteen rounds of wrong render theories).
* **A gate is necessary, not sufficient**: static freeze "guards" cannot certify
  freeze-free (runtime over-reads, heap clobbers and rotated loads are invisible); the live
  layer exists for that.

## 2. Live emulator tests (`test/live/`)

Run headless (Xvfb) against melonDS with deterministic scripted input. Levels:

* **Boot smoke**: fresh DirectBoot → title renders (luma/structure sanity, not black) →
  New Game → intro → first dialogue scene renders; framebuffer changes across inputs
  (a hard freeze gives identical frames).
* **Freeze grinds**: scripted fresh-boot runs that reach the historically dangerous scenes —
  deploy → combat → **ID cut-in** (the cut-in codec is the classic freeze site), player-phase
  command UI (exercises the renderB path), stage-load from the BackStage. Oracles:
  frame-identity over a window (with a generous still-threshold — static map screens
  false-positive short ones) AND the abort-PC signature (`0xFFFF0104` BIOS spin / abort in
  the FS-memcpy cluster). Multiple independent runs; a run counts only if it *reached* the
  target scene (scene-reach detection — "didn't get there" is a harness failure, not a pass;
  one real crash was misfiled as a nav flake for a whole round).
* **Scenario drives with owner saves** (`test/fixtures/`): the crashes that mattered were
  save-state-dependent (New-Game+ stage entry, the post-final-stage ending, deep-campaign
  heap states, partial no-cheat rosters). Fixtures must include: an NG+ back-stage save, a
  late-campaign big-roster save, a no-cheat partial-roster save, and the specific
  owner-reported repro saves. JP-ROM-with-same-save is the control oracle for any crash.
* **Harness hygiene** (all bought with pain): fresh boot for anything arm9/render-related —
  savestates restore old code/data/atlas and mask fixes; per-instance isolated HOME/display;
  held key presses (~120 ms); consistent machine load for timing-sensitive repros with an
  in-batch known-bad control; kill only your own emulator instance.
* **When deeper introspection is needed**: gdb watchpoints do not fire in melonDS/DeSmuME
  and the gdb stub wedges under load — the working pattern is an *instrumented emulator
  build* (env-gated VRAM/RAM write loggers, abort-time dumps, file-based input/poke/dump
  hooks). Keep such a build recipe in `test/live/` docs; it is the difference between hours
  and weeks on "who wrote this byte" questions.

## 3. Screenshot goldens + VLM-as-judge (`test/vlm/`)

Pixel metrics (MAE vs. golden, sparkle counts) are **pre-filters only**. They miss whole
defect classes (ghosting, overflow, baseline drift, wrong-but-clean glyphs) and they
false-positive on legitimate JP→ZH change. The authoritative render verdict is a **vision
model judging the actual crops**:

* Capture **full-screen crops** (whole 256×192 at 3–4×) *and* tight per-field crops.
  Judge layout defects on the full screen — a glyph overflowing into the neighbor column is
  literally cropped out of a tight per-field crop (a blindness that hid the owner's
  recurring complaints for rounds).
* Judge against an explicit defect vocabulary: OVERFLOW / CUT-OFF / OVERLAP / OVERPAINT /
  BASELINE-or-SIZE mismatch / STRAY-or-garbage glyphs / WRONG SCRIPT. "If in doubt, call it
  BROKEN with the rule number."
* Track **known residuals** explicitly (documented, size-bounded imperfections answer
  RESIDUAL, not BROKEN) so the gate stays honest without going blind.
* Golden screenshots: captured from gated builds only; compare at native crop resolution
  (downscaling averages per-glyph garble away); refresh goldens only on *intended* change.
* When two judges disagree about a glyph, decode the ROM bytes — don't vote.
* Judge fresh captures only (stale crop directories misled multiple investigations).

## 3.5 The offline pixel oracle — full-game coverage without a playthrough

Emulator navigation covers a handful of scenes per hour; the game has ~22k text lines.
The scalable layer is `test/render_oracle.py`: a reimplementation of BOTH render paths
(renderA 12×12 atlas; trampoline renderB 8×16 from arm9; one/two-byte tokens; F0-macro
expansion) that draws any line pixel-faithfully in microseconds.

* **Trust anchor**: `test/test_render_oracle_parity.py` compares the oracle against a
  LIVE melonDS golden (stroke-mask IoU ≥ 0.80 on the first dialogue line). The oracle is
  only trusted because a live capture agrees with it; recheck parity whenever the render
  code or the atlas pipeline changes.
* **Coverage runner**: `test/coverage_render.py <rom> --out DIR [--sheets]` renders EVERY
  line from EVERY text surface (~0.5 s total) and emits:
  - `findings.json` — algorithmic defect classes needing zero judgment:
    `unknown_slot` (sparkle), `empty_glyph`, `box_violation`, `no_shadow`,
    `mixed_style` (bank-surface line drawing renderB TEXT glyphs next to atlas CJK —
    the 吉翁海兵→吉翁海無 garble / NT等级4 sunk-digit class, detected from bytes alone);
  - `sheets/*.png` — labeled contact sheets of every unique line for judge fleets;
  - `corpus.jsonl` — machine-readable per-line style reports.
* **Judgment fan-out**: what algorithms cannot decide (glyph identity, ugliness,
  semantics) goes to parallel subagent/VLM judges over the sheets, ~40 lines per sheet,
  with verdicts returned as JSON and fixes applied at the SOURCE (charmap/atlas/data),
  per AGENTS.md. The renderB 8×16 charset was mapped this way
  (word-level proof recorded in `data/renderb_charset.json`) — with it, `mixed_style` findings resolve
  semantically: renderB glyph == intended char ⇒ authentic JP styling; ≠ ⇒ garble.
* The **freeze class** stays with the live tier (script-CFG gate statically + boot smoke /
  dialogue grind / combat cut-in dynamically); the oracle proves pixels, not control flow.

## 4. The escalation ladder (how the layers compose)

For any change: static suite → **offline coverage run (every line, every glyph)** → boot
smoke → targeted live scenario (if the change touches render/combat/stage data) → VLM
judging of the affected surfaces → (for anything freeze-adjacent) multi-run freeze grind
with control. For any *owner-reported* defect: reproduce live first (on their save),
root-cause to bytes, fix, then **add the class gate with a self-test** and only then ship.
The gate count only ever grows; that is by design — each gate is a fossilized postmortem.

Two final principles:

* **"All gates green" is a claim about the classes we've seen.** New defect classes ship
  through green gates (they did, repeatedly); the response is never "the gates passed" but
  "which gate was blind, and what's its self-test".
* **Byte-identity is the ultimate regression gate for this repo**: the rebuilt ROM must
  equal the shipped golden ROM sha1-for-sha1 (whole-ROM and per-component). Any
  intentional divergence in the future invalidates that check *deliberately*, with the
  full ladder above standing in behind it.
