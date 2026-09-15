# CLAUDE.md — pipelines/gold

Supplements the root `CLAUDE.md` when working inside this pipeline. See the root file for repo-wide setup, conventions, and the `wikidata`/`musicbrainz` pipelines.

## Commands

- **Run:** `uv run --package gold python -m gold` (reads `MUSICBRAINZ_SILVER_DIR`/`WIKIDATA_SILVER_DIR`, writes `GOLD_OUTPUT_DIR/1_canonical_genre_tree.json`, `1_regional_genre_tree.json`, `1_genre_match.parquet`, `1_genre_match_unresolved.csv`, `2_songs.json`)

## Architecture

**No Bronze/Silver of its own** — `gold` only reads the two upstream pipelines' Silver output and exports. Four steps, run in sequence by `src/gold/__main__.py`: `export_canonical_genre_tree` (`canonical_genre_tree_export.py`, independent of the rest) and `export_regional_genre_tree` (`regional_genre_tree_export.py`, independent of the rest) both share the recursive tree-builder in `genre_tree_builder.py`; `genre_match` (`genre_match.py`); `export_songs` (`song_export.py`, consumes `genre_match`'s output). See `SCHEMA.md` for column/shape detail and `DESIGN.md` for the match-cascade rationale.

**No manual/on-demand scripts** — every export this pipeline produces runs automatically as part of `__main__.py`, same cadence as every other pipeline's daily run.

**JSON Schema validation**: both JSON exports are validated (`jsonschema.validate`) against a schema in `src/gold/schemas/` before being written, raising `ValueError` on mismatch — a malformed export should fail the run loudly rather than ship a broken file to `grow-the-music-tree-api`.

**Quality checks**: `genre_match` and `genre_tree_builder` validate join-key null rates/uniqueness and row-count deltas (`src/gold/quality_checks.py`) before writing output, raising `ValueError` on failure — same fail-loud rationale as the JSON Schema validation above.

## Docs

- `SCHEMA.md` — pure data dictionary (columns, types, meaning).
- `DESIGN.md` — match-cascade rationale, manual-CSV curation mechanics.
