#!/usr/bin/env python3
"""One-time migration: render ASCII / parenthesis / dash runs in Chinese name-
pool strings using the *Japanese original's own* narrow glyphs, instead of the
wide 12x12 ZH-atlas glyphs the translator minted.

Why this is safe (see AGENTS.md "Encoding safety rules"):
  * We only ever substitute a run with the EXACT bytes the same record's JP/HEAD
    name used for the identical character run, so it renders precisely the glyph
    the Japanese game drew (no garble is possible by construction).
  * The substituted bytes are therefore one of: a one-byte renderB code (legal
    because the JP original used that byte -> bank_onebyte_regression), a
    system-dict macro 0xF0xx (skipped by pool_trampoline_tokens, renders on the
    trampoline via the shared system dict), or a two-byte E-band token whose slot
    is a PRISTINE JP slot (NOT in charmap two_byte_zh -> pool_trampoline_tokens
    explicitly allows original-JP tokens; only zh-minted slots garble).
  * Digits / + / % are NEVER narrowed (AGENTS.md: they must stay ZH-atlas or they
    render baseline-sunk next to 12px glyphs).
  * Edits are length-preserving (narrow run + trailing 0x00 pad) so the fixed
    put_hex-at-offset pool layout is untouched.

The result must build correctly stand-alone; this script rewrites the committed
sources ONCE and is not part of the build path.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "build"))
import build_guide as G  # noqa: E402
from build_guide import (GameROM, glyph_stream, decode_text, u32, TRAMPOLINE_SPLIT,  # noqa: E402
                         extract_units, extract_pilots, id_command,
                         MASTER_TABLE, MASTER_STRIDE, UNIT_NAME_FIELD,
                         CHARDB, CHARDB_STRIDE, PILOT_NAME_FIELD)

POOLS = ("arenas/resident_caves.json", "arenas/battle_name_pool.json",
         "arenas/post_dict_labels.json", "arenas/ui_names_bank.json")

# fullwidth -> halfwidth for target-class test (identity of the rendered char)
FW = {ord('（'): '(', ord('）'): ')', ord('－'): '-', ord('ー'): '-', ord('・'): '.'}
for _i in range(0x21, 0x7F):
    FW[0xFF00 + (_i - 0x20)] = chr(_i)
def _norm(c): return FW.get(ord(c), c) if c else ''
# narrow these: ASCII letters, parens, dash, slash, dot.  NOT digits/+/% (baseline rule).
TARGET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()-/.")
DIGITS = set("0123456789")


def _load_zh_minted():
    cm = json.loads((REPO / "data/charmap.json").read_text())
    return {int(s) for s in cm.get("two_byte_zh", {}).values()}


def tokenize(rom, data, exp):
    """-> list of (raw_bytes, [chars]).  A dict macro yields its 2 ref bytes with
    the list of chars it expands to; a direct token yields its 1/2 bytes + [char]."""
    G.decode_text(rom, b"\x01", "bank")            # ensure identities loaded
    ID = G._IDENT
    out = []
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b >= 0xF0 and i + 1 < n:
            raw = data[i:i + 2]
            sub = exp(((b << 8) | data[i + 1]) - 0xF000)
            chars = []
            if sub:
                for slot, font in glyph_stream(rom, sub, "bank", exp):
                    chars.append((ID.renderb.get(slot) if font == "B" else ID.atlas.get(slot)) or "\u25a1")
            out.append((raw, chars))
            i += 2
            continue
        if b >= 0xE0 and i + 1 < n:
            slot = ((b << 8) | data[i + 1]) - 0xE000 + 224
            font = "B" if slot < TRAMPOLINE_SPLIT else "A"
            ch = (ID.renderb.get(slot) if font == "B" else ID.atlas.get(slot)) or "\u25a1"
            out.append((data[i:i + 2], [ch]))
            i += 2
            continue
        if b in (0, 1):
            i += 1
            continue
        font = "B" if b < TRAMPOLINE_SPLIT else "A"
        ch = (ID.renderb.get(b) if font == "B" else ID.atlas.get(b)) or "\u25a1"
        out.append((data[i:i + 1], [ch]))
        i += 1
    return out


def target_runs(tokens):
    """Maximal runs of consecutive tokens whose EVERY char is a TARGET char.
    -> list of (idx_start, idx_end_excl, run_str, run_bytes)."""
    runs = []
    i, n = 0, len(tokens)
    while i < n:
        raw, chars = tokens[i]
        if chars and all(_norm(c) in TARGET for c in chars):
            j = i
            rb = bytearray()
            rs = []
            while j < n and tokens[j][1] and all(_norm(c) in TARGET for c in tokens[j][1]):
                rb += tokens[j][0]
                rs.extend(_norm(c) for c in tokens[j][1])
                j += 1
            runs.append((i, j, "".join(rs), bytes(rb)))
            i = j
        else:
            i += 1
    return runs


def jp_safe(run_bytes, zh_minted):
    """True if every token in run_bytes is trampoline-safe to reuse."""
    i, n = 0, len(run_bytes)
    while i < n:
        b = run_bytes[i]
        if b >= 0xF0:              # dict macro: gate-skipped, renders via system dict
            i += 2
        elif b >= 0xE0:            # E-band 2-byte: safe only if PRISTINE JP slot
            slot = ((b << 8) | run_bytes[i + 1]) - 0xE000 + 224
            if slot < TRAMPOLINE_SPLIT and slot in zh_minted:
                return False
            i += 2
        else:                      # one-byte renderB code (JP original used it)
            i += 1
    return True


def narrow(zb, jb, zh_minted, jp, zh):
    """Return narrowed ZH bytes reusing JP's own run bytes, or zb unchanged."""
    zt = tokenize(zh, zb, zh.expand_sys)
    jt = tokenize(jp, jb, jp.expand_sys)
    zruns = target_runs(zt)
    jr = target_runs(jt)
    jruns = {rs: rb for _a, _b, rs, rb in jr}
    jruns_ci = {rs.lower(): rb for _a, _b, rs, rb in jr}   # "use the JP version"
    if not zruns or not jruns:
        return zb

    def _is_digit_tok(tok):
        return bool(tok[1]) and all(_norm(c) in DIGITS for c in tok[1])

    # rebuild token-by-token, swapping whole target runs that JP has identically
    # (case-insensitive fallback: ZH "EX-S" reuses JP "Ex-S" -> the JP casing).
    out = bytearray()
    ri = 0
    idx = 0
    while idx < len(zt):
        if ri < len(zruns) and zruns[ri][0] == idx:
            a, b, rs, rb = zruns[ri]
            ri += 1
            # keep a run WIDE if it abuts a digit: it's part of an alphanumeric
            # model code (GP01Fb) whose digits must stay ZH-atlas (baseline rule).
            # Narrowing only part of such a code looks inconsistent (GP small, 01Fb
            # big).  Pure letter/paren/dash clusters (SEED, EX-S, OZ) still narrow.
            abut_digit = (a > 0 and _is_digit_tok(zt[a - 1])) or (b < len(zt) and _is_digit_tok(zt[b]))
            match = jruns.get(rs)
            if match is None:
                match = jruns_ci.get(rs.lower())
            if match is not None and match != rb and not abut_digit and jp_safe(match, zh_minted):
                out += match
            else:
                out += rb
            idx = b
        else:
            out += zt[idx][0]
            idx += 1
    return bytes(out)


