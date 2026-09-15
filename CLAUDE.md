# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What this repo is

A `uv` workspace monorepo of data pipelines feeding the BTMT (Behind The Music Tree) ecosystem. Each pipeline is an independent workspace member under `pipelines/`, sharing only the `common` package. `wikidata` and `musicbrainz` each implement a **Bronze layer** (raw extraction to Parquet) and a **Silver layer** (cleaned, joined, hierarchy-built data) — `wikidata` (nine steps, `1_item_links` → `9_canonical_roots` — see below), `musicbrainz` (three steps so far, `1_recording_link` → `3_song_example` — see below). `pipelines/gold` is the consumer-facing **Gold layer** sitting on top of both.

- `pipelines/common` — shared utilities: per-pipeline `.env` loading, and data-quality check helpers (`common.quality_checks`) used at Bronze ingestion in `musicbrainz`/`wikidata` and at the Silver→Gold boundary in `gold`.
- `pipelines/musicbrainz` — Bronze ingestion from a Postgres MusicBrainz mirror (11 tables: `recording`, `tag`, `recording_tag`, `genre`, `url`, `l_recording_url`, `link`, `link_type`, `artist_credit`, `artist_credit_name`, `artist`), plus a Silver pipeline (`musicbrainz.silver`) that derives a recording ↔ link correspondence (typed by platform via `link_type`), a recording ↔ genre correspondence, and a small capped example-song dataset.
- `pipelines/wikidata` — Bronze ingestion of the music-genre tree from the live Wikidata SPARQL endpoint, plus a Silver pipeline (`wikidata.silver`) that classifies and prunes that tree into global and regional genre hierarchies.
- `pipelines/gold` — the first (so far only) cross-pipeline consumer: exports a canonical genre tree from wikidata's Silver output and reconciles musicbrainz's raw genre-tag names against it, producing the JSON artifacts `grow-the-music-tree-api` imports.

## Setup

```sh
uv sync
```

