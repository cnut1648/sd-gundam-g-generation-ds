"""Unit tests for guarded static BG label repainting."""
from __future__ import annotations

import struct
import unittest

from utils import static_graphics


def resource(entries: tuple[int, int, int, int]) -> bytes:
    header = bytearray(12)
    header[2:4] = bytes((2, 2))
    struct.pack_into("<HHHH", header, 4, 12, 8, 20, 32 * 4)
    screen = b"".join(struct.pack("<H", entry) for entry in entries)
    tiles = bytes([0x66]) * (32 * 4)
    return bytes(header) + screen + tiles


def table(tiles: list[int]) -> dict:
    return {
        "file": "synthetic.bin",
        "palette_indices": {"stroke": 15, "shadow": 2},
        "labels": [
            {
                "text": "A",
                "tiles": tiles,
                "x": 0,
                "y": 0,
                "clear": {
                    "x": 0,
                    "y": 0,
                    "width": 12,
                    "height": 12,
                    "sample_x": 15,
                },
            }
        ],
    }


class StaticGraphicsTest(unittest.TestCase):
    def test_unique_tiles_are_repainted_without_geometry_changes(self):
        source = resource((0, 1, 2, 3))
        atlas = bytes([1]) + bytes(static_graphics.ATLAS_CELL_BYTES - 1)
        output = static_graphics.repaint_atlas_text(
            source, table([0, 1, 2, 3]), atlas=atlas, char_slots={"A": 0}
        )
        canvas, _tile_map = static_graphics.decode_index_canvas(output)
        self.assertEqual(canvas[0][0], 15)
        self.assertEqual(len(output), len(source))
        self.assertEqual(output[12:20], source[12:20])

    def test_incompatible_shared_tile_is_rejected(self):
        source = resource((0, 0, 0, 0))
        atlas = bytes([1]) + bytes(static_graphics.ATLAS_CELL_BYTES - 1)
        with self.assertRaisesRegex(ValueError, "shared by incompatible"):
            static_graphics.repaint_atlas_text(
                source, table([0]), atlas=atlas, char_slots={"A": 0}
            )

    def test_repack_tiles_copy_on_write_shared_cells(self):
        source = resource((0, 0, 0, 0))
        atlas = bytes([1]) + bytes(static_graphics.ATLAS_CELL_BYTES - 1)
        spec = table([])
        spec["repack_tiles"] = True
        del spec["labels"][0]["tiles"]
        output = static_graphics.repaint_atlas_text(
            source, spec, atlas=atlas, char_slots={"A": 0}
        )
        canvas, tile_map = static_graphics.decode_index_canvas(output)
        self.assertEqual(canvas[0][0], 15)
        self.assertEqual(canvas[0][8], 6)
        self.assertNotEqual(tile_map.entries[0] & 0x3FF, tile_map.entries[1] & 0x3FF)
        self.assertEqual(len(output), len(source))


if __name__ == "__main__":
    unittest.main()
