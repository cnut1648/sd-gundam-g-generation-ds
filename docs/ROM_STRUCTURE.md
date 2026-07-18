# ROM_STRUCTURE — the NDS container and the arm9 memory map

This document describes the physical layout of *SD Gundam G Generation DS* (Bandai 2005,
gamecode `ASGJ`, 32 MB cart) and the memory map the translated build relies on. Everything
here was verified against the shipped translated ROM and the Japanese original. File offsets
into the arm9 image convert to RAM addresses as `RAM = 0x02000000 + file_offset` (arm9 has no
overlays and is not compressed).

---

## 1. NDS container essentials

* **Header (0x0..0x200).** Standard NDS header. Fields that matter to a rebuild:
  * `0x2C` arm9 size — auto-set by `ndspy` on save to `len(rom.arm9)`.
  * **`0x60..0x64` = `57 66 41 00`** — the gamecard ROM-control (ROMCTRL) parameter. This is
    the **audio-critical header field**: builds that clobbered it shipped with broken/garbled
    audio (the SDAT streaming reads depend on the bus timing it encodes). Every build must
    assert these four bytes verbatim.
  * `0x6C` secure-area CRC — stale by design. The dump stores the secure area (ROM
    0x4000–0x8000) **decrypted** and flagged "already decrypted" (first 8 bytes
    `E7 FF DE FF E7 FF DE FF`), so the stored encrypted-form CRC never matches and the loader
    ignores it. Editing ModuleParams (which lives inside the secure area, see §3) is safe and
    needs no CRC recompute. `ndspy` does not recompute it.
  * `0x15E` header CRC16 — recomputed by `ndspy` automatically.
* **FAT / FNT.** 3,254 files, all flat in `data/`, named in hex (`0.bin`…`fff.bin`) plus 101
  `_STG*.bin` stage scripts and one `sound_data.sdat`. **No standard Nitro formats**
  (no NARC/NCLR/NFTR/BMG) — everything except the SDAT is a custom Bandai container.
  Replacing files **by FAT index** and calling `ndspy` `rom.save()` round-trips the container
  byte-identically (proven: JP ROM + replaced arm9 + replaced files reproduces the shipped
  ROM bit-for-bit).
* **arm9** — 1,797,560 B in the original, 2,124,004 B translated (appended autoload payloads,
  see §3). Loads at `0x02000000`. **No overlays** (`y9`/overlay table empty of code that
  matters — the game never uses overlays).
* **arm9 footer ("nitrocode")** — 12 bytes stored after the image:
  `21 06 c0 de | 0c 0b 00 00 | 00 00 00 00`. Word1 = `0xB0C` = the arm9-relative address of
  ModuleParams, which never moves, so the footer is preserved verbatim. `ndspy` exposes it as
  `rom.arm9PostData` (`rom.arm9` itself EXCLUDES the footer).
* **arm7, banner, overlays, header params, every other file** — byte-identical to the
  Japanese original in the shipped translation.

### Text-bearing NitroFS files (the complete changed set)

101 `_STG*.bin` stage scripts (grown; see `STAGE_FORMAT.md`) plus 20 misc files:

| file | JP size | role |
|---|---|---|
| `0.bin` | 219,828 | battle-voice barks, container 1 of 5 (in-place edits) |
| `1.bin` | 4,724 | battle-voice barks |
| `1dd.bin` | 804 | battle-voice barks |
| `1de.bin` | 33,984 | battle-voice barks |
| `c4f.bin` | 1,448 | battle-voice barks |
| `1da.bin` | 4,044 → **4,112** | ID-**ability** effect cards (grown +68 B) |
| `1db.bin` | 9,220 | ID-**command** effect labels (in-place; offset table in arm9) |
| `1dc.bin` | 23,944 → **28,452** | combat **cut-in famous lines** (名台詞; grown +4,508 B, arm9 offset table rewritten) |
| `1df.bin` | 3,344 | special-**ability** name records (RAM-resident from boot @ `0x0235992C`) |
| `1e0.bin` | 1,088 | special-**defense** descriptions (RAM base `0x023594EC`) |
| `31e.bin` | 2,432 | weapon-name encyclopedia copy (⚠ NOT read in battle — battle reads arm9 pools) |
| `324.bin` | 58,272 | character/unit encyclopedia (図鑑) biography text |
| `c4b.bin` | 42,252 | encyclopedia (図鑑) text sibling of `324.bin` (dict-compressed JP → all-atlas ZH re-encode) |
| `388.bin` | 832 | captain-badge BG tiles (format id 0x000A, 4bpp raw tiles — graphics, not text) |
| `3d3.bin` / `3d5.bin` | 3,068 / 1,588 | BackStage hub tab labels (作战/编成/MS开发/系统) — raw BG tiles, repainted |
| `478.bin` | 3,312 | in-combat force-HUD faction table (战舰/自军/友军/敌军) — raw 4bpp BG tiles (file id 949; tile block @ 0x610) |
| `48a.bin` | 3,312 | terrain/movement badge OBJ tiles (回避/通/宇/飞/地/水) — raw tiles, `offset = tile*32 + 784` |
| `b6e.bin` | 416 | parts **names** (40 entries: 30 real + 10 予備 spares; arm9 offset table, see map) |
| `b6f.bin` | 1,936 | parts **captions/descriptions** (own arm9 offset table) |

