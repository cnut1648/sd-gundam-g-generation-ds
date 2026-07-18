"""Game-array walkers: follow the SAME tables the console follows at runtime.

Every walker takes a `GameROM` (normally the JP source ROM — the single source
of truth) and returns keyed records carrying:

  * a stable key (utid / cid / idn / record index / file+offset),
  * the exact location (pointer-site file offset, string file offset, length),
  * the raw original bytes span (implicitly: [off, off+len) in the ROM),
  * a loss-aware per-surface transcription (see identities.decode_text).

Lifted and extended from build/build_guide.py's private extractor; several
walkers fix its known gaps (e.g. the master-table bound: unit records > 675
interleave the char-DB — stride 0xD8 == 3 x 0x48 with disjoint fields — and
are REAL units; the old (CHARDB-MASTER)//stride bound dropped 268 of them).
"""
from __future__ import annotations

import sys

from utils import text_codec

from . import layout as L
from .gamerom import GameROM, u16, u32
from .identities import decode_text, glyph_count, glyph_stream


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _hex(v: int) -> str:
    return f"0x{v:X}"


def _str_record(rom: GameROM, ptr_site: int, surface: str, expander) -> dict | None:
    """A pointer-carried string: site word -> {site, ptr, off, len, text}."""
    ptr = u32(rom.arm9, ptr_site)
    if ptr == 0:
        return None
    s = rom.cstr(ptr)
    if s is None:
        return None
    off = rom.file_off(ptr)
    return {
        "ptr_site": _hex(ptr_site),
        "ptr": _hex(ptr),
        "off": _hex(off) if off is not None else None,
        "len": len(s),
        "text": decode_text(rom, s, surface, expander),
    }


def text_runs(data: bytes, lo: int = 0, hi: int | None = None) -> list[tuple[int, int]]:
    """Token-aware (start, len) text runs inside [lo, hi): a run begins at the
    first glyph byte (>= 0x02 one-byte, or a 0xE0/0xF0 token) after a 0x00 (or
    at lo) and ends at the next standalone 0x00."""
    hi = len(data) if hi is None else hi
    runs: list[tuple[int, int]] = []
    i, start = lo, None
    while i < hi:
        b = data[i]
        if b >= 0xE0 and i + 1 < hi:
            if start is None:
                start = i
            i += 2
            continue
        if b == 0x00:
            if start is not None:
                runs.append((start, i - start))
                start = None
            i += 1
            continue
        if start is None and b >= 0x02:
            start = i
        i += 1
    if start is not None:
        runs.append((start, hi - start))
    return runs


# ---------------------------------------------------------------------------
# units / pilots / ID commands
# ---------------------------------------------------------------------------
def units(rom: GameROM) -> list[dict]:
    """All master-table unit records (name + 6 weapon names + carrier cap).

    The master table ENDS exactly where the char-DB begins (`L.CHARDB` =
    0xDCF18): its last record is utid 675 (脱出ポッド).  Slots at utid >= 676 are
    NOT units — the table's byte range would run straight into the char-DB
    (stride 0xD8 == 3 x char-DB stride 0x48), so a "unit name" read there is
    really a char-DB PILOT-name pointer (utid 682 -> アムロ・レイ) and the six
    "weapon" pointers are further pilot-name fields (キラ・ヤマト shows up as a
    weapon); past the char-DB (utid >= 864) the reads are pure garbage or bleed
    the special-defense type-name pool (分身 / Iフィールド — owned by the
    ability/defense walkers, not units).  The unit encyclopedia confirms the
    bound: the highest unit with a 図鑑 bio is utid 669.  (An earlier revision
    walked all 945 raw slots believing 268 extra units were being dropped; those
    "units" are exactly the char-DB aliases + garbage described above.)"""
    out = []
    for utid in range(L.MASTER_COUNT):
        rec = L.MASTER_TABLE + utid * L.MASTER_STRIDE
        if rec >= L.CHARDB:          # the master table ends where the char-DB begins
            break
        name = _str_record(rom, rec + L.UNIT_NAME_FIELD, "bank", rom.expand_sys)
        weapons = []
        for slot in range(L.WEAPONS_PER_UNIT):
            w = _str_record(rom, rec + L.WEAPON_BLOCK + slot * L.WEAPON_STRIDE,
                            "bank", rom.expand_sys)
            if w:
                w["slot"] = slot
                weapons.append(w)
        if name is None and not weapons:
            continue
        out.append({"utid": utid, "record": _hex(rec), "name": name,
                    "carrier_capacity": rom.arm9[rec + L.UNIT_CARRIER_CAP],
                    "weapons": weapons})
    return out


