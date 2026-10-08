# the-music-tree-pipelines

Data pipelines for the [BehindTheMusicTree](https://github.com/BehindTheMusicTree) ecosystem — a `uv` workspace monorepo, one directory per pipeline under [pipelines/](pipelines/).

## Objective

TheMusicTree is the system; BTMT (BehindTheMusicTree) is the team building it. Its first product is a genre tree to explore and listen to music through. Beyond that, it aims to be a reference that classifies all music, so these pipelines keep every song: a song without a genre or without a playable video still ships, so users can link it to one later.

## Pipelines

| Pipeline | Description |
| --- | --- |
| [musicbrainz](pipelines/musicbrainz/README.md) | Reconstructs a genre hierarchy (root genre → subgenre → recording) from MusicBrainz's flat genre list, using Wikidata as a reference taxonomy |
| [wikidata](pipelines/wikidata/README.md) | Ingests Wikidata's music genre taxonomy (`P279` subclass-of tree, rooted at `Q188451`) from the public SPARQL endpoint |
| [curation](pipelines/curation/README.md) | Pulls the hand-curated rule lists from `grow-the-music-tree-api` |
| [gold](pipelines/gold/README.md) | Exports the genre trees and every deduplicated MusicBrainz song (genre and video when known) for `grow-the-music-tree-api` |

## Setup

See [CONTRIBUTING.md](CONTRIBUTING.md) for local environment setup — each pipeline is an independent `uv` workspace member with its own dependencies, sharing one lockfile and one set of dev tools (Ruff, pytest, pre-commit) declared at the repo root.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE)
