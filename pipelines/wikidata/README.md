# wikidata

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Wikidata's music genre taxonomy (`P279` "subclass of", rooted at `Q188451` "music genre"), ingested from the public [Wikidata Query Service](https://query.wikidata.org/) SPARQL endpoint.

## Table of Contents

- [wikidata](#wikidata)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Pipeline](#pipeline)
  - [Schema](#schema)
  - [Setup](#setup)
  - [Running](#running)
  - [Testing](#testing)
  - [Contributing](#contributing)
  - [License](#license)

## Overview

- **Source:** the public Wikidata SPARQL endpoint (`https://query.wikidata.org/sparql`), queried live — no local dump or database
- **Output:** every Wikidata item classified `P31` ("instance of") `Q188451` ("music genre"), plus each genre's direct `P279` ("subclass of") parent edge(s) — see [SCHEMA.md](SCHEMA.md) for why `P31`, not a `P279` walk, is the right root query

Independent of the [musicbrainz](../musicbrainz/README.md) pipeline for now: this ingests Wikidata's genre taxonomy on its own terms, not yet matched against MusicBrainz's flat genre list. That matching (and the resulting `genre_hierarchy`) is future work, likely landing in one of the two pipelines once scoped — not built yet.

## Pipeline

| Layer  | Contents                                                                 |
| ------ | ------------------------------------------------------------------------- |
| Bronze | Wikidata's music genre tree (`P279` edges), queried live via SPARQL and written as-is to Parquet via Polars |
| Silver | `1_classification`: Bronze edges flagged `is_genre`/`exclusion_reason`, filtering out non-genre items (e.g. "music of Kenya") — see [SCHEMA.md](SCHEMA.md#silver) |

## Schema

See [SCHEMA.md](SCHEMA.md) for the data dictionary and lineage notes.

## Setup

See [CONTRIBUTING.md](../../CONTRIBUTING.md#setup) for local environment setup. No credentials needed for this pipeline — `cp .env.example .env` to set `BRONZE_OUTPUT_DIR`/`SILVER_OUTPUT_DIR` (fail-fast: `wikidata.bronze_wikidata`/`wikidata.silver_wikidata`'s entrypoints raise a clear error naming the missing variable if `.env` isn't set up).

## Running

```bash
uv run python -m wikidata.bronze_wikidata
```

writes `wikidata_genre_tree.parquet` (git-ignored) to `BRONZE_OUTPUT_DIR`. Then:

```bash
uv run python -m wikidata.silver_wikidata
```

reads that file and writes `1_classification.parquet` (git-ignored) to `SILVER_OUTPUT_DIR`. Query either directly with [DuckDB](https://duckdb.org/), no import step needed:

```bash
duckdb -c "SELECT * FROM '<BRONZE_OUTPUT_DIR>/wikidata_genre_tree.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/1_classification.parquet' WHERE is_genre LIMIT 10"
```

For row/item counts and the `exclusion_reason` breakdown (see [SCHEMA.md#silver](SCHEMA.md#silver)):

```bash
uv run python -m wikidata.profile_silver
```

## Testing

Unit tests (`tests/test_wikidata_client.py`, `tests/test_bronze_wikidata.py`, `tests/test_silver_wikidata.py`) mock the HTTP layer — no network needed. `tests/test_integration_wikidata.py` is marked `@pytest.mark.integration` and calls the live Wikidata endpoint.

```bash
uv run pytest -m "not integration"   # unit tests, no network needed
uv run pytest -m integration         # hits the live Wikidata endpoint
```

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## License

[Apache 2.0](../../LICENSE)
