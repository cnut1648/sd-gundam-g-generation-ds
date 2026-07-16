#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harness.py — shared melonDS emulator harness for the live test tier.

Runs melonDS headlessly under Xvfb + fluxbox, drives it with synthesized
keyboard/mouse input (xte / xdotool), and captures window screenshots
(ImageMagick `import`).  Every live test builds on this module.

Environment (installed once per machine; see test/README.md):
  * melonDS 1.1 built from source           -> /usr/local/bin/melonDS
    (override with the MELONDS_BIN environment variable)
  * Xvfb, fluxbox, xdotool, xautomation (xte), imagemagick (import)
  * Pillow + numpy in the repo venv (image comparisons)

melonDS configuration
---------------------
melonDS reads ~/.config/melonDS/melonDS.toml.  A hand-written config file
crashes melonDS's serializer, so `ensure_config()` uses generate-then-patch:
if the file is missing it launches melonDS once (headless, no ROM) so melonDS
writes its own defaults, kills it, then patches ONLY the needed keys in place:

    [Emu]                 ExternalBIOSEnable=false, DirectBoot=true
    [JIT]                 Enable=false            (determinism)
    [3D]                  Renderer=0              (software; headless-safe)
    [Instance0.Keyboard]  the 12 DS buttons -> this harness's keysyms
                          (fresh configs default every binding to -1 =
                          UNBOUND, silently dropping all input!)
    [Instance0.Gdb]       Enabled=true + ARM9/ARM7 ports (for the freeze test)

Window geometry: melonDS 1.1 opens a 256x403 window — a ~19px menu bar, then
the two 256x192 DS screens (top: y 19..211, bottom: y 211..403).  All crop
constants below use that geometry.

Savestate warning
-----------------
A melonDS savestate restores the code image (arm9) AND the loaded stage data
into emulated RAM.  Loading a savestate made on a DIFFERENT build silently
replaces the ROM-under-test's code/data with the savestate's — every "fix
verified" against a foreign savestate is testing the WRONG bytes.  Live tests
therefore boot FRESH (DirectBoot) and use cartridge .sav files (test/fixtures/)
for progress; savestates are only ever created and consumed within one run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

MELONDS_BIN = os.environ.get("MELONDS_BIN", "/usr/local/bin/melonDS")
MELON_CFG = Path(os.environ.get("HOME", "/root")) / ".config/melonDS/melonDS.toml"

TEST_DIR = Path(__file__).resolve().parent.parent
GOLDEN = TEST_DIR / "golden"
FIXTURES = TEST_DIR / "fixtures"

# DS button -> X keysym (must match the [Instance0.Keyboard] map below)
BTN = {"A": "x", "B": "z", "X": "s", "Y": "a", "L": "q", "R": "w",
       "START": "Return", "SELECT": "space",
       "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right"}

# DS button -> bit in the instrumented-build inject mask (active-high).
# The local melonDS build carries a file-based input hook (EmuThread.cpp):
# each frame it reads /tmp/melon_inject ("<pressedmask_hex> <touch01> <dsx> <dsy>")
# and overrides the pad state — the reliable input path under Xvfb, where
# synthesized X keyboard events do not reach the emulated ARM7 pad service.
BTN_BIT = {"A": 0x001, "B": 0x002, "SELECT": 0x004, "START": 0x008,
           "RIGHT": 0x010, "LEFT": 0x020, "UP": 0x040, "DOWN": 0x080,
           "R": 0x100, "L": 0x200, "X": 0x400, "Y": 0x800}
INJECT_PATH = Path("/tmp/melon_inject")
_INJECT_OK: bool | None = None


def inject_available() -> bool:
    """True when the melonDS binary carries the file-input hook."""
    global _INJECT_OK
    if _INJECT_OK is None:
        try:
            _INJECT_OK = b"/tmp/melon_inject" in Path(MELONDS_BIN).read_bytes()
        except Exception:
            _INJECT_OK = False
    return _INJECT_OK


