#!/usr/bin/env python3
"""Shared machinery for validating + applying the v1.2 fleet translations.

Everything here follows AGENTS.md:
  * per-surface encoding via utils.text_codec (bank vs stage), with the
    original-JP-payload one-byte allowance and JP-token-run reuse (G9) for
    trampoline records;
  * pooled-string growth ONLY through data/zh/placements/relocation_ledger.json
    (ui-bank heap-safe gaps -> resident-cave zero runs -> ledger-vacated spans);
  * unit/pilot-name pointers stay < 0x02190000 (name_pointer_band);
  * ID-command box titles <= 64px at trampoline advances.

This module never writes files by itself; appliers build new JSON contents and
the driver writes them in one reviewed pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from utils import text_codec  # noqa: E402
from utils.extract import layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from utils.extract.gamerom import GameROM, u16, u32  # noqa: E402
from utils.extract.identities import load_identities  # noqa: E402

CM = text_codec.load_charmap()
IDENT = load_identities()

JP_ROM_PATH = REPO / "0098 - SD Gundam G Generation DS (Japan).nds"
# The campaign BASELINE ZH ROM: the state all briefs/zh_current/arena images
# were captured from (v1.1 data, pre-fleet-apply).  Pinned to a fixed path so
# rebuilding the working ROM after an apply can never shift the baseline the
# appliers diff against (a moved baseline makes replays skip/misplace).
# Regenerate when the campaign REBASES: build from the pre-apply commit's
# data/ (audit/translate2/make_baseline_rom.sh).
import os
ZH_ROM_PATH = Path(os.environ.get("ZH_ROM", REPO / "sd-gundam-g-generation-zh.nds"))
ZH_BASELINE_ROM = REPO / "audit/translate2/staging/baseline_v11.nds"
if ZH_BASELINE_ROM.exists() and "ZH_ROM" not in os.environ:
    ZH_ROM_PATH = ZH_BASELINE_ROM

RAM_BASE = 0x02000000
UI_BANK_RAM = 0x02328720
UI_BANK_SAFE = 0x40E0            # heap-safe references live below this offset
CAVES_FILE_LO = 0x186000         # resident_caves.json file_offset
CAVES_FILE_HI = 0x1985A4
NAME_BAND_LIMIT = 0x02190000     # unit/pilot name pointers must stay below

# proven-safe cave sub-ranges for unit/pilot NAMES (ROM_STRUCTURE 4.3)
NAME_SAFE_RANGES = ((0x18870C, 0x18BBE2), (0x18BF7A, 0x18CB5C), (0x18E47E, 0x18F47E))

# the code_image_parity gate's allowed resident-name-pool window: allocations
# must stay inside [0x188000, 0x198712) (test/golden/arm9_allowed_regions.json)
CAVES_ALLOC_LO = 0x188000 - CAVES_FILE_LO       # arena-relative
CAVES_ALLOC_HI = 0x198712 - CAVES_FILE_LO

DETAIL_BASE = 0xF9048            # didx string = base + offtab[didx]
DETAIL_POOL_LO = 0xF9449
DETAIL_POOL_HI = 0xFC643


def _i(v):
    return v if isinstance(v, int) else int(str(v), 16)


def load_json(rel):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def dump_json(rel, obj):
    p = REPO / rel
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# glyph width / cost helpers
# ---------------------------------------------------------------------------
def payload_px_bank(b: bytes) -> int:
    """Trampoline advance: 2-byte ZH-band = 12px, everything else 8px."""
    w = 0
    for _o, tok, ln in text_codec.iter_tokens(b):
        if ln == 1:
            if tok not in (0x00, 0x01):
                w += 8
        elif tok >= 0xF000:
            w += 8  # macros in names are narrow JP runs; conservative
        else:
            slot = tok - 0xE000 + 224
            w += 12 if slot >= 2196 else 8
    return w


def glyph_cells(b: bytes) -> int:
    return sum(1 for _o, t, _l in text_codec.iter_tokens(b) if t not in (0x00, 0x01))


# ---------------------------------------------------------------------------
# bank-surface encoder with JP-token reuse (G9)
# ---------------------------------------------------------------------------
def _bank_char_of_token(tok: int, ln: int) -> str | None:
    """Decoded identity of one token on the trampoline (renderB for one-byte
    and JP-band; atlas for ZH-band)."""
    if ln == 1:
        return IDENT.renderb.get(tok)
    if tok >= 0xF000:
        return None
    slot = tok - 0xE000 + 224
    if slot >= 2196:
        return IDENT.atlas_zh.get(slot)
    return IDENT.renderb.get(slot)


_W2N = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}  # fullwidth -> ASCII


def _norm(ch: str) -> str:
    return _W2N.get(ch, ch)


def jp_token_map(jp_payload: bytes, expander=None) -> dict[str, bytes]:
    """char -> original JP byte form for every NON-CJK glyph in the JP payload
    (macros included as whole tokens when their expansion is pure non-CJK)."""
    out: dict[str, bytes] = {}
    for off, tok, ln in text_codec.iter_tokens(jp_payload):
        if ln == 1 and tok in (0x00, 0x01):
            continue
        if ln == 2 and tok >= 0xF000:
            sub = expander(tok - 0xF000) if expander else None
            if sub:
                txt = "".join(_bank_char_of_token(t, l) or "?"
                              for _o, t, l in text_codec.iter_tokens(sub)
                              if t not in (0x00, 0x01))
                if txt and all(not _is_cjk(c) for c in txt):
                    out.setdefault("\x00MACRO:" + "".join(_norm(c) for c in txt),
                                   jp_payload[off:off + 2])
            continue
        ch = _bank_char_of_token(tok, ln)
        if ch is None or _is_cjk(ch):
            continue
        out.setdefault(_norm(ch), jp_payload[off:off + ln])
    return out


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF


def encode_bank(text: str, jp_payload: bytes = b"", expander=None,
                allow_low15: bool = True) -> bytes:
    """Encode for a trampoline surface. Per char: reuse the record's ORIGINAL
    JP byte form for identical non-CJK chars (narrow + provably safe), else
    ZH-band. Raises ValueError with the failing char when unencodable."""
    jmap = jp_token_map(jp_payload, expander) if jp_payload else {}
    allowed = frozenset(b for b in jp_payload if b < 0xE0) if jp_payload else frozenset()
    # whole-run dictionary macros from the JP payload ("DNA", "SEED", …):
    # longest-match them against the normalized text
    macros = sorted(((k[len("\x00MACRO:"):], v) for k, v in jmap.items()
                     if k.startswith("\x00MACRO:")), key=lambda kv: -len(kv[0]))
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":                       # explicit escape from staging data
            j = text.index("}", i)
            body = text[i + 1:j]
            i = j + 1
            if body.startswith("F0:"):
                tok = 0xF000 + int(body[3:])
                out += bytes([tok >> 8, tok & 0xFF])
            elif body.startswith("SLOT:"):
                out += text_codec.encode_slot(int(body[5:]))
            else:
                out.append(int(body, 16))
            continue
        hit = None
        for mtext, mbytes in macros:
            if mtext and "".join(_norm(c) for c in text[i:i + len(mtext)]) == mtext:
                hit = (mtext, mbytes)
                break
        if hit is not None:
            out += hit[1]
            i += len(hit[0])
            continue
        i += 1
        # per-char reuse: identical non-CJK char from the JP payload
        n = _norm(ch)
        if not _is_cjk(ch) and n in jmap:
            out += jmap[n]
            continue
        b = text_codec.encode_char(ch, CM, allow_low15=allow_low15,
                                   surface="bank", allowed_one_bytes=allowed)
        if b is None:
            raise ValueError(f"unencodable char {ch!r} on bank surface")
        out += b
    return bytes(out)


def encode_stage(text: str, allow_low15: bool = True) -> bytes:
    return text_codec.encode(text, CM, allow_low15=allow_low15, surface="stage")


# ---------------------------------------------------------------------------
# line reflow (stage surfaces: cutins / details)
# ---------------------------------------------------------------------------
BREAK_AFTER = "、。！？…・）%"
NO_LINE_START = "、。！？…）』」"     # a display line must not open with these


def _split_points(text: str) -> list[int]:
    """Candidate break indices (break AFTER index i), preferring punctuation
    but allowing any position that doesn't strand a forbidden line-start."""
    pts = []
    for i in range(len(text) - 1):
        if text[i + 1] in NO_LINE_START:
            continue
        pts.append(i + 1)
    return pts


