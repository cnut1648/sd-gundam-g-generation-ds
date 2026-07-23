"""Deterministic text repainting for indexed 4bpp BG tile resources.

The strategy-hub labels are baked into NitroFS tile graphics rather than
drawn by either runtime text renderer. This module copies glyph cells from
the committed 12x12 atlas into those resources without invoking a host font
rasterizer.

By default only tiles touched by an annotated clear rectangle are rewritten.
Before a tile is written, every screen-map reference to that tile is
reconstructed and must agree byte-for-byte. Shared-tile damage is therefore a
build error.

Some menu resources deliberately reuse letter-bearing tiles between different
buttons. Those tables opt into ``repack_tiles``: every visible screen cell is
re-encoded and deduplicated (including flipped variants) inside the original
fixed tile capacity. This is copy-on-write for the tilemap: editing one label
can no longer leak pixels into another screen cell.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


ATLAS_CELL = 12
ATLAS_CELL_BYTES = 36


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


@dataclass(frozen=True)
class TileMap:
    width_tiles: int
    height_tiles: int
    screen_offset: int
    gfx_offset: int
    gfx_len: int
    entries: tuple[int, ...]

    @property
    def width(self) -> int:
        return self.width_tiles * 8

    @property
    def height(self) -> int:
        return self.height_tiles * 8


def _tile_map(data: bytes) -> TileMap:
    if len(data) < 12:
        raise ValueError("tile resource is shorter than its 12-byte header")
    width_tiles, height_tiles = data[2], data[3]
    screen_offset = _u16(data, 4)
    screen_len = _u16(data, 6)
    gfx_offset = _u16(data, 8)
    gfx_len = _u16(data, 10)
    expected_screen_len = width_tiles * height_tiles * 2
    if screen_len != expected_screen_len:
        raise ValueError(
            f"screen map has {screen_len} bytes; expected {expected_screen_len}"
        )
    if screen_offset + screen_len > len(data):
        raise ValueError("screen map extends past the resource")
    if gfx_len % 32 or gfx_offset + gfx_len > len(data):
        raise ValueError("4bpp tile graphics have invalid bounds")
    entries = tuple(
        _u16(data, screen_offset + index * 2)
        for index in range(width_tiles * height_tiles)
    )
    tile_count = gfx_len // 32
    bad = sorted({entry & 0x3FF for entry in entries if (entry & 0x3FF) >= tile_count})
    if bad:
        raise ValueError(f"screen map references missing tile(s): {bad}")
    return TileMap(
        width_tiles,
        height_tiles,
        screen_offset,
        gfx_offset,
        gfx_len,
        entries,
    )


def decode_index_canvas(data: bytes) -> tuple[list[list[int]], TileMap]:
    """Decode the visible nibble indices, applying screen-entry flips."""
    tile_map = _tile_map(data)
    canvas = [[0] * tile_map.width for _ in range(tile_map.height)]
    for cell_index, entry in enumerate(tile_map.entries):
        tile_index = entry & 0x3FF
        hflip = bool(entry & 0x400)
        vflip = bool(entry & 0x800)
        start = tile_map.gfx_offset + tile_index * 32
        tile = data[start:start + 32]
        cell_x = cell_index % tile_map.width_tiles
        cell_y = cell_index // tile_map.width_tiles
        for y in range(8):
            source_y = 7 - y if vflip else y
            for x in range(8):
                source_x = 7 - x if hflip else x
                packed = tile[source_y * 4 + source_x // 2]
                canvas[cell_y * 8 + y][cell_x * 8 + x] = (
                    packed >> (4 if source_x & 1 else 0)
                ) & 0xF
    return canvas, tile_map


def _encode_screen_cell(
    canvas: list[list[int]], tile_map: TileMap, cell_index: int
) -> bytes:
    entry = tile_map.entries[cell_index]
    hflip = bool(entry & 0x400)
    vflip = bool(entry & 0x800)
    cell_x = cell_index % tile_map.width_tiles
    cell_y = cell_index // tile_map.width_tiles
    tile = bytearray(32)
    for y in range(8):
        target_y = 7 - y if vflip else y
        for x in range(8):
            target_x = 7 - x if hflip else x
            value = canvas[cell_y * 8 + y][cell_x * 8 + x] & 0xF
            byte_offset = target_y * 4 + target_x // 2
            shift = 4 if target_x & 1 else 0
            tile[byte_offset] |= value << shift
    return bytes(tile)


def _flip_tile(tile: bytes, *, hflip: bool = False, vflip: bool = False) -> bytes:
    """Return an encoded 4bpp tile flipped in pixel space."""
    pixels = [[0] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            packed = tile[y * 4 + x // 2]
            pixels[y][x] = (packed >> (4 if x & 1 else 0)) & 0xF
    if hflip:
        pixels = [list(reversed(row)) for row in pixels]
    if vflip:
        pixels = list(reversed(pixels))
    output = bytearray(32)
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            output[y * 4 + x // 2] |= value << (4 if x & 1 else 0)
    return bytes(output)


def _repack_canvas(
    source: bytes,
    canvas: list[list[int]],
    tile_map: TileMap,
    *,
    where: str,
) -> bytes:
    """Copy-on-write every screen cell into the resource's fixed tile budget."""
    tile_capacity = tile_map.gfx_len // 32
    tiles: list[bytes] = []
    tile_ids: dict[bytes, int] = {}
    entries: list[int] = []

    for cell_index, original_entry in enumerate(tile_map.entries):
        tile = _encode_screen_cell(canvas, tile_map, cell_index)
        # _encode_screen_cell preserves the source entry's flip convention.
        # Normalize it to the visible cell orientation before assigning a new
        # tile id and fresh flip bits.
        tile = _flip_tile(
            tile,
            hflip=bool(original_entry & 0x0400),
            vflip=bool(original_entry & 0x0800),
        )
        variants = (
            (tile, 0),
            (_flip_tile(tile, hflip=True), 0x0400),
            (_flip_tile(tile, vflip=True), 0x0800),
            (_flip_tile(tile, hflip=True, vflip=True), 0x0C00),
        )
        tile_index = None
        flip_bits = 0
        for candidate, candidate_flip in variants:
            if candidate in tile_ids:
                tile_index = tile_ids[candidate]
                flip_bits = candidate_flip
                break
        if tile_index is None:
            tile_index = len(tiles)
            if tile_index >= tile_capacity:
                raise ValueError(
                    f"{where}: repacked canvas needs {tile_index + 1} tiles; "
                    f"resource holds {tile_capacity}"
                )
            tiles.append(tile)
            tile_ids[tile] = tile_index
        entries.append((original_entry & 0xF000) | flip_bits | tile_index)

    output = bytearray(source)
    for cell_index, entry in enumerate(entries):
        struct.pack_into("<H", output, tile_map.screen_offset + cell_index * 2, entry)
    output[tile_map.gfx_offset:tile_map.gfx_offset + tile_map.gfx_len] = (
        b"\x00" * tile_map.gfx_len
    )
    for tile_index, tile in enumerate(tiles):
        start = tile_map.gfx_offset + tile_index * 32
        output[start:start + 32] = tile

    decoded, _ = decode_index_canvas(bytes(output))
    if decoded != canvas:
        raise AssertionError(f"{where}: repacked canvas does not round-trip")
    return bytes(output)