def _inject_write(pressed_mask: int, touch: int = 0, x: int = 0, y: int = 0) -> None:
    tmp = INJECT_PATH.with_suffix(".tmp")
    tmp.write_text(f"{pressed_mask:x} {touch} {x} {y}\n")
    tmp.replace(INJECT_PATH)


def _inject_clear() -> None:
    try:
        INJECT_PATH.unlink()
    except FileNotFoundError:
        pass
# DS button -> Qt key code written into melonDS.toml
MELON_KEYMAP = {"A": 88, "B": 90, "Select": 32, "Start": 16777220,
                "Right": 16777236, "Left": 16777234, "Up": 16777235, "Down": 16777237,
                "R": 87, "L": 81, "X": 83, "Y": 65}

# window crops (left, top, right, bottom) in the 256x403 melonDS window
WHOLE_WINDOW = (0, 0, 256, 403)
TOP_SCREEN = (0, 19, 256, 211)
BOTTOM_SCREEN = (0, 211, 256, 403)
DIALOGUE_TEXT = (0, 348, 256, 396)      # the two rendered ADV dialogue lines
SPEAKER_LINE = (0, 348, 256, 372)       # the upper speaker/nameplate line
INFO_PAGE = (0, 211, 256, 403)          # whole lower screen = unit-info page
NEWGAME_BUTTON = (129, 283)             # the New Game button on the title menu


def log(*a):
    print("[live]", *a, file=sys.stderr, flush=True)


def state_path(display: int) -> Path:
    return Path(f"/tmp/sdglive_{display}_state.json")


# =============================================================================
# melonDS config bootstrap (generate-then-patch)
# =============================================================================
def _patch_toml(path: Path, want: dict) -> bool:
    """Section-aware key patch of a melonDS toml.  `want` maps section name
    ('' = top level) -> {key: value-string}.  Only listed keys are touched;
    missing keys are appended at the section end.  Returns True if changed."""
    lines = path.read_text().splitlines()
    out, changed = [], False
    cur = ""
    pending = dict(want)

    def flush(section):
        nonlocal changed
        for k, v in pending.pop(section, {}).items():
            out.append(f"{k} = {v}")
            changed = True

    seen_in = {s: set() for s in want}
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur in pending:
                miss = {k: v for k, v in want[cur].items() if k not in seen_in.get(cur, set())}
                if miss:
                    pending[cur] = miss
                    flush(cur)
                else:
                    pending.pop(cur, None)
            cur = s[1:-1]
            out.append(ln)
            continue
        m = re.match(r"^(\w+)\s*=", s)
        if m and cur in want and m.group(1) in want[cur]:
            k = m.group(1)
            seen_in.setdefault(cur, set()).add(k)
            new = f"{k} = {want[cur][k]}"
            if s != new:
                out.append(new)
                changed = True
            else:
                out.append(ln)
            continue
        out.append(ln)
    if cur in pending:
        miss = {k: v for k, v in want[cur].items() if k not in seen_in.get(cur, set())}
        if miss:
            pending[cur] = miss
            flush(cur)
        else:
            pending.pop(cur, None)
    for sec, kv in pending.items():
        out.append(f"[{sec}]")
        for k, v in kv.items():
            out.append(f"{k} = {v}")
        changed = True
    if changed:
        path.write_text("\n".join(out) + "\n")
    return changed


