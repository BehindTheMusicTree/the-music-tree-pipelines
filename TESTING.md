# Testing

This project is a bronze → silver ETL pipeline (Polars + Postgres — no Spark, no dbt, no Gold layer yet; see [README.md#pipeline](README.md#pipeline)). Test categories below follow the standard data-pipeline taxonomy, adapted to this stack: pytest instead of dbt tests/pyspark testing/chispa, Pandera (Polars) instead of Great Expectations/Deequ where a data-quality tool is needed.

Tests that need real data use the official MusicBrainz **sample dataset** (`mbdump-sample.tar.xz`, ~336 MB, loaded into a disposable local Postgres via `musicbrainz-docker`'s `createdb.sh -sample`, run through `scripts/setup-sample-db.sh` — see [README.md#data-source](README.md#data-source)). The automated test suite and local pipeline dev both use this sample; CI loads it in the `integration` job via the same script.

## Table of Contents

- [Test categories](#test-categories)
  - [1. Unit tests](#1-unit-tests)
  - [2. Integration tests](#2-integration-tests)
  - [3. Data quality tests](#3-data-quality-tests)
  - [4. Regression tests](#4-regression-tests)
  - [5. E2E / pipeline tests](#5-e2e--pipeline-tests)
  - [6. Performance tests](#6-performance-tests)
  - [7. Freshness tests](#7-freshness-tests)
  - [8. Business conformance tests](#8-business-conformance-tests)
- [Summary](#summary)
- [Directory and naming conventions](#directory-and-naming-conventions)
- [Fixtures and sample data](#fixtures-and-sample-data)
- [Running tests](#running-tests)
- [Current state](#current-state)

## Test categories

### 1. Unit tests

Verify one isolated transformation — a column-cleaning function, a fuzzy-match scoring rule, a business rule (e.g. "a recording needs at least one root genre"). Mostly applies at the **Silver** layer, where cleaning and business logic live. Input is mocked/in-memory, output is asserted exactly, no external dependency.

### 2. Integration tests

Verify that several components work together — reading Bronze output and applying a Silver transformation as one flow, checking the resulting schema, checking joins across tables (e.g. `recording_genre` ⋈ `genre_hierarchy`). This is where a bad column mapping, an incorrect join, or silently dropped rows between Bronze and Silver get caught. Runs against the sample-loaded Postgres (see above), not the live mirror.

### 3. Data quality tests

The core of most data projects: assert that the data itself respects rules, independent of any one transformation.

- **Schema** — expected columns present, correct types
- **Nullity** — e.g. `recording.id` is never null
- **Uniqueness** — primary keys are unique
- **Validity** — values within an expected range or set
- **Referential integrity** — foreign keys resolve (e.g. every `recording_genre.genre_id` exists in `genre_hierarchy`)

Standard tools elsewhere are dbt (`not_null`, `unique`, `accepted_values`), Great Expectations, or Deequ; in this stack a good candidate (once/if it’s added as a dependency) is [Pandera](https://pandera.readthedocs.io/en/stable/polars.html) schemas over Polars DataFrames. Bronze gets light schema checks only (it's raw); Silver gets the bulk of these, since that's where cleaned, join-able data is expected to hold real invariants.

### 4. Regression tests

Catch unexpected changes in output after a pipeline change — row counts staying stable, a column's mean staying close to a prior run, output distribution not shifting unexpectedly. Most valuable during refactors and performance optimizations, where behavior should be provably unchanged. Not implemented yet — there's no Silver output to baseline against until pipeline code exists.

### 5. E2E / pipeline tests

Run the pipeline end-to-end — Bronze → Silver today, Bronze → Silver → Gold if a Gold layer is ever added — against a small, **committed fixture dataset**, asserting on final output (e.g. `recording_genre_path`). This is what actually catches cross-stage bugs like a bad fuzzy genre match silently producing a wrong path; unit tests on individual transforms won't. Because it runs on fixtures rather than a live source, it's CI-safe.

### 6. Performance tests

Verify the pipeline scales — execution time, cost, behavior when input volume doubles. Not relevant yet: there's no pipeline code to benchmark, and the eventual data volume (full MusicBrainz corpus) isn't being processed locally today anyway. Revisit once bronze ingestion against the full corpus is implemented.

### 7. Freshness tests

Verify the source data is current — for the sample-dump-based local Postgres, this typically means periodically refreshing the sample dump used for development. If/when bronze ingestion switches to a continuously-replicated mirror, freshness can instead be enforced by checking the mirror's last replication timestamp is within an expected window before an ingestion run starts. Not implemented yet; this is a pipeline-runtime check rather than a `pytest` test.

### 8. Business conformance tests

Verify data aligns with business rules rather than technical correctness — e.g. every recording resolves to at least one root genre, `genre_hierarchy` has no cycles, the number of root genres roughly matches the expected top-level taxonomy. Not implemented yet; these depend on business rules that don't exist until the Silver transformations do.

## Summary

| # | Category | Verifies | Primary layer | Pytest mechanism | Runs in CI |
|---|---|---|---|---|---|
| 1 | Unit | An isolated transformation (cleaning, scoring, a business rule) | Silver | Own tests, no marker | Yes |
| 2 | Integration | Several components together — Bronze read + Silver transform, joins, resulting schema | Bronze → Silver | `@pytest.mark.integration`, against the sample-loaded Postgres | Yes (once tests exist) |
| 3 | Data quality | The data itself respects rules: schema, nullity, uniqueness, validity, referential integrity | Bronze (light), Silver (heavy) | Assertions inside E2E/pipeline or integration tests — no dedicated marker | Depends on the host test |
| 4 | Regression | Output doesn't change unexpectedly after a pipeline change (row counts, means, distributions) | Silver | Assertions inside E2E/pipeline tests (planned) | Not implemented |
| 5 | E2E / pipeline | The whole pipeline, on a fixture, asserting on final output (e.g. `recording_genre_path`) | Bronze → Silver | Own tests, no marker | Yes |
| 6 | Performance | The pipeline scales — execution time, cost, behavior as volume grows | N/A | Not part of the pytest suite — periodic benchmark | Not implemented |
| 7 | Freshness | The source data is current (e.g. sample dump refreshed periodically; mirror replication timestamp within window, if/when a live mirror is used) | Bronze | Not part of the pytest suite — pipeline-runtime check before ingestion | Not implemented |
| 8 | Business conformance | Data matches business rules (e.g. every recording has a root genre, no cycles in `genre_hierarchy`) | Silver | Assertions inside E2E/pipeline tests (planned) | Not implemented |

This project doesn't have a Gold layer yet (see [README.md#pipeline](README.md#pipeline)) — if one is added later, that's typically where business conformance, E2E, and regression tests carry the most weight, per the usual Bronze/Silver/Gold split. Categories 3, 4, and 8 aren't separate pytest tiers: they describe *what risk a test addresses*, and in practice live as assertions inside the Unit/E2E/Integration tests above rather than a fourth pytest marker.

## Directory and naming conventions

`tests/` mirrors the `src/root_the_music_tree/` module layout. Test files are `test_*.py`, test functions are `test_*` — standard `pytest` discovery, no custom configuration needed beyond `testpaths` (already set in `pyproject.toml`).

## Fixtures and sample data

Unit and E2E/pipeline tests should use small, deterministic Polars DataFrames or hand-built fixture files, purpose-built per bronze/silver stage rather than sampling the full corpus. Prefer `conftest.py` factory fixtures for constructing minimal DataFrames inline; use committed fixture files under `tests/fixtures/` for larger E2E/pipeline inputs.

Integration tests should load a disposable Postgres from the official MusicBrainz sample dump (`mbdump-sample.tar.xz`, ~336 MB, published at `https://ftp.musicbrainz.org/pub/musicbrainz/data/sample/`) via `scripts/setup-sample-db.sh` (wraps `musicbrainz-docker`'s `createdb.sh -sample`) — this gives real schema and real (if reduced) data without touching the live staging mirror. `tests/conftest.py`'s `mb_conn` fixture connects via `psycopg` and calls `pytest.skip` instead of failing hard if the database isn't reachable, so contributors without the sample DB loaded aren't blocked — reuse it rather than opening a new connection per test.

## Running tests

```bash
pytest                        # everything; requires a local sample-loaded Postgres for the integration tests — see above
pytest -m "not integration"   # unit + e2e/pipeline only — what CI runs today
pytest -m integration         # integration only
```

## Current state

No bronze/silver pipeline code exists yet (this repo is still at the project-scaffolding stage) — this document establishes the convention ahead of that code landing, not existing test coverage. Only unit, E2E/pipeline, and integration have a concrete implementation path today; data quality, regression, performance, freshness, and business conformance are documented as categories to grow into, not existing tests. The sample-loading step (Docker service, `createdb.sh -sample`, CI wiring) is built (see [README.md#data-source](README.md#data-source)), and a first `@pytest.mark.integration` test (`tests/test_integration_musicbrainz_db.py`) exercises it end to end — `pytest -m "not integration"` remains what the `test` CI job runs, with a separate `integration` job running `pytest -m integration` against the sample-loaded Postgres. There's no enforced coverage threshold; this is a solo, early-stage project.