Requires `uv` (no manual venv management — `uv sync` creates/updates `.venv` for the whole workspace). Each pipeline reads its own `.env` (see each pipeline's `.env.example`), resolved relative to the pipeline's own source file (`common.env.load_pipeline_env`), not the current working directory — so commands below work the same regardless of where you invoke them from.

`pipelines/musicbrainz` additionally needs a local Postgres loaded with the MusicBrainz sample dataset for dev/integration work — see `pipelines/musicbrainz/scripts/setup-sample-db.sh` and the vendored `pipelines/musicbrainz/vendor/musicbrainz-docker` submodule (`git submodule update --init` after clone).

## Commands

- **Run a pipeline (Bronze):** `uv run --package musicbrainz python -m musicbrainz.ingest` / `uv run --package wikidata python -m wikidata.ingest`
- **Run wikidata Silver:** `uv run --package wikidata python -m wikidata.silver` — see `pipelines/wikidata/CLAUDE.md` for the full step-by-step breakdown
- **Run musicbrainz Silver:** `uv run --package musicbrainz python -m musicbrainz.silver` (reads `BRONZE_OUTPUT_DIR/l_recording_url.parquet`, `url.parquet`, `link.parquet`, and `link_type.parquet`, writes `SILVER_OUTPUT_DIR/1_recording_link.parquet`; also reads `recording_tag.parquet`, `tag.parquet`, `genre.parquet`, writes `SILVER_OUTPUT_DIR/2_recording_genre.parquet`)
- **Run gold:** `uv run --package gold python -m gold` — see `pipelines/gold/CLAUDE.md`
- **Lint (matches CI):** `ruff check .` / format: `ruff format .` (line-length 120)
- **Unit tests only:** `pytest -m "not integration"`
- **Integration tests** (needs live Postgres sample DB / live Wikidata SPARQL): `pytest -m integration`
- **Coverage** (combined unit+integration, gate is `fail_under = 90`, see `pyproject.toml`): run both suites with `--cov`, then `coverage combine && coverage report`
- **Pre-commit:** `pre-commit install` once, then `pre-commit run --all-files`

## Architecture

**Bronze layer only, per pipeline:**

- `musicbrainz`: connects to Postgres via `psycopg`, reads each of the 8 raw tables with Polars (`pl.read_database`), writes one Parquet file per table to `BRONZE_OUTPUT_DIR`. See `pipelines/musicbrainz/src/musicbrainz/{ingest.py,db.py}`.
- `wikidata`: queries the public Wikidata SPARQL endpoint (`https://query.wikidata.org/sparql`) live — no local DB. Pulls every item classified `P31` "instance of" music genre (`Q188451`) plus each genre's direct `P279` "subclass of" parent edges (unfiltered — pruning to genre-only parents is Silver-layer work), writes `wikidata_genre_tree.parquet`. See `pipelines/wikidata/src/wikidata/wikidata_client.py`.
- Both pipelines raise (`common.quality_checks.check_non_empty`, `musicbrainz` inlining the same non-empty check on its streamed row count) if a Bronze extraction comes back with zero rows, failing the daily run loudly rather than letting Silver build on missing source data.

**Silver layer, `wikidata`:** nine sequential classification/pruning steps producing a canonical genre hierarchy and a separate regional hierarchy — see `pipelines/wikidata/CLAUDE.md` for the full step-by-step breakdown, target shape, and manual-CSV curation mechanism.

**Silver layer, `musicbrainz` (`pipelines/musicbrainz/src/musicbrainz/silver/`):** three steps so far. `1_recording_link` joins Bronze `l_recording_url` → `link` → `link_type` → `url` (see `pipelines/musicbrainz/SCHEMA.md#1-bronze`) to produce a recording ↔ link correspondence typed by `link_type.name` (free streaming, streaming, license, etc.), many-to-many (a recording can have zero, one, or several links of any type/platform). `2_recording_genre` joins Bronze `recording_tag` to `tag` to `genre` (case-insensitive name match, `count > 0`) to produce a recording ↔ genre correspondence, many-to-many. Both steps stay tidy/long (one row per link, one row per recording-genre match) — collapsing to one row per recording with list-valued link/genre columns is a Gold-layer concern (not yet built), not Silver's. `3_song_example` joins `1_recording_link` (YouTube URLs only, one video per recording), `2_recording_genre` (highest-weight genre per recording), and Bronze `genre`/`recording`/`artist_credit_name`/`artist` (position-0 artist) into a small `(title, artist, youtube_video_id, genre_name)` dataset, capped to 5 recordings per genre — built for a downstream consumer (`the-music-tree-api`'s genre-tree demo endpoint) via an on-demand JSON export script (`scripts/export_song_example_json.py`), not a scheduled job. See `pipelines/musicbrainz/SCHEMA.md#2-silver` for the full derivations. Planned next step (`recording_genre_path`) is not yet built.

**Gold layer, `gold` (`pipelines/gold/src/gold/`):** the repo's first cross-pipeline consumer, reading both `wikidata`'s and `musicbrainz`'s Silver output. `canonical_genre_tree_export.py` builds `1_canonical_genre_tree.json` from wikidata's `7_canonical_hierarchy.parquet`; `regional_genre_tree_export.py` builds `1_regional_genre_tree.json` from wikidata's `8_regional_hierarchy.parquet` the same way — both share the recursive tree-builder in `genre_tree_builder.py`. `genre_match.py` reconciles musicbrainz's raw `3_song_example.parquet` genre-tag names against wikidata's canonical `item_label`s (exact match → `" music"`-suffix stripping → a hand-curated `manual_genre_alias.csv` → a hand-curated `manual_accepted_non_genre_tags.csv`), writing `1_genre_match.parquet` plus a `1_genre_match_unresolved.csv` triage report for names that still don't match — deliberately a soft `logger.warning`, not a raise, so unresolved names don't block the daily run. `song_export.py` filters `1_genre_match.parquet` to resolved rows and writes `2_songs.json`. No Bronze/Silver of its own, no manual/on-demand scripts — all four steps run every time via `src/gold/__main__.py`. See `pipelines/gold/CLAUDE.md`, `SCHEMA.md`, `DESIGN.md`.

