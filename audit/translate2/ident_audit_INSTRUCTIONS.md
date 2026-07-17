# Decoder glyph-identity audit — per-chunk task

You verify a Japanese font DECODER for the NDS game *SD Gundam G Generation DS*.
The decoder maps each 12×12 glyph "slot" to a character; some mappings are wrong
(e.g. a slot whose pixels draw 振 but the decoder claims 戦). Confirm or correct
each slot **in context**.

Repo root: `/data-lap3/jiashux/xai/sd-gundam-g-generation-ds`

## Inputs for chunk N (N is zero-padded to 3 digits, e.g. 007)
- Image `audit/translate2/staging/ident_fleet/chunk_NNN.png` — one row per slot:
  `slot <n>`, the big glyph cell (white on dark), then real game sentences drawn
  **exactly as the game renders them** (white glyphs on green) with the target
  glyph outlined in a **RED box**.
- JSON `audit/translate2/staging/ident_fleet/chunk_NNN.json` — per slot:
  `slot`, `claimed` (the decoder's character), `contexts` (decoded sentences with
  the target shown as `【claimed】`).

## Method (contextual, NOT standalone)
1. Read the RED-boxed glyph in the green pixel sentences. Upscale/crop if needed.
   Context makes even low-res kanji legible.
2. Check the decoded sentence reads as valid, sensible Japanese with `【claimed】`
   in that position. A WRONG identity makes a non-word (戦り instead of 振り) AND
   the boxed pixels show a different character.
3. Verdict: claim correct → `ok:true`. Claim wrong → give the correct character
   (read from the boxed pixels + the reading that makes the word valid).
- kana / punctuation (ー 〜 ： ／ 「」 " " % etc.) are usually trivially correct.
- Known ROM typo: 愛 is drawn where 受 is meant (愛ける = 受ける). This is the
  game's own byte, NOT a decoder error — mark `ok:true`.
- If a glyph is genuinely unreadable even in context, use `"ok":false,"correct":
  "?","evidence":"unreadable"`.

## Output — write ONLY this one file
`audit/translate2/staging/ident_fleet/out/chunk_NNN.json`
```
{"chunk":N,"verdicts":[
  {"slot":S,"claimed":"C","ok":true},
  {"slot":S,"claimed":"C","ok":false,"correct":"X","evidence":"short reason"},
  ...
]}
```
Include EVERY slot listed in the chunk JSON. Keep evidence short. Edit no other file.
