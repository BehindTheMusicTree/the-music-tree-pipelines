# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Changelog Best Practices

- Changelogs are for humans, not machines.
- Group changes under: Added, Changed, Improved, Deprecated, Removed, Fixed, Documentation, Performance.
- **"Test" is not a standalone category** — mention tests within the related feature or fix entry.
- Use an `[Unreleased]` section for upcoming changes.
- Use ISO 8601 date format: YYYY-MM-DD.

## [Unreleased]

## [0.1.3] - 2026-08-28

### Documentation

- Clarified the `musicbrainz` Silver contract in `CLAUDE.md` and `README.md`: Silver remains normalized (one row per link / recording-genre match); list-valued, one-row-per-recording outputs are deferred to Gold.
- Removed outdated Wikidata hierarchy references and stale `link_type.name` examples from `musicbrainz/README.md`.

### Fixed

- `musicbrainz` `1_recording_link`: corrected tests/docs to use real `link_type.name` values (`free streaming` / `streaming` for YouTube URLs). No pipeline logic change.

### Added

#### `musicbrainz`

- **Bronze:** added `url`, `l_recording_url`, `link`, and `link_type` tables, enabling recording ↔ URL relationships and precise link-type classification.
- **Bronze:** added `artist_credit`, `artist_credit_name`, and `artist` to support the recording → display-artist join.
- **Silver `1_recording_link`:** generalized `1_recording_youtube_url` into a many-to-many recording ↔ URL dataset typed by `link_type.name`.
- **Silver `2_recording_genre`:** derives recording ↔ genre matches from positive (`count > 0`) `recording_tag` endorsements. Many-to-many, no top-N/deduplication.
- **Silver `3_song_example`:** produces a capped demo dataset `(title, artist, youtube_video_id, genre_name)` for `the-music-tree-api`, selecting one YouTube video and highest-weight genre per recording, with up to 5 recordings per genre.
- Added `scripts/export_song_example_json.py` to manually export `3_song_example.parquet` to JSON for the downstream API.

#### `wikidata`

- **Silver `1_item_links`:** adds browsable `item_url` / `parent_url` columns. Renumbered all subsequent Silver steps accordingly.
- **Silver `2_regional_overview_classification`:** flags `music of <place>` items as regional-overview seeds without dropping them.
- **Silver `3_regional_classification`:** propagates regional status through the hierarchy using an any-parent rule. Exploration currently marks ~53% of items as regional due to broad continent-level seeds; intentionally left unresolved for now.
- **Silver `4_genre_parents`:** adds `parent_is_genre` to distinguish genre from non-genre parents.
- **Silver `5_hierarchy`:** builds separate canonical and regional hierarchies, with one parent per item and real regional parent chains. The current lowest-QID parent selection is explicitly provisional.
- Added `wikidata.silver.profile` coverage for the new classification, parent, item-link, and hierarchy outputs.
- Added Bronze `P361` (`part of`) hierarchy edges alongside `P279` (`subclass of`), tagged via `relation_type`.
- Expanded `explore_genre_tree.ipynb` with Bronze/Silver profiling, hierarchy exploration, and canonical-root investigation.
- Added a `notebook` dependency group (`jupyter`, `ipykernel`, `networkx`, `matplotlib`) and an `nbstripout` pre-commit hook to keep notebook diffs clean.
- Clarified `P31`/`P279`/`P361` responsibilities and corrected stale hierarchy examples in `wikidata_client.py` and the exploration notebook.

## [0.1.2] - 2026-08-19

### Fixed

- `musicbrainz` Bronze ingestion: pass `infer_schema_length=BATCH_SIZE` to `pl.read_database` — Polars was inferring each batch's column types from only its first 100 rows, so a batch with early-row `NULL`s in a `datetime` column followed by a real value later in the same batch could raise `ComputeError: could not append value ... make sure that all rows have the same schema`. Observed intermittently in production after the batched-read fix landed (0.1.1): 39.4M-row `recording` reads didn't OOM, but roughly 1 in 2 runs hit this schema error on a batch boundary.

