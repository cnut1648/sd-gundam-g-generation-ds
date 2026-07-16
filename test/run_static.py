#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_static.py — the static (no-emulator) regression gate suite for the
SD Gundam G Generation DS JP→ZH translation ROM.

Run this on EVERY built ROM before shipping.  Exit 0 iff ALL gates pass.

    .venv/bin/python test/run_static.py <rom.nds> [options]

Every gate is anchored to the JAPANESE source ROM (the untranslated oracle) plus
a small set of baselines in test/golden/ — there is no dependency on any previous
translated build.  The gates encode hard-won invariants; each docstring says what
in-game failure the gate protects against.

  GATE                        protects against
  --------------------------  ----------------------------------------------------------
  audio_header                broken music/SFX (SDAT ROMCTRL header word)
  ui_text_dispatch            the unit-info/ID screen 乱码 (garble) regression
  nameplate_render_path       illegible 8px speaker nameplates / stray code at the patch
  ui_font_atlas_dispatch      8px mush ZH on the UI-font path / corrupt render trampoline
  code_image_parity           ANY unexplained arm9 byte change vs the JP source (combat!)
  dialogue_dict_frozen        the battle-entry freeze from a clobbered dialogue dictionary
  font_relocation             boot crash / unreadable text from a bad font relocation
  relocated_pointer_sanity    the off-by-N name-relocation pointer → mid-stage data abort
  charmap_font_consistency    encoding text to glyph slots the ROM font does not have
  glyph_style_uniformity      mixed-weight 'ghost' glyphs (stroke/shadow raster grammar)
  stage_header_alignment      the stage-load black screen from a misaligned header table
  stage_file_structure        stage-file overrun / dangling pointer → load freeze
  stage_script_integrity      the press-A / mid-stage event freezes (dialogue VM desync)
  inline_dialogue_blocks      overrun of code-embedded dialogue blocks (cutscene abort)
  event_script_pointers       the ending/cutscene black screen (clobbered jump pointer)
  battle_voice_structure      in-combat crash/garble from broken bark record framing
  bark_framing                the garbled-bark class (stray byte in a sub-line gap)
  untranslated_dialogue       Japanese story dialogue shipping in a "translated" build
  translation_coverage        silent translation regression (kana ratchet)
  glyph_width                 the too-wide-line blank/freeze class (ZH must fit JP field)
  field_width_budgets         box-title overflow / heap-garbage titles on the ID screens
  label_render_consistency    mixed-size "floating" glyphs in one label list
  unit_weapon_names           unit/weapon name garbage or coverage regression
  id_command_names            ID-command name/summary/detail garbage or coverage loss
  name_pointer_band           the 出击/deploy HARD-FREEZE from a unit/pilot name ptr >= 0x02190000

Options:
  --jp PATH             the Japanese source ROM (default: the copy in the repo root)
  --update-baselines    recapture the ratchet baselines in test/golden/ from this ROM
                        (only do this from a build that itself passed everything else)
  --self-test           prove the gates have teeth: run RED checks on mutated images
                        and on the JP ROM, then exit (does not gate a build)
  --json PATH           also write the per-gate results as JSON
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import ndspy.rom

TEST_DIR = Path(__file__).resolve().parent
REPO = TEST_DIR.parent
GOLDEN = TEST_DIR / "golden"
DEFAULT_JP = REPO / "0098 - SD Gundam G Generation DS (Japan).nds"
CHARMAP_PATH = REPO / "data" / "charmap.json"

RAM_BASE = 0x02000000

# =============================================================================
# ROM / text primitives
# =============================================================================

def load_rom(path: Path) -> ndspy.rom.NintendoDSRom:
    return ndspy.rom.NintendoDSRom(Path(path).read_bytes())


def arm9_image(rom) -> bytes:
    """Decompressed arm9 bytes (handles a BLZ-compressed image, marker @0xB20)."""
    a = bytes(rom.arm9)
    cse = struct.unpack_from("<I", a, 0xB20)[0]
    if cse:
        import ndspy.codeCompression as cc
        a = cc.decompress(a[:cse - RAM_BASE])
    return a


def all_filenames(rom):
    out = []
    def walk(folder, prefix=""):
        for f in folder.files:
            out.append(prefix + f)
        for name, sub in folder.folders:
            walk(sub, prefix + name + "/")
    walk(rom.filenames)
    return out


def stage_files(rom):
    return sorted(f for f in all_filenames(rom)
                  if f.upper().startswith("_STG") and f.endswith(".bin"))


def token_term(d: bytes, start: int) -> int:
    """TOKEN-AWARE offset of the first standalone `00 00` at/after start (-1 = none).
    A byte >= 0xE0 opens a 2-byte glyph/macro token whose LOW byte never terminates."""
    j, n = start, len(d)
    while j < n - 1:
        if d[j] >= 0xE0:
            j += 2
            continue
        if d[j] == 0 and d[j + 1] == 0:
            return j
        j += 1
    return -1


def is_real_block(d: bytes, p: int) -> bool:
    """A real dialogue block: standalone 0x15, token-aware-terminated payload with
    at least one 2-byte glyph/macro token."""
    if p < 0 or p >= len(d) or d[p] != 0x15:
        return False
    t = token_term(d, p + 1)
    return t > p + 1 and any(b >= 0xE0 for b in d[p + 1:t])


def iter_display_blocks(d: bytes):
    """Yield (block_start, terminator) for every real dialogue block, walking
    token-aware so a glyph-token low byte 0x15 is never mistaken for a marker."""
    i, n = 0, len(d)
    while i < n - 1:
        b = d[i]
        if b >= 0xE0:
            i += 2
            continue
        if b == 0x15:
            t = token_term(d, i + 1)
            if t > i + 1 and any(x >= 0xE0 for x in d[i + 1:t]):
                yield (i, t)
                i = t + 2
                continue
        i += 1


# ---- charmap-backed classification ------------------------------------------
class Charmap:
    def __init__(self, path: Path = CHARMAP_PATH):
        raw = json.loads(path.read_text())
        self.one_byte = raw["one_byte"]                                  # char -> code
        self.sb_rev = {v: k for k, v in raw["one_byte"].items()}         # code -> char
        self.jp_slots = {int(k): v for k, v in raw["jp_slot_chars"].items()}
        self.zh_slots = raw["two_byte_zh"]                               # char -> slot
        self.zh_rev = {v: k for k, v in raw["two_byte_zh"].items()}
        # simplified chars minted into reclaimed JP-band slots (< 2196)
        self.zh_minted_slots = {int(s) for s in raw["two_byte_zh"].values()
                                if 224 <= int(s) < 2196}
        self.text_bytes = set(raw["one_byte"].values()) | {0x00}
        kana = lambda c: c is not None and any(
            "\u3040" <= ch <= "\u30ff" or "\uff65" <= ch <= "\uff9f" for ch in c)
        self.kana_slots = ({s for s, c in self.jp_slots.items() if kana(c)}
                           | {c for c, ch in self.sb_rev.items() if kana(ch)})


def _is_kana_char(c) -> bool:
    if not c or len(c) != 1:
        return False
    o = ord(c)
    return 0x3041 <= o <= 0x3096 or 0x30A1 <= o <= 0x30FA   # excludes ・(30FB) ー(30FC)


def _is_ideograph(c) -> bool:
    return c is not None and len(c) == 1 and 0x3400 <= ord(c) <= 0x9FFF


ZH_SLOT_MIN = 2196          # atlas slots >= this are the injected Chinese glyph band
SLOT_DEBIAS = 0xDF20        # 2-byte token 0xE0xx..0xEFxx -> slot = token - 0xDF20


# =============================================================================
# stage-script bytecode VM model (disassembly-audited; see stage_script_integrity)
# =============================================================================
STG_BASE = 0x0232C800       # fixed RAM buffer every stage file loads to
STG_BUFFER = 0x13800        # script buffer size
STG_CAP_SAFE = 0x13400      # per-file cap (1 KiB safety margin under the buffer)
OPSZ = {0x00: 0, 0x01: 0, 0x02: 4, 0x03: 2, 0x04: 1, 0x05: 2, 0x06: 2, 0x07: 0,
        0x08: 0, 0x09: 0, 0x0A: 0, 0x0B: 0, 0x0C: 0, 0x0D: 0, 0x0E: 0, 0x0F: 0,
        0x10: 0, 0x11: 0, 0x12: 0, 0x13: 6, 0x14: 1, 0x16: 4, 0x17: 1, 0x18: 2, 0x19: 1}
JUMP_OPS = (0x02, 0x13, 0x16)             # GOTO / CALL / CGOTO (absolute u32 target)
DISPLAY_OP, RET_OP, GOTO_OP = 0x15, 0x01, 0x02
GAP_STEP_LIMIT = 600
VM_REGION = (0x32060, 0x324A0)            # arm9 span: dispatch + handlers + readers
VM_REGION_SHA1 = "3808629bdc7da1321df30ede1b52e06f1755631e"
VM_JUMPTABLE_RAM = 0x0203207E
VM_HANDLER_STUBS = {0x00: 0x02032064, 0x01: 0x020320B2, 0x02: 0x020320B4,
                    0x13: 0x02032126, 0x15: 0x02032136, 0x16: 0x0203213C}
STG_HEADER_MAX = 128


def header_entries(d: bytes, base: int = STG_BASE):
    """Scene-entry points: the contiguous run of in-range u32 pointers at the file
    head (from offset 4), stopping at the first out-of-buffer word."""
    n = len(d)
    ents, i = [], 4
    while i + 4 <= n and len(ents) < STG_HEADER_MAX:
        p = struct.unpack_from("<I", d, i)[0]
        if base <= p < base + n:
            ents.append(p - base)
            i += 4
        else:
            break
    return ents


def gap_desync(d: bytes, start: int, base: int = STG_BASE):
    """Replay the VM from a block terminator+2 (the press-A resume point). Returns
    ('oob', pc, target) if the advance reaches a jump whose absolute target is
    outside the script buffer (= data abort = hard freeze), else None."""
    n = len(d)
    end = base + n
    pc, steps = start, 0
    while steps < GAP_STEP_LIMIT and 0 <= pc < n:
        steps += 1
        op = d[pc]
        if op > 0x19:                     # dispatch loop skips invalid opcode bytes
            pc += 1
            continue
        if op == DISPLAY_OP or op == RET_OP:
            return None
        if op in JUMP_OPS:
            if pc + 5 > n:
                return None
            tgt = struct.unpack_from("<I", d, pc + 1)[0]
            if not (base <= tgt < end):
                return ("oob", pc, tgt)
            if op == GOTO_OP:
                return None
        pc += 1 + OPSZ.get(op, 0)
    return None


def _block_index(spans):
    s = sorted(spans)
    return [a for (a, _) in s], [b for (_, b) in s]


def _in_span(starts, ends, p):
    i = bisect.bisect_right(starts, p) - 1
    return i >= 0 and p < ends[i]


def _next_block_start(starts, p):
    i = bisect.bisect_right(starts, p)
    return starts[i] if i < len(starts) else None


def skipwalk_to_oob(d, start, base=STG_BASE, step_limit=GAP_STEP_LIMIT):
    n = len(d)
    end = base + n
    pc, steps = start, 0
    while steps < step_limit and 0 <= pc < n:
        steps += 1
        op = d[pc]
        if op > 0x19:
            pc += 1
            continue
        if op == DISPLAY_OP or op == RET_OP:
            return None
        if op in JUMP_OPS:
            if pc + 5 > n:
                return None
            tgt = struct.unpack_from("<I", d, pc + 1)[0]
            if not (base <= tgt < end):
                return (pc, tgt)
            if op == GOTO_OP:
                return None
        pc = pc + 1 + OPSZ.get(op, 0)
    return None


def reach_desyncs(d: bytes, base: int = STG_BASE):
    """Replay the VM over its reachable control-flow graph (seed = scene-entry table
    + every block terminator+2; follow in-range jumps to a fixpoint). Flags reachable
    inter-block ops that corrupt the stream into a wild out-of-buffer jump — the
    event-stream freeze class the per-gap replay misses. 0 on the JP oracle."""
    n = len(d)
    end = base + n
    blocks = list(iter_display_blocks(d))
    spans = [(bs, t + 2) for (bs, t) in blocks]
    starts, ends = _block_index(spans)
    seeds = set(t + 2 for (_, t) in blocks) | set(header_entries(d, base))
    visited, work, flags = set(), list(seeds), []
    while work:
        pc = work.pop()
        steps = 0
        while 0 <= pc < n and pc not in visited and steps < 20000:
            visited.add(pc)
            steps += 1
            op = d[pc]
            if op == DISPLAY_OP:
                t = token_term(d, pc + 1)
                if t < 0:
                    break
                pc = t + 2
                continue
            if op == RET_OP:
                break
            sz = OPSZ.get(op, 0) if op <= 0x19 else 0
            if op <= 0x19 and sz > 0 and not _in_span(starts, ends, pc):
                fired = False
                if op in JUMP_OPS and pc + 5 <= n:      # (A) jump swallows a 0x15 marker
                    tgt = struct.unpack_from("<I", d, pc + 1)[0]
                    if not (base <= tgt < end):
                        for q in range(pc + 1, min(pc + 1 + sz, n)):
                            if d[q] == DISPLAY_OP and is_real_block(d, q):
                                flags.append((pc, op, tgt, q))
                                fired = True
                                break
                ns = _next_block_start(starts, pc)      # (B) operand crosses next block
                if ns is not None and pc + 1 <= ns < pc + 1 + sz:
                    r = skipwalk_to_oob(d, pc, base)
                    if r is not None:
                        flags.append((pc, op, r[1], ns))
                        fired = True
                if fired:
                    break
            if op in JUMP_OPS:
                if pc + 5 > n:
                    break
                tgt = struct.unpack_from("<I", d, pc + 1)[0]
                if not (base <= tgt < end):
                    break
                if (tgt - base) not in visited:
                    work.append(tgt - base)
                if op == GOTO_OP:
                    break
                pc = pc + 1 + sz
                continue
            if op > 0x19:
                break
            pc = pc + 1 + OPSZ.get(op, 0)
    return sorted(set(flags))


