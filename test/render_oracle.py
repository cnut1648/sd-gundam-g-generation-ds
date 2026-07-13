#!/usr/bin/env python3
"""render_oracle.py — offline pixel oracle for the game's two text pipelines.

Renders any text byte stream EXACTLY as the game draws it, without an
emulator, so every line of translated text can be verified at scale:

  * renderA-direct surfaces ("stage"): every slot from the 12x12 CJK atlas
    (2bpp, value1=stroke, value2=shadow), 12px advance.
  * trampoline surfaces ("bank"): slot >= 2196 from the atlas; slot < 2196
    from the renderB 8x16 UI font inside arm9 (RAM 0x02133F14), 8px advance —
    the dispatch installed by the translation (docs/TEXT_SYSTEM.md §3).
  * 0xF0xx dictionary macros expand via the arm9 dictionary at 0x1444B4.

The oracle is validated against live-emulator captures by glyph-mask IoU
(test/test_render_oracle_parity.py); after that single trust anchor, offline
rendering is authoritative for coverage runs (see docs/TESTING_APPROACH.md).

Also provides the ALGORITHMIC style analysis used by static gates:
`line_style_report()` classifies every token as atlas-CJK / renderB-text /
structure, so mixed-font lines (the NT等级4 sunk-digit and 吉翁海無 garble
classes) are detected from bytes alone — no screenshots, no judgment.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from utils import text_codec as tc  # noqa: E402

ATLAS_PATH = REPO / "data" / "font" / "atlas12.bin"
RENDERB_RAM = 0x02133F14
PRIMARY_DICT = 0x1444B4
TRAMPOLINE_SPLIT = 2196

STROKE, SHADOW = 1, 2


def _load_arm9(rom_path: Path) -> bytes:
    rom = Path(rom_path).read_bytes()
    off, _, _ram, size = struct.unpack_from("<IIII", rom, 0x20)
    return rom[off:off + size]


class Oracle:
    def __init__(self, rom_path: Path, atlas_path: Path = ATLAS_PATH):
        self.atlas = atlas_path.read_bytes()
        self.arm9 = _load_arm9(rom_path)
        arm9_ram = 0x02000000
        self.renderb_off = RENDERB_RAM - arm9_ram
        self.expand = tc.make_macro_expander(self.arm9, PRIMARY_DICT)
        self.cm = tc.load_charmap()

    # -- glyph rasters ------------------------------------------------------
    def atlas_glyph(self, slot: int):
        """12x12 2bpp -> list of rows of 0/1/2 (0 empty, 1 stroke, 2 shadow)."""
        b = self.atlas[slot * 36:(slot + 1) * 36]
        rows = []
        for y in range(12):
            row = []
            for x in range(12):
                bit = y * 12 + x
                row.append((b[(bit * 2) // 8] >> ((bit * 2) % 8)) & 3)
            rows.append(row)
        return rows

    def renderb_glyph(self, slot: int):
        """8x16 2bpp (u16-LE rows) -> rows of 0/1/2."""
        off = self.renderb_off + slot * 32
        b = self.arm9[off:off + 32]
        rows = []
        for y in range(16):
            v = b[y * 2] | (b[y * 2 + 1] << 8)
            rows.append([(v >> (x * 2)) & 3 for x in range(8)])
        return rows

    # -- token walk (macro-expanding) ----------------------------------------
    def glyph_stream(self, data: bytes, surface: str, _depth: int = 0):
        """Yield (slot, font) per drawn glyph; font in {'A','B'}.  Controls and
        separators are skipped.  Macros recurse (depth-capped)."""
        i = 0
        while i < len(data):
            b = data[i]
            if b >= 0xF0 and i + 1 < len(data):
                idx = ((b << 8) | data[i + 1]) - 0xF000
                ent = self.expand(idx)
                if ent and _depth < 6:
                    yield from self.glyph_stream(ent, surface, _depth + 1)
                i += 2
                continue
            if b >= 0xE0 and i + 1 < len(data):
                slot = ((b << 8) | data[i + 1]) - 0xE000 + 224
                i += 2
            else:
                slot = b
                i += 1
                if slot < 0x02:          # 00 terminator/separator, 01 filler
                    continue
            if surface == "bank" and slot < TRAMPOLINE_SPLIT:
                yield slot, "B"
            else:
                yield slot, "A"

    # -- rasterize a line -----------------------------------------------------
    def render_line(self, data: bytes, surface: str, scale: int = 1):
        """Render to a PIL image (white stroke / dark shadow on green)."""
        from PIL import Image
        glyphs = list(self.glyph_stream(data, surface))
        H = 16
        W = max(1, sum(12 if f == "A" else 8 for _s, f in glyphs))
        img = Image.new("RGB", (W, H), (0, 90, 0))
        px = img.load()
        x0 = 0
        for slot, font in glyphs:
            if font == "A":
                rows, w = self.atlas_glyph(slot), 12
                # trampoline surfaces: the 0x11A2A0 cave draws atlas glyphs at
                # penY+3 so 12x12 ink (rows 0..11) bottom-aligns with renderB
                # ink (rows 2..14); stage keeps the plain anchor.
                yoff = 3 if surface == "bank" else 1
            else:
                rows, w = self.renderb_glyph(slot), 8
                yoff = 0                                  # 16px cell
            for y, row in enumerate(rows):
                for x, v in enumerate(row):
                    if v == STROKE:
                        px[x0 + x, yoff + y] = (255, 255, 255)
                    elif v == SHADOW and px[x0 + x, yoff + y] == (0, 90, 0):
                        px[x0 + x, yoff + y] = (24, 24, 24)
            x0 += w
        if scale > 1:
            img = img.resize((W * scale, H * scale), Image.NEAREST)
        return img

    # -- algorithmic style analysis -------------------------------------------
    def line_style_report(self, data: bytes, surface: str) -> dict:
        """Counts per style class for one line.  On bank surfaces a mixed
        line (atlas CJK + renderB TEXT glyph) is the misalignment/garble
        signature; renderB STRUCTURE bytes (JP-proven) do not count."""
        n_atlas = n_renderb_text = 0
        renderb_slots = []
        for slot, font in self.glyph_stream(data, surface):
            if font == "A":
                n_atlas += 1
            else:
                n_renderb_text += 1
                renderb_slots.append(slot)
        return {"atlas": n_atlas, "renderb": n_renderb_text,
                "renderb_slots": renderb_slots,
                "mixed": bool(n_atlas and n_renderb_text)}
