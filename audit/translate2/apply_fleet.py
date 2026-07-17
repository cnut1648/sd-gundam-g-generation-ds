#!/usr/bin/env python3
"""Apply validated fleet translations into data/zh (the one reviewed pass).

Change classes and their stores (all per AGENTS.md source-of-truth rules):

  barks       -> data/zh/files/barks/*.json           (in-place, size-capped)
  cut-ins     -> data/zh/files/battle/cutin_quotes.json (growable groups)
  1df ability -> data/zh/files/battle/special_abilities.json (zh_hex records)
  1e0 defense -> data/zh/files/battle/special_defenses.json  (zh_hex records)
  ID details  -> idcmd_detail_pool / resident_caves + characters.json
                 detail_offsets + relocation ledger
  ID names    -> pool arenas (surgical in-place or ledger relocation) +
                 characters.json ids[].name.ptr
  weapons     -> pool arenas + units.json weapons[].ptr (+ 31e cascade)
  unit names  -> pool arenas (name band!) + units.json ptr
  defense nm  -> pool arenas in-place only (+ ui.json abilities annotation)

The driver is idempotent: applying twice produces the same repo state.
Run AFTER validate_fleet.py reports 0 blocking defects for the entities
being applied.  --dry-run prints the plan without writing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_apply import (REPO, CM, encode_bank, encode_stage, payload_px_bank,  # noqa: E402
                       reflow, open_roms, load_json, dump_json, Arena, Pools,
                       RAM_BASE, DETAIL_BASE, NAME_BAND_LIMIT)
from utils import text_codec  # noqa: E402
from utils.extract import layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from utils.extract.gamerom import u32  # noqa: E402
from build.build_guide import decode_text  # noqa: E402

STG = HERE / "staging"

jp, zh = open_roms()

COND_ZH = {"戦闘中": "战斗中", "マップ": "地图"}
TGT_ZH = {"チーム全体": "全队", "自分のみ": "仅自身", "敵チーム全体": "敌队",
          "指揮範囲全体": "指挥范围", "マップ全体": "全图", "自軍全体": "全军"}


# ---------------------------------------------------------------------------
# collect: read every ok entity output and index proposals by target object
# ---------------------------------------------------------------------------
def collect(validation):
    props = {
        "idname": defaultdict(list),   # jp_name_ptr -> [(entity, idn, text)]
        "detail": {},                  # didx -> (entity, effect_zh, detail_jp)
        "cutin": {},                   # group -> (entity, text)
        "bark": {},                    # (file, record) -> (entity, text, budget)
        "ability": {},                 # fam -> (entity, segs)
        "defense": {},                 # rec -> (entity, segs)
        "defname": {},                 # ptr -> (entity, text)
        "weapon": defaultdict(list),   # ptr -> [(entity, text)]
        "unitname": defaultdict(list), # jp name ptr -> [(entity, text)]
        "nameflags": [],
    }
    for kind in ("chars", "units"):
        for bp in sorted((STG / kind).glob("*.json")):
            ent = bp.stem
            st = validation[kind].get(ent, {})
            if st.get("status") != "ok":
                continue
            brief = json.loads(bp.read_text())
            out = json.loads((STG / "out" / kind / bp.name).read_text())
            if out.get("name_flag"):
                props["nameflags"].append((ent, out["name_flag"]))
            if kind == "chars":
                ids_by_idn = {i["idn"]: i for c in brief["cards"] for i in c["ids"]}
                for o in out.get("ids", []):
                    b = ids_by_idn.get(o.get("idn"))
                    if not b:
                        continue
                    if o.get("name_zh"):
                        props["idname"][b["jp_name_ptr"]].append(
                            (ent, o["idn"], o["name_zh"], b["zh_name_ptr"]))
                    if b.get("cutin_owner") and "cutin_zh" in o and b["cutin_group"]:
                        props["cutin"][b["cutin_group"]] = (ent, o["cutin_zh"])
                    if b.get("detail_owner") and o.get("detail_effect_zh"):
                        props["detail"][b["didx"]] = (ent, o["detail_effect_zh"],
                                                      b["detail_jp"])
                bk = {(x["file"], x["record"]): x for x in brief["barks"]}
                for o in out.get("barks", []):
                    key = (o.get("file"), o.get("record"))
                    if key in bk and o.get("zh"):
                        props["bark"][key] = (ent, o["zh"], bk[key]["budget_bytes"])
            else:
                sp = brief.get("specials", {})
                if out.get("name_zh") and out["name_zh"] != brief["name_zh_current"]:
                    props["unitname"][brief["entity"]].append(
                        (ent, out["name_zh"], brief))
                for w in out.get("weapons", []):
                    if w.get("zh"):
                        wb = next((x for x in brief["weapons"]
                                   if x["slot"] == w.get("slot")), None)
                        if wb and w["zh"] != wb["zh_current"]:
                            props["weapon"][wb["ptr"]].append(
                                (ent, w["zh"], wb, brief["utids"][0]))
                ab = sp.get("ability")
                if ab and ab.get("owner") and out.get("ability_segments_zh"):
                    if out["ability_segments_zh"] != ab["zh_segments_current"]:
                        props["ability"][ab["family"]] = (ent, out["ability_segments_zh"])
                df = sp.get("defense")
                if df and df.get("owner") and out.get("defense_segments_zh"):
                    if out["defense_segments_zh"] != df["zh_segments_current"]:
                        props["defense"][df["record"]] = (ent, out["defense_segments_zh"])
                dn = sp.get("defense_name")
                if dn and dn.get("owner") and out.get("defense_name_zh"):
                    if out["defense_name_zh"] != dn["zh_current"]:
                        props["defname"][dn["ptr"]] = (ent, out["defense_name_zh"])
    return props


# ---------------------------------------------------------------------------
# apply: barks
# ---------------------------------------------------------------------------
def apply_barks(props, report):
    zbarks = {(b["file"], b["record"]): b for b in W.barks(zh)}
    tables = {}
    changed = 0
    for (fname, rec), (ent, text, budget) in sorted(props["bark"].items()):
        zb = zbarks.get((fname, rec))
        if not zb:
            report["skipped"].append(f"bark {fname}/{rec}: no ZH record")
            continue
        enc = encode_stage(text)
        if len(enc) > budget:
            report["skipped"].append(f"bark {fname}/{rec}: {len(enc)}B > {budget}B")
            continue
        rel = f"data/zh/files/barks/{fname.split('.')[0]}.json"
        t = tables.setdefault(rel, load_json(rel))
        body, end = int(zb["body"], 16), int(zb["end"], 16)
        # one whole-record edit replaces every old (possibly per-run) edit
        # inside [body, end)
        olds = [e for e in t["edits"] if body <= int(e["offset"], 16) < end]
        new = {"offset": f"0x{body:X}", "size": end - body, "zh": text}
        if len(olds) == 1 and olds[0] == new:
            continue
        t["edits"] = [e for e in t["edits"]
                      if not (body <= int(e["offset"], 16) < end)]
        t["edits"].append(new)
        t["edits"].sort(key=lambda e: int(e["offset"], 16))
        changed += 1
    for rel, t in tables.items():
        dump_json(rel, t)
    report["counts"]["barks"] = changed


# ---------------------------------------------------------------------------
# apply: cut-ins
# ---------------------------------------------------------------------------
def frame_cutin(text: str) -> str:
    """Reflow a quote into the bank's line convention: {00}。 between lines."""
    lines = reflow(text, 18, 3)
    if lines is None:
        raise ValueError(f"cutin does not fit 3x18: {text!r}")
    out = lines[0]
    for ln in lines[1:]:
        out += "{00}。" + ln
    return out


