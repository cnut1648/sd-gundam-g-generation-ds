#!/usr/bin/env python3
"""Pure-pixel WenQuanYi Bitmap Song BDF reader for the 12x12 atlas.

This module deliberately does not use FreeType, Pillow font rasterization,
grayscale masks, scaling, thresholding, or per-glyph bbox recentering. WQY's
9pt BDF CJK strike is already an 11x11 bitmap on a 12px advance; those
source bits are copied to integer atlas coordinates and the game-native
value-2 L-shadow is generated in the reserved twelfth row/column.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CELL = 12
CELL_BYTES = 36
WQY_CJK_BBX = (11, 11, 0, -1)
WQY_CJK_DWIDTH = (12, 0)


@dataclass(frozen=True)
class BDFGlyph:
    width: int
    height: int
    x_offset: int
    y_offset: int
    dwidth: tuple[int, int]
    rows: tuple[int, ...]
    storage_bits: int


class WQYBitmapFont:
    """Read original BDF bitmap rows without invoking a font rasterizer."""

    def __init__(self, path: Path):
        text = path.read_text(encoding="ascii")
        if not text.startswith("STARTFONT 2.1\n"):
            raise ValueError(f"not a BDF 2.1 font: {path}")
        self.glyphs: dict[int, BDFGlyph] = {}
        for block in text.split("STARTCHAR ")[1:]:
            lines = block.splitlines()
            encoding = self._value(lines, "ENCODING ")
            if encoding is None or encoding < 0:
                continue
            bbx = self._tuple(lines, "BBX ", 4)
            dwidth = self._tuple(lines, "DWIDTH ", 2)
            try:
                bitmap_start = next(
                    i for i, line in enumerate(lines) if line.startswith("BITMAP")
                ) + 1
            except StopIteration as exc:
                raise ValueError(f"BDF glyph U+{encoding:04X} has no bitmap") from exc
            width, height, x_offset, y_offset = bbx
            bitmap_lines = lines[bitmap_start:bitmap_start + height]
            if len(bitmap_lines) != height or any(not line for line in bitmap_lines):
                raise ValueError(f"BDF glyph U+{encoding:04X} has a truncated bitmap")
            storage_widths = {len(line) * 4 for line in bitmap_lines}
            if len(storage_widths) != 1:
                raise ValueError(f"BDF glyph U+{encoding:04X} changes row storage width")
            self.glyphs[encoding] = BDFGlyph(
                width=width,
                height=height,
                x_offset=x_offset,
                y_offset=y_offset,
                dwidth=dwidth,
                rows=tuple(int(line, 16) for line in bitmap_lines),
                storage_bits=storage_widths.pop(),
            )

    @staticmethod
    def _value(lines: list[str], prefix: str) -> int | None:
        line = next((line for line in lines if line.startswith(prefix)), None)
        return int(line.split()[1]) if line is not None else None

    @staticmethod
    def _tuple(lines: list[str], prefix: str, count: int) -> tuple[int, ...]:
        line = next((line for line in lines if line.startswith(prefix)), None)
        if line is None:
            raise ValueError(f"BDF glyph is missing {prefix.strip()}")
        values = tuple(int(value) for value in line.split()[1:])
        if len(values) != count:
            raise ValueError(f"bad {prefix.strip()} field: {line!r}")
        return values

    def stroke_mask(self, char: str) -> list[list[bool]]:
        """Return the strict, source-coordinate CJK mask."""
        glyph = self.glyphs.get(ord(char))
        if glyph is None:
            raise ValueError(f"WQY BDF has no glyph for {char!r}")
        metrics = (glyph.width, glyph.height, glyph.x_offset, glyph.y_offset)
        if metrics != WQY_CJK_BBX or glyph.dwidth != WQY_CJK_DWIDTH:
            raise ValueError(
                f"WQY {char!r} metrics changed: BBX={metrics}, "
                f"DWIDTH={glyph.dwidth}"
            )
        mask = [[False] * CELL for _ in range(CELL)]
        for y, row in enumerate(glyph.rows):
            for x in range(glyph.width):
                if (row >> (glyph.storage_bits - 1 - x)) & 1:
                    mask[y][x + glyph.x_offset] = True
        if any(mask[CELL - 1]) or any(row[CELL - 1] for row in mask):
            raise ValueError(f"WQY {char!r} reaches the shadow-reserved edge")
        return mask

    def cell(self, char: str) -> bytes:
        return encode_cell(self.stroke_mask(char))

def encode_cell(mask: list[list[bool]]) -> bytes:
    """Encode stroke=1 plus the game's right/down/right-down value-2 shadow."""
    pixels = [[1 if mask[y][x] else 0 for x in range(CELL)] for y in range(CELL)]
    for y in range(CELL):
        for x in range(CELL):
            if not mask[y][x]:
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1)):
                tx, ty = x + dx, y + dy
                if tx < CELL and ty < CELL and pixels[ty][tx] == 0:
                    pixels[ty][tx] = 2
    raw = bytearray(CELL_BYTES)
    for y in range(CELL):
        for x in range(CELL):
            bit = y * CELL + x
            raw[bit // 4] |= pixels[y][x] << ((bit % 4) * 2)
    return bytes(raw)
