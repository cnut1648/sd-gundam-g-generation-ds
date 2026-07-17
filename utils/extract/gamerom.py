"""GameROM: one loaded ROM (JP source or ZH build) for extraction.

Glyph banks, RAM->file pointer resolution (including the ZH build's appended
autoload pools), NUL-terminated string reads and 0xF0xx macro expanders.
Lifted from build/build_guide.py (the historical private extractor) into the
canonical extraction package.
"""
from __future__ import annotations

import struct
from pathlib import Path

from . import layout as L


def u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


class GameROM:
    """A loaded .nds image: arm9 + NitroFS files + glyph banks + resolvers."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.rom = self.path.read_bytes()
        a9_off, _entry, a9_ram, a9_size = struct.unpack_from("<IIII", self.rom, 0x20)
        assert a9_ram == L.RAM_BASE, f"unexpected arm9 RAM base {a9_ram:#x}"
        self.arm9 = self.rom[a9_off:a9_off + a9_size]
        # translated build? (appended atlas beyond the JP image)
        self.is_zh = len(self.arm9) > L.ARM9_HEAD_END + 0x100
        # RAM->file mapping segments: (ram_lo, ram_hi, file_delta)
        self._segs: list[tuple[int, int, int]] = [
            (L.RAM_BASE, L.RAM_BASE + min(len(self.arm9), L.ARM9_HEAD_END), 0)
        ]
        self._atlas_bytes: bytes
        self._load_glyph_banks()
        self._ndsrom = None
        from utils import text_codec
        self.expand = text_codec.make_macro_expander(self.arm9, L.DICT_TEXT)
        self.expand_sys = text_codec.make_macro_expander(self.arm9, L.DICT_SYS)

    # -- NitroFS files --------------------------------------------------------
    def file(self, name: str) -> bytes:
        from utils import rom as romlib
        if self._ndsrom is None:
            self._ndsrom = romlib.load_rom(self.path)
        return romlib.get_file(self._ndsrom, name)

    # -- glyph banks ----------------------------------------------------------
    def _load_glyph_banks(self):
        if self.is_zh:
            # Parse the 5-entry autoload list to find the appended atlas+pools.
            # The boot copier reads its SOURCE continuously from AutoloadStart
            # (0x1B6860): ITCM, DTCM (image head), then the appended payloads at
            # 0x1B6DA0 — the file cursor starts at 0x1B6860 (ROM_STRUCTURE §3).
            list_start = u32(self.arm9, L.MP_LIST_START) - L.RAM_BASE
            list_end = u32(self.arm9, L.MP_LIST_START + 4) - L.RAM_BASE
            fpos = L.AUTOLOAD_SRC_OFF
            self._atlas_bytes = b""
            o = list_start
            while o + 12 <= list_end:
                ram, size, _bss = struct.unpack_from("<III", self.arm9, o)
                if 0x02300000 <= ram < 0x02400000:   # appended atlas + string pools
                    self._segs.append((ram, ram + size, ram - L.RAM_BASE - fpos))
                    if ram == L.ZH_ATLAS_RAM:
                        self._atlas_bytes = self.arm9[fpos:fpos + size]
                fpos += size
                o += 12
            self.atlas_slots = len(self._atlas_bytes) // L.GLYPH_CELL
        else:
            self._atlas_bytes = self.arm9[
                L.JP_ATLAS_OFF:L.JP_ATLAS_OFF + L.JP_ATLAS_SLOTS * L.GLYPH_CELL]
            self.atlas_slots = L.JP_ATLAS_SLOTS
        self.renderb_slots = (L.DICT_SYS - L.RENDERB_OFF) // L.RENDERB_CELL   # 2093
        self._renderb_bytes = self.arm9[
            L.RENDERB_OFF:L.RENDERB_OFF + self.renderb_slots * L.RENDERB_CELL]

    def atlas_cell(self, slot: int) -> bytes:
        if 0 <= slot < self.atlas_slots:
            return self._atlas_bytes[slot * L.GLYPH_CELL:(slot + 1) * L.GLYPH_CELL]
        return b"\x00" * L.GLYPH_CELL

    def renderb_cell(self, slot: int) -> bytes:
        if 0 <= slot < self.renderb_slots:
            return self._renderb_bytes[slot * L.RENDERB_CELL:(slot + 1) * L.RENDERB_CELL]
        return b"\x00" * L.RENDERB_CELL

    # -- pointer resolution ----------------------------------------------------
    def resolve(self, ram: int) -> tuple[bytes, int] | None:
        """RAM address -> (buffer, offset), or None if unmapped."""
        for lo, hi, delta in self._segs:
            if lo <= ram < hi:
                return self.arm9, ram - delta - L.RAM_BASE if delta else ram - L.RAM_BASE
        return None

    def file_off(self, ram: int) -> int | None:
        r = self.resolve(ram)
        return r[1] if r else None

    def cstr(self, ram: int, maxlen: int = 256) -> bytes | None:
        """NUL-terminated string bytes at a RAM pointer (game name pools).

        A single 0x00 terminates a name string (names never contain a bare
        0x00 separator the way dialogue blocks do)."""
        r = self.resolve(ram)
        if r is None:
            return None
        buf, off = r
        end = buf.find(b"\x00", off, off + maxlen)
        if end < 0:
            end = off + maxlen
        return buf[off:end]
