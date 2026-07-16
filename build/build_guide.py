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
    with a NULL/zero name pointer is an unused slot (skipped)."""
    out = []
    for utid in range(MASTER_COUNT):
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
    carries, JP + ZH, each rendered on the renderB trampoline (bank) surface."""
    out = []

    def pack_pair(jb, zb):
        # 1df/1e0 records reuse the system-dict (0x1444B4) macros for shared
        # numeric/percent content on BOTH the JP and ZH sides, so expand with it.
        return {"jp": pack_glyphs(jp, jb, "bank", jp.expand_sys) if jb else "",
                "zh": pack_glyphs(zh, zb, "bank", zh.expand_sys) if zb else ""}

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


# ===========================================================================
# Aggregate: walk every surface, render JP + ZH, into a JSON-able GAMEDATA dict
# ===========================================================================
def _dummy_name(b: bytes | None) -> bool:
    return not b or b == b"\x01" or b == b"\x00"


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


def build_gamedata(jp: GameROM, zh: GameROM) -> dict:
    """Extract every reviewed surface from BOTH ROMs and pack for the browser.

    JP decode dicts/fonts (empirically verified against the ZH translation):
      * dialogue / cut-ins / ID-detail : atlas ('stage'), DICT_TEXT (jp.expand)
      * names / weapons / ID name+summary : renderB ('bank'), DICT_SYS (expand_sys)
      * ZH has no F-refs, so its dict is irrelevant; ZH names render on BOTH
        the roster (trampoline 'bank') and battle (renderA 'stage') paths.
    """
    data: dict = {
        "sheets": {"jp": sheet_meta(jp, "atlas"),
                   "zh": sheet_meta(zh, "atlas"),
                   "rb": sheet_meta(jp, "renderb")},
    }

    def name_entry(jb, zb):
        """A name/weapon: JP (roster renderB), ZH roster (bank) + battle (atlas)."""
        e = {"jp": pack_glyphs(jp, jb, "bank", jp.expand_sys) if jb else "",
             "zr": pack_glyphs(zh, zb, "bank") if zb else "",
             "zb": pack_glyphs(zh, zb, "stage") if zb else ""}
        return e

    # ---- 1a. stage dialogue (index from data/dialogue; bytes read from ROMs) --
    from utils import stage_text
    guide_ids = _extract_guide_stage_ids()
    stages = []
    for f, sd in stage_text.iter_stage_data():
        jf, zf = jp.file(f), zh.file(f)
        edits = [(int(e["jp_offset"], 16), e["jp_len"], bytes.fromhex(e["zh_hex"]),
                  e.get("kind")) for e in sd.get("edits", [])]
        edits += [(int(x["jp_offset"], 16), 0, bytes.fromhex(x["hex"]), None)
                  for x in sd.get("inserts", [])]
        edits.sort()
        delta, lines = 0, []
        for off, old, new, kind in edits:
            zoff = off + delta
            if kind == "dialogue":
                jb = jf[off:off + old]
                zb = zf[zoff:zoff + len(new)]
                if not _is_priming_row(jb):     # skip あいうえお glyph-warmup rows
                    lines.append([pack_glyphs(jp, jb, "stage", jp.expand, True),
                                  pack_glyphs(zh, zb, "stage", zh.expand, True)])
            delta += len(new) - old
        stages.append({"file": f[:-4], "gid": _file_to_guide_id(f, guide_ids),
                       "n": len(lines), "lines": lines})
    data["stages"] = stages

    # ---- 1b. characters: name (2 plates), 3 ID cmds, cut-ins, barks ----------
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
                "nm": name_entry(ji["name"], zi["name"]),
                "sm": name_entry(ji["summary"], zi["summary"]),
                "dt": {"jp": pack_glyphs(jp, ji["detail"], "stage", jp.expand) if ji["detail"] else "",
                       "zh": pack_glyphs(zh, zi["detail"], "stage") if zi["detail"] else ""},
                "cut": {"jp": pack_glyphs(jp, cj, "stage", jp.expand) if cj else "",
                        "zh": pack_glyphs(zh, cz, "stage") if cz else ""},
                "tgt": TARGET_NAMES.get(zi["target"], ""),
                "map": bool(zi["cond"] & 0x02)})
        jb_list, zb_list = jbark.get(cid, []), zbark.get(cid, [])
        barks = [[pack_glyphs(jp, jb_list[k], "stage", jp.expand) if k < len(jb_list) else "",
                  pack_glyphs(zh, zb, "stage")]
                 for k, zb in enumerate(zb_list)]
        if not ids and not barks:
            continue
        chars.append({"cid": cid, "nm": name_entry(jn, zn), "ids": ids, "barks": barks})
    data["chars"] = chars

    # ---- 1c. units: name (2 plates), weapons, specials -----------------------
    data["briefings"] = extract_briefings(jp, zh)

    ju = {u["utid"]: u for u in extract_units(jp)}
    zu = {u["utid"]: u for u in extract_units(zh)}
    units = []
    for utid in sorted(zu):
        z = zu[utid]
        j = ju.get(utid, {})
        if _dummy_name(z["name"]):
            continue
        jw = {s: b for s, _p, b in j.get("weapons", [])}
        weapons = [name_entry(jw.get(s), wb) for s, _p, wb in z["weapons"]]
        units.append({"utid": utid, "nm": name_entry(j.get("name"), z["name"]),
                      "weapons": weapons, "specials": unit_specials(jp, zh, utid)})
    data["units"] = units
    return data


