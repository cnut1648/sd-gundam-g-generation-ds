#!/usr/bin/env python3
"""ONE-TIME migration: make stage-edit zh_hex layout-independent.

Mixed dialogue edits in data/zh/stages/*.json replace regions that contain
live event bytecode with absolute `13`/`16` operands.  Historically the
zh_hex BAKED those operands as already-relocated values — correct only for
the file layout of the day they were authored.  The first later size change
upstream of a target silently invalidated them (the `_STG20A` replay-branch
soft-lock, BUG-1 of the 2026-07 stage sweep; `_STG10B` carried the same
latent corruption at -4 since v1.1).

This migration rewrites every such baked operand window back to the JP
ORIGINAL bytes.  `utils.stage_text.apply_edits` now relocates in-edit operand
windows exactly like every other pointer (and refuses baked in-buffer values),
so after this migration the build result is byte-identical to before EXCEPT
where the baked value no longer matched the current layout — i.e. exactly the
defective operands:

    _STG20A.bin  2 fork operands (baked at the pre-PLANT shift, -23)
    _STG10B.bin  4 fork operands (baked at a pre-v1.1-reflow shift, -4)

Applied once; kept as evidence.  Idempotent (second run changes nothing).
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from utils import rom, stage_text  # noqa: E402

BASE = stage_text.STAGE_RAM_BASE
JP_ROM = REPO / "0098 - SD Gundam G Generation DS (Japan).nds"


def migrate_stage(path: Path, jp: bytes) -> int:
    d = json.loads(path.read_text(encoding="utf-8"))
    entries = []          # (sort_off, json_obj, key, bytearray)
    for e in d.get("edits", []):
        entries.append((int(e["jp_offset"], 16), e["jp_len"], e, "zh_hex",
                        bytearray.fromhex(e["zh_hex"])))
    for ins in d.get("inserts", []):
        entries.append((int(ins["jp_offset"], 16), 0, ins, "hex",
                        bytearray.fromhex(ins["hex"])))
    entries.sort(key=lambda t: t[0])
    edits = [(off, old_len, bytes(buf)) for off, old_len, _o, _k, buf in entries]

    exclude = bytearray(len(jp))
    for off, old_len, _n in edits:
        for q in range(off, off + old_len):
            exclude[q] = 1
    payload = stage_text.payload_mask(jp)
    outside = set(stage_text.pointer_offsets(jp, payload, exclude))

    changed = 0
    for p in stage_text.pointer_offsets(jp, payload, None):
        if p in outside:
            continue
        srcs = stage_text.edit_window_sources(p, edits)
        if srcs is None:
            continue
        rep = bytearray()
        mappable = True
        for s in srcs:
            if s[0] == "jp":
                rep.append(jp[s[1]])
            else:
                _t, k, r = s
                buf = entries[k][4]
                if r >= len(buf):
                    mappable = False
                    break
                rep.append(buf[r])
        if not mappable:
            raise AssertionError(f"{path.name}: window 0x{p:x} unmappable")
        jp_bytes = jp[p:p + 4]
        if bytes(rep) == jp_bytes:
            continue                                  # already canonical
        value = struct.unpack_from("<I", bytes(rep), 0)[0]
        if not (BASE <= value < BASE + stage_text.STAGE_BUFFER_SIZE):
            continue                                  # deliberate rewrite/drop
        # baked layout value -> restore the JP original bytes
        for i, s in enumerate(srcs):
            if s[0] == "edit":
                _t, k, r = s
                entries[k][4][r] = jp_bytes[i]
        changed += 1

    if changed:
        for _off, _l, obj, key, buf in entries:
            obj[key] = bytes(buf).hex()
        path.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return changed


def main() -> int:
    jprom = rom.load_rom(JP_ROM)
    total = 0
    for path in sorted((REPO / "data" / "zh" / "stages").glob("_STG*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        jp = rom.get_file(jprom, d["file"])
        n = migrate_stage(path, jp)
        if n:
            print(f"{path.name}: {n} operand window(s) -> JP bytes")
            total += n
    print(f"total: {total} windows migrated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
