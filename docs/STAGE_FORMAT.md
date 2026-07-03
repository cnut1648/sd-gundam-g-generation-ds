# STAGE_FORMAT — the `_STG*.bin` stage-script container

101 files, one per stage/session (`_STG00` … `_STG24B`, `_STGX*` extra stages, `_STGSP*`
special/Jupiter arc, `_STGTR1..3` tutorials, `_STGFB1..6` free battles, `_STG98/99`,
`_STGTL/_STGTU`). They hold each stage's event script: dialogue, cutscene direction, choices,
and combat scripting. This is the format, the load model, and the growth/relocation rules the
translation depends on.

---

## 1. Load model

* A stage file is read **whole and verbatim** into the fixed RAM buffer **`0x0232C800`**
  (verified by live RAM dump: file bytes == RAM bytes for the full length).
* The buffer is `[0x0232C800, 0x02340000)` = **`0x13800` = 79,872 B** — the hard per-file
  cap. Build cap in practice: `0x13400` = 78,848 B (1 KB margin). The largest shipped
  translated stage sits ~13 bytes under that cap, so the margin is *not* theoretical.
* The stage descriptor table (arm9 `0x175560`, 101 records × 0x34) gives every stage the
  same buffer base — exactly one stage is resident at a time.
* arm9 holds **no pointer to any interior stage-file offset** — only the base. All
  block-level addressing lives *inside* the file. So growing a file needs no arm9 change
  (the FAT resize is automatic on container save).

## 2. Container layout

```
0x0000  u32   scene/event count
0x0004  ptr   ┐
0x0008  ptr   │  header section pointers — ABSOLUTE RAM addresses
0x000C  ptr   │  (compiled for base 0x0232C800)
0x0010  ptr   │
0x0014  ptr   │
0x0018  ptr   ┘
0x001C  ...   per-scene/event setup table (speaker-id records etc.)
  ...
        ...   bytecode script (dialogue blocks + event opcodes), data tables, name table
```

* **All pointers in the file are absolute** (`0x0232Cxxx`–`0x0233xxxx`), scattered at
  *unaligned* positions through the script (opcode operands, record arrays, header).
  A typical stage has 2,000–3,000 of them.
* **header[0x08] is the name table**: entries of `{u32 nchars, u32 string_ptr}` (8 B each);
  the engine loads `string_ptr` with a 32-bit `ldr`.
* **Alignment sensitivity**: header slots **`0x04, 0x08, 0x10, 0x14, 0x18`** point at tables
  the engine reads with 32-bit `ldr`s; the original aligns all five to 4 bytes in 101/101
  files. Slot `0x0C` (the dialogue/event section) is byte-accessed and exempt. ARMv5 does
  not fault an unaligned `ldr` — it **rotates** the aligned word, yielding a wild value; a
  +2-byte net growth ahead of the name table produced exactly that crash (black screen
  loading one stage from a New-Game+ save). **Alignment of those five tables is
  load-bearing**; see §5.

## 3. Script stream

The script is a stream of opcodes; the ones that matter to translation:

| opcode | form | meaning |
|---|---|---|
| `0x06` | `06 <u16 speaker_id>` | set speaker (char-DB record → nameplate) |
| `0x13` | `13 <u32 abs_ptr>` | CALL/jump-to-subroutine — 4-byte ABSOLUTE operand |
| `0x16` | `16 <u32 abs_ptr>` | pointer operand (setup/resource) |
| `0x15` | `15 [seg] 00 [seg] 00 00` | **display dialogue block** (see TEXT_SYSTEM §4) |
| `0x02` | prefix of record-array elements | pointer-array entries |
| `0x0C..0x0D` | wraps 『option』 segments | **choice block** (player decision) |

* **Reachability**: the set of display blocks the player can see = the CFG walk from the
  scene-entry pointers, following in-range GOTO/CALL/CGOTO to a fixpoint. This — not a
  linear `0x15` scan — is the only correct block enumeration (a `0x15` byte occurs inside
  CALL operands whose targets are `0x0233_15_xx`; treating those as blocks corrupts event
  code — the single most damaging bug family of the project).
* The static CFG over-approximates slightly (jumps into data tables/padding produce a few
  phantom "reachable" blocks); filter by pairing against the JP original.
