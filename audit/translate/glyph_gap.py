#!/usr/bin/env python3
"""Scan staged ZH translations and report characters that need glyph work.

For each char used in the refined translations, check whether it has an encodable
slot.  Trampoline surfaces (names, weapons, specials on the 'bank' path) need a
ZH-band slot (>= 2196) OR an approved zh-extra; stage surfaces (cutins, details,
barks) accept 1-byte + JP-band + ZH-band.  Chars missing a ZH-band slot must be
minted before they can go on a trampoline surface (AGENTS.md)."""
import sys, json, glob, collections
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from utils import text_codec

cm = text_codec.load_charmap()
ST = REPO / "audit/translate"

def encodable(ch, surface):
    try:
        text_codec.encode(ch, surface=surface)
        return True
    except ValueError:
        return False

# collect chars per surface from all staged *_zh_*.json
bank_chars = collections.Counter()   # names/weapons/specials -> trampoline
stage_chars = collections.Counter()  # cutin/detail/bark -> stage
def add(s, counter):
    for ch in (s or ""):
        if ch in "{}":            # skip escape braces
            continue
        counter[ch] += 1

for p in glob.glob(str(ST / "units_zh_*.json")) + [str(ST / "units_zh.json")]:
    if not Path(p).exists(): continue
    for utid, u in json.load(open(p)).items():
        add(u.get("name"), bank_chars)
        for w in (u.get("weapons") or {}).values(): add(w, bank_chars)
        for sp in (u.get("specials") or []): add(sp.get("zh"), bank_chars)
for p in glob.glob(str(ST / "characters_zh_*.json")):
    for cid, c in json.load(open(p)).items():
        add(c.get("name"), bank_chars)
        for idc in (c.get("ids") or []):
            add(idc.get("name"), bank_chars); add(idc.get("summary"), bank_chars)
            add(idc.get("detail"), stage_chars); add(idc.get("cutin"), stage_chars)
        for b in (c.get("barks") or []): add(b, stage_chars)

def report(name, counter, surface):
    miss = sorted({ch for ch in counter if not ch.isspace() and not encodable(ch, surface)})
    print("== %s (surface=%s): %d distinct chars, %d NEED GLYPH ==" % (name, surface, len(counter), len(miss)))
    if miss:
        print("   missing:", "".join(miss))
    return miss

mb = report("bank/trampoline (names/weapons/specials)", bank_chars, "bank")
ms = report("stage (cutins/details/barks)", stage_chars, "stage")
allmiss = sorted(set(mb) | set(ms))
Path(ST / "glyph_gap.txt").write_text("bank_missing: %s\nstage_missing: %s\n" % ("".join(mb), "".join(ms)), encoding="utf-8")
print("\nTOTAL distinct chars needing a glyph/mint:", len(allmiss))
print("".join(allmiss))
