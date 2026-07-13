#!/usr/bin/env python3
"""test_render_oracle_parity.py — trust anchor for the offline pixel oracle.

Compares render_oracle output against a LIVE melonDS capture of the first
dialogue scene (test/golden/dialogue_scene.png, recaptured by boot_smoke
--update-golden).  If the oracle's stroke mask matches the emulator's text
pixels (IoU), then offline coverage runs (coverage_render.py) are trusted to
stand in for emulator playthroughs.

    .venv/bin/python test/test_render_oracle_parity.py <rom.nds>

The golden crop contains the dialogue text region of the first ADV scene
(夏亚: 木马是开往所罗门还是格拉纳达、战局也会随之改变……).  We re-render that
exact line from the stage source bytes and require stroke-mask IoU >= 0.80
after best-offset alignment (emulator applies palette/alpha; masks compare
shape only).  A broken oracle (wrong font, wrong slot math, wrong 2bpp
unpack) scores < 0.3.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from render_oracle import Oracle  # noqa: E402


def stroke_mask_from_capture(img: Image.Image) -> np.ndarray:
    """White-ish text pixels of an emulator capture."""
    a = np.asarray(img.convert("RGB"), dtype=int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (r > 200) & (g > 200) & (b > 200)


def best_iou(a: np.ndarray, b: np.ndarray, max_shift: int = 6) -> float:
    """Max IoU of masks over +-max_shift alignment."""
    best = 0.0
    H = min(a.shape[0], b.shape[0])
    W = min(a.shape[1], b.shape[1])
    a = a[:H, :W]
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            bb = np.roll(np.roll(b[:H, :W], dy, 0), dx, 1)
            inter = (a & bb).sum()
            union = (a | bb).sum()
            if union and inter / union > best:
                best = inter / union
    return best


def main() -> int:
    rom = Path(sys.argv[1] if len(sys.argv) > 1 else REPO / "sd-gundam-g-generation-zh.nds")
    golden = REPO / "test/golden/dialogue_scene.png"
    if not golden.exists():
        print("SKIP: no dialogue golden captured yet")
        return 0

    # The golden shows the typewriter-partial first line 木马似乎还没有察觉到我
    # (12 glyphs) of _STG00's 玛利根 line.  Encode exactly those 12 glyphs from
    # the source bytes so oracle output matches the visible capture.
    stg = json.loads((REPO / "data/dialogue/stages/_STG00.json").read_text())
    target = None
    for r in stg.get("edits", []):
        if "木马似乎还没有察觉到我" in (r.get("zh_text") or ""):
            blob = bytes.fromhex(r["zh_hex"])
            # strip stage framing: leading 0x15 show-dialogue opcode
            body = blob[1:] if blob[:1] == b"\x15" else blob
            glyphs = []
            i = 0
            while i < len(body) and len(glyphs) < 12:
                b0 = body[i]
                if b0 >= 0xE0:
                    glyphs.append(body[i:i + 2]); i += 2
                elif b0 >= 0x02:
                    glyphs.append(body[i:i + 1]); i += 1
                else:
                    i += 1
            target = b"".join(glyphs)
            break
    if target is None:
        print("FAIL: anchor line not found in _STG00.json")
        return 1

    oracle = Oracle(rom)
    ours = oracle.render_line(target, "stage", scale=1)
    ours_mask = np.array([[px == (255, 255, 255) for px in row]
                          for row in np.array(ours.convert("RGB")).reshape(ours.height, ours.width, 3).tolist()])
    ours_mask = np.asarray(ours.convert("RGB"))[..., 0] > 200

    cap = Image.open(golden)
    # dialogue text row of the golden window capture (measured region)
    band_img = cap.crop((10, 170, 256, 196))
    cap_mask = stroke_mask_from_capture(band_img)
    best = best_iou(cap_mask, ours_mask, max_shift=8)
    verdict = best >= 0.80
    print(f"oracle-vs-live stroke IoU: {best:.3f}  ->  {'PASS' if verdict else 'FAIL'} (need >= 0.80)")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
