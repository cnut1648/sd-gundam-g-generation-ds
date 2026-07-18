#!/usr/bin/env python3
"""One-time mint for the phase-2 bio reflow rerun (the 109 phase1-edit records).

Demand: staging/glyph_gap.txt (42 chars from the 109 staged lib texts).
Supply decision (AGENTS.md glyph-minting order, docs/LESSONS_LEARNED §G):

* PROMOTE (7)  — identity-true slot_chars_extra cells whose atlas bitmap IS the
  demanded char (VLM decoder-audit identities, cells untouched since):
  no repaint, registration only.  Guard: the slot must be UNREGISTERED in
  two_byte_zh — the checkpoint mint repainted 47 cells but left their old
  slot_chars_extra identities behind (拷@1880 is now 著, 描@1912 is now 愤,
  版@1909 is now 泪): promoting on a stale identity would garble.
* PAINT (5)    — the last usable ZH-band cells (all 0x15-low tokens, legal on
  the library banks which encode with allow_low15=True; such chars can never
  be used in stage scripts — the encoder refuses them there):
    2549/2805/3573: unregistered duplicate-identity junk cells (糟/硫/锡),
    2293/4085:      zero-usage zero-demand registered cells (魇/襲) — the
                    census reclaim set.
  ZH-band tokens cannot occur in residual JP text (the JP atlas ends at slot
  2195), so the data/** token-aware scan in mint_glyphs.census() IS the full
  candidate-ROM text-surface proof for these cells.
* the remaining 30 low-frequency chars are reworded out of the staged texts
  (reword_bio_gap.py), the same demand-side resolution the campaign applied
  to its own 133-char tail (REWORD_BRIEF.md).

Charmap + atlas move together in this one run; report with text-art crops at
staging/mint_report2.txt, PNG crops at staging/mint_report2.png.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from pcf_raster import PCF, raster12, apply_shadow, cell_bytes, cell_to_grid, show  # noqa: E402
import mint_glyphs  # noqa: E402

PROMOTE = {"斥": 2176, "湖": 1944, "畔": 1945, "殉": 2062,
           "季": 1833, "寿": 2056, "菊": 1973}
PAINT = {"咖": 2549, "啡": 2805, "膝": 3573, "黎": 2293, "描": 4085}


def main():
    dry = "--write" not in sys.argv
    cm_path = REPO / "data/charmap.json"
    atlas_path = REPO / "data/font/atlas12.bin"
    cm = json.loads(cm_path.read_text())
    atlas = bytearray(atlas_path.read_bytes())
    registered = {s: ch for ch, s in cm["two_byte_zh"].items()}
    extra = cm.get("slot_chars_extra", {})

    print("recomputing census (zero-usage/zero-demand proof for paint targets)...")
    cen = mint_glyphs.census()
    reclaimable = {r["slot"] for r in cen["reclaimable"]}
    free_unused = {f["slot"] for f in cen["free_cells"] if not f["used"]}

    rep = []
    for ch, slot in PROMOTE.items():
        assert slot not in registered, \
            f"{ch}: slot {slot} already registered to {registered[slot]!r} (stale identity!)"
        assert extra.get(str(slot)) == ch, \
            f"{ch}: slot {slot} extra identity is {extra.get(str(slot))!r}"
        assert ch not in cm["two_byte_zh"], f"{ch} already registered"
        rep.append(f"=== {ch} -> slot {slot} (PROMOTED from slot_chars_extra, "
                   "bitmap already correct)\n"
                   + show(cell_to_grid(bytes(atlas[slot*36:slot*36+36]))))
        if not dry:
            # identity-true promotion: the cell is NOT repainted, so the
            # slot_chars_extra entry stays — it is the extractor's JP-ROM
            # decode identity (utils/extract/identities.py jpv override);
            # popping it regresses data/jp to {SLOT:n} (extraction_fresh RED).
            cm["two_byte_zh"][ch] = slot

    pcf = PCF()
    for ch, slot in PAINT.items():
        lo = (slot - 224 + 0xE000) & 0xFF
        assert lo not in (0x00,), f"{ch}: slot {slot} token low 0x{lo:02X} forbidden"
        assert slot in reclaimable or slot in free_unused, \
            f"{ch}: slot {slot} not proven reclaimable/free by census"
        assert ch not in cm["two_byte_zh"], f"{ch} already registered"
        g = raster12(pcf, ch)
        assert g is not None, f"{ch}: no WQY glyph"
        grid = apply_shadow(g)
        old = registered.get(slot)
        rep.append(f"=== {ch} -> slot {slot} (painted; token lo 0x{lo:02X}, "
                   f"reclaims {old!r} / junk identity {extra.get(str(slot))!r})\n"
                   + show(grid))
        if not dry:
            atlas[slot*36:slot*36+36] = cell_bytes(grid)
            if old is not None:
                del cm["two_byte_zh"][old]
            extra.pop(str(slot), None)
            cm["two_byte_zh"][ch] = slot

    print("\n".join(rep))
    if dry:
        print("\nDRY RUN (pass --write to commit charmap+atlas)")
        return
    atlas_path.write_bytes(bytes(atlas))
    cm_path.write_text(json.dumps(cm, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    (HERE / "staging/mint_report2.txt").write_text("\n".join(rep) + "\n",
                                                   encoding="utf-8")
    print(f"\nminted {len(PAINT)} + promoted {len(PROMOTE)}; "
          "report at staging/mint_report2.txt")


if __name__ == "__main__":
    main()