def pilots(rom: GameROM) -> list[dict]:
    """All character-DB records (name + voiceset)."""
    out = []
    for cid in range(L.CHARDB_COUNT):
        rec = L.CHARDB + cid * L.CHARDB_STRIDE
        name = _str_record(rom, rec + L.PILOT_NAME_FIELD, "bank", rom.expand_sys)
        if name is None:
            continue
        out.append({"cid": cid, "record": _hex(rec), "name": name,
                    "voiceset": u16(rom.arm9, rec + L.CHARDB_VOICESET)})
    return out


def id_command(rom: GameROM, idn: int) -> dict:
    """One ID-command record (idn = cid*3 + slot)."""
    rec = L.IDCMD_TABLE + idn * L.IDCMD_STRIDE
    didx = rom.arm9[rec + L.IDCMD_DIDX]
    return {
        "idn": idn,
        "record": _hex(rec),
        "name": _str_record(rom, rec + L.IDCMD_NAME, "bank", rom.expand_sys),
        "summary": _str_record(rom, rec + L.IDCMD_SUMMARY, "bank", rom.expand_sys),
        "target": rom.arm9[rec + L.IDCMD_TARGET],
        "cond": rom.arm9[rec + L.IDCMD_COND],
        "didx": didx,
    }


def id_details(rom: GameROM) -> list[dict]:
    """The 256-entry ID-command effect-detail pool (offtab-bounded records).

    A record nominally runs [offsets[didx], offsets[didx+1]) — the correct
    boundary for the packed JP pool, whose interior 00 00 pad runs make a raw
    find(00 00) scan under-run (see the guide's history).  The rebuilt ZH pool
    additionally ALIASES duplicate records (offsets[didx+1] <= offsets[didx]
    when the next slot back-references a shared record or is an empty
    sentinel): there the next-offset bound is meaningless and the record is
    walked exactly like the console renders it — to the first token-aware
    standalone 00 00.  min(bound-if-forward, terminator) covers both pools;
    a slot whose bytes strip to nothing (the 00-sentinel targets) is empty."""
    out = []
    for didx in range(L.DETAIL_OFFTAB_N - 1):
        start = u32(rom.arm9, L.DETAIL_OFFTAB + didx * 4)
        nxt = u32(rom.arm9, L.DETAIL_OFFTAB + (didx + 1) * 4)
        base = L.DETAIL_OFFTAB + start
        if base >= len(rom.arm9):
            continue
        bound = L.DETAIL_OFFTAB + nxt if nxt > start else None
        term = text_codec.find_terminator(rom.arm9, base)
        if bound is not None:
            end = term if 0 <= term < bound else bound
        else:
            # alias/sentinel slot: renderer semantics (terminator-only),
            # with the JP record cap as a runaway guard
            if term < 0 or term - base > 0x400:
                continue
            end = term
        if end <= base:
            continue
        rec = rom.arm9[base:end]
        stripped = rec.rstrip(b"\x00")
        if not stripped:
            continue
        out.append({"didx": didx, "off": _hex(base), "len": len(stripped),
                    "table_off": _hex(start),
                    "text": decode_text(rom, stripped, "stage", rom.expand)})
    return out


def cutin_records(rom: GameROM) -> list[dict]:
    """All cut-in famous-line (名台詞) records of the 1dc bank, plus the
    idn -> record link map."""
    dc = rom.file(L.CUTIN_FILE)
    recs = []
    for r in range(L.CUTIN_OFFTAB_N - 1):
        s0 = u32(rom.arm9, L.CUTIN_OFFTAB + 4 * r)
        s1 = u32(rom.arm9, L.CUTIN_OFFTAB + 4 * (r + 1))
        if not (0 <= s0 < s1 <= len(dc)):
            continue
        raw = dc[s0:s1]
        body = raw
        header = b""
        if body[:2] == b"\x00\x05":          # 00 05 + u16 quote-set id
            header, body = body[:4], body[4:]
        elif body[:2] == b"\x00\x04":        # headerless continuation form
            header, body = body[:2], body[2:]
        k = body.find(b"\x00\x03\x00\x01")   # record trailer
        if k >= 0:
            body = body[:k]
        body = body.rstrip(b"\x00")
        recs.append({"record": r + 1, "start": _hex(s0), "end": _hex(s1),
                     "header": header.hex(), "body_len": len(body),
                     "text": decode_text(rom, body, "stage", rom.expand, True)})
    links = {}
    for idn in range(L.IDCMD_COUNT):
        v = u16(rom.arm9, L.CUTIN_LINK + L.CUTIN_LINK_STRIDE * idn)
        if v:
            links[str(idn)] = v            # record number, 1-based
    return [{"records": recs, "links": links}]