def build_bytemap(jp, zh):
    """entity ZH cstr bytes -> JP cstr bytes (unit/weapon/pilot/id-command names)."""
    m = {}
    def put(jb, zb):
        if jb and zb and zb not in m:
            m[zb] = jb
    for utid in range(1, G.MASTER_COUNT):
        jz = jp.cstr(u32(jp.arm9, MASTER_TABLE + utid * MASTER_STRIDE + UNIT_NAME_FIELD))
        zz = zh.cstr(u32(zh.arm9, MASTER_TABLE + utid * MASTER_STRIDE + UNIT_NAME_FIELD))
        put(jz, zz)
    ju = {u["utid"]: u for u in extract_units(jp)}
    zu = {u["utid"]: u for u in extract_units(zh)}
    for utid, z in zu.items():
        jw = {s: b for s, _p, b in ju.get(utid, {}).get("weapons", [])}
        for s, _p, wb in z.get("weapons", []):
            put(jw.get(s), wb)
    for cid in range(G.CHARDB_COUNT):
        jz = jp.cstr(u32(jp.arm9, CHARDB + cid * CHARDB_STRIDE + PILOT_NAME_FIELD))
        zz = zh.cstr(u32(zh.arm9, CHARDB + cid * CHARDB_STRIDE + PILOT_NAME_FIELD))
        put(jz, zz)
        for slot in range(3):
            ji, zi = id_command(jp, cid * 3 + slot), id_command(zh, cid * 3 + slot)
            put(ji.get("name"), zi.get("name"))
            put(ji.get("summary"), zi.get("summary"))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jp", default=str(REPO / "0098 - SD Gundam G Generation DS (Japan).nds"))
    ap.add_argument("--zh", default=str(REPO / "sd-gundam-g-generation-zh.nds"))
    ap.add_argument("--apply", action="store_true", help="write the pool JSONs")
    args = ap.parse_args()
    jp, zh = GameROM(Path(args.jp)), GameROM(Path(args.zh))
    zh_minted = _load_zh_minted()
    bytemap = build_bytemap(jp, zh)
    print(f"bytemap entities: {len(bytemap)}")
    changed = skipped_unsafe = 0
    examples = []
    for rel in POOLS:
        p = REPO / "data" / rel
        if not p.exists():
            print(f"  (missing {rel})"); continue
        doc = json.loads(p.read_text())
        n_here = 0
        for e in doc.get("entries", []):
            zb = bytes.fromhex(e["payload_hex"])
            core = zb.rstrip(b"\x00")
            jb = bytemap.get(core) or bytemap.get(zb)
            if not jb:
                continue
            nb = narrow(core, jb, zh_minted, jp, zh)
            if nb == core:
                continue
            # never let a name fully revert to its JP bytes: that reclassifies a
            # translated name as JP and trips the translation-count gates
            # (unit_weapon_names / id_command_names).  Keep those wide.
            if nb == jb or nb == jb.rstrip(b"\x00"):
                skipped_unsafe += 1
                continue
            # verify decode-equality before accepting.  Normalise fullwidth<->
            # halfwidth: the ZH atlas paren decodes '(' while the reused JP
            # renderB paren decodes '（' — same glyph class, the intended JP
            # form; only a DIFFERENT character (real garble) survives the norm.
            def _ns(s): return "".join(_norm(c) for c in s).lower()
            if _ns(decode_text(zh, nb, "bank", zh.expand_sys)) != _ns(decode_text(zh, core, "bank", zh.expand_sys)):
                skipped_unsafe += 1
                continue
            padded = nb + b"\x00" * (len(zb) - len(nb))
            if len(padded) != len(zb):
                skipped_unsafe += 1
                continue
            if args.apply:
                e["payload_hex"] = padded.hex()
            changed += 1
            n_here += 1
            if len(examples) < 20:
                examples.append((rel, decode_text(zh, core, "bank", zh.expand_sys),
                                 core.hex(), nb.hex()))
        if args.apply and n_here:
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        print(f"  {rel}: {n_here} narrowed")
    print(f"TOTAL narrowed: {changed}   skipped(unsafe/decode-mismatch): {skipped_unsafe}")
    for rel, txt, a, b in examples:
        print(f"   [{txt}]  {a} -> {b}")
    if args.apply:
        print("APPLIED. Rebuild ROM and run the full gate suite + coverage.")


if __name__ == "__main__":
    main()
