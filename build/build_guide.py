#!/usr/bin/env python3
"""build_guide.py — enhance 攻略.html with content extracted STRICTLY from the game.

This is a translation-REVIEW tool.  It does NOT read the project's source
annotations (data/charmap.json, data/names/*.json, data/dialogue/*.json …).
Instead it walks the game's OWN in-ROM arrays (the same pointer tables the
console follows at runtime), reads the raw text byte streams, and renders every
string EXACTLY as the game's two font pipelines draw it — the actual 12x12 CJK
atlas and 8x16 UI font shipped inside the ROM.  Both the Japanese source ROM and
the built Chinese ROM are decoded the same way, so a reviewer can compare, glyph
for glyph, what the game really shows — and any encoding/garble bug is visible
(the point of the exercise) instead of hidden behind our own source text.

Output: the guide shows the ACTUAL RENDERED GLYPHS (a sprite sheet built from
the ROM's glyph banks + a tiny canvas renderer), never a charmap decode.  There
is no Unicode round-trip: the game stores glyph BITMAPS, not text, so the only
honest "what the game shows" is the bitmap itself.

    python build/build_guide.py --jp <japanese.nds> --zh <translated.nds> \
        [--html 攻略.html] [--out 攻略.html]

Deterministic: no VLM, no network, no fonts, no charmap — only the two ROMs.
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import text_codec  # noqa: E402  (token-aware terminator scan)

# ---------------------------------------------------------------------------
# ROM / arm9 constants (all from docs/ROM_STRUCTURE.md, verified at load time)
# ---------------------------------------------------------------------------
RAM_BASE = 0x02000000
ARM9_HEAD_END = 0x1B6DB8          # end of the ORIGINAL JP image (resident band)

# glyph banks
JP_ATLAS_OFF = 0x11A2A0           # JP in-image 12x12 atlas (2196 slots) file off
JP_ATLAS_SLOTS = 2196
RENDERB_OFF = 0x133F14            # 8x16 UI font (renderB) file off (RAM 0x02133F14)
# renderB is the FULL 8x16 JP UI font (kana + kanji + latin), NOT a 224-glyph
# subset: it spans from RENDERB_OFF up to the primary/system dict at 0x1444B4,
# = (0x1444B4-0x133F14)/32 = 2093 glyphs.  This is why JP names (kanji-bearing)
# render correctly via renderB, and why the trampoline split at 2196 works
# (renderB covers <~2093, the ZH atlas covers >=2196).
GLYPH_CELL = 36                   # 12x12 2bpp atlas cell bytes
RENDERB_CELL = 32                 # 8x16 2bpp renderB cell bytes
ZH_ATLAS_RAM = 0x023027A0         # appended atlas RAM base in the ZH build
TRAMPOLINE_SPLIT = 2196           # slot >= this -> renderA atlas even on renderB path

# dictionaries (macro expansion).  EMPIRICALLY (verified by rendering real JP
# dialogue + names against the ZH translation), the JP text F-refs (0xF0xx) in
# BOTH dialogue and name pools resolve through the 0x12D770 dictionary — e.g.
# _STG01 "F10f F23 … Fa4" -> シャ シャア … 来た ("Char … came to laugh?"),
# matching the ZH.  The 0x1444B4 table is the system/combat dictionary and
# yields garble on stored text (鏡球 / 木く).  ZH strings use no F-refs at all.
DICT_TEXT = 0x12D770          # dialogue + name macro store (JP decode)
DICT_SYS = 0x1444B4           # system/combat macros (not used for text decode)

# autoload list (ZH build): 5 * 12-byte entries {ramAddr,size,bssSize}
MP_LIST_START = 0xB0C


def u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


# ===========================================================================
# GameROM: one ROM (JP or ZH); glyph banks, pointer resolver, macro expander.
# ===========================================================================
class GameROM:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.rom = self.path.read_bytes()
        a9_off, _entry, a9_ram, a9_size = struct.unpack_from("<IIII", self.rom, 0x20)
        assert a9_ram == RAM_BASE, f"unexpected arm9 RAM base {a9_ram:#x}"
        self.arm9 = self.rom[a9_off:a9_off + a9_size]
        # is this the translated build?  (appended atlas beyond the JP image)
        self.is_zh = len(self.arm9) > ARM9_HEAD_END + 0x100
        # pool RAM->file mapping segments: (ram_lo, ram_hi, file_delta)
        self._segs: list[tuple[int, int, int]] = [
            (RAM_BASE, RAM_BASE + min(len(self.arm9), ARM9_HEAD_END), 0)
        ]
        self._atlas_bytes: bytes
        self._load_glyph_banks()
        self._load_files()
        self.expand = self._make_macro_expander(DICT_TEXT)      # JP dialogue + names
        self.expand_sys = self._make_macro_expander(DICT_SYS)   # system (rarely needed)

    # -- NitroFS files (needed for cut-ins/barks/special banks) --------------
    def _load_files(self):
        from utils import rom as romlib
        self._ndsrom = romlib.load_rom(self.path)

    def file(self, name: str) -> bytes:
        from utils import rom as romlib
        return romlib.get_file(self._ndsrom, name)

    # -- glyph banks ---------------------------------------------------------
    def _load_glyph_banks(self):
        if self.is_zh:
            # parse the 5-entry autoload list to find the appended atlas + pools.
            # The boot copier reads its SOURCE continuously from AutoloadStart
            # (0x1B6860): ITCM, DTCM (in the image head), then the appended
            # atlas/pools at 0x1B6DA0 — so the file cursor starts at 0x1B6860,
            # NOT at 0x1B6DA0 (docs/ROM_STRUCTURE.md §3).
            list_start = u32(self.arm9, MP_LIST_START) - RAM_BASE
            list_end = u32(self.arm9, MP_LIST_START + 4) - RAM_BASE
            fpos = 0x1B6860              # AutoloadStart (source cursor origin)
            self._atlas_bytes = b""
            o = list_start
            while o + 12 <= list_end:
                ram, size, _bss = struct.unpack_from("<III", self.arm9, o)
                if 0x02300000 <= ram < 0x02400000:   # appended atlas + string pools
                    self._segs.append((ram, ram + size, ram - RAM_BASE - fpos))
                    if ram == ZH_ATLAS_RAM:
                        self._atlas_bytes = self.arm9[fpos:fpos + size]
                fpos += size
                o += 12
            self.atlas_slots = len(self._atlas_bytes) // GLYPH_CELL
        else:
            self._atlas_bytes = self.arm9[JP_ATLAS_OFF:JP_ATLAS_OFF + JP_ATLAS_SLOTS * GLYPH_CELL]
            self.atlas_slots = JP_ATLAS_SLOTS
        self.renderb_slots = (DICT_SYS - RENDERB_OFF) // RENDERB_CELL      # 2093
        self._renderb_bytes = self.arm9[RENDERB_OFF:RENDERB_OFF + self.renderb_slots * RENDERB_CELL]

    def atlas_cell(self, slot: int) -> bytes:
        if 0 <= slot < self.atlas_slots:
            return self._atlas_bytes[slot * GLYPH_CELL:(slot + 1) * GLYPH_CELL]
        return b"\x00" * GLYPH_CELL

    def renderb_cell(self, slot: int) -> bytes:
        if 0 <= slot < self.renderb_slots:
            return self._renderb_bytes[slot * RENDERB_CELL:(slot + 1) * RENDERB_CELL]
        return b"\x00" * RENDERB_CELL

    # -- pointer resolver: RAM addr -> (buffer, offset) ----------------------
    def resolve(self, ram: int) -> tuple[bytes, int] | None:
        for lo, hi, delta in self._segs:
            if lo <= ram < hi:
                return self.arm9, ram - delta - RAM_BASE if delta else ram - RAM_BASE
        return None

    def cstr(self, ram: int, maxlen: int = 256) -> bytes | None:
        """Token-aware NUL-terminated string at a RAM pointer (game name pools).

        A single 0x00 terminates a name string (names never contain a bare 0x00
        separator the way dialogue blocks do)."""
        r = self.resolve(ram)
        if r is None:
            return None
        buf, off = r
        end = buf.find(b"\x00", off, off + maxlen)
        if end < 0:
            end = off + maxlen
        return buf[off:end]

    # -- macro expander (dictionary 0xF0xx) ----------------------------------
    def _make_macro_expander(self, table_off: int):
        arm9 = self.arm9
        n = u16(arm9, table_off) // 2

        def expand(idx: int) -> bytes | None:
            if 0 <= idx < n:
                off = u16(arm9, table_off + idx * 2)
                s = table_off + off
                e = arm9.find(b"\x00", s)
                return arm9[s:e] if e >= 0 else arm9[s:]
            return None
        return expand


# ===========================================================================
# Decode: byte stream -> list of drawn glyphs (slot, font) per the game grammar
# ===========================================================================
def glyph_stream(rom: GameROM, data: bytes, surface: str, expander=None, _depth: int = 0):
    """Yield (slot, font) per drawn glyph.  font 'A' = 12x12 atlas, 'B' = 8x16.

    surface 'stage' = renderA-direct (every slot from the atlas);
    surface 'bank'  = trampoline (slot<2196 -> renderB 8x16, else atlas).
    Controls/separators skipped; 0xF0xx macros expand recursively (depth-capped)
    via `expander` (the primary dialogue dictionary by default; pass
    rom.expand_alt for name pools, whose F-refs index the alt/name dictionary).
    """
    expand = expander or rom.expand
    i = 0
    n = len(data)
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
        if surface == "bank" and slot < TRAMPOLINE_SPLIT:
            yield slot, "B"
        else:
            yield slot, "A"


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
    """Return (png_bytes, meta) for a glyph bank.  kind in {'atlas','renderb'}.

    Each cell is drawn at its grid position; stroke=opaque light, shadow=semi
    dark, empty=transparent — the same palette the line renderer uses so the
    browser canvas reproduces the game pixels exactly."""
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
# Game-array walkers (pure RE: follow the same tables the console follows)
# ===========================================================================
# master unit table
MASTER_TABLE = 0xB94BC
MASTER_STRIDE = 0xD8
MASTER_COUNT = 945
UNIT_NAME_FIELD = 0x00
WEAPON_BLOCK = 0x2C
WEAPON_STRIDE = 0x1C
WEAPONS_PER_UNIT = 6
# character / pilot DB
CHARDB = 0xDCF18
CHARDB_STRIDE = 0x48
CHARDB_COUNT = 563
PILOT_NAME_FIELD = 0x04


def _rec_ptr(rom: GameROM, off: int) -> int:
    return u32(rom.arm9, off)


def extract_units(rom: GameROM) -> list[dict]:
    """Walk the unit master table (arm9 0xB94BC): unit name + 6 weapon names.

    Reads the same pointer words the battle/roster code dereferences.  A record
    with a NULL/zero name pointer is an unused slot (skipped).  The table is
    bounded to the records that PRECEDE the char-DB (0xDCF18): because the master
    stride 0xD8 == 3x the char-DB stride 0x48, higher master indices alias the
    char-DB and would read PILOT names as if they were units."""
    unit_n = (CHARDB - MASTER_TABLE) // MASTER_STRIDE + 1
    out = []
    for utid in range(unit_n):
        rec = MASTER_TABLE + utid * MASTER_STRIDE
        nptr = _rec_ptr(rom, rec + UNIT_NAME_FIELD)
        name = rom.cstr(nptr) if nptr else None
        weapons = []
        for slot in range(WEAPONS_PER_UNIT):
            wptr = _rec_ptr(rom, rec + WEAPON_BLOCK + slot * WEAPON_STRIDE)
            if wptr:
                wb = rom.cstr(wptr)
                if wb:
                    weapons.append((slot, wptr, wb))
        if not name and not weapons:
            continue
        out.append({"utid": utid, "name_ptr": nptr, "name": name, "weapons": weapons})
    return out


CHARDB_VOICESET = 0x0A       # u16: voice/quote-set id (== bark 0x05 field)
# ID-command table: id = char_id*3 + slot (slot 0/1/2)
IDCMD_TABLE = 0xEC994
IDCMD_STRIDE = 0x24
IDCMD_NAME = 0x00
IDCMD_SUMMARY = 0x08
IDCMD_TARGET = 0x0E          # u8 enum: 01=self 03=enemy-squad 09=all
IDCMD_DIDX = 0x22           # u8 detail index
IDCMD_COND = 0x23          # u8 condition bits (bit 0x02 -> map, else battle)
DETAIL_OFFTAB = 0xF9048     # u32[]; string = DETAIL_OFFTAB + u32[DETAIL_OFFTAB + didx*4]
# cut-in famous line: parallel link table, indexed by the same id
CUTIN_LINK = 0x16FD64       # stride 0xC; +0x00 u16 = (cut-in record #)+1
CUTIN_OFFTAB = 0x16EEA8     # u32[]; record = 1dc.bin[offtab[R] : offtab[R+1]]
TARGET_NAMES = {0x01: "仅自身", 0x03: "敌队", 0x05: "自身", 0x09: "全军"}
BARK_FILES = ("0.bin", "1.bin", "1dd.bin", "1de.bin", "c4f.bin")


def extract_pilots(rom: GameROM) -> list[dict]:
    """Walk the character DB (arm9 0xDCF18 +0x04 name ptr)."""
    out = []
    for cid in range(CHARDB_COUNT):
        rec = CHARDB + cid * CHARDB_STRIDE
        nptr = _rec_ptr(rom, rec + PILOT_NAME_FIELD)
        name = rom.cstr(nptr) if nptr else None
        if not name:
            continue
        out.append({"char_id": cid, "name_ptr": nptr, "name": name})
    return out


def id_command(rom: GameROM, idn: int) -> dict:
    """One ID-command record (id = char_id*3 + slot)."""
    rec = IDCMD_TABLE + idn * IDCMD_STRIDE
    nptr = _rec_ptr(rom, rec + IDCMD_NAME)
    sptr = _rec_ptr(rom, rec + IDCMD_SUMMARY)
    didx = rom.arm9[rec + IDCMD_DIDX]
    cond = rom.arm9[rec + IDCMD_COND]
    target = rom.arm9[rec + IDCMD_TARGET]
    detail = None
    doff = DETAIL_OFFTAB + u32(rom.arm9, DETAIL_OFFTAB + didx * 4)
    end = rom.arm9.find(b"\x00\x00", doff)
    if 0 <= end < doff + 200:
        detail = rom.arm9[doff:end]
    return {"id": idn, "name": rom.cstr(nptr), "summary": rom.cstr(sptr),
            "detail": detail, "target": target, "cond": cond, "didx": didx}


def _clip_at_00(data: bytes, start: int = 0) -> int:
    """Token-aware index of the first standalone 0x00 at/after start (2-byte
    tokens 0xE0.. are skipped so a token's low 0x00 is not mistaken for it)."""
    i = start
    n = len(data)
    while i < n:
        if data[i] >= 0xE0 and i + 1 < n:
            i += 2
            continue
        if data[i] == 0x00:
            return i
        i += 1
    return n


def cutin_line(rom: GameROM, idn: int) -> bytes | None:
    """Cut-in famous line bytes for an ID (via the parallel link table).

    Record grammar: `00 05 <voiceset> 00 <line1> [00 03 <line2>] 00 03 00 01`.
    Strip the header and the trailing terminator so only the quote text renders.
    """
    v = u16(rom.arm9, CUTIN_LINK + 0xC * idn)
    if v == 0:
        return None
    r = v - 1
    s0 = u32(rom.arm9, CUTIN_OFFTAB + 4 * r)
    s1 = u32(rom.arm9, CUTIN_OFFTAB + 4 * (r + 1))
    dc = rom.file("1dc.bin")
    if not (0 <= s0 < s1 <= len(dc)):
        return None
    rec = dc[s0:s1]
    if rec[:2] == b"\x00\x05":                 # strip `00 05 <voiceset..> 00`
        j = 2
        while j < len(rec) and rec[j] != 0:
            j += 1
        rec = rec[j + 1:]
    for term in (b"\x00\x03\x00\x01", b"\x00\x00"):   # strip trailing framing
        k = rec.find(term)
        if k >= 0:
            rec = rec[:k]
            break
    return rec or None


# special ability / defense linkage (all keyed by utid; verified by disasm of
# drawers 0x2055AB4 / 0x2055BD8):
#   ability: fam = (utid-1)//3 if utid<=630 else utid-420; rec=1df[offA[fam+1]:offA[fam+2]]
#   defense: type-name = *(E71 + 0x1C*utid + 0x00); ri = u8(E71+0x1C*utid+0x1B);
#            desc = 1e0[offD[ri+1]:offD[ri+2]]
OFFTAB_A = 0x1781A4          # special-ability record-offset table (u32[])
OFFTAB_D = 0x178134          # special-defense record-offset table (u32[])
E71_TABLE = 0xE71B0          # per-utid special-profile table, stride 0x1C
E71_STRIDE = 0x1C
ABILITY_SPLIT = 0x276        # utid<=630 low path
ABILITY_SUB = 0x1A4          # 420, high path subtrahend


def _record_segments(buf: bytes, o0: int, o1: int, limit: int = 2) -> list[bytes]:
    """Split a 1df/1e0 record into its `00 03`-delimited name segments."""
    if not (0 <= o0 < len(buf)):
        return []
    rec = buf[o0:o1] if (o0 < o1 <= len(buf)) else buf[o0:]
    segs = []
    for part in rec.split(b"\x00\x03"):
        part = part.strip(b"\x00")
        if part:
            segs.append(part)
    return segs[:limit]


def unit_specials(jp: GameROM, zh: GameROM, utid: int) -> list[dict]:
    """Special abilities (1df) + special defense type/description (1e0) a unit
    carries.  Returns raw JP/ZH bytes (_jb/_zb) per record; the caller renders
    them on the renderB trampoline (bank) surface with the system dict."""
    out = []

    def pack_pair(jb, zb):
        return {"_jb": jb or b"", "_zb": zb or b""}

    # -- special ability (shared per 3-utid family) --
    fam = (utid - 1) // 3 if utid <= ABILITY_SPLIT else utid - ABILITY_SUB
    if fam >= 0:
        ja0, ja1 = u32(jp.arm9, OFFTAB_A + 4 * (fam + 1)), u32(jp.arm9, OFFTAB_A + 4 * (fam + 2))
        za0, za1 = u32(zh.arm9, OFFTAB_A + 4 * (fam + 1)), u32(zh.arm9, OFFTAB_A + 4 * (fam + 2))
        js = _record_segments(jp.file("1df.bin"), ja0, ja1)
        zs = _record_segments(zh.file("1df.bin"), za0, za1)
        for k in range(max(len(js), len(zs))):
            jb = js[k] if k < len(js) else None
            zb = zs[k] if k < len(zs) else None
            if zb:
                out.append(dict(kind="ability", **pack_pair(jb, zb)))
    # -- special defense: type name (e71+0x00) + description (1e0) --
    e = E71_TABLE + E71_STRIDE * utid
    if e + E71_STRIDE <= len(zh.arm9):
        tj, tz = u32(jp.arm9, e), u32(zh.arm9, e)
        if tz:
            out.append(dict(kind="defense", **pack_pair(jp.cstr(tj), zh.cstr(tz))))
        rj, rz = jp.arm9[e + 0x1B], zh.arm9[e + 0x1B]
        dj = _record_segments(jp.file("1e0.bin"), u32(jp.arm9, OFFTAB_D + 4 * (rj + 1)),
                              u32(jp.arm9, OFFTAB_D + 4 * (rj + 2)), limit=3)
        dz = _record_segments(zh.file("1e0.bin"), u32(zh.arm9, OFFTAB_D + 4 * (rz + 1)),
                              u32(zh.arm9, OFFTAB_D + 4 * (rz + 2)), limit=3)
        for k in range(max(len(dj), len(dz))):
            jb = dj[k] if k < len(dj) else None
            zb = dz[k] if k < len(dz) else None
            if zb:
                out.append(dict(kind="defense", **pack_pair(jb, zb)))
    return out


def build_bark_index(rom: GameROM) -> dict[int, list[bytes]]:
    """Scan the 5 bark files for records `00 05 <voiceset> 00 06 <char_id> ...`
    and group the text runs by char_id (rec+6)."""
    idx: dict[int, list[bytes]] = {}
    for fn in BARK_FILES:
        data = rom.file(fn)
        i, n = 0, len(data)
        while i < n - 7:
            if (data[i] == 0x00 and data[i + 1] == 0x05 and
                    data[i + 4] == 0x00 and data[i + 5] == 0x06):
                char_id = u16(data, i + 6)
                text_start = i + 8
                # one sub-line's text ends at the first standalone 0x00
                # (token-aware); the sub-line framing 00 03 00 0X / next
                # sub-header follows.  Only keep non-empty text runs.
                end = _clip_at_00(data, text_start)
                run = data[text_start:end]
                if run:
                    idx.setdefault(char_id, []).append(run)
                i = end + 1
            else:
                i += 1
    return idx


# ===========================================================================
# Packing: a text byte stream -> compact glyph-ref array for the HTML canvas
# ===========================================================================
import base64  # noqa: E402

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


# ---------------------------------------------------------------------------
# Text decode (readability aid).  The BITMAP is the pure-game render; the text
# below is the DECODE OF THE SAME RENDERED SLOTS via the glyph-identity tables
# (atlas identity = data/charmap.json; renderB identity = data/renderb_charset
# .json for slots <224 plus the VLM-built data/guide/renderb_ident.json for the
# kanji band).  Because it maps the exact slots the game draws, the text agrees
# with the bitmap glyph-for-glyph (a garble shows the wrong char AND the wrong
# glyph); it is never our source translation.
# ---------------------------------------------------------------------------
@dataclass
class _Ident:
    atlas: dict
    renderb: dict


def _load_identities() -> _Ident:
    import json as _json
    cm = _json.loads((REPO / "data/charmap.json").read_text())
    atlas: dict[int, str] = {}
    for ch, code in cm["one_byte"].items():
        atlas.setdefault(int(code), ch)
    for s, ch in cm["jp_slot_chars"].items():
        atlas.setdefault(int(s), ch)
    for ch, s in cm["two_byte_zh"].items():
        atlas.setdefault(int(s), ch)
    for s, ch in cm.get("slot_chars_extra", {}).items():
        atlas.setdefault(int(s), ch)
    # VLM-identified atlas cells that carry no charmap identity (decode-only,
    # so every rendered slot has a readable transcription — no □ in the guide)
    aext = REPO / "data/guide/atlas_ident.json"
    if aext.exists():
        for s, ch in _json.loads(aext.read_text()).get("slots", {}).items():
            if ch:
                atlas.setdefault(int(s), ch)
    rb: dict[int, str] = {}
    rc = _json.loads((REPO / "data/renderb_charset.json").read_text())["slots"]
    for s, info in rc.items():
        if info.get("char"):
            rb[int(s)] = info["char"]
    ext = REPO / "data/guide/renderb_ident.json"
    if ext.exists():
        for s, ch in _json.loads(ext.read_text()).get("slots", {}).items():
            if ch:
                rb[int(s)] = ch
    return _Ident(atlas, rb)


_IDENT: _Ident | None = None


def decode_text(rom: GameROM, data: bytes, surface: str, exp=None,
                dialogue: bool = False, _depth: int = 0) -> str:
    """Unicode transcription of the rendered slots (see note above)."""
    global _IDENT
    if _IDENT is None:
        _IDENT = _load_identities()
    exp = exp or rom.expand
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
        ch = (_IDENT.renderb.get(slot) if font == "B" else _IDENT.atlas.get(slot))
        out.append(ch if ch is not None else "\u25a1")   # □ = unidentified glyph
    return "".join(out).strip("▼")


# ===========================================================================
# Aggregate: walk every surface, render JP + ZH, into a JSON-able GAMEDATA dict
# ===========================================================================
# game placeholder labels for empty roster/char slots — not real units/chars
_PLACEHOLDER_BYTES = {b"\xf4\xfe",            # 欠番 (system-dict macro; vacant slot)
                      b"\xea\x5a\xe8\x77"}     # 预备 (reserve slot)


def _dummy_name(b: bytes | None) -> bool:
    return (not b or b == b"\x01" or b == b"\x00"
            or b in _PLACEHOLDER_BYTES)


def _is_priming_row(jb: bytes) -> bool:
    """Glyph-priming warmup rows every stage file opens with (あいうえおかきく…):
    the block payload starts with the consecutive kana slots 0x15..0x1b.  These
    are not dialogue — they prime the glyph cache — so the review omits them."""
    p = jb[1:] if jb[:1] == b"\x15" else jb          # drop block opener
    return p[:5] == bytes((0x16, 0x17, 0x18, 0x19, 0x1A))


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


BRIEF_LO, BRIEF_HI = 0x1985A4, 0x1A626B      # briefing (作戦内容) record region
BRIEF_POOL_RAM = 0x023E7000                  # pool B: ZH briefing blobs


def _brief_has_ptr(p: bytes) -> bool:
    for i in range(len(p) - 4):
        if p[i] in (0x13, 0x16):
            v = int.from_bytes(p[i + 1:i + 5], "little")
            if 0x02180000 <= v < 0x021B0000:
                return True
    return False


def extract_briefings(jp: GameROM, zh: GameROM) -> list[dict]:
    """Briefing (作戦内容) text, JP + ZH.  JP text is inline in the record region
    [0x1985A4,0x1A626B); the ZH build relocates it to pool B (0x023E7000) as
    finer display blobs.  Both are in narrative order but segment differently,
    so JP blocks are greedily paired with the consecutive ZH blobs that cover
    them (by rendered glyph count) — a bilingual reference for review."""
    # JP inline briefing blocks (token-aware 15..0000), text-only
    jblocks = []
    i = BRIEF_LO
    while i < BRIEF_HI - 1:
        if jp.arm9[i] == 0x15:
            t = text_codec.find_terminator(jp.arm9, i + 1)
            if 0 < t <= BRIEF_HI:
                p = jp.arm9[i + 1:t]
                if len(list(glyph_stream(jp, p, "stage", jp.expand))) >= 4 and not _brief_has_ptr(p):
                    jblocks.append(p)
                i = t + 2
                continue
        i += 1
    # ZH pool-B blobs, in offset order (offsets = structural index; bytes read
    # from the ROM's pool B) — token-aware NUL-walk of the pool.
    import json as _json
    bb = _json.loads((REPO / "data/arenas/briefing_blobs.json").read_text())
    zblobs = []
    for e in bb["entries"]:
        b = zh.cstr(BRIEF_POOL_RAM + int(e["offset"], 16))
        if b and len(list(glyph_stream(zh, b, "stage"))) >= 1:
            zblobs.append(b)
    # greedy alignment: accumulate ZH blobs to cover each JP block
    out, zi = [], 0
    for jb in jblocks:
        jn = len(list(glyph_stream(jp, jb, "stage", jp.expand)))
        acc, zc = [], 0
        while zi < len(zblobs) and zc < jn * 0.85:
            acc.append(zblobs[zi])
            zc += len(list(glyph_stream(zh, zblobs[zi], "stage")))
            zi += 1
        out.append({"jp": pack_glyphs(jp, jb, "stage", jp.expand),
                    "zh": "\x1f".join(pack_glyphs(zh, z, "stage") for z in acc)})
    return out


def extract_briefings_by_stage(jp: GameROM, zh: GameROM) -> list[dict]:
    """作战内容 briefings grouped by the stage descriptor that owns them.

    Each stage descriptor's +0x14 points at the start of that stage's briefing
    in the inline region; JP briefing blocks there are greedily paired with the
    consecutive ZH pool-B blobs that cover them (they segment differently)."""
    import collections
    import json as _json
    # JP inline briefing text blocks (address order)
    jblocks = []
    i = BRIEF_LO
    while i < BRIEF_HI - 1:
        if jp.arm9[i] == 0x15:
            t = text_codec.find_terminator(jp.arm9, i + 1)
            if 0 < t <= BRIEF_HI:
                p = jp.arm9[i + 1:t]
                if len(list(glyph_stream(jp, p, "stage", jp.expand))) >= 3 and not _brief_has_ptr(p):
                    jblocks.append((i, p))
                i = t + 2
                continue
        i += 1
    # ZH pool-B blobs (offset index from data; bytes read from the ROM)
    bb = _json.loads((REPO / "data/arenas/briefing_blobs.json").read_text())
    zblobs = [zh.cstr(BRIEF_POOL_RAM + int(e["offset"], 16)) for e in bb["entries"]]
    zblobs = [b for b in zblobs if b and any(True for _ in glyph_stream(zh, b, "stage"))]
    zlen = [len(list(glyph_stream(zh, b, "stage"))) for b in zblobs]
    jlen = {off: len(list(glyph_stream(jp, jb, "stage", jp.expand))) for off, jb in jblocks}
    # group JP blocks by the descriptor whose +0x14 <= block offset
    starts = sorted((u32(jp.arm9, STAGE_DESC + k * STAGE_DESC_STRIDE + STAGE_DESC_BRIEF) - RAM_BASE, k)
                    for k in range(101)
                    if BRIEF_LO <= u32(jp.arm9, STAGE_DESC + k * STAGE_DESC_STRIDE + STAGE_DESC_BRIEF) - RAM_BASE < BRIEF_HI)

    def _desc_of(off):
        k = None
        for so, kk in starts:
            if so <= off:
                k = kk
            else:
                break
        return k
    jgroups: dict = collections.OrderedDict()
    for off, jb in jblocks:
        jgroups.setdefault(_desc_of(off), []).append((off, jb))
    # Distribute ZH blobs across groups by CUMULATIVE JP-glyph proportion.  The old
    # per-block greedy cascaded: an untranslated JP block still consumed ZH blobs, so
    # drift starved late stages (SP7 etc.).  Assigning each group its glyph-share of
    # the ZH pool by absolute cumulative position is drift-free (re-syncs every stage).
    GJ = sum(jlen.values()) or 1
    GZ = sum(zlen) or 1
    groups: dict = collections.OrderedDict()
    zi = zcum = cumJP = 0
    glist = list(jgroups.items())
    for gi, (k, jblist) in enumerate(glist):
        cumJP += sum(jlen[o] for o, _ in jblist)
        target = GZ if gi == len(glist) - 1 else round(cumJP / GJ * GZ)
        gblobs, glens = [], []
        while zi < len(zblobs) and zcum < target:
            gblobs.append(zblobs[zi]); glens.append(zlen[zi]); zcum += zlen[zi]; zi += 1
        # within-group greedy pair (bounded to this group -> no cross-stage drift)
        items, bj = [], 0
        for o, jb in jblist:
            acc, zc = [], 0
            while bj < len(gblobs) and zc < jlen[o] * 0.85:
                acc.append(gblobs[bj]); zc += glens[bj]; bj += 1
            items.append((jb, acc))
        if bj < len(gblobs) and items:            # leftover ZH -> last block (no loss)
            items[-1] = (items[-1][0], items[-1][1] + gblobs[bj:])
        groups[k] = items
    def _dlab(k, off):
        """Decode a descriptor string field (+0x0c label / +0x10 title) for the
        client-side stage matcher (# briefing -> Route stage by title_jp)."""
        if k is None:
            return ""
        p = u32(jp.arm9, STAGE_DESC + k * STAGE_DESC_STRIDE + off)
        o = p - RAM_BASE
        if not (0 <= o < len(jp.arm9)):
            return ""
        s = jp.cstr(p)
        return decode_text(jp, s, "bank", jp.expand_sys) if s else ""

    out = []
    for k, items in groups.items():
        lines = []
        for jb, acc in items:
            zjoin = b"\x00".join(acc)
            lines.append({
                "jt": decode_text(jp, jb, "stage", jp.expand, True),
                "jb": pack_glyphs(jp, jb, "stage", jp.expand, True),
                "zt": decode_text(zh, zjoin, "stage", zh.expand, True) if zjoin else "",
                "zb": pack_glyphs(zh, zjoin, "stage", zh.expand, True) if zjoin else "",
            })
        if lines:
            out.append({"desc": k, "n": len(lines), "lines": lines,
                        "lab": _dlab(k, STAGE_DESC_LABEL), "title": _dlab(k, STAGE_DESC_TITLE)})
    return out


STAGE_DESC = 0x175560          # stage descriptor table (101 × 0x34)
STAGE_DESC_STRIDE = 0x34
STAGE_DESC_BRIEF = 0x14        # +0x14 -> briefing region start for that stage
STAGE_DESC_LABEL = 0x0c        # +0x0c -> stage-number label ("第3前" …)
STAGE_DESC_TITLE = 0x10        # +0x10 -> stage title ("ソロモンの攻略（前編）" …)
SHARED_MIN = 8                 # a dialogue block in >= this many stages is a
                               # shared template (thanks-for-playing / 特别演习
                               # extra-mode text appended to many files) — shown
                               # once, not repeated per stage (#5)


def _load_stage_blocks() -> dict:
    """Optional per-block speaker/branch metadata from the VM-walk subagent."""
    p = REPO / "data/guide/stage_blocks.json"
    if p.exists():
        import json as _json
        return _json.loads(p.read_text())
    return {}


# library / hangar encyclopedia data files (renderA-direct 'stage' surface + dialogue dict)
LIBRARY_FILES = [
    ("char_bios",     "library/character_bios.json", "324.bin", "\u89d2\u8272\u56fe\u9274 Character bios", True),
    ("unit_bios",     "library/unit_bios.json",      "c4b.bin", "\u673a\u4f53\u56fe\u9274 Unit bios",      True),
    ("part_names",    "hangar/part_names.json",      "b6e.bin", "\u6539\u9020\u90e8\u4ef6\u540d Part names", False),
    ("part_captions", "hangar/part_captions.json",   "b6f.bin", "\u90e8\u4ef6\u8bf4\u660e Part captions",   True),
]


def extract_bios(jp: GameROM, zh: GameROM) -> list[dict]:
    """Encyclopedia (資料館 library + hangar): character/unit biographies and
    改造 part names/captions.  Bytes are read from the ROM data files at each
    entry's offset/size and rendered on the renderA-direct 'stage' surface with
    the dialogue dict (verified: a char bio decodes 「誇りや名誉、血筋…」)."""
    import json as _json
    out = []
    for key, rel, fname, title, long in LIBRARY_FILES:
        p = REPO / "data/files" / rel
        if not p.exists():
            continue
        d = _json.loads(p.read_text())
        entries = d.get("edits", d.get("entries", []))
        jf, zf = jp.file(fname), zh.file(fname)
        items = []
        for e in entries:
            off = int(e["offset"], 16) if isinstance(e["offset"], str) else e["offset"]
            sz = e["size"]
            jb, zb = bytes(jf[off:off + sz]), bytes(zf[off:off + sz])
            items.append({
                "ix": len(items) + 1,
                "zt": decode_text(zh, zb, "stage", zh.expand, True) if zb else "",
                "jt": decode_text(jp, jb, "stage", jp.expand, True) if jb else "",
                "zb": pack_glyphs(zh, zb, "stage", zh.expand, True) if zb else "",
                "jb": pack_glyphs(jp, jb, "stage", jp.expand, True) if jb else "",
            })
        out.append({"key": key, "title": title, "long": long, "n": len(items), "items": items})
    return out


def build_gamedata(jp: GameROM, zh: GameROM) -> dict:
    """Extract every reviewed surface from BOTH ROMs and pack for the browser.

    Each text field is emitted as {zt,jt,zb,jb}: ZH text, JP text (readability;
    decode of the rendered slots), ZH bitmap, JP bitmap (pure-game render).
    JP decode dicts/fonts (verified against the ZH translation): dialogue/
    cut-ins/detail = atlas 'stage' + DICT_TEXT; names/weapons/ID/specials =
    renderB 'bank' + DICT_SYS.
    """
    data: dict = {
        "sheets": {"jp": sheet_meta(jp, "atlas"),
                   "zh": sheet_meta(zh, "atlas"),
                   "rb": sheet_meta(jp, "renderb")},
    }

    def fld(jb, zb, surface, jexp, dlg=False, zexp=None):
        """One reviewed text field: ZH/JP text + ZH/JP bitmap.  zexp defaults to
        the ZH dialogue dict; 1df/1e0 specials pass zh.expand_sys (they reuse the
        system dict's numeric/percent macros on BOTH sides)."""
        ze = zexp or zh.expand
        return {
            "zt": decode_text(zh, zb, surface, ze, dlg) if zb else "",
            "jt": decode_text(jp, jb, surface, jexp, dlg) if jb else "",
            "zb": pack_glyphs(zh, zb, surface, ze, dlg) if zb else "",
            "jb": pack_glyphs(jp, jb, surface, jexp, dlg) if jb else "",
        }

    def name_fld(jb, zb):
        # names render on the system-dict trampoline; ZH names may now reuse the
        # JP original's system-dict macros (narrowed ASCII), so decode/pack ZH
        # with expand_sys too (not the dialogue dict).
        return fld(jb, zb, "bank", jp.expand_sys, zexp=zh.expand_sys)

    # cid -> name (for dialogue speaker labels; all named char-DB records)
    names = {}
    for cid in range(CHARDB_COUNT):
        zn = zh.cstr(u32(zh.arm9, CHARDB + cid * CHARDB_STRIDE + PILOT_NAME_FIELD))
        jn = jp.cstr(u32(jp.arm9, CHARDB + cid * CHARDB_STRIDE + PILOT_NAME_FIELD))
        if not _dummy_name(zn):
            names[cid] = {"zt": decode_text(zh, zn, "bank", zh.expand_sys),
                          "jt": decode_text(jp, jn, "bank", jp.expand_sys) if jn else ""}
    data["names"] = names

    # ---- 1a. stage dialogue (index from data/dialogue; bytes read from ROMs) --
    from utils import stage_text
    guide_ids = _extract_guide_stage_ids()
    sblocks = _load_stage_blocks()
    # first pass: count block frequency across stages (for shared-template dedup)
    import collections
    freq = collections.Counter()
    per_stage = []
    for f, sd in stage_text.iter_stage_data():
        jf, zf = jp.file(f), zh.file(f)
        edits = [(int(e["jp_offset"], 16), e["jp_len"], bytes.fromhex(e["zh_hex"]),
                  e.get("kind")) for e in sd.get("edits", [])]
        edits += [(int(x["jp_offset"], 16), 0, bytes.fromhex(x["hex"]), None)
                  for x in sd.get("inserts", [])]
        edits.sort()
        delta, blocks = 0, []
        for off, old, new, kind in edits:
            zoff = off + delta
            if kind == "dialogue":
                jb = jf[off:off + old]
                if not _is_priming_row(jb):
                    blocks.append((off, jb, zf[zoff:zoff + len(new)]))
                    freq[jb] += 1
            delta += len(new) - old
        per_stage.append((f, blocks))

    smeta = sblocks
    shared = []               # dedup'd shared template blocks (shown once)
    shared_seen = set()
    stages = []
    for f, blocks in per_stage:
        meta = smeta.get(f[:-4]) or smeta.get(f) or {}
        lines = []
        for off, jb, zb in blocks:
            if freq[jb] >= SHARED_MIN:
                if jb not in shared_seen:
                    shared_seen.add(jb)
                    shared.append(fld(jb, zb, "stage", jp.expand, True))
                continue
            m = meta.get(hex(off)) or meta.get(str(off)) or {}
            ln = fld(jb, zb, "stage", jp.expand, True)
            if m.get("sp", -1) >= 0:
                ln["sp"] = m["sp"]
            for k in ("branch", "choice", "event"):
                if m.get(k):
                    ln[k[0] if k != "event" else "ev"] = 1
            lines.append(ln)
        stages.append({"file": f[:-4], "gid": _file_to_guide_id(f, guide_ids),
                       "n": len(lines), "lines": lines})
    data["stages"] = stages
    data["shared"] = shared

    # ---- 1b. characters: name, 3 ID cmds, cut-ins, barks ---------------------
    jbark, zbark = build_bark_index(jp), build_bark_index(zh)
    chars = []
    for cid in range(CHARDB_COUNT):
        rec = CHARDB + cid * CHARDB_STRIDE
        jn = jp.cstr(u32(jp.arm9, rec + PILOT_NAME_FIELD))
        zn = zh.cstr(u32(zh.arm9, rec + PILOT_NAME_FIELD))
        if _dummy_name(zn):
            continue
        ids = []
        for slot in range(3):
            idn = cid * 3 + slot
            ji, zi = id_command(jp, idn), id_command(zh, idn)
            if _dummy_name(zi["name"]):
                continue
            cj, cz = cutin_line(jp, idn), cutin_line(zh, idn)
            ids.append({
                "nm": name_fld(ji["name"], zi["name"]),
                "sm": name_fld(ji["summary"], zi["summary"]),
                "dt": fld(ji["detail"], zi["detail"], "stage", jp.expand),
                "cut": fld(cj, cz, "stage", jp.expand),
                "tgt": TARGET_NAMES.get(zi["target"], ""),
                "map": bool(zi["cond"] & 0x02)})
        jb_list, zb_list = jbark.get(cid, []), zbark.get(cid, [])
        barks = [fld(jb_list[k] if k < len(jb_list) else b"", zb, "stage", jp.expand)
                 for k, zb in enumerate(zb_list)]
        if not ids and not barks:
            continue
        chars.append({"cid": cid, "nm": name_fld(jn, zn), "ids": ids, "barks": barks})
    data["chars"] = chars

    # ---- 1c. units: name, weapons, specials ----------------------------------
    ju = {u["utid"]: u for u in extract_units(jp)}
    zu = {u["utid"]: u for u in extract_units(zh)}
    units = []
    for utid in sorted(zu):
        z = zu[utid]
        j = ju.get(utid, {})
        if _dummy_name(z["name"]):
            continue
        jw = {s: b for s, _p, b in j.get("weapons", [])}
        weapons = [name_fld(jw.get(s), wb) for s, _p, wb in z["weapons"]]
        specials = []
        for sp in unit_specials(jp, zh, utid):
            specials.append({"kind": sp["kind"],
                             **fld(sp.get("_jb", b""), sp.get("_zb", b""), "bank",
                                   jp.expand_sys, zexp=zh.expand_sys)})
        units.append({"utid": utid, "nm": name_fld(j.get("name"), z["name"]),
                      "weapons": weapons, "specials": specials})
    data["units"] = units

    # ---- briefings (作战内容), per stage descriptor, into the Route tab -------
    data["briefings"] = extract_briefings_by_stage(jp, zh)
    # ---- encyclopedia (資料館 library + hangar bios / part names) ------------
    data["library"] = extract_bios(jp, zh)
    return data


# ===========================================================================
# HTML generation: inject sprite sheets + canvas renderer + tabs into 攻略.html
# ===========================================================================
# HTML generation: inject sprite sheets + canvas renderer + tabs into 攻略.html
# ===========================================================================
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
.gf-bmp .pop canvas{filter:brightness(.92) sepia(.25) hue-rotate(60deg) saturate(1.25)}
canvas.ggc{vertical-align:middle;image-rendering:pixelated;image-rendering:crisp-edges;display:inline-block}
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
</style>"""

# the browser-side module (canvas glyph renderer + grid tabs + route weave)
GG_JS = r"""
(function(){
var GG;
var $=function(s,r){return (r||document).querySelector(s);};
var SHEETS={};
function loadSheets(cb){var keys=['jp','zh','rb'],left=keys.length,done=false;
  function fin(){if(done)return;if(--left<=0){done=true;cb();}}
  keys.forEach(function(k){var im=new Image();im.onload=fin;im.onerror=fin;im.src=GG.sheets[k].png;SHEETS[k]=im;});}
function b64u16(s){var bin=atob(s),a=new Uint16Array(bin.length>>1);for(var i=0;i<a.length;i++)a[i]=bin.charCodeAt(i*2)|(bin.charCodeAt(i*2+1)<<8);return a;}
// deferred paint queue: canvases are SIZED up front (stable layout) and PAINTED over frames
var DRAWQ=[],PUMPING=false;
function pump(){PUMPING=true;var t=performance.now();while(DRAWQ.length&&performance.now()-t<10){var fn=DRAWQ.shift();try{fn();}catch(e){}}if(DRAWQ.length){requestAnimationFrame(pump);}else{PUMPING=false;}}
function queueDraw(fn){DRAWQ.push(fn);if(!PUMPING){PUMPING=true;requestAnimationFrame(pump);}}
// surface: 0=stage(renderA-direct, baseline +1) 1=bank(trampoline, +3); rom: 0=jp 1=zh
function drawStr(packed,surface,rom,scale){
  scale=scale||2; var g=b64u16(packed||''),W=0,i,v;
  for(i=0;i<g.length;i++){v=g[i];W+=(v===0xFFFF)?7:((v>=0x8000)?8:12);}
  W=Math.max(W,1);
  var cv=document.createElement('canvas');cv.className='ggc';cv.width=W*scale;cv.height=16*scale;
  cv.style.width=(W*scale)+'px';cv.style.height=(16*scale)+'px';
  queueDraw(function(){
    var ctx=cv.getContext('2d');ctx.imageSmoothingEnabled=false;var x=0,i,v;
    for(i=0;i<g.length;i++){v=g[i];
      if(v===0xFFFF){ctx.fillStyle='#3b567f';ctx.fillRect((x+2)*scale,6*scale,3*scale,4*scale);x+=7;continue;}
      var font=(v>=0x8000)?1:0,slot=v&0x7FFF,key=font?'rb':(rom?'zh':'jp'),sh=GG.sheets[key],img=SHEETS[key];
      var sx=(slot%sh.cols)*sh.cw,sy=((slot/sh.cols)|0)*sh.ch,adv=font?8:12,yoff=font?0:(surface?3:1);
      ctx.drawImage(img,sx,sy,sh.cw,sh.ch,x*scale,yoff*scale,sh.cw*scale,sh.ch*scale);x+=adv;
    }
  });
  return cv;
}
// -- field primitive: ZH text(big)+JP text(small) | ZH bitmap (hover reveals JP bitmap) --
function txtCol(zt,jt){
  var c=document.createElement('span');c.className='gf-txt';
  var z=document.createElement('span');z.className='zt';z.textContent=zt||'';c.appendChild(z);
  if(jt){var j=document.createElement('span');j.className='jt';j.textContent=jt;c.appendChild(j);}
  return c;
}
function bmpCol(zb,jb,surf,scale){
  var c=document.createElement('span');c.className='gf-bmp';
  // scrollable inner holds the (possibly wide) ZH bitmap; the JP popover is a
  // sibling of the scroller so overflow-x:auto never clips it (fixes Route hover)
  var scroll=document.createElement('span');scroll.className='gf-bmp-scroll';
  if(zb)scroll.appendChild(drawStr(zb,surf,1,scale));
  c.appendChild(scroll);
  if(jb){var pop=document.createElement('span');pop.className='pop';c.appendChild(pop);c.classList.add('hasjp');
    var built=false;c.addEventListener('mouseenter',function(){if(!built){built=true;pop.appendChild(drawStr(jb,surf,0,scale));}});}
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
// -- Units tab card --
function buildUnit(bd,u){
  var h=document.createElement('div');h.className='gcard-hd';h.appendChild(field(u.nm,1,2,null,'name'));bd.appendChild(h);
  if(u.weapons&&u.weapons.length){var s=sect(bd,'\u6b66\u5668 Weapons');u.weapons.forEach(function(w){s.appendChild(field(w,1,2));});}
  if(u.specials&&u.specials.length){var s2=sect(bd,'\u7279\u6b8a\u80fd\u529b / \u9632\u5fa1');u.specials.forEach(function(sp){s2.appendChild(field(sp,1,2,sp.kind==='defense'?'\u9632\u5fa1':'\u80fd\u529b'));});}
}
// -- IDs/Quotes tab card --
function buildChar(bd,c){
  var h=document.createElement('div');h.className='gcard-hd';h.appendChild(field(c.nm,1,2,null,'name'));bd.appendChild(h);
  c.ids.forEach(function(id){
    var s=sect(bd,'ID\u6307\u4ee4'+(id.tgt?' \u00b7 '+id.tgt:'')+(id.map?' \u00b7 \u5730\u56fe':' \u00b7 \u6218\u6597\u4e2d'));
    s.appendChild(field(id.nm,1,2,'\u6307\u4ee4'));
    if(id.sm&&(id.sm.zt||id.sm.jt||id.sm.zb))s.appendChild(field(id.sm,1,2,'\u6548\u679c\u540d'));
    if(id.dt&&(id.dt.zt||id.dt.zb))s.appendChild(field(id.dt,0,2,'\u6548\u679c',null,true));
    if(id.cut&&(id.cut.zt||id.cut.zb))s.appendChild(field(id.cut,0,2,'\u540d\u53f0\u8bcd',null,true));
  });
  if(c.barks&&c.barks.length){var b=collSect(bd,'\u6218\u6597\u558a\u8bdd Barks',c.barks.length);b._fill=function(){c.barks.forEach(function(bk){b.appendChild(field(bk,0,2,null,null,true));});};}
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
  try{var io=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting){io.disconnect();go();}});});io.observe(host);}catch(e){go();}
  var view=host.closest?host.closest('.view'):null,v=(view&&view.id)?view.id.replace('view-',''):null;
  if(v){var btn=$('#tabs button[data-v="'+v+'"]');if(btn)btn.addEventListener('click',function(){setTimeout(go,0);});}
  if(host.offsetParent!==null)go();
}
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
    lib.forEach(function(sec,si){grid('#ggenc'+si,sec.items,function(it){return '#'+it.ix;},function(bd,it){bd.appendChild(field(it,0,2,null,null,sec.long));});});
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
function bestBrief(s){var key=normT(s&&s.title_jp);if(key.length<2)return null;var best=null,bs=0;GG.briefings.forEach(function(b){var bt=normT(b.title);if(!bt)return;var sc=lcsLen(key,bt);if(sc>bs){bs=sc;best=b;}});return (bs>=2)?best:null;}
function dialogueBlock(stageList){
  var tot=0;stageList.forEach(function(s){tot+=s.n;});
  var c=collBlock('\u5267\u60c5\u5bf9\u8bdd\uff08\u542b\u8bf4\u8bdd\u4eba \u00b7 \u5206\u652f/\u4e8b\u4ef6\u6807\u8bb0\uff09',tot+' \u6bb5');
  c.body._fill=function(){stageList.forEach(function(s){
    if(stageList.length>1){var h=document.createElement('div');h.className='dlg-file';h.textContent=s.file;c.body.appendChild(h);}
    s.lines.forEach(function(ln){c.body.appendChild(dlgLine(ln));});});};
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
  extraStages.forEach(function(s){g.appendChild((function(){var w=document.createElement('div');w.className='gcard';w._idx=s.file;var ix=document.createElement('div');ix.className='gcard-ix';ix.textContent=s.file;w.appendChild(ix);var bd=document.createElement('div');bd.className='gcard-bd';var c=collBlock('\u5267\u60c5\u6587\u672c',s.n+' \u6bb5');c.body._fill=function(){s.lines.forEach(function(ln){c.body.appendChild(dlgLine(ln));});};bd.appendChild(c.wrap);w.appendChild(bd);return w;})());});
  sec.appendChild(g);host.appendChild(sec);
}
function addToggle(){
  var b=document.createElement('button');b.id='gg-bmptoggle';b.textContent='\u9690\u85cf\u4f4d\u56fe';
  b.addEventListener('click',function(){var off=document.body.classList.toggle('gg-nobmp');b.textContent=off?'\u663e\u793a\u4f4d\u56fe':'\u9690\u85cf\u4f4d\u56fe';});
  document.body.appendChild(b);
}
function start(){window.GG=GG;indexStages();loadSheets(function(){initTabs();weaveStage();addShared();addExtras();addToggle();});}
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
# self-test entry point (temporary; full HTML build added next)
# ===========================================================================
def _selftest(jp_path: str, zh_path: str):
    import json as _json
    zh = GameROM(Path(zh_path))
    print(f"ZH atlas slots: {zh.atlas_slots}  segs: {[(hex(a),hex(b)) for a,b,_ in zh._segs]}")
    # compare our glyph_stream to the parity-anchored render_oracle on sample data
    sys.path.insert(0, str(REPO / "test"))
    from render_oracle import Oracle
    orc = Oracle(Path(zh_path))
    # sample stage dialogue blocks + a bank record
    tests = []
    doc = _json.loads((REPO / "data/dialogue/stages/_STG01.json").read_text())
    for blk in (doc if isinstance(doc, list) else doc.get("blocks", []))[:3]:
        if isinstance(blk, dict) and blk.get("zh_hex"):
            tests.append(("stage", bytes.fromhex(blk["zh_hex"])))
    nb = _json.loads((REPO / "data/arenas/battle_name_pool.json").read_text())
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
    ju = {u["utid"]: u for u in extract_units(jp)}
    zu = {u["utid"]: u for u in extract_units(zh)}
    rows = []  # (label, image)

    def add(label, data, surface, rom_kind, exp=None):
        rows.append((label, render_line(jp, zh, data, surface, rom_kind, 3, exp)))

    for utid in (1, 639, 335):  # ∀高达, Eternal, Char's custom
        if utid in zu and zu[utid]["name"]:
            add(f"u{utid} JP roster(B)", ju[utid]["name"], "bank", "jp", jp.expand_sys)
            add(f"u{utid} ZH roster(B)", zu[utid]["name"], "bank", "zh")
            add(f"u{utid} ZH battle(A)", zu[utid]["name"], "stage", "zh")
            jw = {s: b for s, _p, b in ju.get(utid, {}).get("weapons", [])}
            for slot, _p, wb in zu[utid]["weapons"][:2]:
                if slot in jw:
                    add(f"u{utid} JP wpn{slot}(B)", jw[slot], "bank", "jp", jp.expand_sys)
                add(f"u{utid} ZH wpn{slot}(A)", wb, "stage", "zh")
    jp_pil = {p["char_id"]: p for p in extract_pilots(jp)}
    zh_pil = {p["char_id"]: p for p in extract_pilots(zh)}
    for cid in (18, 91, 1, 10):   # Amuro, Char, Aina, ...
        if cid in zh_pil:
            add(f"c{cid} JP roster(B)", jp_pil[cid]["name"], "bank", "jp", jp.expand_sys)
            add(f"c{cid} ZH roster(B)", zh_pil[cid]["name"], "bank", "zh")
            add(f"c{cid} ZH battle(A)", zh_pil[cid]["name"], "stage", "zh")

    pad, lblw = 6, 220
    W = lblw + max((im.width for _l, im in rows), default=100) + pad * 2
    H = sum(im.height + 8 for _l, im in rows) + pad * 2
    sheet = Image.new("RGB", (W, H), (18, 22, 34))
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
