#!/usr/bin/env python3
"""build_guide.py — enhance 攻略.html with content extracted STRICTLY from the game.

This is a translation-REVIEW tool built on the canonical extraction package
(utils/extract/ — the same single code path behind data/jp/, the committed JP
ground-truth dump).  Structure comes from the dump conventions (which records
exist, keys, speakers, briefing grouping, bio ownership); pixel truth comes
from the two ROMs: every string is re-read at its extracted address from BOTH
the Japanese source ROM and the built Chinese ROM and rendered EXACTLY as the
game's two font pipelines draw it (the real 12x12 CJK atlas and 8x16 UI font
shipped in the ROM).  A reviewer compares, glyph for glyph, what the game
really shows — any encoding/garble bug is visible instead of hidden behind our
own source text.

Output: the guide shows the ACTUAL RENDERED GLYPHS (a sprite sheet built from
the ROM's glyph banks + a tiny canvas renderer), never a charmap decode alone.

    python build/build_guide.py --jp <japanese.nds> --zh <translated.nds> \
        [--html 攻略.html] [--out 攻略.html]

Deterministic: no VLM, no network, no fonts — only the two ROMs plus the
committed identity registries (data/charmap.json, data/renderb_charset.json)
and curated extraction inputs (data/extraction/).
"""
from __future__ import annotations

import argparse
import base64
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import text_codec  # noqa: E402
from utils.extract import layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from utils.extract.gamerom import GameROM, u16, u32  # noqa: E402
from utils.extract.identities import glyph_stream, load_identities  # noqa: E402

TRAMPOLINE_SPLIT = L.TRAMPOLINE_SPLIT


# ===========================================================================
# Presentation decode: rendered slots -> readable text (▼ page breaks, □ for
# unidentified glyphs).  The bitmap is the pure-game render; this text is the
# decode OF THE SAME SLOTS via the identity registries, so it agrees with the
# bitmap glyph-for-glyph — it is never our source translation.
# ===========================================================================
def decode_text(rom: GameROM, data: bytes, surface: str, exp=None,
                dialogue: bool = False, _depth: int = 0) -> str:
    ident = load_identities()
    exp = exp or rom.expand
    amap = ident.atlas_zh if rom.is_zh else ident.atlas_jp
    out: list[str] = []
    i = 1 if (dialogue and data[:1] == b"\x15") else 0
    n = len(data)
    while i < n:
        b = data[i]
        if b >= 0xF0 and i + 1 < n:
            sub = exp(((b << 8) | data[i + 1]) - 0xF000)
            if sub is not None and _depth < 6:
                out.append(decode_text(rom, sub, surface, exp, False, _depth + 1))
            i += 2
            continue
        if b >= 0xE0 and i + 1 < n:
            slot = ((b << 8) | data[i + 1]) - 0xE000 + 224
            font = "B" if (surface == "bank" and slot < TRAMPOLINE_SPLIT) else "A"
            i += 2
        else:
            slot = b
            i += 1
            if slot == 0x00:
                if dialogue and out and out[-1] != "▼":
                    out.append("▼")
                continue
            if slot == 0x01:
                continue
            font = "B" if (surface == "bank" and slot < TRAMPOLINE_SPLIT) else "A"
        ch = (ident.renderb.get(slot) if font == "B" else amap.get(slot))
        out.append(ch if ch is not None else "\u25a1")   # □ = unidentified glyph
    return "".join(out).strip("▼")


# ===========================================================================
# Glyph rasters (the actual shipped bitmaps) + sprite-sheet + line renderer
# ===========================================================================
STROKE, SHADOW = 1, 2


