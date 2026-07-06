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

There's no local MusicBrainz Postgres dump for now — a full dump doesn't fit on this dev machine's disk. Instead, bronze ingestion reads over an **SSH tunnel to the BehindTheMusicTree `infrastructure` VPS's MusicBrainz db-only mirror (staging)** — a continuously-replicated, full-corpus Postgres mirror (not a sample), on Postgres 18.

1. Bring up the tunnel from the `infrastructure` repo (requires SSH access already set up per its `docs/guides/users-overview.md`):

   ```bash
   btmt-tunnel   # or: ssh -N <SERVER_HOST>-tunnel
   ```

2. This forwards the MB staging Postgres to `127.0.0.1:55433`. Connection string (replace `<username>` with your DB user; set credentials via `PGPASSWORD` or `.pgpass` — do not embed passwords in the URI):

   ```
   postgresql://<username>@127.0.0.1:55433/musicbrainz_db
   ```

3. Verify:

   ```bash
   pg_isready -h 127.0.0.1 -p 55433
   psql "postgresql://<username>@127.0.0.1:55433/musicbrainz_db" -c 'select count(*) from artist;'
   ```

**Known limitation:** this couples local dev to private BTMT infra access — a disk-space workaround, not the target setup. Revisit a self-contained local sample dataset (e.g. `musicbrainz-docker`'s own `createdb.sh -sample`) once disk space allows, so the project is reproducible without VPS access.

This tunnel is for real pipeline dev/runs only — the automated test suite doesn't need it; see [TESTING.md](TESTING.md) for how tests get their MusicBrainz data instead.

## Setup

See [CONTRIBUTING.md](CONTRIBUTING.md#setup) for local environment setup.

## Testing

See [TESTING.md](TESTING.md) for test tiers and conventions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE)