# ===========================================================================
# HTML generation: inject sprite sheets + canvas renderer + tabs into 攻略.html
# ===========================================================================
GG_STYLE = """<style id="gg-style">
.ggwrap{margin-top:14px}
.gg-intro{color:var(--ink2);font-size:13px;margin:-4px 0 14px;max-width:900px}
.gg-src{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:12px;margin:0 0 12px;overflow:hidden}
.gg-hd{display:flex;align-items:center;gap:10px;padding:9px 13px;cursor:pointer;user-select:none}
.gg-hd:hover{background:#16223a}
.gg-hd .idx{font-size:11px;color:var(--ink4);font-family:ui-monospace,monospace}
.gg-hd .nm{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.gg-bd{display:none;padding:4px 13px 12px;border-top:1px solid var(--line)}
.gg-src.open .gg-bd{display:block}
.gg-row{display:flex;gap:10px;align-items:flex-start;padding:4px 0;border-bottom:1px solid #ffffff08;flex-wrap:wrap}
.gg-lab{font-size:11px;color:var(--ink4);min-width:52px;padding-top:5px;flex:none}
.gg-pair{display:flex;flex-direction:column;gap:2px}
.gg-jp canvas{image-rendering:pixelated;filter:brightness(.82) sepia(.3) hue-rotate(60deg) saturate(1.3)}
.gg-zh canvas{image-rendering:pixelated}
.gg-sub{font-size:10px;color:var(--ink4);margin-right:5px}
.gg-tag{font-size:10.5px;color:var(--cyan2);border:1px solid var(--line2);border-radius:5px;padding:0 5px;margin-left:6px}
.gg-warn{color:var(--bad);font-weight:700}
.gg-eff{color:var(--gold);font-size:11px}
canvas.ggc{vertical-align:middle;image-rendering:pixelated;image-rendering:crisp-edges}
.gg-blk{margin:6px 0;padding:6px 9px;background:#0e1626;border:1px solid var(--line);border-radius:8px}
.gg-blk .bt{font-size:11px;color:var(--ink3);margin-bottom:3px;letter-spacing:.3px}
.gg-search{margin:0 0 12px}
.gg-dlg .gg-row{border:none;padding:2px 0}
.gg-note{font-size:12px;color:var(--ink3);background:#0e1626;border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin-bottom:12px}
</style>"""

