"""Assemble the structured JP data dump (the content of data/jp/).

Grouping philosophy (the repo's data contract):
  * one CHARACTER block carries everything of that character: name, the three
    ID commands (name/summary/effect detail/cut-in quote), barks, encyclopedia
    bio;
  * one UNIT block carries name, six weapon names, special ability/defense
    linkage, encyclopedia bio;
  * one STAGE file carries its descriptor labels, briefing and every dialogue
    block in console play order;
  * flat banks (event text, cut-in records, battle effect banks, parts,
    dictionaries, the string-pointer graph) each get one file.

Every record carries its exact ROM location — that location is the KEY the
translation mapping in data/zh/ refers to.  The dump is machine-generated,
deterministic, and verified fresh by the static gates; never hand-edit it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import layout as L
from . import walkers as W
from .gamerom import GameROM

REPO = Path(__file__).resolve().parent.parent.parent
EXTRACTION_DIR = REPO / "data" / "extraction"
SCHEMA_VERSION = 1


def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def load_speaker_overlay() -> dict:
    """Curated per-block speaker/choice knowledge (not derivable from the
    bytecode yet): data/extraction/stage_speakers.json."""
    return _load_json(EXTRACTION_DIR / "stage_speakers.json", {}).get("stages", {})


def load_bio_map() -> dict:
    """Curated bio-index -> owner identity (no in-ROM index table exists):
    data/extraction/library_bio_map.json."""
    return _load_json(EXTRACTION_DIR / "library_bio_map.json", {})


def build_dump(rom: GameROM) -> dict[str, dict]:
    """Extract everything -> {relative dump path: JSON payload}."""
    assert not rom.is_zh, "the JP source ROM is the single source of truth"
    header = {
        "_generated": {
            "tool": "build/extract_data_from_game.py",
            "schema": SCHEMA_VERSION,
            "rom_sha1": hashlib.sha1(rom.rom).hexdigest(),
            "note": "machine-generated JP ground truth; regenerate, never edit",
        }
    }
    bio_map = load_bio_map()
    overlay = load_speaker_overlay()

    out: dict[str, dict] = {}

    # ---- characters -------------------------------------------------------
    pilots = W.pilots(rom)
    details = {d["didx"]: d for d in W.id_details(rom)}
    cutins = W.cutin_records(rom)[0]
    cutin_by_rec = {r["record"]: r for r in cutins["records"]}
    bark_records = W.barks(rom)
    barks_by_cid: dict[int, list[dict]] = {}
    for b in bark_records:
        barks_by_cid.setdefault(b["cid"], []).append(b)
    char_bios = {b["index"]: b for b in W.bios(rom, "char")}
    bio_of_cid = {cid: i for i, cid in enumerate(bio_map.get("char", []))
                  if isinstance(cid, int) and cid >= 0}

    chars = []
    for p in pilots:
        cid = p["cid"]
        entry = dict(p)
        ids = []
        for slot in range(3):
            idn = cid * 3 + slot
            if idn >= L.IDCMD_COUNT:
                continue
            idc = W.id_command(rom, idn)
            if not idc["name"]:
                continue
            idc["slot"] = slot
            det = details.get(idc["didx"])
            if det:
                idc["detail"] = det
            rec = cutins["links"].get(str(idn))
            if rec and rec in cutin_by_rec:
                idc["cutin"] = cutin_by_rec[rec]
            ids.append(idc)
        if ids:
            entry["ids"] = ids
        blist = barks_by_cid.get(cid)
        if blist:
            entry["barks"] = blist
        bi = bio_of_cid.get(cid)
        if bi is not None and bi in char_bios:
            entry["bio"] = char_bios[bi]
        chars.append(entry)
    claimed_cids = {p["cid"] for p in pilots}
    out["characters.json"] = {
        **header,
        "characters": chars,
        "unassigned_barks": [b for b in bark_records
                             if b["cid"] not in claimed_cids],
        "unassigned_bios": [b for i, b in sorted(char_bios.items())
                            if i not in set(bio_of_cid.values())],
    }

    # ---- units ------------------------------------------------------------
    unit_bios = {b["index"]: b for b in W.bios(rom, "unit")}
    bio_of_utid = {utid: i for i, utid in enumerate(bio_map.get("unit", []))
                   if isinstance(utid, int) and utid >= 0}
    units = []
    for u in W.units(rom):
        utid = u["utid"]
        entry = dict(u)
        link = W.unit_special_link(rom, utid)
        if link:
            entry["specials"] = link
        bi = bio_of_utid.get(utid)
        if bi is not None and bi in unit_bios:
            entry["bio"] = unit_bios[bi]
        units.append(entry)
    out["units.json"] = {
        **header,
        "units": units,
        "unassigned_bios": [b for i, b in sorted(unit_bios.items())
                            if i not in set(bio_of_utid.values())],
    }

    # ---- stages -----------------------------------------------------------
    descs = W.stage_descriptors(rom)
    events = W.event_text_blocks(rom)
    briefs_by_desc: dict[int, list[dict]] = {}
    for e in events:
        if e.get("briefing"):
            for k in e.get("descs", []):
                briefs_by_desc.setdefault(k, []).append(e)
    files: dict[str, list[dict]] = {}
    for d in descs:
        if d["file"]:
            files.setdefault(d["file"], []).append(d)
    for fname in sorted(files):
        base = fname[:-4] if fname.endswith(".bin") else fname
        dlist = files[fname]
        briefing = []
        seen_off = set()
        for d in dlist:
            for e in briefs_by_desc.get(d["index"], []):
                if e["off"] not in seen_off:
                    seen_off.add(e["off"])
                    briefing.append(e)
        out[f"stages/{base}.json"] = {
            **header,
            "file": fname,
            "size": len(rom.file(fname)),
            "descriptors": dlist,
            "briefing": briefing,
            "blocks": W.stage_blocks(rom, fname, overlay.get(base) or overlay.get(fname)),
        }

    # ---- flat banks ---------------------------------------------------------
    out["event_text.json"] = {**header, "region": {
        "lo": W._hex(L.EVENT_TEXT_LO), "hi": W._hex(L.EVENT_TEXT_HI)},
        "blocks": events}
    out["cutins.json"] = {**header, **cutins}
    out["battle_effects.json"] = {
        **header,
        "ability_cards": W.ability_cards(rom),
        "command_effects": W.command_effects(rom),
        "specials": W.special_records(rom),
    }
    out["parts.json"] = {**header, "parts": W.parts(rom)}
    out["library.json"] = {**header, "weapon_list": W.weapon_list(rom)}
    ev_gallery = W.ev_gallery_titles(rom)
    char_gallery = W.library_gallery_titles(rom, "character")
    unit_gallery = W.library_gallery_titles(rom, "unit")
    # Stable identity is the exact token stream, never the decoder annotation:
    # renderB/codebook knowledge can improve later without renumbering every
    # translation key.  Current source data has 28 unique raw streams and each
    # stream decodes consistently across all 513 records.
    series_by_raw: dict[str, dict[str, str]] = {}
    for section in (char_gallery, unit_gallery):
        for rec in section["records"]:
            raw_hex = rec["series_raw_hex"]
            jp = rec["series_jp"]
            existing = series_by_raw.get(raw_hex)
            if existing is None:
                existing = {
                    "series_id": f"SERIES{len(series_by_raw) + 1:04d}",
                    "raw_hex": raw_hex,
                    "jp": jp,
                }
                series_by_raw[raw_hex] = existing
            elif existing["jp"] != jp:
                raise ValueError(
                    f"gallery series raw {raw_hex} has divergent decoder annotations"
                )
            rec["series_id"] = existing["series_id"]
    gallery_files = {}
    for name in (
        L.EV_GALLERY_TITLE_FILE, L.EV_GALLERY_CATALOG_FILE,
        L.CHAR_GALLERY_METADATA_FILE, L.CHAR_GALLERY_STRING_FILE,
        L.UNIT_GALLERY_METADATA_FILE, L.UNIT_GALLERY_STRING_FILE,
    ):
        blob = rom.file(name)
        gallery_files[name] = {
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
    out["gallery.json"] = {
        **header,
        "_about": (
            "Gallery identities are source raw bytes/hashes/record ids. The *_jp and "
            "series.jp fields are best-effort renderB/codebook decoder annotations only; "
            "build and translation joins must never use them as identity keys."
        ),
        "files": gallery_files,
        "ev": ev_gallery,
        "series": list(series_by_raw.values()),
        "characters": char_gallery,
        "units": unit_gallery,
    }
    out["ui.json"] = {
        **header,
        "dict_text": W.dictionary_entries(rom, "text"),
        "dict_sys": W.dictionary_entries(rom, "sys"),
        "pointer_strings": W.pointer_strings(rom),
    }
    return out


def write_dump(dump: dict[str, dict], out_dir: Path) -> list[Path]:
    """Write the dump deterministically (sorted paths, indent=1, no ASCII
    escaping — the repo JSON convention)."""
    written = []
    for rel in sorted(dump):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dump[rel], ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
        written.append(p)
    return written