def apply_cutins(props, report):
    rel = "data/zh/files/battle/cutin_quotes.json"
    t = load_json(rel)
    by_group = {g["group"]: g for g in t["groups"]}
    changed = 0
    for group, (ent, text) in sorted(props["cutin"].items()):
        g = by_group.get(group)
        if not g:
            report["skipped"].append(f"cutin group {group}: not in bank")
            continue
        if not text:
            continue                       # empty = keep (无 records stay)
        framed = frame_cutin(text)
        enc = encode_stage(framed)
        if g.get("zh_hex") == enc.hex():
            continue
        g["zh"] = framed
        g["zh_hex"] = enc.hex()
        changed += 1
    if changed:
        dump_json(rel, t)
    report["counts"]["cutins"] = changed


# ---------------------------------------------------------------------------
# apply: 1df / 1e0 special records (full-record zh_hex edits)
# ---------------------------------------------------------------------------
def _record_span(recmap, index):
    for r in recmap:
        if r["index"] == index:
            return int(r["start"], 16), int(r["end"], 16)
    return None, None


def compose_record(segs_zh, jp_raw):
    enc = [encode_bank(s, jp_raw, jp.expand_sys) for s in segs_zh]
    body = b"\x00\x03".join(enc)
    tail = b"\x00\x03\x00\x00" if jp_raw.endswith(b"\x00\x00") else b"\x00\x03"
    room = len(jp_raw) - len(tail)
    if len(body) > room:
        raise ValueError(f"record body {len(body)}B > room {room}B")
    return body + b"\x00" * (room - len(body)) + tail


