#!/usr/bin/env python3
"""Print the next N pending fleet tasks (queue order), skipping tasks whose
output file already exists. Usage: fleet_next.py [N] [--status]"""
import json
import sys
from pathlib import Path

STG = Path(__file__).resolve().parent / "staging"
queue = json.loads((STG / "assign/queue.json").read_text())


def out_path(t):
    k = t["kind"]
    if k == "stage":
        return STG / "out/stages" / (t["stage"] + ".json")
    if k == "char_bio":
        return STG / "out/lib" / f"char_{t['cid']}.json"
    if k == "char_bio_orphan":
        return STG / "out/lib" / f"char_orphan_{t['orphan_index']}.json"
    if k == "unit_bio":
        return STG / "out/lib" / f"unit_{t['utid']}.json"
    if k == "weapon_list":
        return STG / "out/lib" / f"weapon_{t['batch']}.json"
    raise ValueError(k)


ledger_p = STG / "fleet_p23_ledger.json"
inflight = set()
if ledger_p.exists():
    inflight = set(json.loads(ledger_p.read_text())["live"].values())
pend = [t for t in queue if not out_path(t).exists() and t["id"] not in inflight]
if "--status" in sys.argv:
    done = len(queue) - len(pend)
    by = {}
    for t in pend:
        by[t["kind"]] = by.get(t["kind"], 0) + 1
    print(f"done {done}/{len(queue)}; pending by kind: {by}")
    sys.exit(0)
n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
for t in pend[:n]:
    print(json.dumps(t, ensure_ascii=False))