# ---------------------------------------------------------------------------
# special abilities / defenses (1df / 1e0 global records + per-unit linkage)
# ---------------------------------------------------------------------------
def _offtab_records(rom: GameROM, offtab: int, fname: str, count_hint: int = 512):
    """Records of a bank addressed by a u32 offset table in arm9 (monotonic,
    ending at/before the file size).

    Several tables carry a non-offset word at index 0 (1df/1e0/b6f: the
    offset run starts at index 1 — the same +1 the game's own record readers
    use, cf. the ability drawer's offA[fam+1]/offA[fam+2]); the part NAME
    table starts at index 0.  Auto-detect: an index-0 word larger than index 1
    is not the first record offset.  The run is MOSTLY ascending but individual
    entries may be re-aimed anywhere in the file (a grown record relocated to
    an appended copy), so the walk stops only at the first out-of-file value;
    a record whose successor offset is not greater is bounded by EOF (the
    reader splits on 00 03 segments, mirroring the game's drawer)."""
    data = rom.file(fname)
    first = 1 if u32(rom.arm9, offtab) > u32(rom.arm9, offtab + 4) else 0
    vals = []
    for k in range(first, count_hint):
        v = u32(rom.arm9, offtab + 4 * k)
        if v > len(data):
            break
        vals.append(v)
    recs = []
    for k in range(len(vals) - 1):
        o0, o1 = vals[k], vals[k + 1]
        if o1 <= o0:
            o1 = len(data)
        recs.append((k, o0, o1))
    return data, recs


def special_records(rom: GameROM) -> dict:
    """Global 1df (special-ability) / 1e0 (special-defense) records with their
    00 03-delimited display segments."""
    out = {}
    for key, offtab, fname, cap in (
            ("ability", L.OFFTAB_A, L.SPECIAL_ABILITY_FILE, 512),
            ("defense", L.OFFTAB_D, L.SPECIAL_DEFENSE_FILE, L.OFFTAB_D_WORDS)):
        data, recs = _offtab_records(rom, offtab, fname, count_hint=cap)
        items = []
        for k, o0, o1 in recs:
            segs = []
            for part in data[o0:o1].split(b"\x00\x03"):
                part = part.strip(b"\x00")
                if part:
                    segs.append(decode_text(rom, part, "bank", rom.expand_sys))
            items.append({"index": k, "start": _hex(o0), "end": _hex(o1),
                          "segments": segs})
        out[key] = items
    return out


def unit_special_link(rom: GameROM, utid: int) -> dict:
    """Per-utid linkage into the special ability/defense banks (disasm of the
    drawers 0x2055AB4/0x2055BD8)."""
    fam = (utid - 1) // 3 if utid <= L.ABILITY_SPLIT else utid - L.ABILITY_SUB
    e = L.E71_TABLE + L.E71_STRIDE * utid
    link: dict = {}
    if fam >= 0:
        link["ability_family"] = fam
    if e + L.E71_STRIDE <= len(rom.arm9):
        tname = _str_record(rom, e + L.E71_DEFENSE_NAME, "bank", rom.expand_sys)
        if tname:
            link["defense_name"] = tname
        link["defense_record"] = rom.arm9[e + L.E71_DEFENSE_RIDX]
    return link