def apply_specials(props, report):
    jspec = W.special_records(jp)
    for key, fname, rel, recmap in (
            ("ability", L.SPECIAL_ABILITY_FILE,
             "data/zh/files/battle/special_abilities.json", jspec["ability"]),
            ("defense", L.SPECIAL_DEFENSE_FILE,
             "data/zh/files/battle/special_defenses.json", jspec["defense"])):
        t = load_json(rel)
        changed = 0
        for index, (ent, segs) in sorted(props[key].items()):
            s, e = _record_span(recmap, index)
            if s is None:
                report["skipped"].append(f"{key} record {index}: no JP span")
                continue
            raw = jp.file(fname)[s:e]
            try:
                rec_bytes = compose_record(segs, raw)
            except ValueError as err:
                report["skipped"].append(f"{key} record {index}: {err}")
                continue
            # drop every old edit overlapping [s, e); add the full record edit
            t["edits"] = [ed for ed in t["edits"]
                          if not (int(ed["offset"], 16) < e and
                                  int(ed["offset"], 16) + ed["size"] > s)]
            zh_note = "{00}".join(segs)
            t["edits"].append({"offset": f"0x{s:x}", "size": len(rec_bytes),
                               "zh": zh_note, "zh_hex": rec_bytes.hex()})
            changed += 1
        t["edits"].sort(key=lambda ed: int(ed["offset"], 16))
        if changed:
            dump_json(rel, t)
        report["counts"][key] = changed


# ---------------------------------------------------------------------------
# apply: ID details (in-place within span, else ledger relocation to caves)
# ---------------------------------------------------------------------------
STAT_WORDS = "命中率|回避率|攻击力|命中|回避|攻击|反应|装甲|SP|HP"
_STAT_UP = re.compile(rf"({STAT_WORDS})(?:各|)(?:提升|上升|增加|提高|上昇)(\d+%?)")
_STAT_DN = re.compile(rf"({STAT_WORDS})(?:各|)(?:降低|减少|下降)(\d+%?)")


def compact_effect(eff: str) -> str:
    """Canonical stat-clause style (the store's own shipped notation):
    命中率提升20% -> 命中率+20%; 装甲降低3 -> 装甲-3.  Prose clauses
    (必定回避敌方的攻击…) are untouched — meaning is never dropped."""
    out = _STAT_UP.sub(lambda m: f"{m.group(1)}+{m.group(2)}", eff)
    out = _STAT_DN.sub(lambda m: f"{m.group(1)}-{m.group(2)}", out)
    out = out.replace("、并且", "、").replace("、并", "、")
    return out


def compose_detail_text(didx, effect_zh, jdetails, compact=False):
    d = jdetails[didx]
    b = bytes(jp.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]])
    t = decode_text(jp, b, "stage", jp.expand, False)
    m = re.match(r"^使用条件：?(.+?)対象：(.+?)（効果）(.*)$", t)
    cond, tgt = COND_ZH[m.group(1)], TGT_ZH[m.group(2)]
    eff_src = compact_effect(effect_zh) if compact else effect_zh
    nsegs = len([s for s in b.split(b"\x00") if s.strip(b"\x01")])
    lines = reflow(eff_src, 18, max(2, min(3, nsegs - 1)))
    if lines is None and not compact:
        return compose_detail_text(didx, effect_zh, jdetails, compact=True)
    eff = "{00}".join(lines)
    return f"使用条件、{cond}{{01}}{{01}}对象、{tgt}{{00}}（效果）{eff}"


