#!/usr/bin/env python3
"""Decoder audit v2 — context-sentence pixel/text confrontation (owner's
method): for every 2-byte glyph slot the decoder ever reads, pick real game
sentences that USE it, render the sentence exactly as the game draws it
(oracle, correct font per surface), and print the decoder's text for the
same bytes rendered through an independent reference font (WQY) directly
underneath.  A reviewer reads pairs; any pair that doesn't say the same
thing pinpoints a decode bug at sentence context (the 振り-vs-戦り method).

Greedy set-cover keeps it small: each sentence covers many slots, so a few
hundred rows audit every slot in use.

Outputs: staging/decoder_audit/pairs_<surface>_NN.png + rows.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "test"))
from lib_apply import open_roms  # noqa: E402
from utils import text_codec as tc  # noqa: E402
import utils.extract.layout as L  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from build.build_guide import decode_text  # noqa: E402
from render_oracle import Oracle  # noqa: E402
from pcf_raster import PCF, raster12  # noqa: E402

OUT = HERE / "staging/decoder_audit"
OUT.mkdir(parents=True, exist_ok=True)
jp, zh = open_roms()
oracle = Oracle(Path("0098 - SD Gundam G Generation DS (Japan).nds"))
pcf = PCF()

MAXLEN = 30                       # glyphs per row (truncate long payloads)


def slots_of(payload: bytes, surface: str):
    out = set()
    for slot, font in oracle.glyph_stream(payload, surface):
        if slot >= 224:
            out.add((surface, slot))
    return out


def corpus():
    """(surface, payload, where) candidate sentences."""
    rows = []
    # --- bank surface: every pointered name/label string + special segments
    for u in W.units(jp):
        for fld in [u.get("name")] + list(u.get("weapons", [])):
            if fld and fld.get("ptr"):
                b = jp.cstr(int(fld["ptr"], 16))
                if b:
                    rows.append(("bank", b, f"unit{u['utid']}"))
    for c in W.pilots(jp):
        if c.get("name", {}).get("ptr"):
            b = jp.cstr(int(c["name"]["ptr"], 16))
            if b:
                rows.append(("bank", b, f"cid{c.get('cid', '?')}"))
    for idn in range(L.IDCMD_COUNT):
        i = W.id_command(jp, idn)
        for k in ("name", "summary"):
            if (i.get(k) or {}).get("ptr"):
                b = jp.cstr(int(i[k]["ptr"], 16))
                if b:
                    rows.append(("bank", b, f"idn{idn}:{k}"))
    spec = W.special_records(jp)
    for kind in ("ability", "defense"):
        fname = L.SPECIAL_ABILITY_FILE if kind == "ability" else L.SPECIAL_DEFENSE_FILE
        data = jp.file(fname)
        for r in spec[kind]:
            raw = data[int(r["start"], 16):int(r["end"], 16)]
            for part in raw.split(b"\x00\x03"):
                part = part.strip(b"\x00")
                if part:
                    rows.append(("bank", part, f"{kind}{r['index']}"))
    # --- stage surface: barks + ID details (renderA-direct)
    for b in W.barks(jp):
        raw = jp.file(b["file"])[int(b["body"], 16):int(b["end"], 16)]
        for part in raw.replace(b"\x00\x04", b"\x00\x03").split(b"\x00\x03"):
            part = part.strip(b"\x00\x01\x02")
            if len(part) >= 2:
                rows.append(("stage", part, f"bark:{b['file']}@{b['record']}"))
    for d in W.id_details(jp):
        raw = bytes(jp.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]])
        for part in raw.split(b"\x00"):
            part = part.strip(b"\x01")
            if len(part) >= 2:
                rows.append(("stage", part, f"didx{d['didx']}"))
    return rows


def set_cover(rows):
    cov, picked = set(), []
    scored = []
    for surface, payload, where in rows:
        s = slots_of(payload, surface)
        if s:
            scored.append((surface, payload, where, s))
    # greedy: repeatedly take the row covering most uncovered slots
    remaining = scored
    while True:
        best, bestn = None, 0
        for r in remaining:
            n = len(r[3] - cov)
            if n > bestn:
                best, bestn = r, n
        if best is None or bestn == 0:
            break
        picked.append(best)
        cov |= best[3]
    all_slots = set().union(*(r[3] for r in scored)) if scored else set()
    return picked, cov, all_slots


def wqy_line(text: str, width: int):
    """Reference render of the DECODED text through WQY (independent font)."""
    cells = []
    for ch in text:
        if ch in "{}":  # escapes shown literally is fine; skip braces
            continue
        g = raster12(pcf, ch)
        cells.append(g)
    from PIL import Image
    img = Image.new("RGB", (max(1, min(width, 12 * len(cells))), 12), (26, 26, 34))
    for i, g in enumerate(cells):
        if g is None:
            continue
        x0 = i * 12
        if x0 + 12 > width:
            break
        for y, row in enumerate(g):
            for x, v in enumerate(row):
                if v:
                    img.putpixel((x0 + x, y), (255, 220, 120))
    return img


def sheets(picked):
    from PIL import Image, ImageDraw
    SCALE, PER = 2, 22
    rows_meta = []
    chunks = [picked[i:i + PER] for i in range(0, len(picked), PER)]
    paths = []
    for si, chunk in enumerate(chunks):
        W_ = 820
        H = 10 + len(chunk) * 62
        img = Image.new("RGB", (W_, H), (18, 24, 32))
        dr = ImageDraw.Draw(img)
        y = 8
        for k, (surface, payload, where, slots) in enumerate(chunk):
            payload = payload[:MAXLEN * 2]
            # macros must expand through the SURFACE's own dictionary in BOTH
            # panels (bank->system dict, stage->dialogue dict); otherwise the
            # top render disagrees on macro glyphs for a reason unrelated to
            # the slot->glyph identity we are auditing (the （効果）/（擦極）
            # artifact).  With the dict shared, any top/bottom mismatch is a
            # pure identity disagreement — the 振/戦 bug class.
            exp = jp.expand_sys if surface == "bank" else jp.expand
            oracle.expand = exp
            game = oracle.render_line(payload, surface, scale=SCALE)
            txt = decode_text(jp, payload, surface, exp, False)
            ref = wqy_line(txt, W_ - 130)
            ref = ref.resize((ref.width * SCALE, ref.height * SCALE),
                             Image.NEAREST)
            rid = si * PER + k
            dr.text((6, y + 8), f"#{rid:03d}", fill=(120, 200, 255))
            dr.text((6, y + 22), surface[:1].upper(), fill=(255, 160, 90))
            img.paste(game.crop((0, 0, min(game.width, W_ - 130),
                                 game.height)), (60, y))
            img.paste(ref, (60, y + 34))
            y += 62
            rows_meta.append({"row": rid, "surface": surface,
                              "where": where, "payload": payload.hex(),
                              "decoded": txt,
                              "slots": sorted(s for _f, s in slots)})
        p = OUT / f"pairs_{si:02d}.png"
        img.save(p)
        paths.append(str(p))
    (OUT / "rows.json").write_text(
        json.dumps(rows_meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return paths


def main():
    rows = corpus()
    picked, cov, universe = set_cover(rows)
    print(f"corpus sentences: {len(rows)}; picked rows: {len(picked)}; "
          f"slot coverage: {len(cov)}/{len(universe)}")
    paths = sheets(picked)
    print(f"sheets: {len(paths)} -> {paths[0]} ..")
    missing = sorted(universe - cov)
    if missing:
        print("uncovered slots:", missing[:20])


if __name__ == "__main__":
    main()
