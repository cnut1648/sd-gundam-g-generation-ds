"""Code-binary (arm9) assembly: translated tables + patches + relocated banks.

The translated game rebuilds its code binary from the ORIGINAL Japanese image in
two moves:

1. HEAD EDITS  [0x0 .. 0x1B6DA0)
   The original image is kept and selectively overwritten from the data files:
     * name tables      data/names/*.json    pointer words re-aimed at translated
                                             strings (units, weapons, pilots,
                                             ID commands, abilities, parts)
     * UI tables        data/ui/*.json       label literal sites, the text-macro
                                             dictionary, cut-in quote offsets,
                                             resource offset words
     * string arenas    data/arenas/*.json   translated strings written in place
                                             (pools) or into verified-free caves,
                                             plus the embedded event/briefing text
     * code patches     data/patches/code_patches.json   documented render/decoder
                                             detours, cave bodies, gameplay tweaks
     * raw regions      data/patches/raw_regions.json    annotated residual bytes

2. APPENDED BANKS  [0x1B6DA0 ..)
   The original image ends with a 2-entry boot-time autoload list (ITCM/DTCM).
   The translated image replaces that list with three extra autoload payloads and
   a 5-entry list, so the boot code itself copies our data into RAM:

       [12x12 glyph atlas]   -> RAM 0x023027A0   data/font/atlas12.bin
       [UI/name string bank] -> RAM 0x02328720   data/arenas/ui_names_bank.json
       [briefing blob bank]  -> RAM 0x023E7000   data/arenas/briefing_blobs.json
       [5-entry autoload list]

   The autoload loader walks the list forward while its SOURCE pointer advances
   continuously, so payload file order must equal list order; the ITCM/DTCM
   payloads stay in the head (at 0x1B6860) and the appended payloads follow at
   0x1B6DA0.  Boot-time facts that pin the RAM addresses:
     * the crt0 BSS clear runs [0x021B6860, 0x023027A0): the atlas sits exactly
       at the byte where clearing STOPS, the byte the original heap began at;
     * the heap floor literal (arena-lo) is bumped past the UI bank so the heap
       can never overwrite it;
     * the briefing bank lives above the heap ceiling (arena-hi 0x023C0000) in a
       RAM gap that is zero in every game state, so it needs no heap change.
   Module-params words at 0xB0C/0xB10 must point at the relocated list, the
   renderer's atlas base literal at 0x1315C at the relocated atlas, and the
   arena-lo literal at 0xA48F8 at the first free byte above the UI bank.

The 12-byte "nitrocode" trailer that follows the image inside the ROM is owned
by the container layer (utils/rom.py keeps it verbatim); it is NOT part of the
image built here.

Public entry point:  build_arm9(jp_arm9, data_dir=None) -> bytes
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

RAM_BASE = 0x02000000
IMAGE_LEN = 0x1B6DB8          # original image (without the nitrocode trailer)
FOOTER_LEN = 0x0C
NITROCODE = b"\x21\x06\xc0\xde"
LIST_OFF = 0x1B6DA0           # original 2-entry autoload list == append point
AUTOLOAD_SRC_OFF = 0x1B6860   # autoload source block (ITCM+DTCM payloads)

# ModuleParams / relocation literals (file offsets; RAM = RAM_BASE + offset)
MP_LIST_START = 0xB0C
MP_LIST_END = 0xB10
MP_AUTOLOAD_SRC = 0xB14
MP_BSS_END = 0xB1C
FONT_PTR_LITERAL = 0x1315C    # glyph renderer's atlas base
ARENA_LO_LITERAL = 0xA48F8    # default heap floor
ARENA_ALIGN = 0x100

# Four live pointer literals feed the same current/max-HP format strings to the
# formatted tile drawer at 0x02013BE0.  JP stored those strings at 0x021B3E90/94,
# inside the developer-string band later reused by the 0x1B3E22 code cave.  Keep
# one resident copy in the last 8 bytes of the retired in-image 12x12 atlas and
# repoint every caller; fixing only the reaction-panel pair at 0x240A8/0x240B0
# leaves the earlier unit-panel path broken.  The live atlas was already moved
# to 0x023027A0; no literal to the retired base 0x0211A2A0 remains.
HP_FORMAT_CURRENT_JP_RAM = 0x021B3E90
HP_FORMAT_MAX_JP_RAM = 0x021B3E94
HP_FORMAT_RAM = 0x0212D768
HP_FORMATS = b"D4\x00\x00/D4\x00"
HP_FORMAT_CURRENT_REL = 0
HP_FORMAT_MAX_REL = 4
HP_FORMAT_PTR_SITES = (
    (0x23640, HP_FORMAT_CURRENT_JP_RAM, HP_FORMAT_CURRENT_REL,
     "unit-panel current-HP format pointer"),
    (0x23648, HP_FORMAT_MAX_JP_RAM, HP_FORMAT_MAX_REL,
     "unit-panel max-HP format pointer"),
    (0x240A8, HP_FORMAT_CURRENT_JP_RAM, HP_FORMAT_CURRENT_REL,
     "reaction-panel current-HP format pointer"),
    (0x240B0, HP_FORMAT_MAX_JP_RAM, HP_FORMAT_MAX_REL,
     "reaction-panel max-HP format pointer"),
)

FONT_RAM = 0x023027A0         # == BSS-clear end == original heap floor
ITCM_ENTRY = (0x01FF8000, 0x520, 0)
DTCM_ENTRY = (0x027C0000, 0x020, 0)

# name-table geometry (documented in the data files; fixed by the game binary)
UNIT_NAME_FIELD = 0x00
WEAPON_BLOCK_OFF = 0x2C
PILOT_NAME_FIELD = 0x04
IDCMD_NAME_FIELD = 0x00
IDCMD_SUMMARY_FIELD = 0x08


def _i(v) -> int:
    """Parse an int that may be stored as '0x..' hex string."""
    return v if isinstance(v, int) else int(str(v), 16)


class _Image:
    """Byte patcher over the original image with old-value assertions."""

    def __init__(self, jp: bytes):
        self.jp = jp                      # pristine original (for asserts)
        self.buf = bytearray(jp[:LIST_OFF])

    def put(self, off: int, payload: bytes, what: str = "?"):
        if off < 0 or off + len(payload) > LIST_OFF:
            raise ValueError(f"{what}: write [{off:#x},{off + len(payload):#x}) "
                             f"outside the image head")
        self.buf[off:off + len(payload)] = payload

    def put_u32(self, off: int, value: int, what: str = "?",
                expect_old: int | None = None):
        if expect_old is not None:
            old = struct.unpack_from("<I", self.jp, off)[0]
            if old != expect_old:
                raise ValueError(f"{what}: word at {off:#x} is {old:#010x}, "
                                 f"expected {expect_old:#010x}")
        struct.pack_into("<I", self.buf, off, value)

    def put_u16(self, off: int, value: int, what: str = "?"):
        struct.pack_into("<H", self.buf, off, value)

    def put_hex(self, off: int, new_hex: str, what: str = "?",
                old_hex: str | None = None):
        new = bytes.fromhex(new_hex)
        if old_hex is not None:
            old = bytes.fromhex(old_hex)
            if self.jp[off:off + len(old)] != old:
                raise ValueError(f"{what}: bytes at {off:#x} do not match the "
                                 f"recorded original")
        self.put(off, new, what)


def _load(data_dir: Path, rel: str) -> dict:
    return json.loads((data_dir / rel).read_text())


# ---------------------------------------------------------------------------
# head-edit appliers (one per data file family)
# ---------------------------------------------------------------------------

def _apply_units(img: _Image, data_dir: Path):
    d = _load(data_dir, "names/units.json")
    t = d["table"]
    base, stride = _i(t["file_offset"]), _i(t["stride"])
    capacity_field = _i(t["carrier_capacity"].lstrip("+")) if "carrier_capacity" in t else None
    for e in d["entries"]:
        if "ptr" in e:
            img.put_u32(base + e["utid"] * stride + UNIT_NAME_FIELD,
                        _i(e["ptr"]), f"unit name {e['utid']}")
        if capacity_field is not None and "carrier_capacity" in e:
            # warship carrier-capacity stat (u16). Spec/default value: applies to
            # newly acquired units; existing saves bake their own slot allocation.
            img.put_u16(base + e["utid"] * stride + capacity_field,
                        int(e["carrier_capacity"]),
                        f"unit {e['utid']} carrier capacity")


def _apply_weapons(img: _Image, data_dir: Path):
    d = _load(data_dir, "names/weapons.json")
    t = d["table"]
    base, stride = _i(t["file_offset"]), _i(t["stride"])
    woff, wstride = _i(t["weapons_offset"]), _i(t["weapon_stride"])
    for e in d["entries"]:
        if "ptr" in e:
            off = base + e["utid"] * stride + woff + e["slot"] * wstride
            img.put_u32(off, _i(e["ptr"]), f"weapon name {e['utid']}/{e['slot']}")


def _apply_pilots(img: _Image, data_dir: Path):
    d = _load(data_dir, "names/pilots.json")
    t = d["table"]
    base, stride = _i(t["file_offset"]), _i(t["stride"])
    for e in d["entries"]:
        if "ptr" in e:
            img.put_u32(base + e["char_id"] * stride + PILOT_NAME_FIELD,
                        _i(e["ptr"]), f"pilot name {e['char_id']}")


def _apply_id_commands(img: _Image, data_dir: Path):
    d = _load(data_dir, "names/id_commands.json")
    t = d["table"]
    base, stride = _i(t["file_offset"]), _i(t["stride"])
    for e in d["entries"]:
        rec = base + e["id"] * stride
        name = e.get("name") or {}
        summ = e.get("summary") or {}
        if "ptr" in name:
            img.put_u32(rec + IDCMD_NAME_FIELD, _i(name["ptr"]),
                        f"ID command {e['id']} name")
        if "ptr" in summ:
            img.put_u32(rec + IDCMD_SUMMARY_FIELD, _i(summ["ptr"]),
                        f"ID command {e['id']} summary")
    ot = d["detail_offset_table"]
    obase = _i(ot["file_offset"])
    for e in d["details"]:
        if "offset" in e:
            img.put_u32(obase + e["index"] * 4, _i(e["offset"]),
                        f"ID command detail offset {e['index']}")


def _apply_abilities(img: _Image, data_dir: Path):
    d = _load(data_dir, "names/abilities.json")
    for e in d["entries"]:
        new, old = _i(e["ptr"]), _i(e["old_ptr"])
        for site in e["sites"]:
            img.put_u32(_i(site), new, f"ability name site {site}",
                        expect_old=old)


def _apply_parts(img: _Image, data_dir: Path):
    d = _load(data_dir, "names/parts.json")
    base = _i(d["table"]["file_offset"])
    for e in d["entries"]:
        img.put_u32(base + e["index"] * 4, _i(e["offset"]),
                    f"parts name offset {e['index']}")


def _apply_labels(img: _Image, data_dir: Path):
    d = _load(data_dir, "ui/labels.json")
    for e in d["entries"]:
        new, old = _i(e["ptr"]), _i(e["old_ptr"])
        for site in e["sites"]:
            img.put_u32(_i(site), new, f"label site {site}", expect_old=old)


def _apply_cutin_offsets(img: _Image, data_dir: Path):
    """Write the cut-in quote offset table DERIVED from the quote bank.

    The 1dc.bin quote bank is a concatenation of (header + payload +
    terminator + pad4) records; the arm9 table holds each record's start
    offset (index k = the k-th record, table[count-1] = total size sentinel)
    plus a separate resource-size word.  Deriving the offsets from
    data/files/battle/cutin_quotes.json at build time makes it IMPOSSIBLE
    for quote edits to desynchronize the table (the 名台词-mispairing
    regression class: the table went stale after quote records changed
    length, so every cut-in showed the wrong character's quote)."""
    from . import data_files, text_codec

    d = _load(data_dir, "ui/cutin_quote_offsets.json")
    q = json.loads((data_dir / "files" / "battle" / "cutin_quotes.json").read_text())
    offs, pos = [], 0
    for g in q["groups"]:
        offs.append(pos)
        payload = (bytes.fromhex(g["zh_hex"]) if g.get("zh_hex")
                   else text_codec.encode(g["zh"], allow_low15=True))
        pos += len(bytes.fromhex(g["header"])) + len(payload)
        pos += len(data_files.CUTIN_TERMINATOR)
        pos += (-pos) % 4
    offs.append(pos)                                   # size sentinel entry
    base = _i(d["table"]["file_offset"])
    count = d["table"]["count"]
    if len(offs) != count:
        raise ValueError(f"cut-in offsets: derived {len(offs)} entries, table holds {count}")
    for k, off in enumerate(offs):
        img.put_u32(base + k * 4, off, f"cut-in quote offset {k}")
    w = d["resource_size_word"]
    img.put_u32(_i(w["file_offset"]), pos, "cut-in resource size")


def _apply_resource_offsets(img: _Image, data_dir: Path):
    d = _load(data_dir, "ui/resource_offsets.json")
    for e in d["entries"]:
        img.put_u32(_i(e["file_offset"]), _i(e["value"]), e["what"],
                    expect_old=_i(e["old_value"]))


def _apply_dictionary(img: _Image, data_dir: Path):
    d = _load(data_dir, "ui/dictionary.json")
    base = _i(d["base"]["file_offset"])
    for e in d["offset_entries"]:
        img.put_u16(base + 2 * e["index"], _i(e["offset"]),
                    f"dictionary offset {e['index']}")
    for e in d["string_edits"]:
        img.put_hex(base + _i(e["offset"]), e["payload_hex"],
                    f"dictionary string @+{e['offset']}")


_ARENA_FILES = (
    "arenas/battle_name_pool.json",
    "arenas/idcmd_detail_pool.json",
    "arenas/post_dict_labels.json",
    "arenas/resident_caves.json",
)


def _apply_arenas(img: _Image, data_dir: Path):
    for rel in _ARENA_FILES:
        d = _load(data_dir, rel)
        base = _i(d["file_offset"])
        for e in d["entries"]:
            img.put_hex(base + _i(e["offset"]), e["payload_hex"],
                        f"{rel} @+{e['offset']}")


def _apply_event_blocks(img: _Image, data_dir: Path):
    d = _load(data_dir, "arenas/event_text_blocks.json")
    for e in d["entries"]:
        payload = bytes.fromhex(e["payload_hex"])
        if len(payload) != e["length"]:
            raise ValueError(f"event block {e['offset']}: payload length "
                             f"{len(payload)} != recorded {e['length']}")
        img.put(_i(e["offset"]), payload, f"event block {e['offset']}")


def _apply_patches(img: _Image, data_dir: Path, rel: str):
    d = _load(data_dir, rel)
    for e in d["entries"]:
        img.put_hex(_i(e["file_offset"]), e["new_hex"],
                    f"{rel}: {e.get('what', '?')}", old_hex=e.get("old_hex"))


# ---------------------------------------------------------------------------
# appended autoload banks
# ---------------------------------------------------------------------------

def _bank_bytes(data_dir: Path, rel: str) -> tuple[int, bytes]:
    """Rebuild an autoload string bank from its arena file -> (ram_base, blob)."""
    d = _load(data_dir, rel)
    size = _i(d["size"])
    blob = bytearray(size)
    for e in d["entries"]:
        off = _i(e["offset"])
        payload = bytes.fromhex(e["payload_hex"])
        if off + len(payload) > size:
            raise ValueError(f"{rel}: entry @+{off:#x} overruns the bank")
        blob[off:off + len(payload)] = payload
    return _i(d["ram_base"]), bytes(blob)


def _validate_input(jp: bytes):
    if len(jp) not in (IMAGE_LEN, IMAGE_LEN + FOOTER_LEN):
        raise ValueError(
            f"unexpected code-binary length {len(jp):#x}; expected the original "
            f"image ({IMAGE_LEN:#x}) or image+trailer ({IMAGE_LEN + FOOTER_LEN:#x})")
    checks = (
        (MP_LIST_START, RAM_BASE + LIST_OFF, "autoload list start"),
        (MP_LIST_END, RAM_BASE + IMAGE_LEN, "autoload list end"),
        (MP_AUTOLOAD_SRC, RAM_BASE + AUTOLOAD_SRC_OFF, "autoload source"),
        (MP_BSS_END, FONT_RAM, "static BSS end"),
        (ARENA_LO_LITERAL, FONT_RAM, "heap floor literal"),
    )
    for off, want, what in checks:
        got = struct.unpack_from("<I", jp, off)[0]
        if got != want:
            raise ValueError(f"not the original Japanese code binary: {what} at "
                             f"{off:#x} is {got:#010x}, expected {want:#010x}")
    want_list = struct.pack("<6I", *ITCM_ENTRY, *DTCM_ENTRY)
    if jp[LIST_OFF:LIST_OFF + 0x18] != want_list:
        raise ValueError("original 2-entry autoload list not found at 0x1B6DA0")
    hp_format_off = HP_FORMAT_CURRENT_JP_RAM - RAM_BASE
    if jp[hp_format_off:hp_format_off + len(HP_FORMATS)] != HP_FORMATS:
        raise ValueError("original current/max HP formats D4 and /D4 not found")
    for ptr_off, jp_target, _rel, what in HP_FORMAT_PTR_SITES:
        got = struct.unpack_from("<I", jp, ptr_off)[0]
        if got != jp_target:
            raise ValueError(f"not the original Japanese code binary: {what} at "
                             f"{ptr_off:#x} is {got:#010x}, expected {jp_target:#010x}")


def build_arm9(jp_arm9: bytes, data_dir: Path | str | None = None,
               verify: bool = True) -> bytes:
    """Assemble the translated code binary from the original Japanese one.

    Parameters
    ----------
    jp_arm9:
        The original image as the ROM container exposes it (0x1B6DB8 bytes);
        a copy with the 12-byte nitrocode trailer appended is also accepted.
    data_dir:
        Repository data directory (defaults to <repo>/data).
    verify:
        Check the result against data/manifest.json ("arm9") and raise on
        mismatch.  Leave enabled; disable only for experiments.
    """
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    jp = bytes(jp_arm9)
    if len(jp) == IMAGE_LEN + FOOTER_LEN and jp[IMAGE_LEN:IMAGE_LEN + 4] == NITROCODE:
        jp = jp[:IMAGE_LEN]
    _validate_input(jp)

    img = _Image(jp)

    # 1. translated tables / strings / patches over the head
    _apply_units(img, data_dir)
    _apply_weapons(img, data_dir)
    _apply_pilots(img, data_dir)
    _apply_id_commands(img, data_dir)
    _apply_abilities(img, data_dir)
    _apply_parts(img, data_dir)
    _apply_labels(img, data_dir)
    _apply_cutin_offsets(img, data_dir)
    _apply_resource_offsets(img, data_dir)
    _apply_dictionary(img, data_dir)
    _apply_arenas(img, data_dir)
    _apply_event_blocks(img, data_dir)
    _apply_patches(img, data_dir, "patches/code_patches.json")
    _apply_patches(img, data_dir, "patches/raw_regions.json")

    # 2. appended autoload banks + relocation plumbing
    font = (data_dir / "font" / "atlas12.bin").read_bytes()
    ui_ram, ui_bank = _bank_bytes(data_dir, "arenas/ui_names_bank.json")
    brief_ram, brief_bank = _bank_bytes(data_dir, "arenas/briefing_blobs.json")
    for name, blob in (("font", font), ("UI bank", ui_bank),
                       ("briefing bank", brief_bank)):
        if len(blob) % 4:
            raise ValueError(f"{name} length {len(blob):#x} is not word-aligned "
                             f"(the autoload copy loop moves words)")
    if ui_ram != FONT_RAM + len(font):
        raise ValueError("UI bank RAM base must sit flush after the atlas "
                         f"({FONT_RAM + len(font):#010x}), got {ui_ram:#010x}")

    entries = [ITCM_ENTRY, DTCM_ENTRY,
               (FONT_RAM, len(font), 0),
               (ui_ram, len(ui_bank), 0),
               (brief_ram, len(brief_bank), 0)]
    autoload_list = b"".join(struct.pack("<3I", *e) for e in entries)

    list_start = RAM_BASE + LIST_OFF + len(font) + len(ui_bank) + len(brief_bank)
    heap_floor = (ui_ram + len(ui_bank) + ARENA_ALIGN - 1) & ~(ARENA_ALIGN - 1)
    img.put_u32(MP_LIST_START, list_start, "autoload list start")
    img.put_u32(MP_LIST_END, list_start + len(autoload_list), "autoload list end")
    img.put_u32(FONT_PTR_LITERAL, FONT_RAM, "renderer atlas base")
    img.put_u32(ARENA_LO_LITERAL, heap_floor, "heap floor")
    for ptr_off, jp_target, rel, what in HP_FORMAT_PTR_SITES:
        img.put_u32(ptr_off, HP_FORMAT_RAM + rel, what, expect_old=jp_target)

    out = bytes(img.buf) + font + ui_bank + brief_bank + autoload_list

    # source contiguity invariant: autoload payload bytes must exactly fill
    # [source block .. list), or the boot copy loop would drift.
    payload_total = sum(size for _ram, size, _bss in entries)
    if payload_total != (list_start - RAM_BASE) - AUTOLOAD_SRC_OFF:
        raise AssertionError("autoload source block and list are not adjacent")

    if verify:
        want = json.loads((data_dir / "manifest.json").read_text())
        want_sha = want["components"]["arm9"]
        got_sha = hashlib.sha1(out).hexdigest()
        if got_sha != want_sha:
            raise RuntimeError(
                f"built code binary does not match the manifest: sha1 {got_sha} "
                f"!= {want_sha} (size {len(out)}). The data files and this "
                f"builder disagree - rebuild the data or fix the regression.")
    return out
