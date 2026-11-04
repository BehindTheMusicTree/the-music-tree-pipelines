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

## Data sources and licenses

The code in this repository is licensed under Apache 2.0. The data it ingests and produces keeps the license of its source:

| Source | Used for | License |
|---|---|---|
| [MusicBrainz](https://musicbrainz.org) core data | Artists, recordings, genre entities | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| [MusicBrainz](https://musicbrainz.org) supplementary data | Recording tags, from which song ↔ genre associations are derived | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/) |
| [Wikidata](https://www.wikidata.org) | Genre taxonomy, aliases, artist image references | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| [Wikimedia Commons](https://commons.wikimedia.org) | Artist images | Per-file license; author and license travel with each image |
| [YouTube Data API](https://developers.google.com/youtube/v3) | Video candidates and playability | [YouTube API Services Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service) |

Data derived from MusicBrainz tags remains subject to CC BY-NC-SA 3.0: credit MusicBrainz, non-commercial use only, share-alike. See the [MusicBrainz data license](https://musicbrainz.org/doc/About/Data_License).

## License

[Apache 2.0](LICENSE)
