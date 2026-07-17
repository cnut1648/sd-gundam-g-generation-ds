#!/usr/bin/env python3
"""extract_data_from_game.py — dump the JP game data (the single source of truth).

    python build/extract_data_from_game.py [--rom JP.nds] [--out data/jp]
    python build/extract_data_from_game.py --check          # committed == fresh?
    python build/extract_data_from_game.py --list           # category summary

The JP ROM is the only source of truth for Japanese game data.  This tool
walks the game's own tables (via utils/extract/) and writes the structured,
addressed dump under data/jp/: characters (name + ID commands + effect details
+ cut-ins + barks + bio), units (name + weapons + specials + bio), stages
(briefing + dialogue blocks in play order), event text, battle-effect banks,
parts, encyclopedia lists, dictionaries and the string-pointer graph.

Every record carries its exact ROM address — the KEY that the translation
mapping in data/ refers to.  The dump is deterministic: same ROM, same output,
byte for byte.  `--check` re-extracts and fails if the committed dump differs
(the `extraction_fresh` invariant); run this tool after improving the
extraction algorithm and commit the regenerated dump.

Curated extraction inputs (knowledge not derivable from the bytes) live in
data/extraction/: library_bio_map.json (bio index -> owner identity) and
stage_speakers.json (per-block speaker/choice attribution).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.extract.dump import build_dump, write_dump  # noqa: E402
from utils.extract.gamerom import GameROM  # noqa: E402

DEFAULT_ROM = REPO / "0098 - SD Gundam G Generation DS (Japan).nds"
DEFAULT_OUT = REPO / "data" / "jp"


def log(msg: str) -> None:
    print(f"[extract] {msg}", flush=True)


def _expected_source_sha1() -> str | None:
    mf = REPO / "data" / "manifest.json"
    if mf.exists():
        return json.loads(mf.read_text()).get("source_rom", {}).get("sha1")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rom", default=str(DEFAULT_ROM), help="JP source ROM (.nds)")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="dump directory (default data/jp)")
    ap.add_argument("--check", action="store_true",
                    help="verify the committed dump equals a fresh extraction")
    ap.add_argument("--list", action="store_true",
                    help="print a category/record summary and exit")
    args = ap.parse_args()

    rom_path = Path(args.rom)
    if not rom_path.exists():
        log(f"ERROR: source ROM not found: {rom_path}")
        return 1
    rom = GameROM(rom_path)
    if rom.is_zh:
        log("ERROR: this is a translated build; the dump must come from the JP source ROM")
        return 1
    import hashlib
    got = hashlib.sha1(rom.rom).hexdigest()
    want = _expected_source_sha1()
    if want and got != want:
        log(f"ERROR: ROM sha1 {got} != manifest source_rom {want}")
        return 1

    log(f"extracting from {rom_path.name}")
    dump = build_dump(rom)

    def _count(payload) -> int:
        return sum(len(v) for v in payload.values() if isinstance(v, list))

    if args.list:
        for rel in sorted(dump):
            log(f"  {rel:28s} {_count(dump[rel]):6d} records")
        return 0

    out_dir = Path(args.out)
    if args.check:
        stale, missing = [], []
        for rel, payload in dump.items():
            p = out_dir / rel
            if not p.exists():
                missing.append(rel)
                continue
            want_text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
            if p.read_text(encoding="utf-8") != want_text:
                stale.append(rel)
        extra = [str(p.relative_to(out_dir))
                 for p in out_dir.rglob("*.json")
                 if str(p.relative_to(out_dir)) not in dump]
        if stale or missing or extra:
            for r in missing:
                log(f"  MISSING  {r}")
            for r in stale:
                log(f"  STALE    {r}")
            for r in extra:
                log(f"  ORPHAN   {r}")
            log("FAIL: data/jp is not a fresh extraction — rerun this tool and commit")
            return 1
        log(f"OK: committed dump matches fresh extraction ({len(dump)} files)")
        return 0

    written = write_dump(dump, out_dir)
    total = sum(p.stat().st_size for p in written)
    log(f"wrote {len(written)} files under {out_dir} ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
