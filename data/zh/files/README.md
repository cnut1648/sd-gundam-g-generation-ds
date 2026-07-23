# data/zh/files — translated miscellaneous NitroFS data files

Twenty-five flat data files outside the stage-dialogue (`_STG*`) system carry translated
content. Each JSON table here rebuilds exactly one of them; the builder is
`utils/data_files.py` (`build_data_file(name, jp_bytes)`), which self-checks every
result against `data/manifest.json`. All files are in-place, size-preserving edits of
the Japanese original except `1dc.bin` (+4508 B, records grown) and `1da.bin` (+68 B,
one record relocated to an appended copy).

## Text fields

Text uses the game codec (`utils/text_codec.py` + `data/charmap.json`): plain
characters plus byte-faithful escapes —

| escape       | meaning                                                            |
|--------------|--------------------------------------------------------------------|
| `{00}`       | terminator / segment separator byte                                |
| `{01}`…`{1F}`| control bytes (`{01}` = blank glyph / layout control)              |
| `{03}`/`{04}`| at a cut-in line start: page-break control (see cut-ins below); mid-line these byte values are the punctuation 。 and · |
| `{F0:n}`     | dictionary-macro token n (expands via a string dictionary at runtime) |
| `{SLOT:n}`   | glyph-atlas slot n with no character name in `charmap.json`        |

Every `zh` field re-encodes (via `utils.text_codec.encode`) to the **exact** bytes of
the target file — that is what the build writes, and the per-component sha1 self-check
enforces it. A record that cannot round-trip through the codec carries a canonical
`zh_hex` instead. The JP original of every record lives in `data/jp/`
(same keys: file + offset/index/group) — JP text is never duplicated here.

## JSON formats