def apply_details(props, pools, chars_json, report):
    """FULL REFLOW of the ID-command detail store back to the JP shape:
    a strictly monotonic 256-slot offset table over ONE packed pool
    [0xF9449, 0xFC643), every record terminated 00 00, no cave spill.

    The previous per-record relocation left the ZH table non-monotonic
    (34 didx in resident caves), which blinds the extraction walker and
    fragments the caves.  ZH text is denser than JP, so the whole
    (re)translated store fits its original home; the vacated cave spans
    are returned through the ledger."""
    jdetails = {d["didx"]: d for d in W.id_details(jp)}
    dofs = {e["didx"]: int(e["offset"], 16) for e in chars_json["detail_offsets"]}
    import struct as _st
    cur_offs = [_st.unpack_from("<I", zh.arm9, L.DETAIL_OFFTAB + k * 4)[0]
                for k in range(256)]

    def current_payload(didx):
        """Visible ZH bytes of didx in the CURRENT built ROM (renderer walk)."""
        off = cur_offs[didx]
        if off <= 0x400:                       # the pool-head empty sentinel
            return b""
        foff = DETAIL_BASE + off
        if not (0 <= foff < len(zh.arm9)):
            return b""
        term = text_codec.find_terminator(zh.arm9, foff)
        if term < 0 or term - foff > 0x400:
            return b""
        return bytes(zh.arm9[foff:term]).rstrip(b"\x00")

    CUR_RE = re.compile(r"^使[用無]条件[：、·]?(.+?)[対对]象[：、·]?(.+?)（效果）(.*)$")
    REV_COND = {"战斗中": "战斗中", "地图": "地图"}
    REV_TGT = {"全队": "全队", "仅自身": "仅自身", "本机": "仅自身", "敌队": "敌队",
               "指挥范围": "指挥范围", "全图": "全图", "全军": "全军"}

    def normalized_current(didx):
        """Out-of-scope record, re-derived through the canonical composer:
        parses the CURRENT visible text (tolerating the legacy ：/対象/使無
        deviants and the cave over-read concatenations), re-frames it as
        使用条件、X{01}{01}对象、Y{00}（效果）… — falls back to raw bytes."""
        raw = current_payload(didx)
        if not raw:
            return raw
        txt = decode_text(zh, raw, "stage", zh.expand, False).replace("▼", "")
        m = CUR_RE.match(txt)
        if not m:
            return raw
        cond = REV_COND.get(m.group(1).strip("、"))
        tgt = REV_TGT.get(m.group(2).strip("、"))
        eff = m.group(3)
        # cave over-read: cut at the start of an accidentally-appended record
        k = min([x for x in (eff.find("使用条"), eff.find("使無条")) if x >= 0],
                default=-1)
        if k >= 0:
            eff = eff[:k]
        eff = eff.rstrip("、·")
        if not cond or not tgt or not eff:
            return raw
        d = jdetails.get(didx)
        nsegs = 3
        if d:
            b = bytes(jp.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]])
            nsegs = len([s for s in b.split(b"\x00") if s.strip(b"\x01")])
        lines = reflow(eff, 18, max(2, min(3, nsegs - 1)))
        if lines is None:
            return raw
        text = (f"使用条件、{cond}{{01}}{{01}}对象、{tgt}{{00}}（效果）"
                + "{00}".join(lines))
        try:
            return encode_stage(text)
        except ValueError:
            return raw

    # ---- final payload per didx (two passes: prose, then compact style if
    # the pool overflows) --------------------------------------------------
    def build_final(compact):
        f, ch = {}, 0
        for didx in range(256):
            if didx in props["detail"]:
                ent, effect, _dj = props["detail"][didx]
                text = compose_detail_text(didx, effect, jdetails, compact=compact)
                enc = encode_stage(text)
                f[didx] = enc
                if enc != current_payload(didx):
                    ch += 1
            else:
                f[didx] = normalized_current(didx)
        return f, ch

    def packed_size(f):
        seen, tot = set(), 0x401
        for didx in range(255):
            p = f.get(didx, b"")
            if p and p not in seen:
                seen.add(p)
                tot += len(p) + 2
        return tot

    final, changed = build_final(compact=False)
    if packed_size(final) > 13819:
        final, changed = build_final(compact=True)
        report["counts"]["detail_style"] = "compact"
    else:
        report["counts"]["detail_style"] = "prose"
    # ---- pack: records abut with 00 00 terminators; identical payloads
    # SHARE one placed copy (the shipped aliasing convention — the renderer
    # reads base+u32[didx], so back-references are exact; the extraction
    # walker reports aliased slots as empty, same as the shipped ROM).
    # If the pool still overflows, the LARGEST records spill to resident-cave
    # runs through the ledger (u32 offsets reach them; shipped convention). --
    POOL_LO, POOL_END = 0x401, 13819          # rel DETAIL_BASE (JP extents)
    uniq = {}                                  # payload -> [didx...]
    for didx in range(255):
        p = final.get(didx, b"")
        if p:
            uniq.setdefault(p, []).append(didx)
    total = POOL_LO + sum(len(p) + 2 for p in uniq)
    spill = []
    by_len = sorted(uniq, key=len)
    while total > POOL_END and by_len:
        p = by_len.pop()                       # largest first
        spill.append(p)
        total -= len(p) + 2
    starts = {}
    cave_starts = {}
    for p in spill:
        didxs = uniq.pop(p)
        arena, off, ram = pools.allocate(
            len(p) + 1, f"fleet-v12:detail-spill didx {didxs[0]}", didxs,
            decode_text(zh, p, "stage", zh.expand, False)[:20])
        _surgical_write(arena, off, p + b"\x00\x00", len(p) + 2, surface="stage")
        cave_starts[p] = (ram - RAM_BASE) - DETAIL_BASE
    seq = POOL_LO
    placed = {}
    blob = bytearray()
    for didx in range(255):
        p = final.get(didx, b"")
        if not p:
            starts[didx] = 0x400              # empty slot -> pool-head 00
            continue
        if p in cave_starts:
            starts[didx] = cave_starts[p]
            continue
        if p in placed:
            starts[didx] = placed[p]
            continue
        starts[didx] = placed[p] = seq
        blob += p + b"\x00\x00"
        seq += len(p) + 2
    assert seq <= POOL_END, "packer accounting error"
    starts[255] = seq                          # sentinel end
    report["counts"]["details"] = changed
    report["counts"]["detail_pool_bytes"] = seq - POOL_LO
    report["counts"]["detail_unique_records"] = len(placed)
    report["counts"]["detail_cave_spills"] = len(spill)
    # didx 0 keeps the JP convention (offset 0x400 -> the pool-head 00 byte)
    starts[0] = 0x400
    # ---- emit idcmd_detail_pool.json (whole region deterministic) --------
    rel = "data/zh/placements/idcmd_detail_pool.json"
    t = load_json(rel)
    pad = (POOL_END - seq) + (0xFC643 - (DETAIL_BASE + POOL_END))
    region = bytes(blob) + b"\x00" * pad       # covers [0x401, 0xFC643-base)
    t["entries"] = [{
        "offset": "0x1",
        "text": "(reflowed detail store: one packed region, records 00 00-"
                "terminated; per-record map = data/zh/characters.json "
                "detail_offsets)",
        "payload_hex": region.hex()}]
    dump_json(rel, t)
    # ---- characters.json detail_offsets: every slot that has a record now
    # or exists in the JP dump (the reconciler's mapping universe); JP-empty
    # slots stay unlisted so the applier keeps the original JP word (didx 255
    # is the JP sentinel 13819, already >= our packed end) -------------------
    chars_json["detail_offsets"] = [
        {"didx": k, "offset": f"0x{starts[k]:X}"} for k in range(255)
        if final.get(k) or k in jdetails]
    # ---- retire the old cave homes через the ledger ----------------------
    vacated = []
    for didx, off in sorted(dofs.items()):
        if off >= POOL_END:                    # old cave home
            foff = DETAIL_BASE + off
            aoff = foff - 0x186000
            e = pools.caves.entry_at(aoff)
            if e is not None:
                span = len(bytes.fromhex(e["payload_hex"]))
                pools.caves.remove_entry(aoff)
                vacated.append({"pool": "data/zh/placements/resident_caves.json",
                                "offset": f"0x{aoff:X}", "span": span,
                                "ram": f"0x{pools.caves.ram_of(aoff):X}"})
    if vacated:
        pools._alloc_marks.append({
            "date": "2026-07-17", "fix": "fleet-v12:detail-pool-reflow",
            "ids": [], "text": None, "pool": None, "offset": None,
            "ram": None, "bytes": 0, "reserved": 0, "vacated": vacated})
    report["counts"]["detail_cave_spans_returned"] = len(vacated)


