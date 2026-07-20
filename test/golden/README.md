# test/golden — baselines and reference images for the gate suites

Everything here is either (a) a machine-checkable baseline consumed by
`test/run_static.py`, or (b) a reference image consumed by the live tier.
Baselines marked *ratchet* are recaptured with
`run_static.py <rom> --update-baselines` — only ever from a build that itself
passed every other gate (that is how progress gets locked in).

| file | consumed by | contents |
|---|---|---|
| `arm9_allowed_regions.json` | `gate_code_image_parity` | The annotated map of code-image (arm9) regions where a translated build may differ from the Japanese source: data banks (glyph atlas, UI dictionary, UI font, name pools), documented render/code patches (each with a one-line "what"), and three **forbidden** bands that may never be allow-listed (the unit-thumbnail bank, dialogue compression dictionary, and stage-script VM dispatch). Any diff outside these regions and outside the pointer-repoint rule fails the build. |
| `dialogue_jp_allowlist.json` | `gate_untranslated_dialogue` | The audited set of reachable dialogue payloads that legitimately stay Japanese (staff credits with JP proper nouns, layout-locked tutorial section headers, scream onomatopoeia, non-dialogue data the static reachability walk over-approximates into). Keyed by raw payload hex; each entry says why it is exempt. |
| `speaker_name_cells.json` | `gate_glyph_width` | Per pilot record: the glyph-cell count of the character's original katakana name — the widest string the dialogue nameplate field is designed to hold. Bounds re-pointed Chinese pilot names. |
| `coverage_baseline.json` | `gate_translation_coverage` | *Ratchet.* The translation-coverage floor (CHAR/KANA displacement percentages and per-store kana counts) captured from the shipped ROM. A build whose coverage drops below any metric fails. |
| `names_baseline.json` | `gate_unit_weapon_names`, `gate_id_command_names` | *Ratchet.* Floors for translated unit/weapon name counts and ID-command name/summary/detail counts, plus the play-test squad records that must render Chinese. |
| `title.png` | `test/live/test_boot_render.py` | Golden title-screen window capture (256x403 melonDS window). The title art is untouched by the translation, so this golden is stable; a corrupted boot scores far above the compare threshold. Verified against the shipped ROM on this rig. |
| `join_choice_template.png` | live grinds | Template of the ally-JOIN choice box in the first stage — the navigation anchor the combat grind uses to answer the choice deterministically. |
| `dialogue_scene.png`, `info.png` | `test/live/test_boot_smoke.py` | *Not shipped pre-captured.* First-run captures: created by `test_boot_smoke.py --update-golden` (and `--full --update-golden` for the info page) on a rig whose input preflight passes, then committed. The boot smoke SKIPs the compare (with a loud message) while they are absent. |

## Regeneration

* `coverage_baseline.json`, `names_baseline.json`:
  `.venv/bin/python test/run_static.py <shipped.nds> --update-baselines`
* `title.png`: `.venv/bin/python test/live/test_boot_render.py <shipped.nds> --update-golden`
* `dialogue_scene.png` / `info.png`:
  `.venv/bin/python test/live/test_boot_smoke.py <shipped.nds> --update-golden [--full]`
* `arm9_allowed_regions.json`, `dialogue_jp_allowlist.json`,
  `speaker_name_cells.json`: hand-maintained knowledge bases — extend them only
  with a documented reason per entry (they are the teeth of the suite; a broad
  entry weakens every future run).