def reflow(text: str, max_cells: int, max_lines: int) -> list[str] | None:
    """Split text into <=max_lines lines of <=max_cells glyphs.

    Optimal search (few chars, tiny space): minimize line count, then prefer
    breaks after punctuation, then balance line lengths."""
    if not text:
        return []
    n = len(text)
    if n <= max_cells:
        return [text]
    pts = _split_points(text)
    best = None

    def score(lines):
        punct = sum(1 for l in lines[:-1] if l and l[-1] in BREAK_AFTER)
        spread = max(len(l) for l in lines) - min(len(l) for l in lines)
        return (-punct, spread)

    import itertools
    for k in range(1, max_lines):          # k breaks -> k+1 lines
        cands = []
        for combo in itertools.combinations(pts, k):
            idx = (0,) + combo + (n,)
            lines = [text[idx[i]:idx[i + 1]] for i in range(len(idx) - 1)]
            if any(len(l) == 0 or len(l) > max_cells for l in lines):
                continue
            cands.append(lines)
        if cands:
            best = min(cands, key=score)
            break
    return best


# ---------------------------------------------------------------------------
# arena model: occupancy + free spans + ledger-mediated allocation
# ---------------------------------------------------------------------------
class Arena:
    """One placement pool file (entries at offsets over a byte image)."""

    def __init__(self, rel: str, file_offset_key="file_offset"):
        self.rel = rel
        self.data = load_json(rel)
        self.is_bank = "ram_base" in self.data
        if self.is_bank:
            self.base_ram = _i(self.data["ram_base"])
            self.size = _i(self.data["size"])
            self.base_file = None
        else:
            self.base_file = _i(self.data["file_offset"])
            self.end_file = _i(self.data["end"])
            self.size = self.end_file - self.base_file
            self.base_ram = RAM_BASE + self.base_file
        self.entries = {int(e["offset"], 16): e for e in self.data["entries"]}

    def ram_of(self, off: int) -> int:
        return self.base_ram + off

    def off_of_ram(self, ram: int) -> int | None:
        off = ram - self.base_ram
        return off if 0 <= off < self.size else None

    def occupancy(self) -> list[tuple[int, int]]:
        spans = []
        for off, e in self.entries.items():
            spans.append((off, off + len(bytes.fromhex(e["payload_hex"]))))
        return sorted(spans)

    def entry_at(self, off: int):
        return self.entries.get(off)

    def put_entry(self, off: int, payload: bytes, text: str):
        e = self.entries.get(off)
        rec = {"offset": f"0x{off:X}", "text": text, "payload_hex": payload.hex()}
        if e is not None:
            e.update(rec)
        else:
            self.entries[off] = rec

    def remove_entry(self, off: int):
        self.entries.pop(off, None)

    def rebuild_json(self):
        self.data["entries"] = [self.entries[k] for k in sorted(self.entries)]
        return self.data


