# Schema

Data dictionary for `gold`. See [README.md#pipeline](README.md#pipeline) for the step overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [1. Inputs](#1-inputs)
  - [2. Outputs](#2-outputs)
  - [3. Manual CSVs](#3-manual-csvs)

## 1. Inputs

- `wikidata`'s `7_canonical_hierarchy.parquet` and `8_regional_hierarchy.parquet`: `item_id, item_label, item_url, parent_id, parent_label, parent_url, relation_type` — see `pipelines/wikidata/SCHEMA.md`.
- `musicbrainz`'s `3_songs.parquet`: `title, artist, youtube_video_id, genre_name` — see `pipelines/musicbrainz/SCHEMA.md`.

## 2. Outputs

**`1_canonical_genre_tree.json`** — `{"allowsMultiplePrimaryParents": false, "tree": [<node>, ...]}`, one entry per root of `7_canonical_hierarchy`. A node is `{"id": str, "name": str, "children": [<node>, ...], "side": "pop" (optional, direct children of a root only), "primaryParents": [str] (optional), "secondaryParents": [str] (optional)}`, recursive (validated against `src/gold/schemas/genre_tree.schema.json`), building on `the-music-tree-genre-kit`'s `CriteriaTreeImportSerializer`/`TreeField` import shape. `id` is the node's Wikidata QID (`item_id`), a stable identifier across label changes — consumed on the `grow-the-music-tree-api` side as the upsert key for `import_criteria_tree` (matches existing rows by `wikidata_id`, falling back to name only for id-less nodes). `side` is only ever present with value `"pop"`, per `manual_canonical_genre_pop_side.csv` below — see [DESIGN.md](DESIGN.md). `secondaryParents` lists the QIDs of the item's extra `5_secondary_parents` parents that are themselves canonical items (the canonical tree is imported first, so nothing else would resolve); never `primaryParents`.

**`1_regional_genre_tree.json`** — same shape as `1_canonical_genre_tree.json` above (validated against the same `src/gold/schemas/genre_tree.schema.json`), but with `"allowsMultiplePrimaryParents": true` and built from `8_regional_hierarchy` instead — one entry per regional/geographic root (e.g. "music of Cape Verde") with that region's genres nested underneath. The item's extra `5_secondary_parents` parents that are regional or canonical items are emitted as `primaryParents`, except edges listed in `manual_regional_secondary_parents.csv`, emitted as `secondaryParents`. On a root, the first `primaryParents` ref becomes its main parent on import.

**`1_genre_match.parquet`** — `title, artist, youtube_video_id, genre_name` (carried through from `3_songs.parquet`), plus:

| Column               | Type | Meaning |
| -------------------- | ---- | ------- |
| `wikidata_genre_name` | str, nullable | The matched canonical `item_label`, or `null` if `match_method` is `accepted_non_genre`/`unmatched`. |
| `match_method`        | str  | One of `exact`, `music_suffix`, `manual_alias`, `accepted_non_genre`, `unmatched` — see [DESIGN.md](DESIGN.md) for the cascade. |

**`1_genre_match_unresolved.csv`** — `genre_name, title, artist, youtube_video_id`, one row per unmatched `(genre_name, title, artist, youtube_video_id)` combination (not deduplicated by genre name) — written every run, even when empty. A data expert reviews this to promote each name into `manual_genre_alias.csv` or `manual_accepted_non_genre_tags.csv`.

**`2_songs.json`** — flat list, one entry per row of `1_genre_match.parquet` with a resolved genre (`match_method` not `unmatched`/`accepted_non_genre`): `{"title": str, "artist": str, "youtube_video_id": str, "genre_name": str}` (the resolved `wikidata_genre_name`, renamed to match `SongExampleImportSerializer`'s expected field), validated against `src/gold/schemas/songs.schema.json`. `youtube_video_id` must match `^[A-Za-z0-9_-]{11}$` (a real YouTube video id's fixed length); this is enforced twice — `genre_match` drops any malformed row before matching (logged as a warning, see [DESIGN.md](DESIGN.md)), and the schema pattern here is a last-resort net that should never actually trigger. Both mirror the same constraint musicbrainz's `3_songs` step already enforces at extraction time.

## 3. Manual CSVs

Committed alongside the code, colocated with `src/gold/genre_match.py` — see [DESIGN.md](DESIGN.md) for how they're used and validated.

**`manual_genre_alias.csv`**: `musicbrainz_genre_name, wikidata_genre_name, reason` — a real genre named differently by musicbrainz than by wikidata.

**`manual_accepted_non_genre_tags.csv`**: `musicbrainz_genre_name, reason` — permanent non-genre folksonomy noise (e.g. `asmr`, `birdsong`) that should never resolve to a genre.

**`manual_canonical_genre_pop_side.csv`** (colocated with `src/gold/canonical_genre_tree_export.py`): `root_genre_name, pop_child_genre_name, reason` — for a canonical root, which direct child(ren) are `the-music-tree-genre-kit`'s "pop" side; a root may have zero, one, or several pop children (one row each), but at least one direct child must remain "core".

**`manual_regional_secondary_parents.csv`** (colocated with `src/gold/regional_genre_tree_export.py`): `item_id, item_label, parent_id, parent_label, reason` — an extra parent edge of a regional item to export as `secondaryParents` (classification only, no track flow) instead of the default `primaryParents`. Each `(item_id, parent_id)` must be an exported extra edge, else the export raises.
