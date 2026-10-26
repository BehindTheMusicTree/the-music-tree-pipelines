# geohistory

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Cultural-entity backbone of the music-traditions genealogy. Bronze downloads the pinned
[Glottolog CLDF](https://github.com/glottolog/glottolog-cldf) release and writes one
`glottolog_languoid.parquet` to `BRONZE_OUTPUT_DIR`. The Wikidata side (cultural entities and their dates) is
ingested by `wikidata`. See `DESIGN.md` and `SCHEMA.md`.

## Running

```sh
cp .env.example .env
uv run --package geohistory python -m geohistory.ingest
uv run --package geohistory python -m geohistory.silver
```
