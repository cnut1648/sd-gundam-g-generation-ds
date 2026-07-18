#!/usr/bin/env python3
"""Reconcile the fleet ledger against disk: any 'live' task whose output file
exists AND passes check_p23 is moved to 'done'. Prints stragglers (live with
no passing file) so the orchestrator can poll/resume just those."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STG = HERE / "staging"
led = json.loads((STG / "fleet_p23_ledger.json").read_text())


def out_path(task: str) -> Path:
    kind, _, key = task.partition(":")
    if kind == "stage":
        return STG / "out/stages" / f"{key}.json"
    if kind == "char":
        return STG / "out/lib" / f"char_{key}.json"
    if kind == "char_orphan":
        return STG / "out/lib" / f"char_orphan_{key}.json"
    if kind == "unit":
        return STG / "out/lib" / f"unit_{key}.json"
    if kind == "weapon":
        return STG / "out/lib" / f"weapon_{key}.json"
    raise ValueError(task)


moved, stragglers = 0, []
for sid, task in list(led["live"].items()):
    p = out_path(task)
    if p.exists():
        r = subprocess.run([sys.executable, str(HERE / "check_p23.py"), str(p)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            led["done"][sid] = led["live"].pop(sid)
            moved += 1
            continue
    stragglers.append((sid, task))

(STG / "fleet_p23_ledger.json").write_text(
    json.dumps(led, ensure_ascii=False, indent=1))
print(f"moved {moved} to done; live={len(led['live'])} done={len(led['done'])}")
for sid, task in stragglers:
    print("STRAGGLER", sid, task)