class Pools:
    """All four string arenas + ROM byte images + the relocation ledger."""

    def __init__(self, zh_rom: GameROM, jp_rom: GameROM):
        self.zh, self.jp = zh_rom, jp_rom
        self.ui = Arena("data/zh/placements/ui_names_bank.json")
        self.caves = Arena("data/zh/placements/resident_caves.json")
        self.bnp = Arena("data/zh/placements/battle_name_pool.json")
        self.pdl = Arena("data/zh/placements/post_dict_labels.json")
        self.ledger = load_json("data/zh/placements/relocation_ledger.json")
        self._alloc_marks = []          # new ledger entries this run

    # -- location helpers ---------------------------------------------------
    def arena_of_ram(self, ram: int):
        for a in (self.ui, self.caves, self.bnp, self.pdl):
            off = a.off_of_ram(ram)
            if off is not None:
                return a, off
        return None, None

    def current_len(self, ram: int) -> int:
        """Byte length (excl. NUL) of the live string at RAM in the built ROM."""
        s = self.zh.cstr(ram)
        return len(s) if s else 0

    # -- free space ---------------------------------------------------------
    def _ledger_spans(self, pool_rel: str):
        used, vacated = [], []
        for a in self.ledger["allocations"] + self._alloc_marks:
            tgt = a.get("pool") or ""
            if tgt.endswith(pool_rel):
                used.append((int(a["offset"], 16), int(a["offset"], 16) + a["reserved"]))
            vac = a.get("vacated", [])
            if not isinstance(vac, list):      # prose note ("left in place")
                continue
            for v in vac:
                if not isinstance(v, dict) or v.get("reused_by"):
                    continue
                if "offset" not in v or "span" not in v:
                    # ram-only retirement notes: derive offset when possible
                    if "ram" in v and (v.get("pool") or "").endswith(pool_rel):
                        base = {"ui_names_bank.json": UI_BANK_RAM,
                                "resident_caves.json": RAM_BASE + CAVES_FILE_LO,
                                "battle_name_pool.json": RAM_BASE + 0xB5000,
                                "post_dict_labels.json": RAM_BASE + 0x14AC34}[pool_rel]
                        off = int(v["ram"], 16) - base
                        vacated.append((off, off + v["span"]))
                    continue
                if (v.get("pool") or "").endswith(pool_rel):
                    vacated.append((int(v["offset"], 16), int(v["offset"], 16) + v["span"]))
        return used, vacated

    def free_spans(self, arena: Arena, lo=0, hi=None):
        """Maximal free runs: zero in the BUILT ROM image, not owned by any
        entry, not allocated in the ledger."""
        hi = hi if hi is not None else arena.size
        img = bytearray(hi)
        if arena.is_bank:
            # rebuild bank blob from entries (bank RAM isn't in the arm9 file)
            for off, e in arena.entries.items():
                p = bytes.fromhex(e["payload_hex"])
                if off < hi:
                    img[off:off + len(p)] = p[:max(0, hi - off)]
        else:
            img[0:hi] = self.zh.arm9[arena.base_file:arena.base_file + hi]
        occ = bytearray(hi)
        for a0, a1 in arena.occupancy():
            for k in range(max(lo, a0), min(hi, a1)):
                occ[k] = 1
        name = arena.rel.rsplit("/", 1)[-1]
        used, _vac = self._ledger_spans(name)
        for a0, a1 in used:
            for k in range(max(lo, a0), min(hi, a1)):
                occ[k] = 1
        spans, s = [], None
        for k in range(lo, hi):
            free = (img[k] == 0 and occ[k] == 0)
            if free and s is None:
                s = k
            elif not free and s is not None:
                spans.append((s, k))
                s = None
        if s is not None:
            spans.append((s, hi))
        return spans

    # -- allocation (G5 order) ----------------------------------------------
    def allocate(self, nbytes: int, why: str, ids: list, text: str,
                 name_band: bool = False, vacate: list | None = None):
        """Reserve nbytes (+1 NUL) via the ledger. Returns (arena, off, ram).

        Idempotent: a prior allocation with the same `fix` id is reused."""
        for a in self.ledger["allocations"] + self._alloc_marks:
            if a.get("fix") == why:
                rel = a["pool"].rsplit("/", 1)[-1]
                arena = {"ui_names_bank.json": self.ui,
                         "resident_caves.json": self.caves,
                         "battle_name_pool.json": self.bnp,
                         "post_dict_labels.json": self.pdl}[rel]
                return arena, int(a["offset"], 16), int(a["ram"], 16)
        need = nbytes + 1
        cand = []
        if not name_band:
            for s, e in self.free_spans(self.ui, 0x10, UI_BANK_SAFE):
                # never start flush at 0 and keep a 0 separator before
                if e - s >= need + 1:
                    cand.append((self.ui, s + 1, "ui_names_bank.json"))
                    break
        rngs = [(r[0] - CAVES_FILE_LO, r[1] - CAVES_FILE_LO) for r in NAME_SAFE_RANGES] \
            if name_band else [(CAVES_ALLOC_LO, CAVES_ALLOC_HI)]
        if not cand:
            spans = self.free_spans(self.caves)
            for lo, hi in rngs:
                for s, e in spans:
                    s2, e2 = max(s, lo), min(e, hi)
                    if e2 - s2 >= need + 1:
                        cand.append((self.caves, s2 + 1, "resident_caves.json"))
                        break
                if cand:
                    break
        vac_span = None
        if not cand:
            # ledger-vacated spans — but NEVER one that is already occupied
            # (an earlier allocation this run may have consumed it and written
            # an entry; the raw vacated list has no occupancy awareness — the
            # 新吉翁老兵/新吉翁NT same-span collision class)
            for arena, rel in ((self.ui, "ui_names_bank.json"),
                               (self.caves, "resident_caves.json"),
                               (self.bnp, "battle_name_pool.json")):
                if name_band and arena is not self.caves:
                    continue
                _u, vac = self._ledger_spans(rel)
                reserved = []
                for a in self._alloc_marks + self.ledger["allocations"]:
                    if a.get("pool") and str(a["pool"]).endswith(rel) \
                            and a.get("offset"):
                        o0 = int(a["offset"], 16)
                        reserved.append((o0, o0 + int(a.get("reserved")
                                                      or a.get("bytes") or 0)))
                for s, e in vac:
                    if name_band and not any(lo <= s and e <= hi for lo, hi in rngs):
                        continue
                    if e - s < need:
                        continue
                    # the span may still hold its STALE pre-vacate entry
                    # (overwritten on reuse); what must never overlap is a
                    # pending/committed ALLOCATION (the double-alloc class)
                    if any(a0 < e and a1 > s for a0, a1 in reserved):
                        continue
                    cand.append((arena, s, rel))
                    vac_span = (arena, s, e)
                    break
                if cand:
                    break
        if not cand:
            raise RuntimeError(f"no arena space for {need}B ({why})")
        arena, off, rel = cand[0]
        if vac_span is not None and vac_span[0] is arena:
            # a vacated span may still carry the OLD string's bytes beyond the
            # new reservation: zero the WHOLE span so no residue (e.g. a stray
            # kana tail) survives after the new NUL (offline_coverage class)
            _a, vs, ve = vac_span
            for eoff in sorted(arena.entries):
                e = arena.entries[eoff]
                p = bytearray(bytes.fromhex(e["payload_hex"]))
                lo, hi = max(eoff, vs), min(eoff + len(p), ve)
                if lo < hi:
                    p[lo - eoff:hi - eoff] = b"\x00" * (hi - lo)
                    e["payload_hex"] = bytes(p).hex()
        ram = arena.ram_of(off)
        if name_band and ram >= NAME_BAND_LIMIT:
            raise RuntimeError(f"allocation for {why} landed at {ram:#x} >= band limit")
        mark = {"date": "2026-07-17", "fix": why, "ids": ids, "text": text,
                "pool": f"data/zh/placements/{rel}" if "/" not in rel else rel,
                "offset": f"0x{off:X}", "ram": f"0x{ram:X}",
                "bytes": nbytes, "reserved": need}
        if vacate:
            mark["vacated"] = vacate
        self._alloc_marks.append(mark)
        return arena, off, ram

    def commit_ledger(self):
        self.ledger["allocations"].extend(self._alloc_marks)
        self._alloc_marks = []
        return self.ledger


def open_roms():
    return GameROM(JP_ROM_PATH), GameROM(ZH_ROM_PATH)
