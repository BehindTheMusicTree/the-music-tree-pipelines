# Schema

Data dictionary for `geohistory`. Design rationale is in [DESIGN.md](DESIGN.md).

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [1. Bronze](#1-bronze)
    - [1.1 glottolog_languoid.parquet](#11-glottolog_languoidparquet)
  - [2. Silver](#2-silver)
    - [2.1 1_cultural_entity.parquet](#21-1_cultural_entityparquet)
    - [2.2 1_entity_wikidata_item.parquet](#22-1_entity_wikidata_itemparquet)
    - [2.3 2_entity_membership.parquet](#23-2_entity_membershipparquet)
    - [2.4 3_entity_succession.parquet](#24-3_entity_successionparquet)
    - [2.5 4_entity_time_span.parquet](#25-4_entity_time_spanparquet)

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

## 2. Silver

### 2.1 1_cultural_entity.parquet

One row per cultural entity. `entity_id` is unique. Profile on the real Bronze: 32,812 rows.

| Column            | Type | Meaning                                                                                                     |
| ----------------- | ---- | ----------------------------------------------------------------------------------------------------------- |
| entity_id         | str  | Glottocode for `family`/`language`/`dialect`, Wikidata QID for the Wikidata kinds below |
| kind              | str  | `family`, `language`, `dialect`, `archaeological_culture`, `civilization`, `historical_country`, `country`, `historical_region`, `ethnic_group`, `cultural_region` or `macro_region` |
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
