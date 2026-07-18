# TASK: a PROVABLY-COMPLETE stage reachability algorithm (handle dynamic dispatch)

You are one of several independent agents attacking the SAME problem. Work it end
to end yourself; we conclude only when independent agents AGREE on the mechanism
and the algorithm provably reaches every real display block. Be rigorous, not fast.

## The problem
`data/jp/stages/<stage>.json` lists each stage's display blocks. They come from
`utils/extract/walkers.py::stage_block_order(d)` — a STATIC control-flow walk of
the event-script VM. It is **incomplete**: it reaches only 22754 of 26242
pixel-verified real display lines (86.71%); it misses **3488 real, on-screen
dialogue lines** (endings, late/dynamically-dispatched scenes, whole stages like
`_STG98`). Example misses: "フリーダムガンダム……キラ·ヤマト オマエは俺が倒す……！！",
"師匠……！！", "これまでの戦い、ご苦労". We must find the COMPLETE control-flow model
so every reachable display block is enumerated — and PROVE it.

## Ground truth / the completeness oracle (this is your objective test)
`data/zh` (v1.1) was authored from IN-GAME PIXELS, so every dialogue offset it
edited is a provably-real display block, immune to decoder/CFG bugs. Your
algorithm is complete iff it reaches ALL of them. Use the harness:
```python
import sys; sys.path.insert(0, "audit/translate2")
from reach_oracle import must_reach, check
mr = must_reach()                       # {stage_fname: set(int offsets)} you must cover
check({fname: your_reached_offsets})    # prints coverage %, per-stage misses + JP text
```
Target: **26242/26242 (100%)**. If any offset is genuinely unreachable, you must
PROVE it (show it is not the target of any executed control-transfer / dispatch),
not just skip it.

## What is known (verified — start here, don't re-derive)
* Stage file loads verbatim to RAM base **`0x0232C800`** (`L.STG_BASE`); all
  in-file pointers are ABSOLUTE `0x0232Cxxx..0x0233xxxx`.
* Header: `0x00`=u32 scene/event **count**; `0x04,0x08,0x0C,0x10,0x14,0x18`=six
  ABSOLUTE section pointers (0x08=name table, 0x0C=dialogue/event section, …);
  `0x1C`=per-scene/event setup table (speaker records). See `docs/STAGE_FORMAT.md`.
* Opcodes partially known (`utils/extract/layout.py` `STG_OPSZ`, `STG_JUMP_OPS`):
  `0x15`=display block (`15 [seg] 00 … 00 00`), `0x01`=RET, `0x02`=GOTO(u32),
  `0x13`=CALL(u32, 6B), `0x16`=CGOTO/ptr(u32), `0x06`=set-speaker(u16). The walk
  ASSUMES every opcode `>0x19` is a 1-byte no-op — **this is a guess and is
  probably where it desyncs.**
* arm9 code (disassemble it — see below): main event dispatcher **`0x0209EBAC`**;
  a SEPARATE cutscene/ending jump-table VM at ctx **`0x0227CD0C`** ("mid-stage
  demos and endings are event subroutine chains"). Endings are reached through
  this second VM / arm9 jump tables (`docs/GAME_NOTES.md` §4, `docs/ROM_STRUCTURE.md`).

## Concrete leads (verified on `_STG00.bin`)
* `count@0x00 = 14`, but the current walk uses only the **6 header section
  pointers** as entry points — it likely misses real scene/event entries governed
  by the count and/or the setup table at `0x1C`.
* **357 literal u32 pointers into the ending region `[0x8000,end)` exist in the
  file** — endings ARE addressable in-file; the walk simply never traverses to the
  code that holds those pointers. Find why (desync? wrong entries? an opcode that
  transfers control but is treated as a 1-byte no-op?).

## Tools
* **capstone 5.0.7 is installed.** Recipe to read the VM:
  ```python
  import sys; sys.path.insert(0,".")
  from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
  from utils.extract.gamerom import GameROM
  rom = GameROM("0098 - SD Gundam G Generation DS (Japan).nds")
  a = rom.arm9                                  # arm9 binary; file offset == addr - 0x02000000
  md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)           # try CS_MODE_ARM too; DS arm9 is mostly THUMB
  for insn in md.disasm(a[0x9EBAC:0x9EBAC+0x400], 0x0209EBAC):
      print(f"{insn.address:08x}  {insn.mnemonic} {insn.op_str}")
  ```
  Disassemble the dispatcher `0x0209EBAC` and the ending VM `0x0227CD0C`; recover
  the opcode jump table (the switch), each opcode's operand size, and every
  control-transfer / dispatch (including table-indexed / computed targets).
* `objdump` is also available. `utils/extract/gamerom.py` gives `rom.arm9`,
  `rom.file(name)`, `u16`, `u32`. `utils/extract/walkers.py` has the current walk.

## Deliverables — write to `audit/translate2/staging/vm_reach/agent_<YOURID>/`
1. `findings.md` — the VM model you proved: full opcode table (op → size, control
   effect), how entry points are enumerated (count/setup/section tables), and the
   EXACT dynamic-dispatch mechanism that reaches endings (cite disassembly
   addresses + bytes). State precisely why it is complete.
2. `reach.py` — a self-contained function `reached(fname) -> set[int]` (block-start
   offsets) implementing your model, importable and runnable. It must load the JP
   ROM itself.
3. `coverage.json` — `{"total":N,"hit":M,"pct":..,"per_stage_missed":{...}}` from
   running your `reached` through `reach_oracle.check`. Include your explanation of
   any residual miss and its proof of genuine unreachability.

Do NOT edit repo files outside your `agent_<ID>/` dir. Do NOT run builds. Ground
every claim in the disassembly or the oracle — no heuristics presented as proof.
