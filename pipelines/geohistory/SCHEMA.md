# Schema

Data dictionary for `geohistory`. Design rationale is in [DESIGN.md](DESIGN.md).

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [1. Bronze](#1-bronze)
    - [1.1 glottolog_languoid.parquet](#11-glottolog_languoidparquet)

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
