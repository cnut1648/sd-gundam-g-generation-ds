#!/usr/bin/env python3
"""reconcile_extraction.py — prove the extraction covers every committed translation.

    python build/reconcile_extraction.py [--rom JP.nds] [-v]

Two-way completeness check between the canonical extractor (utils/extract/)
and the committed translation data:

  direction 1 (nothing lost): every translated record in data/ must map onto
  an extracted JP record (by address / table key).  An unmatched translation
  means the extraction algorithm has a gap — fix the extractor, never drop
  the record.

  direction 2 (nothing missed): the extractor's stage-block universe must
  cover every block the static gates' independent JP-ROM scan finds (the
  translation_coverage denominator machinery in test/run_static.py).

Exit code 0 = fully reconciled.  Each mismatch prints category + key.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.extract import layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from utils.extract.gamerom import GameROM  # noqa: E402

DATA = REPO / "data"


class Report:
    def __init__(self):
        self.rows = []
        self.bad = 0

    def add(self, cat: str, total: int, matched: int, notes: list[str]):
        self.rows.append((cat, total, matched, notes))
        if matched != total:
            self.bad += 1

    def dump(self, verbose: bool):
        for cat, total, matched, notes in self.rows:
            flag = "OK  " if matched == total else "FAIL"
            print(f"  {flag} {cat:34s} {matched}/{total}")
            shown = notes if verbose else notes[:6]
            for n in shown:
                print(f"        {n}")
            if not verbose and len(notes) > 6:
                print(f"        ... {len(notes) - 6} more (-v for all)")


def _j(rel: str) -> dict:
    return json.loads((DATA / rel).read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rom", default=str(REPO / "0098 - SD Gundam G Generation DS (Japan).nds"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    rom = GameROM(args.rom)
    rep = Report()

    # ---- 1. stage dialogue edits -> stage block universe --------------------
    total = matched = 0
    notes = []
    from utils import stage_text
    for fname, sd in stage_text.iter_stage_data():
        blocks = W.stage_blocks(rom, fname)
        starts = {int(b["off"], 16): b for b in blocks}
        spans = {int(b["off"], 16): b["len"] for b in blocks}
        for e in sd.get("edits", []):
            kind = e.get("kind")
            off = int(e["jp_offset"], 16)
            if kind == "dialogue":
                total += 1
                b = starts.get(off)
                if b is not None and spans[off] == e["jp_len"]:
                    matched += 1
                elif b is not None:
                    notes.append(f"{fname}@{e['jp_offset']}: len {e['jp_len']} != dump {spans[off]}")
                else:
                    notes.append(f"{fname}@{e['jp_offset']}: no dumped block")
    rep.add("stage dialogue edits", total, matched, notes)

    # ---- 2. bark edits -> bark record universe --------------------------------
    # Committed bark edits map onto extracted RECORDS by containment: shipped
    # edit offsets are usually sub-line run starts, but some legacy edits begin
    # inside the record header tail (the `06 <cid u16>` bytes rewritten
    # verbatim), so run-start equality is not the invariant — record coverage is.
    bark_recs: dict[str, list[tuple[int, int]]] = {}
    bark_runs: dict[str, set[int]] = {}
    for b in W.barks(rom):
        bark_recs.setdefault(b["file"], []).append(
            (int(b["record"], 16), int(b["end"], 16)))
        s = bark_runs.setdefault(b["file"], set())
        for r in b["runs"]:
            s.add(int(r["off"], 16))
    for rel in ("barks/0.json", "barks/1.json", "barks/1dd.json",
                "barks/1de.json", "barks/c4f.json"):
        d = _j("zh/files/" + rel)
        fn = d["file"]
        runs = bark_runs.get(fn, set())
        recs = sorted(bark_recs.get(fn, []))
        total = matched = 0
        notes = []
        for e in d.get("edits", []):
            total += 1
            off = int(e["offset"], 16)
            if off in runs or any(s <= off < t for s, t in recs):
                matched += 1
            else:
                notes.append(f"{fn}@{e['offset']}: outside every bark record")
        rep.add(f"bark edits {fn}", total, matched, notes)

    # ---- 3. battle effect banks ----------------------------------------------
    # Bank edits may open on structural pad bytes they repurpose (a leading
    # {00} rewritten as 。): normalize by skipping the JP span's leading
    # 00/01/03/04 skeleton, then require a dumped run start — or accept a pure
    # skeleton rewrite (every JP byte structural).
    STRUCT = {0x00, 0x01, 0x03, 0x04}

    def _norm(jp_bytes: bytes, off: int) -> int | None:
        k = 0
        while k < len(jp_bytes) and jp_bytes[k] in (0x00, 0x01):
            k += 1
        return off + k if k < len(jp_bytes) else None

    def _bank_match(fn: str, data: bytes, e: dict, runs: set[int],
                    recs: list[tuple[int, int]]) -> bool:
        off = int(e["offset"], 16)
        span = data[off:off + e["size"]]
        if off in runs:
            return True
        n = _norm(span, off)
        if n is not None and n in runs:
            return True
        if all(b in STRUCT for b in span):        # skeleton-only rewrite
            return True
        return any(s <= off < t for s, t in recs)

    cards = W.ability_cards(rom)
    card_runs = {int(r["off"], 16) for c in cards for r in c["runs"]}
    card_recs = sorted((int(c["start"], 16), int(c["end"], 16)) for c in cards)
    f1da = rom.file("1da.bin")
    d = _j("zh/files/battle/ability_cards.json")
    total = matched = 0
    notes = []
    for e in d["edits"]:
        total += 1
        if _bank_match("1da", f1da, e, card_runs, card_recs):
            matched += 1
        else:
            notes.append(f"1da@{e['offset']}: outside runs/records")
    rep.add("ability_cards (1da)", total, matched, notes)

    eff_runs = {int(r["off"], 16) for r in W.command_effects(rom)}
    f1db = rom.file("1db.bin")
    d = _j("zh/files/battle/command_effects.json")
    total = matched = 0
    notes = []
    for e in d["edits"]:
        total += 1
        if _bank_match("1db", f1db, e, eff_runs, []):
            matched += 1
        else:
            notes.append(f"1db@{e['offset']}: no matching run")
    rep.add("command_effects (1db)", total, matched, notes)

    specials = W.special_records(rom)
    for key, rel in (("ability", "zh/files/battle/special_abilities.json"),
                     ("defense", "zh/files/battle/special_defenses.json")):
        recs = {(int(r["start"], 16), int(r["end"], 16)) for r in specials[key]}
        starts = {s for s, _ in recs}
        d = _j(rel)
        total = matched = 0
        notes = []
        for e in d["edits"]:
            total += 1
            off = int(e["offset"], 16)
            if off in starts or any(s <= off < t for s, t in recs):
                matched += 1
            else:
                notes.append(f"{d['file']}@{e['offset']}: outside every record")
        rep.add(f"special {key} ({d['file']})", total, matched, notes)

    # ---- 4. cut-in quotes -----------------------------------------------------
    cut = W.cutin_records(rom)[0]
    recs = {r["record"] for r in cut["records"]}
    d = _j("zh/files/battle/cutin_quotes.json")
    total = matched = 0
    notes = []
    for g in d["groups"]:
        total += 1
        if g["group"] in recs:
            matched += 1
        else:
            notes.append(f"1dc group {g['group']}: no extracted record")
    rep.add("cutin_quotes (1dc)", total, matched, notes)

    # ---- 5. library / hangar banks --------------------------------------------
    for kind, rel in (("char", "zh/files/library/character_bios.json"),
                      ("unit", "zh/files/library/unit_bios.json")):
        recs = {int(b["off"], 16): b["size"] for b in W.bios(rom, kind)}
        d = _j(rel)
        total = matched = 0
        notes = []
        for e in d["edits"]:
            total += 1
            off = int(e["offset"], 16)
            # an edit may keep a few trailing structure bytes outside its
            # budget, so its size may be smaller than the full record slot
            if off in recs and e["size"] <= recs[off]:
                matched += 1
            elif off in recs:
                notes.append(f"{d['file']}@{e['offset']}: size {e['size']} > record {recs[off]}")
            else:
                notes.append(f"{d['file']}@{e['offset']}: not a bio record start")
        rep.add(f"{kind} bios ({d['file']})", total, matched, notes)

    wl = {int(r["off"], 16) for r in W.weapon_list(rom)}
    d = _j("zh/files/library/weapon_names.json")
    total = matched = 0
    notes = []
    for e in d["edits"]:
        total += 1
        if int(e["offset"], 16) in wl:
            matched += 1
        else:
            notes.append(f"31e@{e['offset']}: not a name start")
    rep.add("weapon_names (31e)", total, matched, notes)

    pt = W.parts(rom)
    part_idx = {p["index"] for p in pt}
    cap_offs = {int(p["caption"]["off"], 16) for p in pt if "caption" in p}
    d = _j("zh/files/hangar/part_names.json")
    total = matched = 0
    notes = []
    # b6e is REBUILT whole (entries repacked at new offsets) — the stable key
    # is the part INDEX, mirrored by the arm9 offset table names/parts.json patches
    for e in d["entries"]:
        total += 1
        if e["index"] in part_idx:
            matched += 1
        else:
            notes.append(f"b6e entry {e['index']}: no extracted part")
    rep.add("part_names (b6e)", total, matched, notes)
    d = _j("zh/files/hangar/part_captions.json")
    f_b6f = rom.file("b6f.bin")
    total = matched = 0
    notes = []
    for e in d["edits"]:
        total += 1
        off = int(e["offset"], 16)
        # some caption edits open one byte early, on the record's leading
        # page-control byte (00/01/03/04) — same containment idea as banks
        if off in cap_offs or (off + 1 in cap_offs
                               and f_b6f[off] in (0x00, 0x01, 0x03, 0x04)):
            matched += 1
        else:
            notes.append(f"b6f@{e['offset']}: not a caption record")
    rep.add("part_captions (b6f)", total, matched, notes)

    # ---- 6. name tables --------------------------------------------------------
    units = {u["utid"]: u for u in W.units(rom)}
    d = _j("zh/units.json")
    total = matched = 0
    notes = []
    for e in d["units"]:
        u = units.get(e["utid"])
        if "zh" in e:
            total += 1
            if u and u["name"]:
                matched += 1
            else:
                notes.append(f"utid {e['utid']}: no extracted unit name")
        slots = {w["slot"] for w in u["weapons"]} if u else set()
        for wpn in e.get("weapons", []):
            total += 1
            if wpn["slot"] in slots:
                matched += 1
            else:
                notes.append(f"utid {e['utid']} slot {wpn['slot']}: no extracted weapon")
    rep.add("zh/units (names+weapons)", total, matched, notes)

    pil = {p["cid"] for p in W.pilots(rom)}
    d = _j("zh/characters.json")
    total = matched = 0
    notes = []
    for e in d["characters"]:
        if "zh" in e:
            total += 1
            if e["cid"] in pil:
                matched += 1
            else:
                notes.append(f"cid {e['cid']}: no extracted pilot")
        for idc_e in e.get("ids", []):
            total += 1
            idc = W.id_command(rom, idc_e["idn"])
            ok = True
            if "name" in idc_e and not idc["name"]:
                ok = False
            if "summary" in idc_e and not idc["summary"]:
                ok = False
            if ok:
                matched += 1
            else:
                notes.append(f"idn {idc_e['idn']}: extracted record lacks name/summary")
    rep.add("zh/characters (names+ids)", total, matched, notes)

    details = {x["didx"] for x in W.id_details(rom)}
    total = matched = 0
    notes = []
    import struct as _st
    for e in d.get("detail_offsets", []):
        total += 1
        didx = e["didx"]
        jp_word = _st.unpack_from("<I", rom.arm9, L.DETAIL_OFFTAB + didx * 4)[0]
        if didx in details:
            matched += 1
        elif int(e["offset"], 16) != jp_word:
            matched += 1    # re-aimed slot: a NEW record placed in the detail
            #                 pool for a JP-empty slot — placement, not JP data
        else:
            notes.append(f"didx {didx}: no extracted detail record")
    rep.add("zh/characters detail_offsets", total, matched, notes)

    d = _j("zh/files/hangar/part_names.json")
    idx = {p["index"] for p in pt}
    total = matched = 0
    notes = []
    for e in d["name_offset_words"]:
        total += 1
        if e["index"] in idx:
            matched += 1
        else:
            notes.append(f"part index {e['index']}: not extracted")
    rep.add("zh part name_offset_words", total, matched, notes)

    # ---- 7. pointer-site tables (abilities / ui labels) -------------------------
    pstr = W.pointer_strings(rom)
    site_set = {int(s, 16) for p in pstr for s in p["sites"]}
    tgt_set = {int(p["ptr"], 16) for p in pstr}
    ui = _j("zh/ui.json")
    for section in ("abilities", "labels"):
        total = matched = 0
        notes = []
        for e in ui[section]:
            for s in e["sites"]:
                total += 1
                site = int(s, 16)
                old = int(e["old_ptr"], 16)
                if site in site_set and old in tgt_set:
                    matched += 1
                elif site not in site_set:
                    notes.append(f"ui.{section} site {s}: site not in pointer graph")
                else:
                    notes.append(f"ui.{section} old_ptr {e['old_ptr']}: target not in pointer graph")
        rep.add(f"zh/ui {section}", total, matched, notes)

    # ---- 8. dictionary ----------------------------------------------------------
    dt = {x["index"]: x for x in W.dictionary_entries(rom, "text")}
    d = ui["dictionary"]
    total = matched = 0
    notes = []
    for e in d["offset_entries"]:
        total += 1
        if e["index"] in dt:
            matched += 1
        else:
            notes.append(f"dict index {e['index']}: not extracted")
    entry_offs = {int(x["off"], 16) for x in dt.values()}
    reloc_targets = {int(x["offset"], 16) for x in d["offset_entries"]}
    base = L.DICT_TEXT
    for e in d["string_edits"]:
        total += 1
        off = int(e["offset"], 16)
        if base + off in entry_offs:
            matched += 1
        elif off in reloc_targets:
            matched += 1        # relocated-entry placement, not a JP record
        else:
            notes.append(f"dict string @base+{e['offset']}: no extracted entry at {hex(base + off)}")
    rep.add("zh/ui dictionary", total, matched, notes)

    # ---- 9. event text blocks -----------------------------------------------------
    ev = {int(b["off"], 16): b["len"] for b in W.event_text_blocks(rom)}
    d = _j("zh/event_text.json")
    total = matched = 0
    notes = []
    for e in d["entries"]:
        total += 1
        off = int(e["offset"], 16)
        if off in ev and ev[off] == e["length"]:
            matched += 1
        elif off in ev:
            notes.append(f"event@{e['offset']}: len {e['length']} != dump {ev[off]}")
        else:
            notes.append(f"event@{e['offset']}: no extracted block")
    rep.add("arenas/event_text_blocks", total, matched, notes)

    # ---- 10. direction 2: gate universe covered ------------------------------------
    # The static gates' independent stage-block scan (bytewise 0x15 heuristic)
    # must find nothing the extractor's universe misses.
    total = matched = 0
    notes = []
    from utils import rom as romlib
    game = romlib.load_rom(args.rom)
    for fname, _sd in stage_text.iter_stage_data():
        d0 = bytes(romlib.get_file(game, fname))
        ours = {int(b["off"], 16) for b in W.stage_blocks(rom, fname)}
        for off, _end in W._linear_stage_blocks(d0):
            total += 1
            if off in ours:
                matched += 1
            else:
                notes.append(f"{fname}@{hex(off)}: linear-scan block missing from dump")
    rep.add("gate-universe stage blocks", total, matched, notes)

    print("\nreconciliation report:")
    rep.dump(args.verbose)
    if rep.bad:
        print(f"\nFAIL: {rep.bad} categories incomplete — the extraction algorithm has gaps")
        return 1
    print("\nOK: every committed translation maps onto the extracted universe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
