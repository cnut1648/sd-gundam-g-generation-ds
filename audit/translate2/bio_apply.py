#!/usr/bin/env python3
"""Phase-2 library-bio applier: staging/out/lib -> data/zh/files/library/*.json.

Pipeline per bio record (324.bin chars / c4b.bin units):
  1. take the staged flat-prose paragraphs (one per JP {00}[々] section; a
     leading 「…」 quote paragraph merges into its section's page),
  2. re-flow each page into the viewer grid (18 cells/line, quote-indent
     lines {00}·{01}, plain continuation {00}·, <=6 lines/page),
  3. join pages with {00}々 and terminate with {00}{01} (REQUIRED),
  4. byte-fit the encoded record into the JP slot (0x00-padded in place).

Gap chars (not yet in charmap) are assumed 2-byte tokens for fit purposes;
the mint step registers them before the real encode.  --dry-run reports
per-record fit and the demand census; --write emits the three JSON tables
(gaps-only merge: committed edits are kept verbatim).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from utils import text_codec  # noqa: E402
from lib_apply import BREAK_AFTER, NO_LINE_START  # noqa: E402


def reflow(text: str, max_cells: int, max_lines: int):
    """DP line-break: fewest lines, then most punctuation-adjacent breaks,
    then balanced lines.  O(n * max_cells); replaces lib_apply.reflow's
    exhaustive search (built for short names) for long bio prose."""
    n = len(text)
    if n == 0:
        return []
    INF = (10 ** 9, 10 ** 9, 10 ** 9)
    # best[i] = (lines_used, -punct_breaks, spread_penalty) for text[:i]
    best = [INF] * (n + 1)
    prev = [-1] * (n + 1)
    best[0] = (0, 0, 0)
    for i in range(n):
        if best[i] == INF:
            continue
        for j in range(i + 1, min(i + max_cells, n) + 1):
            if j < n and text[j] in NO_LINE_START:
                continue
            lines, npunct, pen = best[i]
            bonus = 1 if (j == n or text[j - 1] in BREAK_AFTER) else 0
            cand = (lines + 1, npunct - bonus, pen + (max_cells - (j - i)) ** 2)
            if cand < best[j]:
                best[j] = cand
                prev[j] = i
    if best[n] == INF or best[n][0] > max_lines:
        return None
    lines = []
    j = n
    while j > 0:
        i = prev[j]
        lines.append(text[i:j])
        j = i
    return lines[::-1]

STG = HERE / "staging" / "out" / "lib"
LIB = REPO / "data" / "zh" / "files" / "library"

MAX_CELLS = 18          # glyph cells per display line (measured: JP + committed)
MAX_CELLS_QUOTE = 17    # {01}-indented quote continuation lines
MAX_LINES = 6           # lines per page (measured: JP + committed)

CM = text_codec.load_charmap()


def enc_len(text: str, gap: set[str]) -> int:
    """Byte length of a prose line once encoded (gap chars count as 2B)."""
    n = 0
    for ch in text:
        if ch in gap:
            n += 2
            continue
        try:
            n += len(text_codec.encode(ch, allow_low15=True, surface="stage"))
        except Exception:
            gap.add(ch)
            n += 2
    return n


def split_quote(par: str):
    """Split a leading 「…」 quote off a paragraph; returns (quote, rest)."""
    if not par.startswith("「"):
        return None, par
    i = par.find("」")
    if i < 0:
        return None, par
    return par[: i + 1], par[i + 1:]


def build_record(paragraphs: list[str], jp_pages: int):
    """Return (pages, flags): pages = list of list of (indent01, line_text)."""
    # merge a split-off leading quote paragraph back into its page
    paras = list(paragraphs)
    if len(paras) == jp_pages + 1 and paras[0].startswith("「"):
        paras = [paras[0] + paras[1]] + paras[2:]
    flags = []
    if len(paras) != jp_pages:
        flags.append(f"pagecount zh={len(paras)} jp={jp_pages}")
    pages = []
    for par in paras:
        quote, rest = split_quote(par)
        lines: list[tuple[bool, str]] = []
        if quote:
            # quote first line is plain; its continuations are {01}-indented
            q = reflow(quote, MAX_CELLS_QUOTE, 99) or [quote]
            if q == [quote] and len(quote) > MAX_CELLS_QUOTE:
                flags.append("quote reflow fail")
            lines.append((False, q[0]))
            lines += [(True, l) for l in q[1:]]
        if rest:
            r = reflow(rest, MAX_CELLS, 99)
            if r is None:
                flags.append("prose reflow fail (line overflow)")
                r = [rest[i:i + MAX_CELLS] for i in range(0, len(rest), MAX_CELLS)]
            lines += [(False, l) for l in r]
        # a page shows at most MAX_LINES lines: overflow continues on new pages
        for i in range(0, len(lines), MAX_LINES):
            pages.append(lines[i:i + MAX_LINES])
    return pages, flags


def render_text(pages) -> str:
    out = []
    for pi, lines in enumerate(pages):
        if pi:
            out.append("{00}々")
        for li, (ind, txt) in enumerate(lines):
            if li:
                out.append("{00}·{01}" if ind else "{00}·")
            out.append(txt)
    out.append("{00}{01}")
    return "".join(out)


def record_bytes_len(text: str, gap: set[str]) -> int:
    # markers: {00}=1B, 々/·/{01} are 1-2B tokens; estimate via per-char walk
    n = 0
    for tok in re.split(r"(\{[^}]*\})", text):
        if not tok:
            continue
        if tok.startswith("{"):
            n += 1  # {00} and {01} are single control bytes
        else:
            n += enc_len(tok, gap)
    return n


def jp_bio_index():
    idx = {}
    for f, key, kid in (("data/jp/characters.json", "characters", "cid"),
                        ("data/jp/units.json", "units", "utid")):
        d = json.loads((REPO / f).read_text())
        recs = d[key] if isinstance(d, dict) and key in d else d
        for r in (recs if isinstance(recs, list) else recs.values()):
            if isinstance(r, dict) and r.get("bio"):
                idx[(kid, r[kid])] = r["bio"]
    return idx


ORPHAN_91 = {"file": "324.bin", "off": "0x48C8", "size": 320, "index": 91,
             "text": "{00}々"}


def staged_texts():
    """(file, index) -> (rendered zh text, src name, flags) for every staged bio."""
    jp = jp_bio_index()
    out = {}
    for f in sorted(STG.glob("*.json")):
        d = json.loads(f.read_text())
        kind = d.get("kind")
        if kind == "char_bio" and "cid" in d:
            bio = jp.get(("cid", d["cid"]))
        elif kind == "char_bio" and d.get("orphan_index") == 91:
            bio = ORPHAN_91
        elif kind == "unit_bio":
            bio = jp.get(("utid", d["utid"]))
        else:
            continue
        if bio is None:
            print(f"WARN {f.name}: no JP bio record")
            continue
        jp_pages = bio["text"].removesuffix("{00}{01}").count("{00}々") + 1
        pages, flags = build_record(d["paragraphs"], jp_pages)
        out[(bio["file"], bio["index"])] = (render_text(pages), f.name, flags)
    return out


def jp_geometry():
    """file -> {index: (off, size)} for every record, orphan included."""
    jp = jp_bio_index()
    geo = {"324.bin": {}, "c4b.bin": {}}
    for bio in jp.values():
        geo[bio["file"]][bio["index"]] = (int(bio["off"], 16), bio["size"])
    geo["324.bin"][91] = (int(ORPHAN_91["off"], 16), ORPHAN_91["size"])
    return geo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="emit the bio_bank JSON tables into data/zh")
    args = ap.parse_args()

    staged = staged_texts()
    geo = jp_geometry()
    gap: set[str] = set()
    stats = Counter()

    for fname, rel, count, doc in (
            ("324.bin", "character_bios.json", 274, "Character encyclopedia (図鑑) biography bank"),
            ("c4b.bin", "unit_bios.json", 239, "Unit encyclopedia (図鑑) biography bank")):
        old = json.loads((LIB / rel).read_text())
        by_off = {int(e["offset"], 16): e for e in old.get("edits", [])}
        records = []
        for k in range(count):
            off, size = geo[fname][k]
            committed = by_off.get(off)
            if committed is not None:
                records.append({"index": k, "zh": committed["zh"],
                                "src": "phase1-edit"})
                stats[f"{fname} kept-committed"] += 1
            elif (fname, k) in staged:
                text, src, flags = staged[(fname, k)]
                nbytes = record_bytes_len(text, gap)
                records.append({"index": k, "zh": text, "src": src})
                stats[f"{fname} staged"] += 1
                if flags:
                    stats[f"{fname} flagged"] += 1
                    print(f"  flag {src}: {'; '.join(flags)}")
            else:
                records.append({"index": k, "jp_off": f"0x{off:X}",
                                "jp_len": size})
                stats[f"{fname} passthrough"] += 1
        table = {
            "file": fname,
            "what": (f"{doc}. Full rebuild in bio-index order (format bio_bank): "
                     "zh records re-encode grown translated prose (4-aligned); "
                     "jp_off/jp_len records pass the original Japanese bytes "
                     "through verbatim. The arm9 offset table + resource-size "
                     "word are DERIVED from this table at build time "
                     "(utils.arm9_layout._apply_bio_offsets) so record growth "
                     "cannot desynchronize them. Byte grammar per record: "
                     "{00}=line break, {00}·=continuation line, {00}·{01}="
                     "quote-indent line, {00}々=page break, {00}{01}=record "
                     "terminator (REQUIRED)."),
            "format": "bio_bank",
            "records": records,
        }
        if args.write:
            (LIB / rel).write_text(
                json.dumps(table, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8")
    print(dict(stats))
    if gap:
        print("REMAINING GAP CHARS (must be zero to build):", "".join(sorted(gap)))
    print("wrote tables" if args.write else "dry run (no files written)")


if __name__ == "__main__":
    main()