def atlas_rows(rom: GameROM, slot: int):
    """12x12 -> rows of 0/1/2 (0 empty, 1 stroke, 2 shadow)."""
    b = rom.atlas_cell(slot)
    rows = []
    for y in range(12):
        row = []
        for x in range(12):
            bit = y * 12 + x
            row.append((b[(bit * 2) // 8] >> ((bit * 2) % 8)) & 3)
        rows.append(row)
    return rows


def renderb_rows(rom: GameROM, slot: int):
    """8x16 (u16-LE rows) -> rows of 0/1/2."""
    b = rom.renderb_cell(slot)
    rows = []
    for y in range(16):
        v = b[y * 2] | (b[y * 2 + 1] << 8)
        rows.append([(v >> (x * 2)) & 3 for x in range(8)])
    return rows


def render_line(jp: GameROM, zh: GameROM, data: bytes, surface: str, rom_kind: str,
                scale: int = 3, expander=None):
    """Render a byte stream to a PIL image EXACTLY as the game draws it.

    rom_kind selects which ROM's glyph banks to use ('jp' or 'zh').  The
    per-glyph vertical anchor mirrors the shipped render path / render_oracle:
    trampoline (bank) atlas glyphs sit at penY+3 so 12px ink bottom-aligns with
    the 8x16 renderB ink; stage atlas glyphs at +1; renderB at 0.
    """
    from PIL import Image
    rom = zh if rom_kind == "zh" else jp
    glyphs = list(glyph_stream(rom, data, surface, expander))
    H = 16
    W = max(1, sum(12 if f == "A" else 8 for _s, f in glyphs))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = img.load()
    x0 = 0
    for slot, font in glyphs:
        if font == "A":
            rows, w = atlas_rows(rom, slot), 12
            yoff = 3 if surface == "bank" else 1
        else:
            rows, w = renderb_rows(rom, slot), 8
            yoff = 0
        for y, row in enumerate(rows):
            for x, v in enumerate(row):
                if v == STROKE:
                    px[x0 + x, yoff + y] = (240, 244, 250, 255)
                elif v == SHADOW and px[x0 + x, yoff + y][3] == 0:
                    px[x0 + x, yoff + y] = (10, 12, 18, 180)
        x0 += w
    if scale > 1:
        img = img.resize((W * scale, H * scale), Image.NEAREST)
    return img


# ---- sprite sheet (all glyph cells of one bank packed into one PNG) --------
def build_sheet(rom: GameROM, kind: str, cols: int = 64):
    """Return (png_bytes, meta) for a glyph bank.  kind in {'atlas','renderb'}."""
    from PIL import Image
    if kind == "renderb":
        n, cw, ch, rowsf = rom.renderb_slots, 8, 16, renderb_rows
        cols = 64
    else:
        n, cw, ch, rowsf = rom.atlas_slots, 12, 12, atlas_rows
    rows_n = (n + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cw, rows_n * ch), (0, 0, 0, 0))
    px = sheet.load()
    for slot in range(n):
        gx, gy = (slot % cols) * cw, (slot // cols) * ch
        for y, row in enumerate(rowsf(rom, slot)):
            for x, v in enumerate(row):
                if v == STROKE:
                    px[gx + x, gy + y] = (240, 244, 250, 255)
                elif v == SHADOW:
                    px[gx + x, gy + y] = (10, 12, 18, 180)
    import io
    buf = io.BytesIO()
    sheet.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"cols": cols, "cw": cw, "ch": ch, "n": n}


# ===========================================================================
# Packing: a text byte stream -> compact glyph-ref array for the HTML canvas
# ===========================================================================
BREAK = 0xFFFF                # page-break sentinel in a packed dialogue line


def _pack1(slot: int, font: str) -> int:
    return (0x8000 | slot) if font == "B" else slot


def pack_glyphs(rom: GameROM, data: bytes, surface: str, exp=None,
                dialogue: bool = False) -> str:
    """Pack a text byte stream into a base64 uint16 array for canvas rendering.

    Each glyph = (font_bit<<15)|slot (font A = 12x12 atlas, B = 8x16 renderB).
    0xF0xx macros expand via `exp`; the dialogue block opener 0x15 is dropped;
    a standalone 0x00 in a dialogue line becomes a BREAK (page-break) marker.
    """
    exp = exp or rom.expand
    out = bytearray()
    i = 1 if (dialogue and data[:1] == b"\x15") else 0
    n = len(data)
    while i < n:
        b = data[i]
        if b >= 0xF0 and i + 1 < n:
            sub = exp(((b << 8) | data[i + 1]) - 0xF000)
            if sub is not None:
                for slot, font in glyph_stream(rom, sub, surface, exp):
                    out += struct.pack("<H", _pack1(slot, font))
            i += 2
            continue
        if b >= 0xE0 and i + 1 < n:
            slot = ((b << 8) | data[i + 1]) - 0xE000 + 224
            font = "B" if (surface == "bank" and slot < TRAMPOLINE_SPLIT) else "A"
            out += struct.pack("<H", _pack1(slot, font))
            i += 2
            continue
        if b == 0x00:
            if dialogue:
                out += struct.pack("<H", BREAK)
            i += 1
            continue
        if b == 0x01:
            i += 1
            continue
        font = "B" if (surface == "bank" and b < TRAMPOLINE_SPLIT) else "A"
        out += struct.pack("<H", _pack1(b, font))
        i += 1
    while len(out) >= 2 and out[-2:] == struct.pack("<H", BREAK):
        del out[-2:]
    return base64.b64encode(bytes(out)).decode()


def sheet_meta(rom: GameROM, kind: str) -> dict:
    png, meta = build_sheet(rom, kind)
    meta["png"] = "data:image/png;base64," + base64.b64encode(png).decode()
    return meta


# ===========================================================================
# Presentation helpers
# ===========================================================================
# game placeholder labels for empty roster/char slots — not real units/chars
_PLACEHOLDER_BYTES = {b"\xf4\xfe",            # 欠番 (system-dict macro; vacant slot)
                      b"\xea\x5a\xe8\x77",     # 预备 (reserve slot)
                      b"\xe8\x77\xef\xb3"}     # 备用 (reserve ID-command name, 435×)


def _dummy_name(b: bytes | None) -> bool:
    return (not b or b == b"\x01" or b == b"\x00"
            or b in _PLACEHOLDER_BYTES)


def _extract_guide_stage_ids() -> set[str]:
    """The stage ids the existing 攻略.html uses (to weave dialogue into them)."""
    import re
    html = (REPO / "攻略.html").read_text(encoding="utf-8")
    # the STAGES_* arrays start records with {id:"..",disp:..}
    return set(re.findall(r'\{id:"([^"]+)",disp:', html))


# irregular file->guide-id cases (many stage files map to one guide card; some
# game files have no guide card at all -> they surface as "extra" at the bottom)
_STAGE_ID_OVERRIDES = {
    "_STG00S": "00SP", "_STGX7": "X7SP",
    "_STG15A": "15a-前", "_STG15A_2": "15a-后",
    "_STG19B_1": "19b-前", "_STG19B_2": "19b-后",
    "_STG03_1": "03-前", "_STG03_2": "03-后",
    "_STGX1_1": "X1-前", "_STGX1_2": "X1-后",
    # SP4..SP7 have a/b/s files but a single guide card each
    "_STGSP4A": "SP4", "_STGSP4B": "SP4", "_STGSP4S": "SP4",
    "_STGSP5A": "SP5", "_STGSP5B": "SP5", "_STGSP5S": "SP5",
    "_STGSP6A": "SP6", "_STGSP6B": "SP6", "_STGSP6S": "SP6",
    "_STGSP7A": "SP7", "_STGSP7B": "SP7", "_STGSP7S": "SP7",
}


def _file_to_guide_id(f: str, guide_ids: set[str]) -> str | None:
    """Map a `_STG*.bin` file to a guide stage id (None => extra/at-bottom)."""
    base = f[:-4] if f.endswith(".bin") else f
    if base in _STAGE_ID_OVERRIDES:
        gid = _STAGE_ID_OVERRIDES[base]
        return gid if gid in guide_ids else None
    s = base[4:] if base.startswith("_STG") else base       # drop _STG
    # regular: NN, NNa/NNb, X<n>a/b, SP<n>a/b, NNSP
    cand = s
    if len(s) >= 2 and s[-1] in "AB" and not s.endswith("SP"):
        cand = s[:-1] + s[-1].lower()                        # 24A->24a, X3A->X3a
    for c in (cand, s):
        if c in guide_ids:
            return c
    return None


SHARED_MIN = 8                 # a dialogue block in >= this many stages is a
                               # shared template (thanks-for-playing / 特别演习
                               # extra-mode text appended to many files) — shown
                               # once, not repeated per stage


def _is_priming_row(jb: bytes) -> bool:
    """Glyph-priming warmup rows every stage file opens with (あいうえおかきく…)."""
    p = jb[1:] if jb[:1] == b"\x15" else jb          # drop block opener
    return p[:5] == bytes((0x16, 0x17, 0x18, 0x19, 0x1A))


def _strip_line_bullets(data: bytes) -> bytes:
    """Drop the per-line page-control that opens each wrapped bio line.

    Encyclopedia bios frame every wrapped line as ``00 <ctrl> 01``; the control
    byte (03/04/07) is consumed by the text VM BEFORE the glyph blitter, so the
    game draws nothing there — only a naive rasterizer would emit a stray
    ·/々/。.  A 03/04/07 MID-line (real 。/·/々) is kept.  Token-aware."""
    LINE_CTRL = (0x03, 0x04, 0x07)
    out = bytearray()
    i, n = 0, len(data)
    at_line_start = True
    while i < n:
        b = data[i]
        if b >= 0xE0 and i + 1 < n:
            out += data[i:i + 2]
            i += 2
            at_line_start = False
            continue
        if b == 0x00:
            out.append(b)
            i += 1
            at_line_start = True
            continue
        if at_line_start and b in LINE_CTRL:
            i += 1                     # drop the line-start page control
            continue
        out.append(b)
        i += 1
        if b != 0x01:                  # 0x01 filler doesn't end the line-start window
            at_line_start = False
    return bytes(out)


def _clean_cutin(body: bytes) -> bytes:
    """Cut-in / bark body -> presentation form (page controls stripped)."""
    return _strip_line_bullets(body.rstrip(b"\x00"))


# ===========================================================================
# JP structure (the extract-dump conventions) + ZH pairing from the built ROM
# ===========================================================================
def _read_span(rom: GameROM, off: int, ln: int) -> bytes:
    return rom.arm9[off:off + ln]


def _pool_b_bounds(zh: GameROM) -> tuple[int, int]:
    """Briefing pool B bounds straight from the ZH build's autoload list."""
    for lo, hi, _delta in zh._segs:
        if lo == 0x023E7000:
            return lo, hi
    return 0x023E7000, 0x023E7000


def _brief_zh_blobs(zh: GameROM, zblk: bytes, lo: int, hi: int) -> list[bytes]:
    """The ZH translation of one briefing block, EXACT: the rebuilt block is a
    run of u32 pointers into pool B (each one rendered ZH line); follow every
    pointer in order and decode the NUL-terminated blob it targets."""
    out, i = [], 0
    n = len(zblk)
    while i + 4 <= n:
        v = struct.unpack_from("<I", zblk, i)[0]
        if lo <= v < hi:
            b = zh.cstr(v)
            if b:
                out.append(b)
            i += 4
        else:
            i += 1
    return out


def _disp(s: str) -> str:
    """Extractor transcription -> guide display: {00} page breaks become ▼,
    {01} layout controls vanish, unidentified-glyph escapes show as □."""
    import re
    s = s.replace("{00}", "▼").replace("{01}", "")
    return re.sub(r"\{[^}]*\}", "\u25a1", s)


def extract_briefings_by_stage(jp: GameROM, zh: GameROM) -> list[dict]:
    """作战内容 briefings grouped by the stage descriptor(s) that own them.

    JP blocks come from the extractor's event-text walk (briefing-flagged);
    the ZH side reads the SAME-offset block from the ZH ROM and follows its
    pool-B pointers — the exact 1:1 pairing."""
    lo, hi = _pool_b_bounds(zh)
    descs = {d["index"]: d for d in W.stage_descriptors(jp)}
    events = [e for e in W.event_text_blocks(jp) if e.get("briefing")]
    groups: dict[tuple, list[dict]] = {}
    for e in events:
        key = tuple(e.get("descs", ()))
        groups.setdefault(key, []).append(e)
    out = []
    for key, blist in groups.items():
        lines = []
        for e in blist:
            off, ln = int(e["off"], 16), e["len"]
            jb = _read_span(jp, off, ln)
            zblk = _read_span(zh, off, ln)
            blobs = _brief_zh_blobs(zh, zblk, lo, hi)
            zjoin = b"\x00".join(blobs)          # 00 -> page break between lines
            lines.append({
                "jt": decode_text(jp, jb, "stage", jp.expand, True),
                "jb": pack_glyphs(jp, jb, "stage", jp.expand, True),
                "zt": decode_text(zh, zjoin, "stage", zh.expand, True) if zjoin else "",
                "zb": pack_glyphs(zh, zjoin, "stage", zh.expand, True) if zjoin else "",
            })
        if not lines:
            continue
        labs = []
        for k in key:
            d = descs.get(k)
            if d and d["label"] and _disp(d["label"]) not in labs:
                labs.append(_disp(d["label"]))
        # representative descriptor = the highest-k owner (the labelled story
        # stage; low-k owners of a shared start are the label-less test stages)
        rep = descs.get(key[-1]) if key else None
        out.append({"desc": key[-1] if key else None, "n": len(lines),
                    "lines": lines, "labs": labs,
                    "lab": labs[0] if labs else "",
                    "title": _disp(rep["title"]) if rep else ""})
    return out


def build_gamedata(jp: GameROM, zh: GameROM) -> dict:
    """Extract every reviewed surface from BOTH ROMs and pack for the browser.

    Each text field is emitted as {zt,jt,zb,jb}: ZH text, JP text (readability;
    decode of the rendered slots), ZH bitmap, JP bitmap (pure-game render).
    JP decode dicts/fonts (verified against the ZH translation): dialogue/
    cut-ins/detail = atlas 'stage' + the dialogue dict; names/weapons/ID/
    specials = renderB 'bank' + the system dict.
    """
    data: dict = {
        "sheets": {"jp": sheet_meta(jp, "atlas"),
                   "zh": sheet_meta(zh, "atlas"),
                   "rb": sheet_meta(jp, "renderb")},
    }

    def fld(jb, zb, surface, jexp, dlg=False, zexp=None):
        ze = zexp or zh.expand
        return {
            "zt": decode_text(zh, zb, surface, ze, dlg) if zb else "",
            "jt": decode_text(jp, jb, surface, jexp, dlg) if jb else "",
            "zb": pack_glyphs(zh, zb, surface, ze, dlg) if zb else "",
            "jb": pack_glyphs(jp, jb, surface, jexp, dlg) if jb else "",
        }

    def name_fld(jb, zb):
        # names render on the system-dict trampoline; ZH names may reuse the
        # JP original's system-dict macros (narrowed ASCII), so decode/pack ZH
        # with expand_sys too (not the dialogue dict).
        return fld(jb, zb, "bank", jp.expand_sys, zexp=zh.expand_sys)

    def name_fld_battle(jb, zb):
        # the SAME name bytes rendered on the renderA-direct 'stage' surface
        # (the in-battle nameplate path); for names reusing a JP-band slot the
        # two paths draw DIFFERENT glyphs, so showing both exposes garble.
        return fld(jb, zb, "stage", jp.expand_sys, zexp=zh.expand_sys)

    # cid -> name (for dialogue speaker labels; all named char-DB records)
    names = {}
    for cid in range(L.CHARDB_COUNT):
        rec = L.CHARDB + cid * L.CHARDB_STRIDE + L.PILOT_NAME_FIELD
        zn = zh.cstr(u32(zh.arm9, rec))
        jn = jp.cstr(u32(jp.arm9, rec))
        if not _dummy_name(zn):
            names[cid] = {"zt": decode_text(zh, zn, "bank", zh.expand_sys),
                          "jt": decode_text(jp, jn, "bank", jp.expand_sys) if jn else ""}
    data["names"] = names

    # ---- 1a. stage dialogue: EVERY block the event VM can reach, JP+ZH --------
    # We walk BOTH the JP and the (CFG-isomorphic, gate-enforced) ZH stage file
    # with the event VM and pair blocks by play-order index.  The extractor's
    # relaxed block universe additionally contains unreached candidates; the
    # REVIEW shows the unreached ones only when they are actually translated
    # (ending-VM / route-completion text), paired via the translation mapping's
    # growth deltas — untranslated unreached candidates are bytecode noise here
    # (they stay in data/jp for completeness).
    from utils.extract.dump import load_speaker_overlay
    from utils import stage_text
    overlay = load_speaker_overlay()
    guide_ids = _extract_guide_stage_ids()
    import collections
    freq = collections.Counter()
    per_stage = []
    stage_edits = {f: sd for f, sd in stage_text.iter_stage_data()}
    stage_files = sorted({d["file"] for d in W.stage_descriptors(jp) if d["file"]})
    for f in stage_files:
        base = f[:-4]
        meta_by_off = {int(b["off"], 16): b
                       for b in W.stage_blocks(jp, f,
                                               overlay.get(base) or overlay.get(f))}
        jf, zf = jp.file(f), zh.file(f)
        cj, cz = W.stage_block_order(jf), W.stage_block_order(zf)
        iso = len(cj) == len(cz)
        rows, cfg_offs = [], set()      # (scene, jp_off, jb, zb, meta)
        for (scene, jo, _b), (_sc, zo, _b2) in (zip(cj, cz) if iso else zip(cj, cj)):
            jt = text_codec.find_terminator(jf, jo + 1)
            zt = text_codec.find_terminator(zf, zo + 1)
            if jt < 0 or zt < 0:
                continue
            jb = jf[jo:jt + 2]
            zb = zf[zo:zt + 2] if iso else b""
            if _is_priming_row(jb):
                continue
            rows.append((scene, jo, jb, zb, meta_by_off.get(jo, {})))
            cfg_offs.add(jo)
            freq[jb] += 1
        # UNION with translated blocks the static VM walk can't reach
        # (route-completion / thanks-for-playing / ending-VM text), paired by
        # the edits' cumulative growth delta.
        sd = stage_edits.get(f, {})
        edits = sorted([(int(e["jp_offset"], 16), e["jp_len"],
                         bytes.fromhex(e["zh_hex"]), e.get("kind"))
                        for e in sd.get("edits", [])]
                       + [(int(x["jp_offset"], 16), 0, bytes.fromhex(x["hex"]), None)
                          for x in sd.get("inserts", [])])
        delta = 0
        for off, old_len, new, kind in edits:
            if kind == "dialogue" and off not in cfg_offs:
                jb = jf[off:off + old_len]
                if not _is_priming_row(jb):
                    rows.append((-1, off, jb, zf[off + delta:off + delta + len(new)],
                                 meta_by_off.get(off, {})))
                    freq[jb] += 1
            delta += len(new) - old_len
        per_stage.append((f, rows))

    shared = []               # dedup'd shared template blocks (shown once)
    shared_seen = set()
    stages = []
    for f, rows in per_stage:
        lines = []
        for scene, off, jb, zb, meta in rows:
            if freq[jb] >= SHARED_MIN:                       # shared template
                if jb not in shared_seen:
                    shared_seen.add(jb)
                    shared.append(fld(jb, zb, "stage", jp.expand, True))
                continue
            ln = fld(jb, zb, "stage", jp.expand, True)
            ln["sc"] = scene
            if meta.get("speaker", -1) >= 0:
                ln["sp"] = meta["speaker"]
            if meta.get("choice"):                           # real player forks
                ln["c"] = 1
            lines.append(ln)
        nsc = len({ln["sc"] for ln in lines if ln.get("sc", -1) >= 0})
        stages.append({"file": f[:-4], "gid": _file_to_guide_id(f, guide_ids),
                       "n": len(lines), "nsc": nsc, "lines": lines})
    data["stages"] = stages
    data["shared"] = shared

    # ---- 1b. characters: name, 3 ID cmds, cut-ins, barks ---------------------
    # barks are in-place, size-preserving edits: JP and ZH records share the
    # SAME offsets, so pairing is exact by record offset.
    jbarks = W.barks(jp)
    zbarks_by_rec = {(b["file"], b["record"]): b for b in W.barks(zh)}
    jbark_by_cid: dict[int, list[dict]] = {}
    jbark_by_vs: dict[int, list[dict]] = {}
    for b in jbarks:
        jbark_by_cid.setdefault(b["cid"], []).append(b)
        jbark_by_vs.setdefault(b["voiceset"], []).append(b)

    jdetails = {d["didx"]: d for d in W.id_details(jp)}
    zdetails = {d["didx"]: d for d in W.id_details(zh)}
    jcut = W.cutin_records(jp)[0]
    zcut = W.cutin_records(zh)[0]
    jcut_by_rec = {r["record"]: r for r in jcut["records"]}
    zcut_by_rec = {r["record"]: r for r in zcut["records"]}

    def _cutin_bytes(rom: GameROM, cutmap, rec):
        r = cutmap.get(rec)
        if not r:
            return None
        dc = rom.file(L.CUTIN_FILE)
        raw = dc[int(r["start"], 16):int(r["end"], 16)]
        if raw[:2] == b"\x00\x05":
            raw = raw[4:]
        elif raw[:2] == b"\x00\x04":
            raw = raw[2:]
        k = raw.find(b"\x00\x03\x00\x01")
        if k >= 0:
            raw = raw[:k]
        return _clean_cutin(raw) or None

    def _detail_bytes(rom: GameROM, dmap, didx):
        d = dmap.get(didx)
        if not d:
            return None
        return rom.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]]

    def _bark_rows(rec: dict) -> tuple[bytes, bytes]:
        """One JP bark record + its same-offset ZH record -> (jb, zb) bodies."""
        zrec = zbarks_by_rec.get((rec["file"], rec["record"]))
        jf = jp.file(rec["file"])
        zf = zh.file(rec["file"])
        b0, e0 = int(rec["body"], 16), int(rec["end"], 16)
        jb = _clean_cutin(jf[b0:e0])
        if zrec:
            zb0, ze0 = int(zrec["body"], 16), int(zrec["end"], 16)
            zb = _clean_cutin(zf[zb0:ze0])
        else:
            zb = b""
        return jb, zb

    def _bark_glyphs(rom, b):
        return sum(1 for _ in glyph_stream(rom, b, "stage")) if b else 0

    chars = []
    for cid in range(L.CHARDB_COUNT):
        rec = L.CHARDB + cid * L.CHARDB_STRIDE
        jn = jp.cstr(u32(jp.arm9, rec + L.PILOT_NAME_FIELD))
        zn = zh.cstr(u32(zh.arm9, rec + L.PILOT_NAME_FIELD))
        if _dummy_name(zn):
            continue
        ids = []
        for slot in range(3):
            idn = cid * 3 + slot
            if idn >= L.IDCMD_COUNT:
                continue
            ji, zi = W.id_command(jp, idn), W.id_command(zh, idn)
            zi_name = zh.cstr(int(zi["name"]["ptr"], 16)) if zi["name"] else None
            if _dummy_name(zi_name):
                continue
            ji_name = jp.cstr(int(ji["name"]["ptr"], 16)) if ji["name"] else None
            ji_sum = jp.cstr(int(ji["summary"]["ptr"], 16)) if ji["summary"] else None
            zi_sum = zh.cstr(int(zi["summary"]["ptr"], 16)) if zi["summary"] else None
            rec_no = jcut["links"].get(str(idn))
            cj = _cutin_bytes(jp, jcut_by_rec, rec_no) if rec_no else None
            zrec_no = zcut["links"].get(str(idn))
            cz = _cutin_bytes(zh, zcut_by_rec, zrec_no) if zrec_no else None
            ids.append({
                "slot": slot,
                "nm": name_fld(ji_name, zi_name),
                "sm": name_fld(ji_sum, zi_sum),
                "dt": fld(_detail_bytes(jp, jdetails, ji["didx"]),
                          _detail_bytes(zh, zdetails, zi["didx"]), "stage", jp.expand),
                "cut": fld(cj, cz, "stage", jp.expand),
                "tgt": L.IDCMD_TARGET_NAMES.get(zi["target"], ""),
                "map": bool(zi["cond"] & 0x02)})
        # barks: primary link is the char-DB index; a bark-less alternate form
        # falls back to its shared voiceset (char-DB +0x0A == bark header field)
        recs = jbark_by_cid.get(cid)
        if not recs:
            vs = u16(jp.arm9, rec + L.CHARDB_VOICESET)
            recs = jbark_by_vs.get(vs, [])
        barks = []
        for brec in recs or []:
            jb, zb = _bark_rows(brec)
            # skip single-glyph placeholder barks (the lone あ warm-up shared
            # by non-combatant NPC voicesets)
            if _bark_glyphs(zh, zb) <= 1 and _bark_glyphs(jp, jb) <= 1:
                continue
            barks.append(fld(jb, zb, "stage", jp.expand))
        if not ids and not barks:
            continue
        chars.append({"cid": cid, "nm": name_fld(jn, zn),
                      "nmA": name_fld_battle(jn, zn), "ids": ids, "barks": barks})
    data["chars"] = chars

    # ---- 1c. units: name, weapons, specials ----------------------------------
    ju = {u["utid"]: u for u in W.units(jp)}
    zu = {u["utid"]: u for u in W.units(zh)}
    jspec = W.special_records(jp)
    zspec = W.special_records(zh)

    def _seg_bytes(rom: GameROM, fname: str, recmap, index, limit=3):
        for r in recmap:
            if r["index"] == index:
                data_f = rom.file(fname)
                o0, o1 = int(r["start"], 16), int(r["end"], 16)
                segs = []
                for part in data_f[o0:o1].split(b"\x00\x03"):
                    part = part.strip(b"\x00")
                    if part:
                        segs.append(part)
                return segs[:limit]
        return []

    units = []
    for utid in sorted(zu):
        z = zu[utid]
        j = ju.get(utid, {})
        z_name = zh.cstr(int(z["name"]["ptr"], 16)) if z.get("name") else None
        if _dummy_name(z_name):
            continue
        j_name = jp.cstr(int(j["name"]["ptr"], 16)) if j.get("name") else None
        jw = {w["slot"]: jp.cstr(int(w["ptr"], 16)) for w in j.get("weapons", [])}
        weapons = [name_fld(jw.get(w["slot"]), zh.cstr(int(w["ptr"], 16)))
                   for w in z.get("weapons", [])]
        specials = []
        jlink = W.unit_special_link(jp, utid)
        zlink = W.unit_special_link(zh, utid)
        fam = zlink.get("ability_family", -1)
        if fam >= 0:
            js = _seg_bytes(jp, L.SPECIAL_ABILITY_FILE, jspec["ability"], fam, 2)
            zs = _seg_bytes(zh, L.SPECIAL_ABILITY_FILE, zspec["ability"], fam, 2)
            for k in range(max(len(js), len(zs))):
                jb = js[k] if k < len(js) else None
                zb = zs[k] if k < len(zs) else None
                if zb:
                    specials.append({"kind": "ability",
                                     **fld(jb, zb, "bank", jp.expand_sys,
                                           zexp=zh.expand_sys)})
        if "defense_name" in zlink:
            tj = jp.cstr(int(jlink["defense_name"]["ptr"], 16)) if "defense_name" in jlink else None
            tz = zh.cstr(int(zlink["defense_name"]["ptr"], 16))
            if tz:
                specials.append({"kind": "defense",
                                 **fld(tj, tz, "bank", jp.expand_sys,
                                       zexp=zh.expand_sys)})
        # defense description record: index 0 is the valid default record, so
        # no truthiness skip here (the old guide always reads it)
        rj, rz = jlink.get("defense_record", 0), zlink.get("defense_record", 0)
        dj = _seg_bytes(jp, L.SPECIAL_DEFENSE_FILE, jspec["defense"], rj, 3)
        dz = _seg_bytes(zh, L.SPECIAL_DEFENSE_FILE, zspec["defense"], rz, 3)
        for k in range(max(len(dj), len(dz))):
            jb = dj[k] if k < len(dj) else None
            zb = dz[k] if k < len(dz) else None
            if zb:
                specials.append({"kind": "defense",
                                 **fld(jb, zb, "bank", jp.expand_sys,
                                       zexp=zh.expand_sys)})
        units.append({"utid": utid, "nm": name_fld(j_name, z_name),
                      "nmA": name_fld_battle(j_name, z_name),
                      "weapons": weapons, "specials": specials})
    data["units"] = units

    # ---- briefings (作战内容), per stage descriptor, into the Route tab -------
    data["briefings"] = extract_briefings_by_stage(jp, zh)
    # ---- encyclopedia (資料館 library + hangar bios / part names) ------------
    data["library"] = extract_bios(jp, zh)
    return data