def _entry_payload_at(arena, off):
    e = arena.entry_at(off)
    return bytes.fromhex(e["payload_hex"]) if e else None


def _surgical_write(arena, off, payload, clear_len, surface="bank"):
    """Write payload at arena offset, zero-padding to clear_len, patching the
    covering entry (or creating one).  Annotations are decoded PER SURFACE
    (G6): trampoline strings via renderB identities, renderA-direct payloads
    (relocated detail records) via the stage decoder."""
    total = max(len(payload), clear_len)
    buf = payload + b"\x00" * (total - len(payload))
    for eoff in sorted(arena.entries):
        e = arena.entries[eoff]
        p = bytes.fromhex(e["payload_hex"])
        if eoff <= off and off < eoff + len(p):
            # extend entry if needed
            need_end = off + total
            if need_end > eoff + len(p):
                p = p + b"\x00" * (need_end - (eoff + len(p)))
            p = p[:off - eoff] + buf + p[off - eoff + total:]
            e["payload_hex"] = p.hex()
            e["text"] = _annot_bytes(p, surface)
            return
    arena.entries[off] = {"offset": f"0x{off:X}",
                          "text": _annot_bytes(payload, surface),
                          "payload_hex": buf.hex()}


def _annot_bytes(b, surface="bank"):
    exp = zh.expand if surface == "stage" else zh.expand_sys
    return decode_text(zh, b.rstrip(b"\x00"), surface, exp, False)