Everything not listed above (3,133 files incl. `sound_data.sdat`) is byte-identical to JP.

---

## 2. arm9 RAM map (translated build)

```
0x02000000  arm9 static image (code + data pools), file offset == RAM - 0x02000000
   ...      translated string pools, caves, patched literals live in here ("resident")
0x021B6860  AutoloadStart == StaticBssStart: autoload source block begins here in the file;
            at runtime this region becomes BSS
0x021B6DB8  end of the ORIGINAL JP image (JP autoload list sat at file 0x1B6DA0..0x1B6DB8)
─ appended payloads (translated build only; contiguous in file AND in RAM) ─
0x023027A0  glyph atlas       (autoload #3)  0x25F80 B = 4,320 slots × 36 B   file 0x1B6DA0
0x02328720  relocated pool A  (autoload #4)  0x2028C B                        file 0x1DCD20
0x023E7000  relocated pool B  (autoload #5)  0x098FC B                        file 0x1FCFAC
            new 5-entry autoload list @ file 0x2068A8 (RAM 0x022068A8), 0x3C B;
            arm9 image ends at file 0x2068E4
─ fixed runtime regions (same in JP and translated) ─
[0x021B6860, 0x023027A0)  crt0 BSS-clear range (StaticBssStart..StaticBssEnd)
 0x0232C800               stage (_STG) load buffer base, size 0x13800 → ends 0x02340000
[0x0232C800, ~0x0233F4xx) runtime work-buffer + its growth/overflow zone (stage-dependent!)
[0x02340000, 0x023489AC)  upper work buffer
 0x02348A00               arena-lo (heap base) in the translated build (JP: 0x023027A0)
 0x023C0000               arena-hi (heap top; unchanged)
[0x023E7000, 0x023F08FC)  relocated pool B — ABOVE arena-hi ⇒ never heap-touched (always safe)
 0x027C0000               DTCM (renderer contexts/scratch live here — invisible to main-RAM dumps)
 0x01FF8000               ITCM
0xFFFF0104 / 0xFFFF0108   BIOS unhandled data-abort spin (every hard "black screen" freeze
                          parks the PC here — the universal crash signature)
```

Key derived facts:

* **File↔RAM delta for the appended block:** file `[0x1B6DA0, 0x1FCFAC)` maps to RAM
  `[0x023027A0, 0x023489AC)` with a single delta **`+0x0214BA00`** (atlas + pool A are
  contiguous in both spaces). Pool B's delta is `+0x021EA054`.
* **Why the atlas sits at exactly `0x023027A0`:** that address is `StaticBssEnd` — the
  *exclusive* upper bound of the crt0 BSS clear — and was the original arena-lo. Anything
  appended *below* it is zero-filled at boot; anything at/above it and below the (bumped)
  arena-lo is safe from both the BSS clear and the heap. Early in the project a font appended
  right after the image (~`0x021B6DC4`) was silently zeroed by the BSS clear and then
  heap-allocated over — the relocation only works at the BSS boundary with arena-lo bumped.
* **Pool A (`0x02328720`) is only partially durable.** Its RAM range overlaps the stage
  buffer and work-buffer zone: only `[0x02328720, 0x0232C800)` (16,608 B) survives all
  gameplay. The rest of that band gets overwritten by the stage loader / growing combat heap
  — the source of a whole class of "renders garbage only deep into the game" bugs (see
  `LESSONS_LEARNED.md`, memory section). Strings that must survive every stage live either in
  the resident image (< `0x021B6DB8`) or in pool B (`0x023E7000`, above arena-hi).
