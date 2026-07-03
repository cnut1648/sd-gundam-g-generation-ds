"""Stage-script files (`_STG*.bin`): parse, translate-patch, and rebuild.

Format (reverse-engineered, verified against the shipped game):

  * Every stage file is loaded WHOLE into a fixed RAM buffer at
    ``STAGE_RAM_BASE`` (0x0232C800). All pointers inside the file are
    ABSOLUTE RAM addresses relative to that base — there is no relocation
    table; the engine reads them raw.
  * Header (file offset 0): ``u32[0]`` = record count; the u32 slots at
    0x04, 0x08, 0x10, 0x14, 0x18 point to fixed-stride record TABLES whose
    entry fields the engine reads with 32-bit ``ldr``. On ARMv5 an unaligned
    ``ldr`` silently ROTATES the loaded word instead of faulting, so these
    five tables MUST stay 4-byte aligned (they are in the original game;
    breaking this black-screens the game). Slot 0x0C points to the
    event/dialogue section, which is byte-accessed and alignment-exempt.
  * Dialogue text lives in ``0x15 <payload> 00 00`` blocks embedded in the
    event bytecode. Text grammar: any byte >= 0xE0 opens a 2-byte token
    (0xE0xx = glyph, 0xF0xx = dictionary macro) whose LOW byte may be any
    value including 0x00 and 0x15 — all scanning must be token-aware
    (utils/text_codec.py is the reference walker). A standalone 0x00 is a
    segment separator; the first standalone 00 00 pair terminates the block.
  * The translation replaces block payloads with re-encoded Chinese text.
    Many payloads GROW, which shifts every later byte of the file; every
    absolute pointer whose target lies at/after an insertion point must be
    bumped by the accumulated growth, and the header tables re-padded to
    4-byte alignment where growth broke it.

Data model (data/dialogue/stages/<name>.json, one per stage file):

  ``edits``   — byte-range replacements in ORIGINAL-file coordinates:
                {jp_offset, jp_len, zh_hex} (+ human-readable jp/zh text).
                ``zh_hex`` is the canonical build input; the text fields are
                for translators and never enter the build.
  ``inserts`` — pure byte insertions (currently only alignment padding in
                front of a header table that growth had misaligned):
                {jp_offset, hex, reason}.

`build_stage_file` applies all edits/inserts in one pass and relocates every
genuine pointer. The pointer classifier below is the subtle part; its rules
are documented on `pointer_offsets`.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from . import text_codec

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STAGES_DIR = DATA_DIR / "dialogue" / "stages"

STAGE_RAM_BASE = 0x0232C800     # fixed RAM buffer every stage file loads to
STAGE_BUFFER_SIZE = 0x13800     # RAM buffer size (79,872 B); files must fit
BLOCK_OPEN = 0x15               # dialogue-block opener byte
ALIGNED_TABLE_SLOTS = (0x04, 0x08, 0x10, 0x14, 0x18)   # 4-byte-aligned tables
EVENT_SECTION_SLOT = 0x0C       # byte-accessed event/dialogue section (exempt)


@dataclass(frozen=True)
class TextBlock:
    """One ``0x15 <payload> 00 00`` dialogue block."""
    offset: int          # file offset of the 0x15 opener
    payload_start: int   # offset + 1
    terminator: int      # offset of the first standalone 00 00
    end: int             # terminator + 2 (one past the block)

    @property
    def payload_slice(self) -> slice:
        return slice(self.payload_start, self.terminator)


def parse_blocks(data: bytes, start: int = 0) -> list[TextBlock]:
    """Sequential token-aware scan for ``0x15 .. 00 00`` blocks.

    NOTE: the scan is purely lexical. A 0x15 byte inside event bytecode (e.g.
    an address operand) also opens a "block" here — callers that need only
    real dialogue must filter (the shipped per-stage data already identifies
    which ranges are dialogue). For build purposes the lexical scan is the
    right one: it reproduces exactly how payload bytes must be masked when
    classifying pointers."""
    blocks: list[TextBlock] = []
    i = start
    n = len(data)
    while i < n - 1:
        if data[i] == BLOCK_OPEN:
            term = text_codec.find_terminator(data, i + 1)
            if term < 0:
                i += 1
                continue
            blocks.append(TextBlock(i, i + 1, term, term + 2))
            i = term + 2
        else:
            i += 1
    return blocks


def payload_mask(data: bytes) -> bytearray:
    """1 for every byte inside a (lexical) dialogue-block payload, else 0.

    Used by `pointer_offsets` to reject pointer look-alikes inside glyph
    text: a 4-byte window of text bytes can coincidentally form a value in
    the load region without being a pointer."""
    mask = bytearray(len(data))
    for blk in parse_blocks(data):
        for k in range(blk.payload_start, min(blk.terminator, len(data))):
            mask[k] = 1
    return mask


def pointer_offsets(data: bytes,
                    payload: bytearray | None = None,
                    exclude: bytearray | None = None) -> list[int]:
    """Offsets of every GENUINE absolute-pointer window to relocate on growth.

    A candidate window is a 4-byte little-endian value inside
    [STAGE_RAM_BASE, STAGE_RAM_BASE + len(data)) whose PRECEDING byte is
    < 0xE0 (a genuine pointer is never preceded by a 2-byte-token high byte;
    in-range windows behind >= 0xE0 are coincidental token-tail overlaps).

    Two further rules drop coincidental matches:

      * ``exclude`` mask — windows touching a byte range that an edit
        replaces are skipped (their bytes are going away; the replacement
        carries its own already-correct values).
      * priority + ``payload`` mask — candidates are classed by preceding
        byte: 0x13/0x16 (script-opcode operand, priority 0) > 0x02
        (record-array element, priority 1) > anything else (priority 2).
        A priority-2 window inside a dialogue payload is glyph text that
        merely looks like a pointer and is dropped; genuine priority-2
        pointers live only in the header/setup area, outside every payload.

    Windows are then chosen NON-OVERLAPPING in priority order so a
    coincidental sub-window can never shadow a real pointer in dense
    opcode/array runs. Returns sorted file offsets."""
    end = STAGE_RAM_BASE + len(data)
    candidates: list[tuple[int, int]] = []
    for o in range(1, len(data) - 3):
        value = struct.unpack_from("<I", data, o)[0]
        if not (STAGE_RAM_BASE <= value < end and data[o - 1] < 0xE0):
            continue
        if exclude is not None and (exclude[o] or exclude[o + 1]
                                    or exclude[o + 2] or exclude[o + 3]):
            continue
        prev = data[o - 1]
        pri = 0 if prev in (0x13, 0x16) else (1 if prev == 0x02 else 2)
        if pri == 2 and payload is not None and (payload[o] or payload[o + 3]):
            continue
        candidates.append((pri, o))
    candidates.sort()
    occupied = bytearray(len(data))
    taken: list[int] = []
    for _pri, o in candidates:
        if occupied[o] or occupied[o + 1] or occupied[o + 2] or occupied[o + 3]:
            continue
        taken.append(o)
        occupied[o] = occupied[o + 1] = occupied[o + 2] = occupied[o + 3] = 1
    return sorted(taken)


def apply_edits(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Splice byte-range replacements and relocate every genuine pointer.

    ``edits`` = ascending, non-overlapping ``(offset, old_len, new_bytes)``
    in ORIGINAL-file coordinates (``old_len == 0`` inserts). Each length
    delta shifts all later bytes; a pointer at original offset P targeting
    original offset T is rewritten at its shifted position to the shifted
    target — where ``shift(x)`` accumulates the deltas of all edits whose
    replaced range ends at/before x."""
    prev_end = 0
    for off, old_len, _new in edits:
        if off < prev_end:
            raise ValueError(f"overlapping/unsorted edit at 0x{off:x}")
        if off + old_len > len(data):
            raise ValueError(f"edit at 0x{off:x}+{old_len} beyond file end")
        prev_end = off + old_len

    exclude = bytearray(len(data))
    for off, old_len, _new in edits:
        for k in range(off, off + old_len):
            exclude[k] = 1
    pointers = [(o, struct.unpack_from("<I", data, o)[0])
                for o in pointer_offsets(data, payload_mask(data), exclude)]

    deltas = [(off + old_len, len(new) - old_len) for off, old_len, new in edits]

    def shift(pos: int) -> int:
        return sum(d for e, d in deltas if e <= pos)

    out = bytearray()
    pos = 0
    for off, old_len, new in edits:
        out += data[pos:off]
        out += new
        pos = off + old_len
    out += data[pos:]

    for p, value in pointers:
        target = value - STAGE_RAM_BASE
        new_value = STAGE_RAM_BASE + target + shift(target)
        if not (STAGE_RAM_BASE <= new_value < STAGE_RAM_BASE + len(out)):
            raise AssertionError(
                f"pointer at 0x{p:x} relocates out of file: 0x{new_value:08x}")
        struct.pack_into("<I", out, p + shift(p), new_value)
    return bytes(out)


