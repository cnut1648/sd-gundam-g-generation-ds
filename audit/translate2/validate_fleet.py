#!/usr/bin/env python3
"""Validate fleet outputs against briefs + encode/budget rules (no writes).

For every entity brief in staging/{chars,units}, pair with staging/out/... and
check:
  schema      output parses, entity matches, owner-only fields respected
  charset     no ，/～/↑/─ etc; ellipsis pairing; forbidden marks per surface
  encode      every proposed string encodes on its true surface
              (bank: with the record's original-JP one-byte allowance)
  budget      ID name <=64px; bark bytes <= budget; 1df/1e0 record bytes fit;
              cutin/detail reflow into their line grids; unit name <=144px
  numbers     digits+% sequences in detail/ability/defense match the JP field

Report: staging/validation.json  (per-entity status + defect list)
        staging/glyph_gap.txt    (chars unencodable per surface, with demand)
Exit 0 always (report-driven flow).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_apply import (REPO, CM, encode_bank, encode_stage, payload_px_bank,  # noqa: E402
                       reflow, open_roms, glyph_cells)
from utils.extract import walkers as W  # noqa: E402
from utils.extract import layout as L  # noqa: E402

STG = HERE / "staging"

BAD_ANYWHERE = re.compile(r"[，～↑↓←→＜＞<>\u3000 \t“”\"'；;：:]")
BAD_ANYWHERE_NAMEISH = re.compile(r"[，～↑↓←→＜＞<>\u3000 \t「」『』“”\"';；]")
NUM_RE = re.compile(r"\d+")

jp, zh = open_roms()
jdetails = {d["didx"]: d for d in W.id_details(jp)}
jspec = W.special_records(jp)
zspec = W.special_records(zh)

COND_ZH = {"戦闘中": "战斗中", "マップ": "地图"}
TGT_ZH = {"チーム全体": "全队", "自分のみ": "仅自身", "敵チーム全体": "敌队",
          "指揮範囲全体": "指挥范围", "マップ全体": "全图", "自軍全体": "全军"}


def detail_jp_parts(didx):
    d = jdetails.get(didx)
    if not d:
        return None
    b = bytes(jp.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]])
    from build.build_guide import decode_text
    t = decode_text(jp, b, "stage", jp.expand, False)
    m = re.match(r"^使用条件：?(.+?)対象：(.+?)（効果）(.*)$", t)
    nsegs = len([s for s in b.split(b"\x00") if s.strip(b"\x01")])
    return (*(m.groups() if m else (None, None, t)), nsegs)


def compose_detail(didx, effect_zh):
    """Full ZH detail text from the effect clause; returns (text, defects)."""
    parts = detail_jp_parts(didx)
    defects = []
    if parts is None:
        return None, [f"didx {didx}: no JP record"]
    cond, tgt, _rest, nsegs = parts
    czh = COND_ZH.get(cond or "", None)
    tzh = TGT_ZH.get(tgt or "", None)
    if czh is None or tzh is None:
        defects.append(f"didx {didx}: unmapped cond/target {cond!r}/{tgt!r}")
        return None, defects
    max_eff_lines = max(2, min(3, nsegs - 1))
    lines = reflow(effect_zh, 18, max_eff_lines)
    if lines is None:
        defects.append(f"didx {didx}: effect does not fit {max_eff_lines}x18: {effect_zh!r}")
        return None, defects
    eff = "{00}".join(lines)
    return f"使用条件、{czh}{{01}}{{01}}对象、{tzh}{{00}}（效果）{eff}", defects


def check_encode_stage(tag, text, defects, gap):
    try:
        return encode_stage(text)
    except ValueError as e:
        ch = re.search(r"unencodable character '(.+?)'", str(e))
        if ch:
            gap.setdefault(("stage", ch.group(1)), []).append(tag)
        defects.append(f"{tag}: {e}")
        return None


def check_encode_bank(tag, text, jp_payload, defects, gap):
    try:
        return encode_bank(text, jp_payload, jp.expand_sys)
    except ValueError as e:
        ch = re.search(r"unencodable char '(.+?)'", str(e))
        if ch:
            gap.setdefault(("bank", ch.group(1)), []).append(tag)
        defects.append(f"{tag}: {e}")
        return None


def nums_of(s):
    return sorted(NUM_RE.findall(s or ""))


def validate_char(brief, out, gap):
    defects, warns = [], []
    # index brief items
    ids_by_idn = {}
    for card in brief["cards"]:
        for i in card["ids"]:
            ids_by_idn[i["idn"]] = i
    barks_by_key = {(b["file"], b["record"]): b for b in brief["barks"]}
    seen_ids = set()
    for o in out.get("ids", []):
        idn = o.get("idn")
        b = ids_by_idn.get(idn)
        if b is None:
            defects.append(f"idn {idn}: not in brief")
            continue
        seen_ids.add(idn)
        # --- name (<=64px on trampoline; not pure-JP; encodable)
        nm = o.get("name_zh", "")
        if not nm:
            warns.append(f"idn {idn}: empty name_zh (defer to shared owner)")
        elif nm == b["name_zh_current"]:
            pass                                     # no-op: keep proven bytes
        else:
            if BAD_ANYWHERE_NAMEISH.search(nm) or "，" in nm:
                defects.append(f"idn {idn}: bad char in name {nm!r}")
            jp_name_b = jp.cstr(int(b["jp_name_ptr"], 16)) if b["jp_name_ptr"] else b""
            enc = check_encode_bank(f"idn {idn} name", nm, jp_name_b or b"", defects, gap)
            if enc is not None:
                px = payload_px_bank(enc)
                if px > 64:
                    defects.append(f"idn {idn}: name {nm!r} = {px}px > 64px")
        # --- cutin
        if "cutin_zh" in o:
            if not b.get("cutin_owner"):
                warns.append(f"idn {idn}: cutin_zh from non-owner (ignored)")
            cz = o["cutin_zh"]
            if cz and cz != b["cutin_zh_current"]:
                if BAD_ANYWHERE.search(cz):
                    defects.append(f"idn {idn}: bad char in cutin {cz!r}")
                if cz.count("…") % 2 and b["cutin_jp"].count("…") % 2 == 0:
                    defects.append(f"idn {idn}: odd ellipsis in cutin {cz!r}")
                if reflow(cz, 18, 3) is None:
                    defects.append(f"idn {idn}: cutin does not fit 3x18 {cz!r}")
                check_encode_stage(f"idn {idn} cutin", cz, defects, gap)
        # --- detail effect
        if "detail_effect_zh" in o:
            if not b.get("detail_owner"):
                warns.append(f"idn {idn}: detail from non-owner (ignored)")
            else:
                eff = o["detail_effect_zh"]
                if not eff:
                    defects.append(f"idn {idn}: empty detail_effect_zh")
                else:
                    if BAD_ANYWHERE.search(eff):
                        defects.append(f"idn {idn}: bad char in detail {eff!r}")
                    txt, dd = compose_detail(b["didx"], eff)
                    defects += dd
                    if txt:
                        check_encode_stage(f"idn {idn} detail", txt, defects, gap)
                    jn = nums_of(b["detail_jp"])
                    zn = nums_of(eff)
                    if jn != zn:
                        warns.append(f"idn {idn}: numbers JP{jn} != ZH{zn}")
    for idn, b in ids_by_idn.items():
        if idn not in seen_ids:
            defects.append(f"idn {idn}: missing from output")
        elif b.get("detail_owner") and "detail_effect_zh" not in [
                k for o in out.get("ids", []) if o.get("idn") == idn for k in o]:
            defects.append(f"idn {idn}: owner but no detail_effect_zh")
    # barks
    seen_b = set()
    for o in out.get("barks", []):
        key = (o.get("file"), o.get("record"))
        b = barks_by_key.get(key)
        if b is None:
            defects.append(f"bark {key}: not in brief (or not owned)")
            continue
        seen_b.add(key)
        t = o.get("zh", "")
        if not t:
            defects.append(f"bark {key}: empty")
            continue
        if t == b["zh_current"]:
            continue                                 # no-op: keep proven bytes
        if BAD_ANYWHERE.search(t):
            defects.append(f"bark {key}: bad char {t!r}")
        if t.count("…") % 2 and b["jp"].count("…") % 2 == 0:
            defects.append(f"bark {key}: odd ellipsis {t!r}")
        enc = check_encode_stage(f"bark {key}", t, defects, gap)
        if enc is not None and len(enc) > b["budget_bytes"]:
            defects.append(f"bark {key}: {len(enc)}B > budget {b['budget_bytes']}B: {t!r}")
    for key in barks_by_key:
        if key not in seen_b:
            defects.append(f"bark {key}: missing from output")
    return defects, warns


def _seg_span(recmap, index):
    for r in recmap:
        if r["index"] == index:
            return int(r["start"], 16), int(r["end"], 16)
    return None, None


def _record_budget(fname, recmap, index):
    """Writable byte budget of a 1df/1e0 record: full span minus preserved
    2-byte tail (00 00) minus 2 bytes for the final segment separator kept."""
    s, e = _seg_span(recmap, index)
    if s is None:
        return None, None, None
    raw = jp.file(fname)[s:e]
    return s, e, raw


def compose_segments(segs_zh, jp_raw, tag, defects, gap):
    """Encode segments and frame them exactly like the JP record: segments
    joined by 00 03, zero-padded into the JP record span, JP tail preserved."""
    jp_segs = [p for p in jp_raw.split(b"\x00\x03")]
    # trailing framing: everything after the last non-empty text segment
    enc_segs = []
    ok = True
    for k, s in enumerate(segs_zh):
        e = check_encode_bank(f"{tag} seg{k}", s, jp_raw, defects, gap)
        if e is None:
            ok = False
        enc_segs.append(e or b"")
    if not ok:
        return None
    body = b"\x00\x03".join(enc_segs)
    # keep the record's final framing: 00 03 00 00 tail (defensive: use 4)
    tail = b"\x00\x03\x00\x00" if jp_raw.endswith(b"\x00\x00") else b"\x00\x03"
    room = len(jp_raw) - len(tail)
    if len(body) > room:
        defects.append(f"{tag}: segments {len(body)}B > room {room}B")
        return None
    return body + b"\x00" * (room - len(body)) + tail


def validate_unit(brief, out, gap):
    defects, warns = [], []
    nm = out.get("name_zh", "")
    if nm and nm != brief["name_zh_current"]:
        if BAD_ANYWHERE_NAMEISH.search(nm):
            defects.append(f"unit name: bad char {nm!r}")
        jp_name_b = b""
        enc = check_encode_bank("unit name", nm, jp_name_b, defects, gap)
        if enc is not None and payload_px_bank(enc) > 144:
            defects.append(f"unit name {nm!r} > 144px")
    for w in out.get("weapons", []):
        t = w.get("zh", "")
        if not t:
            continue
        if BAD_ANYWHERE_NAMEISH.search(t):
            defects.append(f"weapon slot {w.get('slot')}: bad char {t!r}")
        check_encode_bank(f"weapon slot {w.get('slot')}", t, b"", defects, gap)
    sp = brief.get("specials", {})
    if "ability_segments_zh" in out:
        ab = sp.get("ability")
        if not ab or not ab.get("owner"):
            warns.append("ability segments from non-owner (ignored)")
        else:
            segs = out["ability_segments_zh"]
            if len(segs) != len(ab["jp_segments"]):
                defects.append(f"ability: {len(segs)} segs != JP {len(ab['jp_segments'])}")
            else:
                s, e = _seg_span(jspec["ability"], ab["family"])
                raw = jp.file(L.SPECIAL_ABILITY_FILE)[s:e]
                compose_segments(segs, raw, "ability", defects, gap)
                jn = nums_of("".join(ab["jp_segments"]))
                zn = nums_of("".join(segs))
                if jn != zn:
                    warns.append(f"ability numbers JP{jn} != ZH{zn}")
    if "defense_segments_zh" in out and out["defense_segments_zh"]:
        df = sp.get("defense")
        if not df or not df.get("owner"):
            warns.append("defense segments from non-owner (ignored)")
        else:
            segs = out["defense_segments_zh"]
            if len(segs) != len(df["jp_segments"]):
                defects.append(f"defense: {len(segs)} segs != JP {len(df['jp_segments'])}")
            else:
                s, e = _seg_span(jspec["defense"], df["record"])
                raw = jp.file(L.SPECIAL_DEFENSE_FILE)[s:e]
                compose_segments(segs, raw, "defense", defects, gap)
    if out.get("defense_name_zh"):
        dn = sp.get("defense_name")
        if not dn or not dn.get("owner"):
            warns.append("defense_name from non-owner (ignored)")
        else:
            t = out["defense_name_zh"]
            if BAD_ANYWHERE_NAMEISH.search(t):
                defects.append(f"defense name: bad char {t!r}")
            jb = jp.cstr(int(dn["ptr"], 16)) if dn.get("ptr") else b""
            check_encode_bank("defense name", t, jb or b"", defects, gap)
    return defects, warns


def main():
    report = {"chars": {}, "units": {}}
    gap: dict = {}
    for kind in ("chars", "units"):
        for bp in sorted((STG / kind).glob("*.json")):
            ent = bp.stem
            brief = json.loads(bp.read_text())
            op = STG / "out" / kind / bp.name
            if not op.exists():
                report[kind][ent] = {"status": "missing"}
                continue
            try:
                out = json.loads(op.read_text())
            except Exception as e:
                report[kind][ent] = {"status": "unparseable", "error": str(e)}
                continue
            if out.get("entity") != ent:
                report[kind][ent] = {"status": "wrong-entity", "got": out.get("entity")}
                continue
            if kind == "chars":
                defects, warns = validate_char(brief, out, gap)
            else:
                defects, warns = validate_unit(brief, out, gap)
            report[kind][ent] = {
                "status": "defects" if defects else "ok",
                "defects": defects, "warnings": warns}
    ok = sum(1 for k in report for e in report[k].values() if e["status"] == "ok")
    bad = {k: {e: v for e, v in report[k].items() if v["status"] != "ok"}
           for k in report}
    nmiss = sum(1 for k in report for v in report[k].values() if v["status"] == "missing")
    ndef = sum(1 for k in report for v in report[k].values() if v["status"] == "defects")
    (STG / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = []
    for (surf, ch), tags in sorted(gap.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"{surf}\t{ch}\t{len(tags)}\t{'; '.join(tags[:6])}")
    (STG / "glyph_gap.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"ok={ok} defects={ndef} missing={nmiss} "
          f"unparseable/wrong={sum(1 for k in report for v in report[k].values() if v['status'] in ('unparseable','wrong-entity'))}")
    print(f"glyph gaps: {len(gap)} distinct chars")
    for (surf, ch), tags in sorted(gap.items(), key=lambda kv: -len(kv[1]))[:15]:
        print(f"  {surf} {ch!r} x{len(tags)}")


if __name__ == "__main__":
    main()
