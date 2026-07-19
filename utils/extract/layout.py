"""Canonical ROM layout: every table address / record geometry of the JP game.

This module is THE single home for reverse-engineered layout knowledge used by
extraction (`utils/extract/`), the dump CLI (`build/extract_data_from_game.py`)
and the review guide (`build/build_guide.py`).  The static gates in
`test/run_static.py` keep their own independent copies BY DESIGN (a gate must
not trust the build's inputs), and `utils/arm9_layout.py` keeps the write-side
constants it asserts against the image; everything else imports from here.

All offsets are arm9 FILE offsets unless the name says RAM.  Provenance for
each table is docs/ROM_STRUCTURE.md + the disassembly evidence cited inline.
"""
from __future__ import annotations

RAM_BASE = 0x02000000
ARM9_HEAD_END = 0x1B6DB8         # end of the ORIGINAL JP image (resident band)

# ---------------------------------------------------------------------------
# glyph banks / autoload
# ---------------------------------------------------------------------------
JP_ATLAS_OFF = 0x11A2A0          # JP in-image 12x12 atlas (2196 slots)
JP_ATLAS_SLOTS = 2196
RENDERB_OFF = 0x133F14           # 8x16 UI font (renderB); spans to the system
                                 # dict: (0x1444B4-0x133F14)/32 = 2093 glyphs
GLYPH_CELL = 36                  # 12x12 2bpp atlas cell bytes
RENDERB_CELL = 32                # 8x16 2bpp renderB cell bytes
ZH_ATLAS_RAM = 0x023027A0        # appended atlas RAM base in the ZH build
TRAMPOLINE_SPLIT = 2196          # slot >= this -> renderA atlas even on renderB path
MP_LIST_START = 0xB0C            # ModuleParams autoload-list start word
AUTOLOAD_SRC_OFF = 0x1B6860      # autoload source cursor origin (ITCM+DTCM head)

# ---------------------------------------------------------------------------
# text-macro dictionaries (0xF0xx expansion stores)
# ---------------------------------------------------------------------------
DICT_TEXT = 0x12D770             # dialogue + name macro store (4080 entries)
DICT_SYS = 0x1444B4              # system/combat macro store

# ---------------------------------------------------------------------------
# master unit table (utid-keyed)
# ---------------------------------------------------------------------------
MASTER_TABLE = 0xB94BC
MASTER_STRIDE = 0xD8
MASTER_COUNT = 945               # raw slot count; slots aliasing the char-DB are
                                 # not units (see extract walker bound)
UNIT_NAME_FIELD = 0x00           # u32 -> name string
UNIT_CARRIER_CAP = 0x0D          # u8: carrier capacity (gameplay stat)
WEAPON_BLOCK = 0x2C              # 6 weapon sub-records
WEAPON_STRIDE = 0x1C
WEAPONS_PER_UNIT = 6

# ---------------------------------------------------------------------------
# character / pilot DB (cid-keyed)
# ---------------------------------------------------------------------------
CHARDB = 0xDCF18
CHARDB_STRIDE = 0x48
CHARDB_COUNT = 563
PILOT_NAME_FIELD = 0x04          # u32 -> name string
CHARDB_VOICESET = 0x0A           # u16: voice/quote-set id (== bark 0x05 field)

# ---------------------------------------------------------------------------
# ID commands (idn = cid*3 + slot)
# ---------------------------------------------------------------------------
IDCMD_TABLE = 0xEC994
IDCMD_STRIDE = 0x24
IDCMD_COUNT = 1413               # 471 chars x 3 slots fit before table end
IDCMD_NAME = 0x00                # u32 -> name string
IDCMD_SUMMARY = 0x08             # u32 -> summary string
IDCMD_TARGET = 0x0E              # u8 enum: 01=self-only 03=enemy-squad 05=self 09=all
IDCMD_DIDX = 0x22                # u8: detail index into DETAIL_OFFTAB
IDCMD_COND = 0x23                # u8 condition bits (bit 0x02 -> map command)
DETAIL_OFFTAB = 0xF9048          # u32[256] monotonic; string = base + u32[didx]
DETAIL_OFFTAB_N = 256
IDCMD_TARGET_NAMES = {0x01: "仅自身", 0x03: "敌队", 0x05: "自身", 0x09: "全军"}

# cut-in famous line (名台詞): parallel link table indexed by idn
CUTIN_LINK = 0x16FD64            # stride 0xC; +0x00 u16 = (cut-in record #)+1
CUTIN_LINK_STRIDE = 0xC
CUTIN_OFFTAB = 0x16EEA8          # u32[943]; record = 1dc.bin[offtab[R]:offtab[R+1]]
CUTIN_OFFTAB_N = 943
CUTIN_RESOURCE_SIZE_WORD = 0x16C444   # u32: total size of the 1dc resource
CUTIN_FILE = "1dc.bin"

# ---------------------------------------------------------------------------
# special ability / defense (utid-keyed; disasm of drawers 0x2055AB4/0x2055BD8)
# ---------------------------------------------------------------------------
OFFTAB_A = 0x1781A4              # special-ability record-offset table (u32[]) -> 1df.bin
OFFTAB_D = 0x178134              # special-defense record-offset table (u32[]) -> 1e0.bin
OFFTAB_D_WORDS = (OFFTAB_A - OFFTAB_D) // 4   # 28: the D table physically ends
                                 # where the A table begins; walking further
                                 # re-reads A words as fictional D records
                                 # (token-dangling aliases, decoder-audit find)