def _bio_name_map(jp: GameROM, zh: GameROM):
    """{bio_index: {zt,jt}} owner-name labels for char and unit bios, from the
    curated data/extraction/library_bio_map.json."""
    from utils.extract.dump import load_bio_map
    m = load_bio_map()

    def nm(rom, ptr):
        s = rom.cstr(ptr) if 0x02000000 <= ptr < 0x02400000 else None
        return decode_text(rom, s, "bank", rom.expand_sys) if s else ""
    cn, un = {}, {}
    for i, cid in enumerate(m.get("char", [])):
        if isinstance(cid, int) and cid >= 0:
            base = L.CHARDB + cid * L.CHARDB_STRIDE + L.PILOT_NAME_FIELD
            cn[i] = {"zt": nm(zh, u32(zh.arm9, base)), "jt": nm(jp, u32(jp.arm9, base))}
    for i, utid in enumerate(m.get("unit", [])):
        if isinstance(utid, int) and utid >= 0:
            base = L.MASTER_TABLE + utid * L.MASTER_STRIDE
            un[i] = {"zt": nm(zh, u32(zh.arm9, base)), "jt": nm(jp, u32(jp.arm9, base))}
    return cn, un


def _bios_section(jp: GameROM, zh: GameROM, kind: str, names: dict) -> list[dict]:
    """One bio section (char or unit): records from the extractor's offset-table
    walk, bytes re-read from BOTH ROMs (in-place bank: same offsets)."""
    recs = W.bios(jp, kind)
    fname = recs[0]["file"] if recs else ""
    jf = jp.file(fname) if recs else b""
    zf = zh.file(fname) if recs else b""
    items = []
    for r in recs:
        off, sz = int(r["off"], 16), r["size"]
        jb = _strip_line_bullets(bytes(jf[off:off + sz]))
        zb = _strip_line_bullets(bytes(zf[off:off + sz]))
        zt = decode_text(zh, zb, "stage", zh.expand, True) if zb else ""
        core = zt.replace("\u25bc", "").strip("\u3000 \u3001\u3002\u30fb\u00b7.:\uff1a\uff0f/~\u301c\u201c\u201d'\"-<>")
        for _ph in ("\u4e88\u5099", "\u9884\u5907", "\u6b20\u756a"):   # 予備/预备/欠番
            if core == "" or len(core) <= 1 or core.startswith(_ph):
                core = ""
                break
        if not core:
            continue
        bidx = r["index"]
        nm = names.get(bidx)
        items.append({
            "ix": bidx + 1,
            "name": nm,                                   # {zt,jt} owner name
            "zt": zt,
            "jt": decode_text(jp, jb, "stage", jp.expand, True) if jb else "",
            "zb": pack_glyphs(zh, zb, "stage", zh.expand, True) if zb else "",
            "jb": pack_glyphs(jp, jb, "stage", jp.expand, True) if jb else "",
        })
    return items


