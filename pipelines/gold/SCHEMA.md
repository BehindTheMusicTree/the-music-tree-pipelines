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
- `wikidata`'s Bronze `wikidata_genre_descriptions.parquet` (`WIKIDATA_BRONZE_DIR`): the source of the tree nodes' `description` and Wikipedia `summary` — see `pipelines/wikidata/SCHEMA.md`.
- `musicbrainz`'s `6_artists.parquet`, `7_artist_members.parquet`, `8_artist_links.parquet`, `9_artist_release_groups.parquet`, `11_artist_aliases.parquet` — see `pipelines/musicbrainz/SCHEMA.md`.
- `musicbrainz`'s `5_songs.parquet`: `musicbrainz_recording_id, title, artist, artist_musicbrainz_id, youtube_video_id, youtube_unplayable_reason, youtube_view_count, genre_name, first_release_year` — see `pipelines/musicbrainz/SCHEMA.md`.
- `geohistory`'s `3_entity_succession.parquet`, `5_tradition.parquet`, `5_tradition_link.parquet`, `6_genealogy_lane.parquet`, `7_lane_order.parquet`, `7_band_fragment.parquet` (`GEOHISTORY_SILVER_DIR`) — see `pipelines/geohistory/SCHEMA.md`.

## 2. Outputs

