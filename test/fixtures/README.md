# test/fixtures — saves and inputs for the live tests

## Cartridge saves (`.sav`, 256 KiB raw EEPROM/flash images)

melonDS picks up a save automatically when it sits next to the ROM as
`<rom>.sav`; the live harness copies a fixture there when a test passes
`--sav` (see `test/live/harness.py`, `Emulator.launch(sav=…)`).  From the
title menu, choose Continue (つづきから) instead of New Game to load it.

| file | contents |
|---|---|
| `session00.sav` | An early-game save at **Session 00, the first sortie** (the Zanzibar pursuit stage), player warship with the mobile suits already deployed. The fastest way into a REAL combat stage: Continue → session intro → barks / combat dialogue surfaces within a couple of minutes. |
| `newgame_plus.sav` | The deep-progress **New-Game+ (二周目) save, session X1 cleared** (in-game clock ≈ 4:55), parked at the back-stage strategy map with a developed roster. Reaches the late-game surfaces the intro cannot: back-stage menus (組織/編成/一覧), the stage-select advance flow (作戦 → 進撃), extra-session stage loads, developed units/pilots with ID abilities. |

Both saves were made on (and are compatible with) the shipped translated ROM;
they also load on the Japanese source ROM (the save format is the game's own).

## Why there are no melonDS savestates (`.ml*`) here

A melonDS **savestate restores the emulated RAM — including the code image and
the loaded stage data — from the machine that made it**.  Loading a savestate
made on build A while testing build B silently replaces B's code/text with A's:
every "verification" after that tests the WRONG bytes.  This masked real bugs
more than once historically.  The rule: live tests boot FRESH (DirectBoot) and
use cartridge saves for progress; savestates are only created and consumed
inside a single emulator session (scratch slots), never shipped.

## Input scripts

The live tests synthesize input at runtime (`harness.Emulator.key/tap`) rather
than replaying fixed scripts, because scene timing varies slightly between
boots; each test documents its navigation inline.
