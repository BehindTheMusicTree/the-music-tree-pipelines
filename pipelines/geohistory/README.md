# geohistory

Part of the [the-music-tree-pipelines](../../README.md) monorepo.

Cultural-entity backbone of the music-traditions genealogy. Bronze downloads the pinned
[Glottolog CLDF](https://github.com/glottolog/glottolog-cldf) release and the pinned
[Natural Earth](https://github.com/nvkelso/natural-earth-vector) 1:10m admin-0/admin-1 GeoJSON and map background layers (disputed lines, places, rivers, lakes), and writes
`glottolog_languoid.parquet`, `natural_earth_admin.parquet`, `natural_earth_basemap.parquet`,
`natural_earth_physical.parquet` and `natural_earth_composed_boundary.parquet` to `BRONZE_OUTPUT_DIR`.
The physical label layer identifies islands; independent land polygons supply their boundaries. Map units
supply the explicit regional boundary conventions documented in `DESIGN.md`.
The Wikidata side (cultural entities, dates, geographic statements and qualifiers) is
ingested by `wikidata`. See `DESIGN.md` and `SCHEMA.md`.

## Running

```sh
cp .env.example .env
uv run --package geohistory python -m geohistory.ingest
uv run --package geohistory python -m geohistory.silver
```

After upgrading from a run without geographic statement metadata or reference boundaries, rerun
geohistory Bronze, Wikidata Bronze and curation before geohistory Silver. Silver requires
`place_relation_overrides.csv`, even when it has no entries. Only resolved full relations enter
containment; partial map coverage is written separately. Inspect `2_place_relation_review.csv`
and `8_entity_geometry_report.json` for unresolved relations and missing or incomplete boundaries.