def cfg_iso_divergences(dj: bytes, dz: bytes):
    """NOP/skip-tolerant lockstep walk of the JP and candidate control-flow graphs
    from the shared scene-entry pointers.  A correctly rebuilt stage file is
    STRUCTURALLY ISOMORPHIC to the JP original (same event opcodes, jumps rebased,
    only display payloads / jump operand values differ).  Returns (ndiv, first)."""
    nj, nz = len(dj), len(dz)
    hj, hz = header_entries(dj), header_entries(dz)
    if len(hj) != len(hz):
        return 1, (0, 0, f"hdr={len(hj)}", f"hdr={len(hz)}")
    divs, first, visited, work = 0, None, set(), list(zip(hj, hz))

    def skip(d, pc, n):
        while 0 <= pc < n and (d[pc] == 0x00 or d[pc] > 0x19):
            pc += 1
        return pc

    def kind(d, pc):
        o = d[pc]
        return ("display" if o == DISPLAY_OP else "ret" if o == RET_OP
                else "jump" if o in JUMP_OPS else "op")

    while work:
        pcj, pcz = work.pop()
        steps = 0
        while steps < 400000:
            steps += 1
            pcj = skip(dj, pcj, nj)
            pcz = skip(dz, pcz, nz)
            if not (0 <= pcj < nj and 0 <= pcz < nz):
                break
            if pcj in visited:
                break
            visited.add(pcj)
            kj, kz = kind(dj, pcj), kind(dz, pcz)
            oj, oz = dj[pcj], dz[pcz]
            if kj != kz or (kj in ("op", "jump") and oj != oz):
                divs += 1
                if first is None:
                    first = (pcj, pcz, f"{kj}/0x{oj:02x}", f"{kz}/0x{oz:02x}")
                break
            if kj == "display":
                tj = token_term(dj, pcj + 1)
                tz = token_term(dz, pcz + 1)
                if tj < 0 or tz < 0:
                    divs += 1
                    if first is None:
                        first = (pcj, pcz, "unterm", "unterm")
                    break
                pcj, pcz = tj + 2, tz + 2
                continue
            if kj == "ret":
                break
            if kj == "jump":
                tj = struct.unpack_from("<I", dj, pcj + 1)[0]
                tz = struct.unpack_from("<I", dz, pcz + 1)[0]
                inj = STG_BASE <= tj < STG_BASE + nj
                inz = STG_BASE <= tz < STG_BASE + nz
                if inj != inz:
                    divs += 1
                    if first is None:
                        first = (pcj, pcz, f"jump->{'in' if inj else 'OOB'}",
                                 f"jump->{'in' if inz else 'OOB'}")
                    break
                if inj and inz:
                    work.append((tj - STG_BASE, tz - STG_BASE))
                if oj == GOTO_OP:
                    break
                pcj += 1 + OPSZ[oj]
                pcz += 1 + OPSZ[oj]
                continue
            pcj += 1 + OPSZ.get(oj, 0)
            pcz += 1 + OPSZ.get(oz, 0)
    return divs, first


def audit_opcode_model(a9: bytes):
    """Assert the arm9 event-VM is byte-identical to the disassembly-audited code
    this model was derived from (sha1 of the dispatch region + the jump table),
    so the OPSZ/JUMP_OPS/skip model provably applies to the ROM under test."""
    if VM_REGION[1] > len(a9):
        return False, "arm9 too short for the VM region"
    h = hashlib.sha1(a9[VM_REGION[0]:VM_REGION[1]]).hexdigest()
    if h != VM_REGION_SHA1:
        return False, f"VM dispatch region sha1 {h[:12]}… != audited reference"
    jt = VM_JUMPTABLE_RAM - RAM_BASE
    for opn, want in sorted(VM_HANDLER_STUBS.items()):
        e = struct.unpack_from("<h", a9, jt + opn * 2)[0]
        if ((VM_JUMPTABLE_RAM + e) & ~1) != want:
            return False, f"jump table[{opn:#04x}] does not resolve to the audited handler"
    return True, "VM dispatch byte-identical to the audited reference; jump table intact"


def reachable_display_blocks(d: bytes):
    """Reachable DISPLAY blocks (CFG walk from the scene-entry table) — the lines
    the player actually sees.  Counts EVERY reachable non-empty payload (including
    pure single-byte-kana lines)."""
    n = len(d)
    end = STG_BASE + n
    seen, starts = set(), []
    work = list(header_entries(d))
    while work:
        pc = work.pop()
        while 0 <= pc < n and pc not in seen:
            seen.add(pc)
            op = d[pc]
            if op == DISPLAY_OP:
                t = token_term(d, pc + 1)
                if t < 0:
                    break
                if t > pc + 1:
                    starts.append(pc)
                pc = t + 2
                continue
            if op == RET_OP:
                break
            if op > 0x19:
                pc += 1
                continue
            if op in JUMP_OPS and pc + 5 <= n:
                tgt = struct.unpack_from("<I", d, pc + 1)[0]
                if STG_BASE <= tgt < end and (tgt - STG_BASE) not in seen:
                    work.append(tgt - STG_BASE)
                if op == GOTO_OP:
                    break
            pc = pc + 1 + OPSZ.get(op, 0)
    return sorted(set(starts))


# =============================================================================
# arm9 layout constants (shared by several gates)
# =============================================================================
ROMCTRL_OFF, ROMCTRL_EXPECT = 0x60, bytes.fromhex("57664100")
UI_DISPATCH_OFF, UI_DISPATCH_ORIG, UI_DISPATCH_NOP = 0x1322C, bytes([0x11, 0xD1]), bytes([0xC0, 0x46])
NAMEPLATE_IMM_OFF, NAMEPLATE_IMM_ORIG, NAMEPLATE_IMM_FIX = 0x2BCA6, 0x02, 0x03
UI_FONT_INJ_OFF = 0x131D8
UI_FONT_INJ_ORIG = bytes.fromhex("48011618")     # stock 8x16 glyph-blit opening insns
UI_FONT_INJ_FIX = bytes.fromhex("07f162f8")      # bl -> ZH-to-atlas trampoline cave
UI_FONT_CAVE_OFF, UI_FONT_CAVE_SIG = 0x11A2A0, bytes.fromhex("89231b01")

DIALOGUE_FONT_PTR_OFF = 0x1315C                  # renderA atlas base pointer literal
FONT_RAM_RELOCATED = 0x023027A0
FONT_RAM_ORIGINAL = 0x0211A2A0
UI_FONT_PTR_OFF = 0x1321C                        # renderB 8x16 font base pointer literal
MP_LIST_START_OFF, MP_LIST_END_OFF = 0xB0C, 0xB10
ARENA_LO_OFF = 0xA48F8
APPEND_TAIL_OFF = 0x1B6DA0                       # relocated payloads append from here
GLYPH_CELL = 36
RELOC_MAIN_RAM_LO, RELOC_MAIN_RAM_HI = 0x023027A0, 0x02380000

UI_DICT_OFF = 0x12D770                           # UI dictionary (renderB path)
PRIMARY_DICT_OFF = 0x1444B4                      # dialogue dictionary (renderA path)
PRIMARY_DICT_BAND = (0x1444B4, 0x14AC34)         # overlaps the UI font glyph array!
CHAR_DB_OFF, CHAR_DB_STRIDE = 0xDCF18, 0x48      # pilot record table, +0x04 = name ptr
CHAR_DB_BASE_COUNT, CHAR_DB_FULL_COUNT = 256, 562
MASTER_TABLE_OFF, MASTER_STRIDE, MASTER_MAX = 0xB94BC, 0xD8, 945
MASTER_NAME_OFF, MASTER_WPN_OFF, MASTER_WPN_STRIDE, MASTER_WPN_N = 0x00, 0x2C, 0x1C, 6
ID_CMD_TABLE_OFF, ID_CMD_REC = 0xEC994, 0x24
ID_CMD_NAME_OFF, ID_CMD_SUMMARY_OFF, ID_CMD_DETAIL_IDX_OFF = 0x00, 0x08, 0x22
ID_CMD_DETAIL_OFFTAB, ID_CMD_DETAIL_OFFTAB_RAM = 0xF9048, 0x020F9048
ABILITY_TABLE_LO, ABILITY_TABLE_HI = 0xFC000, 0x119000
DEAD_BANK_DELTA = 0x0214BA00                     # relocated data bank: RAM = file off + this
DEAD_BANK_RAM_LO = 0x02300000

RENDER_A_ADVANCE, RENDER_B_ADVANCE = 12, 8       # fixed-cell advances (12x12 / 8x16)
FREF_MAX_DEPTH = 8

# On-screen field pixel budgets (measured against the real engine):
ID_TITLE_BUDGET_PX = 64          # ID-command box title row (engine truncates + '~' past this)
ID_EFFECT_BUDGET_PX = 76         # ID-command effect summary line in the box body
ABILITY_NAME_BUDGET_PX = 76      # ID-ability name cell
UNIT_NAME_BUDGET_PX = 144        # widest unit-name context (status/database field)
SPEAKER_PLATE_CELLS = 7          # dialogue speaker nameplate field (7 glyph cells)
# Runtime-heap windows inside the relocated data bank: a display-string pointer that
# lands here renders live heap garbage on fresh boot (proven by RAM captures).
HEAP_CLOBBER_WINDOWS = ((0x0232C800, 0x02338000), (0x02340000, 0x023489AC))
ID_TITLE_DANGER = (0x0232C800, 0x023489AC)

BATTLE_VOICE_FILES = ("0.bin", "1.bin", "1dd.bin", "1de.bin", "c4f.bin")
INLINE_DIALOGUE_LO, INLINE_DIALOGUE_HI = 0x198712, 0x1AD536   # code-embedded 0x15 blocks

# JP string/data section (where every original text pointer points):
JP_STRINGS_LO, JP_STRINGS_HI = 0x020B0000, 0x021B6DB8
# Legitimate homes for a RELOCATED string pointer in a translated build:
RESIDENT_POOL_LO, RESIDENT_POOL_HI = 0x02180000, 0x021A0000
RELOC_BANK_LO, RELOC_BANK_HI = 0x02300000, 0x02400000
RELOC_PTR_SCAN_HI = 0x155B14

# Name-pointer reader band (deploy/nameplate freeze class): unit-name
# (master 0xB94BC +0x00) and pilot/character-name (char-DB 0xDCF18 +0x04)
# pointers MUST resolve BELOW 0x02190000.  The 出击/deploy unit-name path
# HARD-FREEZES (data abort) and the affinity/nameplate reader renders BLANK on a
# name pointer >= 0x02190000 — i.e. the 0x0219 resident sub-band and the autoload
# pools (0x0232.. pool A, 0x023E.. pool B).  Proven empirically: 816 name
# pointers < 0x02190000 render fine (52 at the JP pool 0x020B.., 763 relocated to
# 0x0218..); every pointer at 0x0219.. froze/blanked (v1.1 deploy-freeze bug).
# Effect summaries/details and weapon names are read by lenient accessors and may
# live >= 0x02190000, so they are NOT covered here.
NAME_PTR_SAFE_HI = 0x02190000
NAME_PTR_DANGER_HI = 0x02400000   # >= this = pre-existing JP-dummy junk ptrs, never dereferenced


def _pointer_repoint_ok(aj: bytes, az: bytes, word_off: int) -> bool:
    """The pointer-repoint rule: a 4-aligned word may differ from the JP source iff
    the JP value points into the JP string/data section AND the candidate value
    points to a legitimate string home (string section, resident name pool, or a
    relocated autoload bank).  This is how every name/label/table relocation looks;
    anything else (code, stats, offsets) fails."""
    if word_off % 4 or word_off + 4 > min(len(aj), len(az)):
        return False
    wj = struct.unpack_from("<I", aj, word_off)[0]
    wz = struct.unpack_from("<I", az, word_off)[0]
    if wj == wz or not (JP_STRINGS_LO <= wj < JP_STRINGS_HI):
        return False
    return (JP_STRINGS_LO <= wz < JP_STRINGS_HI
            or RESIDENT_POOL_LO <= wz < RESIDENT_POOL_HI
            or RELOC_BANK_LO <= wz < RELOC_BANK_HI)


# =============================================================================
# report plumbing
# =============================================================================
class Report:
    def __init__(self):
        self.rows = []                       # (name, status, detail); status PASS/FAIL/SKIP

    def add(self, name, ok, detail=""):
        st = "PASS" if ok else "FAIL"
        self.rows.append((name, st, detail))
        print(f"  [{st}] {name}: {detail}", flush=True)
        return ok

    def skip(self, name, detail=""):
        self.rows.append((name, "SKIP", detail))
        print(f"  [SKIP] {name}: {detail}", flush=True)

    @property
    def ok(self):
        return all(st != "FAIL" for _, st, _ in self.rows)


# =============================================================================
# the gates
# =============================================================================
def gate_audio_header(rep, ctx):
    """Header word 0x60..0x64 (ROMCTRL) must be the retail value — the setting the
    sound data (SDAT) streaming depends on; a wrong value silently kills audio."""
    got = ctx["raw"][ROMCTRL_OFF:ROMCTRL_OFF + 4]
    rep.add("audio_header", got == ROMCTRL_EXPECT,
            f"header[0x60:0x64]={got.hex()} (want {ROMCTRL_EXPECT.hex()})")


def gate_ui_text_dispatch(rep, ctx):
    """arm9 0x1322C must keep the ORIGINAL conditional branch.  NOP-ing it forces
    all UI text through the raw-glyph path instead of the decoder → the whole
    unit-info / ID screen renders garble.  (A historical regression this suite
    exists to make impossible.)"""
    got = ctx["a9"][UI_DISPATCH_OFF:UI_DISPATCH_OFF + 2]
    if got == UI_DISPATCH_ORIG:
        rep.add("ui_text_dispatch", True, f"0x1322C={got.hex()} (original branch; UI text decodes cleanly)")
    elif got == UI_DISPATCH_NOP:
        rep.add("ui_text_dispatch", False, f"0x1322C={got.hex()} = the NOP that garbles the unit-info/ID screens")
    else:
        rep.add("ui_text_dispatch", False, f"0x1322C={got.hex()} != original {UI_DISPATCH_ORIG.hex()} (unexpected byte)")