def _set_detail_offset(chars_json, didx, new_home):
    for e in chars_json["detail_offsets"]:
        if e["didx"] == didx:
            e["offset"] = f"0x{new_home:X}"
            return
    chars_json["detail_offsets"].append({"didx": didx, "offset": f"0x{new_home:X}"})
    chars_json["detail_offsets"].sort(key=lambda e: e["didx"])


# ---------------------------------------------------------------------------
# apply: pooled names (ID command names; weapons; defense names)
# ---------------------------------------------------------------------------
def adjudicate(cands):
    """Owner-first (first entity listed), then majority."""
    if not cands:
        return None
    texts = [t for _e, _i, t in cands] if len(cands[0]) == 3 else [t for _e, t, *_ in cands]
    first = texts[0]
    from collections import Counter
    cnt = Counter(texts)
    top, n = cnt.most_common(1)[0]
    return top if n > texts.count(first) else first


def apply_idnames(props, pools, chars_json, report):
    # map jp_name_ptr -> all (cid, idn, zh ptr) users from data/zh/characters.json
    users = defaultdict(list)
    jusers = defaultdict(list)
    jc = load_json("data/jp/characters.json")["characters"]
    for c in jc:
        for i in c.get("ids", []):
            if i.get("name"):
                jusers[i["name"]["ptr"]].append((c["cid"], i["idn"]))
    zids = {}
    for c in chars_json["characters"]:
        for i in c.get("ids", []):
            zids[i["idn"]] = i
    changed = reloc = 0
    report["adjudicated"] = report.get("adjudicated", {})
    # resolve each JP record to (final text, current zh home)
    resolved = []                                # (jptr, text, zptr, cands)
    for jptr, cands in sorted(props["idname"].items()):
        text = adjudicate([(e, i, t) for e, i, t, _z in cands])
        if len({t for _e, _i, t, _z in cands}) > 1:
            report["adjudicated"][f"idname {jptr}"] = {
                "chosen": text,
                "proposals": [{"entity": e, "idn": i, "text": t}
                              for e, i, t, _z in cands]}
        idns = sorted({i for _e, i, _t, _z in cands})
        z0 = zids.get(idns[0], {}).get("name", {})
        zptr = z0.get("ptr") or cands[0][3]
        if not zptr:
            report["skipped"].append(f"idname {jptr}: no zh ptr for idn {idns[0]}")
            continue
        resolved.append((jptr, text, zptr, cands))
    # group by CURRENT ZH HOME: the earlier campaign deduped identical zh
    # strings across DIFFERENT JP records; diverging retranslations must
    # SPLIT (first keeps the home, the rest relocate) — never two writes at
    # one address (the 0x61E5 clobber class this replaces).
    by_home = defaultdict(list)
    for r in resolved:
        by_home[r[2]].append(r)

    def place(jptr, text, zptr, cands, keep_home):
        nonlocal changed, reloc
        jp_bytes = jp.cstr(int(jptr, 16)) or b""
        cur = zh.cstr(int(zptr, 16)) or b""
        cur_txt = decode_text(zh, cur, "bank", zh.expand_sys, False)
        if cur_txt == text:
            return zptr
        enc = encode_bank(text, jp_bytes, jp.expand_sys)
        if payload_px_bank(enc) > 64:
            report["skipped"].append(f"idname {jptr}: {text!r} > 64px")
            return None
        arena, aoff = pools.arena_of_ram(int(zptr, 16))
        extern = [u for u in jusers[jptr]
                  if u[1] not in {i for _e, i, _t, _z in cands}]
        if keep_home and arena is not None and len(enc) <= len(cur):
            _surgical_write(arena, aoff, enc + b"\x00", len(cur))
            changed += 1
            return zptr
        # a move with out-of-scope sharers is fine — they keep the OLD string,
        # so its span must NOT be vacated (it stays live for them)
        vacate = None
        if arena is not None and keep_home and not extern:
            vacate = [{"pool": arena.rel, "offset": f"0x{aoff:X}",
                       "span": len(cur), "ram": zptr}]
        tgt, toff, ram = pools.allocate(
            len(enc), f"fleet-v12:idname {jptr}",
            sorted({i for _e, i, _t, _z in cands}), text, vacate=vacate)
        _surgical_write(tgt, toff, enc + b"\x00", len(enc) + 1)
        reloc += 1
        changed += 1
        return f"0x{ram:X}"

    by_cid = {c["cid"]: c for c in chars_json["characters"]}

    def record(cands, new_ptr, zptr, text):
        for _e, idn, _t, _z in cands:
            zi = zids.get(idn)
            if zi is None:
                cid = idn // 3
                c = by_cid.get(cid)
                if c is None:
                    c = {"cid": cid, "ids": []}
                    chars_json["characters"].append(c)
                    chars_json["characters"].sort(key=lambda x: x["cid"])
                    by_cid[cid] = c
                zi = {"idn": idn}
                c.setdefault("ids", []).append(zi)
                c["ids"].sort(key=lambda x: x["idn"])
                zids[idn] = zi
            zi.setdefault("name", {})
            if new_ptr != zptr or "ptr" in zi["name"]:
                zi["name"]["ptr"] = new_ptr
            zi["name"]["zh"] = text

    for home, group in sorted(by_home.items()):
        # identical final texts may keep sharing the home; distinct texts
        # split — the first (lowest jptr) keeps the home
        by_text = defaultdict(list)
        for r in group:
            by_text[r[1]].append(r)
        first = True
        for text, rs in sorted(by_text.items(),
                               key=lambda kv: min(r[0] for r in kv[1])):
            all_cands = [c for r in rs for c in r[3]]
            jptr0 = min(r[0] for r in rs)
            new_ptr = place(jptr0, text, home, all_cands, keep_home=first)
            first = False
            if new_ptr is None:
                continue
            for r in rs:
                record(r[3], new_ptr, home, text)
    report["counts"]["idnames"] = changed
    report["counts"]["idnames_relocated"] = reloc