# the browser-side module (canvas glyph renderer + two new tabs + dialogue weave)
GG_JS = r"""
(function(){
var GG;
var $=function(s,r){return (r||document).querySelector(s);};
var SHEETS={};
function loadSheets(cb){var keys=['jp','zh','rb'],left=keys.length;keys.forEach(function(k){var im=new Image();im.onload=function(){if(--left===0)cb();};im.src=GG.sheets[k].png;SHEETS[k]=im;});}
function b64u16(s){var bin=atob(s),a=new Uint16Array(bin.length>>1);for(var i=0;i<a.length;i++)a[i]=bin.charCodeAt(i*2)|(bin.charCodeAt(i*2+1)<<8);return a;}
// surface: 0=stage(renderA-direct) 1=bank(trampoline); rom: 0=jp 1=zh
function drawStr(packed,surface,rom,scale){
  scale=scale||2; var g=b64u16(packed||''),W=0,i,v;
  for(i=0;i<g.length;i++){v=g[i];W+=(v===0xFFFF)?7:((v>=0x8000)?8:12);}
  W=Math.max(W,1);
  var cv=document.createElement('canvas');cv.className='ggc';cv.width=W*scale;cv.height=16*scale;
  cv.style.width=(W*scale)+'px';cv.style.height=(16*scale)+'px';
  var ctx=cv.getContext('2d');ctx.imageSmoothingEnabled=false;var x=0;
  for(i=0;i<g.length;i++){v=g[i];
    if(v===0xFFFF){ctx.fillStyle='#3b567f';ctx.fillRect((x+2)*scale,6*scale,3*scale,4*scale);x+=7;continue;}
    var font=(v>=0x8000)?1:0,slot=v&0x7FFF,key=font?'rb':(rom?'zh':'jp'),sh=GG.sheets[key],img=SHEETS[key];
    var sx=(slot%sh.cols)*sh.cw,sy=((slot/sh.cols)|0)*sh.ch,adv=font?8:12,yoff=font?0:(surface?3:1);
    ctx.drawImage(img,sx,sy,sh.cw,sh.ch,x*scale,yoff*scale,sh.cw*scale,sh.ch*scale);x+=adv;
  }
  return cv;
}
function jpLine(packed,surface,scale){var d=document.createElement('span');d.className='gg-jp';if(packed)d.appendChild(drawStr(packed,surface,0,scale));return d;}
function zhLine(packed,surface,scale){var d=document.createElement('span');d.className='gg-zh';if(packed)d.appendChild(drawStr(packed,surface,1,scale));return d;}
function row(label,children){var r=document.createElement('div');r.className='gg-row';var l=document.createElement('div');l.className='gg-lab';l.textContent=label;r.appendChild(l);var p=document.createElement('div');p.className='gg-pair';children.forEach(function(c){p.appendChild(c);});r.appendChild(p);return r;}
// a NAME entry {jp, zr(roster), zb(battle)} -> JP ref + ZH on both nameplates
function nameRows(container,label,e){
  if(!e)return;
  container.appendChild(row(label+' 日',[jpLine(e.jp,1,3)]));
  if(e.zr===e.zb){ container.appendChild(row(label+' 中',[zhLine(e.zr,1,3)])); }
  else{
    var jr=jpLine('',1,3); // placeholder
    container.appendChild(row(label+' 名牌',[zhLine(e.zr,1,3)]));
    var w=document.createElement('span');w.className='gg-warn gg-sub';w.textContent='⚠ 两处名牌不一致';
    var br=row(label+' 战斗',[zhLine(e.zb,0,3)]);br.appendChild(w);container.appendChild(br);
  }
}
function idBlock(id){
  var b=document.createElement('div');b.className='gg-blk';
  var t=document.createElement('div');t.className='bt';
  t.textContent='ID指令'+(id.tgt?(' · 对象:'+id.tgt):'')+(id.map?' · 地图':' · 战斗中');b.appendChild(t);
  b.appendChild(row('指令名',[jpLine(id.nm.jp,1,3),zhLine(id.nm.zr,1,3)]));
  if(id.sm.jp||id.sm.zr)b.appendChild(row('效果名',[jpLine(id.sm.jp,1,3),zhLine(id.sm.zr,1,3)]));
  if(id.dt.jp||id.dt.zh)b.appendChild(row('效果',[jpLine(id.dt.jp,0,2),zhLine(id.dt.zh,0,2)]));
  if(id.cut.jp||id.cut.zh)b.appendChild(row('名台词',[jpLine(id.cut.jp,0,2),zhLine(id.cut.zh,0,2)]));
  return b;
}
function renderCharInto(el,c){
  el.innerHTML='';
  nameRows(el,'姓名',c.nm);
  c.ids.forEach(function(id){el.appendChild(idBlock(id));});
  if(c.barks.length){
    var b=document.createElement('div');b.className='gg-blk';var t=document.createElement('div');t.className='bt';t.textContent='战斗喊话 ('+c.barks.length+')';b.appendChild(t);
    c.barks.forEach(function(bk){b.appendChild(row('',[jpLine(bk[0],0,2),zhLine(bk[1],0,2)]));});
    el.appendChild(b);
  }
}
function renderUnitInto(el,u){
  el.innerHTML='';
  nameRows(el,'机体',u.nm);
  if(u.weapons.length){var b=document.createElement('div');b.className='gg-blk';var t=document.createElement('div');t.className='bt';t.textContent='武器';b.appendChild(t);
    u.weapons.forEach(function(w){b.appendChild(row('',[jpLine(w.jp,1,3),zhLine(w.zr,1,3)]));});el.appendChild(b);}
  if(u.specials&&u.specials.length){var b2=document.createElement('div');b2.className='gg-blk';var t2=document.createElement('div');t2.className='bt';t2.textContent='特殊能力 / 防御';b2.appendChild(t2);
    u.specials.forEach(function(s){b2.appendChild(row(s.kind==='defense'?'防御':'能力',[jpLine(s.jp,1,3),zhLine(s.zh,1,3)]));});el.appendChild(b2);}
}
// lazy: render an entry's body when its header scrolls into view / is opened
var io=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting){var el=en.target;if(!el._done){el._done=1;el._render();}io.unobserve(el);}});},{rootMargin:'400px'});
function srcCard(idx,titleChildren,renderFn){
  var w=document.createElement('div');w.className='gg-src';
  var hd=document.createElement('div');hd.className='gg-hd';
  var ix=document.createElement('span');ix.className='idx';ix.textContent=idx;hd.appendChild(ix);
  var nm=document.createElement('div');nm.className='nm';titleChildren.forEach(function(c){nm.appendChild(c);});hd.appendChild(nm);
  var bd=document.createElement('div');bd.className='gg-bd';w.appendChild(hd);w.appendChild(bd);
  var built=false;function build(){if(built)return;built=true;renderFn(bd);}
  hd.addEventListener('click',function(){build();w.classList.toggle('open');});
  bd._render=build;io.observe(bd);   // pre-render title glyphs even before open
  return w;
}
function buildList(host,items,mk){var frag=document.createDocumentFragment();items.forEach(function(it,i){frag.appendChild(mk(it,i));});host.appendChild(frag);}
// -- tab views ----------------------------------------------------------------
function addTab(v,label,em){var b=document.createElement('button');b.dataset.v=v;b.innerHTML=label+'<span class="em">'+em+'</span>';$('#tabs').appendChild(b);}
function addView(v){var s=document.createElement('section');s.className='view';s.id='view-'+v;$('main').appendChild(s);return s;}
function titleName(e,fallback){var s=document.createElement('span');var jc=jpLine(e&&e.jp,1,3),zc=zhLine(e&&(e.zr||e.zb),1,3);s.appendChild(zc);var jd=document.createElement('span');jd.className='gg-sub';jd.style.marginLeft='10px';jd.appendChild(jc);s.appendChild(jd);return s;}
function renderBriefings(host){
  GG.briefings.forEach(function(b){
    var r=document.createElement('div');r.className='gg-row';var p=document.createElement('div');p.className='gg-pair';
    p.appendChild(jpLine(b.jp,0,2));
    (b.zh?b.zh.split('\u001f'):[]).forEach(function(z){p.appendChild(zhLine(z,0,2));});
    r.appendChild(p);host.appendChild(r);
  });
}
function initTabs(){
  addTab('ggids','ID/名台词','Quotes');addTab('ggunits','机体/武器','Units');addTab('ggbrief','作战简报','Briefing');
  var vb=addView('ggbrief');
  vb.innerHTML='<h2 class="sec">作战简报 <span class="muted" style="font-size:13px">（作戦内容 · '+GG.briefings.length+' 段 · 游戏内简报数组）</span></h2>'+
    '<p class="gg-intro">游戏「作战内容」简报文本，日文原文（inline @0x1985A4）与中文实机渲染（重定位至 pool B @0x023E7000）逐段并列。日中分段不同（中文换行更细），已按叙事顺序就近对齐，供核对译文。</p>'+
    '<div id="ggbrieflist" class="ggwrap"></div>';
  var bl=$('#ggbrieflist');var lazy=document.createElement('div');lazy.className='gg-note';lazy.style.cursor='pointer';lazy.textContent='点击展开全部简报 ('+GG.briefings.length+' 段)…';
  lazy.addEventListener('click',function(){if(bl.childElementCount<=1){renderBriefings(bl);}lazy.style.display='none';});
  bl.appendChild(lazy);
  var vi=addView('ggids');
  vi.innerHTML='<h2 class="sec">角色 · ID指令 · 名台词 · 战斗喊话 <span class="muted" style="font-size:13px">'+GG.chars.length+' 名（游戏内数组）</span></h2>'+
    '<p class="gg-intro">全部内容由脚本从游戏 ROM 的角色数组（char-DB 0xDCF18）直接反解、按游戏渲染管线还原。每个名字有<b>两处名牌</b>：后台一览（renderB 8×16 界面字体）与战斗中（renderA 12×12 图集）——同一字符串经两条渲染路径，此处均以真实像素还原（若两者不一致会标红）。日文＝原文参照，中文＝汉化实机渲染。</p>'+
    '<div class="gg-note">提示：点击任意角色展开其 3 条 ID 指令（指令名 · 使用条件 · 效果 · 名台词）与全部战斗喊话。</div>'+
    '<div class="gg-search"><input id="ggidq" placeholder="按序号筛选…" style="background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:7px 11px;color:var(--ink);width:220px"></div>'+
    '<div id="ggidlist" class="ggwrap"></div>';
  buildList($('#ggidlist'),GG.chars,function(c){return srcCard('#'+c.cid,[titleName(c.nm)],function(bd){renderCharInto(bd,c);});});
  var vu=addView('ggunits');
  vu.innerHTML='<h2 class="sec">机体 · 武器 · 特殊能力 <span class="muted" style="font-size:13px">'+GG.units.length+' 台（unit-master 0xB94BC）</span></h2>'+
    '<p class="gg-intro">由脚本从游戏机体主表（0xB94BC）直接反解：机体名、6 个武器槽名、特殊能力/防御。日文原文＋中文实机渲染并列，便于核对译名与术语。机体名同样并列<b>两处名牌</b>。</p>'+
    '<div class="gg-search"><input id="ggunitq" placeholder="按序号筛选…" style="background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:7px 11px;color:var(--ink);width:220px"></div>'+
    '<div id="ggunitlist" class="ggwrap"></div>';
  buildList($('#ggunitlist'),GG.units,function(u){return srcCard('#'+u.utid,[titleName(u.nm)],function(bd){renderUnitInto(bd,u);});});
  function wireFilter(inp,list){$(inp).addEventListener('input',function(e){var q=e.target.value.trim();Array.prototype.forEach.call($(list).children,function(card){var idx=card.querySelector('.idx').textContent;card.style.display=(!q||idx.indexOf(q)>=0)?'':'none';});});}
  wireFilter('#ggidq','#ggidlist');wireFilter('#ggunitq','#ggunitlist');
}
function linesInto(host,lines){
  lines.forEach(function(ln){
    var r=document.createElement('div');r.className='gg-row';
    var p=document.createElement('div');p.className='gg-pair';
    p.appendChild(jpLine(ln[0],0,2));p.appendChild(zhLine(ln[1],0,2));
    r.appendChild(p);host.appendChild(r);
  });
}
// -- weave dialogue into the existing stage panel ------------------------------
var stagesByGid={},extraStages=[];
function indexStages(){stagesByGid={};extraStages=[];GG.stages.forEach(function(s){if(s.gid){stagesByGid[s.gid]=(stagesByGid[s.gid]||[]).concat([s]);}else{extraStages.push(s);}});}
function dialogueSection(stageList){
  var wrap=document.createElement('div');wrap.className='block gg-dlg';
  var bt=document.createElement('div');bt.className='bt';var tot=0;stageList.forEach(function(s){tot+=s.n;});
  bt.textContent='剧情对话（游戏文本 · 日/中实机渲染 · '+tot+' 段）';wrap.appendChild(bt);
  var lazy=document.createElement('div');lazy.className='gg-note';lazy.textContent='点击展开 '+tot+' 段对话…';lazy.style.cursor='pointer';
  var box=document.createElement('div');box.style.display='none';
  lazy.addEventListener('click',function(){
    if(box.childElementCount===0){
      stageList.forEach(function(s){
        if(stageList.length>1){var h=document.createElement('div');h.className='gg-sub';h.textContent=s.file;box.appendChild(h);}
        linesInto(box,s.lines);
      });
    }
    box.style.display=(box.style.display==='none')?'block':'none';
  });
  wrap.appendChild(lazy);wrap.appendChild(box);return wrap;
}
function weaveStage(){
  var orig=window.stageDetail;if(!orig)return;
  window.stageDetail=function(s){orig(s);var list=stagesByGid[s.id];if(list){var pb=$('#pbody');if(pb){pb.appendChild(dialogueSection(list));}}};
}
function addExtras(){
  if(!extraStages.length){return;}
  var host=$('#view-stages');if(!host){return;}
  var sec=document.createElement('div');sec.className='ggwrap';
  sec.innerHTML='<h2 class="sec" style="margin-top:26px">游戏内额外关卡 <span class="muted" style="font-size:13px">（教程/自由战/终章等 · 无攻略卡 · '+extraStages.length+'）</span></h2><p class="gg-intro">以下关卡文件存在于游戏数组但攻略未单列，此处附其剧情文本（日/中实机渲染）。</p>';
  var grid=document.createElement('div');
  extraStages.forEach(function(s){
    var card=srcCard(s.file,[document.createTextNode('剧情文本 · '+s.n+' 段')],function(bd){linesInto(bd,s.lines);});
    grid.appendChild(card);
  });
  sec.appendChild(grid);host.appendChild(sec);
}
function start(){window.GG=GG;indexStages();loadSheets(function(){initTabs();weaveStage();addExtras();});}
function boot(){
  var raw=document.getElementById('gg-data').textContent.trim();
  var bytes=Uint8Array.from(atob(raw),function(c){return c.charCodeAt(0);});
  if(typeof DecompressionStream!=='undefined'){
    new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text()
      .then(function(t){GG=JSON.parse(t);start();})
      .catch(function(err){console.error('gg-data decompress failed',err);});
  }else{
    alert('此攻略的游戏数据需要支持 DecompressionStream 的浏览器（Chrome/Edge/Firefox/Safari 新版）。');
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
