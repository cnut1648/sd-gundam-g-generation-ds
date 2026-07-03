#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vlm_judge.py — the screenshot-judge harness (crops → prompts → verdicts).

Pixel metrics are brittle: they need per-defect crop coordinates and thresholds
and miss whole classes (a glyph that OVERFLOWS its box is invisible to a tight
crop MAE).  The project's authoritative render verdict is therefore a strict
vision judge — any strong vision-language model, or a careful human — applying
the fixed rubric in test/vlm/judge_prompt.md to labelled, upscaled crops.

The harness has two halves so it needs NO model access or API key itself:

  1. PREPARE — turn a directory of raw melonDS window captures (256x403 PNGs,
     e.g. the frames a live test saved) into a judging bundle:

         .venv/bin/python test/vlm/vlm_judge.py prepare \\
             --shots <dir-with-pngs> --out <bundle-dir> [--scale 4]

     For every input frame it writes `full_top_<name>.png` and
     `full_bottom_<name>.png` (the two DS screens, point-filter upscaled so
     12px glyphs are legible to the judge), plus optional tight field crops
     (--crops spec.json, entries {"name","screen","box":[l,t,r,b]}).
     It emits `manifest.json` (every crop + its source) and `prompt.txt` —
     the verbatim judge prompt with the image list filled in.

  2. VERDICT — read the judge's answers back and gate:

         .venv/bin/python test/vlm/vlm_judge.py verdict \\
             --bundle <bundle-dir> --verdicts <verdicts.json>

     verdicts.json maps crop filename -> "CLEAN" | "RESIDUAL" | "BROKEN"
     (optionally "<verdict> — reason").  Exit 0 iff every crop in the manifest
     has a verdict and none is BROKEN.  RESIDUAL is tracked-pass (documented
     accepted imperfections; see the prompt).

How the judge fills the role: give the model (or human) the contents of
`prompt.txt` and the images; collect one line per image
(`<name>: CLEAN|BROKEN|RESIDUAL — reason`); convert to verdicts.json with
`lines-to-json` (or by hand):

         .venv/bin/python test/vlm/vlm_judge.py lines-to-json \\
             --lines answers.txt --out verdicts.json

There is deliberately no network/API integration: the bundle is inspectable,
re-judgeable, and archivable, and a human can stand in for the model 1:1.
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
from pathlib import Path

# behave under `| head` etc.
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

TEST_DIR = Path(__file__).resolve().parent.parent
PROMPT_MD = Path(__file__).resolve().parent / "judge_prompt.md"

# melonDS window geometry (see test/live/harness.py)
TOP_SCREEN = (0, 19, 256, 211)
BOTTOM_SCREEN = (0, 211, 256, 403)
SCREENS = {"top": TOP_SCREEN, "bottom": BOTTOM_SCREEN, "window": (0, 0, 256, 403)}


def _upscale_crop(src: Path, box, scale: int, dst: Path):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    l, t, r, b = box
    r, b = min(r, im.width), min(b, im.height)
    im = im.crop((l, t, r, b))
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)  # point filter
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)


