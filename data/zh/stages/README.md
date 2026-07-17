# Stage dialogue data (`stages/*.json`)

One JSON per `_STG*.bin` NitroFS stage file (101 files). Each describes every
byte range of the original Japanese file that the translation changes;
`utils/stage_text.build_stage_file(jp_bytes, stage_data)` splices them in one
pass and relocates every absolute pointer that the growth shifts.

## File format background

A stage file is loaded whole to RAM `0x0232C800` and is full of absolute
pointers relative to that base. Dialogue lives in `0x15 <payload> 00 00`
blocks inside event bytecode; payloads are glyph-token streams
(`utils/text_codec.py`). The Chinese re-encode grows most payloads, which
shifts all later bytes, so pointers must be rebumped and five header tables
(header slots 0x04/0x08/0x10/0x14/0x18) must stay 4-byte aligned — the
engine reads their entries with 32-bit `ldr`, and ARMv5 rotates misaligned
loads (a misaligned table black-screens the game).

## Schema

```jsonc
{
 "file": "_STG00.bin",       // NitroFS file name
 "source_size": 17111,       // expected size of the Japanese input file
 "built_size": 20176,        // expected size of the built (translated) file
 "edits": [                  // ascending, non-overlapping byte-range replacements
  {
   "jp_offset": "0x111e",    // offset in the ORIGINAL (Japanese) file = the KEY
   "jp_len": 9,              // bytes replaced there
   "kind": "dialogue",       // "dialogue" = one clean text block; "script" = raw range
   "zh_text": "……情况如何？", // (dialogue only) decoded translation — reference
   "zh_hex": "…"             // replacement bytes — THE CANONICAL BUILD INPUT
  }
 ],
 "inserts": [                // optional pure insertions (no bytes replaced)
  {"jp_offset": "0x4c1e", "hex": "0000", "reason": "table_alignment"}
 ]
}
```

* `zh_hex` is authoritative: the build splices exactly these bytes. `zh_text`
  is decoded FROM `zh_hex` for human reading/editing and never re-encoded by
  the build. The JP block behind each key — original text, console play
  order, speaker, choice flags — lives in `data/jp/stages/<stage>.json` at
  the same `jp_offset` (JP text is never duplicated here).
* `kind: "dialogue"` ranges cover exactly one `0x15 … 00 00` block on both
  sides (replacement may carry trailing `0x00` padding after its terminator).
  In the text fields, `{00}` is the in-box line/segment separator, `{01}`…
  are engine control codes kept verbatim, and `{F0:n}` is a dictionary-macro
  reference (already expanded inline when a dictionary was available).
* `kind: "script"` ranges intersect event bytecode (or block groups) —
  their replacement bytes can embed absolute pointers already fixed up for
  the final layout, so regenerate them rather than hand-editing.
* `inserts` with `reason: "table_alignment"` are zero-byte padding placed
  in front of a header table that dialogue growth would otherwise leave
  4-byte-misaligned.

## Invariants checked by the builder

1. built size == `built_size`, source size == `source_size`;
2. every relocated pointer still resolves inside the built file;
3. all five alignment-sensitive header tables stay 4-byte aligned;
4. the built file fits the 0x13800-byte stage RAM buffer.

The ROM build additionally asserts each built file's sha1 against
`data/manifest.json`.