def gate_nameplate_render_path(rep, ctx):
    """The dialogue speaker-nameplate immediate at 0x2BCA6 must be the original
    (0x02, 8x16 path) or the readable-ZH fix (0x03, routes the plate to the 12x12
    dialogue font).  Any other value = stray corruption at a patched code site."""
    got = ctx["a9"][NAMEPLATE_IMM_OFF]
    ok = got in (NAMEPLATE_IMM_ORIG, NAMEPLATE_IMM_FIX)
    which = "12x12 dialogue-font plate (fix)" if got == NAMEPLATE_IMM_FIX else \
            "original 8x16 plate" if got == NAMEPLATE_IMM_ORIG else "UNEXPECTED"
    rep.add("nameplate_render_path", ok, f"0x2BCA6={got:#04x} -> {which}")


def gate_ui_font_atlas_dispatch(rep, ctx):
    """The UI-font glyph-blit at 0x131D8 must be the stock code or the documented
    trampoline that redirects ZH slots (>=2196) to the 12x12 atlas — and when the
    trampoline is present its cave must actually contain the dispatch (otherwise
    ZH text on the UI path renders as 8px mush or crashes)."""
    a9 = ctx["a9"]
    got = a9[UI_FONT_INJ_OFF:UI_FONT_INJ_OFF + 4]
    if got == UI_FONT_INJ_ORIG:
        rep.add("ui_font_atlas_dispatch", True, "0x131D8 = stock 8x16 glyph blit (no ZH redirect)")
    elif got == UI_FONT_INJ_FIX:
        cave_ok = a9[UI_FONT_CAVE_OFF:UI_FONT_CAVE_OFF + 4] == UI_FONT_CAVE_SIG
        rep.add("ui_font_atlas_dispatch", cave_ok,
                f"0x131D8 = trampoline; cave@0x11A2A0 {'present' if cave_ok else 'MISSING/corrupt'} "
                "(ZH slots >= 2196 -> 12x12 atlas on the UI-font path)")
    else:
        rep.add("ui_font_atlas_dispatch", False,
                f"0x131D8={got.hex()} != stock {UI_FONT_INJ_ORIG.hex()} / fix {UI_FONT_INJ_FIX.hex()}")


def gate_code_image_parity(rep, ctx):
    """THE combat-safety anchor: the candidate arm9 must be byte-identical to the
    JAPANESE source everywhere except (a) the annotated translation/render-patch
    regions in test/golden/arm9_allowed_regions.json, (b) 4-aligned pointer words
    that repoint a JP string pointer to a relocated string (the pointer-repoint
    rule), and (c) the appended relocation tail.  Any other diff = an unexplained
    change to game code/data — the class of edit that breaks combat."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    spec = json.loads((GOLDEN / "arm9_allowed_regions.json").read_text())
    regions = sorted((int(r["lo"], 16), int(r["hi"], 16)) for r in spec["regions"])
    forbidden = [(int(r["lo"], 16), int(r["hi"], 16), r["what"]) for r in spec["forbidden"]]
    # self-check the baseline: no allowed window may cross a forbidden band
    for lo, hi in regions:
        for flo, fhi, what in forbidden:
            if lo < fhi and hi > flo:
                rep.add("code_image_parity", False,
                        f"BAD BASELINE: allowed window 0x{lo:X}-0x{hi:X} overlaps forbidden band ({what})")
                return
    starts = [lo for lo, _ in regions]
    ends = [hi for _, hi in regions]

    def in_window(x):
        i = bisect.bisect_right(starts, x) - 1
        return i >= 0 and x < ends[i]

    n = min(len(aj), len(az), APPEND_TAIL_OFF)
    bad, first, repoints = 0, None, 0
    i = 0
    while i < n:
        if aj[i] == az[i]:
            i += 1
            continue
        if in_window(i):
            i += 1
            continue
        w = i & ~3
        if not any(flo <= w < fhi for flo, fhi, _ in forbidden) and _pointer_repoint_ok(aj, az, w):
            repoints += 1
            i = w + 4
            continue
        bad += 1
        if first is None:
            first = i
        i += 1
    if len(az) < APPEND_TAIL_OFF:
        rep.add("code_image_parity", False, f"arm9 truncated ({len(az)} B < the appended-tail offset)")
        return
    if bad:
        rep.add("code_image_parity", False,
                f"{bad} arm9 byte(s) differ from the JP source outside every allowed region/rule "
                f"(first @0x{first:06X}: jp={aj[first]:02x} got={az[first]:02x}) — unexplained code/data edit")
    else:
        rep.add("code_image_parity", True,
                f"all head diffs vs JP are inside {len(regions)} annotated regions "
                f"+ {repoints} pointer-repoint words; appended tail from 0x{APPEND_TAIL_OFF:X} "
                f"({len(az) - APPEND_TAIL_OFF} B) validated by font_relocation")


def gate_dialogue_dict_frozen(rep, ctx):
    """The dialogue compression dictionary (0x1444B4) must be byte-identical to the
    JP source.  Its band physically overlaps the UI font glyph array; writing
    glyphs there once corrupted the dictionary and froze the game at battle entry.
    Combat dialogue macros expand through it EVERY battle — it is never edited."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    cnt = struct.unpack_from("<H", aj, PRIMARY_DICT_OFF)[0] // 2
    dict_len = 0
    for i in range(cnt):
        o = struct.unpack_from("<H", aj, PRIMARY_DICT_OFF + i * 2)[0]
        e = aj.find(b"\x00", PRIMARY_DICT_OFF + o)
        if e >= 0:
            dict_len = max(dict_len, e + 1 - PRIMARY_DICT_OFF)
    ro = aj[PRIMARY_DICT_OFF:PRIMARY_DICT_OFF + dict_len]
    rc = az[PRIMARY_DICT_OFF:PRIMARY_DICT_OFF + dict_len]
    if ro == rc:
        rep.add("dialogue_dict_frozen", True,
                f"dialogue dictionary [0x{PRIMARY_DICT_OFF:X}, +{dict_len}) byte-identical to JP ({cnt} entries)")
    else:
        d0 = next(i for i in range(dict_len) if ro[i] != rc[i])
        rep.add("dialogue_dict_frozen", False,
                f"dialogue dictionary differs from JP (first @0x{PRIMARY_DICT_OFF + d0:X}) — "
                f"macro expansion reads garbage offsets = battle-entry freeze")


def gate_font_relocation(rep, ctx):
    """A relocated-font build must have a well-formed autoload list, the dialogue
    font pointer aimed at the relocated atlas, a glyph-multiple font payload, and
    the heap arena floor raised above every relocated main-RAM bank (else the heap
    grows over the font/text banks and the game corrupts at runtime)."""
    a9 = ctx["a9"]
    fptr = struct.unpack_from("<I", a9, DIALOGUE_FONT_PTR_OFF)[0]
    if fptr == FONT_RAM_ORIGINAL:
        rep.skip("font_relocation", "font pointer is the original in-image atlas (non-relocated build)")
        return
    problems = []
    if fptr != FONT_RAM_RELOCATED:
        problems.append(f"font ptr {fptr:#010x} != relocated atlas {FONT_RAM_RELOCATED:#010x}")
    ls = struct.unpack_from("<I", a9, MP_LIST_START_OFF)[0]
    le = struct.unpack_from("<I", a9, MP_LIST_END_OFF)[0]
    nlist = (le - ls) // 12
    font_sz = 0
    if (le - ls) % 12 or not (3 <= nlist <= 6):
        problems.append(f"autoload list length {le - ls:#x} is not 3..6 entries")
    else:
        entries = [struct.unpack_from("<III", a9, (ls - RAM_BASE) + i * 12) for i in range(nlist)]
        rams = [e[0] for e in entries]
        if FONT_RAM_RELOCATED not in rams:
            problems.append(f"no autoload entry targets the font bank; rams={[hex(r) for r in rams]}")
        else:
            font_sz = next(sz for r, sz, _b in entries if r == FONT_RAM_RELOCATED)
            if font_sz == 0 or font_sz % GLYPH_CELL:
                problems.append(f"font payload size {font_sz:#x} is not a whole number of glyph cells")
            if APPEND_TAIL_OFF + font_sz > len(a9):
                problems.append("font payload truncated at the appended tail")
        main = [(r, sz) for (r, sz, _b) in entries if RELOC_MAIN_RAM_LO <= r < RELOC_MAIN_RAM_HI]
        top = max((r + sz for r, sz in main), default=0)
        top = (top + 0xFF) & ~0xFF
        alo = struct.unpack_from("<I", a9, ARENA_LO_OFF)[0]
        if main and alo <= top - 0x100:
            problems.append(f"heap arena-lo {alo:#010x} not raised above the relocated banks ({top:#010x})")
    if problems:
        rep.add("font_relocation", False, "; ".join(problems))
    else:
        rep.add("font_relocation", True,
                f"autoload {nlist} entries; font @{FONT_RAM_RELOCATED:#x} = {font_sz // GLYPH_CELL} glyph slots; "
                f"heap floor raised above the relocated banks")


