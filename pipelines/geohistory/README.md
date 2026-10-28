# geohistory

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Cultural-entity backbone of the music-traditions genealogy. Bronze downloads the pinned
[Glottolog CLDF](https://github.com/glottolog/glottolog-cldf) release and the pinned
[Natural Earth](https://github.com/nvkelso/natural-earth-vector) 1:10m admin-0/admin-1 GeoJSON, and writes
`glottolog_languoid.parquet` and `natural_earth_admin.parquet` to `BRONZE_OUTPUT_DIR`. The Wikidata side (cultural entities and their dates) is
ingested by `wikidata`. See `DESIGN.md` and `SCHEMA.md`.

## Running

```sh
cp .env.example .env
uv run --package geohistory python -m geohistory.ingest
uv run --package geohistory python -m geohistory.silver
```
