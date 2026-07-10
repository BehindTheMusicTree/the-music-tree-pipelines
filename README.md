# the-music-tree-pipelines

Data pipelines for the [BehindTheMusicTree](https://github.com/BehindTheMusicTree) ecosystem — a `uv` workspace monorepo, one directory per pipeline under [pipelines/](pipelines/).

## Pipelines

| Pipeline | Description |
| --- | --- |
| [root_the_music_tree](pipelines/root_the_music_tree/README.md) | Reconstructs a genre hierarchy (root genre → subgenre → recording) from MusicBrainz's flat genre list, using Wikidata as a reference taxonomy |

## Setup

See [CONTRIBUTING.md](CONTRIBUTING.md) for local environment setup — each pipeline is an independent `uv` workspace member with its own dependencies, sharing one lockfile and one set of dev tools (Ruff, pytest, pre-commit) declared at the repo root.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE)
