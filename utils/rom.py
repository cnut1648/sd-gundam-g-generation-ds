"""ROM container helpers: load, patch components, save, verify.

The build treats the ROM as {code binary (arm9), NitroFS files, everything else
untouched}. ndspy handles the NDS container (header, FAT/FNT, overlay tables);
re-saving after replacing components reproduces the container deterministically.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ndspy.rom

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

PAD32M_SIZE = 33_554_432        # 32 MiB flash-cart image size
PAD_BYTE = 0xFF


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def load_rom(path: str | Path) -> ndspy.rom.NintendoDSRom:
    return ndspy.rom.NintendoDSRom.fromFile(str(path))


def load_manifest() -> dict:
    return json.loads((DATA_DIR / "manifest.json").read_text())


def file_index(rom: ndspy.rom.NintendoDSRom, name: str) -> int:
    """NitroFS file id for a filename (raises if unknown)."""
    idx = rom.filenames.idOf(name)
    if idx is None:
        raise KeyError(f"NitroFS file not found: {name}")
    return idx


def get_file(rom: ndspy.rom.NintendoDSRom, name: str) -> bytes:
    return bytes(rom.files[file_index(rom, name)])


def set_file(rom: ndspy.rom.NintendoDSRom, name: str, data: bytes) -> None:
    rom.files[file_index(rom, name)] = bytearray(data)


def check_component(name: str, data: bytes, manifest: dict) -> None:
    """Assert a built component matches its recorded sha1 (loud, actionable)."""
    expect = manifest["components"][name]
    got = sha1(data)
    if got != expect:
        raise AssertionError(
            f"component {name!r} hash mismatch:\n  built    {got}\n  expected {expect}\n"
            f"The build data and builder code are out of sync for this component.")


def write_padded(rom_bytes: bytes, out_path: Path) -> None:
    """Write the 32 MiB flash-cart padded variant (0xFF fill)."""
    if len(rom_bytes) > PAD32M_SIZE:
        raise ValueError(f"ROM larger than 32 MiB: {len(rom_bytes)}")
    out_path.write_bytes(rom_bytes + bytes([PAD_BYTE]) * (PAD32M_SIZE - len(rom_bytes)))
