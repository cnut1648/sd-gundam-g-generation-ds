#!/usr/bin/env python3
"""Post-apply verification: build a candidate ROM from the edited data/, then
prove every accepted fleet proposal decodes at its final address.

  1. build ROM (skip-verify) -> /tmp/v12_candidate.nds
  2. re-export entity briefs from the candidate (same exporter as staging)
  3. for every accepted (status ok) proposal: candidate *_current must equal
     the proposal text (names/cutins/details normalized through the same
     decoders); report every mismatch with addresses.

This is the cheap all-arithmetic check that runs BEFORE the gate suite.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
STG = HERE / "staging"
CAND = Path("/tmp/v12_candidate.nds")
CSTG = Path("/tmp/v12_candidate_staging")


def build_candidate():
    r = subprocess.run(
        [sys.executable, "build/build.py",
         "0098 - SD Gundam G Generation DS (Japan).nds", str(CAND),
         "--skip-verify"],
        cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit("candidate build FAILED")
    print("candidate ROM built")


def export_candidate():
    (CSTG / "chars").mkdir(parents=True, exist_ok=True)
    (CSTG / "units").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ZH_ROM=str(CAND), STAGING_OUT=str(CSTG))
    r = subprocess.run([sys.executable, "audit/translate2/export_entities.py"],
                       cwd=REPO, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit("candidate export FAILED")
    print("candidate export done:", r.stdout.strip())


def norm(s):
    return (s or "").replace("▼", "").strip()


sys.path.insert(0, str(HERE))


def detail_expect(didx, effect):
    """Compose the effect through the applier's own pipeline (incl. the
    compact-style fallback) so the check tests placement, not style."""
    import apply_fleet as AF
    from utils.extract import walkers as W
    if not hasattr(detail_expect, "_jd"):
        detail_expect._jd = {d["didx"]: d for d in W.id_details(AF.jp)}
    full = AF.compose_detail_text(didx, effect, detail_expect._jd, compact=False)
    comp = AF.compose_detail_text(didx, effect, detail_expect._jd, compact=True)
    strip = lambda t: t.replace("{00}", "").replace("{01}", "")
    return {strip(full), strip(comp)}


def special_expect(kind, index, segs):
    """Render staged 1df/1e0 segments the way the candidate export will show
    them: compose through the applier (JP-macro/one-byte reuse, {F0:n}/{01}
    escapes become bytes), then split/decode exactly like the walker.  This
    makes the comparison notation-exact ({F0:94} vs NT, {01}, ＋ vs +)."""
    import apply_fleet as AF
    from utils.extract import walkers as W
    from build.build_guide import decode_text
    if not hasattr(special_expect, "_j"):
        special_expect._j = W.special_records(AF.jp)
    recmap = special_expect._j[kind]
    r = next((r for r in recmap if r["index"] == index), None)
    if r is None:
        return segs
    fname = "1df.bin" if kind == "ability" else "1e0.bin"
    raw = AF.jp.file(fname)[int(r["start"], 16):int(r["end"], 16)]
    try:
        rec = AF.compose_record(segs, raw)
    except ValueError:
        return segs
    out = []
    for part in rec.split(b"\x00\x03"):
        part = part.strip(b"\x00")
        if part:
            # decode with the ZH rom: ZH-band slots exist only in the
            # translated build's atlas identities
            out.append(decode_text(AF.zh, part, "bank", AF.zh.expand_sys))
    return out


def check():
    validation = json.loads((STG / "validation.json").read_text())
    apply_rep = json.loads((STG / "apply_report.json").read_text()) \
        if (STG / "apply_report.json").exists() else {"skipped": []}
    skipped_keys = "\n".join(apply_rep.get("skipped", []))
    adjudicated = apply_rep.get("adjudicated", {})
    mismatches = []
    checked = 0

    def miss(tag, want, got):
        mismatches.append({"tag": tag, "want": want, "got": got})

    for kind in ("chars", "units"):
        for bp in sorted((STG / kind).glob("*.json")):
            ent = bp.stem
            if validation[kind].get(ent, {}).get("status") != "ok":
                continue
            out = json.loads((STG / "out" / kind / bp.name).read_text())
            cnp = CSTG / kind / bp.name
            if not cnp.exists():
                miss(f"{ent}", "brief in candidate", "missing")
                continue
            cand = json.loads(cnp.read_text())
            old = json.loads(bp.read_text())
            if kind == "chars":
                cids = {i["idn"]: i for c in cand["cards"] for i in c["ids"]}
                oids = {i["idn"]: i for c in old["cards"] for i in c["ids"]}
                for o in out.get("ids", []):
                    idn = o.get("idn")
                    ci, oi = cids.get(idn), oids.get(idn)
                    if not ci or not oi:
                        continue
                    if o.get("name_zh"):
                        checked += 1
                        adj = adjudicated.get(f"idname {oi['jp_name_ptr']}", {})
                        wants = {norm(o["name_zh"])}
                        if adj.get("chosen"):
                            wants.add(norm(adj["chosen"]))
                        if norm(ci["name_zh_current"]) not in wants \
                           and f"idname {oi['jp_name_ptr']}" not in skipped_keys:
                            miss(f"{ent} idn{idn} name", o["name_zh"],
                                 ci["name_zh_current"])
                    if oi.get("cutin_owner") and o.get("cutin_zh"):
                        checked += 1
                        if norm(ci["cutin_zh_current"]) != norm(o["cutin_zh"]):
                            miss(f"{ent} idn{idn} cutin", o["cutin_zh"],
                                 ci["cutin_zh_current"])
                    if oi.get("detail_owner") and o.get("detail_effect_zh"):
                        checked += 1
                        got = norm(ci["detail_zh_current"])
                        wants = detail_expect(oi["didx"], o["detail_effect_zh"])
                        if got not in wants and \
                           f"didx {oi['didx']}" not in skipped_keys:
                            miss(f"{ent} idn{idn} detail(didx{oi['didx']})",
                                 sorted(wants)[0], got)
                cb = {(b["file"], b["record"]): b for b in cand["barks"]}
                ob = {(b["file"], b["record"]): b for b in old["barks"]}
                for o in out.get("barks", []):
                    key = (o.get("file"), o.get("record"))
                    if key in cb and o.get("zh"):
                        checked += 1
                        if norm(cb[key]["zh_current"]) != norm(o["zh"]) and \
                           f"bark {key}" not in skipped_keys:
                            miss(f"{ent} bark {key}", o["zh"], cb[key]["zh_current"])
            else:
                if out.get("name_zh"):
                    checked += 1
                    if norm(cand["name_zh_current"]) != norm(out["name_zh"]) \
                       and f"unitname {ent}" not in skipped_keys:
                        miss(f"{ent} name", out["name_zh"], cand["name_zh_current"])
                cw = {w["slot"]: w for w in cand["weapons"]}
                for w in out.get("weapons", []):
                    if w.get("zh"):
                        checked += 1
                        got = cw.get(w["slot"], {}).get("zh_current")
                        if norm(got) != norm(w["zh"]) and "weapon " not in skipped_keys:
                            miss(f"{ent} weapon{w['slot']}", w["zh"], got)
                sp, csp = json.loads(bp.read_text()).get("specials", {}), cand.get("specials", {})
                if out.get("ability_segments_zh") and sp.get("ability", {}).get("owner"):
                    checked += 1
                    got = csp.get("ability", {}).get("zh_segments_current", [])
                    want = special_expect("ability", sp["ability"]["family"],
                                          out["ability_segments_zh"])
                    if [norm(s) for s in got] != [norm(s) for s in want] \
                       and f"ability record {sp['ability']['family']}" not in skipped_keys:
                        miss(f"{ent} ability", want, got)
                if out.get("defense_segments_zh") and sp.get("defense", {}).get("owner"):
                    checked += 1
                    got = csp.get("defense", {}).get("zh_segments_current", [])
                    want = special_expect("defense", sp["defense"]["record"],
                                          out["defense_segments_zh"])
                    if [norm(s) for s in got] != [norm(s) for s in want] \
                       and f"defense record {sp['defense']['record']}" not in skipped_keys:
                        miss(f"{ent} defense", want, got)
    (STG / "verify_apply.json").write_text(
        json.dumps(mismatches, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"checked {checked} applied fields; mismatches: {len(mismatches)}")
    for m in mismatches[:20]:
        print(" ✗", m["tag"], "\n    want:", m["want"], "\n    got: ", m["got"])
    return 1 if mismatches else 0


if __name__ == "__main__":
    build_candidate()
    export_candidate()
    raise SystemExit(check())