def apply_weapons(props, pools, units_json, report):
    """Weapon-name pool edits + units.json ptr/zh sync + 31e library cascade."""
    jm = {u["utid"]: u for u in load_json("data/jp/units.json")["units"]}
    changed = reloc = 0
    renames = {}                                # old zh text -> new zh text
    for zptr, cands in sorted(props["weapon"].items()):
        texts = {t for _e, t, _b, _u in cands}
        text = (next(iter(texts)) if len(texts) == 1 else
                adjudicate([(e, 0, t) for e, t, _b, _u in cands]))
        cur = zh.cstr(int(zptr, 16)) or b""
        cur_txt = decode_text(zh, cur, "bank", zh.expand_sys, False)
        if cur_txt == text:
            continue
        # JP original bytes for this weapon record (token reuse + one-byte set)
        _e0, _t0, wb, utid0 = cands[0]
        ju = jm.get(utid0, {})
        jp_bytes = b""
        for w in ju.get("weapons", []):
            if w["slot"] == wb["slot"]:
                jp_bytes = jp.cstr(int(w["ptr"], 16)) or b""
                break
        try:
            enc = encode_bank(text, jp_bytes, jp.expand_sys)
        except ValueError as err:
            report["skipped"].append(f"weapon {zptr}: {err}")
            continue
        arena, aoff = pools.arena_of_ram(int(zptr, 16))
        if arena is not None and len(enc) <= len(cur):
            _surgical_write(arena, aoff, enc + b"\x00", len(cur))
            new_ptr = zptr
            changed += 1
        else:
            tgt, toff, ram = pools.allocate(
                len(enc), f"fleet-v12:weapon {zptr}",
                sorted({u for _e, _t, _b, u in cands}), text,
                vacate=([{"pool": arena.rel, "offset": f"0x{aoff:X}",
                          "span": len(cur), "ram": zptr}] if arena else None))
            _surgical_write(tgt, toff, enc + b"\x00", len(enc) + 1)
            new_ptr = f"0x{ram:X}"
            reloc += 1
        renames[cur_txt] = text
        for u in units_json["units"]:
            for w in u.get("weapons", []):
                if w.get("ptr") == zptr:
                    w["zh"] = text
                    w["ptr"] = new_ptr
    # cascade: encyclopedia weapon-name list (31e.bin, renderA-direct bank)
    if renames:
        rel = "data/zh/files/library/weapon_names.json"
        t = load_json(rel)
        casc = 0
        for e in t.get("entries", []):
            zt = e.get("zh")
            if zt in renames:
                e["zh"] = renames[zt]
                e.pop("zh_hex", None)
                casc += 1
        dump_json(rel, t)
        report["counts"]["weapon_31e_cascade"] = casc
    report["counts"]["weapons"] = changed
    report["counts"]["weapons_relocated"] = reloc


