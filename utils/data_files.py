"""Builders for the miscellaneous translated NitroFS data files.

Twenty flat data files outside the stage-dialogue (_STG*) system carry
translated content: battle-voice barks, battle cut-in quotes, ID command /
ability effect labels, special ability & defense descriptions, encyclopedia
biographies, weapon and part names, and a handful of raw-tile UI graphics.
Their translation data lives under ``data/zh/files/`` in four JSON layouts:

  * ``edits``         — in-place rewrites of a fixed-layout text bank: each edit
                        re-encodes ``zh`` (utils.text_codec) at ``offset`` and
                        0x00-pads it to the original run's ``size``. An optional
                        ``append`` record adds a relocated record copy at the end
                        of the file (the record-offset table inside the code
                        binary points at it).
  * ``cutin_groups``  — full rebuild of the battle cut-in quote bank: the file is
                        the concatenation of all records, each ``header`` +
                        encoded ``zh`` + terminator ``00 03 00 01`` + zero padding
                        to a 4-byte boundary. Records may grow; the matching
                        record-offset table lives in the code binary image.
  * ``table``         — full rebuild of a fixed-total-size name table: entries
                        written at explicit offsets, 0x00-padded to their slots.
  * ``graphics``      — raw-tile bitmap repaints (not text): each region carries
                        the original bytes (``jp_hex``, asserted before writing)
                        and the replacement bytes (``zh_hex``).

Text fields use the game text codec (utils.text_codec): plain characters plus
byte-faithful escapes — ``{00}`` separators/padding, ``{03}``/``{04}`` control
bytes, ``{F0:n}`` dictionary macros, ``{SLOT:n}`` glyph slots without a charmap
character. Every ``zh`` field re-encodes to the exact target bytes; a record
that cannot round-trip through the codec would carry ``zh_hex`` instead
(none currently do).

Public API:
    build_data_file(name, jp_bytes) -> bytes
        Rebuild data file ``name`` (e.g. "1dc.bin") from its Japanese original,
        self-checked against data/manifest.json.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from . import text_codec

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FILES_DIR = DATA_DIR / "zh" / "files"

#: data file name -> JSON table (relative to data/files/) that rebuilds it.
DATA_FILE_TABLES = {
    "0.bin": "barks/0.json",
    "1.bin": "barks/1.json",
    "1dd.bin": "barks/1dd.json",
    "1de.bin": "barks/1de.json",
    "c4f.bin": "barks/c4f.json",
    "1da.bin": "battle/ability_cards.json",
    "1db.bin": "battle/command_effects.json",
    "1dc.bin": "battle/cutin_quotes.json",
    "1df.bin": "battle/special_abilities.json",
    "1e0.bin": "battle/special_defenses.json",
    "31e.bin": "library/weapon_names.json",
    "324.bin": "library/character_bios.json",
    "c4b.bin": "library/unit_bios.json",
    "b6e.bin": "hangar/part_names.json",
    "b6f.bin": "hangar/part_captions.json",
    "388.bin": "graphics/388.json",
    "3d3.bin": "graphics/3d3.json",
    "3d5.bin": "graphics/3d5.json",
    "478.bin": "graphics/478.json",
    "48a.bin": "graphics/48a.json",
}

CUTIN_TERMINATOR = b"\x00\x03\x00\x01"


@lru_cache(maxsize=None)
def _load_table(relpath: str) -> dict:
    return json.loads((FILES_DIR / relpath).read_text(encoding="utf-8"))


# Trampoline (renderB-dispatch) text banks: the in-battle effect/command card
# readers render one-byte codes and JP-band slots from the 8x16 JP UI font,
# NOT the CJK atlas (atlas slot 206 = 兵, renderB 206 = 無).  Their zh strings
# are byte-preserving NOTATION (e.g. '見'/'.' stand for renderB-meaning
# bytes), so re-encoding them through the charmap is ambiguous by design.
# These records therefore carry their proven bytes as zh_hex (verbatim build
# input; zh is documentation) — enforced here.  Edits go through validating
# appliers that obey the renderB rules (see AGENTS.md).
BANK_SURFACE_FILES = {"1da.bin", "1db.bin", "1df.bin", "1e0.bin"}


def _record_bytes(rec: dict, surface: str = "stage") -> bytes:
    """Encoded payload of one JSON record: zh text via the codec, or zh_hex verbatim."""
    if "zh_hex" in rec:
        return bytes.fromhex(rec["zh_hex"])
    # flat text banks tolerate 0x15-low glyph tokens (no stage-script framing)
    return text_codec.encode(rec["zh"], allow_low15=True, surface=surface)


def _write_padded(buf: bytearray, offset: int, payload: bytes, size: int, where: str):
    if len(payload) > size:
        raise ValueError(f"{where}: encoded payload {len(payload)} B exceeds its {size}-byte slot")
    buf[offset:offset + size] = payload + b"\x00" * (size - len(payload))


def _build_edits(table: dict, jp: bytes) -> bytes:
    """In-place text-bank rewrite (+ optional appended relocated record)."""
    buf = bytearray(jp)
    is_bank = table["file"] in BANK_SURFACE_FILES
    for rec in table["edits"]:
        off = int(rec["offset"], 16)
        if is_bank and "zh_hex" not in rec:
            raise ValueError(
                f"{table['file']} edit @{rec['offset']}: trampoline-surface record "
                "must carry zh_hex (byte-preserving; see BANK_SURFACE_FILES note)")
        _write_padded(buf, off, _record_bytes(rec), rec["size"],
                      f"{table['file']} edit @{rec['offset']}")
    append = table.get("append")
    if append is not None:
        _write_padded_tail = _record_bytes(append)
        size = append["size"]
        if len(_write_padded_tail) > size:
            raise ValueError(f"{table['file']} append: payload exceeds {size} B")
        buf += _write_padded_tail + b"\x00" * (size - len(_write_padded_tail))
    return bytes(buf)


def _build_cutin_groups(table: dict, jp: bytes) -> bytes:
    """Battle cut-in quote bank: concatenation of grown, 4-aligned records."""
    out = bytearray()
    for rec in table["groups"]:
        out += bytes.fromhex(rec["header"])
        out += _record_bytes(rec)
        out += CUTIN_TERMINATOR
        out += b"\x00" * (-len(out) % 4)
    return bytes(out)


def _build_table(table: dict, jp: bytes) -> bytes:
    """Fixed-total-size name table rebuilt from scratch at explicit offsets."""
    buf = bytearray(table["total_size"])
    for rec in table["entries"]:
        _write_padded(buf, int(rec["offset"], 16), _record_bytes(rec), rec["size"],
                      f"{table['file']} entry {rec['index']}")
    return bytes(buf)


def bio_record_lengths(table: dict, jp_len_only: bool = True) -> list[int]:
    """Emitted length of every bio-bank record, in index order (4-aligned).

    Shared between the file builder below and utils.arm9_layout's offset-table
    applier so the arm9 tables can never desynchronize from the bank bytes
    (the cut-in lesson).  zh records: encoded length rounded up to 4;
    passthrough records: the original JP slice length (already 4-aligned)."""
    out = []
    for rec in table["records"]:
        if "zh" in rec or "zh_hex" in rec:
            n = len(_record_bytes(rec))
            out.append(n + (-n % 4))
        else:
            out.append(int(rec["jp_len"]))
    return out


def _build_bio_bank(table: dict, jp: bytes) -> bytes:
    """Encyclopedia bio bank: full rebuild, records concatenated in index
    order.  Translated records are re-encoded prose (grown freely — the arm9
    offset table is derived from this same JSON at build time); untranslated
    records are carved verbatim from the Japanese original at jp_off/jp_len."""
    out = bytearray()
    lens = bio_record_lengths(table)
    for rec, want in zip(table["records"], lens):
        if "zh" in rec or "zh_hex" in rec:
            payload = _record_bytes(rec)
            out += payload + b"\x00" * (want - len(payload))
        else:
            off = int(rec["jp_off"], 16)
            out += jp[off:off + int(rec["jp_len"])]
    return bytes(out)


def _build_graphics(table: dict, jp: bytes) -> bytes:
    """Raw-tile repaint: replace annotated regions, asserting the original bytes."""
    buf = bytearray(jp)
    for reg in table["regions"]:
        off = int(reg["offset"], 16)
        old = bytes.fromhex(reg["jp_hex"])
        new = bytes.fromhex(reg["zh_hex"])
        if len(old) != reg["size"] or len(new) != reg["size"]:
            raise ValueError(f"{table['file']} region @{reg['offset']}: size mismatch")
        if buf[off:off + len(old)] != old:
            raise ValueError(
                f"{table['file']} region @{reg['offset']}: source bytes differ from the "
                "expected Japanese original — wrong input file?")
        buf[off:off + len(new)] = new
    return bytes(buf)


_BUILDERS = {
    "edits": _build_edits,
    "cutin_groups": _build_cutin_groups,
    "table": _build_table,
    "graphics": _build_graphics,
    "bio_bank": _build_bio_bank,
}


@lru_cache(maxsize=None)
def _expected_sha1(name: str) -> str | None:
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    return manifest.get("components", {}).get(name)


def build_data_file(name: str, jp_bytes: bytes, verify: bool = True) -> bytes:
    """Rebuild translated data file ``name`` from its Japanese original bytes.

    Deterministic and self-checking: the result is asserted against the
    component sha1 recorded in data/manifest.json (raises on any mismatch,
    unless ``verify`` is False — the development mode used while editing
    translations, mirroring build.py --skip-verify)."""
    relpath = DATA_FILE_TABLES.get(name)
    if relpath is None:
        raise KeyError(f"no data-file table registered for {name!r}")
    table = _load_table(relpath)
    if table["file"] != name:
        raise ValueError(f"{relpath} rebuilds {table['file']!r}, not {name!r}")
    out = _BUILDERS[table["format"]](table, jp_bytes)
    if verify:
        want = _expected_sha1(name)
        got = hashlib.sha1(out).hexdigest()
        if want is not None and got != want:
            raise AssertionError(
                f"{name}: rebuilt component sha1 {got} != manifest {want} — the data under "
                f"data/zh/files/{relpath} (or data/charmap.json) no longer reproduces the "
                "expected component")
    return out
