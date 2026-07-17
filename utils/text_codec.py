"""Game text codec: decode/encode strings for the glyph-atlas text system.

The game stores text as a byte stream over a glyph atlas:

  * 1-byte codes ``0x02..0xDF`` — the code IS the glyph slot (atlas slots 2..223:
    punctuation, digits, kana, Latin, a few very common kanji).
    ``0x00`` = terminator / segment separator, ``0x01`` = control (layout) code,
    other non-glyph codes below 0xE0 that appear in script blocks are engine
    control codes, never produced by this encoder.
  * 2-byte tokens ``0xE0..0xEF`` high byte — glyph token: slot = ((hi-0xE0)<<8|lo)+224.
    Slots 224..2195 are the original Japanese glyphs; slots 2196+ are the added
    Chinese glyphs (see data/charmap.json).
  * 2-byte tokens ``0xF0..0xFF`` high byte — dictionary macro: index = token-0xF000
    into a u16-offset-table string dictionary inside the code binary (arm9). Used
    by the original game as text compression; expanded text is again this codec.

data/charmap.json holds the three mapping tables (one_byte, two_byte_zh,
jp_slot_chars). This module is the ONE place that understands the byte format;
everything else (stage dialogue, name tables, UI labels) builds on it.
"""
from __future__ import annotations

import json
import struct
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

GLYPH_TOKEN_BASE = 0xE000     # first 2-byte glyph token
MACRO_TOKEN_BASE = 0xF000     # first 2-byte dictionary-macro token
TWO_BYTE_SLOT_OFFSET = 224    # token 0xE000 -> glyph slot 224
ZH_BAND_MIN = 2196            # atlas slots >= this: injected ZH glyph band
                              # (also the renderA/renderB trampoline split)
GLYPH_CELL_BYTES = 36         # one 12x12 1bpp-padded glyph bitmap in the atlas
CONTROL_BYTE = 0x01           # inline control/layout code (1 byte)
TERMINATOR = 0x00


