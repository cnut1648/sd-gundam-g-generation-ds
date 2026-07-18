#!/usr/bin/env python3
"""Reword validator: file has zero unencodable chars AND still passes check_p23."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from utils import text_codec  # noqa: E402


def main():
    path = Path(sys.argv[1])
    d = json.loads(path.read_text())
    blobs = ([it["name_zh"] for it in d["items"]] if d.get("kind") == "weapon_list"
             else d.get("paragraphs", []))
    bad = sorted({ch for t in blobs for ch in t
                  if not _ok(ch)})
    if bad:
        print(f"FAIL {path.name}: still unencodable: {''.join(bad)}")
        return 1
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "check_p23.py"),
                        str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAIL {path.name}: check_p23 regressed:\n{r.stdout}{r.stderr}")
        return 1
    print(f"PASS {path.name}")
    return 0


def _ok(ch):
    try:
        text_codec.encode(ch, allow_low15=True, surface="stage")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
