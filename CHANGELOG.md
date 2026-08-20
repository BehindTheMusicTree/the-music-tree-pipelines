# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Table of Contents

- [Changelog Best Practices](#changelog-best-practices)
- [Unreleased](#unreleased)
- [0.1.2](#012---2026-08-19)
- [0.1.1](#011---2026-08-19)
- [0.1.0](#010---2026-08-18)

## Changelog Best Practices

- Changelogs are for humans, not machines.
- Group changes under: Added, Changed, Improved, Deprecated, Removed, Fixed, Documentation, Performance.
- **"Test" is not a standalone category** — mention tests within the related feature or fix entry.
- Use an `[Unreleased]` section for upcoming changes.
- Use ISO 8601 date format: YYYY-MM-DD.

## [Unreleased]

### Added

- `wikidata` Silver layer, `1_classification`: flags each Bronze genre-tree row `is_genre`/`exclusion_reason`, excluding Wikidata's "music of \<place\>" regional-overview items (~300 of ~6,300) that were being misclassified as music genres by the `P31` source query.
- `wikidata.silver.profile`: read-only script printing row/item counts and the `exclusion_reason` breakdown for `1_classification.parquet` — see `SCHEMA.md#silver`'s "Data profile" section.
- `wikidata` Bronze: also ingest each genre's `P361` ("part of") parent edges alongside the existing `P279` ("subclass of") ones, tagged by a new `relation_type` column (`"P279"`/`"P361"`) — `P361` is sparser and noisier than `P279` but captures real subgenre hierarchy `P279` misses entirely (e.g. several juke/footwork/ghetto house subgenres). Root items (no `P279`/`P361` parent) drop from ~510 to ~488, since 22 formerly-root items turn out to have a `P361` parent. See `SCHEMA.md`.
- `pipelines/wikidata/notebooks/explore_genre_tree.ipynb`: exploratory notebook over the local Bronze genre tree — `relation_type` breakdown, root/multi-parent items, and a `networkx`/`matplotlib` neighborhood graph plot. New `notebook` uv dependency group (`jupyter`, `ipykernel`, `networkx`, `matplotlib`), kept separate from `dev` so it isn't installed in CI or production.
- `nbstripout` pre-commit hook: strips notebook cell outputs before commit, so committed `.ipynb` diffs stay limited to actual code/markdown changes instead of churning on re-executed outputs.
- `wikidata` Silver layer, `2_genre_parents`: adds a `parent_is_genre` column flagging whether each edge's `parent_id` is itself flagged `is_genre = True` by `1_classification` (not just present in Bronze's raw `P31` extension) — lets a later hierarchy-building step filter to genre-only edges without redoing classification. Flag-only, no rows dropped. `wikidata.silver.profile` now also prints its breakdown. See `SCHEMA.md#silver`.
- `pipelines/wikidata/notebooks/explore_genre_tree.ipynb`: new "Silver exploration" section reading `2_genre_parents.parquet` — `is_genre`/`exclusion_reason` breakdown, sample excluded items, and `parent_is_genre` breakdown with sample non-genre-parent edges.

### Changed

- Renamed `musicbrainz.bronze_musicbrainz` → `musicbrainz.ingest`, `wikidata.bronze_wikidata` → `wikidata.ingest`, and `wikidata.silver_wikidata` → `wikidata.silver`. Each pipeline now lives in its own package (`pipelines/musicbrainz`, `pipelines/wikidata`), so the source-disambiguating suffix from when both were modules inside one shared package no longer applies; the Bronze module is further renamed to `ingest` since `bronze` only restated the layer it already lives in.
- `wikidata.silver` is now a package, one module per Silver step (`silver/classification.py`, `silver/genre_parents.py`) instead of a single flat `silver.py`, with `silver/__main__.py` chaining them so `python -m wikidata.silver` still runs the whole layer. `wikidata.profile_silver` moved to `silver/profile.py` (`python -m wikidata.silver.profile`) alongside the steps it profiles. Tests mirror the split (`test_silver_classification.py`, `test_silver_genre_parents.py`). See `CONTRIBUTING.md#code-style` for the naming convention.

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
