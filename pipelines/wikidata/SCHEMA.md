# Schema

Data dictionary and lineage notes for `wikidata`. See [DESIGN.md](DESIGN.md) for the rationale and
rules behind each step, and [README.md#pipeline](README.md#pipeline) for the top-to-bottom
pipeline overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [1. Wikidata properties used](#1-wikidata-properties-used)
    - [1.1 P31 ("instance of")](#11-p31-instance-of)
    - [1.2 P279 ("subclass of")](#12-p279-subclass-of)
    - [1.3 P361 ("part of")](#13-p361-part-of)
    - [1.4 P2341 ("indigenous to")](#14-p2341-indigenous-to)
    - [1.5 P495 ("country of origin")](#15-p495-country-of-origin)
  - [2. Bronze](#2-bronze)
    - [2.1 wikidata_genre_tree.parquet](#21-wikidata_genre_treeparquet)
    - [2.2 wikidata_genre_indigenous_to.parquet](#22-wikidata_genre_indigenous_toparquet)
    - [2.3 wikidata_genre_country_of_origin.parquet](#23-wikidata_genre_country_of_originparquet)
    - [2.4 wikidata_genre_descriptions.parquet](#24-wikidata_genre_descriptionsparquet)
    - [2.4.1 wikidata_genre_aliases.parquet](#241-wikidata_genre_aliasesparquet)
    - [2.5 wikidata_cultural_entity.parquet](#25-wikidata_cultural_entityparquet)
    - [2.6 wikidata_cultural_entity_claim.parquet](#26-wikidata_cultural_entity_claimparquet)
    - [2.7 wikidata_music_tradition_claims.parquet](#27-wikidata_music_tradition_claimsparquet)
    - [2.8 wikidata_artist_images.parquet](#28-wikidata_artist_imagesparquet)
    - [2.9 wikidata_admin_hierarchy.parquet](#29-wikidata_admin_hierarchyparquet)
    - [2.10 wikidata_cultural_entity_alias.parquet](#210-wikidata_cultural_entity_aliasparquet)
  - [3. Silver](#3-silver)
    - [3.1 Overview](#31-overview)
    - [3.2 1_item_links](#32-1_item_links)
    - [3.3 2_non_genre_pruning](#33-2_non_genre_pruning)
    - [3.4 3_regional_overview_classification](#34-3_regional_overview_classification)
    - [3.5 4_regional_classification](#35-4_regional_classification)
    - [3.6 5_main_parent_selection](#36-5_main_parent_selection)
    - [3.7 6_canonical_parents](#37-6_canonical_parents)
    - [3.8 7_canonical_hierarchy](#38-7_canonical_hierarchy)
    - [3.9 8_regional_hierarchy](#39-8_regional_hierarchy)
    - [3.10 9_canonical_roots](#310-9_canonical_roots)
    - [3.11 10_commons_image_metadata](#311-10_commons_image_metadata)
    - [3.12 11_artist_images](#312-11_artist_images)

## 1. Wikidata properties used

Wikidata models knowledge as items (`Q...` IDs) connected by properties (`P...` IDs). Five
properties drive this pipeline's whole shape.

The three taxonomy properties (`P31`, `P279`, `P361`) are not interchangeable and don't chain into
each other the way you might expect — see [DESIGN.md#1-bronze](DESIGN.md#1-bronze). `P2341` and
`P495` are separate, orthogonal kinds of edges (item-to-people and item-to-country, not
item-to-item taxonomy) and aren't part of that chaining discussion.

### 1.1 P31 ("instance of")

Links an item to the class it directly belongs to. `wd:Q11399` ("rock music") `wdt:P31`
`wd:Q188451` ("music genre") means "rock music is a music genre." This is Wikidata's
class-membership edge — it's how Bronze finds the full set of genre items in the first place (see
`GENRE_TREE_QUERY`'s `?item wdt:P31 wd:Q188451` clause).

### 1.2 P279 ("subclass of")

Links a class to its more general parent class(es), building a taxonomy. `wd:Q11399` ("rock
music") `wdt:P279` `wd:Q373342` ("popular music") means "rock music is a kind of popular music."
This is the main edge that builds the genre _hierarchy_ — a genre can have more than one `P279`
parent, since Wikidata classes aren't a strict tree.

### 1.3 P361 ("part of")

A meronymic (part-whole, not is-a) edge, used inconsistently across genre items in place of or
alongside `P279` for what is still, in practice, subgenre-of-genre information. It's sparser than
`P279` (~250 edges vs. ~9,000) and noisier (most `P361` targets aren't themselves a `P31` music
genre — e.g. "punk subculture"), but a meaningful minority of edges are hierarchy information
`P279` doesn't have at all — e.g. several juke/footwork/ghetto house subgenres are only linked to
their parent via `P361`. Bronze ingests the full `P361` edge set raw and unfiltered, just as it
already does for `P279`, tagged by `relation_type` (see below) so consumers can tell the two edge
types apart rather than silently merging two different semantics into one column.

### 1.4 P2341 ("indigenous to")

Links an item to the people/ethnic group it originates from (e.g. `wd:Q10376827` "Han Chinese
music" `wdt:P2341` `wd:Q49103` "Han Chinese"). This is an ethnographic attribute of the item
itself, not a genre-to-genre taxonomy edge like `P279`/`P361` — its cardinality is independent of
an item's parent count, so it's ingested into its own Bronze table
(`wikidata_genre_indigenous_to.parquet`, see below) rather than into `wikidata_genre_tree.parquet`.
See [DESIGN.md#23-4_regional_classification](DESIGN.md#23-4_regional_classification) for why it's
needed.

### 1.5 P495 ("country of origin")

Links an item to the country it originated in (e.g. `wd:Q1198131` "morna" `wdt:P495` `wd:Q1011`
"Cape Verde"). Same shape as `P2341` above: a per-item attribute, not a genre-to-genre taxonomy
edge, independent of an item's `P279`/`P361` parent count, so it's ingested into its own Bronze
table (`wikidata_genre_country_of_origin.parquet`, see below) rather than into
`wikidata_genre_tree.parquet`. See
[DESIGN.md#23-4_regional_classification](DESIGN.md#23-4_regional_classification) for why it's not
used as a classification signal.

## 2. Bronze

Four Parquet files, one per query in `wikidata_client.py`. See
[DESIGN.md#1-bronze](DESIGN.md#1-bronze) for why `P31` (not a `P279*` walk) is the root query, why
parents aren't restricted to genre items, and why `P2341`/`P495` are ingested into their own tables
instead of `wikidata_genre_tree.parquet`.

### 2.1 wikidata_genre_tree.parquet

One row per (item, parent, relation_type) edge: every Wikidata item classified `P31` ("instance
of") `Q188451` ("music genre") — the class extension, ~6,300 items as of this writing — plus each
genre's direct `P279` ("subclass of") and `P361` ("part of") parent(s). See
`wikidata_client.GENRE_TREE_QUERY` for the exact SPARQL.

An item with neither a `P279` nor a `P361` parent (a root, ~486 of them as of this writing — down
from ~510 pre-`P361`, since 22 formerly-root items turned out to have only a `P361` parent) gets a
single row with `parent_id`/`parent_label`/`relation_type` all null.

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | ---------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`)                                        |
| item_label    | str  | Label for `item_id` (English, falling back to a language-agnostic `mul` label if no English label exists) (e.g. "rock music")                                  |
| parent_id     | str? | QID of a direct `P279`/`P361` parent within the genre tree, or null              |
| parent_label  | str? | Same fallback as `item_label`, for `parent_id`, or null                                           |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or null for a root row |

A multi-parent item (Wikidata classes aren't a strict tree — a genre can have more than one
`P279`/`P361` parent) produces one row per parent, so `item_id` is not unique on its own.

### 2.2 wikidata_genre_indigenous_to.parquet

One row per (item, indigenous-to-group) pair: every `P31` music genre item that also has at least
one `P2341` ("indigenous to") value. Unlike `wikidata_genre_tree.parquet`, items with no `P2341`
value are absent entirely — there is no "root row" placeholder, since absence of an ethnographic
tag isn't a hierarchy position the way a missing parent is. See
`wikidata_client.INDIGENOUS_TO_QUERY`.

| Column               | Type | Meaning                                                             |
| -------------------- | ---- | ---------------------------------------------------------------------- |
| item_id               | str  | Wikidata QID of the genre (e.g. `Q10376827`)                        |
| indigenous_to_id      | str  | Wikidata QID of the people/ethnic group (e.g. `Q49103`)              |
| indigenous_to_label   | str  | Same English/`mul`-fallback label as `item_label`, for `indigenous_to_id` (e.g. "Han Chinese")            |

A genre with several `P2341` values produces one row per value, so `item_id` is not unique on its
own (as of this writing: 207 rows).

### 2.3 wikidata_genre_country_of_origin.parquet

One row per (item, country) pair: every `P31` music genre item that also has at least one `P495`
("country of origin") value. Same absence rule as `wikidata_genre_indigenous_to.parquet` — items
with no `P495` value are absent entirely, no "root row" placeholder. See
`wikidata_client.COUNTRY_OF_ORIGIN_QUERY`.

| Column                  | Type | Meaning                                                        |
| ------------------------ | ---- | ----------------------------------------------------------------- |
| item_id                  | str  | Wikidata QID of the genre (e.g. `Q1198131`)                    |
| country_of_origin_id     | str  | Wikidata QID of the country (e.g. `Q1011`)                     |
| country_of_origin_label  | str  | Same English/`mul`-fallback label as `item_label`, for `country_of_origin_id` (e.g. "Cape Verde")   |

A genre with several `P495` values produces one row per value, so `item_id` is not unique on its
own (as of this writing: 2,496 rows).

### 2.4 wikidata_genre_descriptions.parquet

One row per tree QID: every `item_id` and `parent_id` of `wikidata_genre_tree.parquet` plus every
`Q…` `item_id` of the curation Bronze `manual_regional_overview_additions.csv` (`CURATION_BRONZE_DIR`;
`LOCAL:` ids have no Wikidata item and get no row), so `item_id` is unique (checked before the write).
The English short description (CC0) and the English Wikipedia article come from `wbgetentities`
(`wikidata_client.fetch_descriptions`, `props=descriptions|aliases|sitelinks/urls`, `sitefilter=enwiki`); the
intro comes from the English Wikipedia `action=query&prop=extracts&exintro&explaintext` API
(`wikipedia_client.fetch_intros`, batches of 20, redirects and `continue` followed; text is CC BY-SA,
hence `wikipedia_url` for attribution). See [DESIGN.md](DESIGN.md#13-wikidata_genre_descriptionsparquet)
for why the row scope is the tree's QIDs.

| Column          | Type | Meaning                                                                 |
| --------------- | ---- | ----------------------------------------------------------------------- |
| item_id         | str  | Wikidata QID of a tree node (e.g. `Q11399`)                             |
| description     | str? | English Wikidata short description, or null                             |
| wikipedia_url   | str? | URL of the English Wikipedia article, or null                           |
| wikipedia_intro | str? | Plain-text intro of that article, or null (no article or empty intro)   |

Read only by `gold` (`WIKIDATA_BRONZE_DIR`), not by Silver.

### 2.4.1 wikidata_genre_aliases.parquet

The English aliases (`skos:altLabel`) of the same QIDs, from the same `wbgetentities` calls; one row per
`(item_id, alias)` (unique, checked before the write), long format.

| Column  | Type | Meaning                                         |
| ------- | ---- | ----------------------------------------------- |
| item_id | str  | Wikidata QID of a tree node (e.g. `Q11399`)     |
| alias   | str  | One English Wikidata alias of that item         |

Read only by `gold` (`WIKIDATA_BRONZE_DIR`, `genre_match`'s `wikidata_alias` step), not by Silver.

### 2.5 wikidata_cultural_entity.parquet

One row per (item, kind, Glottocode): every item that is a cultural entity of one of three kinds. An
item can appear under several kinds, and under several Glottocodes (e.g. Limburgish `limb1263` /
`limb1265`), so the unique key is `item_id|kind|glottocode`. Ethnic groups (~80k) are out of scope. See
`wikidata_client.cultural_entity_query`.

| Column     | Type | Meaning                                                                                              |
| ---------- | ---- | ---------------------------------------------------------------------------------------------------- |
| item_id    | str  | Wikidata QID (e.g. `Q12107`)                                                                         |
| item_label | str  | English/`mul`-fallback label                                                                         |
| kind       | str  | `languoid` (has `P1394`), `archaeological_culture` (`P31/P279*` `Q465299`), `historical_country` (`P31/P279*` `Q3024240`), `country` (`P31/P279*` `Q6256`), `cultural_region` (`P31/P279*` `Q3502482`), `civilization` (`P31/P279*` `Q8432`), `historical_region` (`P31/P279*` `Q1620908`) or `ethnic_group` (`P31/P279*` `Q41710`), the last two only with at least 30 sitelinks, `macro_region` (an item that at least two countries or historical countries are `P361`/`P706`/`P30` of), `curated` (a QID listed in the `entity_additions` curation list) or `time_period` (the `P2348` targets of the archaeological cultures, civilizations, countries, historical countries and cultural regions, a date fallback and no entity of its own) |
| glottocode | str? | `P1394` Glottocode for `languoid` rows, null otherwise                                               |

As of this writing: 24,864 rows (languoid 11,925, historical_country 5,026, historical_region 4,202, archaeological_culture 1,325, ethnic_group 987, country 673, cultural_region 323, macro_region 181, time_period 113, civilization 109).

### 2.6 wikidata_cultural_entity_claim.parquet

One row per (item, property, value) claim of a cultural entity of any kind, one query per (kind ×
property). Deprecated statements are excluded. See `wikidata_client.cultural_entity_claim_query`.

| Column         | Type | Meaning                                                                                                               |
| -------------- | ---- | --------------------------------------------------------------------------------------------------------------------- |
| item_id        | str  | Wikidata QID of the cultural entity                                                                                   |
| property       | str  | One of `P571` inception, `P576` dissolved, `P580` start time, `P582` end time, `P2348` time period, `P1365` replaces, `P1366` replaced by, `P361` part of, `P30` continent, `P706` located in/on physical feature, `P625` coordinate location |
| value          | str  | Date properties: raw ISO literal kept as is, BCE years included (e.g. `-3500-01-01T00:00:00Z`); other properties: the QID (or the coordinate literal for `P625`) |
| time_precision | i8?  | `wikibase:timePrecision` for date properties (7 century, 8 decade, 9 year, …), null otherwise                         |

As of this writing: 16,596 rows.

### 2.7 wikidata_music_tradition_claims.parquet

Raw claims of the music-tradition candidates, one row per (item, property, value), unfiltered by date (the ≤ 1950 rule
is semantic and applied in `geohistory` Silver). Candidates are every `P31` music genre item (`Q188451`), the "music
of <place>" overviews the genre tree only knows as parents, the `manual_regional_overview_additions.csv` QIDs and the
`manual_tradition_additions.csv` QIDs. Deduplicated.

| Column         | Type | Meaning                                                                                                                      |
| -------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------- |
| item_id        | str  | Wikidata QID of the candidate                                                                                                |
| property       | str  | `P571` inception, `P580` start time, `P582` end time, `P576` dissolved, `P2596` culture, `P1269` facet of, `P495` country of origin, `P17` country, `P276` location, `P921` main subject, `P361` part of, `P155` follows, `P156` followed by, `P737` influenced by, or the pseudo-properties `sitelinks` and `label` |
| value          | str  | Date properties: raw ISO literal as is, BCE years included; `sitelinks`: the count; `label`: the label; others: the QID      |
| time_precision | i8?  | `wikibase:timePrecision` for date properties, null otherwise                                                                 |

### 2.8 wikidata_artist_images.parquet

Every Wikidata item with both a `P434` MusicBrainz artist ID and a `P18` image, one row per (item, MBID, image).
`wdt:` keeps best-rank statements only. Queried in 16 partitions on the MBID's first hex digit to stay under WDQS's
60 s timeout. Rows whose MBID isn't a lowercase UUID after lowercasing are dropped with a WARNING (Wikidata doesn't
enforce `P434`'s format). Deduplicated.

| Column                | Type | Meaning                                                                               |
| --------------------- | ---- | ------------------------------------------------------------------------------------- |
| item_id               | str  | Wikidata QID of the artist item                                                       |
| musicbrainz_artist_id | str  | `P434`, lowercased                                                                    |
| image_file            | str  | `P18` Commons file name, URL-decoded from its `Special:FilePath/` URI, without `File:` |

As of this writing: about 8,400 rows in partition `0`, so about 135,000 in total.

### 2.9 wikidata_admin_hierarchy.parquet

The administrative ancestry of the Natural Earth admin-0/admin-1 units: one row per (item, property, value) claim of
every item on a seed's `P131*` chain, seeds being the distinct `wikidata_id`s of `geohistory` Bronze's
`natural_earth_admin.parquet` (`GEOHISTORY_BRONZE_DIR`), queried in chunks of 400. See
`wikidata_client.admin_hierarchy_query`.

| Column   | Type | Meaning                                                                  |
| -------- | ---- | ------------------------------------------------------------------------ |
| item_id  | str  | Wikidata QID of a seed or one of its `P131` ancestors (e.g. `Q12642`)    |
| property | str  | `P131` located in the administrative territorial entity or `P361` part of |
| value    | str  | QID of the parent (e.g. `Q12130`)                                        |
| label    | str  | English/`mul`-fallback label of `item_id`                                |

As of this writing: 6,292 rows (P131 5,001, P361 1,291).

### 2.10 wikidata_cultural_entity_alias.parquet

One row per (item, alias): the English Wikidata aliases (`skos:altLabel`) of every cultural entity of
`wikidata_cultural_entity.parquet`, one query per kind. See `wikidata_client.cultural_entity_alias_query`.

| Column  | Type | Meaning                                       |
| ------- | ---- | --------------------------------------------- |
| item_id | str  | Wikidata QID of the cultural entity           |
| alias   | str  | One English alias of that item                |

As of this writing: 32,737 rows.

## 3. Silver

All nine genre steps below are produced by `wikidata.silver`, followed by the two artist image steps (§3.11, §3.12). `1_item_links` preserves the Bronze
edge-list grain 1:1 (`item_id` still not unique) — it doesn't drop rows; downstream consumers
filter on the added columns themselves. `2_non_genre_pruning` is the step that drops rows, via
manual CSV backstops (theme/technique/out-of-scope items — see
[DESIGN.md#21-2_non_genre_pruning](DESIGN.md#21-2_non_genre_pruning)), run as early as possible so
every step downstream works with a smaller, cleaner tree. `3_regional_overview_classification` and
`4_regional_classification` preserve whatever grain they receive unchanged aside from their own
added columns (`4_regional_classification` also injects one synthetic edge per
`manual_main_parent.csv` row — see below). `5_main_parent_selection` is the step that collapses
each item down to exactly one main-parent edge, splitting every other candidate parent edge off
into a sibling `5_secondary_parents.parquet` rather than dropping it; `6_canonical_parents`
preserves that one-row-per-item grain unchanged aside from its own added column.
`7_canonical_hierarchy` and `8_regional_hierarchy` are the steps that prune to the final hierarchy
shape — see below.

### 3.1 Overview

| Step                                                                        | Reads                                        | Writes                                                                       | Adds                                | Key result (as of this writing)                                                                                                                                                 |
| --------------------------------------------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`1_item_links`](#32-1_item_links)                                          | Bronze `wikidata_genre_tree.parquet`, `manual_label_overrides.csv`, `manual_capitalized_words.csv` | `1_item_links.parquet`                                                       | `item_url`, `parent_url`, `has_item_label`, `has_parent_label`, `item_display_label`, `parent_display_label` | `item_url` populated for all 9,729 rows; `parent_url` null only for the 486 root rows                                                                                            |
| [`2_non_genre_pruning`](#33-2_non_genre_pruning)                            | `1_item_links.parquet`, `manual_theme_genres.csv`, `manual_technique_genres.csv`, `manual_out_of_scope_genres.csv` | `2_non_genre_pruning.parquet`                                                | none — drops rows only              | theme/technique/out-of-scope items dropped entirely (see [DESIGN.md#21-2_non_genre_pruning](DESIGN.md#21-2_non_genre_pruning))                                                    |
| [`3_regional_overview_classification`](#34-3_regional_overview_classification) | `2_non_genre_pruning.parquet`                | `3_regional_overview_classification.parquet`                                 | `is_regional_overview`, `classification_reason` | 401 of 9,729 rows (299 of 6,344 items) tagged `is_regional_overview = true` / `regional_overview` (e.g. "music of Kenya") — not dropped                                          |
| [`4_regional_classification`](#35-4_regional_classification)                | `3_regional_overview_classification.parquet`, Bronze `wikidata_genre_indigenous_to.parquet`, `manual_regional_overrides.csv`, `manual_main_parent.csv` | `4_regional_classification.parquet`                                          | `is_regional`, `regional_reason`    | 3,879 of 6,404 remaining items flagged `is_regional` — 359 seed, 179 indigenous_to, 180 manual_override, 1,614 direct, 1,547 inherited (see [DESIGN.md#23-4_regional_classification](DESIGN.md#23-4_regional_classification))                                      |
| [`5_main_parent_selection`](#36-5_main_parent_selection)                    | `4_regional_classification.parquet`          | `5_main_parent_selection.parquet`, `5_secondary_parents.parquet`             | none — selects/splits rows only     | collapses every item to exactly one main-parent edge (manual override wins, else lowest-QID); all other candidate edges kept in `5_secondary_parents.parquet` |
| [`6_canonical_parents`](#37-6_canonical_parents)                            | `5_main_parent_selection.parquet`            | `6_canonical_parents.parquet`                                                | `parent_is_canonical`               | flags whether each item's chosen main parent is itself an actual musical style |
| [`7_canonical_hierarchy`](#38-7_canonical_hierarchy)                        | `6_canonical_parents.parquet`                | `7_canonical_hierarchy.parquet`                                              | prunes to one row per `item_id` (canonical only) | clean, one-parent-per-item canonical edge list |
| [`8_regional_hierarchy`](#39-8_regional_hierarchy)                          | `6_canonical_parents.parquet`                | `8_regional_hierarchy.parquet`                                               | prunes to one row per `item_id` (regional only) | clean, one-parent-per-item regional edge list |
| [`9_canonical_roots`](#310-9_canonical_roots)                               | `7_canonical_hierarchy.parquet`, `manual_accepted_canonical_roots.csv` | `9_canonical_roots.parquet`                                                  | filters to root items, `is_accepted` | for manual exploration of the "too many roots" open question (see [DESIGN.md#263-under-exploration--root-count](DESIGN.md#263-under-exploration--root-count)) |

Each step's own section below has the full column definitions and profiling detail behind these
numbers; see [DESIGN.md](DESIGN.md) for why each step exists and its rules.

### 3.1.1 curation_resolutions.json

Written first, every Silver run (possibly empty). `{"remaps": [{list, key, column, from, to}], "review": [{list, key, column, itemId, reason, target?, evidence?}]}`, validated against `src/wikidata/schemas/curation_resolutions.schema.json`. `list` is the grow curation list name, `key` the row's `item_id`, `reason` one of `redirect_conflict`, `missing`, `not_in_tree`. `evidence` (optional, never null-valued keys, omitted for `missing`) holds the facts behind the reason: `label`, `modified` (last Wikidata edit, ISO datetime) of the resolved entity (the redirect target for a merged item), plus for `not_in_tree` `instanceOf`/`subclassOf` (`[{id, label|null}]`, current truthy P31/P279: preferred-rank statements if any, else normal ones, as Bronze's `wdt:` sees them) and, for `indigenous_to_exclusions`, also `hasIndigenousTo` (bool), and for `redirect_conflict` `conflictList` (the curation list holding the colliding entry). See DESIGN.md#200-curation-qid-resolution.

### 3.1.2 curation_items.json

Written every Silver run, right after `curation_resolutions.json`. `{"items": [{id, label}]}`: every item of `wikidata_genre_tree.parquet` (`item_id` and `parent_id`, so items pruned later are included) with its Bronze label, one entry per id, sorted by id; items whose label is only the QID fallback are left out. grow-the-music-tree-api stores it as the catalog that names curation item ids outside the canonical tree.

### 3.2 1_item_links

`1_item_links.parquet`: `wikidata_genre_tree.parquet` (Bronze) unchanged, plus two columns giving
the human-browsable Wikidata page for `item_id` and, where present, `parent_id`, two columns
flagging whether `item_label`/`parent_label` are a real label or the QID-fallback string, and two
display-only label columns (see [DESIGN.md#20-1_item_links--display-label-casing](DESIGN.md#20-1_item_links--display-label-casing)).

| Column                | Type | Meaning                                                                            |
| --------------------- | ---- | ----------------------------------------------------------------------------------- |
| item_url              | str  | `https://www.wikidata.org/wiki/` + `item_id` — the item's browsable Wikidata page   |
| parent_url            | str? | `https://www.wikidata.org/wiki/` + `parent_id`, or null when `parent_id` is null    |
| has_item_label        | bool | `False` when `item_label == item_id` (Wikidata's label service found no English/`mul` label and fell back to printing the QID) |
| has_parent_label      | bool? | Same check for `parent_label`/`parent_id`, or null when `parent_id` is null       |
| item_display_label    | str  | Display-only genre name: `manual_label_overrides.csv` override, else `item_label` sentence-cased (word-list capitalization from `manual_capitalized_words.csv`, then first-character capitalization) |
| parent_display_label  | str? | Same derivation for `parent_label`, or null when `parent_id` is null              |

**Data profile (as of this writing):**

| Metric                                     |  Rows | Distinct `item_id`s |
| ------------------------------------------- | ----: | -------------------: |
| Total                                       | 9,729 |                6,344 |
| `parent_url` populated                      | 9,243 |                    — |
| `parent_url` null (root, `parent_id` null)  |   486 |                    — |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/1_item_links.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 3.3 2_non_genre_pruning

`2_non_genre_pruning.parquet`: `1_item_links.parquet` with theme, technique, and out-of-scope
items dropped entirely, per three manual CSV backstops. Otherwise unchanged — no columns added.
Runs as the very first classification step, before `3_regional_overview_classification` and
`4_regional_classification`, so every step downstream works with a smaller, cleaner tree. See
[DESIGN.md#21-2_non_genre_pruning](DESIGN.md#21-2_non_genre_pruning) for the three CSVs' scope and
the fail-fast validation each one gets.

No columns added; schema is identical to `1_item_links.parquet`.

**Data profile (as of this writing):**

| Metric                                     |  Rows | Distinct `item_id`s |
| ------------------------------------------- | ----: | -------------------: |
| Total (before drop)                        | 9,729 |                6,344 |
| Dropped (theme/technique/out-of-scope)      |     — |                    — |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/2_non_genre_pruning.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

### 3.4 3_regional_overview_classification

`3_regional_overview_classification.parquet`: `2_non_genre_pruning.parquet` unchanged, plus two
columns classifying whether each row's `item_id` is a regional-overview article (e.g. "music of Kenya")
rather than an actual musical style. See
[DESIGN.md#22-3_regional_overview_classification](DESIGN.md#22-3_regional_overview_classification)
for why this classification exists, its rule, and the manual-CSV backstop mechanics.

| Column                | Type | Meaning                                                                                    |
| --------------------- | ---- | -------------------------------------------------------------------------------------------- |
| is_regional_overview  | bool | `True` if `item_label` was classified as a regional overview article, not a musical style     |
| classification_reason | str? | Why `is_regional_overview` is `True`, or null when `is_regional_overview` is `False`         |

**Data profile (as of this writing):**

| Metric                                        |  Rows | Distinct `item_id`s |
| ---------------------------------------------- | ----: | ------------------: |
| Total                                          | 9,729 |               6,344 |
| `is_regional_overview = false`                 | 9,328 |               6,045 |
| `is_regional_overview = true` (`regional_overview`) |   401 |                 299 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/3_regional_overview_classification.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

### 3.5 4_regional_classification

`4_regional_classification.parquet`: `3_regional_overview_classification.parquet` unchanged aside from two added
columns flagging whether each row's `item_id` is a **regional genre** — nationally or ethnically
specific (e.g. "morna", "fado", and the "music of X" seed items themselves), as opposed to a genre
with no particular regional grounding (e.g. "rock music"). This step also reads `manual_main_parent.csv`
and, before the cascade below runs, injects one synthetic edge per row (tagged
`relation_type = "manual_main_parent"`) — a data expert's explicit main-parent pick for an item,
consumed downstream by [`5_main_parent_selection`](#36-5_main_parent_selection). See
[DESIGN.md#23-4_regional_classification](DESIGN.md#23-4_regional_classification) for why this step
reads the extra Bronze/CSV inputs, its seeding/cascade rule, and open caveats.

| Column          | Type | Meaning                                                                               |
| --------------- | ---- | ------------------------------------------------------------------------------------- |
| is_regional     | bool | Whether `item_id` is a regional genre — set for every item, including non-genre items |
| regional_reason | str? | `"seed"`, `"indigenous_to"`, `"manual_override"`, `"direct"`, `"inherited"`, or null |

**Data profile (as of this writing):**

| Metric                                   | Distinct items |
| ----------------------------------------- | -------------: |
| Total items                               |          6,404 |
| `is_regional = true`                      |          3,879 |
| `is_regional = false`                     |          2,525 |
| `regional_reason = "seed"`                |            359 |
| `regional_reason = "indigenous_to"`       |            179 |
| `regional_reason = "manual_override"`     |            180 |
| `regional_reason = "direct"`              |          1,614 |
| `regional_reason = "inherited"`           |          1,547 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/4_regional_classification.parquet`, read-only, no new data fetched) — these
numbers will drift as Wikidata's live genre tree changes.

### 3.6 5_main_parent_selection

`5_main_parent_selection.parquet` / `5_secondary_parents.parquet`: the first Silver step that
actually prunes rather than flags. Reads `4_regional_classification.parquet` and, for every item
with more than one candidate parent edge, picks exactly one as that item's **main** parent — a
`manual_main_parent.csv` override (already injected as a synthetic edge tagged
`relation_type = "manual_main_parent"` in `4_regional_classification`, see
[3.5 4_regional_classification](#35-4_regional_classification)) wins if present, otherwise the
candidate with the lowest numeric QID is picked as a provisional heuristic. Every other candidate
edge is not dropped — it's written to the sibling `5_secondary_parents.parquet` instead. See
[DESIGN.md#24-5_main_parent_selection](DESIGN.md#24-5_main_parent_selection) for the full rule and
its provisional/trial-and-error caveat.

`5_main_parent_selection.parquet` has the exact same schema as
[`4_regional_classification.parquet`](#35-4_regional_classification) (every column is carried
through unchanged) but is reduced to one row per `item_id`.

`5_secondary_parents.parquet` — one row per demoted candidate parent edge:

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | ---------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q133687529`)                                    |
| item_label    | str  | Same English/`mul`-fallback label as in `1_item_links` (see above), for `item_id`  |
| item_url      | str  | `https://www.wikidata.org/wiki/` + `item_id`                                     |
| parent_id     | str  | QID of this demoted candidate parent                                             |
| parent_label  | str  | Same fallback as `item_label`, for `parent_id`                                    |
| parent_url    | str  | `https://www.wikidata.org/wiki/` + `parent_id`                                   |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or `"manual_main_parent"` if a manual override lost to another manual override's edge |

An item with only one candidate parent (or none — a root) never produces a `5_secondary_parents`
row.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/5_main_parent_selection.parquet` and `SILVER_OUTPUT_DIR/5_secondary_parents.parquet`,
read-only, no new data fetched) — these numbers will drift as Wikidata's live genre tree changes.

### 3.7 6_canonical_parents

`6_canonical_parents.parquet`: `5_main_parent_selection.parquet` unchanged, plus one column flagging
whether each row's (now single, main) `parent_id` is itself an actual musical style. See
[DESIGN.md#25-6_canonical_parents](DESIGN.md#25-6_canonical_parents) for the classification rule.

| Column              | Type  | Meaning                                                                                                                    |
| ------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------- |
| parent_is_canonical | bool? | Whether `parent_id` is `is_regional_overview = False` in `3_regional_overview_classification`; null for root rows (`parent_id` is null) |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/6_canonical_parents.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 3.8 7_canonical_hierarchy

`7_canonical_hierarchy.parquet`: reads `6_canonical_parents.parquet` and reduces it to one row per
non-regional genre item, producing a clean, directly-consumable canonical genre hierarchy edge
list — excluding every `is_regional = true` item (see
[3.9 8_regional_hierarchy](#39-8_regional_hierarchy) for those). Since
`5_main_parent_selection` already collapsed every item to a single parent edge upstream, this step
only needs to prune to same-graph edges (dropping/promoting items whose sole parent points outside
the canonical set) — no further multi-parent collapse happens here. See
[DESIGN.md#26-7_canonical_hierarchy](DESIGN.md#26-7_canonical_hierarchy) for the pruning rule and
the open "too many roots" exploration (the manual-CSV backstops that drop non-genre items entirely
run earlier, in `2_non_genre_pruning` — see
[DESIGN.md#21-2_non_genre_pruning](DESIGN.md#21-2_non_genre_pruning)).

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | ---------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`) — **unique in this table**             |
| item_label    | str  | Same English/`mul`-fallback label as in `1_item_links` (see above), for `item_id`                                                      |
| item_display_label | str | Same as in `1_item_links`, for `item_id` — emitted by Gold as the node `name` |
| item_url      | str  | `https://www.wikidata.org/wiki/` + `item_id`                                     |
| parent_id     | str? | QID of the single chosen parent, or null for a root                              |
| parent_label  | str? | Same fallback as `item_label`, for `parent_id`, or null                                           |
| parent_display_label | str? | Same as in `1_item_links`, for `parent_id`, or null |
| parent_url    | str? | `https://www.wikidata.org/wiki/` + `parent_id`, or null for a root row           |
| relation_type | str? | `"P279"`, `"P361"`, or `"manual_main_parent"` — which property/source produced this edge, or null for a root row |
| is_umbrella | bool | `true` when `item_id` is in `manual_umbrella_genres.csv`: a real node that groups sub-styles and never receives songs directly. Always has at least one child in this table. |

Genre items with zero surviving rows in _either_ this output or `8_regional_hierarchy` (the silent
vanish) are all non-regional, i.e. every one is an "opera"-shaped item, not a regional one. See
[DESIGN.md#262-known-consequence--non-genre-orphans-vanish](DESIGN.md#262-known-consequence--non-genre-orphans-vanish)
for why.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/6_canonical_parents.parquet`, `SILVER_OUTPUT_DIR/7_canonical_hierarchy.parquet`, and
`SILVER_OUTPUT_DIR/8_regional_hierarchy.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

### 3.9 8_regional_hierarchy

`8_regional_hierarchy.parquet`: mirrors `7_canonical_hierarchy`'s pruning, restricted to
`is_regional = true` items (which now includes the `regional_overview` seed items themselves).
Reads `6_canonical_parents.parquet` and reduces it to one row per regional genre item, producing a
clean, directly-consumable regional genre hierarchy edge list. Same "no further multi-parent
collapse needed" property as `7_canonical_hierarchy`, for the same reason. See
[DESIGN.md#27-8_regional_hierarchy](DESIGN.md#27-8_regional_hierarchy) for the pruning rule.

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | ---------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q1198360`) — **unique in this table**           |
| item_label    | str  | Same English/`mul`-fallback label as in `1_item_links` (see above), for `item_id`                                                      |
| item_url      | str  | `https://www.wikidata.org/wiki/` + `item_id`                                     |
| parent_id     | str? | QID of the single chosen parent, or null for a root                              |
| parent_label  | str? | Same fallback as `item_label`, for `parent_id`, or null                                           |
| parent_url    | str? | `https://www.wikidata.org/wiki/` + `parent_id`, or null for a root row           |
| relation_type | str? | `"P279"`, `"P361"`, or `"manual_main_parent"` — which property/source produced this edge, or null for a root row |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/6_canonical_parents.parquet`, `SILVER_OUTPUT_DIR/7_canonical_hierarchy.parquet`, and
`SILVER_OUTPUT_DIR/8_regional_hierarchy.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

### 3.10 9_canonical_roots

`9_canonical_roots.parquet`: `7_canonical_hierarchy.parquet` filtered to root items (`parent_id`
null, or pointing at a QID with no row of its own in that file — a dead-end parent) and reduced to
the three item-identifying columns plus `is_accepted`, sorted by `item_label`. Exists purely to make manual
exploration of the "too many roots" open question
([DESIGN.md#263-under-exploration--root-count](DESIGN.md#263-under-exploration--root-count)) easier — a ready-to-open list of exactly the
items in question, instead of re-deriving the filter each time (as
`notebooks/explore_genre_tree.ipynb` currently does inline). Not part of the pruning chain; consumed by
Gold's `canonical_genre_tree_export.py`, which marks roots with `is_accepted = false` as
`isUnacceptedRoot: true` for review in grow-the-music-tree-api.

| Column     | Type | Meaning                                       |
| ---------- | ---- | ---------------------------------------------- |
| item_id    | str  | Wikidata QID of the root genre (e.g. `Q11399`) |
| item_label | str  | English label for `item_id`                    |
| item_url   | str  | `https://www.wikidata.org/wiki/` + `item_id`   |
| is_accepted | bool | `item_id` is in `manual_accepted_canonical_roots.csv`; false = new since last triage, logged as a WARNING and flagged for review |

See [DESIGN.md#263-under-exploration--root-count](DESIGN.md#263-under-exploration--root-count) for
context on why this count is expected to shrink as the lowest-QID main-parent heuristic and
regional classification rules mature.

### 3.11 10_commons_image_metadata

`10_commons_image_metadata.parquet`: one row per distinct `image_file` of Bronze `wikidata_artist_images.parquet`,
from the Commons API (`prop=imageinfo`, 50 titles per request, normalizations and redirects followed). A cache: rows
are kept for `COMMONS_IMAGE_TTL_DAYS` (30) days, at most `COMMONS_METADATA_MAX_BATCHES_PER_RUN` requests are spent per
run (never-checked files first, then the stalest), and files no longer in Bronze are dropped. Written in a `finally`,
so batches fetched before a failure are kept. A file missing on Commons keeps a row with only `image_file` and
`checked_at` set, so it isn't fetched again before the TTL expires.

| Column               | Type      | Meaning                                                                                          |
| -------------------- | --------- | ------------------------------------------------------------------------------------------------ |
| image_file           | str       | Commons file name, as in Bronze                                                                  |
| thumb_url            | str?      | 500 px wide thumbnail (`upload.wikimedia.org` or `thumb.wikimedia.org`), `utm_*` query dropped   |
| page_url             | str?      | The file's Commons description page                                                              |
| author               | str?      | extmetadata `Artist`, else `Credit`, HTML stripped and whitespace collapsed                      |
| license              | str?      | extmetadata `LicenseShortName` (e.g. `CC BY-SA 4.0`, `Public domain`)                             |
| license_url          | str?      | extmetadata `LicenseUrl`                                                                         |
| attribution_required | bool?     | extmetadata `AttributionRequired`                                                                |
| non_free             | bool?     | extmetadata `NonFree` is `true`                                                                  |
| checked_at           | datetime  | UTC time of the fetch                                                                            |

### 3.12 11_artist_images

`11_artist_images.parquet`: one image per MusicBrainz artist, from Bronze `wikidata_artist_images.parquet` joined with
`10_commons_image_metadata.parquet`. Drops files not checked yet, missing on Commons, non-free, without a license, or
without an author when attribution is required. When several images remain (several QIDs or several best-rank `P18`
values), keeps the smallest `image_file`. Checked: unique MBID, non-null `thumb_url`/`page_url`/`license`, `thumb_url`
on `upload.wikimedia.org` or `thumb.wikimedia.org`. Read by Gold's `artist_export.py`.

| Column                | Type | Meaning                                              |
| --------------------- | ---- | ---------------------------------------------------- |
| musicbrainz_artist_id | str  | MusicBrainz artist MBID                              |
| thumb_url             | str  | As in `10_commons_image_metadata`                    |
| page_url              | str  | As in `10_commons_image_metadata`                    |
| author                | str? | Null only when the license doesn't require attribution |
| license               | str  | As in `10_commons_image_metadata`                    |
| license_url           | str? | As in `10_commons_image_metadata`                    |
