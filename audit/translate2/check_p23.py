#!/usr/bin/env python3
"""Mechanical validator for one phase-2/3 fleet output file.

Usage:
    python3 audit/translate2/check_p23.py audit/translate2/staging/out/stages/_STG00.json
    python3 audit/translate2/check_p23.py audit/translate2/staging/out/lib/char_4.json
    python3 audit/translate2/check_p23.py --all      # sweep everything present

Enforces the briefs' §6 self-checks that can be checked mechanically:
  * strict JSON, right id/kind, right keys;
  * stage: title + every owned briefing off + exactly the TRANSLATE_OFFSETS
    (from staging/assign/stages/<stem>.json) present in dump order — skipped
    offs must be listed in "skipped" AND mentioned in notes;
  * no JP copied back (zh must differ from the JP text and contain no kana);
  * marker rules: no {00}/{01} except choice-{00} and {01} in records whose JP
    used {01} as structure (choice / multi-field / title split);
  * charset: every char encodable on the stage surface (text_codec), no ，～“”
    fullwidth ％＋, ellipsis in even pairs, title on the strict pool charset;
  * bio paragraphs count == JP page-section count (「」-open bios may split the
    quote off page 1: +1 allowed);
  * weapon batch: every assigned off present, names use terms.md when matched.
Exit 0 = PASS, 1 = FAIL (with per-violation lines).
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from utils import text_codec  # noqa: E402

CM = text_codec.load_charmap()
STG_DIR = REPO / "audit/translate2/staging"
ASSIGN = STG_DIR / "assign"
OUT = STG_DIR / "out"

KANA = re.compile(r"[\u3041-\u3096\u30A1-\u30FA\u30FD\u30FE]")
BAD_PUNCT = {"，": "use 、", "～": "forbidden", "“": "use 「", "”": "use 」",
             "％": "half-width %", "＋": "half-width +", "　": "no fullwidth space",
             "‘": "use 『", "’": "use 』", "：": "avoid fullwidth colon",
             "；": "avoid fullwidth semicolon"}
TITLE_OK_PUNCT = set("！…・")  # ・ tolerated: phase-1 names use it
TITLE_BAD = set("、。？：；（）「」『』")
MARK = re.compile(r"\{[0-9A-Fa-f]{2}\}|\{SLOT:\d+\}|\{B:\d+\}|\{F0:\d+\}")


def strip_marks(s):
    return MARK.sub("", s)


OK_PUNCT = set("、。！？…・（）「」『』·%+ ")  # brief §4 whitelist (no space really)


def _allowed_char(ch):
    """Brief §4: CJK (new hanzi are MINTED at apply time), listed punctuation,
    half-width digits, Latin letters, ·, half-width % +."""
    o = ord(ch)
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return True
    if ch.isascii() and (ch.isalnum() or ch in "%+"):
        return True
    return ch in OK_PUNCT and ch != " "


def check_charset(s, where, errs, title=False):
    txt = strip_marks(s)
    for ch in txt:
        if ch in BAD_PUNCT:
            errs.append(f"{where}: forbidden char {ch!r} ({BAD_PUNCT[ch]})")
        elif title and ch in TITLE_BAD:
            errs.append(f"{where}: title-forbidden punctuation {ch!r}")
        elif not _allowed_char(ch):
            # locked terms may need glyphs beyond the §4 core (ν高达, ∀, Ⅱ…):
            # legal iff the stage-surface encoder actually has a slot for it
            if text_codec.encode_char(ch, CM, surface="stage") is None:
                errs.append(f"{where}: char {ch!r} outside brief §4 charset "
                            f"and unencodable on the stage surface")
    runs = re.findall(r"…+", txt)
    for r in runs:
        if len(r) % 2:
            errs.append(f"{where}: odd ellipsis run ({len(r)} of …)")
    if "{00}" in s and "『" not in s:
        errs.append(f"{where}: manual {{00}} outside a 『choice』 record")


def kana_check(zh, jp, where, errs):
    m = KANA.search(strip_marks(zh))
    if m:
        errs.append(f"{where}: kana {m.group()!r} in Chinese output")
    if strip_marks(zh) and strip_marks(zh) == strip_marks(jp):
        errs.append(f"{where}: zh identical to JP (untranslated)")


# ---------------------------------------------------------------- stages ----
def check_stage(path):
    errs = []
    o = json.loads(path.read_text(encoding="utf-8"))
    stem = o.get("stage")
    a = json.loads((ASSIGN / "stages" / f"{stem}.json").read_text(encoding="utf-8"))
    d = json.loads((REPO / a["jp_file"]).read_text(encoding="utf-8"))
    bm = {b["off"]: b for b in d["blocks"]}
    jb = {b["off"]: b for b in d.get("briefing", [])}

    if path.stem != stem:
        errs.append(f"stage field {stem!r} != filename {path.stem!r}")

    # title
    tz = o.get("title_zh", "")
    if not tz:
        errs.append("missing title_zh")
    else:
        jp_t = a["title_jp"]
        n01_jp, n01_zh = jp_t.count("{01}"), tz.count("{01}")
        if n01_jp != n01_zh:
            errs.append(f"title {{01}} split count {n01_zh} != JP {n01_jp}")
        check_charset(tz, "title", errs, title=True)
        kana_check(tz, jp_t, "title", errs)
        if len(strip_marks(tz)) > max(len(strip_marks(jp_t)) + 2, 10):
            errs.append(f"title too long ({len(strip_marks(tz))} vs JP "
                        f"{len(strip_marks(jp_t))})")

    # briefing: exactly the owned offs, in order
    want_b = a["briefing_offs"]
    got_b = [e["off"] for e in o.get("briefing", [])]
    if got_b != want_b:
        missing = [x for x in want_b if x not in got_b]
        extra = [x for x in got_b if x not in want_b]
        errs.append(f"briefing offs mismatch: missing {missing[:5]} "
                    f"extra {extra[:5]} (order must match assignment)")
    for e in o.get("briefing", []):
        off, zh = e.get("off"), e.get("zh", "")
        w = f"briefing {off}"
        if not zh:
            errs.append(f"{w}: empty zh")
            continue
        check_charset(zh, w, errs)
        if off in jb:
            kana_check(zh, jb[off]["text"], w, errs)

    # blocks: exactly TRANSLATE_OFFSETS in dump order (minus declared skips)
    skipped = set(o.get("skipped", []))
    want = [x for x in a["translate_offsets"] if x not in skipped]
    got = [e["off"] for e in o.get("blocks", [])]
    if got != want:
        missing = [x for x in want if x not in set(got)]
        extra = [x for x in got if x not in set(want)]
        if missing:
            errs.append(f"blocks missing {len(missing)}: {missing[:6]}")
        if extra:
            errs.append(f"blocks not in TRANSLATE_OFFSETS ({len(extra)}): "
                        f"{extra[:6]}")
        if not missing and not extra:
            errs.append("blocks out of dump order")
    if skipped:
        if not o.get("notes"):
            errs.append("skipped offs but empty notes (must explain)")
        for s in skipped:
            if s not in a["translate_offsets"]:
                errs.append(f"skipped {s} not an assigned off")

    for e in o.get("blocks", []):
        off, zh = e.get("off"), e.get("zh", "")
        w = f"block {off}"
        b = bm.get(off)
        if b is None:
            continue
        if not zh:
            errs.append(f"{w}: empty zh")
            continue
        jp = b["text"]
        kana_check(zh, jp, w, errs)
        # marker structure
        if "{01}" in zh and "{01}" not in jp:
            errs.append(f"{w}: {{01}} not in JP record")
        if "{00}" in zh:
            if not (b.get("choice") or ("『" in zh and "』{00}『" in zh)):
                errs.append(f"{w}: manual {{00}} in non-choice record")
        if b.get("choice") and "{00}" not in zh:
            errs.append(f"{w}: choice record lost its {{00}} separator")
        check_charset(zh.replace("』{00}『", "』『"), w, errs)
        # length guard: 2x JP glyphs is already suspicious for a dialogue box
        jl, zl = len(strip_marks(jp)), len(strip_marks(zh))
        if jl >= 8 and zl > jl * 1.55:
            errs.append(f"{w}: zh {zl} glyphs vs JP {jl} (>155%, will overflow)")
    if not isinstance(o.get("web_sources"), list):
        errs.append("web_sources must be a list")
    return errs


# --------------------------------------------------------------- library ----
def _jp_sections(text):
    return 1 + len(re.findall(r"\{00\}々|\{00\}\{07\}", text))


def check_bio(path):
    errs = []
    o = json.loads(path.read_text(encoding="utf-8"))
    kind = o.get("kind")
    if kind == "char_bio":
        src = json.loads((REPO / "data/jp/characters.json").read_text("utf-8"))
        if "cid" in o:
            rec = next((c for c in src["characters"] if c["cid"] == o["cid"]), None)
            jp = rec["bio"]["text"] if rec and rec.get("bio") else None
        elif "orphan_index" in o:
            rec = next((b for b in src["unassigned_bios"]
                        if b["index"] == o["orphan_index"]), None)
            jp = rec["text"] if rec else None
        else:
            return ["char_bio without cid/orphan_index"]
    elif kind == "unit_bio":
        src = json.loads((REPO / "data/jp/units.json").read_text("utf-8"))
        rec = next((u for u in src["units"] if u["utid"] == o.get("utid")), None)
        jp = rec["bio"]["text"] if rec and rec.get("bio") else None
    else:
        return [f"unknown kind {kind!r}"]
    if jp is None:
        return [f"no JP bio for {path.name}"]

    paras = o.get("paragraphs")
    if not isinstance(paras, list) or not paras:
        return ["paragraphs missing/empty"]
    nsec = _jp_sections(jp)
    quote_extra = 1 if jp.startswith("「") else 0
    if not (nsec <= len(paras) <= nsec + quote_extra):
        errs.append(f"paragraphs {len(paras)} != JP sections {nsec}"
                    f"{' (+1 quote allowed)' if quote_extra else ''}")
    total = 0
    for i, p in enumerate(paras):
        w = f"para {i}"
        if "\n" in p:
            errs.append(f"{w}: manual line break")
        if "{0" in p:
            errs.append(f"{w}: marker in bio prose")
        check_charset(p, w, errs)
        kana_check(p, jp, w, errs)
        total += len(p)
    jl = len(strip_marks(jp))
    if total > jl * 1.15:
        errs.append(f"bio {total} glyphs vs JP {jl} (>115%, likely overflow)")
    if total < jl * 0.45:
        errs.append(f"bio {total} glyphs vs JP {jl} (<45%, meaning dropped?)")
    if not isinstance(o.get("web_sources"), list):
        errs.append("web_sources must be a list")
    return errs


def _terms_weapons():
    t = {}
    sect = False
    for line in (STG_DIR / "terms.md").read_text("utf-8").splitlines():
        if line.startswith("## "):
            sect = line.startswith("## 武器名")
            continue
        if sect and line.startswith("|") and "---" not in line:
            cols = [c.strip() for c in line.strip("|").split("|")]
            if len(cols) >= 2 and cols[0] != "日文":
                t[cols[0]] = cols[1]
    return t


def check_weapons(path):
    errs = []
    o = json.loads(path.read_text(encoding="utf-8"))
    nn = o.get("batch") or path.stem.split("_")[1]
    a = json.loads((ASSIGN / "lib" / f"weapon_{nn}.json").read_text("utf-8"))
    lib = json.loads((REPO / "data/jp/library.json").read_text("utf-8"))

    def N(x):
        return int(str(x), 16)
    jp = {N(w["off"]): w["text"] for w in lib["weapon_list"]}
    terms = _terms_weapons()
    got = {N(e["off"]): e.get("name_zh", "") for e in o.get("items", [])}
    a["offs"] = [N(x) for x in a["offs"]]
    for off in a["offs"]:
        if off not in got:
            errs.append(f"missing off {off}")
            continue
        zh = got[off]
        if not zh:
            errs.append(f"{hex(off)}: empty name_zh")
            continue
        j = jp[off].replace("·", "・")
        # a terms row may be dual-valued ("有线爪 / 利爪" — make_terms joins
        # multiple phase-1 branch translations): any listed variant is legal
        variants = [v.strip() for v in terms[j].split(" / ")] if j in terms else []
        if j in terms and zh not in variants:
            errs.append(f"{hex(off)}: {zh!r} != terms.md {terms[j]!r} for {j}")
        check_charset(zh, hex(off), errs)
        if j in terms and zh in variants:
            continue  # terms-locked value may legitimately equal the JP form
                      # (月光蝶/金棒/CIWS class) — identity check must not fire
        kana_check(zh, j, hex(off), errs)
    for off in got:
        if off not in a["offs"]:
            errs.append(f"extra off {hex(off)} not in batch")
    return errs


def check_one(path: Path):
    try:
        name = path.name
        if path.parent.name == "stages":
            return check_stage(path)
        if name.startswith("weapon_"):
            return check_weapons(path)
        return check_bio(path)
    except Exception as e:  # malformed JSON / wrong keys
        return [f"EXCEPTION {type(e).__name__}: {e}"]


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        paths = sorted((OUT / "stages").glob("*.json")) + \
            sorted((OUT / "lib").glob("*.json"))
    else:
        paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("nothing to check")
        return 0
    bad = 0
    for p in paths:
        errs = check_one(p)
        if errs:
            bad += 1
            print(f"FAIL {p.relative_to(REPO) if p.is_absolute() else p}")
            for e in errs[:25]:
                print(f"  - {e}")
            if len(errs) > 25:
                print(f"  … and {len(errs) - 25} more")
        else:
            print(f"PASS {p.name}")
    print(f"== {len(paths) - bad}/{len(paths)} PASS ==")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