# ---------------------------------------------------------------------------
# battle effect banks (1da ability cards / 1db command effects)
# ---------------------------------------------------------------------------
def ability_cards(rom: GameROM) -> list[dict]:
    """1da records via the count-prefixed u32 offset table at 0x1775C8."""
    data = rom.file(L.ABILITY_CARD_FILE)
    n = u32(rom.arm9, L.ABILITY_CARD_OFFTAB)
    offs = [u32(rom.arm9, L.ABILITY_CARD_OFFTAB + 4 + 4 * k) for k in range(n)]
    out = []
    order = sorted(range(n), key=lambda k: offs[k])
    ends = {}
    for pos, k in enumerate(order):
        nxt = offs[order[pos + 1]] if pos + 1 < n else len(data)
        ends[k] = nxt
    for k in range(n):
        o0, o1 = offs[k], ends[k]
        if o1 <= o0 or o0 >= len(data):
            continue
        runs = [{"off": _hex(s), "len": ln,
                 "text": decode_text(rom, data[s:s + ln], "bank", rom.expand_sys)}
                for s, ln in text_runs(data, o0, min(o1, len(data)))]
        out.append({"index": k, "start": _hex(o0), "end": _hex(o1), "runs": runs})
    return out


def command_effects(rom: GameROM) -> list[dict]:
    """1db text runs (fixed layout; records read at precomputed offsets — no
    index table exists, so the token-aware run segmentation IS the universe)."""
    data = rom.file(L.COMMAND_EFFECT_FILE)
    return [{"off": _hex(s), "len": ln,
             "text": decode_text(rom, data[s:s + ln], "bank", rom.expand_sys)}
            for s, ln in text_runs(data)]


# ---------------------------------------------------------------------------
# barks (battle voice banks)
# ---------------------------------------------------------------------------
def barks(rom: GameROM) -> list[dict]:
    """All bark records across the 5 voice banks.

    Record grammar (docs/DATA_FORMATS): header ``00 05 <voiceset u16> 00 06
    <char_id u16>``, then text sub-lines separated by ``00 03`` / ``00 04``
    page controls, record terminated ``00 03 00 01`` (or the ``00 03 00 02``
    variant, 216 records) — or implicitly by the NEXT ``00 05 .. .. 00 06``
    header (a terminator-less chain member; the old end+4 advance skipped such a
    follower's header and lost whole records).
    Each sub-line text run is emitted with its own offset; `body`/`end` bound
    the record's translatable span (some shipped translations start their edit
    inside the header tail, so mapping is by containment in [body-2, end))."""
    out = []
    for fn in L.BARK_FILES:
        data = rom.file(fn)
        i, n = 0, len(data)

        def is_header(j: int) -> bool:
            return (j + 8 <= n and data[j] == 0x00 and data[j + 1] == 0x05
                    and data[j + 4] == 0x00 and data[j + 5] == 0x06)

        while i < n - 7:
            if not is_header(i):
                i += 1
                continue
            voiceset = u16(data, i + 2)
            char_id = u16(data, i + 6)
            body = i + 8
            # record end: 00 03 00 01 terminator, else the next header
            j = body
            end, terminated = n, False
            while j < n - 1:
                if data[j] >= 0xE0:
                    j += 2
                    continue
                if data[j] == 0x00 and j + 3 < n and data[j + 1] == 0x03 \
                        and data[j + 2] == 0x00 and data[j + 3] in (0x01, 0x02):
                    # record trailer is 00 03 00 01 (8534 records) OR 00 03 00 02
                    # (216 records); matching only 01 ran `end` to the NEXT header
                    # and swept the 00 03 00 02 trailer into the body, where the
                    # 02 decoded as a spurious 、 (the "！発射用意だ！！、" class)
                    end, terminated = j, True
                    break
                if is_header(j):
                    end = j
                    break
                j += 1
            runs = [{"off": _hex(s), "len": ln,
                     "text": decode_text(rom, data[s:s + ln], "stage", rom.expand)}
                    for s, ln in text_runs(data, body, end)
                    # drop pure control runs (03/04 page bytes between sub-lines)
                    if glyph_count(rom, data[s:s + ln], "stage") >= 1]
            out.append({"file": fn, "record": _hex(i), "voiceset": voiceset,
                        "cid": char_id, "body": _hex(body), "end": _hex(end),
                        "runs": runs})
            i = (end + 4) if terminated else end
    return out


