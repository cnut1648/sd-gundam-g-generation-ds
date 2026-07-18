# renderB glyph identification — shared instructions

You are identifying glyphs in the game's **8×16 UI font ("renderB")** so the
decoder can render them. Each glyph is ONE character (a kanji, kana, Latin
letter, digit, or symbol).

## Your inputs (for chunk NNN)
- `audit/translate2/staging/rb_fleet/chunk_NNN.png` — a contact sheet. Each cell
  shows one glyph, upscaled, labelled in yellow with its **slot number**.
- `audit/translate2/staging/rb_fleet/chunk_NNN.json` — for each slot:
  - `contexts`: real in-game text snippets where the slot appears, shown with the
    target slot still as `{B:slot}`. These are the STRONGEST signal — the
    surrounding characters usually make the missing character obvious.
  - `prev_identified` / `next_identified`: the nearest ALREADY-identified slots.

## The decisive fact: the font is READING-ORDERED
Kanji in this font are sorted by their **on'yomi reading in gojūon (あいうえお)
order**. So an unidentified slot's kanji reading falls **between** the readings
of `prev_identified.char` and `next_identified.char`. Use this to disambiguate.

Worked examples:
- `{B:354}殺`, between 安(アン) and 以(イ) → reading ≈ アン → **暗** (暗殺).
- `市{B:456}地`, between 概(ガイ) and 拡(カク) → reading ガイ/カイ → **街** (市街地).
- `後{B:443}`, between 快(カイ) and 改(カイ) → **悔** (後悔).
- `{B:442}我`, between 快 and 改 → **怪** (怪我).
- Latin/kana band: `{B:245}` between P and R → **Q**; between ぼ and ゅ → a small kana.

## Method (per slot)
1. Read the glyph on the sheet.
2. Read its `contexts` — what word is it? (e.g. `{B:465}下` → 落下 → 落; `本{B:416}` → 本音 → 音.)
3. Confirm the reading fits **between** the neighbour chars' readings.
4. Cross-check the glyph shape matches your answer.

If the three agree, you are done. If context and glyph disagree, trust the glyph
shape + reading-order and note it in `uncertain`.

## Output — WRITE this file
`audit/translate2/staging/rb_fleet/out/chunk_NNN.json`:
```json
{
  "chunk": NNN,
  "ids": { "245": "Q", "354": "暗", "456": "街", ... },
  "uncertain": ["<slot>: why", ...]
}
```
Every slot in the input MUST appear in `ids` with exactly one character (give
your best answer even when uncertain, and also list it under `uncertain`).
Use the correct Unicode character (full-width kanji/kana; half-width Latin/digits).
Do not add commentary outside the JSON file.
