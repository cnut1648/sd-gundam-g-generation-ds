# Phase 2+3 fleet orchestration (session runbook)

Goal: translate ALL of `assign/queue.json` (622 tasks: 101 stages, 273 char
bios + 1 orphan, 239 unit bios, 8 weapon batches) via subagents — one subagent
per stage / per library entity — keeping **40 running at all times** until the
queue drains.

## Files

* `staging/assign/queue.json` — task inventory (largest-first per kind).
* `staging/assign/stages/<stem>.json` — per-stage TRANSLATE_OFFSETS (= v1.1
  pixel-verified ∪ proven-VM reach), owned briefing offs, suspect list.
* `staging/assign/lib/weapon_<nn>.json` — weapon batch off lists.
* `staging/assign/extractor_gap.json` — 8 VM-reached offsets absent from the
  dump (_STGSP5S ×3, _STGSP7A ×3, _STGX6B ×2): report to owner; extractor fix,
  NOT fleet scope.
* `staging/out/stages/<stem>.json`, `staging/out/lib/{char_<cid>,unit_<utid>,
  weapon_<nn>,char_orphan_<idx>}.json` — one output per task ("done" marker).
* `python3 audit/translate2/fleet_next.py --status | N` — pending queue view.
* `python3 audit/translate2/check_p23.py <out-file>|--all` — the mechanical
  gate; every agent must end on PASS, orchestrator re-runs it on completion.
* `staging/fleet_p23_ledger.json` — subagent_id -> task map (orchestrator).

## Subagent protocol (what each prompt says)

general-purpose agent, inherited model, background; prompt = task id + the
pointers: read the brief (`TRANSLATION_BRIEF_STAGES.md` or `_LIBRARY.md`)
fully, read the assignment file, read the JP from `data/jp/...`, use terms.md +
web_search, write EXACTLY one output file, run check_p23.py until PASS.
Never edit other files, never build, never open images.
Stage extras: assignment `translate_offsets` IS the brief's TRANSLATE_OFFSETS;
`briefing_offs` are the owned subset (shared lines are owned by exactly one
stage — the output must contain exactly these); `suspect_nontext` = likely
flag-and-skip blocks (put skips in "skipped" + explain in notes).

## Refill loop (keep 40 alive)

1. `get_command_or_subagent_output` (non-blocking or short timeout) on live ids.
2. For each finished id: re-run check_p23.py on its file.
   * PASS -> mark done in ledger.
   * FAIL/missing -> `spawn_subagent(resume_from=id)` with the error list
     (same type), or respawn fresh if resume impossible. Max 2 retries, then
     orchestrator fixes by hand or quarantines with a note.
3. Spawn next pending tasks (fleet_next.py order) to bring live count to 40.
4. Repeat until `fleet_next.py --status` shows 0 pending and all validate.

## Wave 1 composition

30 largest stages + 4 char bios + 4 unit bios + 2 weapon batches — all three
task kinds are exercised immediately so brief/validator gaps surface in wave 1
before mass production.

## End of campaign

* `check_p23.py --all` → 622/622 PASS.
* Report: extractor_gap.json handoff, new-term collection from `notes`
  (fold into terms.md is a FOLLOW-UP owner decision, not fleet scope),
  length-overflow flags for the apply step.
* NO repo files outside audit/translate2/staging/out/** were touched; apply to
  data/cn is a later, separate step (per the briefs).

## Checkpoint (auto-updated during campaign)

Progress marker: stages 101/101 DONE (all PASS). Weapon batches 00-07 assigned
(00/01 done, 02-07 in flight). Char bios: ~105 done, rest queued largest-first.
Unit bios: not started (queue after char bios). All outputs to date PASS
check_p23.py. Two premature-end agents were resumed successfully via
resume_from (_STG06SP, _STGSP4B) — watch for ~1-2% of agents ending after a
truncated first message; resume_from with "continue where you left off" fixes
them. Cross-fleet naming variants (宿敌/对手路线, 巴基露露/巴基露尔, 太阳激光炮/
太阳射线, 十字先锋/海盗先锋军, 曼特纳/梅因特纳/维护者, 月之民/月之种族…) are
deliberately left to the apply-time settle pass — each agent recorded its choice
+ variants in "notes"; do NOT hand-fix staging files.

## CAMPAIGN COMPLETE (2026-07-18)
All 622 tasks done and validated: 101 stages, 8 weapon batches, 274 char bios (273 cids + 1 orphan), 239 unit bios.
Final sweep: check_p23.py --all = 622/622 PASS; ledger live=0 done=622.
Open handoffs: (1) staging/extractor_gap.json — 8 VM-reached offsets missing from data/jp dump (_STGSP5S x3, _STGSP7A x3, _STGX6B x2): extractor bug, fix in utils/extract + regenerate dump before apply; (2) cross-fleet naming-variant settle pass at apply time (variants recorded in each output's "notes"); (3) new-mint hanzi (e.g. 羁绊) to be minted into charmap/atlas at apply time.

## LIBRARY BAKE COMPLETE (2026-07-18)
Applied the 521 lib staging outputs into the ROM:
* glyph supply: 47 minted cells (1 ZH-band reclaim + 46 candidate-ROM token-free
  JP-band cells, WQY-painted per §G) + 31 identity-true promotions; charmap
  decode identities (slot_chars_extra/jp_slot_chars) restored — encode-only change.
* reword fleet: 36 agents eliminated the 133 unmintable low-frequency chars
  from 107 staged files (synonyms/variant transliterations, notes updated),
  reword_check.py + check_p23 622/622 PASS.
* new bio_bank format (data_files.py + arm9_layout._apply_bio_offsets +
  layout.CHAR/UNIT_BIO_SIZE_WORD): full-bank rebuild in index order, grown
  records, arm9 offset tables + resource size words derived at build time
  (cut-in pattern); reconciler taught the new format; 2 size words added to
  arm9_allowed_regions.
* coverage: 324.bin 274/274 records translated (58 phase-1 + 216 fleet, incl.
  orphan 91), c4b.bin 239/239 (51 phase-1 + 188 fleet). 31e weapon list left
  as shipped (phase-1 short names; staged variants deferred to settle pass).
* verify: build d1c9353b43d8cffb7124996d4b7f808aa7976219 (30,349,672 B),
  pad32m 1b4cc9f204a242ca09bc72384f8f4558faed5278; 32/32 static gates incl.
  offline_coverage (25,696 lines, 0 new findings); boot smoke 5/5; README pins
  updated. Open: stage texts (phase 3) not applied; naming settle pass pending.
