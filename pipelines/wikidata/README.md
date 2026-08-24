# wikidata

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Wikidata's music genre taxonomy (`P279` "subclass of" and `P361` "part of", rooted at `Q188451` "music genre"), ingested from the public [Wikidata Query Service](https://query.wikidata.org/) SPARQL endpoint.

## Table of Contents

- [wikidata](#wikidata)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Target shape](#target-shape)
  - [Pipeline](#pipeline)
  - [Schema](#schema)
  - [Setup](#setup)
  - [Running](#running)
  - [Notebooks](#notebooks)
  - [Testing](#testing)
  - [Contributing](#contributing)
  - [License](#license)

## Overview

- **Source:** the public Wikidata SPARQL endpoint (`https://query.wikidata.org/sparql`), queried live — no local dump or database.
- **Root query:** every Wikidata item classified `P31` ("instance of") `Q188451` ("music genre") — not a `P279` transitive walk from `Q188451`, which finds only ~14 meta-category items and misses nearly every real genre (see [SCHEMA.md](SCHEMA.md) for why).
- **Bronze:** each of those genre items, plus its direct `P279` ("subclass of") and `P361` ("part of") parent edge(s), written as-is.
- **Silver:** four sequential steps refine Bronze into canonical and regional genre hierarchies:
  1. `1_regional_overview_classification` — flags regional-overview articles, e.g. "music of Kenya".
  2. `2_regional_classification` — cascades that flag down to nationally/ethnically-specific genres, e.g. "fado", "morna".
  3. `3_genre_parents` — flags whether each edge's parent is itself a real genre.
  4. `4_hierarchy` — prunes to one row per genre, split into canonical (`4_hierarchy.parquet`) and regional (`4_regional_hierarchy.parquet`) outputs.

See [Pipeline](#pipeline) for exact column names and [SCHEMA.md](SCHEMA.md#silver) for full detail.

Independent of the [musicbrainz](../musicbrainz/README.md) pipeline for now: this ingests Wikidata's genre taxonomy on its own terms, not yet matched against MusicBrainz's flat genre list. That matching (and the resulting `genre_hierarchy`) is future work, likely landing in one of the two pipelines once scoped — not built yet.

## Target shape

**Design intent, not yet reached**

The target shape is **two distinct trees**:

- **Canonical tree**:
  The canonical music genre tree should collapse into a handful of **root genre families** (e.g., rock, blues, jazz, funk/disco, electronic, hip-hop, reggae/dub, classical music, etc.).
  _Identified issue_: Currently, the tree produces hundreds of roots, primarily due to **linking and cleaning problems** rather than extraction mechanisms.
  _Goal_: Simplify the hierarchy to reflect a more intuitive and maintainable structure.

- **Regional tree**:
  The regional follows a **different logic**: one root per **cultural/geographic region**, with that region’s specific genres nested beneath it.
  _Example_: A root like "West Africa" could include sub-genres such as "Afrobeat," "Highlife," or "Mbalax."

See [SCHEMA.md#4_hierarchy](SCHEMA.md#4_hierarchy)'s "Under exploration" callout.

## Pipeline

| Layer  | Job                                  | Contents                                                                                                                                                                                                                                   |
| ------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Bronze | `ingest`                             | Wikidata's music genre tree (`P279`/`P361` edges), queried live via SPARQL and written as-is to Parquet via Polars                                                                                                                         |
| Silver | `1_regional_overview_classification` | Classifies Bronze edges with `is_regional_overview` and `classification_reason`, tagging items such as "music of Kenya" as regional_overview                                                                                              |
| Silver | `2_regional_classification`          | Adds `is_regional`/`regional_reason`, cascading regional status (e.g. "morna", "fado") from `regional_overview` seeds, which are themselves marked `is_regional`/`seed`                                                                    |
| Silver | `3_genre_parents`                    | Adds `parent_is_genre`, identifying edges whose parent isn't itself a real genre                                                                                                                                                            |
| Silver | `4_hierarchy`                        | Prunes to two clean, one-parent-per-item edge lists — canonical (`4_hierarchy.parquet`) and regional (`4_regional_hierarchy.parquet`) — with a provisional lowest-QID heuristic for multi-parent items — see [SCHEMA.md](SCHEMA.md#silver) |

## Schema

See [SCHEMA.md](SCHEMA.md) for the data dictionary and lineage notes.

## Setup

See [CONTRIBUTING.md](../../CONTRIBUTING.md#setup) for local environment setup. No credentials needed for this pipeline — `cp .env.example .env` to set `BRONZE_OUTPUT_DIR`/`SILVER_OUTPUT_DIR` (fail-fast: `wikidata.ingest`/`wikidata.silver`'s entrypoints raise a clear error naming the missing variable if `.env` isn't set up).

## Running

```bash
uv run python -m wikidata.ingest
```

writes `wikidata_genre_tree.parquet` (git-ignored) to `BRONZE_OUTPUT_DIR`. Then:

```bash
uv run python -m wikidata.silver
```

reads that file and writes `1_regional_overview_classification.parquet`, `2_regional_classification.parquet`,
`3_genre_parents.parquet`, `4_hierarchy.parquet`, and `4_regional_hierarchy.parquet`
(git-ignored) to `SILVER_OUTPUT_DIR`. Query any of them directly with [DuckDB](https://duckdb.org/), no import step needed:

```bash
duckdb -c "SELECT * FROM '<BRONZE_OUTPUT_DIR>/wikidata_genre_tree.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/1_regional_overview_classification.parquet' WHERE is_regional_overview LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/2_regional_classification.parquet' WHERE is_regional LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/3_genre_parents.parquet' WHERE parent_is_genre LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/4_hierarchy.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/4_regional_hierarchy.parquet' LIMIT 10"
```

For row/item counts and the `classification_reason`/`is_regional`/`parent_is_genre` breakdowns (see [SCHEMA.md#silver](SCHEMA.md#silver)):

```bash
uv run python -m wikidata.silver.profile
```

## Notebooks

`notebooks/explore_genre_tree.ipynb` — tabular and graph exploration of the Bronze genre tree (relation-type breakdown, root/multi-parent items, a `networkx`/`matplotlib` neighborhood plot). Reads the local `BRONZE_OUTPUT_DIR/wikidata_genre_tree.parquet` — no live SPARQL calls, so run `wikidata.ingest` first (see [Running](#running)).

Install the notebook tooling (a separate `notebook` dependency group, not part of the default/CI install) and launch:

```bash
uv sync --group notebook
uv run --group notebook jupyter lab pipelines/wikidata/notebooks/
```

## Testing

Unit tests (`tests/test_wikidata_client.py`, `tests/test_ingest.py`, `tests/test_silver.py`) mock the HTTP layer — no network needed. `tests/test_integration_wikidata.py` is marked `@pytest.mark.integration` and calls the live Wikidata endpoint.

```bash
uv run pytest -m "not integration"   # unit tests, no network needed
uv run pytest -m integration         # hits the live Wikidata endpoint
```

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## License

[Apache 2.0](../../LICENSE)
