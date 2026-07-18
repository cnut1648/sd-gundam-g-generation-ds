#!/usr/bin/env python3
"""Regenerate the Chinese cells in data/font/atlas12.bin from Fusion Pixel.

The committed atlas remains the one-step build input.  This helper documents and
verifies how its Chinese cells were produced; it is not a post-build ROM patcher.

    python build/regenerate_fusion_atlas.py --font /path/to/fusion-pixel-12px-proportional-zh_hans.ttf
    python build/regenerate_fusion_atlas.py --font /path/to/font.ttf --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
ATLAS_PATH = REPO / "data" / "font" / "atlas12.bin"
CHARMAP_PATH = REPO / "data" / "charmap.json"

FONT_FILENAME = "fusion-pixel-12px-proportional-zh_hans.ttf"
FONT_VERSION = "2026.05.07"
FONT_SHA256 = "7dda18bac79c841a9a545c45b3c2d9d00f1cbbca3217fd8d291dd27298932bbb"
EXPECTED_ATLAS_SHA256 = "84c9ead0d34d37c6b4ff8ad162da5736f2c3f25e945ec85ff926689e1f692338"

CELL = 12
CELL_BYTES = 36
ATLAS_SLOTS = 4320
INK_THRESHOLD = 24
EXPECTED_CJK = 2085
FALLBACKS = {"赝": 3976}
FALLBACK_SHA256 = {
    "赝": "fe550843e5fc4d8e57e9ef85b9a8e829cc5b406143b5779740b8a27d01d80176",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_basic_cjk(char: str) -> bool:
    return len(char) == 1 and 0x4E00 <= ord(char) <= 0x9FFF


def is_missing(font: ImageFont.FreeTypeFont, char: str, missing_sig: tuple) -> bool:
    mask = font.getmask(char)
    return (mask.size, bytes(mask)) == missing_sig


def render_mask(font: ImageFont.FreeTypeFont, char: str) -> list[list[bool]]:
    canvas = Image.new("L", (CELL, CELL), 0)
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), char, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (CELL - width) // 2 - bbox[0]
    y = (CELL - height) // 2 - bbox[1]
    draw.text((x, y), char, font=font, fill=255)
    mask = [
        [canvas.getpixel((x, y)) >= INK_THRESHOLD for x in range(CELL)]
        for y in range(CELL)
    ]
    if not any(value for row in mask for value in row):
        raise ValueError(f"Fusion rendered a blank glyph: {char!r}")
    if any(mask[CELL - 1]) or any(row[CELL - 1] for row in mask):
        raise ValueError(f"Fusion glyph reaches the shadow-reserved edge: {char!r}")
    return mask


def encode_cell(mask: list[list[bool]]) -> bytes:
    pixels = [[1 if mask[y][x] else 0 for x in range(CELL)] for y in range(CELL)]
    for dx, dy in ((1, 0), (0, 1), (1, 1)):
        for y in range(CELL):
            for x in range(CELL):
                if not mask[y][x]:
                    continue
                tx, ty = x + dx, y + dy
                if tx < CELL and ty < CELL and not mask[ty][tx]:
                    pixels[ty][tx] = 2

    raw = bytearray(CELL_BYTES)
    for y in range(CELL):
        for group in range(3):
            raw[y * 3 + group] = sum(
                (pixels[y][group * 4 + i] & 0x03) << (i * 2)
                for i in range(4)
            )
    return bytes(raw)


def regenerate(font_path: Path, atlas_path: Path) -> tuple[bytes, int]:
    font_raw = font_path.read_bytes()
    got_font_sha = sha256(font_raw)
    if font_path.name != FONT_FILENAME or got_font_sha != FONT_SHA256:
        raise ValueError(
            f"font mismatch: want {FONT_FILENAME} {FONT_VERSION} sha256 {FONT_SHA256}; "
            f"got {font_path.name} {got_font_sha}"
        )

    atlas = bytearray(atlas_path.read_bytes())
    if len(atlas) != ATLAS_SLOTS * CELL_BYTES:
        raise ValueError(f"atlas size {len(atlas)} != {ATLAS_SLOTS * CELL_BYTES}")

    charmap = json.loads(CHARMAP_PATH.read_text(encoding="utf-8"))["two_byte_zh"]
    cjk = {char: int(slot) for char, slot in charmap.items() if is_basic_cjk(char)}
    if len(cjk) != EXPECTED_CJK:
        raise ValueError(f"CJK registry count {len(cjk)} != pinned {EXPECTED_CJK}")

    font = ImageFont.truetype(str(font_path), CELL)
    missing_mask = font.getmask(chr(0x10FFFF))
    missing_sig = (missing_mask.size, bytes(missing_mask))
    missing = {char: slot for char, slot in cjk.items() if is_missing(font, char, missing_sig)}
    if missing != FALLBACKS:
        raise ValueError(f"Fusion fallback set changed: {missing!r} != {FALLBACKS!r}")

    for char, slot in FALLBACKS.items():
        cell = bytes(atlas[slot * CELL_BYTES:(slot + 1) * CELL_BYTES])
        if sha256(cell) != FALLBACK_SHA256[char]:
            raise ValueError(f"fallback cell drifted: {char!r} at slot {slot}")

    changed = 0
    for char, slot in cjk.items():
        if char in FALLBACKS:
            continue
        cell = encode_cell(render_mask(font, char))
        start = slot * CELL_BYTES
        if atlas[start:start + CELL_BYTES] != cell:
            changed += 1
        atlas[start:start + CELL_BYTES] = cell

    result = bytes(atlas)
    got_atlas_sha = sha256(result)
    if got_atlas_sha != EXPECTED_ATLAS_SHA256:
        raise ValueError(
            f"generated atlas sha256 {got_atlas_sha} != pinned {EXPECTED_ATLAS_SHA256}; "
            "check Pillow/FreeType and the committed charmap/base atlas"
        )
    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, type=Path,
                        help=f"official {FONT_FILENAME} from release {FONT_VERSION}")
    parser.add_argument("--check", action="store_true",
                        help="verify the committed atlas without writing it")
    args = parser.parse_args()

    current = ATLAS_PATH.read_bytes()
    generated, changed = regenerate(args.font.resolve(), ATLAS_PATH)
    if args.check:
        if generated != current:
            print(f"atlas needs regeneration: {changed} CJK cells differ")
            return 1
        print(f"atlas verified: sha256 {sha256(generated)}, 2084 Fusion CJK + 1 WQY fallback")
        return 0

    ATLAS_PATH.write_bytes(generated)
    print(f"wrote {ATLAS_PATH}: {changed} CJK cells changed, sha256 {sha256(generated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
