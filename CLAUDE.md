# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What this repo is

A `uv` workspace monorepo of data pipelines feeding the BTMT (Behind The Music Tree) ecosystem. Each pipeline is an independent workspace member under `pipelines/`, sharing only the `common` package. All pipelines implement a **Bronze layer** (raw extraction to Parquet); a **Silver layer** (cleaned, joined, hierarchy-built data) is underway for `wikidata` (five steps, `1_item_links` → `5_hierarchy` — see below), and is not yet built for `musicbrainz`. Gold layer is planned but not yet built.

- `pipelines/common` — shared utilities (currently just per-pipeline `.env` loading).
- `pipelines/musicbrainz` — Bronze ingestion from a Postgres MusicBrainz mirror (4 tables: `recording`, `tag`, `recording_tag`, `genre`).
- `pipelines/wikidata` — Bronze ingestion of the music-genre tree from the live Wikidata SPARQL endpoint, plus a Silver pipeline (`wikidata.silver`) that classifies and prunes that tree into global and regional genre hierarchies.

The two pipelines are independent of each other for now (no cross-pipeline joins yet).

## Setup

```sh
uv sync
```

Requires `uv` (no manual venv management — `uv sync` creates/updates `.venv` for the whole workspace). Each pipeline reads its own `.env` (see each pipeline's `.env.example`), resolved relative to the pipeline's own source file (`common.env.load_pipeline_env`), not the current working directory — so commands below work the same regardless of where you invoke them from.

`pipelines/musicbrainz` additionally needs a local Postgres loaded with the MusicBrainz sample dataset for dev/integration work — see `pipelines/musicbrainz/scripts/setup-sample-db.sh` and the vendored `pipelines/musicbrainz/vendor/musicbrainz-docker` submodule (`git submodule update --init` after clone).

## Commands

- **Run a pipeline (Bronze):** `uv run --package musicbrainz python -m musicbrainz.ingest` / `uv run --package wikidata python -m wikidata.ingest`
- **Run wikidata Silver:** `uv run --package wikidata python -m wikidata.silver` (reads `BRONZE_OUTPUT_DIR/wikidata_genre_tree.parquet`, runs all five steps in sequence, writes `SILVER_OUTPUT_DIR/1_item_links.parquet`, `2_regional_overview_classification.parquet`, `3_regional_classification.parquet`, `4_genre_parents.parquet`, `5_hierarchy.parquet`, and `5_regional_hierarchy.parquet`)
- **Lint (matches CI):** `ruff check .` / format: `ruff format .` (line-length 120)
- **Unit tests only:** `pytest -m "not integration"`
- **Integration tests** (needs live Postgres sample DB / live Wikidata SPARQL): `pytest -m integration`
- **Coverage** (combined unit+integration, gate is `fail_under = 90`, see `pyproject.toml`): run both suites with `--cov`, then `coverage combine && coverage report`
- **Pre-commit:** `pre-commit install` once, then `pre-commit run --all-files`

## Architecture

**Bronze layer only, per pipeline:**

- `musicbrainz`: connects to Postgres via `psycopg`, reads each of the 4 raw tables with Polars (`pl.read_database`), writes one Parquet file per table to `BRONZE_OUTPUT_DIR`. See `pipelines/musicbrainz/src/musicbrainz/{ingest.py,db.py}`.
- `wikidata`: queries the public Wikidata SPARQL endpoint (`https://query.wikidata.org/sparql`) live — no local DB. Pulls every item classified `P31` "instance of" music genre (`Q188451`) plus each genre's direct `P279` "subclass of" parent edges (unfiltered — pruning to genre-only parents is Silver-layer work), writes `wikidata_genre_tree.parquet`. See `pipelines/wikidata/src/wikidata/wikidata_client.py`.

**Silver layer, `wikidata` (`pipelines/wikidata/src/wikidata/silver/`):** five sequential steps, each reading the previous step's output. `1_item_links` derives a browsable Wikidata page URL (`item_url`/`parent_url`) from each row's `item_id`/`parent_id`. `2_regional_overview_classification` flags (not drops) rows whose `item_label` is a Wikidata "music of \<place\>" regional-overview article (e.g. "music of Kenya") rather than an actual genre. `3_regional_classification` cascades that seed set down through parent edges to flag every nationally/ethnically-specific genre (e.g. "fado", "morna") as regional, as opposed to canonical genres like "rock music". `4_genre_parents` flags whether each row's parent is itself a real genre. `5_hierarchy` is the first step that prunes rather than flags: it collapses the edge list to one row per genre item and splits it into two outputs — `5_hierarchy.parquet` (canonical, excludes regional items) and `5_regional_hierarchy.parquet` (regional items only). See `pipelines/wikidata/SCHEMA.md#silver` for the full column definitions, rules, and profiling detail — several rules (e.g. the regional cascade's seed set, the multi-parent collapse heuristic) are explicitly marked provisional/exploration-phase there. `5_hierarchy` (canonical) currently surfaces a high number of root items; whether that reflects the source data or an upstream pruning artifact is an open exploration — see `pipelines/wikidata/notebooks/explore_genre_tree.ipynb`.

