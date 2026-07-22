#!/usr/bin/env python3
"""Regenerate existing CJK atlas slots from a prepared WQY 9pt BDF.

The caller supplies the BDF and the atlas from baseline commit 35e21a3. This
script does not download, convert or rasterize a font. It copies the BDF's
integer bitmap rows, adds the game's native 2bpp L-shadow, and writes each
already-registered basic-CJK character back to its existing slot.

The same command also records the changed-character inventory. Latin, digits,
symbols, punctuation, kana and author-adjusted narrow cells are preserved
byte-for-byte. PR comparison artwork is intentionally outside this generator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .wqy_bitmap import CELL_BYTES, WQYBitmapFont
except ImportError:
    from wqy_bitmap import CELL_BYTES, WQYBitmapFont

REPO = Path(__file__).resolve().parents[1]
ATLAS_PATH = REPO / "data/font/atlas12.bin"
CHARMAP_PATH = REPO / "data/charmap.json"
REPORT_PATH = REPO / "data/font/wqy_raster_changes.json"

BASELINE_COMMIT = "35e21a396fb931192084983ff63d5b5e5b45160e"
BASELINE_ATLAS_SHA256 = "a01ffd282246511975bfa2c979c522c4898f6936f2a34617f29755bdbd74ef36"
EXPECTED_ATLAS_SHA256 = "9df98204c9037b2c0e6a9da3848d5077e43c22f4e64d3bd9716beee423ed310c"
EXPECTED_PRESERVED_SHA256 = "4e5ce188565cff35332c167df62d29af3ef43993bc802ee8fb7e77f1e1970942"
ATLAS_SLOTS = 4320
EXPECTED_REGISTERED_CJK = 2175
EXPECTED_NON_CJK = 62
EXPECTED_CHANGED_CJK = 346
EXPECTED_PRESERVED_SLOTS = ATLAS_SLOTS - EXPECTED_REGISTERED_CJK

# These five cells were adjusted after the original WQY pass. Their bitmap,
# slot and runtime advance must remain aligned with the existing implementation.
PRESERVED_LAYOUT = {
    "S": 4222,
    "E": 4223,
    "D": 4214,
    "(": 4156,
    ")": 4253,
}
PRESERVED_LAYOUT_SHA256 = {
    "S": "ac555acca29d290577132b585719513fea6b9f453cb1c6e54ac98cf7a2f449e8",
    "E": "b8fc50bfc6dc227bd8baeb24c12d06f8b6f8b18ff48365a98d76efe83a842bc8",
    "D": "07e807237a37b2bdbdf51e238c49b5586f45aac00ea04c6a4f7e4e8ce015f9b5",
    "(": "ce785de4eed265d7f59fae027639646c152b2a39231bf38ea88f530ea907fd7e",
    ")": "dc4f6447af6619dd91f54443d3a7c0536bfafeab79b1e628b202d390dbaa71fe",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_basic_cjk(char: str) -> bool:
    return len(char) == 1 and 0x4E00 <= ord(char) <= 0x9FFF


def registries() -> tuple[dict[str, int], dict[str, int]]:
    raw = json.loads(CHARMAP_PATH.read_text(encoding="utf-8"))
    registry = {char: int(slot) for char, slot in raw["two_byte_zh"].items()}
    cjk = {char: slot for char, slot in registry.items() if is_basic_cjk(char)}
    non_cjk = {char: slot for char, slot in registry.items() if char not in cjk}
    if (
        len(cjk) != EXPECTED_REGISTERED_CJK
        or len(non_cjk) != EXPECTED_NON_CJK
        or len(set(registry.values())) != len(registry)
    ):
        raise ValueError(
            f"glyph registry changed: {len(cjk)} CJK + {len(non_cjk)} non-CJK, "
            f"{len(set(registry.values()))} unique slots"
        )
    layout = {char: non_cjk.get(char) for char in PRESERVED_LAYOUT}
    if layout != PRESERVED_LAYOUT:
        raise ValueError(f"layout-special registry changed: {layout!r}")
    return cjk, non_cjk


def preserved_sha256(atlas: bytes, cjk: dict[str, int]) -> str:
    cjk_slots = set(cjk.values())
    preserved = b"".join(
        atlas[slot * CELL_BYTES:(slot + 1) * CELL_BYTES]
        for slot in range(ATLAS_SLOTS)
        if slot not in cjk_slots
    )
    if len(preserved) != EXPECTED_PRESERVED_SLOTS * CELL_BYTES:
        raise ValueError(f"preserved atlas region has wrong size: {len(preserved)}")
    return sha256(preserved)


def verify_preserved(atlas: bytes, cjk: dict[str, int]) -> None:
    got = preserved_sha256(atlas, cjk)
    if got != EXPECTED_PRESERVED_SHA256:
        raise ValueError(f"non-CJK atlas cells drifted: {got} != {EXPECTED_PRESERVED_SHA256}")
    for char, slot in PRESERVED_LAYOUT.items():
        cell = atlas[slot * CELL_BYTES:(slot + 1) * CELL_BYTES]
        cell_hash = sha256(cell)
        if cell_hash != PRESERVED_LAYOUT_SHA256[char]:
            raise ValueError(
                f"layout-special cell drifted: {char!r} slot {slot} sha256 {cell_hash}"
            )


def verify_atlas(atlas: bytes) -> None:
    if len(atlas) != ATLAS_SLOTS * CELL_BYTES:
        raise ValueError(
            f"atlas mismatch: want {ATLAS_SLOTS * CELL_BYTES} bytes; got {len(atlas)}"
        )
    cjk, _non_cjk = registries()
    verify_preserved(atlas, cjk)
    got = sha256(atlas)
    if got != EXPECTED_ATLAS_SHA256:
        raise ValueError(f"full WQY atlas sha256 {got} != pinned {EXPECTED_ATLAS_SHA256}")


def compose(
    bdf_path: Path, base_path: Path
) -> tuple[bytes, list[tuple[str, int, bytes, bytes]]]:
    base = base_path.read_bytes()
    if len(base) != ATLAS_SLOTS * CELL_BYTES:
        raise ValueError(
            f"base atlas mismatch: want {ATLAS_SLOTS * CELL_BYTES} bytes; got {len(base)}"
        )
    base_hash = sha256(base)
    if base_hash != BASELINE_ATLAS_SHA256:
        raise ValueError(
            f"base atlas is not {BASELINE_COMMIT}: {base_hash} != {BASELINE_ATLAS_SHA256}"
        )

    cjk, _non_cjk = registries()
    verify_preserved(base, cjk)
    font = WQYBitmapFont(bdf_path)
    atlas = bytearray(base)
    changes: list[tuple[str, int, bytes, bytes]] = []
    for char, slot in sorted(cjk.items(), key=lambda item: item[1]):
        new_cell = font.cell(char)
        start = slot * CELL_BYTES
        old_cell = bytes(atlas[start:start + CELL_BYTES])
        if old_cell != new_cell:
            changes.append((char, slot, old_cell, new_cell))
        atlas[start:start + CELL_BYTES] = new_cell

    result = bytes(atlas)
    if len(changes) != EXPECTED_CHANGED_CJK:
        raise ValueError(
            f"changed CJK set drifted: {len(changes)} != {EXPECTED_CHANGED_CJK}"
        )
    verify_atlas(result)
    return result, changes


def report_bytes(changes: list[tuple[str, int, bytes, bytes]]) -> bytes:
    document = {
        "_about": (
            "Same-character, same-slot WQY BDF raster changes from baseline 35e21a3."
        ),
        "baseline": {
            "commit": BASELINE_COMMIT,
            "atlas_sha256": BASELINE_ATLAS_SHA256,
        },
        "output_atlas_sha256": EXPECTED_ATLAS_SHA256,
        "counts": {
            "registered_cjk": EXPECTED_REGISTERED_CJK,
            "unchanged_cjk": EXPECTED_REGISTERED_CJK - EXPECTED_CHANGED_CJK,
            "changed_cjk": EXPECTED_CHANGED_CJK,
            "preserved_other_slots": EXPECTED_PRESERVED_SLOTS,
        },
        "entries": [
            {
                "index": index,
                "char": char,
                "slot": slot,
                "old_cell_sha256": sha256(old_cell),
                "new_cell_sha256": sha256(new_cell),
            }
            for index, (char, slot, old_cell, new_cell) in enumerate(changes)
        ],
    }
    return (json.dumps(document, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def check_file(path: Path, expected: bytes, label: str) -> bool:
    if not path.is_file() or path.read_bytes() != expected:
        print(f"{label} missing or stale: {path}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bdf", type=Path, help="prepared WQY 9pt BDF")
    parser.add_argument("--base-atlas", type=Path, help=f"atlas from {BASELINE_COMMIT}")
    parser.add_argument("--output", type=Path, default=ATLAS_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--check-atlas", action="store_true",
        help="verify the committed atlas hashes without the BDF or baseline atlas",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    report = args.report.resolve()
    if args.check_atlas:
        verify_atlas(output.read_bytes())
        print(
            f"WQY atlas identity verified: {EXPECTED_REGISTERED_CJK} CJK rebuilt; "
            f"{EXPECTED_PRESERVED_SLOTS} other slots preserved; sha256 "
            f"{EXPECTED_ATLAS_SHA256}"
        )
        return 0
    if args.bdf is None or args.base_atlas is None:
        parser.error("--bdf and --base-atlas are required unless --check-atlas is used")

    bdf = args.bdf.resolve()
    result, changes = compose(bdf, args.base_atlas.resolve())
    expected_report = report_bytes(changes)
    if args.check:
        ok = all((
            check_file(output, result, "atlas"),
            check_file(report, expected_report, "change report"),
        ))
        if not ok:
            return 1
        print(
            f"WQY raster artifacts verified: {len(changes)} same-slot CJK changes; "
            f"atlas sha256 {sha256(result)}"
        )
        return 0

    for path, payload in ((output, result), (report, expected_report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    print(
        f"wrote atlas and report: {len(changes)} same-slot CJK changes; "
        f"atlas sha256 {sha256(result)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
