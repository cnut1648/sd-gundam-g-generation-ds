#!/usr/bin/env python3
"""Targeted candidate-ROM coverage check for the six gallery resources.

Usage:
    .venv/bin/python test/test_coverage_render_gallery.py <candidate.nds>
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test"))

from coverage_render import _iter_gallery_corpus  # noqa: E402


def has_standalone_nul(payload: bytes) -> bool:
    """Match the gallery token grammar: 0x00 is data after a >=0xE0 lead."""
    i = 0
    while i < len(payload):
        if payload[i] >= 0xE0:
            i += 2
            continue
        if payload[i] == 0:
            return True
        i += 1
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <candidate.nds>")
        return 2

    rows = list(_iter_gallery_corpus(Path(sys.argv[1])))
    groups = Counter(src.split("/", 2)[1] for src, _, _, _ in rows)
    expected = Counter(ev=54, character=274 * 2, unit=239 * 2)

    assert groups == expected, f"gallery coverage groups changed: {groups} != {expected}"
    assert len(rows) == 1080
    assert all(surface == "bank" for _, surface, _, _ in rows)
    assert all(payload and not has_standalone_nul(payload)
               for _, _, payload, _ in rows)
    print(f"gallery coverage: {len(rows)} rows {dict(groups)}; all surface=bank")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