# ---------------------------------------------------------------------------
# encyclopedia bios / weapon list / hangar parts
# ---------------------------------------------------------------------------
def bios(rom: GameROM, kind: str) -> list[dict]:
    """Encyclopedia biography records (kind='char' -> 324.bin, 'unit' -> c4b.bin),
    each keyed by its bio index in the arm9 offset table."""
    offtab, count, fname = ((L.CHAR_BIO_OFFTAB, L.CHAR_BIO_N, L.CHAR_BIO_FILE)
                            if kind == "char" else
                            (L.UNIT_BIO_OFFTAB, L.UNIT_BIO_N, L.UNIT_BIO_FILE))
    data = rom.file(fname)
    offs = [u32(rom.arm9, offtab + 4 * k) for k in range(count + 1)]
    out = []
    for k in range(count):
        o0 = offs[k]
        o1 = offs[k + 1] if k + 1 <= count and offs[k + 1] > o0 else len(data)
        if o0 >= len(data) or o1 <= o0:
            continue
        rec = data[o0:min(o1, len(data))]
        stripped = rec.rstrip(b"\x00")
        if not stripped:
            continue
        out.append({"index": k, "file": fname, "off": _hex(o0),
                    "size": o1 - o0, "len": len(stripped),
                    "text": decode_text(rom, stripped, "stage", rom.expand)})
    return out


def weapon_list(rom: GameROM) -> list[dict]:
    """31e.bin — the encyclopedia weapon-name list (00-separated names).  The
    file opens with a glyph-priming/index blob (a systematic kana/kanji table,
    not a real name) that draws out-of-atlas slots (>= 2196); mark it
    reachable:False like the stage/event walkers so downstream (gates, the
    phase-2 exporter) skips it while the dump stays honest."""
    data = rom.file(L.WEAPON_LIST_FILE)
    out = []
    for s, ln in text_runs(data):
        run = data[s:s + ln]
        entry = {"off": _hex(s), "len": ln,
                 "text": decode_text(rom, run, "stage", rom.expand)}
        if any(slot >= L.TRAMPOLINE_SPLIT
               for slot, _ in glyph_stream(rom, run, "stage")):
            entry["reachable"] = False
        out.append(entry)
    return out


def parts(rom: GameROM) -> list[dict]:
    """Hangar parts: name (b6e) + caption (b6f), paired 1:1 by part index via
    the two parallel arm9 offset tables (b6f carries a leading non-offset word,
    handled by ``_offtab_records``).

    Parts render on the TRAMPOLINE (the hangar list/detail 8x16 UI path — the
    caption-compose renderer is even code-patched for this, see
    data/patches/code_patches.json "parts-caption pad-clip"): slots < 2196 draw
    from the renderB 8x16 font and 0xF0xx macros resolve through the SYSTEM
    dictionary (DICT_SYS).  Decoding on the ``bank`` surface with ``expand_sys``
    yields the true text (e.g. ザク系変換パーツ / ジュピトリス製MS); the earlier
    ``stage``/DICT_TEXT decode produced garble and the false belief that a
    "parts-local runtime dictionary" existed outside the ROM."""
    nd, nrecs = _offtab_records(rom, L.PART_NAME_OFFTAB, L.PART_NAME_FILE,
                                L.PART_COUNT + 1)
    cd, crecs = _offtab_records(rom, L.PART_CAP_OFFTAB, L.PART_CAP_FILE,
                                L.PART_COUNT + 1)
    crec_by_k = {k: (o0, o1) for k, o0, o1 in crecs}
    out = []
    for k, o0, o1 in nrecs:
        name_raw = nd[o0:o1].rstrip(b"\x00")
        item = {"index": k,
                "name": {"file": L.PART_NAME_FILE, "off": _hex(o0), "size": o1 - o0,
                         "len": len(name_raw),
                         "text": decode_text(rom, name_raw, "bank", rom.expand_sys)}}
        if k in crec_by_k:
            c0, c1 = crec_by_k[k]
            cap_raw = cd[c0:c1].rstrip(b"\x00")
            item["caption"] = {"file": L.PART_CAP_FILE, "off": _hex(c0),
                               "size": c1 - c0, "len": len(cap_raw),
                               "text": decode_text(rom, cap_raw, "bank", rom.expand_sys)}
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# stage descriptors, briefing / event-text region
# ---------------------------------------------------------------------------
def stage_descriptors(rom: GameROM) -> list[dict]:
    """The 101-entry stage descriptor table: label, title, briefing start and
    the stage's script FILE NAME (in-ROM at +0x24 — no filename heuristics)."""
    out = []
    for k in range(L.STAGE_DESC_N):
        rec = L.STAGE_DESC + k * L.STAGE_DESC_STRIDE

        def txt(field):
            p = u32(rom.arm9, rec + field)
            s = rom.cstr(p)
            return decode_text(rom, s, "bank", rom.expand_sys) if s else ""

        fptr = u32(rom.arm9, rec + L.STAGE_DESC_FILE)
        fname = rom.cstr(fptr)
        brief = u32(rom.arm9, rec + L.STAGE_DESC_BRIEF)
        boff = rom.file_off(brief)
        out.append({"index": k,
                    "record": _hex(rec),
                    "file": fname.decode("ascii", "replace") if fname else "",
                    "label": txt(L.STAGE_DESC_LABEL),
                    "title": txt(L.STAGE_DESC_TITLE),
                    "brief_off": _hex(boff) if boff is not None else None})
    return out


