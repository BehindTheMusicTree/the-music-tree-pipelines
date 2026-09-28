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
- **Root query:** every Wikidata item classified `P31` ("instance of") `Q188451` ("music genre") — not a `P279` transitive walk from `Q188451`, which finds only ~14 meta-category items and misses nearly every real genre (see [DESIGN.md#1-bronze](DESIGN.md#1-bronze) for why).
- **Bronze:** each of those genre items, plus its direct `P279` ("subclass of") and `P361` ("part of") parent edge(s), written as-is.
- **Silver:** nine sequential steps refine Bronze into canonical and regional genre hierarchies:
  1. `1_item_links` — derives a browsable Wikidata page URL (`item_url`/`parent_url`) from each row's `item_id`/`parent_id`.
  2. `2_non_genre_pruning` — drops theme/technique/out-of-scope items via manual CSV backstops.
  3. `3_regional_overview_classification` — flags regional-overview articles, e.g. "music of Kenya".
  4. `4_regional_classification` — cascades that flag down to nationally/ethnically-specific genres, e.g. "fado", "morna".
  5. `5_main_parent_selection` — picks exactly one main parent per item (manual `manual_main_parent.csv` override wins, else lowest-QID), writing `5_main_parent_selection.parquet`; every other candidate parent edge is kept, not dropped, in a sibling `5_secondary_parents.parquet`.
  6. `6_canonical_parents` — flags whether each item's chosen main parent is itself an actual musical style.
  7. `7_canonical_hierarchy` — prunes to one row per non-regional genre, producing `7_canonical_hierarchy.parquet`.
  8. `8_regional_hierarchy` — prunes to one row per regional genre, producing `8_regional_hierarchy.parquet`.
  9. `9_canonical_roots` — extracts `7_canonical_hierarchy`'s root items (no parent) into their own file, flagging (`is_accepted`, WARNING) roots not in `manual_accepted_canonical_roots.csv` — Gold marks those `isUnacceptedRoot` for review.

See [Pipeline](#pipeline) for exact column names and [SCHEMA.md](SCHEMA.md#3-silver) for full detail.

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

See [DESIGN.md#263-under-exploration--root-count](DESIGN.md#263-under-exploration--root-count).

## Pipeline

| Layer  | Job                                  | Contents                                                                                                                                                                                                                                   |
| ------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Bronze | `ingest`                             | Wikidata's music genre tree (`P279`/`P361` edges), queried live via SPARQL and written as-is to Parquet via Polars                                                                                                                         |
| Silver | `1_item_links`                       | Adds `item_url`/`parent_url`, a browsable Wikidata page derived from `item_id`/`parent_id`                                                                                                                                                  |
| Silver | `2_non_genre_pruning`                | Drops theme/technique/out-of-scope items entirely via manual CSV backstops — see [DESIGN.md#21-2_non_genre_pruning](DESIGN.md#21-2_non_genre_pruning) |
| Silver | `3_regional_overview_classification` | Classifies Bronze edges with `is_regional_overview` and `classification_reason`, tagging items such as "music of Kenya" as regional_overview                                                                                              |
| Silver | `4_regional_classification`          | Adds `is_regional`/`regional_reason`, cascading regional status (e.g. "morna", "fado") from `regional_overview` seeds, which are themselves marked `is_regional`/`seed`                                                                    |
| Silver | `5_main_parent_selection`            | Picks exactly one main parent per item (`manual_main_parent.csv` override wins, else a provisional lowest-QID heuristic), producing `5_main_parent_selection.parquet`; every other candidate parent edge is kept, not dropped, in `5_secondary_parents.parquet` |
| Silver | `6_canonical_parents`                    | Adds `parent_is_canonical`, identifying edges whose main parent isn't itself an actual musical style                                                                                                                                                 |
| Silver | `7_canonical_hierarchy`                        | Prunes to a clean, one-parent-per-item canonical edge list (`7_canonical_hierarchy.parquet`) |
| Silver | `8_regional_hierarchy`                        | Prunes to a clean, one-parent-per-item regional edge list (`8_regional_hierarchy.parquet`) |
| Silver | `9_canonical_roots`                  | Filters `7_canonical_hierarchy.parquet` to root items (no parent), flagging `is_accepted` against `manual_accepted_canonical_roots.csv` (Gold emits `isUnacceptedRoot` for unaccepted ones), for manual exploration of the "too many roots" open question — see [DESIGN.md#263-under-exploration--root-count](DESIGN.md#263-under-exploration--root-count)                                                                                  |

## Schema

See [SCHEMA.md](SCHEMA.md) for the data dictionary and lineage notes, and [DESIGN.md](DESIGN.md)
for the rationale, classification rules, and manual-CSV curation mechanics behind each Silver step.

## Setup

See [CONTRIBUTING.md](../../CONTRIBUTING.md#setup) for local environment setup. No credentials needed for this pipeline — `cp .env.example .env` to set `BRONZE_OUTPUT_DIR`/`SILVER_OUTPUT_DIR` (fail-fast: `wikidata.ingest`/`wikidata.silver`'s entrypoints raise a clear error naming the missing variable if `.env` isn't set up).

## Running

```bash
uv run --package wikidata python -m wikidata.ingest
```

writes `wikidata_genre_tree.parquet` (git-ignored) to `BRONZE_OUTPUT_DIR`. Then:

```bash
uv run --package wikidata python -m wikidata.silver
```

reads that file and writes `1_item_links.parquet`, `2_non_genre_pruning.parquet`,
`3_regional_overview_classification.parquet`, `4_regional_classification.parquet`,
`5_main_parent_selection.parquet`, `5_secondary_parents.parquet`, `6_canonical_parents.parquet`,
`7_canonical_hierarchy.parquet`, `8_regional_hierarchy.parquet`, and `9_canonical_roots.parquet`
(git-ignored) to `SILVER_OUTPUT_DIR`. Query any of them directly with
[DuckDB](https://duckdb.org/), no import step needed:

```bash
duckdb -c "SELECT * FROM '<BRONZE_OUTPUT_DIR>/wikidata_genre_tree.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/1_item_links.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/2_non_genre_pruning.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/3_regional_overview_classification.parquet' WHERE is_regional_overview LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/4_regional_classification.parquet' WHERE is_regional LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/5_main_parent_selection.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/5_secondary_parents.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/6_canonical_parents.parquet' WHERE parent_is_canonical LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/7_canonical_hierarchy.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/8_regional_hierarchy.parquet' LIMIT 10"
duckdb -c "SELECT * FROM '<SILVER_OUTPUT_DIR>/9_canonical_roots.parquet' LIMIT 10"
```

For row/item counts and the `classification_reason`/`is_regional`/`parent_is_canonical` breakdowns (see [SCHEMA.md#3-silver](SCHEMA.md#3-silver)):

```bash
uv run --package wikidata python -m wikidata.silver.profile
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