class Charmap:
    """Bidirectional char <-> code tables loaded from data/charmap.json."""

    def __init__(self, path: Path | None = None):
        raw = json.loads((path or DATA_DIR / "charmap.json").read_text())
        # encode tables
        self.one_byte: dict[str, int] = raw["one_byte"]              # char -> 1-byte code
        self.two_byte_zh: dict[str, int] = raw["two_byte_zh"]        # char -> slot (2196+)
        # decode tables
        self.slot_to_char: dict[int, str] = {}
        for ch, code in self.one_byte.items():
            self.slot_to_char.setdefault(code, ch)                   # slot == code (<224)
        for slot_s, ch in raw["jp_slot_chars"].items():
            self.slot_to_char.setdefault(int(slot_s), ch)            # JP slots 224..2195
        for ch, slot in self.two_byte_zh.items():
            self.slot_to_char.setdefault(slot, ch)                   # ZH slots 2196+
        # decode-side refinements: slots whose glyph was established from in-game
        # text evidence later; they override DECODING only — encoding preferences
        # (and therefore build output) are deliberately unaffected.
        for slot_s, ch in raw.get("slot_chars_extra", {}).items():
            self.slot_to_char[int(slot_s)] = ch
        # slot_chars_extra stays decode-only: entries whose identity has been
        # bitmap-verified get PROMOTED into two_byte_zh in data/charmap.json
        # (verification procedure: docs/LESSONS_LEARNED.md §G / AGENTS.md
        # "Glyph minting"); unverified/unsure entries must never become
        # encode targets.
        self.zh_extra_slots: dict[str, int] = {}   # deliberately empty (see above)
        # char -> JP slot (for chars only present as original Japanese glyphs)
        self.jp_char_to_slot: dict[str, int] = {}
        for slot_s, ch in raw["jp_slot_chars"].items():
            slot = int(slot_s)
            if slot >= TWO_BYTE_SLOT_OFFSET:
                self.jp_char_to_slot.setdefault(ch, slot)
        self.text_bytes: set[int] = set(self.one_byte.values()) | {TERMINATOR}

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _token_hazard(slot: int, allow_low15: bool = False) -> bool:
        """A 2-byte glyph token whose low byte is 0x00 is misread as a
        terminator by every byte-walking consumer — never emit one.  A 0x15
        low byte is only dangerous inside stage-script files (0x15 is the
        show-dialogue opcode); flat text banks may use such tokens (and the
        shipped banks do, e.g. 糟 = 0xE915)."""
        if slot < TWO_BYTE_SLOT_OFFSET:
            return False
        lo = (slot - TWO_BYTE_SLOT_OFFSET + GLYPH_TOKEN_BASE) & 0xFF
        if lo == 0x00:
            return True
        return lo == 0x15 and not allow_low15

    def slot_of(self, ch: str, allow_low15: bool = False,
                surface: str = "stage",
                allowed_one_bytes: frozenset[int] = frozenset()) -> int | None:
        """Preferred encodable glyph slot for a character, PER SURFACE.

        surface="stage"  (renderA-direct: stage dialogue, barks, cut-ins,
            library/hangar banks): every slot renders from the 12x12 atlas,
            so the cheap one-byte codes (atlas slots 0..223) are preferred,
            then ZH slots, then JP slots.
        surface="bank"   (trampoline: data/zh/placements banks, the four battle
            effect banks, ID-command/ability panels, battle-info rows): slots
            < 2196 render from the renderB 8x16 JP UI font whose charset
            DIFFERS from the atlas (atlas 206 = 兵 but renderB 206 = 無).
            A one-byte code is only allowed when that byte value already
            occurs in the record's ORIGINAL JP span (`allowed_one_bytes`) —
            i.e. it is record structure (00/03-family framing) or a
            renderB-meaning byte the JP reader is known to accept.  Otherwise
            only ZH-band slots (>= 2196) are safe; JP-band two-byte slots are
            forbidden.  Returns None when the char has no safe encoding: the
            caller must reword, not fall back (that fallback is exactly the
            吉翁海兵→吉翁海無 garble bug).

        Any slot whose 2-byte token would carry a forbidden low byte is
        skipped (see _token_hazard)."""
        if ch in self.one_byte:
            code = self.one_byte[ch]
            if surface == "stage" or code in allowed_one_bytes:
                return code
        slot = self.two_byte_zh.get(ch)
        if slot is not None and not self._token_hazard(slot, allow_low15):
            # JP-band registrations (simplified chars minted into reclaimed
            # slots < 2196) are renderA-atlas cells: legal on stage surfaces
            # only.  On a trampoline surface that token would render the
            # renderB glyph of the SAME slot number — a different character.
            if slot >= ZH_BAND_MIN or surface == "stage":
                return slot
        if surface == "bank":
            # ZH-band only; bitmap-verified extras are the approved fallback
            slot = self.zh_extra_slots.get(ch)
            if slot is not None and not self._token_hazard(slot, allow_low15):
                return slot
            return None
        slot = self.jp_char_to_slot.get(ch)
        if slot is not None and not self._token_hazard(slot, allow_low15):
            return slot
        # last resort on stage surfaces too (keeps proven JP bytes stable)
        slot = self.zh_extra_slots.get(ch)
        if slot is not None and not self._token_hazard(slot, allow_low15):
            return slot
        return None


@lru_cache(maxsize=None)
def load_charmap() -> Charmap:
    return Charmap()


def token_at(data: bytes, i: int) -> tuple[int, int]:
    """(token_value, byte_len) of the code unit at data[i] (grammar-aware)."""
    b = data[i]
    if b >= 0xE0:
        if i + 1 >= len(data):
            return b, 1                       # truncated tail byte; treat as raw
        return (b << 8) | data[i + 1], 2
    return b, 1


def iter_tokens(data: bytes):
    """Yield (offset, token_value, byte_len) over a text byte stream.

    IMPORTANT: any byte >= 0xE0 opens a 2-byte token whose LOW byte may be any
    value (including 0x00 and 0x15) — a byte-wise scan mis-parses such streams.
    """
    i = 0
    while i < len(data):
        tok, ln = token_at(data, i)
        yield i, tok, ln
        i += ln


def find_terminator(data: bytes, start: int) -> int:
    """Token-aware offset of the first standalone 00 00 pair at/after start.

    This is how the game's own text walker finds the end of a text block.
    Returns -1 if the stream ends first."""
    n = len(data)
    j = start
    while j < n - 1:
        if data[j] >= 0xE0:
            j += 2
            continue
        if data[j] == 0x00 and data[j + 1] == 0x00:
            return j
        j += 1
    return -1


def make_macro_expander(code_bin: bytes, table_off: int):
    """Expander for 0xF0xx macros: index -> raw entry bytes (this codec again).

    The dictionary is a u16[N] offset table at table_off inside the code binary;
    entry i lives at table_off + u16[i], NUL-terminated. N = u16[0] / 2."""
    n = struct.unpack_from("<H", code_bin, table_off)[0] // 2

    def expand(idx: int) -> bytes | None:
        if 0 <= idx < n:
            off = struct.unpack_from("<H", code_bin, table_off + idx * 2)[0]
            s = table_off + off
            e = code_bin.find(b"\x00", s)
            return code_bin[s:e] if e >= 0 else code_bin[s:]
        return None

    return expand


