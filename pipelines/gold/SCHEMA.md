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
- `musicbrainz`'s `6_artists.parquet`, `7_artist_members.parquet`, `8_artist_links.parquet`, `9_artist_release_groups.parquet` — see `pipelines/musicbrainz/SCHEMA.md`.
- `musicbrainz`'s `5_songs.parquet`: `musicbrainz_recording_id, title, artist, artist_musicbrainz_id, youtube_video_id, youtube_unplayable_reason, genre_name` — see `pipelines/musicbrainz/SCHEMA.md`.

## 2. Outputs

**`1_canonical_genre_tree.json`** — `{"treeName": "canonical", "tree": [<node>, ...]}`, one entry per root of `7_canonical_hierarchy`. A node is `{"id": str, "name": str, "children": [<node>, ...], "side": "pop" (optional, direct children of a root only), "isUnacceptedRoot": true (optional, roots only), "primaryParents": [str] (optional), "secondaryParents": [str] (optional)}`, recursive (validated against `src/gold/schemas/genre_tree.schema.json`), building on `the-music-tree-genre-kit`'s `CriteriaTreeImportSerializer`/`TreeField` import shape. `id` is the node's Wikidata QID (`item_id`), a stable identifier across label changes — consumed on the `grow-the-music-tree-api` side as the upsert key for `import_criteria_tree` (matches existing rows by `wikidata_id`, falling back to name only for id-less nodes). `side` is only ever present with value `"pop"`, per `manual_canonical_genre_pop_side.csv` below — see [DESIGN.md](DESIGN.md). `secondaryParents` lists the QIDs of the item's extra `5_secondary_parents` parents that are themselves canonical items (the canonical tree is imported first, so nothing else would resolve); never `primaryParents`. `isUnacceptedRoot` is only ever present with value `true`, on a root whose `item_id` is `is_accepted = false` in wikidata's `9_canonical_roots.parquet` (not in `manual_accepted_canonical_roots.csv`) — grow-the-music-tree-api flags it for admin review.

**`1_regional_genre_tree.json`** — same shape as `1_canonical_genre_tree.json` above (validated against the same `src/gold/schemas/genre_tree.schema.json`), but with `"treeName": "regional"` and built from `8_regional_hierarchy` instead — one entry per regional/geographic root (e.g. "music of Cape Verde") with that region's genres nested underneath. The item's extra `5_secondary_parents` parents that are regional or canonical items are emitted as `primaryParents`, except edges listed in `manual_regional_secondary_parents.csv`, emitted as `secondaryParents`. On a root, the first `primaryParents` ref becomes its main parent on import.

**`1_genre_match.parquet`** — `musicbrainz_recording_id, title, artist, artist_musicbrainz_id, youtube_video_id, youtube_unplayable_reason, genre_name` (carried through from `5_songs.parquet`), plus:

| Column               | Type | Meaning |
| -------------------- | ---- | ------- |
| `wikidata_genre_name` | str, nullable | The matched canonical `item_label`, or `null` if `match_method` is `no_genre`/`accepted_non_genre`/`unmatched`. |
| `match_method`        | str  | One of `no_genre` (song has no `genre_name`), `exact`, `music_suffix`, `manual_alias`, `accepted_non_genre`, `unmatched` — see [DESIGN.md](DESIGN.md) for the cascade. |

**`1_genre_match_unresolved.csv`** — `genre_name, title, artist, youtube_video_id`, one row per unmatched `(genre_name, title, artist, youtube_video_id)` combination (not deduplicated by genre name) — written every run, even when empty. A data expert reviews this to promote each name into `manual_genre_alias.csv` or `manual_accepted_non_genre_tags.csv`.

**`1_genre_match_report.json`** — `{"unresolvedGenreTagCount": int}`, the number of distinct `genre_name`s in `1_genre_match_unresolved.csv` — written every run, even when `0`, validated against `src/gold/schemas/genre_match_report.schema.json`.

**`2_songs/part-NNNNN.ndjson.gz`** — gzip-compressed newline-delimited JSON, split into parts of 250,000 rows (`part-00000.ndjson.gz`, `part-00001.ndjson.gz`, ...; the directory is cleared each run). One line per row of `1_genre_match.parquet` (every song, none filtered): `{"musicbrainz_recording_id": str, "title": str, "artist": str, "artist_musicbrainz_id": str, "youtube_video_id": str | null, "youtube_unplayable_reason": str | null, "genre_name": str | null}`. `genre_name` is the resolved `wikidata_genre_name` when `match_method` is `exact`/`music_suffix`/`manual_alias`, else null. Checked in Polars before writing (raises `ValueError`): non-empty; `musicbrainz_recording_id` (`recording.gid`, a lowercase UUID), `title`, `artist`, `artist_musicbrainz_id` (`artist.gid`, a lowercase UUID) non-null; `musicbrainz_recording_id` unique; non-null `youtube_video_id` matches `^[A-Za-z0-9_-]{11}$`; non-null `youtube_unplayable_reason` is one of `not_found`, `not_embeddable`, `private`, `not_processed`, `region_whitelisted`.

**`3_artists/part-NNNNN.ndjson.gz`** — same part layout as `2_songs` (250,000 rows each, directory cleared each run). One line per in-scope artist: every `artist_musicbrainz_id` of `1_genre_match.parquet`, the groups those artists are members of, and those groups' members. Fields: `musicbrainz_artist_id`, `name`, `type`, `country`, `begin_year`, `end_year`, `ended` (from `6_artists`); `members` (`[{musicbrainz_artist_id, begin_year, end_year, roles}]` from `7_artist_members`, members outside the scope dropped); `links` (`[{kind, url}]` from `8_artist_links`); `release_groups` (`[{musicbrainz_release_group_id, title, primary_type, secondary_types, first_release_year}]` from `9_artist_release_groups`). Checked in Polars (non-empty; `musicbrainz_artist_id` and `name` non-null and `musicbrainz_artist_id` unique; no blank `name`), then each row is validated against `src/gold/schemas/artist.schema.json`.

## 3. Manual CSVs

Read from `CURATION_BRONZE_DIR` (curation pipeline's Bronze output) — see [DESIGN.md](DESIGN.md) for how they're used and validated.

**`manual_genre_alias.csv`**: `musicbrainz_genre_name, wikidata_genre_name, reason` — a real genre named differently by musicbrainz than by wikidata.

**`manual_accepted_non_genre_tags.csv`**: `musicbrainz_genre_name, reason` — permanent non-genre folksonomy noise (e.g. `asmr`, `birdsong`) that should never resolve to a genre.

**`manual_canonical_genre_pop_side.csv`**: `root_genre_name, pop_child_genre_name, reason` — for a canonical root, which direct child(ren) are `the-music-tree-genre-kit`'s "pop" side; a root may have zero, one, or several pop children (one row each), but at least one direct child must remain "core".

**`manual_regional_secondary_parents.csv`**: `item_id, item_label, parent_id, parent_label, reason` — an extra parent edge of a regional item to export as `secondaryParents` (classification only, no track flow) instead of the default `primaryParents`. Each `(item_id, parent_id)` must be an exported extra edge, else the export raises.