**`1_canonical_genre_tree.json`** — `{"treeName": "canonical", "curationExportedOn": str, "tree": [<node>, ...]}`, one entry per root of `7_canonical_hierarchy`. `curationExportedOn` is the ISO 8601 `exportedOn` of the curation Bronze `manual_export.json` the tree was built from (required by grow-the-music-tree-api's tree import). A node is `{"id": str, "name": str, "children": [<node>, ...], "side": "pop" (optional, direct children of a root only), "isUnacceptedRoot": true (optional, roots only), "isUmbrella": true (optional, only on a node with children), "primaryParents": [str] (optional), "secondaryParents": [str] (optional), "essentialSongs": [str] (optional, MusicBrainz recording ids ordered by `manual_essential_songs.csv` `position`), "description": str (optional, English Wikidata short description), "summary": str (optional, the item's `manual_summary_overrides.csv` override if it has one — a blank override emits no `summary` at all — else the English Wikipedia intro), "summarySourceUrl": str (optional, the Wikipedia article URL, only when `summary` is the Wikipedia intro, for CC BY-SA attribution)}`, recursive (validated against `src/gold/schemas/genre_tree.schema.json`), building on `the-music-tree-genre-kit`'s `CriteriaTreeImportSerializer`/`TreeField` import shape. `id` is the node's Wikidata QID (`item_id`), a stable identifier across label changes — consumed on the `grow-the-music-tree-api` side as the upsert key for `import_criteria_tree` (matches existing rows by `wikidata_id`, falling back to name only for id-less nodes). `side` is only ever present with value `"pop"`, per `manual_canonical_genre_pop_side.csv` below — see [DESIGN.md](DESIGN.md). `secondaryParents` lists the QIDs of the item's extra `5_secondary_parents` parents that are themselves canonical items (the canonical tree is imported first, so nothing else would resolve), minus edges removed by `manual_secondary_parents.csv`, plus its `add` edges; never `primaryParents`. `isUmbrella` is only ever present with value `true`, on a node whose `item_id` is `is_umbrella` in `7_canonical_hierarchy` (canonical tree only); the build raises on an umbrella without children. `isUnacceptedRoot` is only ever present with value `true`, on a root whose `item_id` is `is_accepted = false` in wikidata's `9_canonical_roots.parquet` (not in `manual_accepted_canonical_roots.csv`) — grow-the-music-tree-api flags it for admin review.

**`1_regional_genre_tree.json`** — same shape as `1_canonical_genre_tree.json` above (validated against the same `src/gold/schemas/genre_tree.schema.json`), but with `"treeName": "regional"` and built from `8_regional_hierarchy` instead — one entry per regional/geographic root (e.g. "music of Cape Verde") with that region's genres nested underneath. The item's extra `5_secondary_parents` parents that are regional or canonical items are emitted as `primaryParents`, except edges removed by `manual_secondary_parents.csv`; that list's `add` edges are emitted as `secondaryParents`. On a root, the first `primaryParents` ref becomes its main parent on import.

**`1_genre_match.parquet`** — `musicbrainz_recording_id, title, artist, artist_musicbrainz_id, youtube_video_id, youtube_unplayable_reason, youtube_view_count, first_release_year` (carried through from `5_songs.parquet`), plus:

| Column               | Type | Meaning |
| -------------------- | ---- | ------- |
| `wikidata_genre_name` | str, nullable | The chosen canonical `item_label` after precedence, or `null` if no tag resolved. |
| `wikidata_genre_item_id` | str, nullable | That genre's Wikidata QID. |
| `is_umbrella` | bool | The chosen genre is an umbrella (false when null). |
| `match_method`        | str  | One of `no_genre` (song has no genre tag), `exact`, `normalized`, `manual_alias`, `accepted_non_genre`, `unmatched` — see [DESIGN.md](DESIGN.md) for the cascade. |

**`1_genre_match_unresolved.csv`** — `genre_name, song_count, weight_sum`, one row per unresolved tag name over all songs (most songs first) — written every run, even when empty. A data expert reviews this to promote each name into `manual_genre_alias.csv` or `manual_accepted_non_genre_tags.csv`.

**`1_genre_match_report.json`** — `{"unresolvedGenreTagCount": int}`, the number of distinct `genre_name`s in `1_genre_match_unresolved.csv` — written every run, even when `0`, validated against `src/gold/schemas/genre_match_report.schema.json`.

**`1_umbrella_candidates.csv`** — `item_id, item_label, child_count, direct_song_count, descendant_song_count`: canonical genres with at least one child that are not yet umbrellas, sorted by `direct_song_count` descending then `item_id`. Written every run; a triage aid for the `umbrella_genres` curation list.

**`2_songs/part-NNNNN.ndjson.gz`** — gzip-compressed newline-delimited JSON, split into parts of 250,000 rows (`part-00000.ndjson.gz`, `part-00001.ndjson.gz`, ...; the directory is cleared each run). One line per row of `1_genre_match.parquet` (every song, none filtered): `{"musicbrainz_recording_id": str, "title": str, "artist": str, "artist_musicbrainz_id": str, "youtube_video_id": str | null, "youtube_unplayable_reason": str | null, "youtube_view_count": int | null, "genre_name": str | null, "first_release_year": int | null}`. `genre_name` is the resolved `wikidata_genre_name` when `match_method` is `exact`/`normalized`/`manual_alias`, else null. Then `manual_song_genre.csv` replaces `genre_name` with its `genre_item_id`'s tree name (null when empty); `1_genre_match.parquet` itself is pre-override. `manual_recording_youtube_video.csv` is applied upstream, in musicbrainz Silver `5_songs`. Checked in Polars before writing (raises `ValueError`): non-empty; `musicbrainz_recording_id` (`recording.gid`, a lowercase UUID), `title`, `artist`, `artist_musicbrainz_id` (`artist.gid`, a lowercase UUID) non-null; `musicbrainz_recording_id` unique; non-null `youtube_video_id` matches `^[A-Za-z0-9_-]{11}$`; non-null `youtube_unplayable_reason` is one of `not_found`, `not_embeddable`, `private`, `not_processed`, `region_whitelisted`; non-null `youtube_view_count` is non-negative and has a `youtube_video_id`; `first_release_year` outside 1000-2100 is set to null (warning logged) instead of raising.

**`3_artists/part-NNNNN.ndjson.gz`** — same part layout as `2_songs`, but 10,000 rows each (directory cleared each run). One line per in-scope artist: every `artist_musicbrainz_id` of `1_genre_match.parquet`, the groups those artists are members of, and the members of all of those (one hop: a member's other bands aren't added), minus the special-purpose artists in `SPECIAL_PURPOSE_ARTIST_IDS` (Various Artists, `[unknown]`, `[anonymous]`, ...). Fields: `musicbrainz_artist_id`, `name`, `type`, `country`, `begin_year`, `end_year`, `ended`, `disambiguation` (nullable, from `6_artists`); `aliases` (sorted, distinct strings: `11_artist_aliases` plus the artist's `sort_name`, minus any equal to `name` ignoring case; empty list when none); `members` (`[{musicbrainz_artist_id, begin_year, end_year, ended, roles}]` from `7_artist_members`, members outside the scope dropped); `links` (`[{kind, url}]` from `8_artist_links`); `release_groups` (`[{musicbrainz_release_group_id, title, primary_type, secondary_types, first_release_year}]` from `9_artist_release_groups`). Checked in Polars (non-empty; `musicbrainz_artist_id` and `name` non-null and `musicbrainz_artist_id` unique; no blank `name`; `musicbrainz_artist_id` and nested `musicbrainz_release_group_id` are lowercase UUIDs; non-null `type` is one of `person`, `group`, `orchestra`, `choir`, `character`, `other`; non-null `country` is two uppercase letters), then the first 1,000 rows are validated against `src/gold/schemas/artist.schema.json` to catch shape drift.

**`2_songs_hashes.parquet`, `3_artists_hashes.parquet`** — `(<key>, row_hash)` per exported row (`musicbrainz_recording_id` / `musicbrainz_artist_id`; `row_hash` is the Polars hash of every other column, so it is only comparable within one Polars version — after an upgrade every row differs once). Written atomically each run.

**`2_songs_delta/`, `3_artists_delta/`** — only written when `_synced/<name>_hashes.parquet` exists (the baseline the `infrastructure` run script copies there after grow merged a run successfully). `part-NNNNN.ndjson.gz`: the full-export rows (same shape and part size as `2_songs`/`3_artists`) whose key is new or whose `row_hash` differs from the baseline. `deleted.ndjson.gz`: `{"<key>": str}` per baseline key absent from this run. Cleared each run.

**`4_genealogy.json`** — `{"columns": [{"id": str, "position": int, "lanes": [{"entityId": str, "label": str, "startYear": int | null, "endYear": int}]}], "bands": [{"entityId": str, "label": str, "fragment": int, "startPosition": int, "endPosition": int, "startYear": int | null, "endYear": int | null}], "successions": [{"predecessorId": str, "successorId": str}], "traditions": [{"wikidataId": str, "label": str, "start": int | null, "end": int | null, "anchorId": str | null, "inCanonicalTree": bool}], "traditionLinks": [{"sourceId": str, "targetId": str, "kind": "lineage" | "association"}]}`. Columns ordered by `position` (0 = west), lanes by start year; `successions` keep only edges between entities shown as a lane or band; `anchorId` is null for an unplaced tradition; `inCanonicalTree` is true when the tradition's QID is an `item_id` of `7_canonical_hierarchy`. Raises `ValueError` on zero traditions or columns, a column without lanes, or a schema violation (`src/gold/schemas/genealogy.schema.json`). POSTed to `grow-the-music-tree-api`'s `/v1/genealogy/import/`.

**`_manifest.json`** — `{"builtOn": str, "curationExportedOn": str, "pipelinesCommit": str}`: the build's UTC ISO 8601 timestamp, the curation `exportedOn` stamped into both trees, and this repo's `git rev-parse HEAD`. Deleted at the start of every Gold run and written atomically (temp file + rename) only after every other output succeeded, so its presence means the Gold outputs are complete and consistent.

## 3. Manual CSVs

Read from `CURATION_BRONZE_DIR` (curation pipeline's Bronze output) — see [DESIGN.md](DESIGN.md#curation-mechanics) for how they're applied and validated. `manual_recording_youtube_video.csv` is read by musicbrainz Silver, see `pipelines/musicbrainz/SCHEMA.md`.

**`manual_genre_alias.csv`**: `musicbrainz_genre_name, wikidata_genre_name, reason` — a real genre named differently by musicbrainz than by wikidata.

**`manual_accepted_non_genre_tags.csv`**: `musicbrainz_genre_name, reason` — permanent non-genre folksonomy noise (e.g. `asmr`, `birdsong`) that should never resolve to a genre.

**`manual_canonical_genre_pop_side.csv`**: `root_genre_name, pop_child_genre_name, reason` — for a canonical root, which direct child(ren) are `the-music-tree-genre-kit`'s "pop" side; a root may have zero, one, or several pop children (one row each), but at least one direct child must remain "core".

**`manual_secondary_parents.csv`**: `item_id, parent_id, action, reason` — `action` `add` or `remove`. Ids are `Q…` or `LOCAL:…`.

**`manual_essential_songs.csv`**: `item_id, musicbrainz_recording_id, position, reason` — `position` unique per item.

**`manual_summary_overrides.csv`**: `item_id, summary, reason` — a genre's `summary`; a blank `summary` suppresses the Wikipedia intro (no `summary`, no `summarySourceUrl`).

**`manual_song_genre.csv`**: `musicbrainz_recording_id, genre_item_id, reason` — replaces a song's computed genre (`musicbrainz_recording_id` is the song's representative recording id; takes precedence over `manual_genre_precedence.csv`); empty `genre_item_id` makes it genreless.

All four raise on a blank required value, a malformed id, a duplicate key, an `item_id`/`genre_item_id` in neither tree, or (recording lists) a recording not in the songs.
