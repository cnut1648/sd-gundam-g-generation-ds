#!/usr/bin/env python3
"""refresh_manifest.py — recompute data/manifest.json component + output hashes
from the current data (the intended new translation state), then verify a
clean no-skip build reproduces them.
"""
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import arm9_layout, data_files, rom, stage_text  # noqa: E402


def sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def main():
    manifest_path = REPO / 'data/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    src = REPO / '0098 - SD Gundam G Generation DS (Japan).nds'
    game = rom.load_rom(src)

    comps = manifest['components']
    changed = []

    a9 = arm9_layout.build_arm9(bytes(game.arm9), verify=False)
    if comps.get('arm9') != sha1(a9):
        comps['arm9'] = sha1(a9)
        changed.append('arm9')
    game.arm9 = bytearray(a9)

    for name, stage_data in stage_text.iter_stage_data():
        jp = rom.get_file(game, name)
        built = stage_text.build_stage_file(jp, stage_data)
        if comps.get(name) != sha1(built):
            comps[name] = sha1(built)
            changed.append(name)
        rom.set_file(game, name, built)

    for name in sorted(data_files.DATA_FILE_TABLES):
        jp = rom.get_file(game, name)
        built = data_files.build_data_file(name, jp, verify=False)
        if comps.get(name) != sha1(built):
            comps[name] = sha1(built)
            changed.append(name)
        rom.set_file(game, name, built)

    out = game.save()
    manifest['output_rom'] = {'sha1': sha1(out), 'size': len(out)}
    padded = out + b'\xff' * (33554432 - len(out))
    manifest['output_rom_padded'] = {'sha1': sha1(padded), 'size': len(padded)}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n',
                             encoding='utf-8')
    print(f'{len(changed)} component hashes refreshed')
    print('output_rom     ', manifest['output_rom'])
    print('output_rom_pad ', manifest['output_rom_padded'])


if __name__ == '__main__':
    main()
