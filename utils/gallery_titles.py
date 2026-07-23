"""Deterministic rebuilders for the six mode0 gallery title resources.

The EV catalogue and the character/unit library catalogues store relative
offsets into companion string banks.  Chinese strings are wider than the JP
mode0 font, so all three pairs are repacked and every owned offset is rewritten
in one pass.  Source identities come from generated ``data/jp/gallery.json``;
translations contain only the 54 EV labels and 28 unique series labels.  The
513 roster names are joined at build time by ``char_id``/``utid`` from the
canonical character/unit mappings.  These strings run through the renderB
trampoline: translated glyphs must be high atlas slots (plus a tiny audited
direct-control set), and width uses the real 6/8/12px advance policy.
"""
from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from . import text_codec
from .extract import layout as L

DATA = Path(__file__).resolve().parent.parent / "data"
GALLERY_FILES = (
    L.EV_GALLERY_TITLE_FILE,
    L.EV_GALLERY_CATALOG_FILE,
    L.CHAR_GALLERY_METADATA_FILE,
    L.CHAR_GALLERY_STRING_FILE,
    L.UNIT_GALLERY_METADATA_FILE,
    L.UNIT_GALLERY_STRING_FILE,
)
TRAMPOLINE_SLOT_ADVANCE = {
    4156: 6, 4253: 6,              # narrow atlas ( )
    4222: 8, 4223: 8, 4214: 8,    # atlas S/E/D, matched to renderB pitch
}