def gate_relocated_pointer_sanity(rep, ctx):
    """Every code/table word that points into the RESIDENT relocated string pool
    must replace a JP word that pointed into the string/data section.  A relocated
    name pointer written into the wrong record field (an off-by-N) is a valid
    pointer that static text checks cannot see — but the engine dereferences the
    field as data and hard-freezes mid-stage.  JP-anchored, zero false positives."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    n = min(len(aj), len(az), RELOC_PTR_SCAN_HI)
    bad = []
    for o in range(0, n - 3, 4):
        wz = struct.unpack_from("<I", az, o)[0]
        if RESIDENT_POOL_LO <= wz < RESIDENT_POOL_HI:
            wj = struct.unpack_from("<I", aj, o)[0]
            if not (JP_STRINGS_LO <= wj < JP_STRINGS_HI):
                bad.append((o, wj, wz))
    if bad:
        o0, wj0, wz0 = bad[0]
        rep.add("relocated_pointer_sanity", False,
                f"{len(bad)} relocated-pool pointer(s) overwrite JP NON-string words "
                f"(off-by-N relocation; e.g. @0x{o0:X}: JP {wj0:#010x} -> {wz0:#010x}) — mid-stage freeze class")
    else:
        rep.add("relocated_pointer_sanity", True,
                "every resident-pool pointer replaces a JP string/data-section pointer")


def gate_charmap_font_consistency(rep, ctx):
    """Every glyph slot in data/charmap.json must exist in the font the ROM
    actually loads (slot < live slot count) — an out-of-range slot renders sparkle
    garbage from past the atlas end."""
    a9 = ctx["a9"]
    slots = ctx["atlas_slots"]
    cm = ctx["cm"]
    vals = list(cm.zh_slots.values()) + list(cm.one_byte.values())
    oob = [v for v in vals if not (0 <= v < slots)]
    if oob:
        rep.add("charmap_font_consistency", False,
                f"font has {slots} slots; {len(oob)} charmap code(s) out of range (max {max(vals)})")
    else:
        rep.add("charmap_font_consistency", True,
                f"{len(cm.zh_slots)} ZH + {len(cm.one_byte)} single-byte charmap codes all < {slots} font slots")


def _atlas_bytes(a9: bytes) -> bytes | None:
    """The 12x12 glyph atlas payload inside the built arm9 (autoload tail)."""
    fptr = struct.unpack_from("<I", a9, DIALOGUE_FONT_PTR_OFF)[0]
    if fptr == FONT_RAM_ORIGINAL:
        return None                                  # unpatched JP image
    ls = struct.unpack_from("<I", a9, MP_LIST_START_OFF)[0]
    le = struct.unpack_from("<I", a9, MP_LIST_END_OFF)[0]
    src = struct.unpack_from("<I", a9, 0xB14)[0] - RAM_BASE   # autoload source block
    off = src
    for i in range((le - ls) // 12):
        ram, size, _bss = struct.unpack_from("<III", a9, (ls - RAM_BASE) + i * 12)
        if ram == fptr:
            return a9[off:off + size]
        off += size
    return None


def gate_glyph_style_uniformity(rep, ctx):
    """Every non-empty glyph in the atlas must follow the original raster
    grammar: stroke pixels = value 1, shadow = value 2 EXACTLY equal to the
    stroke dilated one pixel right/down/down-right (the JP drop-shadow rule,
    verified on 2194/2194 original glyphs), and no value-3 pixels.  CJK
    ideograph strokes must stay inside the 11x11 design box (rows/cols 0..10).
    Violations render as flat/misaligned 'ghost' text next to correct glyphs —
    the mixed-weight defect class."""
    a9 = ctx["a9"]
    atlas = _atlas_bytes(a9)
    if atlas is None:
        rep.add("glyph_style_uniformity", False, "no relocated atlas found in arm9")
        return
    cm = ctx["cm"]
    cjk_slots = set()
    for ch, slot in cm.zh_slots.items():
        if len(ch) == 1 and 0x4E00 <= ord(ch) <= 0x9FFF:
            cjk_slots.add(slot)
    nslot = len(atlas) // GLYPH_CELL
    bad_shadow, bad_v3, bad_box = [], [], []
    for slot in range(nslot):
        cell = atlas[slot * GLYPH_CELL:(slot + 1) * GLYPH_CELL]
        stroke = [[False] * 12 for _ in range(12)]
        shadow = [[False] * 12 for _ in range(12)]
        empty = True
        v3 = False
        for i in range(144):
            v = (cell[i // 4] >> ((i % 4) * 2)) & 3
            if v == 0:
                continue
            empty = False
            r, c = i // 12, i % 12
            if v == 1:
                stroke[r][c] = True
            elif v == 2:
                shadow[r][c] = True
            else:
                v3 = True
        if empty:
            continue
        if v3:
            bad_v3.append(slot)
            continue
        ok = True
        for r in range(12):
            for c in range(12):
                want = False
                if not stroke[r][c]:
                    if r > 0 and stroke[r - 1][c]:
                        want = True
                    elif c > 0 and stroke[r][c - 1]:
                        want = True
                    elif r > 0 and c > 0 and stroke[r - 1][c - 1]:
                        want = True
                if shadow[r][c] != want:
                    ok = False
                    break
            if not ok:
                break
        if not ok:
            bad_shadow.append(slot)
            continue
        if slot in cjk_slots:
            if any(stroke[11][c] for c in range(12)) or any(stroke[r][11] for r in range(12)):
                bad_box.append(slot)
    problems = []
    if bad_shadow:
        problems.append(f"{len(bad_shadow)} glyph(s) violate the shadow rule (first slot {bad_shadow[0]})")
    if bad_v3:
        problems.append(f"{len(bad_v3)} glyph(s) contain value-3 pixels (first slot {bad_v3[0]})")
    if bad_box:
        problems.append(f"{len(bad_box)} CJK glyph(s) stroke outside the 11x11 box (first slot {bad_box[0]})")
    if problems:
        rep.add("glyph_style_uniformity", False, "; ".join(problems))
    else:
        rep.add("glyph_style_uniformity", True,
                f"{nslot} atlas slots: strokes+shadows all follow the JP raster grammar "
                f"({len(cjk_slots)} ZH CJK glyphs in the 11x11 box)")


def gate_stage_header_alignment(rep, ctx):
    """Stage-file header table slots 0x4/0x8/0x10/0x14/0x18 hold pointers the
    engine reads with 32-bit loads.  The ARM9 ROTATES an unaligned load (no fault,
    wrong bytes), so a table shifted off 4-byte alignment by a text grow reads a
    garbage pointer → stage-load black screen.  The JP source keeps all five
    4-aligned in 101/101 files; the byte-walked dialogue slot 0xC is exempt."""
    bad = []
    for fn in ctx["stg_names"]:
        d = ctx["cand_file"](fn)
        if d is None or len(d) < 0x1C:
            continue
        end = STG_BASE + len(d)
        for slot in (0x04, 0x08, 0x10, 0x14, 0x18):
            v = struct.unpack_from("<I", d, slot)[0]
            if STG_BASE <= v < end and (v & 3):
                bad.append((fn, slot, v))
    if bad:
        fn0, s0, v0 = bad[0]
        rep.add("stage_header_alignment", False,
                f"{len(bad)} stage header table(s) misaligned (e.g. {fn0} header[{s0:#x}]={v0:#x}, "
                f"addr&3={v0 & 3}) — rotated 32-bit load = stage-load black screen")
    else:
        rep.add("stage_header_alignment", True,
                f"all header tables 4-byte aligned across {len(ctx['stg_names'])} stage files")


def gate_stage_file_structure(rep, ctx):
    """Stage files must fit the fixed RAM script buffer (with margin) and contain
    no pointer-like word that targets PAST the file end (a dangling pointer from a
    bad grow/relocation is the primary stage-freeze cause).  Pointer windows inside
    dialogue payloads are glyph text, not pointers, and are masked."""
    problems = []
    for fn in ctx["stg_names"]:
        d = ctx["cand_file"](fn)
        if d is None:
            problems.append(f"{fn}: missing from candidate")
            continue
        if len(d) > STG_CAP_SAFE:
            problems.append(f"{fn}: {len(d)} B exceeds the script-buffer cap {STG_CAP_SAFE}")
        mask = bytearray(len(d))
        for (bs, t) in iter_display_blocks(d):
            for k in range(bs + 1, t):
                mask[k] = 1
        end = STG_BASE + len(d)
        for o in range(1, len(d) - 3):
            v = struct.unpack_from("<I", d, o)[0]
            if STG_BASE <= v < STG_BASE + STG_BUFFER and d[o - 1] < 0xE0 and v >= end:
                if mask[o] or mask[o + 3]:
                    continue
                problems.append(f"{fn}: pointer @0x{o:X} dangles past file end ({v:#010x})")
                break
    if problems:
        rep.add("stage_file_structure", False, "; ".join(problems[:6]) +
                ("" if len(problems) <= 6 else f" (+{len(problems) - 6} more)"))
    else:
        rep.add("stage_file_structure", True,
                f"{len(ctx['stg_names'])} stage files <= buffer cap, 0 dangling pointers")


def gate_stage_script_integrity(rep, ctx):
    """The stage-dialogue bytecode VM must never be led off a cliff: (0) the arm9
    VM dispatch is byte-identical to the audited reference (so this model applies);
    (1) replaying every dialogue block's press-A advance reaches the next block /
    a return — never a wild out-of-buffer jump (data abort = hard freeze); (2) the
    same over the REACHABLE event graph (mid-stage event freezes); (3) every stage
    file's control-flow graph is ISOMORPHIC to the freeze-free JP original (an
    in-range dropped/mangled event call hangs without a wild jump); (4) every
    dialogue block is terminated."""
    aud_ok, aud_det = audit_opcode_model(ctx["a9"])
    if not aud_ok:
        rep.add("stage_script_integrity", False, f"VM OPCODE-MODEL AUDIT FAILED: {aud_det}")
        return
    gaps, reaches, isos, unterm = [], [], [], []
    for fn in ctx["stg_names"]:
        dz = ctx["cand_file"](fn)
        dj = ctx["jp_file"](fn)
        if dz is None or dj is None:
            continue
        for (bs, t) in iter_display_blocks(dz):
            r = gap_desync(dz, t + 2)
            if r is not None:
                gaps.append((fn, bs, r[1], r[2]))
            if token_term(dz, bs + 1) < 0:
                unterm.append((fn, bs))
        for f in reach_desyncs(dz):
            reaches.append((fn,) + f)
        ndiv, first = cfg_iso_divergences(dj, dz)
        if ndiv:
            isos.append((fn, ndiv, first))
    if gaps or reaches or isos or unterm:
        ex = ""
        if gaps:
            fn, bs, opc, tgt = gaps[0]
            ex = f" e.g. {fn} block@0x{bs:X} advance -> wild {tgt:#010x}"
        elif reaches:
            fn, pc, op, tgt, eaten = reaches[0]
            ex = f" e.g. {fn} event@0x{pc:X} op {op:#04x} -> wild {tgt:#010x} (eats block 0x{eaten:X})"
        elif isos:
            fn, ndiv, first = isos[0]
            jpc, zpc, jk, zk = first if first else (0, 0, "?", "?")
            ex = f" e.g. {fn} CFG diverges from JP x{ndiv} (JP@0x{jpc:X} {jk} != 0x{zpc:X} {zk})"
        rep.add("stage_script_integrity", False,
                f"{len(gaps)} press-A desync(s) + {len(reaches)} reachable event desync(s) + "
                f"{len(isos)} non-JP-isomorphic file(s) + {len(unterm)} unterminated block(s) "
                f"[FREEZE class].{ex}")
    else:
        rep.add("stage_script_integrity", True,
                "VM model audited; every dialogue advance + reachable event jump stays in-buffer; "
                "every stage file CFG-isomorphic to the JP original")


def gate_inline_dialogue_blocks(rep, ctx):
    """Dialogue blocks embedded in the CODE image (not stage files) have no
    relocatable pointer, so their edits must be strictly in-place: each JP block
    keeps its 0x15 marker at the same offset AND its token-aware terminator —
    an overrun here corrupts adjacent event-script pointers (cutscene abort)."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    region = aj[INLINE_DIALOGUE_LO:INLINE_DIALOGUE_HI]
    nblk, bad = 0, []
    for (bs, t) in iter_display_blocks(region):
        nblk += 1
        off, term = INLINE_DIALOGUE_LO + bs, INLINE_DIALOGUE_LO + t
        if az[off] != 0x15 or az[term:term + 2] != b"\x00\x00":
            bad.append(off)
    if bad:
        rep.add("inline_dialogue_blocks", False,
                f"{len(bad)}/{nblk} code-embedded dialogue block(s) lost their marker/terminator "
                f"(first @0x{bad[0]:X}) — in-place overrun into event code")
    else:
        rep.add("inline_dialogue_blocks", True,
                f"{nblk} code-embedded dialogue blocks keep their 0x15 marker + terminator (JP-anchored)")