def event_text_blocks(rom: GameROM) -> list[dict]:
    """All inline story-text blocks in the arm9 event region: ``0x15 <payload>
    00 00``, token-aware.  Briefing blocks (text-only blocks inside the
    briefing sub-region) are flagged and attributed to the descriptor whose
    +0x14 start is the greatest one <= the block offset."""
    a = rom.arm9
    by_start: dict[int, list[int]] = {}
    for k in range(L.STAGE_DESC_N):
        so = u32(a, L.STAGE_DESC + k * L.STAGE_DESC_STRIDE + L.STAGE_DESC_BRIEF) - L.RAM_BASE
        if L.BRIEF_LO <= so < L.BRIEF_HI:
            by_start.setdefault(so, []).append(k)
    start_list = sorted(by_start)

    def descs_of(off: int) -> list[int]:
        """ALL descriptors owning the greatest briefing start <= off — several
        stages legitimately share one briefing (00/00SP/01 share 0x198555)."""
        best = None
        for so in start_list:
            if so <= off:
                best = so
            else:
                break
        return sorted(by_start[best]) if best is not None else []

    def has_ptr(p: bytes) -> bool:
        for i in range(len(p) - 4):
            if p[i] in (0x13, 0x16):
                v = int.from_bytes(p[i + 1:i + 5], "little")
                if 0x02180000 <= v < 0x021B0000:
                    return True
        return False

    out = []
    i = L.EVENT_TEXT_LO
    while i < L.EVENT_TEXT_HI - 1:
        if a[i] != 0x15:
            i += 1
            continue
        t = text_codec.find_terminator(a, i + 1)
        if not (0 < t <= L.EVENT_TEXT_HI):
            i += 1
            continue
        payload = a[i + 1:t]
        # A false 0x15 landing inside event bytecode is not real display text:
        # it either embeds an event code pointer (0x13/0x16 -> event RAM) or
        # would draw an atlas slot >= 2196 (the JP atlas has only 2196 slots).
        # Mark such blocks reachable:False, exactly like the stage-block walker
        # marks non-VM-reached blocks, so the dump stays honest while every
        # downstream consumer (gates, phase-2/3 exporters) can skip them.
        spurious = has_ptr(payload) or any(
            s >= L.TRAMPOLINE_SPLIT
            for s, _ in glyph_stream(rom, payload, "stage"))
        is_brief = (not spurious and L.BRIEF_LO <= i < L.BRIEF_HI
                    and glyph_count(rom, payload, "stage") >= 3)
        entry = {"off": _hex(i), "len": t + 2 - i,
                 "text": decode_text(rom, a[i:t + 2], "stage", rom.expand, True)}
        if spurious:
            entry["reachable"] = False
        if is_brief:
            entry["briefing"] = True
            ks = descs_of(i)
            if ks:
                entry["descs"] = ks
        out.append(entry)
        i = t + 2
    return out


# ---------------------------------------------------------------------------
# stage script files: event-VM walk (play order) + linear block scan (universe)
# ---------------------------------------------------------------------------
def _stage_scene_entries(d: bytes) -> list[int]:
    """Scene-entry offsets: contiguous run of in-buffer u32 pointers at the
    file head (from 0x04), stopping at the first out-of-buffer word."""
    n = len(d)
    ents, i = [], 4
    while i + 4 <= n and len(ents) < L.STG_HEADER_MAX:
        p = u32(d, i)
        if L.STG_BASE <= p < L.STG_BASE + n:
            ents.append(p - L.STG_BASE)
            i += 4
        else:
            break
    return ents


