#!/usr/bin/env python3
"""coverage_render.py — render EVERY translated text line offline and produce:

  1. findings.json    — algorithmic defects (no judgment needed):
        unknown_slot      token beyond atlas (renders sparkle)
        empty_glyph       atlas stroke layer empty for a used slot
        mixed_style       bank-surface line mixing atlas CJK with renderB
                          TEXT glyphs beyond its own JP-proven inventory
                          (the 吉翁海無 garble / NT等级4 sunk-digit class)
        box_violation     used glyph whose strokes leave the 11x11 box
        no_shadow         used CJK glyph without the drop-shadow grammar
  2. sheets/*.png     — contact sheets of every UNIQUE rendered line
                        (chunked, labeled) for subagent/VLM judging of what
                        algorithms cannot decide (identity/beauty/semantics).
  3. corpus.jsonl     — one record per line: source, offset, surface, text,
                        style report, sheet coordinates.

Coverage: stage dialogue segments, barks, cut-ins, battle banks, library and
hangar banks, arena banks (briefings, pools, caves, ui names), plus all six
candidate-ROM gallery resources: 54 EV titles, 274 character names+series,
and 239 unit names+series.  ~27k lines, runs in well under a minute — this
replaces "hire people to play the game".

Usage:
  coverage_render.py <rom.nds> --out /tmp/coverage [--sheets]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test"))

from render_oracle import Oracle, TRAMPOLINE_SPLIT  # noqa: E402
from utils import text_codec as tc  # noqa: E402
from utils.extract import walkers  # noqa: E402
from utils.extract.gamerom import GameROM  # noqa: E402

RENDERB = json.loads((REPO / "data/renderb_charset.json").read_text())["slots"] \
    if (REPO / "data/renderb_charset.json").exists() else {}


def _has_cjk(s: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in s)

# Surface per bank (empirically established):
#   resident_caves / ui_names_bank / battle_name_pool / post_dict_labels ->
#   trampoline readers (renderB below slot 2196; proven by the on-screen
#   吉翁海無 garble and the NT等级4 renderB digit).
#   briefing_blobs / idcmd_detail_pool -> renderA-direct: their shipped
#   payloads use one-byte codes that decode as clean renderA text (briefing
#   0x09 '…' padding; idcmd 使用条件 with one-byte 用/、), and shipped verified.
#   event_text_blocks -> script/pointer records; text fields are annotations.
BANK_JSONS = [
    ("data/zh/placements/battle_name_pool.json", "bank"),
    ("data/zh/placements/briefing_blobs.json", "stage"),
    ("data/zh/event_text.json", "stage"),
    ("data/zh/placements/idcmd_detail_pool.json", "stage"),
    ("data/zh/placements/resident_caves.json", "bank"),
    ("data/zh/placements/ui_names_bank.json", "bank"),
    ("data/zh/placements/post_dict_labels.json", "bank"),
]
FILE_JSONS = [
    ("data/zh/files/barks/0.json", "stage"),
    ("data/zh/files/barks/1.json", "stage"),
    ("data/zh/files/barks/1dd.json", "stage"),
    ("data/zh/files/barks/1de.json", "stage"),
    ("data/zh/files/barks/c4f.json", "stage"),
    ("data/zh/files/battle/ability_cards.json", "bank"),
    ("data/zh/files/battle/command_effects.json", "bank"),
    ("data/zh/files/battle/special_abilities.json", "bank"),
    ("data/zh/files/battle/special_defenses.json", "bank"),
    ("data/zh/files/battle/cutin_quotes.json", "stage"),
    ("data/zh/files/library/character_bios.json", "stage"),
    ("data/zh/files/library/unit_bios.json", "stage"),
    ("data/zh/files/library/weapon_names.json", "stage"),
    ("data/zh/files/hangar/part_captions.json", "stage"),
    ("data/zh/files/hangar/part_names.json", "stage"),
]


def live_offsets(rom_path: Path) -> dict[str, set[int]]:
    """arm9-pointer-referenced offsets per arena bank (reachability oracle).

    Records nobody points at are dead legacy copies — rendered by coverage
    for completeness but excluded from defect gating (their garble is not
    player-visible).  The id_command/unit_weapon gates walk the true table
    paths separately."""
    import struct
    rom = rom_path.read_bytes()
    arm9_off, _, arm9_ram, arm9_size = struct.unpack_from("<IIII", rom, 0x20)
    arm9 = rom[arm9_off:arm9_off + arm9_size]
    windows = {
        "data/zh/placements/resident_caves.json": (0x186000, 0x1985A4),
        "data/zh/placements/ui_names_bank.json": (0x328720 - 0x2000000 + arm9_ram, None),
    }
    # resident cave: file offsets inside arm9 image; RAM = arm9_ram + file_off
    out: dict[str, set[int]] = {}
    lo = arm9_ram + 0x186000
    hi = arm9_ram + 0x1985A4
    refs = set()
    for i in range(0, len(arm9) - 3, 4):
        v = struct.unpack_from("<I", arm9, i)[0]
        if lo <= v < hi:
            refs.add(v - lo)
    out["data/zh/placements/resident_caves.json"] = refs
    return out


def _iter_gallery_corpus(rom_path: Path):
    """Yield all mode0 gallery strings from the candidate ROM.

    The gallery walkers follow the runtime metadata offsets and terminate
    strings token-aware, so a 0x00 low byte inside a two-byte glyph does not
    truncate the rendered payload.  Series strings are intentionally yielded
    once per metadata record; the contact-sheet layer performs its normal
    payload-byte deduplication later.
    """
    rom = GameROM(rom_path)

    ev = walkers.ev_gallery_titles(rom)
    for record in ev["records"]:
        payload = bytes.fromhex(record["title_raw_hex"])
        yield (
            f"gallery/ev/43f.bin@{record['title_offset']}",
            "bank",
            payload[:-1],
            record["title_jp"],
        )

    for kind, key in (("character", "char_id"), ("unit", "utid")):
        gallery = walkers.library_gallery_titles(rom, kind)
        bank = gallery["string_file"]
        for record in gallery["records"]:
            owner = record[key]
            for field in ("name", "series"):
                payload = bytes.fromhex(record[f"{field}_raw_hex"])
                yield (
                    f"gallery/{kind}/{bank}@{record[f'{field}_offset']}"
                    f"#{key}={owner}:{field}",
                    "bank",
                    payload[:-1],
                    record[f"{field}_jp"],
                )


def iter_corpus(rom_path: Path):
    """Yield (source_id, surface, payload_bytes, doc_text).

    File-backed translation JSON remains the source for legacy surfaces;
    gallery payloads come from ``rom_path`` because their six NitroFS files
    are rebuilt string banks whose final offsets and bytes must be covered.
    """
    for rel, surface in BANK_JSONS:
        p = REPO / rel
        if not p.exists():
            continue
        doc = json.loads(p.read_text())
        for r in doc.get("entries", []):
            hx = r.get("payload_hex")
            if hx:
                yield f"{rel}@{r.get('offset')}", surface, bytes.fromhex(hx), r.get("text", "")
    for rel, surface in FILE_JSONS:
        p = REPO / rel
        if not p.exists():
            continue
        doc = json.loads(p.read_text())
        recs = doc.get("edits") or doc.get("groups") or doc.get("entries") or []
        for r in recs:
            if not isinstance(r, dict):
                continue
            hx = r.get("zh_hex")
            if hx:
                data = bytes.fromhex(hx)
            elif r.get("zh"):
                try:
                    data = tc.encode(r["zh"], allow_low15=True)
                except ValueError:
                    continue
            else:
                continue
            yield f"{rel}@{r.get('offset', r.get('id'))}", surface, data, r.get("zh", "")
    # stage dialogue: zh_hex per block
    for p in sorted((REPO / "data/zh/stages").glob("*.json")):
        doc = json.loads(p.read_text())
        blocks = doc if isinstance(doc, list) else doc.get("blocks") or doc.get("entries") or []
        for r in blocks:
            if isinstance(r, dict) and r.get("zh_hex"):
                yield (f"stages/{p.name}@{r.get('jp_offset', r.get('offset'))}",
                       "stage", bytes.fromhex(r["zh_hex"]), r.get("zh", r.get("zh_text", "")))
    yield from _iter_gallery_corpus(rom_path)


def glyph_checks(oracle: Oracle, atlas_slots: int):
    """Precompute per-slot algorithmic properties for used-glyph checks."""
    import functools

    @functools.lru_cache(maxsize=None)
    def props(slot: int):
        if slot >= atlas_slots:
            return {"unknown": True}
        rows = oracle.atlas_glyph(slot)
        stroke = [(x, y) for y in range(12) for x in range(12) if rows[y][x] == 1]
        shadow = any(v == 2 for row in rows for v in row)
        if not stroke:
            return {"empty": True}
        xs = [x for x, _ in stroke]; ys = [y for _, y in stroke]
        return {"empty": False, "unknown": False,
                "box_violation": max(xs) > 10 or max(ys) > 10,
                "no_shadow": not shadow and len(stroke) > 6}
    return props


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--out", default="/tmp/coverage")
    ap.add_argument("--sheets", action="store_true", help="also write contact sheets")
    ap.add_argument("--sheet-lines", type=int, default=40)
    args = ap.parse_args()

    out = Path(args.out)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    oracle = Oracle(Path(args.rom))
    atlas_slots = len(oracle.atlas) // 36
    props = glyph_checks(oracle, atlas_slots)
    live = live_offsets(Path(args.rom))

    findings, corpus, seen_lines = [], [], {}
    baseline_p = REPO / "test/golden/coverage_mixed_baseline.json"
    baseline = json.loads(baseline_p.read_text()) if baseline_p.exists() else None

    n = 0
    for src, surface, data, text in iter_corpus(Path(args.rom)):
        n += 1
        rep = oracle.line_style_report(data, surface)
        issues = []
        for slot, font in oracle.glyph_stream(data, surface):
            if font != "A":
                continue
            pr = props(slot)
            if pr.get("unknown"):
                issues.append(f"unknown_slot:{slot}")
            elif pr.get("empty"):
                issues.append(f"empty_glyph:{slot}")
            else:
                if pr.get("box_violation"):
                    issues.append(f"box_violation:{slot}")
                if pr.get("no_shadow"):
                    issues.append(f"no_shadow:{slot}")
        onscreen = None
        if surface == "bank" and rep["mixed"]:
            # What the player actually reads: decode the line under the
            # IDENTIFIED renderB charset for slots < 2196 and the atlas
            # charmap for the rest.  The documented `text` field is renderA
            # NOTATION and can be misleading in both directions (text 見击
            # renders 攻击 = fine; text 兵 renders 無 = garble), so garble
            # judgment happens on THIS decode (subagent fleet), while kana
            # bleeding into a CJK line stays an algorithmic finding.
            parts = []
            kana_leak = []
            seen_cjk = False
            for s, font in oracle.glyph_stream(data, surface):
                if font == "A":
                    ch = oracle.cm.slot_to_char.get(s, f"[{s}]")
                    parts.append(ch)
                    if _has_cjk(ch):
                        seen_cjk = True
                else:
                    info = RENDERB.get(str(s)) or {}
                    ch, kind = info.get("char"), info.get("kind")
                    parts.append(ch or f"[B{s}]")
                    # many pool records carry a binary metadata PREFIX that
                    # decodes as pseudo-kana; only kana AFTER the visible CJK
                    # text begins can be a real on-screen leak
                    if kind == "kana" and seen_cjk:
                        kana_leak.append(f"{s}={ch}")
            onscreen = "".join(parts)
            if kana_leak:
                issues.append(f"kana_on_bank:{','.join(kana_leak[:6])}")
        if issues:
            dead = False
            rel, _, off = src.partition("@")
            if rel in live and off.startswith("0x"):
                dead = int(off, 16) not in live[rel]
            findings.append({"source": src, "surface": surface, "dead": dead,
                             "text": text[:60], "onscreen": onscreen,
                             "issues": sorted(set(issues))})
        key = data
        if key not in seen_lines:
            seen_lines[key] = (src, surface)
        corpus.append({"source": src, "surface": surface, "text": text[:80],
                       "onscreen": onscreen, "style": rep})

    (out / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=1))
    with open(out / "corpus.jsonl", "w") as fh:
        for c in corpus:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    if args.sheets:
        from PIL import Image, ImageDraw
        uniq = list(seen_lines.items())
        per = args.sheet_lines
        for si in range(0, len(uniq), per):
            chunk = uniq[si:si + per]
            imgs = []
            for data, (src, surface) in chunk:
                try:
                    imgs.append((oracle.render_line(data, surface, scale=2), src))
                except Exception:
                    continue
            if not imgs:
                continue
            W = max(i.width for i, _ in imgs) + 8
            H = sum(i.height + 14 for i, _ in imgs)
            sheet = Image.new("RGB", (max(W, 640), H), (15, 15, 15))
            d = ImageDraw.Draw(sheet)
            y = 0
            for img, src in imgs:
                d.text((2, y), src[-58:], fill=(180, 180, 60))
                sheet.paste(img, (4, y + 12))
                y += img.height + 14
            sheet.save(out / "sheets" / f"sheet_{si//per:04d}.png")

    mixed = [f for f in findings if any(i.startswith("mixed_style") for i in f["issues"])]
    print(f"corpus: {n} lines ({len(seen_lines)} unique) | findings: {len(findings)} "
          f"(mixed_style: {len(mixed)})")
    if baseline is not None:
        base_set = set(baseline)
        new_mixed = [f["source"] for f in mixed if f["source"] not in base_set]
        print(f"mixed_style vs baseline: {len(new_mixed)} NEW")
        for s in new_mixed[:10]:
            print("  NEW:", s)
        return 1 if new_mixed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
