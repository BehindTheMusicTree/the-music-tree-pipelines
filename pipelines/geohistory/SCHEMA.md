# Schema

Data dictionary for `geohistory`. Design rationale is in [DESIGN.md](DESIGN.md).

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [1. Bronze](#1-bronze)
    - [1.1 glottolog_languoid.parquet](#11-glottolog_languoidparquet)
    - [1.2 natural_earth_admin.parquet](#12-natural_earth_adminparquet)
  - [2. Silver](#2-silver)
    - [2.1 1_cultural_entity.parquet](#21-1_cultural_entityparquet)
    - [2.2 1_entity_wikidata_item.parquet](#22-1_entity_wikidata_itemparquet)
    - [2.3 2_entity_membership.parquet](#23-2_entity_membershipparquet)
    - [2.4 3_entity_succession.parquet](#24-3_entity_successionparquet)
    - [2.5 4_entity_time_span.parquet](#25-4_entity_time_spanparquet)
    - [2.5.2 2_place_containment, 1_entity_ne_admin and 8_entity_footprint](#252-2_place_containment-1_entity_ne_admin-and-8_entity_footprint)
    - [2.6 5_tradition files](#26-5_tradition-files)
    - [2.7 6_genealogy files](#27-6_genealogy-files)
    - [2.8 7_lane_order.parquet and 7_band_fragment.parquet](#28-7_lane_orderparquet-and-7_band_fragmentparquet)

## 1. Bronze

### 1.1 glottolog_languoid.parquet

One row per Glottolog languoid (family, language or dialect) of the pinned CLDF release
(`GLOTTOLOG_CLDF_VERSION`), from `cldf/languages.csv` joined to the `classification` and `category` parameters
of `cldf/values.csv`. `glottocode` is unique (checked before the write). Release v5.3: 27,177 rows (4,853
families, 8,582 languages, 13,705 dialects).

| Column                      | Type | Meaning                                                                                      |
| --------------------------- | ---- | -------------------------------------------------------------------------------------------- |
| glottocode                  | str  | Glottolog identifier (e.g. `bret1244`)                                                       |
| name                        | str  | Languoid name                                                                                |
| level                       | str  | `family`, `language` or `dialect`                                                            |
| category                    | str  | Glottolog category (e.g. `Spoken_L1_Language`, `Sign_Language`, `Unattested`)                |
| classification              | str  | Raw ancestor path `a/b/c` of glottocodes, root first; the last element is the direct parent  |
| latitude                    | str? | Latitude, as published                                                                       |
| longitude                   | str? | Longitude, as published                                                                      |
| macroarea                   | str? | Glottolog macroarea (e.g. `Eurasia`)                                                         |
| iso639_3                    | str? | ISO 639-3 code                                                                               |
| first_year_of_documentation | str? | First year of documentation, as published                                                    |
| last_year_of_documentation  | str? | Last year of documentation, as published                                                     |
| glottolog_version           | str  | Release the row comes from (e.g. `v5.3`)                                                     |

### 1.2 natural_earth_admin.parquet

One row per Natural Earth 1:10m admin-0 country (`ne_10m_admin_0_countries`) or admin-1 unit
(`ne_10m_admin_1_states_provinces`) of the pinned release (`NATURAL_EARTH_VERSION`). `ne_id` is unique, and every
admin-1 `adm0_a3` exists at level 0 (both checked before the write). Release v5.1.2: 4,854 rows (258 admin-0,
4,596 admin-1, 264 of them without `wikidata_id`; 28 admin-1 QIDs are shared by several units).

| Column       | Type | Meaning                                                                                 |
| ------------ | ---- | --------------------------------------------------------------------------------------- |
| ne_id        | str  | `ADM0_A3` for admin-0, `adm1_code` for admin-1 (e.g. `FRA`, `FRA-5327`)                 |
| level        | i8   | `0` (country) or `1` (first-level subdivision)                                          |
| wikidata_id  | str? | Wikidata QID as published (`WIKIDATAID` / `wikidataid`)                                 |
| adm0_a3      | str  | Country the unit belongs to; equals `ne_id` at level 0                                  |
| name         | str  | Natural Earth name (e.g. `Morbihan`)                                                    |
| iso_code     | str  | `ISO_A2_EH` (`-99` when none) for admin-0, `iso_3166_2` for admin-1                     |
| ne_type      | str? | `TYPE` (e.g. `Sovereign country`, `Dependency`) for admin-0, `type_en` for admin-1      |
| geometry_wkb | bin  | Full-resolution `Polygon` or `MultiPolygon`, WKB, WGS 84                                |

## 2. Silver

### 2.1 1_cultural_entity.parquet

One row per cultural entity. `entity_id` is unique. Profile on the real Bronze: 32,812 rows.

| Column            | Type | Meaning                                                                                                     |
| ----------------- | ---- | ----------------------------------------------------------------------------------------------------------- |
| entity_id         | str  | Glottocode for `family`/`language`/`dialect`, Wikidata QID for the Wikidata kinds below |
| kind              | str  | `family`, `language`, `dialect`, `archaeological_culture`, `civilization`, `historical_country`, `country`, `historical_region`, `ethnic_group`, `cultural_region`, `macro_region` or `admin_unit` |
| label             | str  | Glottolog name or Wikidata label                                                                            |
| latitude          | f64? | Latitude                                                                                                    |
| longitude         | f64? | Longitude                                                                                                   |
| coordinate_source | str? | `glottolog` or `wikidata`; null when no coordinates                                                         |
| macroarea         | str? | Glottolog macroarea; null for Wikidata-only entities                                                        |

### 2.2 1_entity_wikidata_item.parquet

Crosswalk entity ↔ Wikidata item, many-to-many. Profile: 17,587 rows.

| Column      | Type | Meaning                           |
| ----------- | ---- | --------------------------------- |
| entity_id   | str  | `1_cultural_entity.entity_id`     |
| wikidata_id | str  | Wikidata QID attached to the entity |

### 2.3 2_entity_membership.parquet

Direct parent edges of the membership DAG (child belongs to parent). `(child_id, parent_id)` is unique. Profile:
26,378 rows (26,039 `glottolog`, 339 `wikidata_p361`).

| Column    | Type | Meaning                                  |
| --------- | ---- | ---------------------------------------- |
| child_id  | str  | `1_cultural_entity.entity_id`            |
| parent_id | str  | `1_cultural_entity.entity_id`            |
| source    | str  | `glottolog`, `wikidata_p361` or `curation` |

### 2.4 3_entity_succession.parquet

Succession edges. `(predecessor_id, successor_id)` is unique. Profile: 1,410 rows.

| Column         | Type | Meaning                                                              |
| -------------- | ---- | -------------------------------------------------------------------- |
| predecessor_id | str  | `1_cultural_entity.entity_id`                                        |
| successor_id   | str  | `1_cultural_entity.entity_id`                                        |
| source         | str  | Wikidata properties asserting the edge: `P1365`, `P1366` or `P1365,P1366` |

### 2.5 4_entity_time_span.parquet

One row per entity of `1_cultural_entity`. Years are astronomical-free Wikidata years: BCE negative, no year 0.
Profile: 32,812 rows; `start_source` is null for 28,568, `P571` 3,497, `P580` 431, `glottolog` 203, `period` 113.

| Column          | Type | Meaning                                                                         |
| --------------- | ---- | ------------------------------------------------------------------------------- |
| entity_id       | str  | `1_cultural_entity.entity_id`                                                   |
| start_year      | i32? | Start year                                                                      |
| start_precision | i8?  | Wikidata time precision of the start; null for Glottolog                        |
| start_source    | str? | `P571`, `P580`, `period`, `glottolog` or `curation`                            |
| end_year        | i32? | End year; null = no end known (alive)                                           |
| end_precision   | i8?  | Wikidata time precision of the end; null for Glottolog                          |
| end_source      | str? | `P576`, `P582`, `period`, `glottolog` or `curation`                            |

### 2.5.2 2_place_containment, 1_entity_ne_admin and 8_entity_footprint

`1_entity_ne_admin.parquet`: one row per Natural Earth polygon, `ne_id` unique.

| Column            | Type | Meaning                                                                    |
| ----------------- | ---- | -------------------------------------------------------------------------- |
| ne_id             | str  | `natural_earth_admin.ne_id`                                                |
| level             | i8   | 0 country, 1 admin-1                                                       |
| entity_id         | str? | Entity the polygon stands for; null when none (excluded or unnamed)        |
| country_entity_id | str? | Entity of the polygon's country                                            |

`2_place_containment.parquet`: direct container edges between place entities, `(parent_id, child_id)` unique.

| Column    | Type | Meaning                                                  |
| --------- | ---- | -------------------------------------------------------- |
| parent_id | str  | `1_cultural_entity.entity_id`                            |
| child_id  | str  | `1_cultural_entity.entity_id`                            |
| source    | str  | `p131`, `p361`, `p30`, `p706` or `natural_earth`         |

`8_entity_footprint.parquet`: the admin units each entity covers, `(entity_id, admin_id)` unique.

| Column    | Type | Meaning                                                                       |
| --------- | ---- | ----------------------------------------------------------------------------- |
| entity_id | str  | `1_cultural_entity.entity_id`                                                 |
| admin_id  | str  | `1_cultural_entity.entity_id` of kind `admin_unit`                            |
| rule      | str  | `self`, `cover` (contained admin units), `succession` (a successor's units) or `point` (admin unit intersecting its coordinates) |

`8_entity_geometry.parquet`: the map geometry of every country, admin unit and zone-kind entity with a footprint, `entity_id` unique.

| Column       | Type      | Meaning                                                                          |
| ------------ | --------- | -------------------------------------------------------------------------------- |
| entity_id    | str       | `1_cultural_entity.entity_id`                                                    |
| geometry_wkb | binary    | WGS 84 (Multi)Polygon, full Natural Earth resolution                             |
| source       | str       | `natural_earth` (its own polygon) or `footprint` (union of its admin units)      |
| bbox         | list[f64] | `[minx, miny, maxx, maxy]` of the whole geometry                                 |
| main_bbox    | list[f64] | Same for the largest polygon part (mainland, without remote islands)             |
| centroid     | list[f64] | `[x, y]` centroid of the largest polygon part                                    |

### 2.5.1 5_music_anchor files

`5_music_anchor.parquet`: one row per music genre or "music of X" overview that resolved to a place or culture entity,
dated or not.

| Column      | Type | Meaning                                                                                              |
| ----------- | ---- | ---------------------------------------------------------------------------------------------------- |
| wikidata_id | str  | Wikidata QID                                                                                         |
| anchor_id   | str  | `1_cultural_entity.entity_id` the item is placed on                                                  |
| rule        | str  | `override`, `P2596`, `P1269`, `P495`, `P17`, `P276`, `P921`, `P361` or `label`                       |
| is_overview | bool | The label starts with "music of "                                                                    |

`5_music_anchor_unplaced.csv`: `wikidata_id`, `label`, `candidate_ids` (the `|`-joined entities an ambiguous label
matched, empty when none) of the overviews without anchor.

### 2.6 5_tradition files

`5_tradition.parquet`: one row per music tradition (genre or "music of X" overview starting by 1950, plus additions).

| Column      | Type | Meaning                                                                              |
| ----------- | ---- | ------------------------------------------------------------------------------------ |
| wikidata_id | str  | Wikidata QID                                                                         |
| label       | str  | Wikidata label                                                                       |
| start_year  | i32? | Start year                                                                           |
| end_year    | i32? | End year, capped at 1950                                                             |
| anchor_id   | str? | `1_cultural_entity.entity_id` the tradition is placed on; null = unplaced            |
| anchor_rule | str? | `5_music_anchor.rule` of the anchor (see 2.5.1); null when unplaced                   |

`5_tradition_link.parquet`: `from_wikidata_id` (str), `to_wikidata_id` (str), `kind` (`lineage` or `association`);
`from` is the earlier or influencing tradition. `5_tradition_unplaced.csv`: `wikidata_id`, `label`, `start_year` of
traditions without anchor. `5_tradition_report.json`: `{"unplacedTraditionCount": N}`.

### 2.7 6_genealogy files

`6_genealogy_lane.parquet`: one row per lane (an entity anchoring a tradition and having no anchored descendant).

| Column     | Type | Meaning                                                                            |
| ---------- | ---- | ---------------------------------------------------------------------------------- |
| entity_id  | str  | `1_cultural_entity.entity_id`                                                      |
| label      | str  | Entity label                                                                       |
| column_id  | str  | Column the lane sits in; successive lanes without time overlap share a column      |
| start_year | i32? | Start year (entity span, else envelope of its traditions)                          |
| end_year   | i32  | End year, capped at 1950                                                           |

`6_genealogy_band.parquet`: one row per (band, column) with the same columns as the lane file; a band is an ancestor
covering at least two lanes, or an anchored entity having anchored descendants.

`6_genealogy_tradition_lane.parquet`: `wikidata_id` (str), `lane_id` (str) — one row per anchored tradition, `lane_id`
being its anchor lifted to a non-admin place (an `entity_id` of the lane or band files); `gold` uses it as `anchorId`.

### 2.8 7_lane_order.parquet and 7_band_fragment.parquet

`7_lane_order.parquet`: `column_id` (str), `position` (i32, 0 = west). `7_band_fragment.parquet`: one row per
contiguous run of a band's columns: `entity_id`, `label`, `fragment` (i32), `start_position`, `end_position` (i32,
inclusive), `start_year`, `end_year`.