def gate_event_script_pointers(rep, ctx):
    """The code image embeds event scripts driving cutscenes/endings via inline
    `13 <4-byte absolute pointer>` jumps.  A pointer whose 2nd byte is 0x15 looks
    like a dialogue block to a naive baker; writing text over it makes the jump
    wild → ending/cutscene black screen.  Every JP-valid jump pointer must still
    resolve to valid RAM in the candidate."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    lo, hi = 0x02000000, 0x02400000
    bad = []
    o, n = 0, min(len(aj), len(az))
    while o < n - 5:
        if aj[o] == 0x13:
            jv = struct.unpack_from("<I", aj, o + 1)[0]
            if lo <= jv < hi:
                cv = struct.unpack_from("<I", az, o + 1)[0]
                if not (lo <= cv < hi):
                    bad.append((o, jv, cv))
                o += 5
                continue
        o += 1
    if bad:
        o0, jv0, cv0 = bad[0]
        rep.add("event_script_pointers", False,
                f"{len(bad)} event-script jump pointer(s) corrupted (e.g. @0x{o0:X}: "
                f"JP {jv0:#010x} -> {cv0:#010x}) — wild jump = cutscene/ending black screen")
    else:
        rep.add("event_script_pointers", True,
                "every JP-valid inline event jump pointer still resolves to valid RAM")


def _bv_headers_terms(oj: bytes):
    """All battle-voice sub-headers `05 ?? ?? 00 06 ?? ??` and terminators
    `00 03 00 0X` in the JP file (byte positions)."""
    n = len(oj)
    hdrs = [i for i in range(n - 7) if oj[i] == 5 and oj[i + 3] == 0 and oj[i + 4] == 6]
    terms = [i for i in range(n - 4)
             if oj[i] == 0 and oj[i + 1] == 3 and oj[i + 2] == 0 and oj[i + 3] in (1, 2)]
    return hdrs, terms


def gate_battle_voice_structure(rep, ctx):
    """Battle-voice (bark) files may only change their sub-line TEXT.  The record
    structure — every `05 .. 00 06 ..` sub-header, every `00 03 00 0X` terminator,
    the pre-record head region, and the file length — must be byte-identical to
    the JP source; broken framing crashes or garbles combat voice playback."""
    problems = []
    checked = 0
    for fn in BATTLE_VOICE_FILES:
        oj, oz = ctx["jp_file"](fn), ctx["cand_file"](fn)
        if oj is None or oz is None:
            problems.append(f"{fn}: missing")
            continue
        if len(oj) != len(oz):
            problems.append(f"{fn}: length {len(oz)} != JP {len(oj)} (record framing changed)")
            continue
        hdrs, terms = _bv_headers_terms(oj)
        checked += len(hdrs)
        hbad = [i for i in hdrs if oz[i:i + 7] != oj[i:i + 7]]
        tbad = [i for i in terms if oz[i:i + 4] != oj[i:i + 4]]
        h0 = hdrs[0] if hdrs else len(oj)
        headdiff = sum(1 for i in range(h0) if oj[i] != oz[i])
        if hbad:
            problems.append(f"{fn}: {len(hbad)} sub-header(s) clobbered (first @0x{hbad[0]:X})")
        if tbad:
            problems.append(f"{fn}: {len(tbad)} record terminator(s) clobbered (first @0x{tbad[0]:X})")
        if headdiff:
            problems.append(f"{fn}: {headdiff} byte(s) changed in the pre-record head region")
    if problems:
        rep.add("battle_voice_structure", False, "; ".join(problems[:6]))
    else:
        rep.add("battle_voice_structure", True,
                f"{len(BATTLE_VOICE_FILES)} battle-voice files: {checked} sub-headers + all "
                f"terminators + head regions byte-identical to JP (text runs free to change)")


def gate_bark_framing(rep, ctx):
    """Between a bark sub-line terminator and the next sub-header the JP file is
    ZERO padding.  A single stray non-zero byte there makes the live renderer fold
    the next sub-header into a bogus 2-byte token and render the following
    sub-line as garble.  The JP framing gives exact gap positions (0 false
    positives)."""
    hits = []
    for fn in BATTLE_VOICE_FILES:
        oj, oz = ctx["jp_file"](fn), ctx["cand_file"](fn)
        if oj is None or oz is None or len(oj) != len(oz):
            continue
        n = len(oj)
        _, terms = _bv_headers_terms(oj)
        for t in terms:
            j = t + 4
            while j < n and oj[j] == 0:
                if oz[j] != 0:
                    hits.append((fn, j))
                j += 1
    if hits:
        fn0, o0 = hits[0]
        rep.add("bark_framing", False,
                f"{len(hits)} stray byte(s) in bark inter-sub-line gaps (e.g. {fn0}@0x{o0:X}) "
                f"— garbles the following bark line in combat")
    else:
        rep.add("bark_framing", True, "all bark inter-sub-line gaps still zero (no garble strays)")


def _decode_block_text(payload: bytes, cm: Charmap):
    """(text, jp_tokens): decode a dialogue payload; jp_tokens = tokens only
    Japanese text uses (kana / dictionary macro / JP-band atlas ideograph)."""
    out, jp = [], []
    i, n = 0, len(payload)
    while i < n:
        b = payload[i]
        if b == 0:
            out.append("|")
            i += 1
            continue
        if b < 0xE0:
            c = cm.sb_rev.get(b, f"<{b:02x}>")
            out.append(c)
            i += 1
            if _is_kana_char(c):
                jp.append(c)
        else:
            if i + 1 >= n:
                out.append("<TRUNC>")
                break
            cc = (b << 8) | payload[i + 1]
            i += 2
            if cc >= 0xF000:
                out.append(f"<D{cc - 0xF000}>")
                jp.append(f"<D{cc - 0xF000}>")
            else:
                slot = cc - SLOT_DEBIAS
                if slot >= ZH_SLOT_MIN or slot in cm.zh_minted_slots:
                    # minted simplified glyphs live in reclaimed JP-band slots
                    out.append(cm.zh_rev.get(slot, f"<z{slot}>"))
                else:
                    c = cm.jp_slots.get(slot)
                    out.append(c if c else f"<j{slot}>")
                    if _is_kana_char(c) or _is_ideograph(c) or c is None:
                        jp.append(c or f"<j{slot}>")
    return "".join(out), jp


def gate_untranslated_dialogue(rep, ctx):
    """Every REACHABLE stage dialogue block (CFG walk from the scene entries) must
    render Chinese: no kana, no JP dictionary macro, no JP-band kanji — except the
    small audited intentional-JP allowlist (credits, layout-locked tutorial
    headers, screams).  Catches whole stages silently shipping in Japanese."""
    allow = ctx["dialogue_jp_allow"]
    cm = ctx["cm"]
    hits = []
    for fn in ctx["stg_names"]:
        d = ctx["cand_file"](fn)
        if d is None:
            continue
        for bs in reachable_display_blocks(d):
            t = token_term(d, bs + 1)
            pl = d[bs + 1:t]
            if pl.hex() in allow:
                continue
            text, jp = _decode_block_text(pl, cm)
            if jp:
                hits.append((fn, bs, text))
    if hits:
        from collections import Counter
        per = Counter(fn for fn, _o, _t in hits)
        worst = ", ".join(f"{fn}({c})" for fn, c in per.most_common(4))
        fn0, off0, text0 = hits[0]
        rep.add("untranslated_dialogue", False,
                f"{len(hits)} reachable dialogue block(s) render Japanese outside the allowlist "
                f"across {len(per)} stage file(s): {worst}; e.g. {fn0}@0x{off0:X}: {text0[:40]}")
    else:
        rep.add("untranslated_dialogue", True,
                f"every reachable stage dialogue block renders Chinese "
                f"({len(allow)} audited intentional-JP payloads exempt)")


class _Tally:
    __slots__ = ("kana", "kanji", "zh", "neutral", "ref")

    def __init__(self):
        self.kana = self.kanji = self.zh = self.neutral = self.ref = 0

    @property
    def residual_jp(self):
        return self.kana + self.kanji


def _classify_stream(payload: bytes, cm: Charmap, minted_as_zh: bool = True):
    """minted_as_zh: reclaimed JP-band slots re-registered as simplified
    glyphs count as Chinese — TRUE for candidate-ROM scans (the atlas cell
    now holds the zh glyph).  FALSE when scanning the JP SOURCE: there the
    same token is original Japanese text (the mint criterion only proves
    OUR build replaces those payloads, not that the JP ROM lacks them)."""
    kana = kanji = zh = neutral = ref = 0
    has_zh = False
    p, n = 0, len(payload)
    while p < n:
        b = payload[p]
        if b == 0x00:
            p += 1
            continue
        if b < 0xE0:
            ch = cm.sb_rev.get(b)
            if _is_kana_char(ch):
                kana += 1
            elif _is_ideograph(ch):
                kanji += 1
            else:
                neutral += 1
            p += 1
        else:
            if p + 1 >= n:
                ref += 1
                break
            cc = (b << 8) | payload[p + 1]
            p += 2
            if cc >= 0xF000:
                ref += 1
            else:
                slot = cc - SLOT_DEBIAS
                if slot >= ZH_SLOT_MIN or (minted_as_zh
                                           and slot in cm.zh_minted_slots):
                    # minted simplified glyphs live in reclaimed JP-band slots
                    # (charmap two_byte_zh registrations below ZH_SLOT_MIN)
                    zh += 1
                    has_zh = True
                else:
                    ch = cm.jp_slots.get(slot)
                    if _is_kana_char(ch):
                        kana += 1
                    elif _is_ideograph(ch):
                        kanji += 1
                    else:
                        neutral += 1
    return kana, kanji, zh, neutral, ref, has_zh


def _iter_blocks_bytewise(d: bytes):
    i, n = 0, len(d)
    while i < n - 1:
        if d[i] == 0x15:
            j = i + 1
            while j < n - 1 and not (d[j] == 0 and d[j + 1] == 0):
                j += 1
            yield i, i + 1, j
            i = j + 2
        else:
            i += 1


def _scan_coverage(rom, a9: bytes, cm: Charmap, stg_names, file_get,
                   minted_as_zh: bool = True):
    dlg, alt = _Tally(), _Tally()
    for fn in stg_names:
        d = file_get(fn)
        if d is None:
            continue
        for _o, s, e in _iter_blocks_bytewise(d):
            ka, kj, z, neu, rf, hz = _classify_stream(d[s:e], cm, minted_as_zh)
            dlg.kana += ka
            dlg.zh += z
            dlg.neutral += neu
            dlg.ref += rf
            if not hz:
                dlg.kanji += kj
    cnt = struct.unpack_from("<H", a9, UI_DICT_OFF)[0] // 2
    offs = struct.unpack_from("<%dH" % cnt, a9, UI_DICT_OFF)
    for o in offs:
        s = UI_DICT_OFF + o
        e = a9.find(b"\x00", s)
        ka, kj, z, neu, rf, hz = _classify_stream(a9[s:e], cm, minted_as_zh)
        alt.kana += ka
        alt.zh += z
        alt.neutral += neu
        alt.ref += rf
        if not hz:
            alt.kanji += kj
    return dlg, alt


COVERAGE_EPS = 1e-6


def gate_translation_coverage(rep, ctx, update=False):
    """The progress ratchet: kana presence is the unambiguous residual-Japanese
    signal.  Decodes the stage dialogue + the UI dictionary of the candidate AND
    of the JP source, computes how much original JP the candidate displaced, and
    FAILS if any coverage metric drops below test/golden/coverage_baseline.json.
    A build can only ever translate MORE, never less."""
    cm = ctx["cm"]
    dlg, alt = _scan_coverage(None, ctx["a9"], cm, ctx["stg_names"], ctx["cand_file"])
    # JP source scan: minted tokens there are ORIGINAL JAPANESE (the mints
    # only replace payloads in OUR build) — count them as kanji, keeping the
    # residual-JP denominator honest (the 従/償 lesson)
    bdlg, balt = _scan_coverage(None, ctx["jp_a9"], cm, ctx["stg_names"],
                                ctx["jp_file"], minted_as_zh=False)
    J0 = bdlg.residual_jp + balt.residual_jp
    Jv = dlg.residual_jp + alt.residual_jp
    K0 = bdlg.kana + balt.kana
    Kv = dlg.kana + alt.kana
    pct = lambda a, b: 0.0 if b == 0 else 100.0 * a / b
    metrics = {
        "char_pct": pct(J0 - Jv, J0),
        "kana_pct": pct(K0 - Kv, K0),
        "dialogue_kana_displaced_pct": pct(bdlg.kana - dlg.kana, bdlg.kana),
        "ui_dict_kana_displaced_pct": pct(balt.kana - alt.kana, balt.kana),
        "dialogue_kana": dlg.kana, "dialogue_zh": dlg.zh,
        "ui_dict_kana": alt.kana, "ui_dict_zh": alt.zh,
        "residual_jp": Jv, "residual_jp_original": J0,
    }
    summary = (f"CHAR {metrics['char_pct']:.2f}% / KANA {metrics['kana_pct']:.2f}% | "
               f"dialogue kana {bdlg.kana}->{dlg.kana}, ZH={dlg.zh}; "
               f"UI dict kana {balt.kana}->{alt.kana}, ZH={alt.zh}")
    baseline_path = GOLDEN / "coverage_baseline.json"
    if update:
        out = {"_what": "translation-coverage ratchet floor (recapture with "
                        "run_static.py <rom> --update-baselines from a passing build)",
               **{k: metrics[k] for k in sorted(metrics)}}
        baseline_path.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        rep.add("translation_coverage", True, f"baseline CAPTURED -> {summary}")
        return
    if not baseline_path.exists():
        rep.add("translation_coverage", False,
                f"no baseline at {baseline_path} (run --update-baselines from a good build); {summary}")
        return
    bl = json.loads(baseline_path.read_text())
    regressions = []
    for key, label in (("char_pct", "CHAR coverage"), ("kana_pct", "KANA coverage"),
                       ("dialogue_kana_displaced_pct", "dialogue kana displaced"),
                       ("ui_dict_kana_displaced_pct", "UI-dict kana displaced")):
        if metrics[key] < float(bl.get(key, 0.0)) - COVERAGE_EPS:
            regressions.append(f"{label} {metrics[key]:.2f}% < baseline {float(bl[key]):.2f}%")
    if regressions:
        rep.add("translation_coverage", False, "REGRESSION: " + "; ".join(regressions) + f" [{summary}]")
    else:
        rep.add("translation_coverage", True, f">= baseline; {summary}")


def _make_dict_expander(a9: bytes, base: int):
    cnt = struct.unpack_from("<H", a9, base)[0] // 2

    def expand(idx):
        if 0 <= idx < cnt:
            o = struct.unpack_from("<H", a9, base + idx * 2)[0]
            s = base + o
            e = a9.find(b"\x00", s)
            return a9[s:e] if e >= 0 else a9[s:]
        return None
    return expand


def _rendered_width(payload, advance, expander, depth=0):
    p, n, w = 0, len(payload), 0
    while p < n:
        b = payload[p]
        if b == 0x00:
            p += 1
            continue
        if b < 0xE0:
            w += advance
            p += 1
        else:
            if p + 1 >= n:
                w += advance
                break
            cc = (b << 8) | payload[p + 1]
            p += 2
            if cc >= 0xF000:
                sub = expander(cc - 0xF000) if (expander and depth < FREF_MAX_DEPTH) else None
                w += _rendered_width(sub, advance, expander, depth + 1) if sub is not None else advance
            else:
                w += advance
    return w


def _read_string_any_bank(a9: bytes, ptr: int):
    fo = ptr - RAM_BASE
    if not (0 <= fo < len(a9)):
        fo = ptr - DEAD_BANK_DELTA
        if not (0 <= fo < len(a9)):
            return None
    e = a9.find(b"\x00", fo)
    return a9[fo:e] if e >= 0 else a9[fo:]


def _cell_count(s: bytes) -> int:
    i = c = 0
    while i < len(s):
        i += 2 if s[i] >= 0xE0 else 1
        c += 1
    return c


def gate_glyph_width(rep, ctx):
    """A rendered line WIDER than its field blanks or freezes (the classic NDS
    fan-translation killer: it is glyph WIDTH, not byte length).  Every re-encoded
    UI-dictionary entry must render no wider than the JP it replaced; every
    re-pointed pilot name must fit max(JP width, the pilot's katakana name width,
    the 7-cell speaker-plate field).  Stage dialogue is exempt here (its blocks
    are re-framed, not in-place) — the box discipline for those lives in the
    stage gates + the live/VLM tier."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    exp_j = _make_dict_expander(aj, UI_DICT_OFF)
    exp_z = _make_dict_expander(az, UI_DICT_OFF)
    viol, checked = [], 0
    n_alt = struct.unpack_from("<H", aj, UI_DICT_OFF)[0] // 2
    for k in range(n_alt):
        j, z = exp_j(k), exp_z(k)
        if j is None or z is None or j == z:
            continue
        checked += 1
        jw = _rendered_width(j, RENDER_B_ADVANCE, exp_j)
        zw = _rendered_width(z, RENDER_B_ADVANCE, exp_z)
        if zw > jw:
            viol.append((f"ui-dict#{k}", jw, zw))
    kat = ctx["speaker_cells"]
    for r in range(CHAR_DB_FULL_COUNT):
        rec = CHAR_DB_OFF + r * CHAR_DB_STRIDE + 4
        if rec + 4 > min(len(aj), len(az)):
            break
        pj = struct.unpack_from("<I", aj, rec)[0]
        pz = struct.unpack_from("<I", az, rec)[0]
        j = _read_string_any_bank(aj, pj)
        z = _read_string_any_bank(az, pz)
        if j is None or z is None or (pj == pz and j == z):
            continue
        checked += 1
        jw = _rendered_width(j, RENDER_B_ADVANCE, exp_j)
        zw = _rendered_width(z, RENDER_B_ADVANCE, exp_z)
        bound = max(jw, kat.get(r, 0) * RENDER_B_ADVANCE,
                    SPEAKER_PLATE_CELLS * RENDER_B_ADVANCE if r >= CHAR_DB_BASE_COUNT else 0)
        if zw > bound:
            viol.append((f"pilot-name#{r}", bound, zw))
    if viol:
        shown = "; ".join(f"{w}: {zw}px > bound {jw}px" for w, jw, zw in viol[:20])
        rep.add("glyph_width", False,
                f"{len(viol)}/{checked} re-encoded string(s) render wider than their field "
                f"(blank/freeze risk): {shown}")
    else:
        rep.add("glyph_width", True,
                f"all {checked} re-encoded UI-dict entries + pilot names fit their JP/katakana/"
                f"plate field bounds")


def _fref_off_expander(a9: bytes, base: int):
    cnt = struct.unpack_from("<H", a9, base)[0] // 2 if base + 2 <= len(a9) else 0

    def expand(idx):
        if 0 <= idx < cnt:
            return struct.unpack_from("<H", a9, base + idx * 2)[0]
        return None
    return expand


def _decode_render_slots(a9: bytes, foff: int, expander, depth=0):
    """Decode a string the way the engine renders it: (slot, is_atlas) per glyph,
    dictionary macros expanded, is_atlas == slot >= the ZH band (12px advance)."""
    out = []
    i, n, guard = foff, len(a9), 0
    while 0 <= i < n and guard < 400:
        guard += 1
        b = a9[i]
        if b == 0x00:
            break
        if b < 0xE0:
            out.append((b, False))
            i += 1
        elif b < 0xF0:
            if i + 1 >= n:
                break
            slot = ((b << 8) | a9[i + 1]) - SLOT_DEBIAS
            out.append((slot, slot >= ZH_SLOT_MIN))
            i += 2
        else:
            if i + 1 >= n:
                break
            idx = ((b << 8) | a9[i + 1]) - 0xF000
            sub = expander(idx) if (expander and depth < FREF_MAX_DEPTH) else None
            if sub is not None:
                out += _decode_render_slots(a9, PRIMARY_DICT_OFF + sub, expander, depth + 1)
            else:
                out.append((0, False))
            i += 2
    return out


def _slots_width(slots):
    return sum(RENDER_A_ADVANCE if at else RENDER_B_ADVANCE for _, at in slots)


def _ram_to_file(a9: bytes, ptr: int):
    if RAM_BASE <= ptr < RAM_BASE + len(a9):
        return ptr - RAM_BASE
    f = ptr - DEAD_BANK_DELTA
    return f if 0 <= f < len(a9) else None


def _read_field(a9, ptr, expander):
    f = _ram_to_file(a9, ptr)
    if f is None or f < 0x1000 or a9[f] == 0:
        return None
    slots = _decode_render_slots(a9, f, expander)
    return _slots_width(slots), len(slots), slots