def _parts_section(jp: GameROM, zh: GameROM) -> list[dict]:
    """改造部件: part NAME (b6e) paired 1:1 with its CAPTION (b6f) by index.
    Both sides read the record layout the RESPECTIVE ROM's offset table
    declares (the ZH build repacked b6e and patched the arm9 table).  The
    caption bank's JP macros resolve through a parts-local runtime dict the
    image does not carry, so JP captions are omitted; the ZH caption renders
    true."""
    jparts = {p["index"]: p for p in W.parts(jp)}
    zparts = {p["index"]: p for p in W.parts(zh)}
    jfe, zfe = jp.file(L.PART_NAME_FILE), zh.file(L.PART_NAME_FILE)
    zfc = zh.file(L.PART_CAP_FILE)

    def _rd(f, rec):
        off, sz = int(rec["off"], 16), rec["size"]
        return bytes(f[off:off + sz])
    items = []
    for i in sorted(zparts):
        zp = zparts[i]
        jp_p = jparts.get(i)
        jb = _rd(jfe, jp_p["name"]) if jp_p else b""
        zb = _rd(zfe, zp["name"])
        nzt = decode_text(zh, zb, "stage", zh.expand, True) if zb else ""
        core = nzt.replace("\u25bc", "").strip("\u3000 \u3001\u3002\u30fb\u00b7.:\uff1a")
        for _ph in ("\u4e88\u5099", "\u9884\u5907", "\u6b20\u756a"):
            if core == "" or core.startswith(_ph):
                core = ""
                break
        if not core:
            continue
        it = {"ix": i + 1,
              "zt": nzt, "jt": decode_text(jp, jb, "stage", jp.expand, True) if jb else "",
              "zb": pack_glyphs(zh, zb, "stage", zh.expand, True) if zb else "",
              "jb": pack_glyphs(jp, jb, "stage", jp.expand, True) if jb else ""}
        if "caption" in zp:
            czb = _rd(zfc, zp["caption"])
            it["cap"] = {"zt": decode_text(zh, czb, "stage", zh.expand, True) if czb else "",
                         "jt": "",
                         "zb": pack_glyphs(zh, czb, "stage", zh.expand, True) if czb else "",
                         "jb": ""}
        items.append(it)
    return items


def extract_bios(jp: GameROM, zh: GameROM) -> list[dict]:
    """Encyclopedia (資料館 library + hangar): character/unit biographies (each
    tagged with its owner's name) and 改造 part names paired with captions."""
    cn, un = _bio_name_map(jp, zh)
    cbios = _bios_section(jp, zh, "char", cn)
    ubios = _bios_section(jp, zh, "unit", un)
    parts = _parts_section(jp, zh)
    return [
        {"key": "char_bios", "title": "\u89d2\u8272\u56fe\u9274 Character bios",
         "long": True, "kind": "bio", "n": len(cbios), "items": cbios},
        {"key": "unit_bios", "title": "\u673a\u4f53\u56fe\u9274 Unit bios",
         "long": True, "kind": "bio", "n": len(ubios), "items": ubios},
        {"key": "parts", "title": "\u6539\u9020\u90e8\u4ef6 Parts (\u540d\u79f0+\u8bf4\u660e)",
         "long": False, "kind": "part", "n": len(parts), "items": parts},
    ]


