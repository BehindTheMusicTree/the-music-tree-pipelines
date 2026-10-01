# curation

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Bronze-only pipeline: pulls the hand-curated genre-tree rules (manual overrides, aliases, exclusions)
from [grow-the-music-tree-api](https://github.com/BehindTheMusicTree/grow-the-music-tree-api)'s
`curation/export/` endpoint and writes one `manual_<list>.csv` per rule list to `BRONZE_OUTPUT_DIR`.
`wikidata`'s Silver and `gold` read those CSVs from their `CURATION_BRONZE_DIR`.

grow-the-music-tree-api is the source of truth: rules are edited through its `curation/<list>/entries/`
endpoints or the grow admin UI, never here.

## Running

```sh
cp .env.example .env  # then fill GROW_API_KEY
uv run --package curation python -m curation.ingest
```

Run it before `wikidata.silver` and `gold`. It fails fast on a non-2xx response, a missing list, or
a row whose columns don't match the expected CSV header. An empty list is legitimate and produces a
header-only CSV.

## License

Apache-2.0