def gate_field_width_budgets(rep, ctx):
    """Strings drawn into a REGISTERED on-screen field must fit that field's true
    pixel budget at the true render-path advance (atlas glyph 12px, UI glyph 8px,
    macros expanded).  Also: no display-string pointer may land in the runtime-heap
    windows of the relocated bank (renders live heap garbage on fresh boot)."""
    a9 = ctx["a9"]
    exp = _fref_off_expander(a9, PRIMARY_DICT_OFF)
    viol, checked = [], 0
    # (1) ID-command box titles — exhaustive over every distinct record
    seen = set()
    over = total = 0
    for idx in range(1408):
        ro = ID_CMD_TABLE_OFF + idx * ID_CMD_REC
        if ro + ID_CMD_REC > len(a9):
            break
        p = struct.unpack_from("<I", a9, ro + ID_CMD_NAME_OFF)[0]
        if p in seen:
            continue
        seen.add(p)
        if ID_TITLE_DANGER[0] <= p < ID_TITLE_DANGER[1]:
            checked += 1
            viol.append((f"ID-command title rec#{idx} POINTER-IN-HEAP-WINDOW ({p:#x})", 0, 0))
            continue
        r = _read_field(a9, p, exp)
        if r:
            total += 1
            checked += 1
            if r[0] > ID_TITLE_BUDGET_PX:
                over += 1
                viol.append((f"ID-command title rec#{idx}", r[0], ID_TITLE_BUDGET_PX))
    # (2) ID-ability name cells + heap-window pointer tooth
    ab_seen = set()
    for w in range(ABILITY_TABLE_LO, min(ABILITY_TABLE_HI, len(a9) - 4), 4):
        p = struct.unpack_from("<I", a9, w)[0]
        if p in ab_seen:
            continue
        if any(lo <= p < hi for lo, hi in HEAP_CLOBBER_WINDOWS):
            ab_seen.add(p)
            checked += 1
            viol.append((f"ID-ability name POINTER-IN-HEAP-WINDOW ({p:#x})", 0, 0))
            continue
        r = _read_field(a9, p, exp)
        if not r:
            continue
        ab_seen.add(p)
        wd, ng, slots = r
        if ng <= 8 and any(at for _, at in slots) and wd > ABILITY_NAME_BUDGET_PX:
            checked += 1
            viol.append((f"ID-ability name ({p:#x})", wd, ABILITY_NAME_BUDGET_PX))
    # (3) master unit-name field
    mt_seen = set()
    for utid in range(1, MASTER_MAX):
        ro = MASTER_TABLE_OFF + utid * MASTER_STRIDE
        if ro + 4 > len(a9):
            break
        p = struct.unpack_from("<I", a9, ro)[0]
        if p in mt_seen:
            continue
        mt_seen.add(p)
        r = _read_field(a9, p, exp)
        if not r:
            continue
        checked += 1
        if r[0] > UNIT_NAME_BUDGET_PX:
            viol.append((f"unit name utid{utid}", r[0], UNIT_NAME_BUDGET_PX))
    if viol:
        shown = "; ".join(f"{lab}: {w}px > {b}px" if b else lab for lab, w, b in viol[:15])
        rep.add("field_width_budgets", False,
                f"{len(viol)} field string(s) overflow their box / point into heap windows: {shown}")
    else:
        rep.add("field_width_budgets", True,
                f"{checked} registered field strings within budget "
                f"(ID titles {total} distinct <= {ID_TITLE_BUDGET_PX}px; 0 heap-window pointers)")


def gate_label_render_consistency(rep, ctx):
    """The ID-ability labels stack as one vertical list and must render uniformly:
    (a) all group members from ONE store class (all relocated-bank or all
    resident — a mixed group renders one label at a different size/baseline);
    (b) no label may mix atlas-band CJK (12px) with UI-band CJK (8px) in one field
    (the 'floating glyph' defect)."""
    a9 = ctx["a9"]
    cm = ctx["cm"]
    exp = _fref_off_expander(a9, PRIMARY_DICT_OFF)
    rev = {}
    for ch, s in cm.zh_slots.items():
        rev.setdefault(int(s), ch)
    for s, ch in cm.jp_slots.items():
        rev.setdefault(int(s), ch)

    def atlas_cjk(slots):
        """Known atlas-band CJK content (glyphs past the charmap decode as nothing —
        matching is therefore by known-prefix, not exact equality)."""
        return "".join(rev.get(s, "") for s, at in slots
                       if at and rev.get(s) and _is_ideograph(rev[s]))

    def find_member(key):
        for w in range(ABILITY_TABLE_LO, min(ABILITY_TABLE_HI, len(a9) - 4), 4):
            p = struct.unpack_from("<I", a9, w)[0]
            f = _ram_to_file(a9, p)
            if f is None or f < 0x1000 or a9[f] == 0:
                continue
            slots = _decode_render_slots(a9, f, exp)
            if len(slots) > 10:
                continue
            if atlas_cjk(slots).startswith(key):
                return p, ("relocated-bank" if p >= DEAD_BANK_RAM_LO else "resident"), slots
        return None

    group = [("领导(力)", "领导"), ("NT等级N", "等级"), ("移动力UP", "移动")]
    members = []
    for label, key in group:
        m = find_member(key)
        if m:
            members.append((label, *m))
    if len(members) < 2:
        rep.skip("label_render_consistency", "ID-ability display group not located in this build")
        return
    regions = {lab: reg for lab, _p, reg, _s in members}
    floats = []
    for lab, _p, _reg, slots in members:
        a_cjk = [s for s, at in slots if at and rev.get(s) and _is_ideograph(rev[s])]
        b_cjk = [s for s, at in slots if not at and rev.get(s) and _is_ideograph(rev[s])]
        if a_cjk and b_cjk:
            floats.append(lab)
    mixed = len(set(regions.values())) > 1
    if mixed or floats:
        msg = []
        if mixed:
            msg.append("group renders from MIXED stores: " +
                       ", ".join(f"{lab}={reg}@{p:#x}" for lab, p, reg, _ in members))
        if floats:
            msg.append(f"atlas/UI-band float inside: {floats}")
        rep.add("label_render_consistency", False, "; ".join(msg))
    else:
        rep.add("label_render_consistency", True,
                f"ID-ability label group store-uniform ({next(iter(regions.values()))}), no floats "
                f"[{', '.join(lab for lab, *_ in members)}]")


def _scan_name_string(a9, foff, atlas_n, depth=0):
    """Render-path-accurate walk of a NUL-terminated name string.
    Returns (slots, issues); issues = out-of-atlas / truncated / bad-macro."""
    slots, issues = [], []
    i, n, guard = foff, len(a9), 0
    while i < n and a9[i] != 0 and guard < 400:
        guard += 1
        b = a9[i]
        if b < 0xE0:
            slots.append(b)
            i += 1
        elif b < 0xF0:
            if i + 1 >= n:
                issues.append(("truncated", i))
                break
            slot = ((b << 8) | a9[i + 1]) - SLOT_DEBIAS
            slots.append(slot)
            if slot >= atlas_n:
                issues.append(("oob_atlas", i, slot))
            i += 2
        else:
            if i + 1 >= n:
                issues.append(("truncated", i))
                break
            tok = ((b << 8) | a9[i + 1]) - 0xF000
            if PRIMARY_DICT_OFF + tok * 2 + 1 >= n:
                issues.append(("bad_macro", i, tok))
            elif depth < FREF_MAX_DEPTH:
                off = struct.unpack_from("<H", a9, PRIMARY_DICT_OFF + tok * 2)[0]
                s2, i2 = _scan_name_string(a9, PRIMARY_DICT_OFF + off, atlas_n, depth + 1)
                slots += s2
                issues += i2
            i += 2
    return slots, issues