* **Choice blocks and combat scripts must not be rewritten structurally.** Choices are
  `0x0C..0x0D`-wrapped with ≥2 option segments (exactly 8 such blocks game-wide; each option
  gets its own line — a hard-break in the encoder). Single-segment 『…』 text is just quoted
  dialogue and is growable (an early over-broad "protect everything quoted" rule silently
  kept ~130 lines stuck on stale compact text). Combat-script/control blocks (non-display
  opcodes, `0x01`-headed layout-locked banners) are kept byte-identical or size-locked.

## 4. Growth + pointer relocation

Growing a dialogue block shifts everything after it, so every absolute pointer whose target
is at/after the insertion point must be bumped by the delta. The proven algorithm:

1. **Find genuine pointers**: scan every byte offset `o ≥ 1` for a 4-byte LE word `v` with
   `base ≤ v < base+len` (base = `0x0232C800`) **and preceding byte `< 0xE0`** (a genuine
   pointer is never preceded by a 2-byte glyph/F-ref token's high byte — in-range windows
   preceded by `≥ 0xE0` are coincidental overlaps with token tails).
2. **Priority classes** by the preceding byte: `0x13/0x16` opcode operand = priority 0;
   `0x02` record-array element = priority 1; anything else = priority 2.
3. **Exclusions**:
   * positions inside a block being *replaced* by the growth (its old bytes vanish);
   * priority-2 windows whose bytes lie inside a **dialogue payload** (`payload_mask` of all
     token-aware `15…00 00` interiors) — those are coincidental byte patterns inside glyph
     text, never real pointers (genuine pri-2 pointers live only in the header/setup region,
     which is outside every payload).
4. Select **non-overlapping** candidates in priority order (a coincidental sub-window must
   not shadow a real pointer in dense pointer arrays), then apply all grows and bump every
   selected pointer whose target ≥ each insertion point.

Additional rules:
* **Buffer cap** per §1; grows that would exceed it are skipped (choose shorter wording).
* **Alignment repair** (the *alignment-insert technique*): after growth, for every header
  table (slots 0x4/0x8/0x10/0x14/0x18) at offset `t` with `t & 3 ≠ 0`, insert `(-t) & 3`
  zero bytes AT the table and relocate pointers ≥ `t` — greedy ascending, recomputing as
  earlier inserts shift later tables. The inserted bytes are dead space; every dialogue
  block re-decodes identically.
* **Verification obligations** (all byte-level, off the final image): every non-grown block
  decodes byte-identically at its shifted offset; every grown block decodes to its intended
  text; every in-range pointer resolves in-range; all five header tables 4-aligned; choice
  blocks/combat scripts byte-identical; file ≤ cap.

## 5. Failure modes this design guards against (quick index)

| symptom | cause | rule |
|---|---|---|
| stage black-screens at load | pointers not relocated after a size change | §4 relocation |
| stage black-screens at load, all pointers valid | header table misaligned → rotated `ldr` | §2/§4 alignment |
| mid-stage hang at a cutscene, no wild PC | event CALL corrupted by a false `0x15` block | §3 CFG reachability + JP CFG-isomorphism |
| ending cutscene black screen | arm9 jump-table pointer overwritten by text | script-pointer integrity (`13 <ptr>` valid in JP stays valid) |
| a block loses its later segments | 0x00 padding in a non-final segment | TEXT_SYSTEM §4 padding |
| choices merge onto one line / choice breaks | choice block rewritten | §3 choice protection |
| dialogue shows stray "…" or JP page layout | segment-preserving re-encode instead of reflow | TEXT_SYSTEM §4 reflow |

## 6. What "translated" means for a stage file

Per stage, the translation data (see `data/dialogue/stages/*.json`) carries each reachable
display block keyed by its JP identity (offset in the JP file + JP text), the authored ZH,
and the encoded payload. The builder re-encodes, grows, relocates, aligns, and verifies per
§4. Blocks intentionally left JP (credits, tutorial headers, onomatopoeia, non-reachable
template text) are explicit entries in an allowlist, not silent gaps.

A note on non-reachable text: the stage files contain a large shared "thanks-for-playing /
extra mode" template block appended to many files plus dictionary-compressed blocks that the
audited CFG model never reaches in stage play. These ship untranslated by decision — the
gate that counts untranslated text is reachability-based.