def stage_block_order(d: bytes) -> list[tuple[int, int, bool]]:
    """Reachable DISPLAY blocks of a stage file in CONSOLE PLAY ORDER:
    (scene_index, block_offset, is_branch).  Ordered DFS over the event VM's
    control flow (GOTO reroutes; CALL/CGOTO recurse then fall through);
    is_branch marks blocks reached only across a CGOTO conditional edge."""
    n = len(d)
    end = L.STG_BASE + n
    order: list[tuple[int, int, bool]] = []
    seen: set[int] = set()
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

    def run(pc: int, scene: int, viacond: bool):
        while 0 <= pc < n and pc not in seen:
            seen.add(pc)
            op = d[pc]
            if op == L.STG_DISPLAY:
                t = text_codec.find_terminator(d, pc + 1)
                if t < 0:
                    return
                if t > pc + 1:
                    order.append((scene, pc, viacond))
                pc = t + 2
                continue
            if op == L.STG_RET:
                return
            if op > 0x19:
                pc += 1
                continue
            if op in L.STG_JUMP_OPS and pc + 5 <= n:
                tgt = u32(d, pc + 1)
                if L.STG_BASE <= tgt < end:
                    if op == L.STG_GOTO:
                        pc = tgt - L.STG_BASE
                        continue
                    run(tgt - L.STG_BASE, scene, viacond or op == L.STG_CGOTO)
                pc += 1 + L.STG_OPSZ.get(op, 0)
                continue
            pc += 1 + L.STG_OPSZ.get(op, 0)

    for scene, entry in enumerate(_stage_scene_entries(d)):
        run(entry, scene, False)
    return order


def _linear_stage_blocks(d: bytes) -> list[tuple[int, int]]:
    """(offset, end) of every plausible display block by linear scan: a 0x15
    marker with a token-aware terminator whose payload contains at least one
    2-byte glyph token (the same heuristic the static gates use)."""
    out = []
    i, n = 0, len(d)
    while i < n - 2:
        if d[i] == 0x15:
            t = text_codec.find_terminator(d, i + 1)
            if t > i + 1 and any(x >= 0xE0 for x in d[i + 1:t]):
                out.append((i, t + 2))
                i = t + 2
                continue
        i += 1
    return out


def _candidate_stage_blocks(d: bytes) -> list[tuple[int, int]]:
    """EVERY ``0x15 <payload> 00 00`` span (token-aware), independently at each
    0x15 position.  Catches the display blocks the strict scan misses — pure
    one-byte-glyph lines (………！！) and script-laden blocks the ending VM
    dispatches dynamically — at the cost of false candidates inside bytecode;
    callers keep only candidates that don't overlap higher-confidence blocks."""
    out = []
    n = len(d)
    for i in range(n - 2):
        if d[i] != 0x15:
            continue
        t = text_codec.find_terminator(d, i + 1)
        if t > i + 1:
            out.append((i, t + 2))
    return out


def _is_priming_row(payload: bytes) -> bool:
    """Glyph-cache warmup rows every stage file opens with (あいうえお…)."""
    p = payload[1:] if payload[:1] == b"\x15" else payload
    return p[:5] == bytes((0x16, 0x17, 0x18, 0x19, 0x1A))


