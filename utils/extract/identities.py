"""Glyph-identity tables + per-surface text decode (the honest ROM transcription).

The game stores glyph SLOT streams, not Unicode.  A readable transcription maps
each rendered slot to its identified character:

  * renderA (12x12 atlas) identity: data/charmap.json — `one_byte`,
    `jp_slot_chars` (original JP identities), `slot_chars_extra` (evidence-based
    decode-only refinements, OVERRIDE the base label), `two_byte_zh` (ZH-minted
    identities; win on the ZH ROM only — the JP ROM keeps the truthful JP label).
  * renderB (8x16 UI font) identity: data/renderb_charset.json `slots`
    (word-level-proof identities across the full 2093-glyph bank).

`decode_text` mirrors the console's two render paths: on a 'bank' (trampoline)
surface slots < 2196 draw from renderB, everything else from the atlas.
Unidentified slots decode as {SLOT:n} / {B:n} escapes — loss-aware, never wrong.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import layout as L
from .gamerom import GameROM

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


@dataclass
class Ident:
    atlas_jp: dict[int, str]     # slot -> char as the JAPANESE ROM draws it
    atlas_zh: dict[int, str]     # slot -> char as the TRANSLATED ROM draws it
    renderb: dict[int, str]      # renderB slot -> char


@lru_cache(maxsize=None)
def load_identities() -> Ident:
    cm = json.loads((DATA_DIR / "charmap.json").read_text())
    jpv: dict[int, str] = {}
    for ch, code in cm["one_byte"].items():
        jpv.setdefault(int(code), ch)
    for s, ch in cm["jp_slot_chars"].items():
        jpv.setdefault(int(s), ch)
    # evidence-based identity corrections MUST override the base label
    for s, ch in cm.get("slot_chars_extra", {}).items():
        jpv[int(s)] = ch
    # ZH-render identities: reclaimed cells draw the minted Chinese glyph on the
    # translated ROM, so two_byte_zh WINS there; the JP ROM keeps jpv.
    zhv = dict(jpv)
    for ch, s in cm["two_byte_zh"].items():
        zhv[int(s)] = ch
    rb: dict[int, str] = {}
    rc = json.loads((DATA_DIR / "renderb_charset.json").read_text())["slots"]
    for s, info in rc.items():
        if info.get("char"):
            rb[int(s)] = info["char"]
    return Ident(jpv, zhv, rb)


def glyph_stream(rom: GameROM, data: bytes, surface: str, expander=None,
                 _depth: int = 0):
    """Yield (slot, font) per drawn glyph.  font 'A' = 12x12 atlas, 'B' = 8x16.

    surface 'stage' = renderA-direct (every slot from the atlas);
    surface 'bank'  = trampoline (slot < 2196 -> renderB, else atlas).
    Controls/separators are skipped; 0xF0xx macros expand recursively."""
    expand = expander or rom.expand
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b >= 0xF0 and i + 1 < n:
            idx = ((b << 8) | data[i + 1]) - 0xF000
            sub = expand(idx)
            if sub is not None and _depth < 6:
                yield from glyph_stream(rom, sub, surface, expander, _depth + 1)
            i += 2
            continue
        if b >= 0xE0 and i + 1 < n:
            slot = ((b << 8) | data[i + 1]) - 0xE000 + 224
            i += 2
        else:
            slot = b
            i += 1
            if slot < 0x02:          # 0x00 terminator/sep, 0x01 blank/control
                continue
        if surface == "bank" and slot < L.TRAMPOLINE_SPLIT:
            yield slot, "B"
        else:
            yield slot, "A"


def glyph_count(rom: GameROM, data: bytes, surface: str, expander=None) -> int:
    return sum(1 for _ in glyph_stream(rom, data, surface, expander))


def decode_text(rom: GameROM, data: bytes, surface: str, expander=None,
                dialogue: bool = False, _depth: int = 0) -> str:
    """Unicode transcription of the rendered slots (per-surface, ROM-aware).

    Escapes: {00} page/segment separator (dialogue), {SLOT:n} unidentified
    atlas glyph, {B:n} unidentified renderB glyph, {F0:n} unresolvable macro.
    """
    ident = load_identities()
    expand = expander or rom.expand
    amap = ident.atlas_zh if rom.is_zh else ident.atlas_jp
    out: list[str] = []
    i = 1 if (dialogue and data[:1] == b"\x15") else 0
    n = len(data)
    while i < n:
        b = data[i]
        if b >= 0xF0 and i + 1 < n:
            idx = ((b << 8) | data[i + 1]) - 0xF000
            sub = expand(idx)
            if sub is not None and _depth < 6:
                out.append(decode_text(rom, sub, surface, expand, False, _depth + 1))
            else:
                out.append("{F0:%d}" % idx)
            i += 2
            continue
        if b >= 0xE0 and i + 1 < n:
            slot = ((b << 8) | data[i + 1]) - 0xE000 + 224
            font = "B" if (surface == "bank" and slot < L.TRAMPOLINE_SPLIT) else "A"
            i += 2
        else:
            slot = b
            i += 1
            if slot == 0x00:
                out.append("{00}")
                continue
            if slot == 0x01:
                out.append("{01}")
                continue
            font = "B" if (surface == "bank" and slot < L.TRAMPOLINE_SPLIT) else "A"
        ch = (ident.renderb.get(slot) if font == "B" else amap.get(slot))
        if ch is None:
            ch = ("{B:%d}" if font == "B" else "{SLOT:%d}") % slot
        out.append(ch)
    s = "".join(out)
    # trailing terminators are record padding, not content
    while s.endswith("{00}"):
        s = s[:-4]
    return s