def apply_unitnames(props, pools, units_json, report):
    """Unit master-name changes: strict name-pointer band (< 0x02190000)."""
    jm = {u["utid"]: u for u in load_json("data/jp/units.json")["units"]}
    changed = 0
    for ent_key, cands in sorted(props["unitname"].items()):
        ent, text, brief = cands[0]
        utids = brief["utids"]
        zu = next((u for u in units_json["units"] if u["utid"] == utids[0]), None)
        if zu is None or "ptr" not in zu:
            report["skipped"].append(f"unitname {ent_key}: no zh ptr")
            continue
        zptr = zu["ptr"]
        cur = zh.cstr(int(zptr, 16)) or b""
        cur_txt = decode_text(zh, cur, "bank", zh.expand_sys, False)
        if cur_txt == text:
            continue
        # weaponless master records are pilot/faction identity labels that
        # render renderA-DIRECT (LESSONS A12): pure ZH-band only — no JP-token
        # reuse, no one-bytes (the two fonts share no slot identity there)
        weaponless = not brief.get("weapons")
        jp_bytes = b"" if weaponless else (
            jp.cstr(int(jm[utids[0]]["name"]["ptr"], 16)) or b""
            if utids[0] in jm else b"")
        try:
            enc = encode_bank(text, jp_bytes, jp.expand_sys)
        except ValueError as err:
            report["skipped"].append(f"unitname {ent_key}: {err}")
            continue
        if payload_px_bank(enc) > 144:
            report["skipped"].append(f"unitname {ent_key}: {text!r} > 144px")
            continue
        arena, aoff = pools.arena_of_ram(int(zptr, 16))
        if arena is not None and len(enc) <= len(cur):
            _surgical_write(arena, aoff, enc + b"\x00", len(cur))
            new_ptr = zptr
        else:
            tgt, toff, ram = pools.allocate(
                len(enc), f"fleet-v12:unitname {ent_key}", utids, text,
                name_band=True,
                vacate=([{"pool": arena.rel, "offset": f"0x{aoff:X}",
                          "span": len(cur), "ram": zptr}] if arena else None))
            _surgical_write(tgt, toff, enc + b"\x00", len(enc) + 1)
            new_ptr = f"0x{ram:X}"
        for u in units_json["units"]:
            if u.get("ptr") == zptr:
                u["ptr"] = new_ptr
                u["zh"] = text
        changed += 1
    report["counts"]["unitnames"] = changed


def apply_defnames(props, pools, report):
    ui = load_json("data/zh/ui.json")
    changed = 0
    for jptr, (ent, text) in sorted(props["defname"].items()):
        # find the ui.json ability entry re-aiming this JP ptr (if any)
        hit = next((a for a in ui["abilities"] if a.get("old_ptr") == jptr), None)
        zptr = hit["ptr"] if hit else jptr
        cur = zh.cstr(int(zptr, 16)) or b""
        cur_txt = decode_text(zh, cur, "bank", zh.expand_sys, False)
        if cur_txt == text:
            continue
        jp_bytes = jp.cstr(int(jptr, 16)) or b""
        enc = encode_bank(text, jp_bytes, jp.expand_sys)
        arena, aoff = pools.arena_of_ram(int(zptr, 16))
        if arena is None or len(enc) > len(cur):
            report["skipped"].append(f"defname {jptr}: {text!r} grows — deferred")
            continue
        _surgical_write(arena, aoff, enc + b"\x00", len(cur))
        if hit:
            hit["zh"] = text
        changed += 1
    if changed:
        dump_json("data/zh/ui.json", ui)
    report["counts"]["defnames"] = changed


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    validation = json.loads((STG / "validation.json").read_text())
    props = collect(validation)
    report = {"counts": {}, "skipped": [], "flags": props["nameflags"]}

    if args.dry_run:
        for k in ("idname", "detail", "cutin", "bark", "ability", "defense",
                  "defname", "weapon", "unitname"):
            report["counts"][k] = len(props[k])
        print(json.dumps(report["counts"], indent=1))
        return

    pools = Pools(zh, jp)
    chars_json = load_json("data/zh/characters.json")
    units_json = load_json("data/zh/units.json")

    apply_barks(props, report)
    apply_cutins(props, report)
    apply_specials(props, report)
    apply_details(props, pools, chars_json, report)
    apply_idnames(props, pools, chars_json, report)
    apply_weapons(props, pools, units_json, report)
    apply_unitnames(props, pools, units_json, report)
    apply_defnames(props, pools, report)

    # persist arenas + ledger + annotations
    for arena in (pools.ui, pools.caves, pools.bnp, pools.pdl):
        arena.rebuild_json()
        dump_json(arena.rel, arena.data)
    dump_json("data/zh/placements/relocation_ledger.json", pools.commit_ledger())
    dump_json("data/zh/characters.json", chars_json)
    dump_json("data/zh/units.json", units_json)

    (STG / "apply_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["counts"], indent=1))
    print(f"skipped: {len(report['skipped'])} (see staging/apply_report.json)")


if __name__ == "__main__":
    main()