**Target shape (design intent, not yet reached):** the canonical tree (`5_hierarchy`) should collapse down to a handful of root genre families — rock, blues, jazz, funk/disco, electronic, hip-hop, reggae/dub, classical music, etc. — not the hundreds of roots it currently produces. Getting there is expected to be mostly a linking/cleaning problem (correcting mis-collapsed parent edges, e.g. the multi-parent lowest-QID heuristic), not a new extraction or classification mechanism. The regional tree (`5_regional_hierarchy`) follows different logic entirely: one root per cultural/geographic region (e.g. "music of Cape Verde"), with each region's own genres nested underneath — it is not expected to collapse to a small root count the way the canonical tree is.

Planned Silver layer for `musicbrainz` (`recording_genre`, `genre_hierarchy`, `recording_genre_path`) is not yet built.

**`common.env.load_pipeline_env(__file__)`** resolves each pipeline's `.env` relative to the calling module's own file path, and `require_env(name)` fails fast (raises) on a missing var rather than silently defaulting — this is why `uv run --package X ...` works identically from any CWD, including from a separately cloned checkout on a server.

**Testing taxonomy** is documented per-pipeline in `TESTING.md` (currently only in `pipelines/musicbrainz/`) — an 8-category framework (unit, integration, data quality, regression, E2E/pipeline, performance, freshness, business conformance); only unit/integration/E2E have concrete implementations today.

**CI** (`.github/workflows/ci.yml`): `lint` (ruff + actionlint) → `test` (`pytest -m "not integration"`) and `integration` (loads the MusicBrainz sample DB into a disposable Postgres via Docker, `pytest -m integration`) in parallel → `coverage` (combines both runs' coverage data, enforces `fail_under=90`). Runs on push/PR to `develop`/`main`.

### Production deployment (cross-repo)

Neither pipeline is deployed _from_ this repo — there is no CD workflow here. Both run **daily in production** via a `bronze_ingestion` Ansible role in the separate `infrastructure` repo:

- The role clones this repo onto the VPS (staging tracks `develop`, prod tracks `main`/release tags), and renders `pipelines/musicbrainz/.env` and `pipelines/wikidata/.env` directly into that checkout (Postgres connection to the on-VPS MusicBrainz mirror, `BRONZE_OUTPUT_DIR` under a per-env data dir).
- A systemd `oneshot` service + daily timer (`bronze-ingestion-{env}`, e.g. `bronze-ingestion-staging`/`bronze-ingestion-prod`) runs `git pull --ff-only` on the pinned branch, then `uv sync --frozen`, then both pipelines in sequence (`uv run --package musicbrainz ...`, `uv run --package wikidata ...`), posting a Discord status embed on success/failure. Manual trigger: `systemctl start bronze-ingestion-<env>.service`.
- Each daily run pulls the pinned branch (`develop` for staging, `main` for prod) before running, so a merge here reaches staging/prod on the next daily run — matching the auto-deploy-on-push behavior Coolify apps already get on those same branches, not gated behind an `infrastructure` tag push.
- No code changes are needed in this repo for that to work — it relies entirely on `common.env.load_pipeline_env()` resolving `.env` correctly regardless of invocation CWD.

## Repo conventions

Full detail in `CONTRIBUTING.md` — summary:

- **Branching:** Git Flow (`main`/`develop`, `feature/*`/`fix/*`/`chore/*`), no direct commits to `main`/`develop`, PRs target `develop`.
- **Commits/PR titles:** Conventional Commits, `type(scope): summary`, imperative, <70 chars, lowercase.
- **Before opening a PR:** update `CHANGELOG.md` under `[Unreleased]`.
- **Code style:** Ruff (lint+format, line-length 120); Polars, never pandas; fail-fast (no silent fallbacks/defaults masking missing config); no comments unless the _why_ is non-obvious; no dead code; exact-pin (`==`) runtime/dev deps (`[build-system]` backend excepted).
