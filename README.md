# root-the-music-tree

Giving MusicBrainz's flat genre list some roots.

MusicBrainz stores genres as a flat list (`genre` table: id, name, comment — no parent/child relationship). This project reconstructs a genre hierarchy (root genre → subgenre → recording) using Wikidata as a reference taxonomy, via a Python/Polars/Postgres bronze → silver pipeline.

Part of the [BehindTheMusicTree](https://github.com/BehindTheMusicTree) ecosystem: produces a standalone genre-hierarchy dataset intended for consumption by [TheMusicTreeAPI](https://github.com/BehindTheMusicTree/the-music-tree-api), the ecosystem's authoritative genre reference (its `Genre`/`Criteria` model already has a parent/root hierarchy), which in turn serves [GrowTheMusicTree](https://github.com/BehindTheMusicTree/grow-the-music-tree-frontend) (community taxonomy curation) and [HearTheMusicTree](https://github.com/BehindTheMusicTree/hear-the-music-tree-api) (genre-aware playlists). root-the-music-tree does not write to TheMusicTreeAPI directly — it publishes an independent dataset for TheMusicTreeAPI to ingest.

## Table of Contents

- [Overview](#overview)
- [Pipeline](#pipeline)
- [Consumers](#consumers)
- [Data source](#data-source)
- [Setup](#setup)
- [Testing](#testing)
- [Contributing](#contributing)
- [License](#license)

## Overview

- **Source:** MusicBrainz Postgres tables (`recording`, `artist`, `release`, `tag`, `recording_tag`, `genre`) — see [Data source](#data-source) for how dev/local access is wired
- **Reference taxonomy:** genre parent/child relationships from Wikidata (`P279` subclass of, `P136` genre), fuzzy-matched to MusicBrainz genre names
- **Output:** each recording resolved to its full genre path (root genre → ... → specific genre), published as a standalone dataset — see [Consumers](#consumers)

## Pipeline

| Layer | Contents |
|---|---|
| Bronze | Raw MusicBrainz tables ingested as-is from Postgres to Parquet via Polars |
| Silver | `recording_genre` (cleaned recording ↔ genre associations), `genre_hierarchy` (parent/child from Wikidata), `recording_genre_path` (final recording → genre-path join) |

## Consumers

This repo's output (`genre_hierarchy`, `recording_genre_path`) is an **independent dataset** — root-the-music-tree does not call or write into any other ecosystem service. It is intended to be ingested by [TheMusicTreeAPI](https://github.com/BehindTheMusicTree/the-music-tree-api), which owns the authoritative `Genre`/`Criteria` hierarchy (parent + root fields, closure table via `CriteriaLineageRel`) served to:

- **[GrowTheMusicTree](https://github.com/BehindTheMusicTree/grow-the-music-tree-frontend)** — community-driven curation of the genre taxonomy
- **[HearTheMusicTree](https://github.com/BehindTheMusicTree/hear-the-music-tree-api)** — genre intelligence for playlist generation and classification

**Not yet decided:** the publishing format/mechanism for TheMusicTreeAPI to ingest this dataset (e.g. versioned Parquet artifact, CSV export). This is a future integration point, not built yet.

## Data source

Local dev and pipeline runs use the official MusicBrainz **sample dataset** (`mbdump-sample.tar.xz`, ~336 MB), loaded into a disposable local Postgres via [`musicbrainz-docker`](https://github.com/metabrainz/musicbrainz-docker)'s `createdb.sh -sample`. This gives real schema and real (if reduced) data, fully self-contained.

1. Clone `musicbrainz-docker` alongside this repo and bring up its Postgres service with the sample dump loaded — follow that repo's own setup docs for the exact `docker compose` invocation and current flags for `createdb.sh -sample`.
2. Connect to the resulting local Postgres instance (host/port per `musicbrainz-docker`'s `docker-compose.yml`; set credentials via `PGPASSWORD` or `.pgpass` — do not embed passwords in the URI):

   ```
   postgresql://<username>@127.0.0.1:<port>/musicbrainz_db
   ```

3. Verify (substitute the port `musicbrainz-docker` actually mapped, e.g. `5432`):

   ```bash
   pg_isready -h 127.0.0.1 -p <port>
   psql "postgresql://<username>@127.0.0.1:<port>/musicbrainz_db" -c 'select count(*) from artist;'
   ```

See [TESTING.md](TESTING.md) for how the automated test suite uses this same sample data.

## Setup

See [CONTRIBUTING.md](CONTRIBUTING.md#setup) for local environment setup.

## Testing

See [TESTING.md](TESTING.md) for test tiers and conventions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE)
