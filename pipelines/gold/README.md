# gold

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

The consumer-facing export layer: reads `wikidata`'s and `musicbrainz`'s Silver output and produces the
two artifacts `grow-the-music-tree-api` imports at seed time — a canonical genre tree and a small set of
every song reconciled against it. This is the repo's first cross-pipeline join.

Two consumers also pin a snapshot of these exports, so a schema change to them needs a refresh there too:
`genre-tree-view`'s playground fixture (refreshed nightly by the `infrastructure` repo) and
`grow-the-music-tree-api`'s latency SLO suite (`perf/fixtures/`, refreshed by hand, see its README "Performance").

## Table of Contents

- [gold](#gold)
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

- **Inputs:** `wikidata`'s `7_canonical_hierarchy.parquet` and `8_regional_hierarchy.parquet`, and `musicbrainz`'s `5_songs.parquet`, `6_artists.parquet`, `7_artist_members.parquet`, `8_artist_links.parquet` and `9_artist_release_groups.parquet` (all Silver outputs, produced independently by those pipelines — see their own READMEs).
- **Outputs:** `1_canonical_genre_tree.json` and `1_regional_genre_tree.json` (each the full genre tree for that scope, nested `{"treeName": "canonical" | "regional", "tree": [...]}` shape), `2_songs/part-NNNNN.ndjson.gz` (every song as gzip NDJSON parts of 250,000 rows, with a nullable canonical `genre_name` and nullable video), `3_artists/part-NNNNN.ndjson.gz` (song artists, the groups they're members of, and the members of all of those, minus MusicBrainz's special-purpose artists (Various Artists, `[unknown]`, ...), with members, links and release groups), `1_genre_match_report.json` (`{"unresolvedGenreTagCount": N}`), and `_manifest.json` (written last, only once everything else succeeded), the JSON files validated against a JSON Schema and the songs checked in Polars before being written, and all regenerated automatically every run — there are no manual/on-demand export scripts here.

## Pipeline

1. `export_canonical_genre_tree` — builds the canonical genre tree from `7_canonical_hierarchy.parquet` and writes `1_canonical_genre_tree.json`. Independent of the other steps.
2. `export_regional_genre_tree` — builds the regional genre tree from `8_regional_hierarchy.parquet` and writes `1_regional_genre_tree.json`. Independent of the other steps; shares its tree-building logic with `export_canonical_genre_tree` via `genre_tree_builder.py`.
3. `genre_match` — reconciles musicbrainz's raw `genre_name` tags against wikidata's canonical `item_label`s via a match cascade (no genre → exact → `" music"`-suffix-stripped → manual alias CSV → accepted-non-genre CSV → unmatched), writing `1_genre_match.parquet`, a triage sidecar `1_genre_match_unresolved.csv`, and `1_genre_match_report.json` (count of distinct unresolved names, written even when 0). Unmatched names are a soft warning, not a pipeline failure — see [DESIGN.md](DESIGN.md).
4. `export_songs` — writes every row of `1_genre_match.parquet` to `2_songs/part-NNNNN.ndjson.gz`, `genre_name` set only for a resolved genre.
5. `export_artists` — writes the in-scope artists to `3_artists/part-NNNNN.ndjson.gz`, checked in Polars, a sample validated against `artist.schema.json`.

See [SCHEMA.md](SCHEMA.md) for full column/shape detail and [DESIGN.md](DESIGN.md) for the match-cascade rationale.

## Schema

See [SCHEMA.md](SCHEMA.md).

## Setup

See [CONTRIBUTING.md](../../CONTRIBUTING.md#setup) for local environment setup. `cp .env.example .env` and point `MUSICBRAINZ_SILVER_DIR`/`WIKIDATA_SILVER_DIR` at those pipelines' Silver output and `WIKIDATA_BRONZE_DIR` at wikidata's Bronze output.

## Running

```bash
uv run --package gold python -m gold
```

Reads `MUSICBRAINZ_SILVER_DIR`, `WIKIDATA_SILVER_DIR`, `WIKIDATA_BRONZE_DIR`, writes to `GOLD_OUTPUT_DIR` (all required, no in-code defaults — see `.env.example`).

## Testing

`pytest -m "not integration"` from the repo root.

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## License

[Apache 2.0](../../LICENSE)