## [0.1.1] - 2026-08-19

### Fixed

- `musicbrainz` Bronze ingestion: stream each table via a server-side cursor in bounded batches instead of loading it fully into memory before writing — the unbatched `recording` read (2.8M+ rows) was OOM-killing the daily VPS job under swap pressure.

### Documentation

- Add `CLAUDE.md`: repo structure, setup/lint/test/coverage commands, Bronze-layer architecture per pipeline, and the cross-repo production deployment via the `infrastructure` repo's `bronze_ingestion` Ansible role.

## [0.1.0] - 2026-08-18

### Added

- Initial project scaffolding: package structure, `pyproject.toml`, CI workflow (lint + test), and testing documentation.
- Automated MusicBrainz sample dataset setup for dev and CI: `musicbrainz-docker` vendored as a pinned git submodule, loaded via `pipelines/musicbrainz/scripts/setup-sample-db.sh`, wired into a new `integration` CI job.
- Bronze layer: ingest MusicBrainz `recording`, `tag`, `recording_tag`, and `genre` tables from Postgres to Parquet via Polars (`musicbrainz.bronze_musicbrainz`), configured through `BRONZE_OUTPUT_DIR`.
- Shared `pipelines/common` workspace package (`common.env`) providing fail-fast environment variable loading (`require_env`, `load_pipeline_env`) reusable across pipelines.
- `scripts/setup-duckdb-views.sh` to auto-generate DuckDB views over bronze Parquet output for ad hoc querying, documented in the pipeline's `README.md`.
- `SCHEMA.md` documenting the Bronze layer data dictionary and lineage for `musicbrainz`.
- Unit tests for `bronze_musicbrainz`, `db`, and `common.env`, plus a combined coverage gate (`pytest-cov`, 90% `fail_under`) enforced across the `test` and `integration` CI jobs — a new `coverage` job merges both jobs' data via `coverage combine` before checking the threshold, so it isn't blind to code only exercised against the real sample database.
- New `pipelines/wikidata` pipeline: Bronze layer ingesting Wikidata's music genre tree (every item `P31` "instance of" `Q188451` "music genre", plus each genre's `P279` "subclass of" parent edges) live from the public SPARQL endpoint (`wikidata.wikidata_client`), written as-is to `wikidata_genre_tree.parquet` via `wikidata.bronze_wikidata`. Independent of `musicbrainz`'s genre list for now — not yet matched against it.

### Changed

- Renamed the repo from `root-the-music-tree` to `the-music-tree-pipelines` and restructured it into a `uv` workspace monorepo, one directory per pipeline (`pipelines/musicbrainz_to_the_music_tree_api/`), to hold every data pipeline in the stack going forward instead of just the genre-hierarchy one. CI now uses `uv sync`/`uv run` instead of `pip install -e ".[dev]"`.
- Adopted a `<source>_to_<target>` naming convention for pipeline directories, documented in `CONTRIBUTING.md`; the genre-hierarchy pipeline is renamed `root_the_music_tree` → `musicbrainz_to_the_music_tree_api` to match (source: MusicBrainz, target: TheMusicTreeAPI, the eventual consumer of its output dataset).
- Renamed the pipeline directory and Python package `musicbrainz_to_the_music_tree_api` → `musicbrainz`, since the verbose `<source>_to_<target>` form only earned its keep while disambiguating against other pipelines that don't exist yet.
- `wikidata.wikidata_client.run_query` now retries transient failures (connection errors, 5xx responses, truncated JSON from the live SPARQL endpoint) with exponential backoff (`tenacity`, up to 3 attempts). The `Integration` CI job also reruns failed `integration`-marked tests once via `pytest-rerunfailures` (`--reruns 2 --reruns-delay 5`), absorbing endpoint flake without mocking the live dependency.