* The **work-buffer growth is roster/progress-dependent**: nominal top `0x02337040`,
  observed overflow to `0x02337CC4` on early saves and past `0x0233F400` by mid-game saves.
  No fixed "safe dead band" exists inside `[0x0232C800, 0x02340000)`.

---

## 3. ModuleParams and the autoload mechanism

The crt0 (`0x02000888`) calls the autoload copier at **file `0x9C4` / RAM `0x020009C4`**
*before* the BSS clear. Disassembled behaviour (the #1 rebuild risk — get this exactly right):

```
r0 = ModuleParams (@0x02000B0C)
r1 = AutoloadListStart (MP+0x00)     r2 = AutoloadListEnd (MP+0x04)
r3 = AutoloadStart     (MP+0x08)     ; ONE continuous SOURCE cursor
for each 12-byte list entry {ramAddr, size, bssSize}:
    copy `size` bytes word-wise from r3 to ramAddr   ; r3 NEVER resets between entries
    zero-fill `bssSize` bytes after the destination  ; consumes NO source
```

* The list is walked **forward**; the source is the per-entry payloads **concatenated in list
  order**. Any main-RAM destination works (no filtering).
* **ModuleParams @ file `0xB0C`**: `+0x00` AutoloadListStart, `+0x04` AutoloadListEnd,
  `+0x08` AutoloadStart (`0x021B6860`, unchanged), `+0x0C` StaticBssStart (`0x021B6860`),
  `+0x10` StaticBssEnd (`0x023027A0`). Only the first two words are patched.
* **JP list** (file `0x1B6DA0`): 2 entries — ITCM `{0x01FF8000, 0x520, 0}`,
  DTCM `{0x027C0000, 0x020, 0}`.
* **Translated list** (file `0x2068A8`): 5 entries — ITCM, DTCM, then
  `{0x023027A0, 0x25F80, 0}` (glyph atlas), `{0x02328720, 0x2028C, 0}` (pool A),
  `{0x023E7000, 0x98FC, 0}` (pool B). Adjacency must be exact: the payloads are inserted at
  file `0x1B6DA0` (displacing the old list), and
  `0x520 + 0x20 + ΣpayloadSizes == newListFileOff − 0x1B6860` or the source cursor lands in
  the wrong place. Payload sizes must be multiples of 4 (the copy loop is word-wise).
* ModuleParams sits inside the secure area — safe to edit in place (§1).

---

## 4. ADDRESS MAP (consolidated)

All addresses verified against the final shipped ROM. "file" = arm9 image offset;
RAM = `0x02000000 + file` unless stated. JP→ZH columns show patched literals.

### 4.1 Boot / layout / heap

| what | file | RAM / value |
|---|---|---|
| autoload copier routine | `0x9C4` | `0x020009C4` (called from crt0 `0x02000888`) |
| ModuleParams | `0xB0C` | ListStart JP `0x021B6DA0` → ZH `0x022068A8`; ListEnd JP `0x021B6DB8` → ZH `0x022068E4`; AutoloadStart/BssStart `0x021B6860`; BssEnd `0x023027A0` (all others unchanged) |
| BSS clear range | — | `[0x021B6860, 0x023027A0)` — nothing translated may live here |
| arena-lo literal | `0xA48F8` | JP `0x023027A0` → ZH **`0x02348A00`** (heap base, bumped above the payloads) |
| arena-hi literal | `0xA496C` | `0x023C0000` (unchanged; do not touch) |
| glyph-atlas autoload payload | `0x1B6DA0` | → RAM `0x023027A0`, `0x25F80` B (4,320 × 36) |
| pool A autoload payload | `0x1DCD20` | → RAM `0x02328720`, `0x2028C` B |
| pool B autoload payload | `0x1FCFAC` | → RAM `0x023E7000`, `0x98FC` B |
| new autoload list | `0x2068A8` | 5 × 12 B; arm9 image ends `0x2068E4` |

### 4.2 Text decode / render engine

| what | file | RAM / notes |
|---|---|---|
| string decoder loop | `0x132E0` | `0x020132E0` — per byte until `00 00` |
| per-char dispatcher | `0x1327C` | `0x0201327C` — `≥0xF000` recurse dict; `0xE000..0xEFFF` slot = code−0xDF20; builds slot array at ctx+0x1A |
| drawer | `0x13220` | `0x02013220` — ctx+0x64 bit0=1 → renderA, 0 → renderB |
| renderA 12×12 rasterizer | `0x13108` | `0x02013108` — reads `atlas + slot*36`; **NO bounds check** (OOB slot ⇒ sparkle) |
| renderB 8×16 rasterizer | `0x13160` | `0x02013160` — calls the dispatch trampoline |
| render-dispatch trampoline ("the cave") | `0x11A2A0` | `0x0211A2A0` — `if slot ≥ 0x894 (2196): renderA(slot) else renderB_font + slot*32`. Routes every high slot to the 12×12 atlas on renderB-path screens |
| **renderer atlas pointer** | **`0x1315C`** | JP `0x0211A2A0` → ZH **`0x023027A0`** (THE relocation literal) |
| renderB font-base literal | `0x1321C` | `0x02133F14` — must stay the in-image font (a historic relocation to `0x02326E00` garbled all JP UI kanji) |
| decoder-bypass branch | `0x1322C` | must be `11 d1` (`bne`); NOP `c0 46` forces all UI text down the raw path ⇒ global garble |
| in-image JP atlas (dead after relocation) | `0x11A2A0..0x12D770` | 2,196 slots × 36 B; reusable cave space in the translated build |
| alt (name) dictionary | `0x12D770..0x133F14` | `0x0212D770` — canonical pilot/unit/name macro store (e.g. `F0BC`=アムロ); ジオン entry 12 @ `0x212F777`, 連邦 entry 30; ザンジバル entry 1832 @ file `0x13162D` |
| renderB 8×16 UI font | `0x133F14` | `0x02133F14`, 32 B/glyph |
| primary dictionary | `0x1444B4..0x14AC34` | `0x021444B4` — dialogue/data macro store. **Clobbering it freezes combat** — treat as read-only |
| dictionary selector | `0x16B868` | `[0x0216B868]=0x021444B4` (primary), `[+4]=0x0212D770` (alt) |
| renderB label arena | `0x14AC34..0x155B14` | stat/UI label strings (editable band) |
| OBJ-text path (engine A) | — | `0x0202BC74 → 0x02013C00 → 0x02013220 → 0x02013704` → OBJ VRAM `0x06400000` |
| engine-B generic text helper | `0x12EFC` | `0x02012EFC(ctx,Xpx,Ypx,str,[sp]=count)`; init veneer `0x02012F75 → 0x02013D64(ctx,mapbase)` |
| engine-B char-tile copy helper | `0x12C40` | `0x02012C40` — advances tile cursor by `r7=(penPx+7)>>3` @ `0x12C4A`; tilemap last-writer `strh` @ `0x02012CCC`. The r7-over-advance is the ghost/aliasing bug family |
| engine-A copy helper / twin | `0x13590` / `0x136A8` | weapon-name top-screen path; char base `0x06000000`, map `0x0601F000` |
| glyph plot rasterizer | `0x12FE4` | `0x02012FE4` (trampoline point `0x12FE6`); DTCM 2-row tile ctx `0x027C29D0`, stride8=13, row1[col0] aliases row0[col13] |
| scratch buffer for text compose | — | `0x02022854` (0x800 B); memcpy `0x0200D834`, memset `0x0200D85C` |
| engine-B BG2 (info panels) | — | char base `0x06200000`, map base `0x0620F000`; BackStage list map base `0x06009800`; fixed ID-page cells `0x0620F1B0/B2` |
| panel compose scratches | — | 1db → `0x027C37D4`; 1da → `0x027C36F4`; defense compose `0x027C37F0`; per-panel scratch `0x0227D5A0` (dispatch `0x0209EBAC`) |

### 4.3 Name / label / command data tables

| what | file | RAM / layout |
|---|---|---|
| **character DB** | `0xDCF18` | 563 records × 0x48; `+0x04` = name pointer (speaker nameplates, rosters). Script call `06 <u16 speaker_id>` selects the record |
| **unit master table** | `0xB94BC` | `0x020B94BC`, stride 0xD8, indexed by unit-type id: `+0x00` unit-name ptr; `+0x2C + slot*0x1C` = weapon[0..5] name ptr. Weaponless records (utids ~610–944) are pilot/faction identity labels and render renderA-DIRECT (must encode ≥2196-only). `master[679+n]+0x00` aliases `charDB[r]+0x04` (0xD8 = 3×0x48) |
| **ID-command table** | `0xEC994` | `0x020EC994`, stride 0x24, ~1,410 live records: `+0x00` name (battle-quote) ptr; `+0x08` summary ptr; `+0x0E` target enum (01=仅自身/03=敌队/09=全军); `+0x22` detail index (didx); `+0x23` condition bits (bit 0x02 → 地图, else 战斗中) |
| ID-command name accessor | — | `0x0200F2D0(obj, cmdID)` |
| ID-command detail offset table | `0xF9048` | 256 × u32; string = `0x020F9048 + offtab[didx]`; 3 NUL-separated segments; render accessor `0x02024CF4`; original JP effect pool `[0xF9449, 0xFC643)` |
| effect coefficient table ("efftab") | `0xEBC25` | stride 0x14 — the **source of truth** for effect text (D1/2=HP/SP回复, D3/4/6=命中率/回避/攻击 %, D5=反应 flat, D7=装甲 flat, D≥12 specials) |
| procedural stat numbers | — | coeff table RAM `0x02289F9C` (s16), digit/number formatter `0x0203E8F0`, symbol glyphs `0x0214BB50+`, stat mini-labels `0x14B1C5+` |
| info/ID panel builder | — | `0x0203F610`; stat-label pointer table `0x3FC30..0x3FC40`; info-panel pool `0x3FF30` |
| value-3-ink LUT patch | `0x14510` | `strb r2→r1` — un-hides colour-index-3 ink on the stat panel (needed for 射击/反应/指挥) |
| ID-ability getter | — | `0x02098DC8`; character table has **501 entries** — out-of-table charIDs must clamp to 0 (无ID能力); bounds-check cave @ `0x18F600` |
| pilot name arena | `0x18E47E..0x18F47E` | the ONLY store the affinity/nameplate readers accept (they reject high-RAM pointers) |
| resident cave runs (proven zero in JP) | — | `[0x18F615, 0x18F821)` ID-ability names; `[0x190030, 0x190870)` + `[0x1945B3, 0x194852)` relocated summaries/details (**⚠ ≥ 0x02190000 — summaries/details/weapon names ONLY, never unit/pilot names**, see below); pool regions `(0x18BBE2, 0x18BDB4)`, `[0x18BF7A, 0x18CB5C)`; relocated-name band `[0x02180000, 0x021A0000)` |
| **name-pointer band limit** | — | **Unit-name (`master 0xB94BC +0x00`) and pilot/character-name (`char-DB 0xDCF18 +0x04`) pointers MUST resolve `< 0x02190000`.** The 出击/deploy unit-name path HARD-FREEZES (data abort) and the affinity/nameplate reader renders BLANK on a name pointer `≥ 0x02190000` — i.e. the `0x0219..` resident sub-band and the autoload pools (`0x0232..` pool A, `0x023E..` pool B). Proven: 816 name ptrs `< 0x02190000` render fine (52 still at the JP pool `0x020B..`, 763 relocated to `0x0218..`); every ptr at `0x0219..` froze/blanked (the v1.1 卡碧尼Mk2 deploy-freeze). Effect summaries/details and weapon names use lenient accessors and MAY exceed `0x02190000`. So a unit/pilot name that needs relocating goes into a proven-safe zero run **below `0x190000`** (e.g. `[0x18870C, 0x18BBE2)` dead ID-table gaps — where 446 shipped caves already live — the pilot arena, or the `0x18B..` pools), never the `0x190030/0x1945B3` caves or pool A/B. Pre-existing JP dummy (欠番) records carry out-of-RAM junk (`≥ 0x02400000`) that is never dereferenced. Gate: `name_pointer_band` (`test/run_static.py`). |
| dead SJIS dev strings | `0x1B3E22..0x1B6DA0` | debug text up to the payload boundary — the classic code-cave donor (`0x1B3670`, `0x1B3E7C`, `0x1B3FC4`, `0x1B3E40`…). **⚠ NOT uniformly dead** (LESSONS C6): the OBJ-text **number-format strings** `"D4"` @`0x1B3E90` and `"/D4"` @`0x1B3E94` are LIVE — referenced from literal pools `0x23640/0x23648/0x240A8/0x240B0` and passed to the OBJ-text drawer `0x02013BE0` by the battle focus-plate composers (`0x02023544/0x02023582/0x02024022/0x02024056`, HP cur @x=176 / `/max` @x=208); paving them blanks every focus-plate HP readout. Other strings' printf-pool refs (`0x22EA0..0x24470`) feed only the compiled-out debug printf `0x020A3ECC` (dead; recorded in `code_patches.json` `_paved_ref_allowlist`). Gate `patch_literal_safety` audits every cave placement against the whole-image reference scan |
| parts NAME offset table | `0x16B474` | count `0x2A` @ `0x16B470`; offtab[0..39] `0x16B474..0x16B510`; sentinel [40]=`0x1A0` @ `0x16B514`; accessor `0x0200F90C` = `base[(idx+1)]`; `b6e.bin` loads at RAM `0x02377C38`, `ptr = 0x02377C38 + offtab[idx]` |
| parts CAPTION offset table | `0x16B518` | same `[count][offsets]` shape; accessor `0x0200F900` (b6f descriptions) |
| cut-in (1dc) offset table | `0x16EEA8` | 943 × u32 record offsets; sentinel @ `0x16FD60`; 1dc byte-size ref @ `0x16C444` (= `0x6F24` in the shipped ROM). Growing 1dc REQUIRES rewriting all three |
| 1db (ID-command labels) offset table | RAM `0x0217716C` | 257 × u32 (`table[16]=0x190` → 持続 record) |
| 1da (ID-ability labels) offset table | RAM `0x021775C8` | 129 × u32 |
| 1df offset tables | RAM `0x02176DB8` / `0x021781A4` | special-ability records; 1df RAM-resident base `0x0235992C`; drawer `0x02055AB4` (2 lines @ tile rows 0x14/0x16, tile_x 3, map `0x0620F000`); one arm9 list ref `0x178278`. Line fetch = byte-wise scan to the k-th `00 03` stop with NO record-end bound (LESSONS D8: JP topology 2 stops/record is load-bearing); per line ≤26 glyphs / 208 px (draw via `0x02012EFC`) |
| 1e0 (special-defense desc) | RAM base `0x023594EC` | offtab arm9 `0x02178134`; drawer `0x02055BD8` (3 lines @ tile rows 0xC/0xE/0x10, same box); number code-drawn by `0x020559D4`; JP topology 3 stops/record. Units with no defense take the static-label path (`0x02055CEE` → label `0x14AE47`), not the 1e0 drawer |
| special-defense type-name pool | `0xB6E6A` | `0x020B6E6A` (Iフィールド etc.); name/quote arena `0x020B5xxx–0x020B7xxx` (primary-dict-encoded, strings shared) |
| bark format processor | — | `0x02065750`; token-truncation fix: veneer @ `0x6588E` → cave @ `0x190014` |
| briefing (作戦内容) table | `0x1985A4..0x1A626B` | record table; ZH text blobs live in pool B (`0x023E7000`); viewer decode_run tail hook @ `0x1330E`, return-fixup global @ `0x18F47C` |
| captain badge tiles | — | `388.bin` BG tiles (graphics); force-HUD `478.bin`; terrain `48a.bin` (see §1 table) |
| compressed squad sub-menu | — | expander pc `0x020A0A86` → transient buffer `0x023B7000`; combat-UI decompressor target ~`0x023B0000` (wordcopy `0x0200D836`, map composer `0x020660DE`) — custom codec, never translated (owner won't-fix) |

### 4.4 Stage engine / script VM / progression

| what | file | RAM / notes |
|---|---|---|
| stage descriptor table | `0x175560` | 101 records × 0x34, one per `_STG` file; word[0] = `0x0232C800` for every stage (single fixed load buffer) |
| stage load buffer | — | `0x0232C800`, size `0x13800` (79,872 B), ends `0x02340000`. Whole `_STG` file is read here verbatim |
| resident stage context | — | ptr @ `0x0227D444`; `[+4]` (= `0x0227D448`) holds the buffer base; `header[8]` = the name table |
| stage name-string reader | — | `0x0202E838` (`ldr r4,[r2,#4]` @ `0x0202E850` — the instruction behind the alignment crash) |
| dialogue/event script opcode dispatcher | `0x9EBAC` | `0x0209EBAC` |
| event-script VM (cutscene/ending driver) | — | ctx @ `0x0227CD0C` {+0 IP, +4 callstack, +8 jump-table base}; dispatcher loop `0x02032060`; opcode jump table `0x0203207E` (26 opcodes); JUMP handler `0x020323F8` (reads a 4-byte ABSOLUTE target); read-opcode leaf `0x0203248C` |
| mid-stage demo object setup | — | `0x020A2070` (event object @ BSS `0x0227FCF8`); FS/memcpy cluster `0x020A2000..0x020A3900` (memcpy `0x020A37A8` / `0x020A38C0`) |
| current stage id | — | halfword @ `0x0227CC48` (35 arm9 literal refs). NB `0x0227CE55` is the stage *selector* (a different field) |
| free-battle (索敵) counter | — | byte @ `0x0227CC80` (5 literal refs; capped at 7 by the increment) |
| post-battle unlock handler | — | `0x0202AAFC` (increment @ `0x0202AB2C`, decision call @ `0x0202AB3C`); decision fn `0x02032690..0x020326FE` (maps (stage, counter) → next stage); backstage check caller `0x020324BA`; unlock setup `0x0202AC70` |
| free-battle threshold patch sites | `0x326A2 / 0x326AC / 0x326B6 / 0x326CA / 0x326DE / 0x326E8 / 0x326F2` | JP `cmp r5,#3/#4; bne` → shipped `cmp r5,#1; blo` (`01 2d 00 d3`) — the owner-requested "1 free battle unlocks the next special stage" gameplay patch (all seven gated transitions) |
| Eternal carrier-capacity stat | `0xDAFF1` | unit master table `+0x0D` u16 (utid 639): JP `2` → shipped `6` — owner-requested gameplay data edit giving the Eternal the standard 6-unit warship capacity. Spec/default only: an existing save keeps its baked per-group slot allocation (edit the save too for already-owned Eternals) |
| stage-id examples | — | `0x1A`=11SP, `0x35`=24a, `0x36`=24b, `0x3A`=SP2b, `0x3B`=SP3a, `0x3C`=SP3b, `0x3D`=SP4a, `0x3E`=SP4b, `0x3F`=SP4s, `0x47`=SP7b |
| BackStage 一覧 cursor row | — | byte @ `0x0227D059` (row ≠ charID on partial rosters!) |
| parts inventory | — | `0x0227CDDC` (active at save-load) |
| crash signature | — | BIOS abort spin @ `0xFFFF0104` (sometimes `0xFFFF0108`); data abort with sp(abort)=0 double-faults into a permanent loop |

### 4.5 Trampolines / caves shipped in the final ROM (why those bytes differ)

The head of arm9 (< `0x1B6DA0`) differs from JP in ~79k bytes across ~3.8–10k regions. They
fall into these classes (each must be reproducible by the build):

1. **Patched literals** — atlas ptr `0x1315C`, arena-lo `0xA48F8`, ModuleParams `0xB0C/0xB10`.
2. **Translated data pools in place** — char-DB names, master-table names, ID-command
   names/summaries, detail pool, label arenas, alt-dict entries, briefing table.
3. **Repointed pointer words** — table entries retargeted at relocated strings (resident
   caves / pool A / pool B).
4. **Code patches** (small, each with a documented why):
   * render-dispatch trampoline @ `0x11A2A0` (slot ≥2196 → renderA);
   * value-3-ink LUT `0x14510`;
   * engine-B r7 clamp trampoline @ `0x12C4A` → cave `0x1B3E22+` (affinity ghost / roster
     aliasing / info-panel strays; ctx-signature-gated);
   * engine-A clamp trampoline @ `0x1359A` → cave `0x11C1FC`;
   * plot clip trampoline @ `0x12FE6` → cave `0x1B35F8` (list first-glyph bottom-strip clip);
   * squad-panel ghost blank @ `0x12CCC` → cave `0x1B3FC4` (length-aware conditional);
   * browse auto-populate @ `0x0204A340` → cave `0x1B3E7C`;
   * 一覧 portrait decode-on-change @ `0x4A66A` → cave `0x1B3670`;
   * bark hi-byte un-consume veneer @ `0x6588E` → cave `0x190014`;
   * briefing viewer return fixup @ `0x1330E` + global `0x18F47C`;
   * ID-ability out-of-table bounds check @ getter `0x02098DC8` → cave `0x18F600`;
   * free-battle thresholds (7 × 4-byte windows, §4.4);
   * the Eternal carrier-capacity stat word (`0xDAFF1`, §4.4 — gameplay data, not code);
   * renderB font slot 4 content = digit “3” (restored JP glyph — never repoint it).
5. **The appended tail** — atlas + pools + autoload list (§2/§3).

Anything outside these classes in an arm9 diff is a bug.