def cmd_prepare(a) -> int:
    shots = Path(a.shots)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    frames = sorted(p for p in shots.glob("*.png"))
    if a.pattern:
        rx = re.compile(a.pattern)
        frames = [p for p in frames if rx.search(p.name)]
    if not frames:
        print(f"no frames matched in {shots}", file=sys.stderr)
        return 2
    crop_spec = []
    if a.crops:
        crop_spec = json.loads(Path(a.crops).read_text())
    manifest = {"prompt": "prompt.txt", "scale": a.scale, "crops": []}
    images = []
    for src in frames:
        stem = src.stem
        for screen in a.screens.split(","):
            box = SCREENS[screen]
            name = f"full_{screen}_{stem}.png"
            _upscale_crop(src, box, a.scale, out / name)
            manifest["crops"].append({"file": name, "source": str(src),
                                      "screen": screen, "box": list(box), "kind": "full"})
            images.append(name)
        for spec in crop_spec:
            sb = SCREENS[spec.get("screen", "window")]
            l, t, r, b = spec["box"]
            box = (sb[0] + l, sb[1] + t, sb[0] + r, sb[1] + b)
            name = f"crop_{spec['name']}_{stem}.png"
            _upscale_crop(src, box, max(a.scale, 4), out / name)
            manifest["crops"].append({"file": name, "source": str(src),
                                      "screen": spec.get("screen", "window"),
                                      "box": list(box), "kind": "field"})
            images.append(name)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    prompt = PROMPT_MD.read_text()
    img_list = "\n".join(str((out / n).resolve()) for n in images)
    prompt = prompt.replace(
        "Images: <absolute paths of every full_*.png and crop_*.png in the batch>",
        "Images:\n" + img_list)
    (out / "prompt.txt").write_text(prompt)
    print(f"bundle ready: {len(images)} crops from {len(frames)} frames -> {out}")
    print(f"  judge prompt : {out / 'prompt.txt'}")
    print(f"  manifest     : {out / 'manifest.json'}")
    print("give prompt.txt + the images to the judge; collect one verdict line per image;")
    print("then: vlm_judge.py lines-to-json --lines answers.txt --out verdicts.json")
    print("and : vlm_judge.py verdict --bundle", out, "--verdicts verdicts.json")
    return 0


VERDICT_RX = re.compile(r"^\s*(?P<name>[\w.\-]+\.png)\s*[:：]\s*(?P<v>CLEAN|BROKEN|RESIDUAL)\b(?P<why>.*)$",
                        re.IGNORECASE)


def cmd_lines_to_json(a) -> int:
    verdicts = {}
    for ln in Path(a.lines).read_text().splitlines():
        m = VERDICT_RX.match(ln.strip())
        if m:
            verdicts[m.group("name")] = (m.group("v").upper() + m.group("why")).strip()
    Path(a.out).write_text(json.dumps(verdicts, indent=1, ensure_ascii=False) + "\n")
    print(f"parsed {len(verdicts)} verdict(s) -> {a.out}")
    return 0 if verdicts else 1


def cmd_verdict(a) -> int:
    bundle = Path(a.bundle)
    manifest = json.loads((bundle / "manifest.json").read_text())
    verdicts = json.loads(Path(a.verdicts).read_text())
    missing, broken, residual, clean = [], [], [], []
    for c in manifest["crops"]:
        name = c["file"]
        v = verdicts.get(name)
        if v is None:
            missing.append(name)
            continue
        head = v.split()[0].split("—")[0].split("-")[0].strip().upper()
        if head.startswith("CLEAN"):
            clean.append(name)
        elif head.startswith("RESIDUAL"):
            residual.append((name, v))
        else:
            broken.append((name, v))
    print(f"=== VLM verdict gate :: {bundle} ===")
    print(f"  crops: {len(manifest['crops'])}  clean: {len(clean)}  "
          f"residual: {len(residual)}  broken: {len(broken)}  unjudged: {len(missing)}")
    for name, v in residual:
        print(f"  [tracked] {name}: {v}")
    for name, v in broken:
        print(f"  [BROKEN ] {name}: {v}")
    for name in missing:
        print(f"  [MISSING] {name}: no verdict (a missing/unreached screen is FAIL, never skip)")
    ok = not broken and not missing
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="build a judging bundle from raw window captures")
    p.add_argument("--shots", required=True, help="directory of 256x403 melonDS window PNGs")
    p.add_argument("--out", required=True, help="bundle output directory")
    p.add_argument("--scale", type=int, default=4, help="point-filter upscale factor (default 4)")
    p.add_argument("--screens", default="top,bottom", help="which screens to emit (top,bottom,window)")
    p.add_argument("--pattern", default=None, help="only frames whose name matches this regex")
    p.add_argument("--crops", default=None,
                   help="optional JSON list of tight field crops "
                        '[{"name":..,"screen":"top|bottom|window","box":[l,t,r,b]},..]')
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("lines-to-json", help="parse judge answer lines into verdicts.json")
    p.add_argument("--lines", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_lines_to_json)
    p = sub.add_parser("verdict", help="gate a bundle against a verdicts.json")
    p.add_argument("--bundle", required=True)
    p.add_argument("--verdicts", required=True)
    p.set_defaults(func=cmd_verdict)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