GG_STYLE = """<style id="gg-style">
.ggwrap{margin-top:14px}
.gg-intro{color:var(--ink2);font-size:13px;margin:-4px 0 14px;max-width:1000px;line-height:1.6}
.gg-note{font-size:12px;color:var(--ink3);background:#0e1626;border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin-bottom:12px}
.gg-search{margin:0 0 12px}
.gg-search input{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:7px 11px;color:var(--ink);width:240px}
/* grid of always-expanded cards (units / ids) */
.ggrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(370px,1fr));gap:12px;align-items:start}
.gcard{position:relative;background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:12px;padding:24px 13px 12px;overflow:visible;min-height:52px}
.gcard-ix{position:absolute;top:7px;left:12px;font-size:11px;color:var(--ink4);font-family:ui-monospace,monospace}
.gcard-hd{border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:4px}
.gsect{margin:7px 0 1px}
.gstitle{font-size:10px;color:var(--ink3);letter-spacing:.7px;text-transform:uppercase;margin:6px 0 2px;opacity:.85}
.gstitle.click{cursor:pointer;user-select:none}
.gstitle.click:hover{color:var(--ink2)}
.gstitle .cnt,.gcoll-hd .cnt{color:var(--ink4);font-size:10px;margin-left:4px}
.gstitle .tw{color:var(--cyan2);margin-right:3px}
/* one reviewed field: ZH text (big) + JP text (small) | ZH bitmap (hover -> JP bitmap) */
.gf{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:3px 0;border-bottom:1px solid #ffffff07}
.gf:last-child{border-bottom:none}
.gf.name{padding:0;border-bottom:none}
.gf.stk{flex-direction:column;align-items:stretch;gap:2px}
.gf.stk .gf-txt{flex:none}
.gf.stk .gf-bmp,.dlg-col .gf-bmp{max-width:100%}
.gf.stk .gf-bmp-scroll,.dlg-col .gf-bmp-scroll{display:block;max-width:100%;overflow-x:auto;overflow-y:hidden}
.gf-lab{font-size:10px;color:var(--ink4);min-width:38px;flex:none;align-self:flex-start;padding-top:3px}
.gf-txt{display:flex;flex-direction:column;min-width:0;flex:1}
.gf-txt .zt{font-size:14px;color:var(--ink);line-height:1.3;word-break:break-word}
.gf.name .zt{font-size:18px;font-weight:700;letter-spacing:.5px}
.gf-txt .jt{font-size:11px;color:var(--ink4);line-height:1.3;word-break:break-word}
.gf-txt .zt:empty,.gf-txt .jt:empty{display:none}
.gf-bmp{position:relative;flex:none;line-height:0;padding:1px 0}
.gf-bmp.hasjp{cursor:help}
.gf-bmp .pop{display:none;position:absolute;right:0;top:100%;z-index:30;background:#0b1220;border:1px solid var(--line2);border-radius:7px;padding:7px 6px 4px;margin-top:3px;box-shadow:0 8px 24px #000a;white-space:nowrap}
.gf-bmp .pop::before{content:'\u65e5 JP';position:absolute;top:2px;left:6px;font-size:8px;color:var(--ink4)}
.gf-bmp:hover .pop{display:block}
.gf-bmp .pop .gseg{filter:brightness(.92) sepia(.25) hue-rotate(60deg) saturate(1.25)}
.gf-lines{display:inline-flex;flex-direction:column;gap:2px;vertical-align:top}
.gf-lines>.gseg{display:flex}
.gf-el{height:8px}
/* collapsible block (route dialogue / briefing / shared) */
.gcoll{margin:10px 0;background:#0e1626;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.gcoll-hd{padding:8px 12px;cursor:pointer;user-select:none;font-size:13px;color:var(--ink2)}
.gcoll-hd:hover{background:#16223a}
.gcoll-hd .tw{color:var(--cyan2);margin-right:5px}
.gcoll-bd{padding:6px 12px 10px;border-top:1px solid var(--line)}
.collbody{padding-top:2px}
/* dialogue line: [speaker + badges] [ZH/JP text | ZH bitmap] */
.dlg{align-items:flex-start}
.dlg-file{font-size:11px;color:var(--cyan2);margin:9px 0 3px;font-family:ui-monospace,monospace}
.dlg-meta{flex:none;width:104px;display:flex;flex-direction:column;gap:2px;align-items:flex-start;padding-top:2px}
.dlg-meta .spk{font-size:12px;color:var(--gold);font-weight:600;line-height:1.25}
.dlg-col{display:flex;flex-direction:column;gap:3px;align-items:stretch;flex:1;min-width:0}
.gbadge{font-size:9.5px;border-radius:5px;padding:0 5px;line-height:1.55;border:1px solid;white-space:nowrap}
.gbadge.br{color:#ffb27a;border-color:#7a4a2a;background:#2a170c}
.gbadge.ev{color:#7ad4ff;border-color:#2a5a7a;background:#0c1f2a}
.gbadge.ch{color:#c79bff;border-color:#4a2a7a;background:#170c2a}
/* global show/hide in-game bitmaps */
#gg-bmptoggle{position:fixed;right:18px;bottom:18px;z-index:200;background:var(--panel);border:1px solid var(--line2);color:var(--ink);border-radius:20px;padding:8px 15px;font-size:13px;cursor:pointer;box-shadow:0 5px 18px #0008}
#gg-bmptoggle:hover{background:#16223a}
body.gg-nobmp .gf-bmp{display:none!important}
/* dual name render (roster renderB vs battle renderA) */
.gname-row{display:flex;gap:8px;align-items:center;padding:2px 0}
.gname-tag{font-size:9px;color:var(--ink4);min-width:30px;flex:none}
.gname-tag b{color:var(--cyan2);font-weight:600}
/* per-ID-skill card block (skill 1/2/3 clearly separated) */
.gskill{margin:9px 0 3px;border:1px solid var(--line2);border-radius:9px;background:#0c1526;padding:7px 9px 4px}
.gskill-hd{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--gold);font-weight:700;margin-bottom:3px;border-bottom:1px solid var(--line);padding-bottom:3px}
.gskill-hd .gsk-n{background:#2a2140;color:#e7d9ff;border:1px solid #4a2a7a;border-radius:5px;font-size:10px;padding:0 6px;line-height:1.6}
.gskill-hd .gsk-meta{font-size:10px;color:var(--ink3);font-weight:400;margin-left:auto}
/* story scene / event header inside the dialogue block */
.dlg-scene{font-size:11px;color:#7ad4ff;font-weight:600;margin:10px 0 4px;padding:2px 8px;background:#0c1f2a;border-left:3px solid #2a5a7a;border-radius:3px}
</style>"""