**`common.env.load_pipeline_env(__file__)`** resolves each pipeline's `.env` relative to the calling module's own file path, and `require_env(name)` fails fast (raises) on a missing var rather than silently defaulting — this is why `uv run --package X ...` works identically from any CWD, including from a separately cloned checkout on a server.

**Testing taxonomy** is documented per-pipeline in `TESTING.md` (currently only in `pipelines/musicbrainz/`) — an 8-category framework (unit, integration, data quality, regression, E2E/pipeline, performance, freshness, business conformance); only unit/integration/E2E have concrete implementations today.

**CI** (`.github/workflows/ci.yml`): `lint` (ruff + actionlint) → `test` (`pytest -m "not integration"`) and `integration` (loads the MusicBrainz sample DB into a disposable Postgres via Docker, `pytest -m integration`) in parallel → `coverage` (combines both runs' coverage data, enforces `fail_under=90`). Runs on push/PR to `develop`/`main`.

### Production deployment (cross-repo)

Neither pipeline (nor Gold) is deployed _from_ this repo — there is no CD workflow here. All three run **daily in production** via a `music_tree_pipelines` Ansible role in the separate `infrastructure` repo:

- The role clones this repo onto the VPS (staging tracks `develop`, prod tracks `main`/release tags), and renders `pipelines/musicbrainz/.env`, `pipelines/wikidata/.env`, and `pipelines/gold/.env` directly into that checkout (Postgres connection to the on-VPS MusicBrainz mirror, `BRONZE_OUTPUT_DIR`/`SILVER_OUTPUT_DIR`/`GOLD_OUTPUT_DIR` under a per-env data dir).
- A systemd `oneshot` service + daily timer (`music-tree-pipelines-{env}`, e.g. `music-tree-pipelines-staging`/`music-tree-pipelines-prod`) runs `git pull --ff-only` on the pinned branch, then `uv sync --frozen`, then Bronze for both pipelines, Silver where enabled, then Gold once both Silver steps succeed, posting a Discord status embed on success/failure. Manual trigger: `systemctl start music-tree-pipelines-<env>.service`.
- On **staging only**, once Gold succeeds, its exports are POSTed straight to `grow-the-music-tree-api`'s staging API (canonical genre tree, then songs) — see the `infrastructure` repo's `music-tree-pipelines/README.md` for the full grow-sync mechanism.
- Each daily run pulls the pinned branch (`develop` for staging, `main` for prod) before running, so a merge here reaches staging/prod on the next daily run — matching the auto-deploy-on-push behavior Coolify apps already get on those same branches, not gated behind an `infrastructure` tag push.
- No code changes are needed in this repo for that to work — it relies entirely on `common.env.load_pipeline_env()` resolving `.env` correctly regardless of invocation CWD.

## Repo conventions

Full detail in `CONTRIBUTING.md` — summary:

- **Branching:** Git Flow (`main`/`develop`, `feature/*`/`fix/*`/`chore/*`), no direct commits to `main`/`develop`, PRs target `develop`.
- **Commits/PR titles:** Conventional Commits, `type(scope): summary`, imperative, <70 chars, lowercase.
- **Before opening a PR:** update `CHANGELOG.md` under `[Unreleased]`.
- **Code style:** Ruff (lint+format, line-length 120); Polars, never pandas; fail-fast (no silent fallbacks/defaults masking missing config); no comments unless the _why_ is non-obvious; no dead code; exact-pin (`==`) runtime/dev deps (`[build-system]` backend excepted).
- **Per-pipeline docs:** each pipeline's `SCHEMA.md` stays a pure data dictionary — columns, types,
  meaning, and data profiles, nothing else. Rationale, classification rules, and manual-CSV
  curation mechanics belong in a sibling `DESIGN.md` instead (see `pipelines/wikidata/SCHEMA.md` +
  `pipelines/wikidata/DESIGN.md` for the pattern). Add a pipeline's `DESIGN.md` once it has design
  rationale worth writing down — `pipelines/musicbrainz` doesn't need one yet, its `SCHEMA.md` is
  still columns-only.
