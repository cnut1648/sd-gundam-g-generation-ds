"""utils.extract — THE canonical JP game-data extraction package.

The JP ROM is the single source of truth for everything the game shows; this
package is the one place that knows how to read it:

  * `layout`      — every table address / record geometry (single home);
  * `gamerom`     — ROM loading, pointer resolution, macro expanders;
  * `identities`  — glyph-slot -> character identity + per-surface decode;
  * `walkers`     — the per-category table walkers (units, characters, ID
                    commands, barks, stages, briefings, banks, dictionaries,
                    string-pointer graph);
  * `dump`        — assembles the committed `data/jp/` ground-truth dump.

Consumers: `build/extract_data_from_game.py` (the dump CLI),
`build/build_guide.py` (the review guide), the translation-staging tooling,
and the reconciliation gates.
"""
from .gamerom import GameROM  # noqa: F401
from .identities import decode_text, glyph_stream, load_identities  # noqa: F401