def decode(data: bytes, charmap: Charmap | None = None, expander=None,
           control_escapes: bool = True) -> str:
    """Decode a text byte stream to readable text.

    Unknown/control code units become escapes like ``{01}`` / ``{F0:12}`` so the
    result is loss-aware but never crashes. Macros are expanded inline when an
    ``expander`` is supplied, else kept as ``{F0:idx}`` escapes."""
    cm = charmap or load_charmap()
    out: list[str] = []
    for _off, tok, ln in iter_tokens(data):
        if ln == 2:
            if tok >= MACRO_TOKEN_BASE:
                idx = tok - MACRO_TOKEN_BASE
                sub = expander(idx) if expander else None
                if sub is not None:
                    out.append(decode(sub, cm, expander, control_escapes))
                else:
                    out.append("{F0:%d}" % idx)
                continue
            slot = tok - GLYPH_TOKEN_BASE + TWO_BYTE_SLOT_OFFSET
            ch = cm.slot_to_char.get(slot)
            out.append(ch if ch is not None else "{SLOT:%d}" % slot)
            continue
        if tok == TERMINATOR:
            out.append("{00}")
            continue
        ch = cm.slot_to_char.get(tok)
        if ch is not None and tok >= 0x02:
            out.append(ch)
        else:
            out.append("{%02X}" % tok)
    return "".join(out)


def encode_char(ch: str, charmap: Charmap | None = None,
                allow_low15: bool = False, surface: str = "stage",
                allowed_one_bytes: frozenset[int] = frozenset()) -> bytes | None:
    """Encode ONE character to its preferred byte form (None if unencodable)."""
    cm = charmap or load_charmap()
    slot = cm.slot_of(ch, allow_low15, surface=surface,
                      allowed_one_bytes=allowed_one_bytes)
    if slot is None:
        return None
    return encode_slot(slot)


def encode_slot(slot: int) -> bytes:
    """Encode a glyph slot number to its byte form."""
    if slot < TWO_BYTE_SLOT_OFFSET:
        return bytes([slot])
    tok = slot - TWO_BYTE_SLOT_OFFSET + GLYPH_TOKEN_BASE
    return bytes([tok >> 8, tok & 0xFF])


def encode(text: str, charmap: Charmap | None = None,
           allow_low15: bool = False, surface: str = "stage",
           allowed_one_bytes: frozenset[int] = frozenset()) -> bytes:
    """Encode readable text (with {..} escapes as produced by decode()).

    surface="stage" for renderA-direct text (dialogue, barks, cut-ins,
    library/hangar banks); surface="bank" for trampoline surfaces (arena and
    battle effect banks) where one-byte and JP-band tokens render from the
    WRONG font — there a one-byte code is only used when its byte value
    occurs in the record's original JP span (`allowed_one_bytes`).  See
    Charmap.slot_of and AGENTS.md.

    Raises ValueError on unencodable characters — translation data must only
    use characters that exist in the glyph atlas (for the given surface)."""
    cm = charmap or load_charmap()
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":                                   # escape
            j = text.index("}", i)
            body = text[i + 1:j]
            i = j + 1
            if body.startswith("F0:"):
                idx = int(body[3:])
                tok = MACRO_TOKEN_BASE + idx
                out += bytes([tok >> 8, tok & 0xFF])
            elif body.startswith("SLOT:"):
                out += encode_slot(int(body[5:]))
            else:
                out.append(int(body, 16))
            continue
        b = encode_char(ch, cm, allow_low15, surface=surface,
                        allowed_one_bytes=allowed_one_bytes)
        if b is None:
            raise ValueError(
                f"unencodable character {ch!r} (U+{ord(ch):04X}) on surface {surface!r}")
        out += b
        i += 1
    return bytes(out)


def rendered_width(data: bytes, advance: int, expander=None, _depth: int = 0) -> int:
    """Summed glyph advance of a text byte stream (macros recurse via expander).

    Every glyph costs `advance` pixels (fixed-cell renderer); an unresolvable
    macro conservatively costs one cell."""
    w = 0
    for _off, tok, ln in iter_tokens(data):
        if ln == 1:
            if tok == TERMINATOR:
                continue
            w += advance
            continue
        if tok >= MACRO_TOKEN_BASE and _depth < 4:
            sub = expander(tok - MACRO_TOKEN_BASE) if expander else None
            w += rendered_width(sub, advance, expander, _depth + 1) if sub is not None else advance
        else:
            w += advance
    return w
