#!/usr/bin/env python3
"""Generate the phase 2 (library) + phase 3 (stages) fleet assignments.

Per TRANSLATION_BRIEF_STAGES.md, each stage agent gets a TRANSLATE_OFFSETS
list = the pixel-verified v1.1 dialogue set (reach_oracle.must_reach) UNION the
proven-VM-reached display blocks (staging/vm_reach/agent_1/reach.py, the
99.79%-complete model), intersected with the dump's actual block starts (an
agent can only translate text that exists in data/jp).  VM-reached offsets that
are NOT dump blocks are recorded in assign/extractor_gap.json (extractor gap to
fix at its source later; out of fleet scope).

Writes (all under audit/translate2/staging/assign/):
  stages/<stem>.json   one per stage: translate_offsets + briefing offs + title
  lib/weapon_<nn>.json one per weapon batch: the exact `off` list
  queue.json           full fleet task inventory (largest-first per type)
  shared_strings.json  briefing offs + titles shared by several stages
  extractor_gap.json   VM-reached offsets absent from data/jp
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "audit/translate2"))
sys.path.insert(0, str(REPO / "audit/translate2/staging/vm_reach/agent_1"))

from reach_oracle import must_reach  # noqa: E402
import reach as R  # noqa: E402

ASSIGN = REPO / "audit/translate2/staging/assign"
(ASSIGN / "stages").mkdir(parents=True, exist_ok=True)
(ASSIGN / "lib").mkdir(parents=True, exist_ok=True)
(REPO / "audit/translate2/staging/out/lib").mkdir(parents=True, exist_ok=True)
(REPO / "audit/translate2/staging/out/stages").mkdir(parents=True, exist_ok=True)


def jdump(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")


# ---------------------------------------------------------------- stages ----
mr = must_reach()
queue = []
shared_brief = {}
shared_title = {}
gap = []

# pass 1: collect per-stage data + briefing ownership (first stage in sorted
# order OWNS a shared briefing off — the phase-1 one-owner-per-record principle)
stage_data = {}
brief_owner = {}
for p in sorted((REPO / "data/jp/stages").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    stem = p.stem
    stage_data[stem] = d
    seen = set()
    for b in d.get("briefing", []):
        off = b["off"]
        if off in seen:
            continue
        seen.add(off)
        brief_owner.setdefault(off, stem)
        shared_brief.setdefault(off, []).append(stem)

for stem, d in stage_data.items():
    fname = stem + ".bin"
    v11 = mr.get(fname, set())
    vm = R.reached(fname)
    want = v11 | vm
    # dump order, exact off strings from the dump
    offs = [b["off"] for b in d["blocks"] if int(b["off"], 16) in want]
    have = {int(b["off"], 16) for b in d["blocks"]}
    for miss in sorted(vm - have):
        gap.append({"stage": stem, "off": hex(miss),
                    "why": "VM-reached display block absent from data/jp dump"})
    briefing, brief_ctx, seen = [], [], set()
    for b in d.get("briefing", []):
        off = b["off"]
        if off in seen:
            continue
        seen.add(off)
        (briefing if brief_owner[off] == stem else brief_ctx).append(off)
    title = d["descriptors"][0]["title"]
    # non-text suspects among the assigned offsets (glyph-priming smears the
    # brief tells the agent to flag-and-skip): give the agent a heads-up list
    gar = re.compile(r"\{SLOT:\d+\}|\{B:\d+\}|\{F0:\d+\}|□")
    bm = {b["off"]: b for b in d["blocks"]}
    suspects = [o for o in offs
                if gar.search(bm[o]["text"]) or "あいうえお" in bm[o]["text"]]
    jdump(ASSIGN / "stages" / f"{stem}.json", {
        "stage": stem,
        "jp_file": f"data/jp/stages/{stem}.json",
        "title_jp": title,
        "briefing_offs": briefing,
        "briefing_owned_elsewhere": brief_ctx,
        "translate_offsets": offs,
        "n_blocks": len(offs),
        "suspect_nontext": suspects,
        "derivation": "v1.1 pixel-verified (reach_oracle) UNION proven-VM reach"
                      " (vm_reach/agent_1), dump block order; shared briefing"
                      " offs owned by the first stage (sorted) that lists them",
    })
    shared_title.setdefault(title, []).append(stem)
    queue.append({"id": f"stage:{stem}", "kind": "stage", "stage": stem,
                  "n": len(offs) + len(briefing) + 1})

# --------------------------------------------------------------- library ----
chars = json.loads((REPO / "data/jp/characters.json").read_text(encoding="utf-8"))
units = json.loads((REPO / "data/jp/units.json").read_text(encoding="utf-8"))
lib = json.loads((REPO / "data/jp/library.json").read_text(encoding="utf-8"))

for c in chars["characters"]:
    if c.get("bio") and c["bio"].get("text"):
        queue.append({"id": f"char:{c['cid']}", "kind": "char_bio",
                      "cid": c["cid"], "name_jp": c["name"]["text"],
                      "n": len(c["bio"]["text"])})
for i, b in enumerate(chars.get("unassigned_bios", [])):
    queue.append({"id": f"char_orphan:{b['index']}", "kind": "char_bio_orphan",
                  "orphan_index": b["index"], "name_jp": "(orphan bio)",
                  "n": len(b["text"])})
for u in units["units"]:
    if u.get("bio") and u["bio"].get("text"):
        queue.append({"id": f"unit:{u['utid']}", "kind": "unit_bio",
                      "utid": u["utid"], "name_jp": u["name"]["text"],
                      "n": len(u["bio"]["text"])})

wl = [w for w in lib["weapon_list"] if w.get("reachable", True)]
NBATCH = 8
per = (len(wl) + NBATCH - 1) // NBATCH
for nn in range(NBATCH):
    batch = wl[nn * per:(nn + 1) * per]
    if not batch:
        continue
    jdump(ASSIGN / "lib" / f"weapon_{nn:02d}.json", {
        "batch": f"{nn:02d}",
        "jp_file": "data/jp/library.json",
        "offs": [w["off"] for w in batch],
    })
    queue.append({"id": f"weapon:{nn:02d}", "kind": "weapon_list",
                  "batch": f"{nn:02d}", "n": len(batch)})

# largest-first inside each kind; stages first overall (longest poles)
order = {"stage": 0, "char_bio": 1, "char_bio_orphan": 1, "unit_bio": 2,
         "weapon_list": 3}
queue.sort(key=lambda t: (order[t["kind"]], -t["n"]))
jdump(ASSIGN / "queue.json", queue)
jdump(ASSIGN / "shared_strings.json", {
    "briefing": {k: v for k, v in sorted(shared_brief.items()) if len(v) > 1},
    "titles": {k: v for k, v in shared_title.items() if len(v) > 1},
})
jdump(ASSIGN / "extractor_gap.json", gap)

n = {"stage": 0, "char": 0, "unit": 0, "weapon": 0}
for t in queue:
    n[t["id"].split(":")[0].split("_")[0]] += 1
print(f"queue: {len(queue)} tasks "
      f"(stages {n['stage']}, char bios {n['char']}, unit bios {n['unit']}, "
      f"weapon batches {n['weapon']})")
print(f"extractor gap offsets: {len(gap)}")
print(f"total stage offsets: "
      f"{sum(t['n'] for t in queue if t['kind'] == 'stage')}")
