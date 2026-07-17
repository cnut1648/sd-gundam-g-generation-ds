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

# The only atlas slots knowingly drawn without a resolved identity.  Both are
# the two kanji of ONE already-translated bark (0.bin@0x19C14, "□□の呼吸っ
# てやつをな", shipped ZH 不死队的本事); resisted candidate/corpus/ZH proof,
# so left unidentified rather than guessed (guessing is the 戦/振 mistake).
GAP_ALLOWLIST = {1997, 2149}

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
            if slot >= 224 and font == "A" and slot not in ident:
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
