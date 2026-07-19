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

Data model (data/zh/stages/<name>.json, one per stage file):

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

Layout independence (the BUG-1 lesson — a mixed edit's baked pointer):
some edited ranges are not pure dialogue: they carry live event bytecode with
absolute ``13``/``16`` operands (fork/call targets). Replacement bytes MUST
carry those operands as the JP ORIGINAL bytes; `apply_edits` relocates them
exactly like pointers outside edits. A replacement that instead bakes an
absolute in-buffer value froze the file layout of its authoring day into the
data — the first later size change anywhere before the target silently made
the operand land mid-instruction (the `_STG20A` replay-branch soft-lock: two
fork operands baked at the pre-grow shift entered a wait-handler 23 bytes
early and parked the event VM forever). `apply_edits` therefore REFUSES any
in-edit genuine-pointer window whose bytes are neither the JP originals nor
out-of-buffer (an intentional bytecode rewrite is expressed by dropping the
operand entirely, never by re-baking it).
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from . import text_codec

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STAGES_DIR = DATA_DIR / "zh" / "stages"

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


def edit_window_sources(p: int, edits: list[tuple[int, int, bytes]]):
    """Byte provenance of the 4-byte window at original offset ``p`` against
    ``edits``: a list of 4 sources, each ``("jp", q)`` (original byte at q,
    outside every edit) or ``("edit", k, r)`` (replacement byte r of edit k).
    Returns None when no byte of the window lies inside a replaced range."""
    srcs, touch = [], False
    for i in range(4):
        q = p + i
        hit = None
        for k, (off, old_len, _new) in enumerate(edits):
            if old_len > 0 and off <= q < off + old_len:
                hit = ("edit", k, q - off)
                break
        if hit is None:
            srcs.append(("jp", q))
        else:
            srcs.append(hit)
            touch = True
    return srcs if touch else None


def _anchor_displacement(data: bytes, out: bytearray, edits, shift, target: int,
                         masked: list[int]) -> int:
    """A relocated operand's target lies INSIDE an edited range: the target
    instruction's displacement within the replacement cannot be derived from
    lengths alone (the rewrite may shuffle bytes around it). Anchor-match the
    JP target signature against the built bytes near the length-preserving
    position, masking every selected pointer window (their operand bytes are
    legitimately rewritten). The match must be UNIQUE; anything else is an
    unsupported bytecode restructure and fails the build."""
    for off, old_len, new in edits:
        if old_len > 0 and off <= target < off + old_len:
            break
    else:
        raise AssertionError(f"anchor target 0x{target:x} not inside any edit")
    sig_len = 12
    sig = data[target:target + sig_len]
    mask_rel = set()
    for m in masked:
        for q in range(m, m + 4):
            if target <= q < target + sig_len:
                mask_rel.add(q - target)
    base_built = off + shift(off) + (target - off)
    hits = []
    for d in range(-4, 5):
        pos = base_built + d
        if pos < 0 or pos + sig_len > len(out):
            continue
        if all(i in mask_rel or out[pos + i] == sig[i] for i in range(sig_len)):
            hits.append(d)
    if len(hits) != 1:
        raise AssertionError(
            f"operand target 0x{target:x} inside a rewritten edit: anchor match "
            f"{'ambiguous ' + str(hits) if hits else 'not found'} — restructure "
            f"the edit so the target instruction keeps a unique 12-byte context")
    return hits[0]


def apply_edits(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Splice byte-range replacements and relocate every genuine pointer.

    ``edits`` = ascending, non-overlapping ``(offset, old_len, new_bytes)``
    in ORIGINAL-file coordinates (``old_len == 0`` inserts). Each length
    delta shifts all later bytes; a pointer at original offset P targeting
    original offset T is rewritten at its shifted position to the shifted
    target — where ``shift(x)`` accumulates the deltas of all edits whose
    replaced range ends at/before x.

    Pointer windows are relocated in two passes:

      1. windows fully OUTSIDE every replaced range — read from the original
         bytes, rewritten in place at their shifted position (as always);
      2. windows touching a replaced range (live bytecode operands inside a
         mixed dialogue edit, or straddling its boundary). The replacement
         must carry the JP ORIGINAL operand bytes — the pass rewrites them to
         the shifted target, so the data stays layout-independent. A window
         whose replacement bytes assemble to an out-of-buffer value is an
         intentional bytecode rewrite (the operand is gone) and ships
         verbatim; an in-buffer value that differs from the JP bytes is a
         BAKED LAYOUT (the `_STG20A` soft-lock class) and fails the build.

    Operand targets that themselves lie inside a replaced range are placed by
    unique signature anchoring (see `_anchor_displacement`)."""
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
    payload = payload_mask(data)
    outside = [(o, struct.unpack_from("<I", data, o)[0])
               for o in pointer_offsets(data, payload, exclude)]

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

    # pass 2 windows: the full (no-exclude) scan minus the outside set
    all_sel = pointer_offsets(data, payload, None)
    outside_set = {p for p, _ in outside}
    occupied = bytearray(len(data) + 4)
    for q, _v in outside:
        occupied[q] = occupied[q + 1] = occupied[q + 2] = occupied[q + 3] = 1
    inedit = []
    for p in all_sel:
        if p in outside_set:
            continue
        srcs = edit_window_sources(p, edits)
        if srcs is None:
            continue            # dropped by exclude for another reason: skip
        # the two passes must never write overlapping bytes (shadow-set skew
        # between the exclude/no-exclude scans would corrupt an operand)
        if occupied[p] or occupied[p + 1] or occupied[p + 2] or occupied[p + 3]:
            raise AssertionError(
                f"in-edit pointer window 0x{p:x} overlaps an outside window "
                f"— scan shadow skew, refusing to relocate both")
        inedit.append((p, srcs))

    def check_in_file(p: int, new_value: int) -> int:
        if not (STAGE_RAM_BASE <= new_value < STAGE_RAM_BASE + len(out)):
            raise AssertionError(
                f"pointer at 0x{p:x} relocates out of file: 0x{new_value:08x}")
        return new_value

    for p, value in outside:
        target = value - STAGE_RAM_BASE
        new_value = check_in_file(p, STAGE_RAM_BASE + target + shift(target))
        struct.pack_into("<I", out, p + shift(p), new_value)

    for p, srcs in inedit:
        rep = bytearray()
        mappable = True
        for s in srcs:
            if s[0] == "jp":
                rep.append(data[s[1]])
            else:
                _, k, r = s
                new = edits[k][2]
                if r >= len(new):
                    mappable = False
                    break
                rep.append(new[r])
        jp_bytes = data[p:p + 4]
        if mappable and bytes(rep) == jp_bytes:
            # canonical form: JP operand bytes in the replacement -> relocate
            s0 = srcs[0]
            if s0[0] == "jp":
                built_pos = s0[1] + shift(s0[1])
            else:
                _, k, r = s0
                off = edits[k][0]
                built_pos = off + shift(off) + r
            value = struct.unpack_from("<I", jp_bytes, 0)[0]
            target = value - STAGE_RAM_BASE
            if 0 <= target < len(data) and exclude[target]:
                d = _anchor_displacement(data, out, edits, shift, target, all_sel)
                new_value = STAGE_RAM_BASE + target + shift(target) + d
            else:
                new_value = STAGE_RAM_BASE + target + shift(target)
            struct.pack_into("<I", out, built_pos, check_in_file(p, new_value))
            continue
        assembled = struct.unpack_from("<I", bytes(rep), 0)[0] if mappable else None
        if (assembled is not None
                and STAGE_RAM_BASE <= assembled < STAGE_RAM_BASE + STAGE_BUFFER_SIZE):
            raise AssertionError(
                f"edit at window 0x{p:x} bakes an absolute pointer "
                f"0x{assembled:08x} (JP bytes {jp_bytes.hex()}) — replacements "
                f"must carry the JP operand bytes; the builder relocates them")
        # otherwise: deliberate bytecode rewrite (operand dropped) — verbatim
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

    ``stage_data`` is one parsed data/zh/stages/*.json. Returns the
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