def gate_unit_weapon_names(rep, ctx, update=False):
    """The per-unit master table feeds EVERY in-battle unit + weapon name.  Two
    teeth: (1) zero garbage (an out-of-atlas token = on-screen sparkle); (2) the
    count of translated (changed-vs-JP) names must never drop below the baseline
    floor (a name reverting to Japanese fails the build)."""
    aj, az = ctx["jp_a9"], ctx["a9"]
    atlas_n = ctx["atlas_slots"]
    cm = ctx["cm"]
    N = 0
    for utid in range(2000):
        if MASTER_TABLE_OFF + utid * MASTER_STRIDE + 4 > len(az):
            break
        p = struct.unpack_from("<I", az, MASTER_TABLE_OFF + utid * MASTER_STRIDE)[0]
        if _ram_to_file(az, p) is None and p != 0:
            break
        N = utid + 1
    units, weapons, garbage = {}, {}, []
    for utid in range(N):
        ro = MASTER_TABLE_OFF + utid * MASTER_STRIDE
        fields = [("unit", MASTER_NAME_OFF, units)]
        fields += [("weapon", MASTER_WPN_OFF + s * MASTER_WPN_STRIDE, weapons) for s in range(MASTER_WPN_N)]
        for kind, off, store in fields:
            p = struct.unpack_from("<I", az, ro + off)[0]
            f = _ram_to_file(az, p)
            if f is None or az[f] == 0 or p in store:
                continue
            slots, issues = _scan_name_string(az, f, atlas_n)
            cur = _read_string_any_bank(az, p)
            pj = struct.unpack_from("<I", aj, ro + off)[0]
            org = _read_string_any_bank(aj, pj)
            if issues:
                cls = "GARBAGE"
                garbage.append((kind, utid, issues[:2]))
            elif not slots:
                cls = "empty"
            elif org is not None and cur != org:
                cls = "ZH"
            else:
                oslots, _ = _scan_name_string(aj, _ram_to_file(aj, pj) or f, atlas_n)
                cls = "JP" if any(s in cm.kana_slots for s in oslots) else "SHARED"
            store[p] = cls
    zh_u = sum(1 for c in units.values() if c == "ZH")
    zh_w = sum(1 for c in weapons.values() if c == "ZH")
    jp_u = sum(1 for c in units.values() if c == "JP")
    jp_w = sum(1 for c in weapons.values() if c == "JP")
    base_path = GOLDEN / "names_baseline.json"
    base = json.loads(base_path.read_text()) if base_path.exists() else {}
    if update:
        base.update({"min_zh_units": zh_u, "min_zh_weapons": zh_w})
        base_path.write_text(json.dumps(base, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    fails = []
    if garbage:
        fails.append(f"{len(garbage)} GARBAGE name string(s), e.g. {garbage[0]}")
    if not update and base:
        if zh_u < base.get("min_zh_units", 0):
            fails.append(f"ZH unit names regressed: {zh_u} < baseline {base['min_zh_units']}")
        if zh_w < base.get("min_zh_weapons", 0):
            fails.append(f"ZH weapon names regressed: {zh_w} < baseline {base['min_zh_weapons']}")
    if fails:
        rep.add("unit_weapon_names", False, "; ".join(fails))
    else:
        rep.add("unit_weapon_names", True,
                f"{N} master records: {zh_u} ZH units / {zh_w} ZH weapons "
                f"({jp_u}/{jp_w} still-JP), 0 garbage"
                + (" [baseline captured]" if update else ""))


def gate_id_command_names(rep, ctx, update=False):
    """The ID-command table feeds every command NAME (the famous-quote box title),
    effect SUMMARY and effect DETAIL box.  Teeth: (1) zero garbage on the true
    render path; (2) the play-test squad's records must render Chinese; (3) the
    translated counts must never drop below the baseline floors."""
    az = ctx["a9"]
    atlas_n = ctx["atlas_slots"]
    base_path = GOLDEN / "names_baseline.json"
    base = json.loads(base_path.read_text()) if base_path.exists() else {}
    squad = base.get("id_command_squad", [48, 273, 274, 678])

    # verified renderB punctuation bytes (data/renderb_charset.json): a name
    # consisting ONLY of these is a deliberate silent/punct name (…………, ……？)
    # — translated content, not residual Japanese
    RB_PUNCT = {0x7C, 0xD9, 0xDB, 0xD8, 0x7A}

    def scan2(foff, depth=0):
        issues, has_zh = [], False
        all_punct = az[foff] != 0
        i, n = foff, len(az)
        while i < n and az[i] != 0:
            c = az[i]
            if c < 0xE0:
                if c not in RB_PUNCT:
                    all_punct = False
                i += 1
            elif c < 0xF0:
                if i + 1 >= n:
                    issues.append(("truncated", i))
                    break
                slot = ((c << 8) | az[i + 1]) - SLOT_DEBIAS
                all_punct = False
                if slot >= ZH_SLOT_MIN:
                    has_zh = True
                    if slot >= atlas_n:
                        issues.append(("oob_atlas", i, slot))
                i += 2
            else:
                if i + 1 >= n:
                    issues.append(("truncated", i))
                    break
                all_punct = False
                tok = ((c << 8) | az[i + 1]) - 0xF000
                if UI_DICT_OFF + tok * 2 + 1 >= n:
                    issues.append(("bad_macro", i, tok))
                elif depth < 6:
                    off = struct.unpack_from("<H", az, UI_DICT_OFF + tok * 2)[0]
                    i2, z2 = scan2(UI_DICT_OFF + off, depth + 1)
                    issues += i2
                    has_zh = has_zh or z2
                i += 2
        return issues, has_zh or all_punct

    offtab = [struct.unpack_from("<I", az, ID_CMD_DETAIL_OFFTAB + k * 4)[0] for k in range(256)]
    distinct = {}
    for idx in range(1500):
        ro = ID_CMD_TABLE_OFF + idx * ID_CMD_REC
        if ro + ID_CMD_REC > len(az):
            break
        p = struct.unpack_from("<I", az, ro + ID_CMD_NAME_OFF)[0]
        f = _ram_to_file(az, p)
        if f is None and p != 0:
            break
        if f is not None and az[f] != 0:
            distinct.setdefault(p, idx)
    cov = {k: [0, 0] for k in ("name", "summary", "detail")}
    garbage, per = [], {}
    for p, idx in distinct.items():
        ro = ID_CMD_TABLE_OFF + idx * ID_CMD_REC
        rec = {}
        for fieldname, foff in (
                ("name", _ram_to_file(az, p)),
                ("summary", _ram_to_file(az, struct.unpack_from("<I", az, ro + ID_CMD_SUMMARY_OFF)[0])),
                ("detail", _ram_to_file(az, ID_CMD_DETAIL_OFFTAB_RAM + offtab[az[ro + ID_CMD_DETAIL_IDX_OFF]]))):
            if foff is None or az[foff] == 0:
                rec[fieldname] = (False, 0)
                continue
            issues, zh = scan2(foff)
            rec[fieldname] = (zh, len(issues))
            cov[fieldname][1] += 1
            if zh:
                cov[fieldname][0] += 1
            if issues:
                garbage.append((idx, fieldname, issues[:2]))
        per[idx] = rec
    squad_fail = []
    for idx in squad:
        if idx not in per:
            squad_fail.append((idx, "record not found"))
        elif per[idx]["name"][1]:
            squad_fail.append((idx, "GARBAGE"))
        elif not per[idx]["name"][0]:
            squad_fail.append((idx, "still Japanese"))
    if update:
        base.update({"min_zh_id_names": cov["name"][0],
                     "min_zh_id_summaries": cov["summary"][0],
                     "min_zh_id_details": cov["detail"][0],
                     "id_command_squad": squad})
        base_path.write_text(json.dumps(base, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    fails = []
    if garbage:
        fails.append(f"{len(garbage)} GARBAGE string(s), e.g. rec#{garbage[0][0]} {garbage[0][1]}")
    if squad_fail:
        fails.append("squad gate: " + "; ".join(f"rec#{i} {w}" for i, w in squad_fail))
    if not update and base:
        for key, fld in (("min_zh_id_names", "name"), ("min_zh_id_summaries", "summary"),
                         ("min_zh_id_details", "detail")):
            if cov[fld][0] < base.get(key, 0):
                fails.append(f"ZH {fld} coverage regressed: {cov[fld][0]} < baseline {base[key]}")
    if fails:
        rep.add("id_command_names", False, "; ".join(fails))
    else:
        rep.add("id_command_names", True,
                f"{len(distinct)} distinct commands: name {cov['name'][0]}/{cov['name'][1]} ZH, "
                f"summary {cov['summary'][0]}/{cov['summary'][1]}, detail {cov['detail'][0]}/{cov['detail'][1]}, "
                f"0 garbage, squad {squad} ZH" + (" [baseline captured]" if update else ""))


def gate_bank_onebyte_regression(rep, ctx, update=False):
    """Trampoline text banks render one-byte codes from the renderB 8x16 JP UI
    font whose charset differs from the atlas (atlas 206 = 兵, renderB 206 =
    無) — the 吉翁海兵→吉翁海無 garble class.  This gate freezes, per record,
    the set of one-byte text tokens (0x20..0xDF) a record may contain: any NEW
    one-byte value on these surfaces fails the build.  Baseline:
    test/golden/bank_onebyte_baseline.json (captured from a repaired state
    whose one-byte inventory equals the JP/original-patch proven bytes)."""
    repo = Path(__file__).resolve().parent.parent
    surfaces = {
        "data/arenas/battle_name_pool.json": ("entries", "payload_hex", "offset"),
        "data/arenas/briefing_blobs.json": ("entries", "payload_hex", "offset"),
        "data/arenas/event_text_blocks.json": ("entries", "payload_hex", "offset"),
        "data/arenas/idcmd_detail_pool.json": ("entries", "payload_hex", "offset"),
        "data/arenas/resident_caves.json": ("entries", "payload_hex", "offset"),
        "data/arenas/ui_names_bank.json": ("entries", "payload_hex", "offset"),
        "data/files/battle/ability_cards.json": ("edits", "zh_hex", "offset"),
        "data/files/battle/command_effects.json": ("edits", "zh_hex", "offset"),
        "data/files/battle/special_abilities.json": ("edits", "zh_hex", "offset"),
        "data/files/battle/special_defenses.json": ("edits", "zh_hex", "offset"),
    }

    def one_bytes(hexs: str) -> list[int]:
        b = bytes.fromhex(hexs)
        vals, i = set(), 0
        while i < len(b):
            if b[i] >= 0xE0 and i + 1 < len(b):
                i += 2
                continue
            if 0x20 <= b[i] <= 0xDF:
                vals.add(b[i])
            i += 1
        return sorted(vals)

    current: dict[str, dict[str, list[int]]] = {}
    for rel, (list_key, hex_key, id_key) in surfaces.items():
        doc = json.loads((repo / rel).read_text())
        recs = {}
        for r in doc.get(list_key, []):
            hx = r.get(hex_key)
            if hx:
                recs[r[id_key]] = one_bytes(hx)
        current[rel] = recs

    base_path = GOLDEN / "bank_onebyte_baseline.json"
    if update or not base_path.exists():
        base_path.write_text(json.dumps(current, indent=0, sort_keys=True) + "\n")
        rep.add("bank_onebyte_regression", True,
                f"{sum(len(v) for v in current.values())} bank records [baseline captured]")
        return
    base = json.loads(base_path.read_text())
    bad = []
    for rel, recs in current.items():
        brecs = base.get(rel, {})
        for off, vals in recs.items():
            allowed = set(brecs.get(off, []))
            extra = [v for v in vals if v not in allowed]
            if extra:
                bad.append(f"{rel}@{off}: new one-byte token(s) {[hex(v) for v in extra]}")
    if bad:
        rep.add("bank_onebyte_regression", False,
                f"{len(bad)} record(s) grew renderB-unsafe one-byte tokens; first: {bad[0]}")
    else:
        rep.add("bank_onebyte_regression", True,
                f"{sum(len(v) for v in current.values())} bank records, "
                "one-byte inventory within baseline on every trampoline surface")


def gate_cutin_offset_table(rep, ctx):
    """The 名台词 pairing gate: every arm9 cut-in quote-table entry must point
    at an actual record start of the rebuilt 1dc.bin quote bank, the sentinel
    entry and the resource-size word must equal the bank length.  (The table
    is build-derived now; this gate proves the derivation against the bank
    the game actually loads — a stale/desynced table shows the WRONG
    character's famous quote on every cut-in.)"""
    az = ctx["a9"]
    bank = ctx["cand_file"]("1dc.bin")
    spec = json.loads((Path(__file__).resolve().parent.parent /
                       "data/ui/cutin_quote_offsets.json").read_text())
    base = int(spec["table"]["file_offset"], 16)
    count = spec["table"]["count"]
    szw_off = int(spec["resource_size_word"]["file_offset"], 16)
    starts, i = [0], 0
    TERM = b"\x00\x03\x00\x01"
    while True:
        j = bank.find(TERM, i)
        if j < 0:
            break
        nxt = j + 4
        nxt += (-nxt) % 4
        if nxt >= len(bank):
            break
        starts.append(nxt)
        i = nxt
    bad = [k for k, s in enumerate(starts)
           if struct.unpack_from("<I", az, base + k * 4)[0] != s]
    sentinel = struct.unpack_from("<I", az, base + (count - 1) * 4)[0]
    szw = struct.unpack_from("<I", az, szw_off)[0]
    fails = []
    if bad:
        fails.append(f"{len(bad)} stale offset(s), first index {bad[0]}")
    if len(starts) != count - 1:
        fails.append(f"bank has {len(starts)} records, table expects {count - 1}")
    if sentinel != len(bank):
        fails.append(f"sentinel {sentinel} != bank size {len(bank)}")
    if szw != len(bank):
        fails.append(f"resource-size word {szw} != bank size {len(bank)}")
    if fails:
        rep.add("cutin_offset_table", False, "; ".join(fails))
    else:
        rep.add("cutin_offset_table", True,
                f"{len(starts)}/{count - 1} quote offsets + sentinel + size word all agree")


def gate_idcmd_detail_integrity(rep, ctx):
    """The （效果）-line gate: for EVERY entry of the ID-command detail offset
    table, walk the record exactly like the in-battle renderer (stop at the
    first standalone 00 00) and require that the VISIBLE part contains the
    effect line whenever the JP original's visible part does.  Catches the
    forged-interior-terminator class (in-place zero padding hid the effect
    line on 159/256 detail views) — table pointers and strings can all be
    individually intact while the panel still renders nothing."""
    az, aj = ctx["a9"], ctx["jp_a9"]

    def visible(a9, foff, limit=0x400):
        """Bytes the renderer draws: from foff to the first standalone 00 00."""
        out = bytearray()
        i = 0
        while foff + i + 1 < len(a9) and i < limit:
            b = a9[foff + i]
            if b >= 0xE0:
                out += a9[foff + i:foff + i + 2]
                i += 2
                continue
            if b == 0x00 and a9[foff + i + 1] == 0x00:
                break
            out.append(b)
            i += 1
        return bytes(out)

    def has_effect(vis, a9):
        """True iff the visible stream contains an intact effect header:
        the 果 glyph token (效果/効果) — AND no token in the stream carries a
        forbidden 0x00 low byte (a seam/garble sign: the byte-walking reader
        would misparse it, e.g. the （官果） seam class)."""
        found = False
        i = 0
        while i < len(vis):
            if vis[i] >= 0xE0 and i + 1 < len(vis):
                if vis[i + 1] == 0x00:
                    return False                      # forbidden low-00 token
                slot = ((vis[i] << 8) | vis[i + 1]) - SLOT_DEBIAS
                ch = ctx["cm"].zh_rev.get(slot) or ctx["cm"].jp_slots.get(slot)
                if ch == "果":
                    found = True
                i += 2
            else:
                if vis[i] >= 0xF0:
                    # macro token: expands to the JP effect header
                    found = True
                i += 1
        return found

    n = missing = interior = 0
    for k in range(256):
        oz = struct.unpack_from("<I", az, ID_CMD_DETAIL_OFFTAB + k * 4)[0]
        ojp = struct.unpack_from("<I", aj, ID_CMD_DETAIL_OFFTAB + k * 4)[0]
        fz = _ram_to_file(az, ID_CMD_DETAIL_OFFTAB_RAM + oz)
        fj = _ram_to_file(aj, ID_CMD_DETAIL_OFFTAB_RAM + ojp)
        if fz is None or fj is None or aj[fj] == 0:
            continue
        n += 1
        vj, vz = visible(aj, fj), visible(az, fz)
        if has_effect(vj, aj) and not has_effect(vz, az):
            missing += 1
    if missing:
        rep.add("idcmd_detail_integrity", False,
                f"{missing}/{n} detail views lost their （效果） line "
                "(forged interior 00 00 truncates the record)")
    else:
        rep.add("idcmd_detail_integrity", True,
                f"{n} detail views: every JP effect line has a rendered ZH counterpart")


def gate_offline_coverage(rep, ctx):
    """Render EVERY text line of every surface through the offline pixel
    oracle (test/render_oracle.py, parity-anchored to live melonDS) and fail
    on any algorithmic defect: unknown/empty glyphs, stroke-box violations,
    renderB kana leaking into visible bank text (garble class), style mixing.
    Allowlist: test/golden/coverage_allowlist.json (documented artifacts)."""
    import subprocess
    repo = Path(__file__).resolve().parent.parent
    out = Path("/tmp/coverage_gate")
    r = subprocess.run(
        [sys.executable, str(repo / "test/coverage_render.py"),
         str(ctx["rom_path"]), "--out", str(out)],
        capture_output=True, text=True)
    if r.returncode not in (0,):
        rep.add("offline_coverage", False, f"coverage runner failed: {r.stderr[-200:]}")
        return
    findings = json.loads((out / "findings.json").read_text())
    allow_p = GOLDEN / "coverage_allowlist.json"
    allow = set(json.loads(allow_p.read_text())["sources"]) if allow_p.exists() else set()
    bad = [f for f in findings if f["source"] not in allow and not f.get("dead")]
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if bad:
        rep.add("offline_coverage", False,
                f"{len(bad)} unlisted finding(s); first: {bad[0]['source']} {bad[0]['issues'][:2]}")
    else:
        rep.add("offline_coverage", True,
                f"{tail}; all findings allowlisted ({len(findings)})")


# =============================================================================
# runner
# =============================================================================
def _atlas_slot_count(a9: bytes) -> int:
    fptr = struct.unpack_from("<I", a9, DIALOGUE_FONT_PTR_OFF)[0]
    if fptr == FONT_RAM_ORIGINAL:
        return 2196
    ls = struct.unpack_from("<I", a9, MP_LIST_START_OFF)[0]
    le = struct.unpack_from("<I", a9, MP_LIST_END_OFF)[0]
    for i in range((le - ls) // 12):
        ram, size, _ = struct.unpack_from("<III", a9, (ls - RAM_BASE) + i * 12)
        if ram == fptr:
            return size // GLYPH_CELL
    return 2196


def build_context(rom_path: Path, jp_path: Path):
    raw = Path(rom_path).read_bytes()
    cand = ndspy.rom.NintendoDSRom(raw)
    jp = load_rom(jp_path)
    a9 = arm9_image(cand)
    jp_a9 = arm9_image(jp)
    fc, fj = {}, {}

    def cand_file(name):
        if name not in fc:
            b = cand.getFileByName(name)
            fc[name] = bytes(b) if b is not None else None
        return fc[name]

    def jp_file(name):
        if name not in fj:
            b = jp.getFileByName(name)
            fj[name] = bytes(b) if b is not None else None
        return fj[name]

    allow_spec = json.loads((GOLDEN / "dialogue_jp_allowlist.json").read_text())
    speaker_spec = json.loads((GOLDEN / "speaker_name_cells.json").read_text())
    return {
        "raw": raw, "cand": cand, "jp": jp, "a9": a9, "jp_a9": jp_a9,
        "cand_file": cand_file, "jp_file": jp_file,
        "rom_path": Path(rom_path),
        "stg_names": stage_files(jp),          # the JP file set is the authoritative list
        "cm": Charmap(),
        "atlas_slots": _atlas_slot_count(a9),
        "dialogue_jp_allow": set(allow_spec["allow"].keys()),
        "speaker_cells": {int(k): v for k, v in speaker_spec["cells"].items()},
    }


def gate_pool_trampoline_tokens(rep, ctx):
    """Every REFERENCED name-pool string (weapons/units/pilots/abilities/
    id-commands via their table ptrs) renders on the TRAMPOLINE path: a
    2-byte token with slot < 2196 draws the renderB glyph of that slot
    number — a different character (the 多佛炮→多恩炮 garble class).  ZERO
    JP-band 2-byte tokens are allowed in any referenced pool string.
    (One-byte bytes are covered by bank_onebyte_regression.)"""
    cm = ctx["cm"]
    refs = []
    for rel in ("names/weapons.json", "names/units.json", "names/pilots.json",
                "names/abilities.json", "names/id_commands.json"):
        p = REPO / "data" / rel
        if not p.exists():
            continue
        doc = json.loads(p.read_text())
        stack = [doc]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "ptr" and isinstance(v, str):
                        refs.append((rel, int(v, 16)))
                    elif isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(x, list):
                stack.extend(x)
    az = ctx["a9"]
    bad = []
    for rel, ptr in refs:
        f = _ram_to_file(az, ptr)
        if f is None:
            continue
        i, n = f, len(az)
        while i < n - 1 and az[i] != 0:
            c = az[i]
            if c < 0xE0:
                i += 1
                continue
            if c >= 0xF0:
                i += 2
                continue
            slot = ((c << 8) | az[i + 1]) - SLOT_DEBIAS
            # Only ZH-INTENT tokens garble: a slot registered in two_byte_zh
            # promises the ATLAS glyph, but the trampoline draws the renderB
            # glyph of the same number.  Original-JP tokens (jp_slot_chars
            # identity only) draw their intended renderB glyph — correct for
            # untranslated/intentionally-JP strings.
            if slot < ZH_SLOT_MIN and slot in cm.zh_minted_slots:
                ch = cm.zh_rev.get(slot) or "?"
                bad.append((rel, f"0x{ptr:X}", slot, ch))
            i += 2
    if bad:
        rel0, p0, s0, c0 = bad[0]
        rep.add("pool_trampoline_tokens", False,
                f"{len(bad)} referenced pool string(s) carry JP-band 2-byte tokens "
                f"(renderB garble); e.g. {rel0} @{p0}: slot {s0} ({c0})")
    else:
        rep.add("pool_trampoline_tokens", True,
                f"{len(refs)} referenced name-pool strings: 0 JP-band 2-byte tokens "
                f"(trampoline-safe)")


def gate_name_pointer_band(rep, ctx):
    """The 出击/deploy freeze guard.  Every unit-name (master 0xB94BC +0x00) and
    pilot/character-name (char-DB 0xDCF18 +0x04) pointer in the built ROM must
    resolve BELOW 0x02190000.  The deploy unit-name path data-aborts (hard freeze)
    and the affinity/nameplate reader renders blank on a name pointer >=
    0x02190000 — the 0x0219 resident sub-band and the autoload pools (pool A
    0x0232.., pool B 0x023E..).  This is the exact v1.1 regression: unit/pilot
    names were relocated into the 0x0219 caves ([0x190030,0x190870) /
    [0x1945B3,0x194852)) and pool A, so deploying e.g. 卡碧尼Mk2 froze the game.
    Effect summaries/details and weapon names use lenient accessors and may live
    >= 0x02190000 (not checked).  Pre-existing JP dummy (欠番) records carry
    out-of-RAM junk pointers (>= 0x02400000) the engine never dereferences and
    that are byte-identical to JP — excluded by the danger-band upper bound."""
    az = ctx["a9"]

    def band_bad(p):
        return NAME_PTR_SAFE_HI <= p < NAME_PTR_DANGER_HI

    bad_u = []
    for utid in range(MASTER_MAX):
        ro = MASTER_TABLE_OFF + utid * MASTER_STRIDE + MASTER_NAME_OFF
        if ro + 4 > len(az):
            break
        p = struct.unpack_from("<I", az, ro)[0]
        if band_bad(p):
            bad_u.append((utid, p))
    bad_p = []
    for cid in range(CHAR_DB_FULL_COUNT):
        ro = CHAR_DB_OFF + cid * CHAR_DB_STRIDE + 4
        if ro + 4 > len(az):
            break
        p = struct.unpack_from("<I", az, ro)[0]
        if band_bad(p):
            bad_p.append((cid, p))
    if bad_u or bad_p:
        ex = ""
        if bad_u:
            u0 = bad_u[0]
            ex = f" e.g. unit utid {u0[0]} -> {u0[1]:#010x}"
        elif bad_p:
            p0 = bad_p[0]
            ex = f" e.g. char {p0[0]} -> {p0[1]:#010x}"
        rep.add("name_pointer_band", False,
                f"{len(bad_u)} unit-name + {len(bad_p)} pilot-name pointer(s) resolve "
                f">= 0x02190000 -> 出击 deploy HARD-FREEZE / blank nameplate.{ex}")
    else:
        rep.add("name_pointer_band", True,
                f"all unit-name (master+0x00) and pilot-name (charDB+0x04) pointers "
                f"resolve < 0x02190000 (deploy/nameplate-safe)")


GATES = [
    gate_audio_header,
    gate_ui_text_dispatch,
    gate_nameplate_render_path,
    gate_ui_font_atlas_dispatch,
    gate_code_image_parity,
    gate_dialogue_dict_frozen,
    gate_font_relocation,
    gate_relocated_pointer_sanity,
    gate_charmap_font_consistency,
    gate_glyph_style_uniformity,
    gate_stage_header_alignment,
    gate_stage_file_structure,
    gate_stage_script_integrity,
    gate_inline_dialogue_blocks,
    gate_event_script_pointers,
    gate_battle_voice_structure,
    gate_bark_framing,
    gate_untranslated_dialogue,
    gate_translation_coverage,
    gate_glyph_width,
    gate_field_width_budgets,
    gate_label_render_consistency,
    gate_unit_weapon_names,
    gate_id_command_names,
    gate_name_pointer_band,
    gate_bank_onebyte_regression,
    gate_pool_trampoline_tokens,
    gate_cutin_offset_table,
    gate_idcmd_detail_integrity,
    gate_offline_coverage,
]
RATCHET_GATES = {gate_translation_coverage, gate_unit_weapon_names, gate_id_command_names,
                 gate_bank_onebyte_regression}


def run_all(rom_path: Path, jp_path: Path, update=False) -> Report:
    print(f"=== static gate suite :: {Path(rom_path).name} ===", flush=True)
    print(f"    JP source oracle  :: {Path(jp_path).name}", flush=True)
    rep = Report()
    try:
        ctx = build_context(rom_path, jp_path)
    except Exception as e:
        rep.add("context", False, f"could not load ROMs/baselines: {type(e).__name__}: {e}")
        return rep
    for gate in GATES:
        t0 = time.time()
        try:
            if gate in RATCHET_GATES:
                gate(rep, ctx, update)
            else:
                gate(rep, ctx)
        except Exception as e:            # a crashing gate is a FAIL, never an abort
            rep.add(gate.__name__.replace("gate_", ""), False,
                    f"gate raised {type(e).__name__}: {e}")
        _ = t0
    return rep


# =============================================================================
# --self-test: prove the gates have teeth (RED on damage, GREEN on the build)
# =============================================================================
def self_test(rom_path: Path, jp_path: Path) -> int:
    """Mutate a copy of the ROM under test in targeted ways and require the right
    gate to go RED; also run the translation gates on the JP source and require
    them RED.  This guards the guards: a gate that cannot fail protects nothing."""
    import copy
    print("--- gate-teeth self-test ---", flush=True)
    rc = 0

    def expect_fail(label, gate_names, mutate):
        nonlocal rc
        ctx = build_context(rom_path, jp_path)
        mutate(ctx)
        rep = Report()
        print(f"[red-check] {label}:")
        for gate in GATES:
            nm = gate.__name__.replace("gate_", "")
            if nm not in gate_names:
                continue
            try:
                if gate in RATCHET_GATES:
                    gate(rep, ctx, False)
                else:
                    gate(rep, ctx)
            except Exception as e:
                rep.add(nm, False, f"raised {e}")
        failed = {n for n, st, _ in rep.rows if st == "FAIL"}
        want = set(gate_names)
        if want & failed:
            print(f"    OK — {sorted(want & failed)} went RED as required")
        else:
            print(f"    *** TEETH MISSING: none of {sorted(want)} failed ***")
            rc = 1

    def mut_a9(ctx, off, new):
        a = bytearray(ctx["a9"])
        a[off:off + len(new)] = new
        ctx["a9"] = bytes(a)

    expect_fail("NOP the UI text dispatch (the garble regression)",
                ["ui_text_dispatch"], lambda c: mut_a9(c, UI_DISPATCH_OFF, UI_DISPATCH_NOP))
    expect_fail("flip a byte inside the dialogue dictionary",
                ["dialogue_dict_frozen"],
                lambda c: mut_a9(c, PRIMARY_DICT_OFF + 0x40,
                                 bytes([c["a9"][PRIMARY_DICT_OFF + 0x40] ^ 0xFF])))
    expect_fail("flip a byte of combat code (outside every allowed region)",
                ["code_image_parity"],
                lambda c: mut_a9(c, 0x40000, bytes([c["a9"][0x40000] ^ 0xFF])))
    expect_fail("corrupt the stage-VM dispatch code",
                ["stage_script_integrity", "code_image_parity"],
                lambda c: mut_a9(c, VM_REGION[0] + 8, b"\x00\x00"))
    expect_fail("point an ID-command title into the runtime-heap window",
                ["field_width_budgets"],
                lambda c: mut_a9(c, ID_CMD_TABLE_OFF + 273 * ID_CMD_REC,
                                 struct.pack("<I", 0x0232D000)))
    expect_fail("relocate a unit name into the 0x0219 band (the 出击 deploy freeze)",
                ["name_pointer_band"],
                lambda c: mut_a9(c, MASTER_TABLE_OFF + 184 * MASTER_STRIDE + MASTER_NAME_OFF,
                                 struct.pack("<I", 0x02190663)))
    expect_fail("relocate a pilot name into pool A (blank nameplate)",
                ["name_pointer_band"],
                lambda c: mut_a9(c, CHAR_DB_OFF + 419 * CHAR_DB_STRIDE + 4,
                                 struct.pack("<I", 0x0232873E)))

    def mut_stg(ctx, corrupt):
        real = ctx["cand_file"]

        def cand_file(name, _real=real, _corrupt=corrupt):
            d = _real(name)
            if name == "_STG00.bin" and d is not None:
                return _corrupt(bytearray(d))
            return d
        ctx["cand_file"] = cand_file

    def corrupt_event_call(d):
        # overwrite a scene-entry pointer table word -> CFG diverges from JP
        struct.pack_into("<I", d, 8, STG_BASE + 0x20)
        return bytes(d)
    expect_fail("corrupt a stage file's event control flow",
                ["stage_script_integrity"], lambda c: mut_stg(c, corrupt_event_call))

    def mut_glyph(ctx):
        # erase the drop shadow of one ZH glyph (paint value-2 pixels to 0)
        a = bytearray(ctx["a9"])
        atlas = _atlas_bytes(bytes(a))
        # find the atlas source offset again to mutate in place
        ls = struct.unpack_from("<I", a, MP_LIST_START_OFF)[0]
        le = struct.unpack_from("<I", a, MP_LIST_END_OFF)[0]
        src = struct.unpack_from("<I", a, 0xB14)[0] - RAM_BASE
        off = src
        for i in range((le - ls) // 12):
            ram, size, _b = struct.unpack_from("<III", a, (ls - RAM_BASE) + i * 12)
            fptr = struct.unpack_from("<I", a, DIALOGUE_FONT_PTR_OFF)[0]
            if ram == fptr:
                break
            off += size
        # slot 2196 (first ZH glyph): strip value-2 bits
        cell_off = off + 2196 * GLYPH_CELL
        for k in range(GLYPH_CELL):
            b = a[cell_off + k]
            for p in range(4):
                if (b >> (p * 2)) & 3 == 2:
                    b &= ~(3 << (p * 2))
            a[cell_off + k] = b
        ctx["a9"] = bytes(a)
    expect_fail("strip the drop shadow from a ZH glyph (mixed-weight ghost text)",
                ["glyph_style_uniformity"], mut_glyph)

    def mut_bark(ctx):
        real = ctx["cand_file"]
        jp0 = ctx["jp_file"]("0.bin")
        _, terms = _bv_headers_terms(jp0)
        gap_off = None
        for t in terms:
            j = t + 4
            if j < len(jp0) and jp0[j] == 0:
                gap_off = j
                break

        def cand_file(name, _real=real, _off=gap_off):
            d = _real(name)
            if name == "0.bin" and d is not None and _off is not None:
                b = bytearray(d)
                b[_off] = 0xE1
                return bytes(b)
            return d
        ctx["cand_file"] = cand_file
    expect_fail("write a stray byte into a bark framing gap",
                ["bark_framing"], mut_bark)

    # translation gates must be RED on the untranslated JP source itself
    print("[red-check] JP source ROM through the translation gates:")
    ctx = build_context(jp_path, jp_path)
    rep = Report()
    for gate in (gate_untranslated_dialogue, gate_translation_coverage,
                 gate_unit_weapon_names, gate_id_command_names):
        nm = gate.__name__.replace("gate_", "")
        try:
            if gate in RATCHET_GATES:
                gate(rep, ctx, False)
            else:
                gate(rep, ctx)
        except Exception as e:
            rep.add(nm, False, f"raised {e}")
    jp_failed = {n for n, st, _ in rep.rows if st == "FAIL"}
    if {"untranslated_dialogue", "translation_coverage"} <= jp_failed:
        print("    OK — the untranslated JP ROM is RED on the translation gates")
    else:
        print("    *** TEETH MISSING: JP ROM passed a translation gate ***")
        rc = 1

    print(f"--- self-test {'PASS' if rc == 0 else 'FAIL'} ---")
    return rc


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", help="the built ROM to gate")
    ap.add_argument("--jp", default=str(DEFAULT_JP), help="the Japanese source ROM")
    ap.add_argument("--update-baselines", action="store_true",
                    help="recapture the ratchet baselines in test/golden/ from this ROM")
    ap.add_argument("--self-test", action="store_true",
                    help="prove gate teeth (RED checks on mutated images + the JP ROM)")
    ap.add_argument("--json", default=None, help="also write results as JSON")
    a = ap.parse_args()
    rom_path = Path(a.rom)
    jp_path = Path(a.jp)
    if not rom_path.exists():
        print(f"ROM not found: {rom_path}", file=sys.stderr)
        return 2
    if not jp_path.exists():
        print(f"JP source ROM not found: {jp_path} (pass --jp)", file=sys.stderr)
        return 2
    if a.self_test:
        return self_test(rom_path, jp_path)

    t0 = time.time()
    rep = run_all(rom_path, jp_path, a.update_baselines)
    npass = sum(1 for _, st, _ in rep.rows if st == "PASS")
    nskip = sum(1 for _, st, _ in rep.rows if st == "SKIP")
    print("\n=== SUMMARY ===", flush=True)
    for name, st, _detail in rep.rows:
        print(f"  {st:4}  {name}", flush=True)
    verdict = "ALL PASS" if rep.ok else "FAIL"
    print(f"\n{npass} passed / {nskip} skipped / "
          f"{sum(1 for _, st, _ in rep.rows if st == 'FAIL')} failed "
          f"in {time.time() - t0:.1f}s -> {verdict}", flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(
            [{"gate": n, "status": st, "detail": d} for n, st, d in rep.rows],
            indent=1, ensure_ascii=False) + "\n")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
