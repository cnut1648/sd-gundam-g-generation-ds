#!/usr/bin/env python3
"""Per-slot CONTEXTUAL identity evidence for the decoder audit fleet.

For every atlas slot the game actually draws, gather real game sentences that
use it, render them as PIXELS exactly like the game (oracle), box the target
glyph in red, and pair each with the decoder's text (target char in 【】).
A VLM subagent then judges, in context, whether the decoder's claimed
character matches the boxed pixels — the owner's "contextual, not standalone"
method (isolated 12x12 kanji are unreadable; in a sentence they are obvious).

Emits, per chunk of N slots:
  staging/ident_fleet/chunk_XXX.png    stacked rows: [big cell][context strips]
  staging/ident_fleet/chunk_XXX.json   {slots:[{slot,claimed,contexts:[...]}]}
and staging/ident_fleet/index.json listing all chunks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "test"))
from lib_apply import open_roms  # noqa: E402
from render_oracle import Oracle  # noqa: E402
from utils.extract import walkers as W  # noqa: E402
from build.build_guide import decode_text  # noqa: E402
import utils.extract.layout as L  # noqa: E402

REPO = HERE.parent.parent
OUT = HERE / "staging/ident_fleet"
OUT.mkdir(parents=True, exist_ok=True)
jp, zh = open_roms()
oracle = Oracle(REPO / "0098 - SD Gundam G Generation DS (Japan).nds")

SLOTS_PER_CHUNK = 10
MAX_CTX = 3
MAXLEN = 28


def resolvers():
    cm = json.loads((REPO / "data/charmap.json").read_text())
    jpv = {}
    for k, ch in cm["jp_slot_chars"].items():
        jpv[int(k)] = ch
    for k, ch in cm.get("slot_chars_extra", {}).items():
        jpv[int(k)] = ch
    for ch, s in cm["two_byte_zh"].items():
        jpv.setdefault(int(s), ch)
    return jpv


def corpus():
    rows = []
    for b in W.barks(jp):
        rows.append(("stage", jp.file(b["file"])[int(b["body"], 16):int(b["end"], 16)],
                     f"bark {b['file']}@{b['record']}"))
    for d in W.id_details(jp):
        rows.append(("stage", bytes(jp.arm9[int(d["off"], 16):int(d["off"], 16) + d["len"]]),
                     f"didx{d['didx']}"))
    sp = W.special_records(jp)
    for kind in ("ability", "defense"):
        fn = L.SPECIAL_ABILITY_FILE if kind == "ability" else L.SPECIAL_DEFENSE_FILE
        for r in sp[kind]:
            rows.append(("bank", jp.file(fn)[int(r["start"], 16):int(r["end"], 16)],
                         f"{kind}{r['index']}"))
    for u in W.units(jp):
        if (u.get("name") or {}).get("ptr"):
            rows.append(("bank", jp.cstr(int(u["name"]["ptr"], 16)), f"unit{u['utid']}"))
    for idn in range(L.IDCMD_COUNT):
        i = W.id_command(jp, idn)
        for k in ("name", "summary"):
            if (i.get(k) or {}).get("ptr"):
                rows.append(("bank", jp.cstr(int(i[k]["ptr"], 16)), f"idn{idn}:{k}"))
    return rows


def stream(payload, surface):
    oracle.expand = jp.expand if surface == "stage" else jp.expand_sys
    return list(oracle.glyph_stream(payload, surface))


def render_hl(payload, surface, target_slot, scale=3):
    """render_line with a red box around every glyph == target_slot."""
    from PIL import Image, ImageDraw
    glyphs = stream(payload, surface)[:MAXLEN]
    W_ = max(1, sum(12 if f == "A" else 8 for _s, f in glyphs))
    img = Image.new("RGB", (W_, 16), (0, 90, 0))
    px = img.load()
    boxes, x0 = [], 0
    for slot, font in glyphs:
        if font == "A":
            rows_, w, yoff = oracle.atlas_glyph(slot), 12, (3 if surface == "bank" else 1)
        else:
            rows_, w, yoff = oracle.renderb_glyph(slot), 8, 0
        for y, row in enumerate(rows_):
            for x, v in enumerate(row):
                if v == 2:
                    px[x0 + x, yoff + y] = (255, 255, 255)
                elif v == 1 and px[x0 + x, yoff + y] == (0, 90, 0):
                    px[x0 + x, yoff + y] = (30, 30, 30)
        if slot == target_slot:
            boxes.append((x0, x0 + w))
        x0 += w
    img = img.resize((W_ * scale, 16 * scale), Image.NEAREST)
    dr = ImageDraw.Draw(img)
    for a, b in boxes:
        dr.rectangle([a * scale, 0, b * scale - 1, 16 * scale - 1],
                     outline=(255, 40, 40))
    return img


def marked_text(payload, surface, target_slot, jpv):
    """decoded text with 【claimed】 around the target glyph occurrences."""
    oracle.expand = jp.expand if surface == "stage" else jp.expand_sys
    out = []
    for slot, font in stream(payload, surface):
        ch = jpv.get(slot, "□")
        out.append(f"【{ch}】" if slot == target_slot else ch)
    return "".join(out)[:MAXLEN + 8]


def main():
    jpv = resolvers()
    rows = corpus()
    # slot -> contexts (surface, payload, where), prefer shorter & distinct text
    slot_ctx = {}
    for surface, payload, where in rows:
        seen_here = set()
        for slot, font in stream(payload, surface):
            if font != "A" or slot < 224 or slot in seen_here:
                continue
            seen_here.add(slot)
            slot_ctx.setdefault(slot, [])
            if len(slot_ctx[slot]) < 12:
                slot_ctx[slot].append((surface, payload, where))
    inuse = sorted(slot_ctx)
    # cell big-render helper
    from PIL import Image, ImageDraw
    def cell_img(slot, scale=6):
        rows_ = oracle.atlas_glyph(slot)
        im = Image.new("RGB", (12, 12), (20, 20, 28))
        for y, r in enumerate(rows_):
            for x, v in enumerate(r):
                if v == 2:
                    im.putpixel((x, y), (255, 255, 255))
                elif v == 1:
                    im.putpixel((x, y), (90, 90, 90))
        return im.resize((12 * scale, 12 * scale), Image.NEAREST)

    chunks = [inuse[i:i + SLOTS_PER_CHUNK] for i in range(0, len(inuse), SLOTS_PER_CHUNK)]
    index = []
    for ci, chunk in enumerate(chunks):
        meta = {"chunk": ci, "slots": []}
        # pick up to MAX_CTX shortest distinct contexts per slot
        rowscan = []
        for slot in chunk:
            ctxs = sorted(slot_ctx[slot], key=lambda t: len(t[1]))[:MAX_CTX]
            entries = []
            imgs = []
            for surface, payload, where in ctxs:
                entries.append({"where": where, "surface": surface,
                                "decoded": marked_text(payload, surface, slot, jpv)})
                imgs.append(render_hl(payload, surface, slot))
            meta["slots"].append({"slot": slot, "claimed": jpv.get(slot, "□"),
                                  "contexts": entries})
            rowscan.append((slot, jpv.get(slot, "□"), imgs))
        # compose the chunk PNG
        SC = 3
        rowh = 16 * SC + 8
        H = 12 + len(rowscan) * (rowh + 6)
        Wd = 900
        img = Image.new("RGB", (Wd, H), (16, 20, 28))
        dr = ImageDraw.Draw(img)
        y = 8
        for slot, ch, imgs in rowscan:
            dr.text((6, y + 4), f"slot {slot}", fill=(120, 200, 255))
            dr.text((6, y + 20), f"claim {ch}", fill=(255, 210, 120))
            img.paste(cell_img(slot, 4), (78, y))
            x = 140
            for im in imgs:
                if x + im.width > Wd - 6:
                    break
                img.paste(im, (x, y + 4))
                x += im.width + 14
            y += rowh + 6
            dr.line([(0, y - 3), (Wd, y - 3)], fill=(40, 48, 60))
        p = OUT / f"chunk_{ci:03d}.png"
        img.save(p)
        (OUT / f"chunk_{ci:03d}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        index.append({"chunk": ci, "png": str(p), "n": len(chunk),
                      "slots": chunk})
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    print(f"in-use atlas slots: {len(inuse)}; chunks: {len(chunks)} "
          f"({SLOTS_PER_CHUNK}/chunk)")


if __name__ == "__main__":
    main()
