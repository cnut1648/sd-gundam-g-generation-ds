#!/usr/bin/env python3
"""Minimal PCF bitmap-font reader for the WQY 12px song font + the atlas glyph
grammar (stroke=1, L-shadow=2) from docs/TEXT_SYSTEM.md.

Self-check: regenerating an already-minted ZH-band cell must reproduce the
shipped 36-byte cell exactly (same recipe = same bytes)."""
from __future__ import annotations

import struct
from pathlib import Path

PCF_PATH = "/usr/share/fonts/X11/misc/wenquanyi_9pt.pcf"

PCF_METRICS = 1 << 2
PCF_BITMAPS = 1 << 3
PCF_BDF_ENCODINGS = 1 << 5


class PCF:
    def __init__(self, path=PCF_PATH):
        self.data = Path(path).read_bytes()
        d = self.data
        if d[:4] != b"\x01fcp":
            raise ValueError("not a PCF")
        (count,) = struct.unpack_from("<I", d, 4)
        self.tables = {}
        for i in range(count):
            typ, fmt, size, off = struct.unpack_from("<4I", d, 8 + i * 16)
            self.tables[typ] = (fmt, size, off)
        self._parse_encodings()
        self._parse_metrics()
        self._parse_bitmaps()

    def _fmt_le(self, fmt):
        return (fmt & 4) == 0          # byte order bit (PCF_BYTE_MASK = 4)

    def _parse_encodings(self):
        fmt, size, off = self.tables[PCF_BDF_ENCODINGS]
        d = self.data
        (sfmt,) = struct.unpack_from("<I", d, off)
        le = self._fmt_le(sfmt)
        e = "<" if le else ">"
        mn2, mx2, mn1, mx1, dflt = struct.unpack_from(e + "4hH", d, off + 4)
        self.enc = {}
        n = (mx2 - mn2 + 1) * (mx1 - mn1 + 1)
        base = off + 14
        for i in range(n):
            (g,) = struct.unpack_from(e + "H", d, base + i * 2)
            if g == 0xFFFF:
                continue
            b2 = mn2 + i % (mx2 - mn2 + 1)
            b1 = mn1 + i // (mx2 - mn2 + 1)
            cp = (b1 << 8) | b2 if mx1 else b2
            self.enc[cp] = g

    def _parse_metrics(self):
        fmt, size, off = self.tables[PCF_METRICS]
        d = self.data
        (sfmt,) = struct.unpack_from("<I", d, off)
        le = self._fmt_le(sfmt)
        e = "<" if le else ">"
        comp = sfmt & 0x100
        if comp:
            (cnt,) = struct.unpack_from(e + "H", d, off + 4)
            self.metrics = []
            p = off + 6
            for i in range(cnt):
                lsb, rsb, w, asc, desc = struct.unpack_from("5B", d, p)
                self.metrics.append((lsb - 0x80, rsb - 0x80, w - 0x80,
                                     asc - 0x80, desc - 0x80))
                p += 5
        else:
            (cnt,) = struct.unpack_from(e + "I", d, off + 4)
            self.metrics = []
            p = off + 8
            for i in range(cnt):
                lsb, rsb, w, asc, desc, attr = struct.unpack_from(e + "5hH", d, p)
                self.metrics.append((lsb, rsb, w, asc, desc))
                p += 12

    def _parse_bitmaps(self):
        fmt, size, off = self.tables[PCF_BITMAPS]
        d = self.data
        (sfmt,) = struct.unpack_from("<I", d, off)
        le = self._fmt_le(sfmt)
        e = "<" if le else ">"
        (cnt,) = struct.unpack_from(e + "I", d, off + 4)
        self.bmp_off = struct.unpack_from(e + f"{cnt}I", d, off + 8)
        sizes = struct.unpack_from(e + "4I", d, off + 8 + cnt * 4)
        self.bmp_pad = sfmt & 3          # glyph-row pad: 0=byte,1=short,2=int
        self.bmp_bit_msb = bool(sfmt & 8)
        self.bmp_data_off = off + 8 + cnt * 4 + 16
        self.bmp_size = sizes[self.bmp_pad]

    def glyph(self, ch: str):
        """-> (rows as list of int bitmasks, metrics) or None."""
        g = self.enc.get(ord(ch))
        if g is None:
            return None
        lsb, rsb, w, asc, desc = self.metrics[g]
        h = asc + desc
        span = (1, 2, 4)[self.bmp_pad]
        stride = ((rsb - lsb + 7) // 8 + span - 1) // span * span
        p = self.bmp_data_off + self.bmp_off[g]
        rows = []
        width = rsb - lsb
        for y in range(h):
            v = 0
            for b in range(stride):
                byte = self.data[p + y * stride + b]
                if not self.bmp_bit_msb:
                    byte = int(f"{byte:08b}"[::-1], 2)
                v = (v << 8) | byte
            rows.append((v, stride * 8))
        return rows, (lsb, rsb, w, asc, desc)


def raster12(pcf: PCF, ch: str):
    """12x12 0/1 grid: WQY 12px glyph at the cell origin (11x11 design box)."""
    r = pcf.glyph(ch)
    if r is None:
        return None
    rows, (lsb, rsb, w, asc, desc) = r
    grid = [[0] * 12 for _ in range(12)]
    # font is 12px line (ascent 11 desc 1 typical); glyph box top-left at origin
    for y, (v, nbits) in enumerate(rows):
        for x in range(min(12, rsb - lsb)):
            bit = (v >> (nbits - 1 - x)) & 1
            yy = y
            xx = x + lsb
            if 0 <= yy < 12 and 0 <= xx < 12 and bit:
                grid[yy][xx] = 1
    return grid


def apply_shadow(grid):
    """stroke=1 + L-shadow=2 (stroke dilated right/down/down-right)."""
    out = [[0] * 12 for _ in range(12)]
    for y in range(12):
        for x in range(12):
            if grid[y][x]:
                out[y][x] = 1
    for y in range(12):
        for x in range(12):
            if grid[y][x]:
                for dy, dx in ((0, 1), (1, 0), (1, 1)):
                    yy, xx = y + dy, x + dx
                    if yy < 12 and xx < 12 and out[yy][xx] == 0:
                        out[yy][xx] = 2
    return out


def cell_bytes(grid) -> bytes:
    """12x12 values (0..3) -> 36-byte 2bpp cell (bit order per guide decoder:
    bit index = y*12+x, value = (byte >> (bit*2 % 8)) & 3)."""
    out = bytearray(36)
    for y in range(12):
        for x in range(12):
            bit = y * 12 + x
            out[(bit * 2) // 8] |= (grid[y][x] & 3) << ((bit * 2) % 8)
    return bytes(out)


def cell_to_grid(b: bytes):
    rows = []
    for y in range(12):
        row = []
        for x in range(12):
            bit = y * 12 + x
            row.append((b[(bit * 2) // 8] >> ((bit * 2) % 8)) & 3)
        rows.append(row)
    return rows


def show(grid):
    return "\n".join("".join(".#*?"[v] for v in row) for row in grid)


if __name__ == "__main__":
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    pcf = PCF()
    # self-check vs shipped minted cells
    cm = json.loads(Path("data/charmap.json").read_text())
    atlas = Path("data/font/atlas12.bin").read_bytes()
    ok = bad = 0
    fails = []
    for ch, slot in sorted(cm["two_byte_zh"].items(), key=lambda kv: kv[1]):
        if slot < 2196:
            continue
        g = raster12(pcf, ch)
        if g is None:
            continue
        mine = cell_bytes(apply_shadow(g))
        got = atlas[slot * 36:slot * 36 + 36]
        if mine == got:
            ok += 1
        else:
            bad += 1
            if len(fails) < 5:
                fails.append((ch, slot))
    print(f"regen self-check: {ok} byte-exact, {bad} differ")
    for ch, slot in fails:
        print("---", ch, slot)
        print(show(cell_to_grid(atlas[slot * 36:slot * 36 + 36])))
        print("vs mine:")
        g = raster12(pcf, ch)
        print(show(apply_shadow(g)))