def _u16(data: bytes, offset: int) -> int:
    if not 0 <= offset <= len(data) - 2:
        raise ValueError(f"u16 outside gallery resource: 0x{offset:X}")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if not 0 <= offset <= len(data) - 4:
        raise ValueError(f"u32 outside gallery resource: 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def _cstr(data: bytes, offset: int) -> tuple[bytes, int]:
    """Read through one standalone NUL without splitting a two-byte token."""
    if not 0 <= offset < len(data):
        raise ValueError(f"gallery string offset outside bank: 0x{offset:X}")
    end = offset
    while end < len(data):
        if data[end] >= 0xE0:
            if end + 1 >= len(data):
                raise ValueError(f"truncated gallery token at 0x{end:X}")
            end += 2
        elif data[end] == 0:
            return data[offset:end], end
        else:
            end += 1
    raise ValueError(f"unterminated gallery string at 0x{offset:X}")


def _parse_int(value: int | str) -> int:
    return value if isinstance(value, int) else int(value, 0)


@lru_cache(maxsize=1)
def _jp() -> dict:
    return json.loads((DATA / "jp" / "gallery.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _zh() -> dict:
    return json.loads((DATA / "zh" / "gallery.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _roster(kind: str) -> dict[int, dict]:
    if kind == "character":
        rows = json.loads((DATA / "zh" / "characters.json").read_text(encoding="utf-8"))[
            "characters"
        ]
        key = "cid"
    elif kind == "unit":
        rows = json.loads((DATA / "zh" / "units.json").read_text(encoding="utf-8"))["units"]
        key = "utid"
    else:
        raise ValueError(f"unknown gallery roster kind: {kind!r}")
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {key} in canonical roster translation")
    return result


MODE0_DIRECT = {
    " ": 0x01,
    "、": 0x7A, "。": 0x7B, "…": 0x7C,
    "（": 0x7D, "）": 0x7E, "?": 0xD8, "!": 0xD9,
}


def _one_byte_tokens(source_raw: bytes) -> frozenset[int]:
    """Return only standalone source bytes (never low bytes of 2-byte tokens)."""
    return frozenset(
        token for _, token, length in text_codec.iter_tokens(source_raw) if length == 1
    )


def _mode0_encode(
    text: str,
    label: str,
    *,
    allowed_one_bytes: frozenset[int],
) -> bytes:
    """Encode for the real mode0/renderB-trampoline glyph identities."""
    cm = text_codec.load_charmap()
    out = bytearray()
    for char in text:
        # Prefer the translated atlas only when it is a trampoline-safe high
        # slot.  This includes the dedicated S/E/D and narrow-paren cells.
        slot = cm.two_byte_zh.get(char)
        if slot is not None and slot >= text_codec.ZH_BAND_MIN:
            if cm._token_hazard(slot, allow_low15=True):
                raise ValueError(f"{label}: hazardous atlas slot {slot} for {char!r}")
            out.extend(text_codec.encode_slot(slot))
            continue
        if char in MODE0_DIRECT:
            code = MODE0_DIRECT[char]
            if code not in allowed_one_bytes:
                raise ValueError(
                    f"{label}: direct mode0 byte 0x{code:02X} for {char!r} "
                    "is absent from this record's JP source tokens"
                )
            out.append(code)
            continue
        raise ValueError(f"{label}: no safe high-slot/direct mode0 glyph for {char!r}")
    return bytes(out)


def _mode0_decode(payload: bytes) -> str:
    cm = text_codec.load_charmap()
    atlas = {slot: char for char, slot in cm.two_byte_zh.items()
             if slot >= text_codec.ZH_BAND_MIN}
    renderb = json.loads((DATA / "renderb_charset.json").read_text(encoding="utf-8"))["slots"]
    direct_rev = {value: char for char, value in MODE0_DIRECT.items()}
    out = []
    for _, token, length in text_codec.iter_tokens(payload):
        slot = (token if length == 1 else
                token - text_codec.GLYPH_TOKEN_BASE + text_codec.TWO_BYTE_SLOT_OFFSET)
        if slot >= text_codec.ZH_BAND_MIN:
            char = atlas.get(slot)
        elif length == 1 and slot in direct_rev:
            char = direct_rev[slot]
        else:
            char = renderb.get(str(slot), {}).get("char")
        if char is None:
            raise ValueError(f"mode0 readback has unidentified slot {slot}")
        out.append(char)
    return "".join(out)


def _encoded_display(
    text: str,
    *,
    budget_px: int,
    label: str,
    source_raw: bytes,
) -> bytes:
    if not text:
        raise ValueError(f"{label}: empty display text")
    # These resources use the mode0/renderB-trampoline reader.  Never use the
    # stage encoder here: its one-byte S/D values are different renderB glyphs
    # (the historical SEED -> げ/き corruption).  surface="bank" emits the
    # safe ZH-band atlas slots, including the dedicated S/E/D and parens.
    allowed_one_bytes = _one_byte_tokens(source_raw)
    payload = _mode0_encode(text, label, allowed_one_bytes=allowed_one_bytes)
    units = list(text_codec.iter_tokens(payload))
    if any(length == 1 and token == 0 for _, token, length in units):
        raise ValueError(f"{label}: display text contains a standalone NUL")
    if any(length == 2 and token >= text_codec.MACRO_TOKEN_BASE for _, token, length in units):
        raise ValueError(f"{label}: rebuilt text must not depend on a JP dictionary macro")
    if _mode0_decode(payload) != text:
        raise AssertionError(f"{label}: mode0 glyph-identity readback failed")
    width = 0
    for _, token, length in units:
        if length == 1:
            width += 8             # original renderB direct glyph
        else:
            slot = token - text_codec.GLYPH_TOKEN_BASE + text_codec.TWO_BYTE_SLOT_OFFSET
            width += (TRAMPOLINE_SLOT_ADVANCE.get(slot, 12)
                      if slot >= text_codec.ZH_BAND_MIN else 8)
    if width > budget_px:
        raise ValueError(f"{label}: rendered width {width}px exceeds {budget_px}px")
    return payload


def _assert_source_files(source: Mapping[str, bytes]) -> None:
    expected = _jp()["files"]
    if set(source) != set(GALLERY_FILES):
        raise ValueError(
            f"gallery source set changed: got={sorted(source)} want={sorted(GALLERY_FILES)}"
        )
    for name in GALLERY_FILES:
        blob = source[name]
        row = expected[name]
        digest = hashlib.sha256(blob).hexdigest()
        if len(blob) != row["size"] or digest != row["sha256"]:
            raise ValueError(
                f"{name}: JP source baseline changed "
                f"(size={len(blob)}, sha256={digest})"
            )


def _translation_maps() -> tuple[dict[int, dict], dict[str, dict]]:
    zh = _zh()
    ev_rows = zh.get("ev_titles", [])
    series_rows = zh.get("series", [])
    ev = {row["event_no"]: row for row in ev_rows}
    series = {row["series_id"]: row for row in series_rows}
    wanted_ev = set(range(1, L.EV_GALLERY_COUNT + 1))
    wanted_series = {row["series_id"] for row in _jp()["series"]}
    if len(ev) != len(ev_rows) or set(ev) != wanted_ev:
        raise ValueError("gallery translation coverage is not exactly EV01..EV54")
    if len(series) != len(series_rows) or set(series) != wanted_series:
        raise ValueError("gallery series coverage is not exactly the 28 extracted identities")
    return ev, series


def _build_ev(source_bank: bytes, source_catalog: bytes, translations: dict[int, dict]) -> tuple[bytes, bytes]:
    generated = _jp()["ev"]
    if source_bank[: len(L.EV_GALLERY_TITLE_PREFIX)] != L.EV_GALLERY_TITLE_PREFIX:
        raise ValueError("43f.bin prefix changed")
    want_catalog_size = len(L.EV_GALLERY_CATALOG_HEADER) + (
        L.EV_GALLERY_COUNT * L.EV_GALLERY_RECORD_SIZE
    )
    if len(source_catalog) != want_catalog_size or not source_catalog.startswith(
        L.EV_GALLERY_CATALOG_HEADER
    ):
        raise ValueError("440.bin header/record geometry changed")

    records = generated["records"]
    if len(records) != L.EV_GALLERY_COUNT:
        raise ValueError("generated EV identity count changed")
    out_bank = bytearray(L.EV_GALLERY_TITLE_PREFIX)
    out_catalog = bytearray(source_catalog)
    expected_source_offset = len(L.EV_GALLERY_TITLE_PREFIX)
    allowed_catalog_changes: set[int] = set()
    encoded_by_no: dict[int, bytes] = {}
    rebuilt_offsets: dict[int, int] = {}
    for index, identity in enumerate(records):
        event_no = index + 1
        rec_off = len(L.EV_GALLERY_CATALOG_HEADER) + index * L.EV_GALLERY_RECORD_SIZE
        if identity["event_no"] != event_no or _parse_int(identity["metadata_offset"]) != rec_off:
            raise ValueError(f"EV{event_no:02d}: generated record identity/order changed")
        bit, group, flag, reserved = source_catalog[rec_off:rec_off + 4]
        source_offset = _u32(source_catalog, rec_off + 4)
        source_raw, source_end = _cstr(source_bank, source_offset)
        expected_tuple = (
            identity["bit"], identity["group"], identity["catalog_flag"],
            identity["reserved"], _parse_int(identity["title_offset"]),
            bytes.fromhex(identity["title_raw_hex"]),
        )
        actual_tuple = (
            bit, group, flag, reserved, source_offset, source_raw + b"\x00",
        )
        if actual_tuple != expected_tuple or source_offset != expected_source_offset:
            raise ValueError(f"EV{event_no:02d}: JP catalogue/title identity changed")
        expected_source_offset = source_end + 1

        row = translations[event_no]
        display = row.get("display_zh") or row.get("zh", "")
        payload = _encoded_display(
            display, budget_px=L.EV_GALLERY_TITLE_BUDGET_PX,
            label=f"EV{event_no:02d}",
            source_raw=source_raw,
        )
        rebuilt_offset = len(out_bank)
        out_bank.extend(payload)
        out_bank.append(0)
        struct.pack_into("<I", out_catalog, rec_off + 4, rebuilt_offset)
        allowed_catalog_changes.update(range(rec_off + 4, rec_off + 8))
        encoded_by_no[event_no] = payload
        rebuilt_offsets[event_no] = rebuilt_offset

    source_tail = source_bank[expected_source_offset:]
    expected_tail = bytes.fromhex(generated.get("title_tail_hex", ""))
    if expected_tail != b"\x00" or source_tail != expected_tail:
        raise ValueError(
            f"43f.bin tail identity changed: got={source_tail.hex()} "
            f"expected={expected_tail.hex()}"
        )
    out_bank.extend(source_tail)
    for offset, (old, new) in enumerate(zip(source_catalog, out_catalog, strict=True)):
        if old != new and offset not in allowed_catalog_changes:
            raise AssertionError(f"440.bin immutable metadata changed at 0x{offset:X}")
    if bytes(out_catalog[: len(L.EV_GALLERY_CATALOG_HEADER)]) != L.EV_GALLERY_CATALOG_HEADER:
        raise AssertionError("440.bin header changed during rebuild")
    for event_no in range(1, L.EV_GALLERY_COUNT + 1):
        rec_off = len(L.EV_GALLERY_CATALOG_HEADER) + (
            event_no - 1
        ) * L.EV_GALLERY_RECORD_SIZE
        offset = _u32(out_catalog, rec_off + 4)
        payload, _ = _cstr(out_bank, offset)
        if offset != rebuilt_offsets[event_no] or payload != encoded_by_no[event_no]:
            raise AssertionError(f"EV{event_no:02d}: rebuilt offset/string readback failed")
    return bytes(out_bank), bytes(out_catalog)


def _library_spec(kind: str) -> tuple[str, str, int, str, int, int, int]:
    if kind == "character":
        return (
            L.CHAR_GALLERY_METADATA_FILE, L.CHAR_GALLERY_STRING_FILE,
            L.CHAR_GALLERY_COUNT, "char_id", L.CHAR_GALLERY_ROSTER_ID,
            L.CHAR_GALLERY_BIO_ID, L.CHAR_GALLERY_NAME_BUDGET_PX,
        )
    return (
        L.UNIT_GALLERY_METADATA_FILE, L.UNIT_GALLERY_STRING_FILE,
        L.UNIT_GALLERY_COUNT, "utid", L.UNIT_GALLERY_ROSTER_ID,
        L.UNIT_GALLERY_BIO_ID, L.UNIT_GALLERY_NAME_BUDGET_PX,
    )


def _build_library(
    kind: str,
    source_metadata: bytes,
    source_bank: bytes,
    series_translations: dict[str, dict],
) -> tuple[bytes, bytes]:
    metadata_name, bank_name, count, id_key, id_off, bio_off, name_budget = _library_spec(kind)
    generated = _jp()["characters" if kind == "character" else "units"]
    records = generated["records"]
    if len(source_metadata) != (count + 1) * L.GALLERY_METADATA_STRIDE:
        raise ValueError(f"{metadata_name}: metadata geometry changed")
    if len(records) != count or generated["record_count"] != count:
        raise ValueError(f"{kind} gallery generated coverage changed")

    roster = _roster(kind)
    name_text_by_offset: dict[int, bytes] = {}
    series_text_by_offset: dict[int, bytes] = {}
    source_name_raw_by_offset: dict[int, bytes] = {}
    source_series_raw_by_offset: dict[int, bytes] = {}
    record_payloads: list[tuple[int, int, bytes, bytes]] = []
    seen_ids: set[int] = set()
    for index, identity in enumerate(records):
        rec_off = (index + 1) * L.GALLERY_METADATA_STRIDE
        record = source_metadata[rec_off:rec_off + L.GALLERY_METADATA_STRIDE]
        name_off, series_off = _u32(record, 0), _u32(record, 4)
        roster_id, bio_id = _u16(record, id_off), _u16(record, bio_off)
        name_raw, _ = _cstr(source_bank, name_off)
        series_raw, _ = _cstr(source_bank, series_off)
        actual = (
            identity["record_index"], identity["metadata_index"],
            _parse_int(identity["metadata_offset"]), record,
            roster_id, bio_id, name_off, name_raw + b"\x00",
            series_off, series_raw + b"\x00",
        )
        expected = (
            index, index + 1, rec_off, bytes.fromhex(identity["record_raw_hex"]),
            identity[id_key], identity["bio_id"],
            _parse_int(identity["name_offset"]), bytes.fromhex(identity["name_raw_hex"]),
            _parse_int(identity["series_offset"]), bytes.fromhex(identity["series_raw_hex"]),
        )
        if actual != expected:
            raise ValueError(f"{metadata_name} record {index}: JP identity changed")
        if roster_id in seen_ids:
            raise ValueError(f"{metadata_name}: duplicate runtime {id_key} {roster_id}")
        seen_ids.add(roster_id)
        if roster_id not in roster or not roster[roster_id].get("zh"):
            raise ValueError(f"{metadata_name}: no canonical name for {id_key} {roster_id}")
        name_text = roster[roster_id].get("gallery_zh") or roster[roster_id]["zh"]
        name_payload = _encoded_display(
            name_text, budget_px=name_budget,
            label=f"{kind} gallery {id_key} {roster_id} name",
            source_raw=name_raw,
        )
        series_id = identity["series_id"]
        series_row = series_translations.get(series_id)
        if series_row is None:
            raise ValueError(f"{metadata_name}: no series translation for {series_id}")
        series_payload = _encoded_display(
            series_row.get("display_zh") or series_row.get("zh", ""),
            budget_px=L.LIBRARY_GALLERY_SERIES_BUDGET_PX,
            label=f"{kind} gallery {series_id}",
            source_raw=series_raw,
        )
        old = name_text_by_offset.setdefault(name_off, name_payload)
        if old != name_payload:
            raise ValueError(f"{metadata_name}: shared name offset has divergent text")
        old = series_text_by_offset.setdefault(series_off, series_payload)
        if old != series_payload:
            raise ValueError(f"{metadata_name}: shared series offset has divergent text")
        source_name_raw_by_offset.setdefault(name_off, name_raw)
        source_series_raw_by_offset.setdefault(series_off, series_raw)
        record_payloads.append((name_off, series_off, name_payload, series_payload))

    source_name_start = min(name_text_by_offset)
    source_series_start = min(series_text_by_offset)
    if source_name_start != _parse_int(generated["source_name_start"]):
        raise ValueError(f"{bank_name}: source name-region start changed")
    if source_series_start != _parse_int(generated["source_series_start"]):
        raise ValueError(f"{bank_name}: source series-region start changed")
    marker_delta = _u32(source_metadata, 4) - source_series_start
    if marker_delta != generated["header_series_marker_delta"] or marker_delta not in (0, 1):
        raise ValueError(f"{metadata_name}: header series marker changed")
    if max(name_text_by_offset) >= source_series_start:
        raise ValueError(f"{bank_name}: source name/series regions overlap")

    def require_contiguous_region(rows: dict[int, bytes], start: int, stop: int, label: str):
        cursor = start
        for offset in sorted(rows):
            if offset != cursor:
                raise ValueError(
                    f"{bank_name}: {label} region gap/overlap at 0x{cursor:X}/0x{offset:X}"
                )
            raw, end = _cstr(source_bank, offset)
            if raw != rows[offset]:
                raise ValueError(f"{bank_name}: {label} source readback changed")
            cursor = end + 1
        if cursor != stop:
            raise ValueError(
                f"{bank_name}: {label} coverage ends 0x{cursor:X}, expected 0x{stop:X}"
            )

    require_contiguous_region(
        source_name_raw_by_offset, source_name_start, source_series_start, "name"
    )
    last_series_end = max(_cstr(source_bank, offset)[1] + 1
                          for offset in source_series_raw_by_offset)
    require_contiguous_region(
        source_series_raw_by_offset, source_series_start, last_series_end, "series"
    )
    if source_bank[last_series_end:] != b"\x00\x00":
        raise ValueError(
            f"{bank_name}: expected exactly two trailing zero pad bytes, got "
            f"{source_bank[last_series_end:].hex()}"
        )

    out_bank = bytearray(source_bank[:source_name_start])
    name_offsets: dict[int, int] = {}
    for source_off in sorted(name_text_by_offset):
        name_offsets[source_off] = len(out_bank)
        out_bank.extend(name_text_by_offset[source_off])
        out_bank.append(0)
    rebuilt_series_start = len(out_bank)
    series_offsets: dict[int, int] = {}
    for source_off in sorted(series_text_by_offset):
        series_offsets[source_off] = len(out_bank)
        out_bank.extend(series_text_by_offset[source_off])
        out_bank.append(0)
    out_bank.extend(b"\x00\x00")

    out_metadata = bytearray(source_metadata)
    struct.pack_into("<I", out_metadata, 4, rebuilt_series_start + marker_delta)
    allowed_metadata_changes = set(range(4, 8))
    for index, (source_name_off, source_series_off, name_payload, series_payload) in enumerate(
        record_payloads
    ):
        rec_off = (index + 1) * L.GALLERY_METADATA_STRIDE
        name_off = name_offsets[source_name_off]
        series_off = series_offsets[source_series_off]
        struct.pack_into("<II", out_metadata, rec_off, name_off, series_off)
        allowed_metadata_changes.update(range(rec_off, rec_off + 8))
        if _cstr(out_bank, name_off)[0] != name_payload:
            raise AssertionError(f"{metadata_name} record {index}: name readback failed")
        if _cstr(out_bank, series_off)[0] != series_payload:
            raise AssertionError(f"{metadata_name} record {index}: series readback failed")
        if (_u32(out_metadata, rec_off), _u32(out_metadata, rec_off + 4)) != (
            name_off, series_off
        ):
            raise AssertionError(f"{metadata_name} record {index}: offset readback failed")
    for offset, (old, new) in enumerate(zip(source_metadata, out_metadata, strict=True)):
        if old != new and offset not in allowed_metadata_changes:
            raise AssertionError(f"{metadata_name}: immutable metadata changed at 0x{offset:X}")
    return bytes(out_metadata), bytes(out_bank)


def build_gallery_files(source: Mapping[str, bytes]) -> dict[str, bytes]:
    """Rebuild all six coupled resources, with full source and readback gates."""
    _assert_source_files(source)
    ev_translations, series_translations = _translation_maps()
    ev_bank, ev_catalog = _build_ev(
        source[L.EV_GALLERY_TITLE_FILE],
        source[L.EV_GALLERY_CATALOG_FILE],
        ev_translations,
    )
    char_meta, char_bank = _build_library(
        "character",
        source[L.CHAR_GALLERY_METADATA_FILE],
        source[L.CHAR_GALLERY_STRING_FILE],
        series_translations,
    )
    unit_meta, unit_bank = _build_library(
        "unit",
        source[L.UNIT_GALLERY_METADATA_FILE],
        source[L.UNIT_GALLERY_STRING_FILE],
        series_translations,
    )
    return {
        L.EV_GALLERY_TITLE_FILE: ev_bank,
        L.EV_GALLERY_CATALOG_FILE: ev_catalog,
        L.CHAR_GALLERY_METADATA_FILE: char_meta,
        L.CHAR_GALLERY_STRING_FILE: char_bank,
        L.UNIT_GALLERY_METADATA_FILE: unit_meta,
        L.UNIT_GALLERY_STRING_FILE: unit_bank,
    }


def validate_gallery_data() -> dict[str, int]:
    """Translation-only/static validation that does not require a ROM file."""
    ev, series = _translation_maps()
    for event_no, row in ev.items():
        identity = _jp()["ev"]["records"][event_no - 1]
        _encoded_display(
            row.get("display_zh") or row.get("zh", ""),
            budget_px=L.EV_GALLERY_TITLE_BUDGET_PX,
            label=f"EV{event_no:02d}",
            source_raw=bytes.fromhex(identity["title_raw_hex"]),
        )
    for kind, section, key in (
        ("character", "characters", "char_id"),
        ("unit", "units", "utid"),
    ):
        roster = _roster(kind)
        for identity in _jp()[section]["records"]:
            row = roster.get(identity[key])
            if not row or not row.get("zh"):
                raise ValueError(f"{kind} gallery lacks runtime name {identity[key]}")
            _encoded_display(
                row.get("gallery_zh") or row["zh"],
                budget_px=(
                    L.CHAR_GALLERY_NAME_BUDGET_PX
                    if kind == "character"
                    else L.UNIT_GALLERY_NAME_BUDGET_PX
                ),
                label=f"{kind} gallery {identity[key]} name",
                source_raw=bytes.fromhex(identity["name_raw_hex"]),
            )
            series_id = identity["series_id"]
            series_row = series[series_id]
            _encoded_display(
                series_row.get("display_zh") or series_row.get("zh", ""),
                budget_px=L.LIBRARY_GALLERY_SERIES_BUDGET_PX,
                label=f"{kind} gallery {series_id}",
                source_raw=bytes.fromhex(identity["series_raw_hex"]),
            )
    return {"ev_titles": len(ev), "series": len(series), "records": 274 + 239}