def _atlas_cell(atlas: bytes, slot: int) -> list[list[int]]:
    start = slot * ATLAS_CELL_BYTES
    cell = atlas[start:start + ATLAS_CELL_BYTES]
    if len(cell) != ATLAS_CELL_BYTES:
        raise ValueError(f"atlas slot {slot} is out of bounds")
    pixels = [[0] * ATLAS_CELL for _ in range(ATLAS_CELL)]
    for y in range(ATLAS_CELL):
        for x in range(ATLAS_CELL):
            bit = y * ATLAS_CELL + x
            pixels[y][x] = (cell[bit // 4] >> ((bit % 4) * 2)) & 0x3
    return pixels


def repaint_atlas_text(
    source: bytes,
    table: dict,
    *,
    atlas: bytes,
    char_slots: dict[str, int],
) -> bytes:
    """Repaint annotated labels from committed atlas cells.

    Each label supplies a clear rectangle and one ``sample_x`` column outside
    the old lettering. ``sample_y`` optionally chooses the clean donor rows;
    together they restore the button background before new glyphs are drawn.
    ``clip_shadow`` may discard only value-2 edge shadow pixels that cross into
    a shared tile; stroke pixels are never clipped.
    """
    canvas, tile_map = decode_index_canvas(source)
    original_canvas = [row[:] for row in canvas]
    palette = table["palette_indices"]
    stroke_index = int(palette["stroke"])
    shadow_index = int(palette["shadow"])
    if not (0 <= stroke_index <= 15 and 0 <= shadow_index <= 15):
        raise ValueError("4bpp stroke/shadow indices must be in 0..15")

    for label_index, label in enumerate(table["labels"]):
        text = label["text"]
        x = int(label["x"])
        y = int(label["y"])
        repack_tiles = bool(table.get("repack_tiles", False))
        safe_tiles = (
            None
            if repack_tiles
            else {int(tile) for tile in label["tiles"]}
        )
        clip_shadow = bool(label.get("clip_shadow", False))
        clear = label["clear"]
        clear_x = int(clear["x"])
        clear_y = int(clear["y"])
        clear_width = int(clear["width"])
        clear_height = int(clear["height"])
        sample_x = int(clear["sample_x"])
        sample_y = int(clear.get("sample_y", clear_y))
        where = f"{table['file']} label {label_index} {text!r}"

        if (
            clear_width <= 0
            or clear_height <= 0
            or clear_x < 0
            or clear_y < 0
            or clear_x + clear_width > tile_map.width
            or clear_y + clear_height > tile_map.height
            or not 0 <= sample_x < tile_map.width
            or sample_y < 0
            or sample_y + clear_height > tile_map.height
        ):
            raise ValueError(f"{where}: clear rectangle/sample is out of bounds")
        if (
            x < clear_x
            or y < clear_y
            or x + len(text) * ATLAS_CELL > clear_x + clear_width
            or y + ATLAS_CELL > clear_y + clear_height
        ):
            raise ValueError(f"{where}: atlas text does not fit its clear rectangle")

        for target_y in range(clear_y, clear_y + clear_height):
            background_y = sample_y + target_y - clear_y
            background = canvas[background_y][sample_x]
            if background in (stroke_index, shadow_index):
                raise ValueError(
                    f"{where}: background sample ({sample_x}, {background_y}) "
                    f"uses text index {background}"
                )
            for target_x in range(clear_x, clear_x + clear_width):
                cell_index = (
                    (target_y // 8) * tile_map.width_tiles + target_x // 8
                )
                if (
                    safe_tiles is not None
                    and (tile_map.entries[cell_index] & 0x3FF) not in safe_tiles
                ):
                    continue
                if canvas[target_y][target_x] in (stroke_index, shadow_index):
                    canvas[target_y][target_x] = background

        for char_index, char in enumerate(text):
            try:
                slot = int(char_slots[char])
            except KeyError as exc:
                raise ValueError(f"{where}: no two-byte atlas slot for {char!r}") from exc
            pixels = _atlas_cell(atlas, slot)
            origin_x = x + char_index * ATLAS_CELL
            for glyph_y, row in enumerate(pixels):
                for glyph_x, value in enumerate(row):
                    target_x = origin_x + glyph_x
                    target_y = y + glyph_y
                    cell_index = (
                        (target_y // 8) * tile_map.width_tiles + target_x // 8
                    )
                    tile_index = tile_map.entries[cell_index] & 0x3FF
                    if (
                        value
                        and safe_tiles is not None
                        and tile_index not in safe_tiles
                    ):
                        if value == 2 and clip_shadow:
                            continue
                        raise ValueError(
                            f"{where}: glyph reaches unapproved tile {tile_index} "
                            f"at ({target_x}, {target_y})"
                        )
                    if value == 1:
                        canvas[target_y][target_x] = stroke_index
                    elif value == 2:
                        canvas[target_y][target_x] = shadow_index
                    elif value != 0:
                        raise ValueError(
                            f"{where}: atlas slot {slot} contains unsupported value {value}"
                        )

    if table.get("repack_tiles", False):
        return _repack_canvas(
            source,
            canvas,
            tile_map,
            where=str(table["file"]),
        )

    touched_cells = {
        (y // 8) * tile_map.width_tiles + (x // 8)
        for y in range(tile_map.height)
        for x in range(tile_map.width)
        if canvas[y][x] != original_canvas[y][x]
    }

    owners: dict[int, list[int]] = {}
    for cell_index, entry in enumerate(tile_map.entries):
        owners.setdefault(entry & 0x3FF, []).append(cell_index)

    output = bytearray(source)
    touched_tiles = {tile_map.entries[index] & 0x3FF for index in touched_cells}
    for tile_index in sorted(touched_tiles):
        renderings = {
            _encode_screen_cell(canvas, tile_map, cell_index)
            for cell_index in owners[tile_index]
        }
        if len(renderings) != 1:
            raise ValueError(
                f"{table['file']}: tile {tile_index} is shared by incompatible "
                f"screen cells {owners[tile_index]}"
            )
        start = tile_map.gfx_offset + tile_index * 32
        output[start:start + 32] = renderings.pop()
    return bytes(output)