E71_TABLE = 0xE71B0              # per-utid special-profile table
E71_STRIDE = 0x1C
E71_DEFENSE_NAME = 0x00          # u32 -> defense type-name string
E71_DEFENSE_RIDX = 0x1B          # u8 -> 1e0 record index
ABILITY_SPLIT = 0x276            # utid <= 630: fam = (utid-1)//3
ABILITY_SUB = 0x1A4              # utid  > 630: fam = utid-420
SPECIAL_ABILITY_FILE = "1df.bin"
SPECIAL_DEFENSE_FILE = "1e0.bin"

# ---------------------------------------------------------------------------
# battle effect banks (in-battle info screen)
# ---------------------------------------------------------------------------
ABILITY_CARD_OFFTAB = 0x1775C8   # count(u32=129) + u32[129] record offsets -> 1da.bin
ABILITY_CARD_FILE = "1da.bin"
COMMAND_EFFECT_FILE = "1db.bin"  # fixed layout, records read at precomputed
                                 # offsets (no discovered index table; text runs
                                 # are enumerated by the tokenizer)

# ---------------------------------------------------------------------------
# barks (battle voice lines)
# ---------------------------------------------------------------------------
BARK_FILES = ("0.bin", "1.bin", "1dd.bin", "1de.bin", "c4f.bin")
# record grammar: 00 05 <voiceset u16> 00 06 <char_id u16> <text runs...>
# terminated 00 03 00 01 (sub-lines separated by 00 03 / 00 04 page controls)

# ---------------------------------------------------------------------------
# encyclopedia (資料館) + hangar banks
# ---------------------------------------------------------------------------
CHAR_BIO_OFFTAB = 0x191FA0       # u32[274]+sentinel -> 324.bin records
UNIT_BIO_OFFTAB = 0x191BDC       # u32[239]+sentinel -> c4b.bin records
CHAR_BIO_N, UNIT_BIO_N = 274, 239
CHAR_BIO_FILE, UNIT_BIO_FILE = "324.bin", "c4b.bin"
# per-bank resource-size words (loader allocation size, like the cut-in
# bank's CUTIN_RESOURCE_SIZE_WORD): the only u32s in arm9 equal to each
# bank's byte length, alongside the offset-table sentinels
CHAR_BIO_SIZE_WORD = 0x16C964    # u32 == len(324.bin)
UNIT_BIO_SIZE_WORD = 0x16EE00    # u32 == len(c4b.bin)
WEAPON_LIST_FILE = "31e.bin"     # encyclopedia weapon-name list (00-separated)
PART_NAME_OFFTAB = 0x16B474      # u32[41] -> b6e.bin (40 parts + sentinel)
PART_CAP_OFFTAB = 0x16B518      # u32[41] -> b6f.bin
PART_COUNT = 41
PART_NAME_FILE, PART_CAP_FILE = "b6e.bin", "b6f.bin"

# ---------------------------------------------------------------------------
# stage descriptors + briefing / event-text region (arm9-inline story text)
# ---------------------------------------------------------------------------
STAGE_DESC = 0x175560            # stage descriptor table
STAGE_DESC_STRIDE = 0x34
STAGE_DESC_N = 101
STAGE_DESC_LABEL = 0x0C          # u32 -> stage-number label ("第3前" ...)
STAGE_DESC_TITLE = 0x10          # u32 -> stage title
STAGE_DESC_BRIEF = 0x14          # u32 -> briefing region start (RAM)
STAGE_DESC_FILE = 0x24           # u32 -> "_STGxx.bin" ASCII file name
BRIEF_LO, BRIEF_HI = 0x198555, 0x1A626B   # briefing (作戦内容) record region
BRIEF_MAX_GAP = 0x800            # per-stage briefings form ONE contiguous span
                                 # from BRIEF_LO to the last descriptor's briefing
                                 # end (real inter-briefing gaps are < 0x200); a gap
                                 # larger than this past the final descriptor start
                                 # marks the story-digest / gallery cutscenes
                                 # (『宇宙世紀0079…』 recap et al.) that render like
                                 # briefings but own no descriptor — not briefings.
EVENT_TEXT_LO, EVENT_TEXT_HI = 0x198555, 0x1AD536   # full inline story-text region
                                 # (briefings + route/event blocks; the committed
                                 # event_text edits span 0x1985A4..0x1AD516)

# ---------------------------------------------------------------------------
# stage event-VM (dialogue script files _STG*.bin; disassembly-audited)
# ---------------------------------------------------------------------------
STG_BASE = 0x0232C800            # fixed RAM buffer every stage file loads to
STG_OPSZ = {0x00: 0, 0x01: 0, 0x02: 4, 0x03: 2, 0x04: 1, 0x05: 2, 0x06: 2, 0x07: 0,
            0x08: 0, 0x09: 0, 0x0A: 0, 0x0B: 0, 0x0C: 0, 0x0D: 0, 0x0E: 0, 0x0F: 0,
            0x10: 0, 0x11: 0, 0x12: 0, 0x13: 6, 0x14: 1, 0x16: 4, 0x17: 1, 0x18: 2,
            0x19: 1}
STG_JUMP_OPS = (0x02, 0x13, 0x16)   # GOTO / CALL / CGOTO, u32 absolute target
STG_DISPLAY, STG_RET, STG_GOTO, STG_CGOTO = 0x15, 0x01, 0x02, 0x16
STG_HEADER_MAX = 128

# ---------------------------------------------------------------------------
# JP string pools referenced by literal pointers in code/tables.  Sites (code
# words holding a pointer into these bands) are enumerated by the extractor's
# pointer-graph scan; the committed translations re-aim a subset of them.
# ---------------------------------------------------------------------------
JP_STRING_BANDS = (
    (0xB5000, 0xB94BC),          # in-battle name/title pool (up to the master table)
    (0xF9048, 0xFC650),          # ID-command detail pool (offtab + strings)
    (0x14AC34, 0x155B14),        # post-dictionary label band (menu descriptors)
)
