#!/usr/bin/env python3
"""Decoder (READ-path) self-check — a pass/fail gate against the two shipped
decoder-bug classes, so neither can silently return:

  A. byte-grammar bugs (the dict-terminator truncation that made 完->0):
       both macro dictionaries must parse into WHOLE tokens and TILE their
       storage (no dangling high byte, no non-zero inter-entry gap); every
       walker record (1df/1e0/barks) must be token-clean.
  B. missing/uncertain glyph identities (the 戦->振 class surfaces as a
       different glyph; a MISSING identity surfaces as an unidentified slot):
       every atlas (renderA) slot actually drawn on any text surface must
       have a decode identity, except an explicit, documented allowlist.

Surface-correct macro expansion is mandatory in the scan (stage->dialogue
dict, bank->system dict); expanding stage text with the system dict invents
phantom slots (the （効果）/（擦極） artifact).

Run:  python3 audit/translate2/decoder_audit.py   (exit 1 on any finding)
The visual context-sentence confrontation sheets live in decoder_audit2.py.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "test"))
from lib_apply import open_roms  # noqa: E402
from render_oracle import Oracle  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
import utils.extract.layout as L  # noqa: E402

# Every in-use atlas slot now has a confirmed identity (the 1068-slot
# contextual VLM audit resolved 1997=阿 / 2149=吽, 阿吽の呼吸).  Empty = the
# gate demands a full identity table.
GAP_ALLOWLIST = set()

jp, zh = open_roms()
oracle = Oracle(Path(__file__).resolve().parents[2]
                / "0098 - SD Gundam G Generation DS (Japan).nds")


def token_clean(b: bytes) -> bool:
    i = 0
    while i < len(b):
        if b[i] >= 0xE0:
            if i + 1 >= len(b):
                return False
            i += 2
        else:
            i += 1
    return True


def check_dicts():
    findings = []
    for name, T, exp in (("DICT_SYS", L.DICT_SYS, jp.expand_sys),
                         ("DICT_TEXT", L.DICT_TEXT, jp.expand)):
        n = struct.unpack_from("<H", jp.arm9, T)[0] // 2
        spans = []
        for i in range(n):
            e = exp(i)
            if e is None:
                continue
            if not token_clean(e):
                findings.append(f"{name} entry {i:#x}: dangling token {e.hex()}")
            off = struct.unpack_from("<H", jp.arm9, T + i * 2)[0]
            spans.append((T + off, T + off + len(e)))
        spans.sort()
        for k in range(1, len(spans)):
            gap = jp.arm9[spans[k - 1][1]:spans[k][0]]
            if gap.strip(b"\x00"):
                findings.append(f"{name}: non-zero gap {spans[k-1][1]:#x}"
                                f"..{spans[k][0]:#x} = {gap.hex()[:24]}")
    return findings


def check_records():
    findings = []
    jspec = W.special_records(jp)
    for kind, fname in (("ability", "1df.bin"), ("defense", "1e0.bin")):
        data = jp.file(fname)
        for r in jspec[kind]:
            raw = data[int(r["start"], 16):int(r["end"], 16)]
            if not token_clean(raw):
                findings.append(f"{kind} record {r['index']}: dangling token")
    for b in W.barks(jp):
        raw = jp.file(b["file"])[int(b["body"], 16):int(b["end"], 16)]
        if not token_clean(raw):
            findings.append(f"bark {b['file']}@{b['record']}: dangling token")
    return findings


def check_identities():
    cm = json.loads((HERE.parent.parent / "data/charmap.json").read_text())
    ident = (set(int(k) for k in cm["jp_slot_chars"])
             | set(cm["one_byte"].values())
             | set(int(k) for k in cm.get("slot_chars_extra", {})))
    seen = {}

    def feed(payload, surface, where):
        oracle.expand = jp.expand if surface == "stage" else jp.expand_sys
        for slot, font in oracle.glyph_stream(payload, surface):
            # Only real JP atlas glyphs need an identity: slots 224..2195.  A
            # slot >= JP_ATLAS_SLOTS on the JP ROM points past the 2196-slot
            # atlas -- the junk-band decode of an out-of-range 0xE0xx token in a
            # priming/index blob or a false 0x15, not a drawn glyph, so it is
            # not an identity gap.  (In-range garbage is excluded upstream by
            # skipping reachable:False records.)
            if 224 <= slot < L.JP_ATLAS_SLOTS and font == "A" and slot not in ident:
                seen.setdefault(slot, where)
    for b in W.barks(jp):
        feed(jp.file(b["file"])[int(b["body"], 16):int(b["end"], 16)],
             "stage", f"bark {b['file']}@{b['record']}")
    for d in W.id_details(jp):
        feed(bytes(jp.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]]),
             "stage", f"didx{d['didx']}")
    jspec = W.special_records(jp)
    for kind in ("ability", "defense"):
        fn = L.SPECIAL_ABILITY_FILE if kind == "ability" else L.SPECIAL_DEFENSE_FILE
        for r in jspec[kind]:
            feed(jp.file(fn)[int(r["start"], 16):int(r["end"], 16)],
                 "bank", f"{kind}{r['index']}")
    for u in W.units(jp):
        if (u.get("name") or {}).get("ptr"):
            feed(jp.cstr(int(u["name"]["ptr"], 16)), "bank", f"unit{u['utid']}")
    for idn in range(L.IDCMD_COUNT):
        i = W.id_command(jp, idn)
        for k in ("name", "summary"):
            if (i.get(k) or {}).get("ptr"):
                feed(jp.cstr(int(i[k]["ptr"], 16)), "bank", f"idn{idn}")

    # -- narrative surfaces (what phases 2-3 translate FROM) ----------------
    # Character/unit bios, library weapon names, hangar part names, every
    # reachable stage dialogue block and every non-spurious event-text /
    # briefing block: all render renderA-direct ("stage").  These were NOT in
    # the original scan, so unidentified glyphs here decoded as {SLOT:n} in
    # data/jp without failing the gate.  Records the extractor marks
    # reachable:False (non-VM-reached stage blocks; spurious 0x15 event blocks,
    # where the >=2196 junk-band tokens live) are skipped: they are not real
    # drawn text.
    for kind, bfile in (("char", L.CHAR_BIO_FILE), ("unit", L.UNIT_BIO_FILE)):
        data = jp.file(bfile)
        for b in W.bios(jp, kind):
            o = int(b["off"], 16)
            feed(data[o:o + b["size"]].rstrip(b"\x00"), "stage",
                 f"{kind}bio#{b['index']}")
    wdata = jp.file(L.WEAPON_LIST_FILE)
    for w in W.weapon_list(jp):
        o = int(w["off"], 16)
        feed(wdata[o:o + w["len"]], "stage", f"weapon@{w['off']}")
    for p in W.parts(jp):
        nm = p["name"]
        o = int(nm["off"], 16)
        feed(jp.file(nm["file"])[o:o + nm["size"]].rstrip(b"\x00"),
             "stage", f"part#{p['index']}")
    for e in W.event_text_blocks(jp):
        if e.get("reachable") is False:
            continue
        o = int(e["off"], 16)
        feed(bytes(jp.arm9[o:o + e["len"]]), "stage", f"event@{e['off']}")
    seen_files = set()
    for d in W.stage_descriptors(jp):
        fn = d["file"]
        if fn:
            tp = struct.unpack_from("<I", jp.arm9,
                                    int(d["record"], 16) + L.STAGE_DESC_TITLE)[0]
            feed(jp.cstr(tp), "bank", f"title[{d['label']}]")
        if not fn or fn in seen_files:
            continue
        seen_files.add(fn)
        fdata = jp.file(fn)
        for blk in W.stage_blocks(jp, fn):
            if blk.get("reachable") is False:
                continue
            o = int(blk["off"], 16)
            feed(fdata[o:o + blk["len"]], "stage", f"{fn}@{blk['off']}")

    return [f"unidentified atlas slot {s} used at {w}"
            for s, w in sorted(seen.items()) if s not in GAP_ALLOWLIST]


def main():
    findings = check_dicts() + check_records() + check_identities()
    if findings:
        print(f"decoder_audit: {len(findings)} FINDING(S)")
        for f in findings:
            print("  ✗", f)
        return 1
    print("decoder_audit: PASS (dicts tile token-clean; records token-clean; "
          f"every drawn atlas slot identified except allowlist {sorted(GAP_ALLOWLIST)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