# the browser-side module (canvas glyph renderer + grid tabs + route weave)
GG_JS = r"""
(function(){
var GG;
var $=function(s,r){return (r||document).querySelector(s);};
var GRIDS={};
// Glyphs render as CSS background-image SPRITES, not <canvas>.  The 3 sprite
// sheets are decoded ONCE by the browser and shared by every glyph, so the page
// scales to tens of thousands of glyphs with no per-element backing store.  (The
// old canvas-per-line approach allocated ~17k canvases across the ID+Units tabs
// and exhausted the browser's canvas limit on lower-memory machines, so glyphs
// showed as broken-image boxes.)  injectSpriteCSS() defines one class per sheet;
// each glyph is a clipped <i> positioned onto the shared sheet.
function injectSpriteCSS(){
  if(document.getElementById('gg-sprite'))return;
  var st=document.createElement('style');st.id='gg-sprite';
  st.textContent=".ggs{display:inline-block;vertical-align:top;background-repeat:no-repeat;image-rendering:pixelated;image-rendering:crisp-edges}"
    +".gg-jp{background-image:url("+GG.sheets.jp.png+")}"
    +".gg-zh{background-image:url("+GG.sheets.zh.png+")}"
    +".gg-rb{background-image:url("+GG.sheets.rb.png+")}"
    +".gseg{display:inline-flex;align-items:flex-start;line-height:0;white-space:nowrap}";
  document.head.appendChild(st);
}
function b64u16(s){var bin=atob(s),a=new Uint16Array(bin.length>>1);for(var i=0;i<a.length;i++)a[i]=bin.charCodeAt(i*2)|(bin.charCodeAt(i*2+1)<<8);return a;}
// surface: 0=stage(renderA-direct, baseline +1) 1=bank(trampoline, +3); rom: 0=jp 1=zh
function drawSeg(g,surface,rom,scale){   // g = one line's u16 glyphs (no BREAK)
  scale=scale||2;
  var seg=document.createElement('span');seg.className='gseg';seg.style.height=(16*scale)+'px';
  for(var i=0;i<g.length;i++){var v=g[i];
    var font=(v>=0x8000)?1:0,slot=v&0x7FFF,key=font?'rb':(rom?'zh':'jp'),sh=GG.sheets[key];
    var cw=sh.cw,ch=sh.ch,cols=sh.cols,sx=(slot%cols)*cw,sy=((slot/cols)|0)*ch,yoff=font?0:(surface?3:1);
    var gi=document.createElement('i');gi.className='ggs '+(font?'gg-rb':(rom?'gg-zh':'gg-jp'));
    gi.style.width=(cw*scale)+'px';gi.style.height=(ch*scale)+'px';gi.style.marginTop=(yoff*scale)+'px';
    gi.style.backgroundPosition='-'+(sx*scale)+'px -'+(sy*scale)+'px';
    gi.style.backgroundSize=(cols*cw*scale)+'px auto';
    seg.appendChild(gi);
  }
  return seg;
}
// BREAK(0xFFFF)=in-game line break -> split into separate canvas lines, stacked
function drawLines(packed,surface,rom,scale){
  var g=b64u16(packed||''),lines=[[]],i;
  for(i=0;i<g.length;i++){if(g[i]===0xFFFF)lines.push([]);else lines[lines.length-1].push(g[i]);}
  var wrap=document.createElement('span');wrap.className='gf-lines';
  lines.forEach(function(seg){if(seg.length){wrap.appendChild(drawSeg(seg,surface,rom,scale));}else{var e=document.createElement('span');e.className='gf-el';wrap.appendChild(e);}});
  return wrap;
}
// -- field primitive: ZH text(big)+JP text(small) | ZH bitmap (hover reveals JP bitmap) --
function setLines(el,s){var p=(s||'').split('\u25bc'),i;for(i=0;i<p.length;i++){if(i)el.appendChild(document.createElement('br'));el.appendChild(document.createTextNode(p[i]));}}
function txtCol(zt,jt){
  var c=document.createElement('span');c.className='gf-txt';
  var z=document.createElement('span');z.className='zt';setLines(z,zt);c.appendChild(z);
  if(jt){var j=document.createElement('span');j.className='jt';setLines(j,jt);c.appendChild(j);}
  return c;
}
function bmpCol(zb,jb,surf,scale){
  var c=document.createElement('span');c.className='gf-bmp';
  // scrollable inner holds the (possibly wide) ZH bitmap; the JP popover is a
  // sibling of the scroller so overflow-x:auto never clips it (fixes Route hover)
  var scroll=document.createElement('span');scroll.className='gf-bmp-scroll';
  if(zb)scroll.appendChild(drawLines(zb,surf,1,scale));
  c.appendChild(scroll);
  if(jb){var pop=document.createElement('span');pop.className='pop';c.appendChild(pop);c.classList.add('hasjp');
    var built=false;c.addEventListener('mouseenter',function(){if(!built){built=true;pop.appendChild(drawLines(jb,surf,0,scale));}});}
  return c;
}
// f={zt,jt,zb,jb}; surf 0=stage 1=bank; stk=stack text over bitmap (long fields)
function field(f,surf,scale,label,cls,stk){
  var r=document.createElement('div');r.className='gf'+(stk?' stk':'')+(cls?' '+cls:'');
  if(label){var l=document.createElement('span');l.className='gf-lab';l.textContent=label;r.appendChild(l);}
  r.appendChild(txtCol(f.zt,f.jt));
  r.appendChild(bmpCol(f.zb,f.jb,surf,scale));
  return r;
}
function sect(host,title){var s=document.createElement('div');s.className='gsect';var t=document.createElement('div');t.className='gstitle';t.textContent=title;s.appendChild(t);host.appendChild(s);return s;}
function collSect(host,title,n){ // in-card collapsible (barks): body filled on first open
  var s=document.createElement('div');s.className='gsect';
  var t=document.createElement('div');t.className='gstitle click';t.innerHTML='<span class="tw">\u25b8</span>'+title+'<span class="cnt">'+n+'</span>';
  var b=document.createElement('div');b.className='collbody';b.style.display='none';var open=false;
  t.addEventListener('click',function(){open=!open;b.style.display=open?'block':'none';t.querySelector('.tw').textContent=open?'\u25be':'\u25b8';if(open&&b._fill){b._fill();b._fill=null;}});
  s.appendChild(t);s.appendChild(b);host.appendChild(s);return b;
}
// -- name header: ZH/JP text once, then TWO in-game renders (后台 renderB roster
//    trampoline + 战斗 renderA battle atlas) so cross-path garble is visible --
function nameHeader(nm,nmA){
  var h=document.createElement('div');h.className='gcard-hd';
  var t=document.createElement('div');t.className='gf name';t.appendChild(txtCol(nm.zt,nm.jt));h.appendChild(t);
  function row(tag,f,surf){
    if(!f||(!f.zb&&!f.jb))return;
    var r=document.createElement('div');r.className='gname-row';
    var l=document.createElement('span');l.className='gname-tag';l.innerHTML='<b>'+tag+'</b>';r.appendChild(l);
    r.appendChild(bmpCol(f.zb,f.jb,surf,2));h.appendChild(r);
  }
  row('\u540e\u53f0',nm,1);          // roster / renderB trampoline
  row('\u6218\u6597',nmA,0);         // in-battle / renderA atlas
  return h;
}
// -- Units tab card --
function buildUnit(bd,u){
  bd.appendChild(nameHeader(u.nm,u.nmA));
  if(u.weapons&&u.weapons.length){var s=sect(bd,'\u6b66\u5668 Weapons');u.weapons.forEach(function(w){s.appendChild(field(w,1,2));});}
  if(u.specials&&u.specials.length){var s2=sect(bd,'\u7279\u6b8a\u80fd\u529b / \u9632\u5fa1');u.specials.forEach(function(sp){s2.appendChild(field(sp,1,2,sp.kind==='defense'?'\u9632\u5fa1':'\u80fd\u529b'));});}
}
// a cut-in / effect field is "empty" if its ZH is blank or just 无/なし (no real quote)
function isNone(f){if(!f)return true;var z=(f.zt||'').replace(/[\u25bc\u3002\u00b7\s\u3000]/g,'');return z===''||z==='\u65e0'||z==='\u306a\u3057';}
// -- IDs/Quotes tab card: each ID skill (1/2/3) in its own clearly-boxed block --
function buildChar(bd,c){
  bd.appendChild(nameHeader(c.nm,c.nmA));
  c.ids.forEach(function(id,i){
    var box=document.createElement('div');box.className='gskill';
    var hd=document.createElement('div');hd.className='gskill-hd';
    var n=(id.slot!=null?id.slot:i)+1;
    hd.innerHTML='<span class="gsk-n">ID\u6280\u80fd '+n+'</span>';
    var meta=document.createElement('span');meta.className='gsk-meta';
    meta.textContent=(id.map?'\u5730\u56fe':'\u6218\u6597\u4e2d')+(id.tgt?' \u00b7 '+id.tgt:'');
    hd.appendChild(meta);box.appendChild(hd);
    box.appendChild(field(id.nm,1,2,'\u6307\u4ee4'));
    if(id.sm&&(id.sm.zt||id.sm.jt||id.sm.zb))box.appendChild(field(id.sm,1,2,'\u6548\u679c\u540d'));
    if(id.dt&&(id.dt.zt||id.dt.zb))box.appendChild(field(id.dt,0,2,'\u6548\u679c',null,true));
    if(id.cut&&!isNone(id.cut))box.appendChild(field(id.cut,0,2,'\u540d\u53f0\u8bcd',null,true));
    bd.appendChild(box);
  });
  if(c.barks&&c.barks.length){var b=collSect(bd,'\u6218\u6597\u558a\u8bdd Barks',c.barks.length);b._fill=function(){c.barks.forEach(function(bk){b.appendChild(field(bk,0,2,null,null,true));});};}
}
// -- Library tab item: bio (with owner name header) or part (name + caption) --
function buildLibItem(bd,it,sec){
  if(sec.kind==='part'){
    var h=document.createElement('div');h.className='gcard-hd';h.appendChild(field(it,0,2,null,'name'));bd.appendChild(h);
    if(it.cap&&(it.cap.zt||it.cap.zb)){var s=sect(bd,'\u8bf4\u660e Caption');s.appendChild(field(it.cap,0,2,null,null,true));}
    return;
  }
  // bio: show the owner (character/unit) name if known, then the bio text
  if(it.name&&(it.name.zt||it.name.jt)){
    var nh=document.createElement('div');nh.className='gcard-hd';
    var t=document.createElement('div');t.className='gf name';t.appendChild(txtCol(it.name.zt,it.name.jt));nh.appendChild(t);bd.appendChild(nh);
  }
  bd.appendChild(field(it,0,2,null,null,sec.long));
}
// -- grid: build all cards lazily on first view-show, chunked; pixels paint deferred.
// Robust triggers (tabs sometimes didn't render until refresh): IntersectionObserver
// + owning-tab-button click + immediate-if-visible, all idempotent. --
function grid(hostSel,items,idOf,build){
  var host=$(hostSel);if(!host)return;var built=false;
  function go(){if(built)return;built=true;
    var i=0;
    (function chunk(){var end=Math.min(i+40,items.length),frag=document.createDocumentFragment();
      for(;i<end;i++){var it=items[i],idx=idOf(it);
        var w=document.createElement('div');w.className='gcard';w._idx=idx;
        var ix=document.createElement('div');ix.className='gcard-ix';ix.textContent=idx;w.appendChild(ix);
        var bd=document.createElement('div');bd.className='gcard-bd';build(bd,it);w.appendChild(bd);frag.appendChild(w);}
      host.appendChild(frag);
      if(i<items.length)requestAnimationFrame(chunk);})();
  }
  // Deterministic trigger: register go() under its owning view; start() wraps the
  // base page's showView() so EVERY tab switch builds that view's grids.
  // IntersectionObserver + offsetParent are kept as backups (hash-nav / already-visible).
  var view=host.closest?host.closest('.view'):null,v=(view&&view.id)?view.id.replace('view-',''):null;
  if(v){(GRIDS[v]=GRIDS[v]||[]).push(go);}
  try{var io=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting){io.disconnect();go();}});});io.observe(host);}catch(e){go();}
  if(host.offsetParent!==null)go();
}
function buildView(v){var gs=GRIDS[v];if(gs)for(var i=0;i<gs.length;i++)gs[i]();}
function wireFilter(inp,gridsel){var el=$(inp);if(!el)return;el.addEventListener('input',function(e){var q=e.target.value.trim();Array.prototype.forEach.call($(gridsel).children,function(cd){cd.style.display=(!q||(cd._idx||'').indexOf(q)>=0)?'':'none';});});}
// -- tabs --
function addTab(v,label,em){var b=document.createElement('button');b.dataset.v=v;b.innerHTML=label+'<span class="em">'+em+'</span>';$('#tabs').appendChild(b);}
function addView(v){var s=document.createElement('section');s.className='view';s.id='view-'+v;$('main').appendChild(s);return s;}
function initTabs(){
  addTab('ggids','ID/\u540d\u53f0\u8bcd','Quotes');addTab('ggunits','\u673a\u4f53/\u6b66\u5668','Units');
  var vi=addView('ggids');
  vi.innerHTML='<h2 class="sec">\u89d2\u8272 \u00b7 ID\u6307\u4ee4 \u00b7 \u540d\u53f0\u8bcd \u00b7 \u6218\u6597\u558a\u8bdd <span class="muted" style="font-size:13px">'+GG.chars.length+' \u540d</span></h2>'+
    '<p class="gg-intro">\u7531\u811a\u672c\u4ece\u6e38\u620f\u89d2\u8272\u6570\u7ec4\uff08char-DB 0xDCF18\uff09\u53cd\u89e3\u3001\u6309\u6e38\u620f\u6e32\u67d3\u7ba1\u7ebf\u8fd8\u539f\u3002\u6bcf\u683c\u663e\u793a<b>\u4e2d\u6587\u6587\u672c\uff08\u5927\uff09\uff0b\u65e5\u6587\u6587\u672c\uff08\u5c0f\uff09\uff0b\u4e2d\u6587\u4f4d\u56fe</b>\uff1b\u9f20\u6807\u60ac\u505c\u4f4d\u56fe\u53ef\u770b<b>\u65e5\u6587\u4f4d\u56fe</b>\u5bf9\u7167\u3002\u6bcf\u5f20\u5361\u7247\u5168\u5c55\u5f00\uff0c\u6218\u6597\u558a\u8bdd\u9ed8\u8ba4\u6298\u53e0\u3002</p>'+
    '<div class="gg-search"><input id="ggidq" placeholder="\u6309\u5e8f\u53f7\u7b5b\u9009\u2026"></div>'+
    '<div id="ggidgrid" class="ggrid"></div>';
  grid('#ggidgrid',GG.chars,function(c){return '#'+c.cid;},buildChar);
  var vu=addView('ggunits');
  vu.innerHTML='<h2 class="sec">\u673a\u4f53 \u00b7 \u6b66\u5668 \u00b7 \u7279\u6b8a\u80fd\u529b <span class="muted" style="font-size:13px">'+GG.units.length+' \u53f0</span></h2>'+
    '<p class="gg-intro">\u7531\u811a\u672c\u4ece\u673a\u4f53\u4e3b\u8868\uff080xB94BC\uff09\u53cd\u89e3\u3002\u6bcf\u5f20\u5361\u7247\u5168\u5c55\u5f00\uff1a\u673a\u4f53\u540d\u3001\u5404\u6b66\u5668\u3001\u5404\u7279\u6b8a\u80fd\u529b/\u9632\u5fa1\uff0c\u5747<b>\u4e2d\u6587\u6587\u672c\uff0b\u65e5\u6587\u6587\u672c\uff0b\u4e2d\u6587\u4f4d\u56fe</b>\uff0c\u60ac\u505c\u4f4d\u56fe\u770b\u65e5\u6587\u4f4d\u56fe\u3002</p>'+
    '<div class="gg-search"><input id="ggunitq" placeholder="\u6309\u5e8f\u53f7\u7b5b\u9009\u2026"></div>'+
    '<div id="ggunitgrid" class="ggrid"></div>';
  grid('#ggunitgrid',GG.units,function(u){return '#'+u.utid;},buildUnit);
  var lib=GG.library||[];
  if(lib.length){
    addTab('ggenc','\u8d44\u6599\u5e93','Library');
    var vl=addView('ggenc');
    var tot=lib.reduce(function(a,s){return a+s.n;},0);
    var lh='<h2 class="sec">\u8d44\u6599\u9986 \u00b7 \u56fe\u9274 <span class="muted" style="font-size:13px">'+tot+' \u6761</span></h2>'+
      '<p class="gg-intro">\u6e38\u620f\u8d44\u6599\u9986\uff08library / hangar\uff09\uff1a\u89d2\u8272\u4e0e\u673a\u4f53\u56fe\u9274\u4f20\u8bb0\u3001\u6539\u9020\u90e8\u4ef6\u540d\u4e0e\u8bf4\u660e\u3002\u6bcf\u6761<b>\u4e2d\u6587\u6587\u672c\uff0b\u65e5\u6587\u6587\u672c\uff0b\u4e2d\u6587\u4f4d\u56fe</b>\uff0c\u60ac\u505c\u4f4d\u56fe\u770b\u65e5\u6587\u4f4d\u56fe\u3002</p>';
    lib.forEach(function(sec,si){lh+='<h3 class="sec" style="font-size:15px;margin-top:18px">'+sec.title+' <span class="muted" style="font-size:12px">'+sec.n+'</span></h3><div id="ggenc'+si+'" class="ggrid"></div>';});
    vl.innerHTML=lh;
    lib.forEach(function(sec,si){grid('#ggenc'+si,sec.items,function(it){return '#'+it.ix;},function(bd,it){buildLibItem(bd,it,sec);});});
  }
  wireFilter('#ggidq','#ggidgrid');wireFilter('#ggunitq','#ggunitgrid');
}
// -- Route tab: weave dialogue (speakers + badges) + briefing per stage --
function badge(txt,cls){var b=document.createElement('span');b.className='gbadge '+cls;b.textContent=txt;return b;}
function dlgLine(ln){
  var r=document.createElement('div');r.className='gf dlg';
  var meta=document.createElement('span');meta.className='dlg-meta';
  var nm=(ln.sp!=null&&GG.names[ln.sp])?GG.names[ln.sp]:null;
  if(nm){var sp=document.createElement('span');sp.className='spk';sp.textContent=nm.zt+'\uff1a';if(nm.jt)sp.title=nm.jt;meta.appendChild(sp);}
  if(ln.b)meta.appendChild(badge('\u5206\u652f','br'));
  if(ln.c)meta.appendChild(badge('\u9009\u62e9','ch'));
  if(ln.ev)meta.appendChild(badge('\u4e8b\u4ef6','ev'));
  r.appendChild(meta);
  var col=document.createElement('span');col.className='dlg-col';
  col.appendChild(txtCol(ln.zt,ln.jt));col.appendChild(bmpCol(ln.zb,ln.jb,0,2));
  r.appendChild(col);return r;
}
function collBlock(title,cnt){
  var wrap=document.createElement('div');wrap.className='gcoll';
  var hd=document.createElement('div');hd.className='gcoll-hd';hd.innerHTML='<span class="tw">\u25b8</span><b>'+title+'</b><span class="cnt">'+cnt+'</span>';
  var body=document.createElement('div');body.className='gcoll-bd';body.style.display='none';var open=false;
  hd.addEventListener('click',function(){open=!open;body.style.display=open?'block':'none';hd.querySelector('.tw').textContent=open?'\u25be':'\u25b8';if(open&&body._fill){body._fill();body._fill=null;}});
  wrap.appendChild(hd);wrap.appendChild(body);return {wrap:wrap,body:body};
}
var stagesByGid={},extraStages=[];
function indexStages(){stagesByGid={};extraStages=[];GG.stages.forEach(function(s){if(s.gid){(stagesByGid[s.gid]=stagesByGid[s.gid]||[]).push(s);}else{extraStages.push(s);}});}
function normT(s){if(!s)return '';return s.replace(/[\u25a1\s\u3000\uff08\uff09()\uff3b\uff3d\[\]\uff0c\u3001\u3002.!\uff01?\uff1f\-\u30fc\uff0d\u30fb:\uff1a]/g,'').replace(/\u7bc7/g,'\u7de8');}
function lcsLen(a,b){var m=a.length,n=b.length,i,j,best=0,prev=new Array(n+1),cur=new Array(n+1);for(j=0;j<=n;j++)prev[j]=0;for(i=1;i<=m;i++){cur[0]=0;for(j=1;j<=n;j++){cur[j]=(a.charAt(i-1)===b.charAt(j-1))?prev[j-1]+1:0;if(cur[j]>best)best=cur[j];}var t=prev;prev=cur;cur=t;}return best;}
// match a Route stage to its briefing by the descriptor LABEL (stage number),
// not by fuzzy title text — the title-LCS heuristic mis-attached stage 01
// (宿命の出会い) to X6b (宿命の戦い).  labs carry every owning stage's number
// (00/00SP/01 share one briefing).  Normalize dash/case and 後→后 / 篇.
function normLab(s){return (s||'').toString().toLowerCase().replace(/[-\s\u30fb]/g,'').replace(/\u5f8c/g,'\u540e').replace(/\u7bc7/g,'');}
function bestBrief(s){var id=normLab(s&&s.id);if(!id)return null;var found=null;
  GG.briefings.forEach(function(b){(b.labs||[]).forEach(function(l){if(normLab(l)===id)found=b;});});
  return found;}
function sceneName(sc){return sc<0?'\u5176\u5b83/\u672a\u89e6\u8fbe':'\u573a\u666f';}
function appendScenedLines(host,s){
  // renumber the distinct scene ids to sequential 1..N for display
  var scMap={},scN=0;
  s.lines.forEach(function(ln){if(ln.sc!=null&&ln.sc>=0&&!(ln.sc in scMap))scMap[ln.sc]=++scN;});
  var lastSc=undefined;
  s.lines.forEach(function(ln){
    if(s.nsc>1&&ln.sc!=null&&ln.sc!==lastSc){
      lastSc=ln.sc;
      var sh=document.createElement('div');sh.className='dlg-scene';
      sh.textContent=(ln.sc<0?'\u5176\u5b83 / \u672a\u89e6\u8fbe\u5bf9\u8bdd':'\u573a\u666f '+(scMap[ln.sc]||'?'));
      host.appendChild(sh);
    }
    host.appendChild(dlgLine(ln));
  });
}
function dialogueBlock(stageList){
  var tot=0;stageList.forEach(function(s){tot+=s.n;});
  var c=collBlock('\u5267\u60c5\u5bf9\u8bdd\uff08\u6309\u6e38\u620f\u5185\u6f14\u51fa\u987a\u5e8f \u00b7 \u542b\u8bf4\u8bdd\u4eba\uff09',tot+' \u6bb5');
  c.body._fill=function(){stageList.forEach(function(s){
    if(stageList.length>1){var h=document.createElement('div');h.className='dlg-file';h.textContent=s.file;c.body.appendChild(h);}
    appendScenedLines(c.body,s);});};
  return c.wrap;
}
function briefingBlock(s){
  var b=bestBrief(s);if(!b)return null;
  var c=collBlock('\u4f5c\u6218\u7b80\u62a5\uff08\u4f5c\u6226\u5185\u5bb9\uff09',b.n+' \u6bb5');
  c.body._fill=function(){b.lines.forEach(function(ln){c.body.appendChild(field(ln,0,2,null,null,true));});};
  return c.wrap;
}
function weaveStage(){
  var orig=window.stageDetail;if(!orig)return;
  window.stageDetail=function(s){orig(s);var pb=$('#pbody');if(!pb)return;
    var bb=briefingBlock(s);if(bb)pb.appendChild(bb);
    var list=stagesByGid[s.id];if(list)pb.appendChild(dialogueBlock(list));};
}
function addShared(){
  if(!GG.shared||!GG.shared.length)return;var host=$('#view-stages');if(!host)return;
  var c=collBlock('\u5404\u5173\u5361\u901a\u7528\u6a21\u677f\u6587\u672c\uff08\u5982\u201c\u7279\u522b\u6f14\u4e60\u201d\u7b49 \u00b7 \u4ec5\u5217\u4e00\u6b21\uff09',GG.shared.length+' \u6bb5');
  c.body._fill=function(){GG.shared.forEach(function(ln){c.body.appendChild(field(ln,0,2,null,null,true));});};
  var wrap=document.createElement('div');wrap.className='ggwrap';wrap.appendChild(c.wrap);host.appendChild(wrap);
}
function addExtras(){
  if(!extraStages.length)return;var host=$('#view-stages');if(!host)return;
  var sec=document.createElement('div');sec.className='ggwrap';
  sec.innerHTML='<h2 class="sec" style="margin-top:26px">\u6e38\u620f\u5185\u989d\u5916\u5173\u5361 <span class="muted" style="font-size:13px">\uff08\u65e0\u653b\u7565\u5361 \u00b7 '+extraStages.length+'\uff09</span></h2>';
  var g=document.createElement('div');g.className='ggrid';
  extraStages.forEach(function(s){g.appendChild((function(){var w=document.createElement('div');w.className='gcard';w._idx=s.file;var ix=document.createElement('div');ix.className='gcard-ix';ix.textContent=s.file;w.appendChild(ix);var bd=document.createElement('div');bd.className='gcard-bd';var c=collBlock('\u5267\u60c5\u6587\u672c',s.n+' \u6bb5');c.body._fill=function(){appendScenedLines(c.body,s);};bd.appendChild(c.wrap);w.appendChild(bd);return w;})());});
  sec.appendChild(g);host.appendChild(sec);
}
function addToggle(){
  var b=document.createElement('button');b.id='gg-bmptoggle';b.textContent='\u9690\u85cf\u4f4d\u56fe';
  b.addEventListener('click',function(){var off=document.body.classList.toggle('gg-nobmp');b.textContent=off?'\u663e\u793a\u4f4d\u56fe':'\u9690\u85cf\u4f4d\u56fe';});
  document.body.appendChild(b);
}
function start(){window.GG=GG;indexStages();injectSpriteCSS();
  // Build the whole UI up front (tabs appear instantly).
  initTabs();weaveStage();addShared();addExtras();addToggle();
  // Hook the base page's showView so each tab switch deterministically builds its
  // grids (fixes "tabs sometimes don't load until refresh").
  var _sv=window.showView;
  if(typeof _sv==='function'){window.showView=function(v){var r=_sv.apply(this,arguments);try{buildView(v);}catch(e){}return r;};}
  var cur=$('#tabs button.on');if(cur&&cur.dataset)try{buildView(cur.dataset.v);}catch(e){}
}
function boot(){
  var raw=document.getElementById('gg-data').textContent.trim();
  var bytes=Uint8Array.from(atob(raw),function(c){return c.charCodeAt(0);});
  if(typeof DecompressionStream!=='undefined'){
    new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text()
      .then(function(t){GG=JSON.parse(t);start();})
      .catch(function(err){console.error('gg-data decompress failed',err);});
  }else{
    alert('\u6b64\u653b\u7565\u7684\u6e38\u620f\u6570\u636e\u9700\u8981\u652f\u6301 DecompressionStream \u7684\u6d4f\u89c8\u5668\uff08Chrome/Edge/Firefox/Safari \u65b0\u7248\uff09\u3002');
  }
}
boot();
})();
"""


