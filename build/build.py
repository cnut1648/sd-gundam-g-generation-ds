#!/usr/bin/env python3
"""Build the Chinese translation ROM from the Japanese source ROM.

    python build/build.py <japanese-source.nds> <translated-output.nds> [--pad32m PATH]

One deterministic pass, driven entirely by the data/ folder:

  1. Verify the source ROM (sha1).
  2. Rebuild the ARM9 code binary: translated name/label/text pools baked over
     the Japanese image, documented code patches, relocated string pools and the
     CJK glyph atlas appended as extra autoload payloads. (utils/arm9_layout.py)
  3. Rebuild all 101 stage dialogue files (_STG*.bin): translated dialogue
     blocks spliced in with growth, absolute-pointer relocation and header-table
     alignment. (utils/stage_text.py)
  4. Rebuild the miscellaneous data files (menus, barks, cut-ins, graphics).
     (utils/data_files.py)
  5. Re-assemble the ROM container and verify every component and the final
     image against data/manifest.json.

Every step self-checks: a hash mismatch aborts the build with a message naming
the failing component. On success the output is byte-reproducible.

Options:
  --pad32m PATH   also write a 32 MiB 0xFF-padded image (some flash carts want
                  power-of-two sizes).
  --skip-verify   build even if component hashes mismatch (for development on
                  new translations; the final manifest check becomes a warning).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import arm9_layout, data_files, rom, stage_text  # noqa: E402


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", help="Japanese source ROM (.nds)")
    ap.add_argument("output", help="translated ROM to write (.nds)")
    ap.add_argument("--pad32m", metavar="PATH", default=None,
                    help="also write a 32 MiB padded image")
    ap.add_argument("--skip-verify", action="store_true",
                    help="don't abort on component hash mismatches")
    args = ap.parse_args()

    t0 = time.time()
    manifest = rom.load_manifest()

    def check(name: str, blob: bytes) -> None:
        if args.skip_verify:
            got = rom.sha1(blob)
            expect = manifest["components"].get(name)
            if got != expect:
                log(f"  WARNING: {name} hash {got[:12]} != expected {str(expect)[:12]}")
            return
        rom.check_component(name, blob, manifest)

    # ---- 1. source ROM ------------------------------------------------------
    log(f"loading source ROM: {args.source}")
    src_bytes = Path(args.source).read_bytes()
    src_sha = rom.sha1(src_bytes)
    if src_sha != manifest["source_rom"]["sha1"]:
        log(f"ERROR: source ROM sha1 {src_sha}")
        log(f"       expected       {manifest['source_rom']['sha1']}")
        log("       This build starts from the Japanese cartridge dump listed in data/manifest.json.")
        return 1
    game = rom.load_rom(args.source)

    # ---- 2. ARM9 ------------------------------------------------------------
    log("building ARM9 (names, labels, code patches, string pools, glyph atlas)")
    arm9 = arm9_layout.build_arm9(bytes(game.arm9), verify=not args.skip_verify)
    check("arm9", arm9)
    game.arm9 = bytearray(arm9)

    # ---- 3. stage dialogue files -------------------------------------------
    stages = list(stage_text.iter_stage_data())
    log(f"building {len(stages)} stage dialogue files")
    for name, stage_data in stages:
        jp = rom.get_file(game, name)
        built = stage_text.build_stage_file(jp, stage_data)
        check(name, built)
        rom.set_file(game, name, built)

    # ---- 4. miscellaneous data files ---------------------------------------
    misc = sorted(data_files.DATA_FILE_TABLES)
    log(f"building {len(misc)} data files (barks, cut-ins, effect text, biographies, graphics)")
    for name in misc:
        jp = rom.get_file(game, name)
        built = data_files.build_data_file(name, jp, verify=not args.skip_verify)
        check(name, built)
        rom.set_file(game, name, built)

    # ---- 5. assemble + verify ----------------------------------------------
    log("assembling ROM container")
    out_bytes = game.save()
    out_sha = rom.sha1(out_bytes)
    expect = manifest["output_rom"]["sha1"]
    if out_sha == expect:
        log(f"final ROM sha1 {out_sha}  (MATCHES the shipped translation)")
    else:
        log(f"final ROM sha1 {out_sha}")
        log(f"expected       {expect}")
        if not args.skip_verify:
            log("ERROR: output does not match the recorded build. Aborting (use --skip-verify to keep it).")
            return 1
        log("WARNING: output differs from the recorded build (expected when translations were edited).")

    out_path = Path(args.output)
    out_path.write_bytes(out_bytes)
    log(f"wrote {out_path}  ({len(out_bytes):,} bytes)")

    if args.pad32m:
        rom.write_padded(out_bytes, Path(args.pad32m))
        log(f"wrote {args.pad32m}  (32 MiB padded image)")

    log(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