* **`edits`** — in-place rewrite table for a fixed-layout text bank. Each edit:
  `{offset, size, zh}`. The build re-encodes `zh` at `offset` and 0x00-pads to
  `size` (the original run's byte budget). An optional top-level `append` record adds
  bytes at the end of the file (used only by `1da.bin`).
* **`cutin_groups`** — full rebuild of the cut-in quote bank: the file is the ordered
  concatenation of all records, each `header` (hex) + encoded `zh` + terminator
  `00 03 00 01` + zero padding to a 4-byte boundary.
* **`table`** — full rebuild of a fixed-total-size table: entries written at explicit
  offsets, 0x00-padded to their slot (`size`).
* **`graphics`** — raw-tile bitmap repaints (not text): regions of
  `{offset, size, jp_hex, zh_hex}`; the build asserts the original bytes (`jp_hex`)
  before writing `zh_hex`.
* **`atlas_graphics`** — static BG labels drawn from committed
  `data/font/atlas12.bin` cells. Each label declares its clear box and clean
  background sample. Resources with shared tiles opt into a deterministic
  fixed-capacity copy-on-write repack, so one label cannot contaminate another
  screen cell and the NitroFS file never grows.

## The files

### barks/ — battle-voice bark banks (`0.bin`, `1.bin`, `1dd.bin`, `1de.bin`, `c4f.bin`)

The short spoken lines rendered during combat. A voice set chains sub-lines:
`05 VV WW 00 06 SS TT <text run> …` terminated `00 03 00 01`; rewritten text is
0x00-padded to the original run budget. **Framing invariant:** the renderer does not
skip stray bytes in the 00-pad gap before a sub-header — a single non-zero byte there
makes it eat the header's `05` into a bogus glyph token and render the next sub-line
as garbage. All rewrites keep every framing byte and never write a `{00}` inside a
run (the in-combat decoder stops at the first 0x00 of a run).

### battle/ — in-battle info/ID screen banks

* `ability_cards.json` (`1da.bin`) — ID-ABILITY effect-card labels (scope lines
  团队内/自身以外, stat lines 命中率↑ …). 129 records addressed by a count+offsets
  table in the code binary; record 0 outgrew its slot and lives as an appended copy
  at the end of the file (+68 B), where that offset table points.
* `command_effects.json` (`1db.bin`) — ID-COMMAND effect labels (对象/持续/效果
  lines). Fixed layout, strictly size-preserving.
* `special_abilities.json` (`1df.bin`) — SPECIAL-ability names/descriptions
  (月光蝶系统, 精神感应, 専用機 tags …). Fixed 3344-byte layout.
* `special_defenses.json` (`1e0.bin`) — SPECIAL-defense descriptions (威力NNN
  barrier/field text; the number is drawn by code at a fixed position).
* `cutin_quotes.json` (`1dc.bin`) — the battle cut-in famous-quote (名台詞) bank:
  the banner shown when a pilot fires an ID command. 942 records addressed by a
  943-entry u32 offset table in the code binary; records were re-encoded at full
  length, so the bank **grew**. Record grammar: header `00 05 <quote-set id u16le>`
  (a few re-authored records use the headerless `00 04` continuation form), then
  text lines separated by `{00}`; a continuation line's leading `{03}` commits the
  page (banner advances), `{04}` continues without a commit. The renderer draws a
  whole record (it does not stop at `{00}`), and the cartridge streaming codec
  expands `{F0:n}` macros while decompressing the bank — so the offset table, the
  `00` framing positions and the terminators must all stay exactly as built here.

### library/ — encyclopedia banks

* `weapon_names.json` (`31e.bin`) — the encyclopedia copy of the weapon-name list
  (0x00-separated names; the in-battle weapon names live in the code binary).
* `character_bios.json` (`324.bin`) — character encyclopedia (図鑑) biography prose.
* `unit_bios.json` (`c4b.bin`) — unit/mobile-suit encyclopedia description prose.

### hangar/ — special-parts banks

* `part_names.json` (`b6e.bin`) — the 40-entry part NAME table (15 model-conversion
  parts + 15 special parts + 10 予備 spares), repacked whole (416 B total is fixed);
  the mirroring arm9 offset table is patched from this file's `name_offset_words`. Every name starts
  with a `{01}` blank glyph that anchors the first glyph against the list renderer's
  top-clip.
* `part_captions.json` (`b6f.bin`) — the part description captions. The inspect box
  renders a fixed 5-line window, so each caption keeps enough trailing blank lines
  that it never bleeds into the next part's box.

### graphics/ — static tile graphics (pixels, not runtime strings)

* `42d.json` — title and bonus-menu OBJ tiles: 按START键！/开始/继续/附加,
  普通模式/特殊模式/返回, 资料/角色/机体, EV回顾, 声音/BGM/SE. The
  original-ROM `START`, `EV`, and `SE` rasters are retained; translated labels
  have black outlines and softer fill colors, and the title prompt keeps its
  original geometry with only the bright fill remapped one palette step darker.
* `388.json` — ship-info panel BG tiles: the captain badge 艦長 → 舰长.
* `3d3.json` — strategy-hub root tabs: 作戦/編成/MS開発/システム →
  作战/编成/MS开发/系统. `作战` is redrawn as one same-size atlas label; unchanged
  `MS` retains the original crisp pixels.
* `3d4.json` — 作战 submenu: 作戦内容/マップ/索敵/進撃 →
  作战内容/地图/索敌/进击.
* `3d5.json` — 编成 submenu: 配属/一覧/別働隊 → 配属/列表/别动队.
* `3d6.json` — MS开发 submenu: ハンガー/系統図 → 格纳库/系统图.
* `3d7.json` — 系统 submenu: セーブ/ロード/オプション → 保存/读取/设置.
  All five resources are rebuilt from the committed WQY-backed 12x12 atlas and
  repacked within their original tile capacities. Every submenu label is centered
  on the common button-content axis at x=34 (2/3/4 glyph starts: x=22/16/10).
* `478.json` — in-combat force-HUD faction table BG tiles: 戦艦/自軍/友軍/敵軍 →
  战舰/自军/友军/敌军.
* `48a.json` — in-combat terrain-legend OBJ tiles: 汎→通, 飛→飞 (legend reads
  回避/通/宇/飞/地/水).