def ensure_config(gdb_ports: tuple[int, int] | None = None) -> None:
    """Make sure ~/.config/melonDS/melonDS.toml exists and carries the settings
    the harness depends on.  If absent, let melonDS GENERATE its defaults first
    (a fully hand-written file crashes melonDS's serializer), then patch."""
    if not MELON_CFG.exists():
        log("melonDS config missing — generating defaults (brief headless launch)")
        disp = pick_display()
        xvfb = subprocess.Popen(["Xvfb", f":{disp}", "-screen", "0", "640x480x24", "-nolisten", "tcp"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
        env = dict(os.environ, DISPLAY=f":{disp}")
        mel = subprocess.Popen([MELONDS_BIN], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # melonDS only writes melonDS.toml on a GRACEFUL quit (closeEvent /
        # end of exec()), which SIGTERM/SIGKILL skip.  It installs a SIGINT
        # handler that runs the clean-shutdown path, so give it a moment to come
        # up, then SIGINT it and wait for the config to land.
        time.sleep(6.0)
        mel.send_signal(signal.SIGINT)
        for _ in range(40):
            if MELON_CFG.exists():
                break
            time.sleep(0.5)
        mel.terminate()
        time.sleep(0.5)
        mel.kill()
        xvfb.terminate()
        if not MELON_CFG.exists():
            raise RuntimeError("melonDS did not generate its config file "
                               f"({MELON_CFG}); is {MELONDS_BIN} runnable?")
    want = {
        "Emu": {"ExternalBIOSEnable": "false", "DirectBoot": "true"},
        "JIT": {"Enable": "false"},
        "3D": {"Renderer": "0"},
        "Instance0.Keyboard": {k: str(v) for k, v in MELON_KEYMAP.items()},
    }
    if gdb_ports:
        want["Instance0.Gdb"] = {"Enabled": "true"}
        want["Instance0.Gdb.ARM9"] = {"Port": str(gdb_ports[0]), "BreakOnStartup": "false"}
        want["Instance0.Gdb.ARM7"] = {"Port": str(gdb_ports[1]), "BreakOnStartup": "false"}
    if _patch_toml(MELON_CFG, want):
        log(f"patched melonDS config ({MELON_CFG})")


# =============================================================================
# display management
# =============================================================================
def pick_display() -> int:
    used = set()
    for p in Path("/tmp/.X11-unix").glob("X*"):
        try:
            used.add(int(p.name[1:]))
        except ValueError:
            pass
    for p in Path("/tmp").glob("sdglive_*_state.json"):
        try:
            used.add(int(p.name.split("_")[1]))
        except (ValueError, IndexError):
            pass
    for n in range(151, 240):
        if n not in used:
            return n
    raise RuntimeError("no free X display number found")


def kill_display(display: int) -> None:
    p = state_path(display)
    if p.exists():
        try:
            s = json.loads(p.read_text())
            for k in ("pid", "fluxbox_pid", "xvfb_pid"):
                if s.get(k):
                    try:
                        os.kill(int(s[k]), 9)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            p.unlink()
        except Exception:
            pass
    subprocess.run(["pkill", "-9", "-f", f"Xvfb :{display}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for lock in (f"/tmp/.X{display}-lock", f"/tmp/.X11-unix/X{display}"):
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass


# =============================================================================
# the emulator session
# =============================================================================
class Emulator:
    """One headless melonDS session on a private X display."""

    def __init__(self, display: int, workdir: Path | None = None):
        self.display = display
        self.env = dict(os.environ, DISPLAY=f":{display}")
        self.workdir = Path(workdir) if workdir else Path(f"/tmp/sdglive_{display}")
        self.state: dict = {}

    # -- lifecycle -------------------------------------------------------------
    def launch(self, rom: Path, sav: Path | None = None, timeout: float = 30.0):
        """Kill anything on this display, start Xvfb + fluxbox + melonDS with a
        private copy of the ROM (so stray .sav/.ml* never leak between runs),
        optionally seeding a cartridge save next to it."""
        ensure_config()
        kill_display(self.display)
        _inject_clear()          # never leak a previous run's pressed state
        self.workdir.mkdir(parents=True, exist_ok=True)
        run_rom = self.workdir / "run.nds"
        shutil.copy(rom, run_rom)
        for ext in (".sav", ".ml1", ".ml2", ".ml3", ".ml8"):
            p = run_rom.with_suffix(ext)
            if p.exists():
                p.unlink()
        if sav:
            shutil.copy(sav, run_rom.with_suffix(".sav"))
        disp = f":{self.display}"
        log(f"launching Xvfb {disp} + fluxbox + melonDS ({Path(rom).name}"
            + (f", save={Path(sav).name}" if sav else "") + ")")
        xvfb = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        flux = subprocess.Popen(["fluxbox"], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        mlog = open(self.workdir / "melonds.log", "wb")
        mel = subprocess.Popen([MELONDS_BIN, str(run_rom)], env=self.env,
                               stdout=mlog, stderr=subprocess.STDOUT)
        wid = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = subprocess.run(["xdotool", "search", "--name", "melonDS"],
                               env=self.env, capture_output=True, text=True)
            for w in [int(x) for x in r.stdout.split() if x.strip()]:
                g = subprocess.run(["xdotool", "getwindowgeometry", str(w)],
                                   env=self.env, capture_output=True, text=True).stdout
                m = re.search(r"Geometry:\s*(\d+)x(\d+)", g)
                if m and int(m.group(1)) >= 200 and int(m.group(2)) >= 300:
                    wid = w
                    break
            if wid:
                break
            time.sleep(0.5)
        if wid is None:
            kill_display(self.display)
            raise RuntimeError("melonDS window did not appear (see melonds.log)")
        time.sleep(2.0)
        self.state = {"display": f":{self.display}", "pid": mel.pid, "wid": wid,
                      "rom": str(run_rom), "xvfb_pid": xvfb.pid, "fluxbox_pid": flux.pid}
        state_path(self.display).write_text(json.dumps(self.state, indent=1))
        self.focus()
        log(f"emulator up: pid={mel.pid} wid={wid}")

    def kill(self):
        kill_display(self.display)
        _inject_clear()

    # -- input -------------------------------------------------------------------
    def focus(self):
        wid = str(self.state["wid"])
        subprocess.run(["xdotool", "windowactivate", wid], env=self.env, capture_output=True)
        subprocess.run(["xdotool", "windowfocus", wid], env=self.env, capture_output=True)
        time.sleep(0.15)

    def key(self, name: str, hold_ms: int = 140, pause: float = 0.5, count: int = 1):
        """Press a DS button.  The key is HELD ~140ms — an instantaneous tap is
        too short for the game loop to sample reliably (dropped inputs).

        Preferred path: the instrumented build's /tmp/melon_inject file hook
        (X-synthesized keys do not reach the emulated pad in this environment).
        Falls back to xte when the binary lacks the hook."""
        bit = BTN_BIT.get(name.upper())
        if inject_available() and bit is not None:
            for _ in range(count):
                _inject_write(bit)
                time.sleep(max(hold_ms, 60) / 1000.0)
                _inject_write(0)
                time.sleep(pause)
            return
        ks = BTN.get(name.upper(), name)
        for _ in range(count):
            self.focus()
            subprocess.run(["xte", f"keydown {ks}", f"usleep {hold_ms * 1000}", f"keyup {ks}"],
                           env=self.env, capture_output=True)
            time.sleep(pause)

    def tap(self, x: int, y: int, hold_ms: int = 250, pause: float = 0.6):
        """Touch a WINDOW-coordinate point (bottom DS screen starts at y=211).

        Preferred path: the /tmp/melon_inject hook (touch coords are native
        bottom-screen coords: dsx = x, dsy = y - 211). Fallback: xdotool+xte."""
        if inject_available() and y >= 211:
            _inject_write(0, 1, max(0, min(255, int(x))), max(0, min(191, int(y) - 211)))
            time.sleep(max(hold_ms, 80) / 1000.0)
            _inject_write(0, 0, 0, 0)
            time.sleep(pause)
            return
        self.focus()
        wid = str(self.state["wid"])
        subprocess.run(["xdotool", "mousemove", "--window", wid, "--sync", str(x), str(y)],
                       env=self.env, capture_output=True)
        time.sleep(0.1)
        subprocess.run(["xte", "mousedown 1", f"usleep {hold_ms * 1000}", "mouseup 1"],
                       env=self.env, capture_output=True)
        time.sleep(pause)

    # -- capture -------------------------------------------------------------------
    def shot(self, path: Path, retries: int = 3) -> bool:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        wid = str(self.state["wid"])
        for _ in range(retries):
            subprocess.run(["import", "-window", wid, str(path)],
                           env=self.env, capture_output=True, timeout=20)
            if path.exists() and path.stat().st_size > 0:
                return True
            time.sleep(0.4)
        return False

    # -- savestates (single-run scratch only; see the module docstring) -------------
    def save_state(self, slot: int = 8):
        self.focus()
        subprocess.run(["xte", "keydown Shift_L", f"keydown F{slot}", "usleep 150000",
                        f"keyup F{slot}", "keyup Shift_L"], env=self.env, capture_output=True)
        time.sleep(0.8)

    def load_state(self, slot: int = 8):
        self.key(f"F{slot}", pause=1.6)


# =============================================================================
# image helpers (Pillow + numpy, imported lazily)
# =============================================================================
def _np():
    import numpy as np
    return np


def load_gray(path):
    np = _np()
    from PIL import Image
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def crop(arr, box):
    l, t, r, b = box
    h, w = arr.shape
    return arr[max(0, t):min(h, b), max(0, l):min(w, r)]


def mean_luma(arr, box=None):
    a = crop(arr, box) if box else arr
    return float(_np().mean(a))


def luma_std(arr, box=None):
    a = crop(arr, box) if box else arr
    return float(_np().std(a))


def region_mae(img_a, img_b, box):
    """Mean-abs-error between the same region of two frames at NATIVE resolution
    (preserves per-glyph detail — a coarse downscale averages garble away).
    If shapes differ the second crop is resized to the first's shape."""
    np = _np()
    a, b = crop(img_a, box), crop(img_b, box)
    if a.shape != b.shape:
        from PIL import Image
        b = np.asarray(Image.fromarray(b.astype("uint8")).resize(
            (a.shape[1], a.shape[0]), Image.BILINEAR), dtype=np.float32)
    return float(np.mean(np.abs(a - b)))


def frame_delta(img_a, img_b, box=None, size=64):
    """Coarse whole-frame change measure (64px downscale MAE) — freeze detection."""
    np = _np()
    from PIL import Image

    def small(arr):
        a = crop(arr, box) if box else arr
        im = Image.fromarray(a.astype("uint8")).resize((size, size), Image.BILINEAR)
        return np.asarray(im, dtype=np.float32)
    return float(np.mean(np.abs(small(img_a) - small(img_b))))


def template_mae(frame, template, box):
    """MAE between a window crop and a golden template image (shape-matched)."""
    np = _np()
    a = crop(frame, box)
    b = template
    if a.shape != b.shape:
        from PIL import Image
        b = np.asarray(Image.fromarray(b.astype("uint8")).resize(
            (a.shape[1], a.shape[0]), Image.BILINEAR), dtype=np.float32)
    return float(np.mean(np.abs(a - b)))


# =============================================================================
# shared navigation: fresh boot -> title -> New Game -> first dialogue
# =============================================================================
TITLE_WAIT_S = 13          # boot logos -> title screen
INTRO_CRAWL_S = 34         # New Game -> intro crawl -> first ADV scene


def boot_to_title(emu: Emulator, rom: Path, sav: Path | None = None):
    emu.launch(rom, sav=sav)
    log(f"waiting ~{TITLE_WAIT_S}s for the title screen …")
    time.sleep(TITLE_WAIT_S)


def menu_state(frame) -> str:
    """Classify the bottom screen: 'menu' (title main menu: three button bars),
    'title' (press-START prompt bar), or 'other'.

    Measured signatures (row means over x 40..220, window coords):
      * menu:  button-1 bar y 243..251 bright (~130+), no prompt bar at y 311..319
      * title: prompt bar y 311..319 very bright (~180+), y 243..251 dark (<110)
    """
    np = _np()
    band = frame[:, 40:220]
    b1 = float(np.mean(band[243:251]))
    prompt = float(np.mean(band[311:319]))
    if prompt > 170:
        return "title"
    if b1 > 118:
        return "menu"
    return "other"


def goto_main_menu(emu: Emulator, out: Path, presses: int = 10) -> bool:
    """From the title screen, press START (retrying — the first press after
    boot is often swallowed) until the main menu's button bars appear."""
    out.mkdir(parents=True, exist_ok=True)
    for i in range(presses):
        emu.key("START", hold_ms=250, pause=2.2)
        p = out / f"menu_try{i}.png"
        if not emu.shot(p):
            continue
        st = menu_state(load_gray(p))
        if st == "menu":
            log(f"main menu reached after {i + 1} START press(es)")
            return True
    log("main menu NOT reached (input not being accepted?)")
    return False


class InputEnvironmentError(RuntimeError):
    """The EMULATION ENVIRONMENT is not accepting game input (not a ROM bug)."""


def preflight_input(emu: Emulator, out: Path, presses: int = 6) -> bool:
    """Verify the environment can actually DRIVE the game: from the title
    screen, press START until the bottom screen flips from the title art to the
    main menu.  Returns True when the menu appears.

    WHY THIS EXISTS: every input layer can look healthy (X delivers key events,
    the emulator's key mapping fires, the emulated KEYINPUT register reflects
    the press) and the game can still sit on 'press START' if the emulated
    ARM7-side input service never comes up — an emulator/host-environment
    fault.  When that happens EVERY interactive test would fail identically
    while the ROM is fine, so interactive tests run this preflight and exit
    with code 3 (environment, not a verdict) instead of failing the build.
    The no-input render checks (test_boot_render.py) still gate the ROM."""
    for i in range(presses):
        emu.key("START", hold_ms=250, pause=2.2)
        p = out / f"preflight_{i}.png"
        emu.shot(p)
        if menu_state(load_gray(p)) == "menu":
            log(f"input preflight OK (main menu after {i + 1} press(es))")
            return True
    log("input preflight FAILED — the game never left the title under START presses")
    return False


def start_new_game(emu: Emulator, out: Path | None = None):
    """From the title screen: START (retried) -> main menu -> confirm the
    pre-highlighted はじめから (New Game) with A -> intro crawl."""
    scratch = out or (emu.workdir / "nav")
    if not goto_main_menu(emu, scratch):
        # legacy fallback: blind tap (old environments without the inject hook)
        emu.key("START", pause=2.5)
        emu.tap(*NEWGAME_BUTTON)
    else:
        emu.key("A", hold_ms=250, pause=2.0)
    log(f"New Game confirmed; waiting ~{INTRO_CRAWL_S}s intro crawl …")
    time.sleep(INTRO_CRAWL_S)


def grind_to_deploy(emu: Emulator, out: Path, max_steps: int = 120,
                    join_second_option: bool = True, tag: str = "grind"):
    """Advance the (long, scripted) first-stage intro with A presses until the
    deploy map (bottom-screen luma > 95).  On the ally-JOIN choice box (detected
    with test/golden/join_choice_template.png) picks option 2 when asked (the
    squad the combat tests use).  Returns the deploy frame path or None."""
    tmpl = load_gray(GOLDEN / "join_choice_template.png")
    box = (5, 168, 200, 210)
    joined = False
    for i in range(max_steps):
        emu.key("A", pause=0.45)
        p = out / f"{tag}_{i:03d}.png"
        emu.shot(p)
        g = load_gray(p)
        if not joined and template_mae(g, tmpl, box) < 11.0:
            time.sleep(0.6)
            if join_second_option:
                emu.key("DOWN", pause=0.5)
            emu.key("A", pause=0.6)
            joined = True
            log(f"JOIN choice answered at step {i}")
            continue
        if joined and mean_luma(g, BOTTOM_SCREEN) > 95.0:
            log(f"deploy map reached at step {i}")
            return p
    log("deploy map NOT reached within the step budget")
    return None