def generate_html(gd: dict, html_in: Path, html_out: Path):
    import gzip
    import json as _json
    import re
    html = Path(html_in).read_text(encoding="utf-8")
    html = re.sub(r"<!--GG:START-->.*?<!--GG:END-->\n?", "", html, flags=re.S)
    data_json = _json.dumps(gd, ensure_ascii=False, separators=(",", ":"))
    # gzip + base64 the payload (halves the file); the browser inflates it via
    # DecompressionStream at load.  mtime=0 keeps the output byte-reproducible.
    gz = gzip.compress(data_json.encode("utf-8"), compresslevel=9, mtime=0)
    payload = base64.b64encode(gz).decode()
    block = ("<!--GG:START-->\n" + GG_STYLE +
             '\n<script id="gg-data" type="application/octet-stream">' + payload + "</script>\n"
             "<script>" + GG_JS + "</script>\n<!--GG:END-->\n")
    html = html.replace("</body>", block + "</body>")
    Path(html_out).write_text(html, encoding="utf-8")
    return len(html)



# ===========================================================================
# self-test + dev harnesses
# ===========================================================================
def _selftest(jp_path: str, zh_path: str):
    import json as _json
    zh = GameROM(Path(zh_path))
    print(f"ZH atlas slots: {zh.atlas_slots}  segs: {[(hex(a),hex(b)) for a,b,_ in zh._segs]}")
    # compare the extractor's glyph_stream to the parity-anchored render_oracle
    sys.path.insert(0, str(REPO / "test"))
    from render_oracle import Oracle
    orc = Oracle(Path(zh_path))
    tests = []
    doc = _json.loads((REPO / "data/zh/stages/_STG01.json").read_text())
    for blk in doc.get("edits", [])[:3]:
        if isinstance(blk, dict) and blk.get("zh_hex"):
            tests.append(("stage", bytes.fromhex(blk["zh_hex"])))
    nb = _json.loads((REPO / "data/zh/placements/battle_name_pool.json").read_text())
    for e in nb.get("entries", [])[:3]:
        if e.get("payload_hex"):
            tests.append(("bank", bytes.fromhex(e["payload_hex"])))
    ok = 0
    for surf, data in tests:
        mine = list(glyph_stream(zh, data, surf))
        theirs = list(orc.glyph_stream(data, surf))
        match = mine == theirs
        ok += match
        print(f"  [{surf}] {len(mine)} glyphs  match_oracle={match}")
        if not match:
            print("   mine :", mine[:20])
            print("   oracle:", theirs[:20])
    print(f"glyph_stream parity: {ok}/{len(tests)}")


