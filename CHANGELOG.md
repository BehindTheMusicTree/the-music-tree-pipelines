# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Table of Contents

- [Changelog Best Practices](#changelog-best-practices)
- [Unreleased](#unreleased)

## Changelog Best Practices

- Changelogs are for humans, not machines.
- Group changes under: Added, Changed, Improved, Deprecated, Removed, Fixed, Documentation, Performance.
- **"Test" is not a standalone category** — mention tests within the related feature or fix entry.
- Use an `[Unreleased]` section for upcoming changes.
- Use ISO 8601 date format: YYYY-MM-DD.

## [Unreleased]

### Added

- Initial project scaffolding: package structure, `pyproject.toml`, CI workflow (lint + test), and testing documentation.
- Automated MusicBrainz sample dataset setup for dev and CI: `musicbrainz-docker` vendored as a pinned git submodule, loaded via `pipelines/musicbrainz_to_the_music_tree_api/scripts/setup-sample-db.sh`, wired into a new `integration` CI job.
- Bronze layer: ingest MusicBrainz `recording`, `tag`, `recording_tag`, and `genre` tables from Postgres to Parquet via Polars (`musicbrainz_to_the_music_tree_api.bronze_musicbrainz`), configured through `BRONZE_OUTPUT_DIR`.
- Shared `pipelines/common` workspace package (`common.env`) providing fail-fast environment variable loading (`require_env`, `load_pipeline_env`) reusable across pipelines.
- `scripts/setup-duckdb-views.sh` to auto-generate DuckDB views over bronze Parquet output for ad hoc querying, documented in the pipeline's `README.md`.
- `SCHEMA.md` documenting the Bronze layer data dictionary and lineage for `musicbrainz_to_the_music_tree_api`.

### Changed

- Renamed the repo from `root-the-music-tree` to `the-music-tree-pipelines` and restructured it into a `uv` workspace monorepo, one directory per pipeline (`pipelines/musicbrainz_to_the_music_tree_api/`), to hold every data pipeline in the stack going forward instead of just the genre-hierarchy one. CI now uses `uv sync`/`uv run` instead of `pip install -e ".[dev]"`.
- Adopted a `<source>_to_<target>` naming convention for pipeline directories, documented in `CONTRIBUTING.md`; the genre-hierarchy pipeline is renamed `root_the_music_tree` → `musicbrainz_to_the_music_tree_api` to match (source: MusicBrainz, target: TheMusicTreeAPI, the eventual consumer of its output dataset).
