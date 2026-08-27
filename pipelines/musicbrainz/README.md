# musicbrainz

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Bronze/Silver ingestion of MusicBrainz recording data — links (YouTube, streaming, etc.) and genre tags per recording.

MusicBrainz stores recordings, their external links (`url`/`l_recording_url`/`link`/`link_type`), and community genre tags (`recording_tag`/`tag`/`genre`). This project ingests those tables as-is (Bronze), then derives a recording ↔ link correspondence and a recording ↔ genre correspondence (Silver), via a Python/Polars/Postgres pipeline. Genre hierarchy construction (root genre → subgenre) is separate, ongoing work in the [`wikidata`](../wikidata/README.md) pipeline of this monorepo — the two pipelines are independent for now, with no cross-pipeline joins yet.

Part of the [BehindTheMusicTree](https://github.com/BehindTheMusicTree) ecosystem, feeding [TheMusicTreeAPI](https://github.com/BehindTheMusicTree/the-music-tree-api), which in turn serves [GrowTheMusicTree](https://github.com/BehindTheMusicTree/grow-the-music-tree-frontend) (community taxonomy curation) and [HearTheMusicTree](https://github.com/BehindTheMusicTree/hear-the-music-tree-api) (genre-aware playlists). musicbrainz does not write to TheMusicTreeAPI directly — it publishes an independent dataset for TheMusicTreeAPI to ingest.

## Table of Contents

- [musicbrainz](#musicbrainz)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Pipeline](#pipeline)
  - [Schema](#schema)
  - [Consumers](#consumers)
  - [Data source](#data-source)
  - [Querying bronze output](#querying-bronze-output)
  - [Setup](#setup)
  - [Testing](#testing)
  - [Contributing](#contributing)
  - [License](#license)

## Overview

- **Source:** MusicBrainz Postgres tables (`recording`, `tag`, `recording_tag`, `genre`, `url`, `l_recording_url`, `link`, `link_type`) — see [Data source](#data-source) for how dev/local access is wired
- **Output:** a tidy recording ↔ link correspondence (typed via `link_type`, e.g. free streaming/streaming/license) and a tidy recording ↔ genre correspondence — see [Consumers](#consumers). Both stay one-row-per-fact; collapsing to one row per recording is deferred to a future Gold layer, not built here.

## Pipeline

| Layer  | Contents                                                                                                                                                               |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bronze | Raw MusicBrainz tables ingested as-is from Postgres to Parquet via Polars                                                                                              |
| Silver | `recording_link` (recording ↔ link correspondence, typed via `link_type`, e.g. free streaming/streaming/license, built); `recording_genre` (cleaned recording ↔ genre associations, built); `recording_genre_path` (recording → genre-hierarchy-path join, cross-pipeline with `wikidata`'s Silver output) — not yet built |

## Schema

See [SCHEMA.md](SCHEMA.md) for the data dictionary, source-schema deviations, and volumetrics.

## Consumers

This repo's output (`recording_link`, `recording_genre`) is an **independent dataset** — musicbrainz does not call or write into any other ecosystem service. It is intended to be ingested by [TheMusicTreeAPI](https://github.com/BehindTheMusicTree/the-music-tree-api), which owns the authoritative `Genre`/`Criteria` hierarchy (parent + root fields, closure table via `CriteriaLineageRel`) served to:

- **[GrowTheMusicTree](https://github.com/BehindTheMusicTree/grow-the-music-tree-frontend)** — community-driven curation of the genre taxonomy
- **[HearTheMusicTree](https://github.com/BehindTheMusicTree/hear-the-music-tree-api)** — genre intelligence for playlist generation and classification

**Not yet decided:** the publishing format/mechanism for TheMusicTreeAPI to ingest this dataset (e.g. versioned Parquet artifact, CSV export). This is a future integration point, not built yet.

## Data source

Local dev, CI, and pipeline runs use the official MusicBrainz **sample dataset** (`mbdump-sample.tar.xz`, ~336 MB), loaded into a disposable local Postgres via [`musicbrainz-docker`](https://github.com/metabrainz/musicbrainz-docker)'s `createdb.sh -sample`. This gives real schema and real (if reduced) data, fully self-contained. `musicbrainz-docker` is vendored as a pinned git submodule at `vendor/musicbrainz-docker` (under this pipeline directory), and the same script loads it for both a local contributor and CI.

1. `git submodule update --init` (once, after cloning this repo).
2. `cp .env.example .env` and adjust if needed — required config (`MB_HOST`/`MB_PORT`/`MB_DB`/`MB_USER`/`BRONZE_OUTPUT_DIR`) has no in-code defaults (fail-fast: `musicbrainz.db.connect()` and the `ingest` module's entrypoint raise a clear error naming the missing variable if `.env` isn't set up). `.env` is auto-loaded by both — no manual `export`/`source` needed to *run* them. (It is **not** exported to your interactive shell just by existing — see the note in [Querying bronze output](#querying-bronze-output) if you want `$BRONZE_OUTPUT_DIR` available there too.)
3. `scripts/setup-sample-db.sh` — builds and starts a local Postgres, loads the sample dump if not already loaded, and prints the connection string. Safe to re-run.
4. Connect to the resulting local Postgres instance (set credentials via `PGPASSWORD` or `.pgpass` — do not embed passwords in the URI):

   ```
   postgresql://<username>@127.0.0.1:<port>/musicbrainz_db
   ```

   For JDBC-based clients (DBeaver, DataGrip, etc.), use the `jdbc:` form instead — JDBC doesn't support the `user@host` syntax above:

   ```
   jdbc:postgresql://127.0.0.1:<port>/musicbrainz_db?user=<username>&password=<password>
   ```

5. Verify (substitute `<port>` and `<username>` with the values `scripts/setup-sample-db.sh` printed):

   ```bash
   pg_isready -h 127.0.0.1 -p <port>
   psql "postgresql://<username>@127.0.0.1:<port>/musicbrainz_db" -c 'select count(*) from recording;'
   ```

See [TESTING.md](TESTING.md) for test tiers and conventions, including how to use the sample dataset when running local pipeline development or adding integration tests.

## Querying bronze output

```bash
uv run python -m musicbrainz.ingest
```

writes one Parquet file per bronze table (git-ignored) to `BRONZE_OUTPUT_DIR` (see [Data source](#data-source) — required, set in `.env`). Query them directly with [DuckDB](https://duckdb.org/) (`brew install duckdb`), no import step needed — substitute `<BRONZE_OUTPUT_DIR>` below with your actual path:

```bash
duckdb -c "SELECT * FROM '<BRONZE_OUTPUT_DIR>/recording.parquet' LIMIT 10"
```

For repeated exploration, `scripts/setup-duckdb-views.sh` (re)creates one named view per Parquet file found in `BRONZE_OUTPUT_DIR` (same `.env` var; the script sources `.env` itself — no need to export anything in your shell first), in a persistent `bronze.duckdb` alongside them — auto-discovers files, safe to re-run, only needed again if a table is added to or removed from `BRONZE_TABLES`:

```bash
scripts/setup-duckdb-views.sh
duckdb <BRONZE_OUTPUT_DIR>/bronze.duckdb -c "SELECT * FROM recording LIMIT 10"
```

(If you'd rather not retype the full path and want shell tab-completion/`$BRONZE_OUTPUT_DIR` expansion to work directly in ad hoc commands, `set -a; source .env; set +a` once per terminal session first — this exports it to your shell, separately from the auto-loading the scripts above already do for themselves.)

## Setup

See [CONTRIBUTING.md](../../CONTRIBUTING.md#setup) for local environment setup.

## Testing

See [TESTING.md](TESTING.md) for test tiers and conventions.

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## License

[Apache 2.0](../../LICENSE)