def stage_blocks(rom: GameROM, fname: str,
                 overlay: dict | None = None) -> list[dict]:
    """Every display block of one stage script file: the VM-reached blocks in
    play order (scene >= 0, `order` = play index) UNION the linear-scan blocks
    the static VM walk cannot reach (ending-VM text; scene -1).  `overlay`
    optionally contributes curated per-block speaker cid / choice flags."""
    d = rom.file(fname)
    reached = stage_block_order(d)
    meta = overlay or {}
    blocks = []
    seen = set()
    spans: list[tuple[int, int]] = []
    for order_i, (scene, off, branch) in enumerate(reached):
        t = text_codec.find_terminator(d, off + 1)
        if t < 0 or off in seen:
            continue
        seen.add(off)
        spans.append((off, t + 2))
        blocks.append((off, t + 2, scene, order_i, branch))
    # strict linear scan (tokened blocks): these may legitimately overlap VM
    # blocks — a cold-start scan produces the historical "mega-block" spans
    # (warmup row + script + first line) that the shipped translation edits
    # actually use, while the VM sees the nested display blocks individually.
    for off, end in _linear_stage_blocks(d):
        if off not in seen:
            seen.add(off)
            spans.append((off, end))
            blocks.append((off, end, -1, -1, False))
    # relaxed candidates (one-byte-only lines like ………！！, script-laden
    # blocks only the ending VM dispatches): accept only where the 0x15 marker
    # is NOT inside an accepted block's span (payload bytes 0x15 = か would
    # otherwise mint false blocks).
    for off, end in _candidate_stage_blocks(d):
        if off in seen or any(o < off < e for o, e in spans):
            continue
        seen.add(off)
        blocks.append((off, end, -1, -1, False))
    blocks.sort(key=lambda b: (b[3] if b[3] >= 0 else 10 ** 9, b[0]))
    out = []
    for off, end, scene, order_i, branch in blocks:
        raw = d[off:end]
        entry = {"off": _hex(off), "len": end - off,
                 "text": decode_text(rom, raw, "stage", rom.expand, True)}
        if scene >= 0:
            entry["scene"] = scene
            entry["order"] = order_i
        else:
            entry["reachable"] = False
        if branch:
            entry["branch"] = True
        if _is_priming_row(raw):
            entry["priming"] = True
        m = meta.get(hex(off)) or meta.get(str(off)) or {}
        if m.get("sp", -1) >= 0:
            entry["speaker"] = m["sp"]
        if m.get("narration"):
            entry["narration"] = True
        if m.get("choice"):
            entry["choice"] = True
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# dictionaries + string-pointer graph (labels / abilities universe)
# ---------------------------------------------------------------------------
def dictionary_entries(rom: GameROM, which: str) -> list[dict]:
    """Entries of a 0xF0xx macro dictionary ('text' = dialogue/name store,
    'sys' = system/combat store)."""
    base = L.DICT_TEXT if which == "text" else L.DICT_SYS
    surface = "stage" if which == "text" else "bank"
    a = rom.arm9
    n = u16(a, base) // 2
    out = []
    for idx in range(n):
        off = u16(a, base + idx * 2)
        s = base + off
        e = a.find(b"\x00", s)
        raw = a[s:e] if e >= 0 else a[s:s + 64]
        if not raw:
            continue
        out.append({"index": idx, "offset": _hex(off), "off": _hex(s),
                    "len": len(raw),
                    "text": decode_text(rom, raw, surface, None)})
    return out


def _plausible_string(rom: GameROM, off: int, maxlen: int = 96) -> bytes | None:
    """Byte span at arm9 `off` iff it parses as a clean glyph string.

    A single 0xF0xx macro token IS a whole word (many UI labels/ability names
    are exactly one macro), so any 2-byte token qualifies; bare one-byte
    strings need >= 2 glyphs to filter numeric noise."""
    a = rom.arm9
    end = a.find(b"\x00", off, off + maxlen)
    if end <= off:
        return None
    raw = a[off:end]
    glyphs = tokens = 0
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b >= 0xE0:
            if i + 1 >= n:
                return None
            glyphs += 1
            tokens += 1
            i += 2
            continue
        if b == 0x01:
            i += 1
            continue
        if b < 0x02:
            return None
        glyphs += 1
        i += 1
    return raw if (glyphs >= 2 or tokens >= 1) else None


def pointer_strings(rom: GameROM) -> list[dict]:
    """The string-pointer graph: every 4-aligned word in the code image whose
    value points into a JP string band at a clean string start.  This is the
    universe that UI-label / ability-name pointer re-aims live in; grouped by
    target string."""
    a = rom.arm9
    bands = [(L.RAM_BASE + lo, L.RAM_BASE + hi) for lo, hi in L.JP_STRING_BANDS]
    by_target: dict[int, list[int]] = {}
    for site in range(0, min(len(a), L.ARM9_HEAD_END) - 3, 4):
        v = u32(a, site)
        if any(lo <= v < hi for lo, hi in bands):
            by_target.setdefault(v, []).append(site)
    out = []
    for v in sorted(by_target):
        off = v - L.RAM_BASE
        raw = _plausible_string(rom, off)
        if raw is None:
            continue
        out.append({"ptr": _hex(v), "off": _hex(off), "len": len(raw),
                    "text": decode_text(rom, raw, "bank", rom.expand_sys),
                    "text_stage": decode_text(rom, raw, "stage", rom.expand),
                    "sites": [_hex(s) for s in by_target[v]]})
    return out