def check_table_alignment(data: bytes) -> None:
    """Assert every alignment-sensitive header table is 4-byte aligned.

    The engine reads these tables' u32 entry fields with ``ldr``; ARMv5
    rotates misaligned loads, so a misaligned table means wild pointers and
    a black screen. The original game keeps all five aligned — a built file
    must too (growth padding restores it where needed)."""
    if len(data) < 0x1C:
        return
    for slot in ALIGNED_TABLE_SLOTS:
        value = struct.unpack_from("<I", data, slot)[0]
        if STAGE_RAM_BASE <= value < STAGE_RAM_BASE + len(data):
            if value & 3:
                raise AssertionError(
                    f"header table [0x{slot:02x}] = 0x{value:08x} not 4-byte aligned")


def build_stage_file(jp_bytes: bytes, stage_data: dict) -> bytes:
    """Rebuild one translated stage file from its original bytes + edit data.

    ``stage_data`` is one parsed data/dialogue/stages/*.json. Returns the
    built file; asserts size, header-table alignment and pointer sanity."""
    if ("source_size" in stage_data
            and len(jp_bytes) != stage_data["source_size"]):
        raise AssertionError(
            f"{stage_data.get('file')}: source file is {len(jp_bytes)} B, "
            f"expected {stage_data['source_size']} B — wrong input ROM?")

    edits: list[tuple[int, int, bytes]] = []
    for e in stage_data.get("edits", []):
        edits.append((int(e["jp_offset"], 16), e["jp_len"],
                      bytes.fromhex(e["zh_hex"])))
    for ins in stage_data.get("inserts", []):
        edits.append((int(ins["jp_offset"], 16), 0, bytes.fromhex(ins["hex"])))
    edits.sort()

    built = apply_edits(jp_bytes, edits)

    expected_size = stage_data.get("built_size")
    if expected_size is not None and len(built) != expected_size:
        raise AssertionError(
            f"{stage_data.get('file')}: built {len(built)} B, "
            f"expected {expected_size} B")
    if len(built) > STAGE_BUFFER_SIZE:
        raise AssertionError(
            f"{stage_data.get('file')}: {len(built)} B exceeds the "
            f"0x{STAGE_BUFFER_SIZE:x} B stage RAM buffer")
    check_table_alignment(built)
    return built


def load_stage_data(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_stage_data(stages_dir: str | Path = STAGES_DIR):
    """Yield (file_name, stage_data) for every shipped per-stage JSON."""
    for path in sorted(Path(stages_dir).glob("*.json")):
        data = load_stage_data(path)
        yield data["file"], data
