# Reword pass: eliminate unencodable characters from staged library texts

## Why
The glyph atlas gained 47 minted + 25 promoted cells, which covers every
high-frequency gap character.  133 low-frequency characters remain that have
NO atlas cell and cannot get one (supply exhausted).  Every staged library
text that uses one must be reworded so the sentence keeps its exact meaning
using only encodable characters.

## Rules (binding)
1. Edit ONLY the file you were assigned, in
   `audit/translate2/staging/out/lib/` — change the minimal span around each
   listed character.  Meaning, register, and locked terms (terms.md) must be
   preserved.  Do not shorten below or lengthen beyond the brief's length
   band (bio 45–115% of the JP section).
2. A replacement is valid iff every char of the new text encodes:
   `python3 - <<EOF` ...
   `from utils import text_codec; text_codec.encode(TEXT, allow_low15=True, surface="stage")`
   ... i.e. run `python3 audit/translate2/reword_check.py <file>` until it
   prints PASS (it also re-runs check_p23 on the file).
3. Prefer: synonym substitution (侣→伴, 冤→蒙冤 rephrase, 玻璃→透明装甲 only
   if factually right, 笔→记 etc.), light rephrasing second.  NEVER change
   proper nouns — if a proper noun contains a gap char (迦/姗/娅…), pick the
   variant transliteration WITHOUT that char and record it in "notes"
   (e.g. 迦→加, 姗→珊); these become settle-pass entries.
4. 「」quotes must stay quotes; markers/{00} etc. do not appear in staging
   paragraphs and must not be introduced.
5. Record every replacement in the file's "notes" field, appended as:
   `reword: X→Y (reason)`.

## Assignment format
You get a file name plus its gap characters.  Fix ALL of them.
