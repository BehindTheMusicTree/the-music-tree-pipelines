# root-the-music-tree

Giving MusicBrainz's flat genre list some roots.

MusicBrainz stores genres as a flat list (`genre` table: id, name, comment — no parent/child relationship). This project reconstructs a genre hierarchy (root genre → subgenre → recording) using Wikidata as a reference taxonomy, via a Python/Polars/Postgres bronze → silver pipeline.

## Table of Contents

- [Overview](#overview)
- [Pipeline](#pipeline)
- [Setup](#setup)
- [Contributing](#contributing)
- [License](#license)

## Overview

- **Source:** a local MusicBrainz Postgres dump (`recording`, `artist`, `release`, `tag`, `recording_tag`, `genre`)
- **Reference taxonomy:** genre parent/child relationships from Wikidata (`P279` subclass of, `P136` genre), fuzzy-matched to MusicBrainz genre names
- **Output:** each recording resolved to its full genre path (root genre → ... → specific genre)

## Pipeline

| Layer | Contents |
|---|---|
| Bronze | Raw MusicBrainz tables ingested as-is from Postgres to Parquet via Polars |
| Silver | `recording_genre` (cleaned recording ↔ genre associations), `genre_hierarchy` (parent/child from Wikidata), `recording_genre_path` (final recording → genre-path join) |

## Setup

See [CONTRIBUTING.md](CONTRIBUTING.md#setup) for local environment setup, including the MusicBrainz Postgres dump.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE)