def _render_samples(jp_path: str, zh_path: str, out_png: str):
    """Dev-only: contact sheet of sample names/weapons for VLM verification."""
    from PIL import Image, ImageDraw
    jp = GameROM(Path(jp_path))
    zh = GameROM(Path(zh_path))
    ju = {u["utid"]: u for u in W.units(jp)}
    zu = {u["utid"]: u for u in W.units(zh)}
    rows = []  # (label, image)

    def add(label, data, surface, rom_kind, exp=None):
        rows.append((label, render_line(jp, zh, data, surface, rom_kind, 3, exp)))

    def _name(rom, u):
        return rom.cstr(int(u["name"]["ptr"], 16)) if u.get("name") else None

    for utid in (1, 639, 335):  # ∀高达, Eternal, Char's custom
        if utid in zu and zu[utid].get("name"):
            add(f"u{utid} JP roster(B)", _name(jp, ju[utid]), "bank", "jp", jp.expand_sys)
            add(f"u{utid} ZH roster(B)", _name(zh, zu[utid]), "bank", "zh")
            add(f"u{utid} ZH battle(A)", _name(zh, zu[utid]), "stage", "zh")
            jw = {w["slot"]: jp.cstr(int(w["ptr"], 16)) for w in ju.get(utid, {}).get("weapons", [])}
            for w in zu[utid]["weapons"][:2]:
                slot = w["slot"]
                if slot in jw:
                    add(f"u{utid} JP wpn{slot}(B)", jw[slot], "bank", "jp", jp.expand_sys)
                add(f"u{utid} ZH wpn{slot}(A)", zh.cstr(int(w["ptr"], 16)), "stage", "zh")
    jp_pil = {p["cid"]: p for p in W.pilots(jp)}
    zh_pil = {p["cid"]: p for p in W.pilots(zh)}
    for cid in (18, 91, 1, 10):   # Amuro, Char, Aina, ...
        if cid in zh_pil:
            add(f"c{cid} JP roster(B)", _name(jp, jp_pil[cid]), "bank", "jp", jp.expand_sys)
            add(f"c{cid} ZH roster(B)", _name(zh, zh_pil[cid]), "bank", "zh")
            add(f"c{cid} ZH battle(A)", _name(zh, zh_pil[cid]), "stage", "zh")

    pad, lblw = 6, 220
    W_px = lblw + max((im.width for _l, im in rows), default=100) + pad * 2
    H_px = sum(im.height + 8 for _l, im in rows) + pad * 2
    sheet = Image.new("RGB", (W_px, H_px), (18, 22, 34))
    d = ImageDraw.Draw(sheet)
    y = pad
    for label, im in rows:
        d.text((4, y + 6), label, fill=(150, 200, 120))
        sheet.paste(im, (lblw, y), im)
        y += im.height + 8
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    print(f"wrote {out_png} ({len(rows)} rows)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jp", default="0098 - SD Gundam G Generation DS (Japan).nds",
                    help="Japanese source ROM")
    ap.add_argument("--zh", default="sd-gundam-g-generation-zh.nds",
                    help="built Chinese ROM")
    ap.add_argument("--html", default=str(REPO / "攻略.html"),
                    help="guide HTML to enhance (read)")
    ap.add_argument("--out", default=str(REPO / "攻略.html"),
                    help="output HTML (default: overwrite --html in place)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--render-samples", metavar="PNG")
    args = ap.parse_args()
    if args.selftest:
        _selftest(args.jp, args.zh)
        return 0
    if args.render_samples:
        _render_samples(args.jp, args.zh, args.render_samples)
        return 0
    import time
    t0 = time.time()
    print(f"[guide] reading ROMs: JP={args.jp}  ZH={args.zh}")
    jp = GameROM(Path(args.jp))
    zh = GameROM(Path(args.zh))
    if not zh.is_zh:
        print("[guide] ERROR: --zh is not a translated build (no appended atlas)")
        return 1
    print("[guide] extracting game arrays (dialogue, characters, units) …")
    gd = build_gamedata(jp, zh)
    nlines = sum(s["n"] for s in gd["stages"])
    print(f"[guide]   {len(gd['stages'])} stages / {nlines} dialogue lines, "
          f"{len(gd['chars'])} characters, {len(gd['units'])} units")
    size = generate_html(gd, Path(args.html), Path(args.out))
    print(f"[guide] wrote {args.out}  ({size/1e6:.1f} MB)  in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
