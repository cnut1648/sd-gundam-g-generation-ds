#!/usr/bin/env python3
"""renderB identity harness — complete the 8x16 UI-font identity registry.

The renderB font (data/renderb_charset.json) is only partially identified; every
BANK-surface record whose slot is unidentified decodes as {B:n}.  This tool
enumerates every USED-but-unidentified renderB slot across data/jp (real `text`
fields only — never the wrong-surface `text_stage` comparison field), renders
each 8x16 glyph, gathers its in-context occurrences, and records its
READING-ORDER neighbours (the font sorts kanji by on'yomi gojuon, so an
unidentified slot's kanji reading is bracketed by the nearest identified slots).

Output per chunk: chunk_NNN.png (labelled glyph contact sheet) + chunk_NNN.json
(per-slot context + neighbours).  A subagent fleet reads both and returns
{slot: char}.  Cross-check = re-decode the same records and confirm coherent
words (a wrong identity breaks the word).
"""
from __future__ import annotations
import json, re, glob, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "test"))
from render_oracle import Oracle
from PIL import Image, ImageDraw

OUT = REPO / "audit/translate2/staging/rb_fleet"
CHUNK = 48
BP = re.compile(r"\{B:(\d+)\}")


def load_identified():
    S = json.loads((REPO / "data/renderb_charset.json").read_text())["slots"]
    return {int(k): v["char"] for k, v in S.items() if v.get("char")}


def gather():
    """slot -> list of context snippets (from real text fields, reachable only)."""
    ctx: dict[int, list[str]] = {}
    def walk(n, reach):
        if isinstance(n, dict):
            r = n.get("reachable", reach)
            for k, v in n.items():
                if k == "text_stage":       # wrong-surface comparison field: skip
                    continue
                if isinstance(v, str):
                    if r is False:
                        continue
                    for m in BP.finditer(v):
                        sl = int(m.group(1))
                        snip = v[max(0, m.start() - 8):m.end() + 8]
                        ctx.setdefault(sl, [])
                        if snip not in ctx[sl] and len(ctx[sl]) < 10:
                            ctx[sl].append(snip)
                else:
                    walk(v, r)
        elif isinstance(n, list):
            for v in n:
                walk(v, reach)
    for f in sorted(glob.glob(str(REPO / "data/jp/**/*.json"), recursive=True)):
        walk(json.loads(Path(f).read_text()), None)
    return ctx


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    o = Oracle(str(REPO / "0098 - SD Gundam G Generation DS (Japan).nds"))
    ided = load_identified()
    ctx = gather()
    slots = sorted(ctx)
    print(f"unidentified used renderB slots: {len(slots)}")
    lut = {0: 0, 1: 255, 2: 120}
    scale = 9
    cols = 8
    cellw, cellh = 8 * scale + 8, 16 * scale + 16
    chunks = [slots[i:i + CHUNK] for i in range(0, len(slots), CHUNK)]
    for ci, ch in enumerate(chunks):
        rows = (len(ch) + cols - 1) // cols
        img = Image.new("RGB", (cellw * cols, cellh * rows), (18, 18, 18))
        d = ImageDraw.Draw(img)
        meta = []
        for i, s in enumerate(ch):
            x, y = (i % cols) * cellw, (i // cols) * cellh
            g = Image.new("L", (8, 16))
            g.putdata([lut[v] for r in o.renderb_glyph(s) for v in r])
            g = g.resize((8 * scale, 16 * scale), Image.NEAREST)
            img.paste(g, (x + 2, y + 14))
            d.text((x + 2, y + 2), str(s), fill=(255, 255, 0))
            lo = max((k for k in ided if k < s), default=None)
            hi = min((k for k in ided if k > s), default=None)
            meta.append({
                "slot": s,
                "contexts": ctx[s],
                "prev_identified": {"slot": lo, "char": ided.get(lo)},
                "next_identified": {"slot": hi, "char": ided.get(hi)},
            })
        img.save(OUT / f"chunk_{ci:03d}.png")
        (OUT / f"chunk_{ci:03d}.json").write_text(
            json.dumps({"chunk": ci, "slots": meta}, ensure_ascii=False, indent=1))
    print(f"wrote {len(chunks)} chunks to {OUT}")


if __name__ == "__main__":
    main()
