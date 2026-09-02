# Schema

Data dictionary and lineage notes for `wikidata`. See [README.md#pipeline](README.md#pipeline) for the Bronze layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Wikidata properties used](#wikidata-properties-used)
  - [Bronze](#bronze)
  - [Silver](#silver)
    - [Overview](#overview)
    - [1_item_links](#1_item_links)
    - [2_regional_overview_classification](#2_regional_overview_classification)
    - [3_regional_classification](#3_regional_classification)
    - [4_genre_parents](#4_genre_parents)
    - [5_hierarchy](#5_hierarchy)
    - [6_canonical_roots](#6_canonical_roots)

## Wikidata properties used

Wikidata models knowledge as items (`Q...` IDs) connected by properties (`P...` IDs). Three
properties drive this pipeline's whole shape:

- **`P31` ("instance of")** — links an item to the class it directly belongs to. `wd:Q11399`
  ("rock music") `wdt:P31` `wd:Q188451` ("music genre") means "rock music is a music genre." This
  is Wikidata's class-membership edge — it's how Bronze finds the full set of genre items in the
  first place (see `GENRE_TREE_QUERY`'s `?item wdt:P31 wd:Q188451` clause).
- **`P279` ("subclass of")** — links a class to its more general parent class(es), building a
  taxonomy. `wd:Q11399` ("rock music") `wdt:P279` `wd:Q373342` ("popular music") means "rock music
  is a kind of popular music." This is the main edge that builds the genre _hierarchy_ — a genre
  can have more than one `P279` parent, since Wikidata classes aren't a strict tree.
- **`P361` ("part of")** — a meronymic (part-whole, not is-a) edge, used inconsistently across
  genre items in place of or alongside `P279` for what is still, in practice, subgenre-of-genre
  information. It's sparser than `P279` (~250 edges vs. ~9,000) and noisier (most `P361` targets
  aren't themselves a `P31` music genre — e.g. "punk subculture"), but a meaningful minority of
  edges are hierarchy information `P279` doesn't have at all — e.g. several juke/footwork/ghetto
  house subgenres are only linked to their parent via `P361`. Bronze ingests the full `P361` edge
  set raw and unfiltered, just as it already does for `P279`, tagged by `relation_type` (see
  below) so consumers can tell the two edge types apart rather than silently merging two
  different semantics into one column.
- **`P2341` ("indigenous to")** — links an item to the people/ethnic group it originates from
  (e.g. `wd:Q10376827` "Han Chinese music" `wdt:P2341` `wd:Q49103` "Han Chinese"). This is an
  ethnographic attribute of the item itself, not a genre-to-genre taxonomy edge like
  `P279`/`P361` — its cardinality is independent of an item's parent count, so it's ingested into
  its own Bronze table (`wikidata_genre_indigenous_to.parquet`, see below) rather than into
  `wikidata_genre_tree.parquet`. It exists to catch genres that are ethnically/regionally
  specific but happen to be roots with no `P279`/`P361` parent at all (so the `3_regional_classification`
  parent-based cascade has nothing to propagate from) — e.g. "Han Chinese music" has no parent
  edge, so without `P2341` it would surface as a spurious canonical root instead of being
  classified regional. See [`3_regional_classification`](#3_regional_classification).
- **`P495` ("country of origin")** — links an item to the country it originated in (e.g.
  `wd:Q1198131` "morna" `wdt:P495` `wd:Q1011` "Cape Verde"). Same shape and rationale as `P2341`
  above: a per-item attribute, not a genre-to-genre taxonomy edge, independent of an item's
  `P279`/`P361` parent count, so it's ingested into its own Bronze table
  (`wikidata_genre_country_of_origin.parquet`, see below) rather than into
  `wikidata_genre_tree.parquet`. It catches nationally-specific genres that would otherwise slip
  through the `3_regional_classification` parent-based cascade — either because they're roots with
  no parent edge at all, or because their parent chain never happens to reach a `regional_overview`
  seed. See [`3_regional_classification`](#3_regional_classification).

The three taxonomy properties (`P31`, `P279`, `P361`) are not interchangeable and don't chain into
each other the way you might expect — see below. `P2341` and `P495` are separate, orthogonal kinds
of edges (item-to-people and item-to-country, not item-to-item taxonomy) and aren't part of that
chaining discussion.

## Bronze

One Parquet file, `wikidata_genre_tree.parquet`, one row per (item, parent, relation_type) edge:
every Wikidata item classified `P31` ("instance of") `Q188451` ("music genre") — the class
extension, ~6,300 items as of this writing — plus each genre's direct `P279` ("subclass of") and
`P361` ("part of") parent(s). See `wikidata_client.GENRE_TREE_QUERY` for the exact SPARQL.

**Why `P31`, not a `P279*` walk from `Q188451`:** the intuitive query — "every item transitively
`P279` subclass-of music genre" — returns only 14 items (verified live), mostly _meta-categories_
rather than actual genres:

> `gharana`, `palo`, `game piece`, `opera genre`, `fusion music genre`, `jazz genre`, `electronic
music genre`, `blues genre`, `folk music genre`, `world music genre`, `rock genre`, `music by
instrument`, `Shengqiang`, plus `Q188451` itself.

Note the pattern: `"jazz genre"`, `"rock genre"` are _classes of genre_, not genres — not the
~6,300 actual genres like "rock music" or "bebop" that Bronze needs.

That's because Wikidata keeps the two relationships separate:

- **Class membership** is `P31` — e.g. `wd:Q11399` "rock music" `wdt:P31` `wd:Q188451` "music genre".
- **Subgenre hierarchy** is `P279`, but _between genre items_ — e.g. `wd:Q11399` "rock music"
  `wdt:P279` `wd:Q373342` "popular music".

That `P279` edge doesn't chain back up to `Q188451`. Confirmed live:

```sparql
ASK { wd:Q373342 wdt:P279* wd:Q188451 }   # → false
```

So a `P279*` walk from `Q188451` finds only the 14 meta-category items above, and silently misses
"rock music" and every other real genre — their `P279` parent chains lead to broader _concepts_
like "popular music", not back to the "music genre" class they're an _instance_ of.

**Parents are not restricted to also being a music genre instance.** A genre's `P279`/`P361`
edges routinely point at non-genre classes too — e.g. "opera" (`Q1344`) is `P279` both "classical
music" and "composed musical work" (`Q207628`, not itself `P31` music genre). Bronze ingests this
raw and unfiltered, consistent with the "as-is" bronze principle used for MusicBrainz's tables
(see [`../musicbrainz/SCHEMA.md`](../musicbrainz/SCHEMA.md)); flagging genre-only parents and
pruning to a single parent per item is Silver-layer work — see
[`4_genre_parents`](#4_genre_parents) (flagging) and [`5_hierarchy`](#5_hierarchy) (pruning) below.
An item with neither a `P279`
nor a `P361` parent (a root, ~486 of
them as of this writing — down from ~510 pre-`P361`, since 22 formerly-root items turned out to
have only a `P361` parent) gets a single row with `parent_id`/`parent_label`/`relation_type` all
null.

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | -------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`)                                        |
| item_label    | str  | Label for `item_id` (English, falling back to a language-agnostic `mul` label if no English label exists) (e.g. "rock music")                                  |
| parent_id     | str? | QID of a direct `P279`/`P361` parent within the genre tree, or null              |
| parent_label  | str? | Same fallback as `item_label`, for `parent_id`, or null                                           |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or null for a root row |

**Deliberate deviation from the raw query response**: Wikidata's SPARQL results return full
entity URIs (`http://www.wikidata.org/entity/Q11399`), not bare QIDs — `ingest.py`
strips the `http://www.wikidata.org/entity/` prefix before writing Parquet, since the QID is the
natural join key and the full URI is otherwise dead weight. Labels are passed through as-is.

A multi-parent item (Wikidata classes aren't a strict tree — a genre can have more than one
`P279`/`P361` parent) produces one row per parent, so `item_id` is not unique on its own.

A second Parquet file, `wikidata_genre_indigenous_to.parquet`, one row per (item,
indigenous-to-group) pair: every `P31` music genre item that also has at least one `P2341`
("indigenous to") value. Unlike `wikidata_genre_tree.parquet`, items with no `P2341` value are
absent entirely — there is no "root row" placeholder, since absence of an ethnographic tag isn't
a hierarchy position the way a missing parent is. See `wikidata_client.INDIGENOUS_TO_QUERY`.

| Column               | Type | Meaning                                                             |
| -------------------- | ---- | -------------------------------------------------------------------- |
| item_id               | str  | Wikidata QID of the genre (e.g. `Q10376827`)                        |
| indigenous_to_id      | str  | Wikidata QID of the people/ethnic group (e.g. `Q49103`)              |
| indigenous_to_label   | str  | Same English/`mul`-fallback label as `item_label`, for `indigenous_to_id` (e.g. "Han Chinese")            |

A genre with several `P2341` values produces one row per value, so `item_id` is not unique on its
own (as of this writing: 207 rows).

A third Parquet file, `wikidata_genre_country_of_origin.parquet`, one row per (item,
country) pair: every `P31` music genre item that also has at least one `P495` ("country of
origin") value. Same absence rule as `wikidata_genre_indigenous_to.parquet` — items with no `P495`
value are absent entirely, no "root row" placeholder. See `wikidata_client.COUNTRY_OF_ORIGIN_QUERY`.

| Column                  | Type | Meaning                                                        |
| ------------------------ | ---- | --------------------------------------------------------------- |
| item_id                  | str  | Wikidata QID of the genre (e.g. `Q1198131`)                    |
| country_of_origin_id     | str  | Wikidata QID of the country (e.g. `Q1011`)                     |
| country_of_origin_label  | str  | Same English/`mul`-fallback label as `item_label`, for `country_of_origin_id` (e.g. "Cape Verde")   |

A genre with several `P495` values produces one row per value, so `item_id` is not unique on its
own (as of this writing: 2,496 rows).

## Silver

All five steps below are produced by `wikidata.silver`. `1_item_links`,
`2_regional_overview_classification`, `3_regional_classification`, and `4_genre_parents` preserve
the Bronze edge-list grain 1:1 (`item_id` still not unique) — none of them drop rows; downstream
consumers filter on the added columns themselves. `5_hierarchy` is different: it's the first step
that actually drops rows, and the first where `item_id` is unique — see below.

### Overview

| Step                                                                        | Reads                                        | Writes                                                                       | Adds                                | Key result (as of this writing)                                                                                                                                                 |
| --------------------------------------------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`1_item_links`](#1_item_links)                                             | Bronze `wikidata_genre_tree.parquet`         | `1_item_links.parquet`                                                       | `item_url`, `parent_url`, `has_item_label`, `has_parent_label` | `item_url` populated for all 9,729 rows; `parent_url` null only for the 486 root rows                                                                                            |
| [`2_regional_overview_classification`](#2_regional_overview_classification) | `1_item_links.parquet`                       | `2_regional_overview_classification.parquet`                                 | `is_regional_overview`, `classification_reason` | 401 of 9,729 rows (299 of 6,344 items) tagged `is_regional_overview = true` / `regional_overview` (e.g. "music of Kenya") — not dropped                                          |
| [`3_regional_classification`](#3_regional_classification)                   | `2_regional_overview_classification.parquet`, Bronze `wikidata_genre_indigenous_to.parquet`, Bronze `wikidata_genre_country_of_origin.parquet`, `manual_regional_overrides.csv` | `3_regional_classification.parquet`                                          | `is_regional`, `regional_reason`    | ~84% of items flagged `is_regional` — 299 seed, 179 indigenous_to, 2,048 country_of_origin, 1 manual_override, 2,135 direct, 666 inherited (exploration-phase finding, see callout below)                                      |
| [`4_genre_parents`](#4_genre_parents)                                       | `3_regional_classification.parquet`          | `4_genre_parents.parquet`                                                    | `parent_is_genre`                   | 2,989 of 9,729 rows have a non-genre parent; 486 rows are roots (`parent_is_genre = null`)                                                                                      |
| [`5_hierarchy`](#5_hierarchy)                                               | `4_genre_parents.parquet`                    | `5_hierarchy.parquet` (canonical), `5_regional_hierarchy.parquet` (regional) | prunes to one row per `item_id`     | canonical: 806 final rows from 1,017 items; regional: 5,327 final rows from 5,327 items (seed items are real nodes, not promoted synthetic roots); 211 items vanish from both |
| [`6_canonical_roots`](#6_canonical_roots)                                   | `5_hierarchy.parquet`                        | `6_canonical_roots.parquet`                                                  | filters to `parent_id = null`       | 297 root items, for manual exploration of the "too many roots" open question (see `5_hierarchy`'s "Under exploration" callout) |

Each step's own section below has the full column definitions, rules, and profiling detail behind
these numbers — this table is just the fast top-to-bottom path through the chain.

### 1_item_links

`1_item_links.parquet`: `wikidata_genre_tree.parquet` (Bronze) unchanged, plus two columns giving
the human-browsable Wikidata page for `item_id` and, where present, `parent_id`, and two columns
flagging whether `item_label`/`parent_label` are a real label or the QID-fallback string.

| Column           | Type | Meaning                                                                            |
| ---------------- | ---- | ----------------------------------------------------------------------------------- |
| item_url         | str  | `https://www.wikidata.org/wiki/` + `item_id` — the item's browsable Wikidata page   |
| parent_url       | str? | `https://www.wikidata.org/wiki/` + `parent_id`, or null when `parent_id` is null    |
| has_item_label   | bool | `False` when `item_label == item_id` (Wikidata's label service found no English/`mul` label and fell back to printing the QID) |
| has_parent_label | bool? | Same check for `parent_label`/`parent_id`, or null when `parent_id` is null       |

**Why `item_url`/`parent_url` are needed:** `item_id`/`parent_id` are bare QIDs (e.g. `Q11399`) —
the natural join key, but not something a person can act on directly. Manual review (profiling
output, ad-hoc DuckDB queries, the exploration notebook) otherwise requires manually prepending the
wiki page prefix to look an item up. This is deliberately the human-facing `/wiki/` page prefix,
not the `http://www.wikidata.org/entity/` RDF entity URI that Bronze's `ingest.py` already strips
off (see [Bronze](#bronze) above) — that prefix identifies the machine data URI, not the browsable
page, so it isn't reusable here.

**Why `has_item_label`/`has_parent_label` are needed:** Bronze's `SERVICE wikibase:label` query
(`en,mul` fallback chain) still falls back to printing the bare QID when an item has neither an
English nor a `mul` (language-agnostic) label upstream — indistinguishable from a real label by
string shape alone. Any later step that pattern-matches on `item_label`/`parent_label` text (e.g.
the `regional_overview` prefix match in [`2_regional_overview_classification`](#2_regional_overview_classification))
would otherwise silently match against a QID. These flags let such steps exclude or special-case
unlabeled rows instead.

**Data profile (as of this writing):**

| Metric                                     |  Rows | Distinct `item_id`s |
| ------------------------------------------- | ----: | -------------------: |
| Total                                       | 9,729 |                6,344 |
| `parent_url` populated                      | 9,243 |                    — |
| `parent_url` null (root, `parent_id` null)  |   486 |                    — |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/1_item_links.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 2_regional_overview_classification

`2_regional_overview_classification.parquet`: `1_item_links.parquet` unchanged, plus two
columns classifying whether each row's `item_id` is a regional-overview article (e.g. "music of Kenya")
rather than an actual musical style.

| Column                | Type | Meaning                                                                                    |
| --------------------- | ---- | -------------------------------------------------------------------------------------------- |
| is_regional_overview  | bool | `True` if `item_label` was classified as a regional overview article, not a musical style     |
| classification_reason | str? | Why `is_regional_overview` is `True` (see below), or null when `is_regional_overview` is `False` |

**Why classification is needed:** Wikidata's `P31` "instance of" `Q188451` ("music genre") class
extension — Bronze's source query — is noisy. It includes items that are not themselves musical
styles, e.g. "music of Kenya" (a country's music scene overview, not a style). Left unflagged,
these would pollute any genre hierarchy or genre-matching built on top of this data.

**`classification_reason` values:**

| Value               | Rule                                   | Rationale                                                                                                                                                                                |
| ------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `regional_overview` | `item_label` starts with `"music of "` | Wikidata's national/regional music overview articles (e.g. "music of France", "music of Kenya") — ~300 of ~6,300 items as of this writing, always this exact prefix, never a genre name. |

This tags, it does not exclude: `regional_overview` items stay in every downstream Parquet file
and are the seed set [`3_regional_classification`](#3_regional_classification) propagates
`is_regional` down from — any genre item with a parent edge into one of these becomes a regional
genre, and the seeds themselves become regional genre nodes in their own right (see
[`5_hierarchy`](#5_hierarchy)).

This is a first classification pass covering the single highest-confidence, most mechanical rule
found during analysis. Other non-genre categories are known to exist in the Bronze data (musical
forms/techniques like "fugue" or "polyphony", ensemble/format labels like "big band music") but
aren't covered here yet — they don't reduce to one clean, false-positive-free rule the way
`regional_overview` does, and are left for a later Silver step.

**Data profile (as of this writing):**

| Metric                                        |  Rows | Distinct `item_id`s |
| ---------------------------------------------- | ----: | ------------------: |
| Total                                          | 9,729 |               6,344 |
| `is_regional_overview = false`                 | 9,328 |               6,045 |
| `is_regional_overview = true` (`regional_overview`) |   401 |                 299 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/2_regional_overview_classification.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

### 3_regional_classification

`3_regional_classification.parquet`: `2_regional_overview_classification.parquet` unchanged, plus two columns
flagging whether each row's `item_id` is a **regional genre** — nationally or ethnically specific
(e.g. "morna", "fado", and the "music of X" seed items themselves), as opposed to a genre with no
particular regional grounding (e.g. "rock music"). This step also reads Bronze
`wikidata_genre_indigenous_to.parquet` and Bronze `wikidata_genre_country_of_origin.parquet` (see
[Bronze](#bronze)) to catch nationally/ethnically-specific genres that have no `P279`/`P361` parent
for the cascade below to propagate through in the first place, plus a git-tracked, hand-curated
CSV (`src/wikidata/silver/manual_regional_overrides.csv`, not Bronze — it's authored by a data
expert, not fetched from Wikidata) for the rare item the three automated sources still miss.

| Column          | Type | Meaning                                                                               |
| --------------- | ---- | ------------------------------------------------------------------------------------- |
| is_regional     | bool | Whether `item_id` is a regional genre — set for every item, including non-genre items |
| regional_reason | str? | `"seed"`, `"indigenous_to"`, `"country_of_origin"`, `"manual_override"`, `"direct"`, `"inherited"`, or null (see rule below) |

**Rule:** four kinds of items seed the regional graph and are themselves flagged `is_regional =
True`, not merely a launching point for other items:

- `regional_overview` items (from `2_regional_overview_classification`, e.g. "music of Kenya",
  "music of Cape Verde") — `regional_reason = "seed"`.
- items with at least one `P2341` ("indigenous to") value in Bronze
  `wikidata_genre_indigenous_to.parquet` (e.g. "Han Chinese music") — `regional_reason =
  "indigenous_to"`. Unlike `regional_overview` seeds these are ordinary genre items, not non-genre
  overview articles, and are often roots with no `P279`/`P361` parent at all — the parent-based
  cascade has nothing to reach them through, so they need this direct, independent signal instead.
- items with at least one `P495` ("country of origin") value in Bronze
  `wikidata_genre_country_of_origin.parquet` (e.g. "morna") — `regional_reason =
  "country_of_origin"`. Same rationale as `indigenous_to` above: an ordinary genre item, often a
  root with no `P279`/`P361` parent, so it needs this direct signal instead of relying on the
  parent-based cascade.
- items listed by `item_id` in `manual_regional_overrides.csv` — `regional_reason =
  "manual_override"`. A fallback for genres none of the three structural/property-based sources
  above catch — typically a root item with no `P279`/`P361` parent and no `P2341`/`P495` value
  either (e.g. "mezwed", a Tunisian genre with none of those signals). Each entry carries a
  `reason` column explaining why a data expert added it; see the file itself for the current list.
  Expected to stay small — this is a manual backstop for gaps, not the primary classification
  mechanism.

A genre item is regional if **any one** of its parent edges points at any kind of seed, or at
an item already flagged regional — propagated down as a multi-source cascade, repeated to a
fixpoint. `regional_reason` is `"direct"` when the item's own parent set includes a seed
(`regional_overview`, `indigenous_to`, `country_of_origin`, or `manual_override`) directly,
`"inherited"` when it only reaches regional status via an already-flagged parent that isn't itself
a seed.

> ⚠️ **ANY-parent, not ALL-parent — confirmed by a real multi-parent case.** A naive "every parent
> trail dead-ends in a seed" rule would miss real regional genres that also happen to have a clean
> secondary parent: "Portuguese folk music" has one parent edge into "music of Portugal" (a seed)
> and another into "European folk music" (not a seed) — live data confirms "European folk music"
> is itself also considered a regional genre. Having _any_ parent edge into a regional item is
> sufficient, regardless of whether the item also has a clean parent elsewhere. This structural
> rule alone catches both "morna" (direct seed hit) and "fado" (inherited, two hops through
> "Portuguese folk music") without any manual help — the curated override list above exists only
> for items the structural rule and the `P2341`/`P495` signals all miss entirely.

> ⚠️ **Exploration phase — this rule will evolve.** Cascading from _every_ `regional_overview`
> seed, including continent-level overview articles ("music of Asia", "music of Europe", "music of
> Africa", "music of the Americas") alongside country/ethnic-level ones ("music of Kenya", "music
> of Cape Verde"), currently flags **~84% of all items** as regional (see profile below) — far more
> than the ~367-item vanished-from-hierarchy baseline that originally motivated this step. That's
> because continent-level seeds have large direct fan-out (e.g. "A-pop" is a direct child of
> "music of Asia"), and now also because the `P495` ("country of origin") seed set alone covers
> 2,048 items. This is being kept as-is for now since the pipeline is still in an exploration
> phase, not shipped as a settled design decision — narrowing the seed set to exclude
> continent-level overview articles (so only country/ethnic-level pages seed the cascade) is a
> likely future refinement once there's a concrete product need to get the regional/canonical split
> tighter.

> ⚠️ **Known Bronze gap, not yet investigated:** "variété française," a named example of a regional
> genre, does not appear anywhere in the current Bronze extraction at all — not a `P279`/`P361`
> gap, it's simply absent from the `P31` "music genre" class extension entirely. This needs deeper
> investigation into why Wikidata's own query misses it (wrong assumed label, different
> instance-of class, etc.) rather than being treated as a non-issue.

**Data profile (as of this writing):**

| Metric                                   | Distinct items |
| ----------------------------------------- | -------------: |
| Total items                               |          6,344 |
| `is_regional = true`                      |          5,328 |
| `is_regional = false`                     |          1,016 |
| `regional_reason = "seed"`                |            299 |
| `regional_reason = "indigenous_to"`       |            179 |
| `regional_reason = "country_of_origin"`   |          2,048 |
| `regional_reason = "manual_override"`     |              1 |
| `regional_reason = "direct"`              |          2,135 |
| `regional_reason = "inherited"`           |            666 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/3_regional_classification.parquet`, read-only, no new data fetched) — these
numbers will drift as Wikidata's live genre tree changes.

### 4_genre_parents

`4_genre_parents.parquet`: `3_regional_classification.parquet` unchanged, plus one column flagging
whether each row's `parent_id` is itself an actual musical style.

| Column          | Type  | Meaning                                                                                                                    |
| --------------- | ----- | -------------------------------------------------------------------------------------------------------------------------- |
| parent_is_genre | bool? | Whether `parent_id` is `is_regional_overview = False` in `2_regional_overview_classification`; null for root rows (`parent_id` is null) |

**Rule:** a parent counts as an actual musical style only if it is flagged `is_regional_overview = False` by
`2_regional_overview_classification` — not merely present in Bronze's raw `P31` "music genre" extension. This
keeps the Silver steps agreeing with each other: an edge into a `regional_overview` item like
"music of Kenya" is `parent_is_genre = False`, the same as an edge into a concept that was never
`P31` "music genre" at all (e.g. "opera" → "composed musical work").

**Data profile (as of this writing):**

| Metric                                     |  Rows |
| ------------------------------------------ | ----: |
| Total                                      | 9,729 |
| `parent_is_genre = true`                   | 6,254 |
| `parent_is_genre = false`                  | 2,989 |
| `parent_is_genre = null` (root, no parent) |   486 |

Non-genre parents span both a genre item tagged non-genre in step 1 (e.g. an edge into "music of
Tanzania") and a parent that was never in Bronze's `P31` "music genre" extension at all (e.g.
"national song" → "national anthem", "Renaissance music" → "Renaissance art") — both count as
`parent_is_genre = false` under the rule above.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/4_genre_parents.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 5_hierarchy

`5_hierarchy.parquet` (canonical) and `5_regional_hierarchy.parquet` (regional): the first Silver
step that actually prunes rather than flags. Reads `4_genre_parents.parquet` and reduces it to one
row per genre item, split into two clean, directly-consumable genre hierarchy edge lists — a
canonical one, excluding every `is_regional = true` item, and a regional one, containing only
`is_regional = true` items (which now includes the `regional_overview` seed items themselves).

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | -------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`) — **unique in this table**             |
| item_label    | str  | Same English/`mul`-fallback label as in `1_item_links` (see above), for `item_id`                                                      |
| item_url      | str  | `https://www.wikidata.org/wiki/` + `item_id`                                     |
| parent_id     | str? | QID of the single chosen parent, or null for a root                              |
| parent_label  | str? | Same fallback as `item_label`, for `parent_id`, or null                                           |
| parent_url    | str? | `https://www.wikidata.org/wiki/` + `parent_id`, or null for a root row           |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or null for a root row |

**Rule**, applied in two stages, run separately for the two outputs:

1. **Prune to same-graph edges.** For `5_hierarchy`, keep a row only if `is_regional_overview = False` for the
   item itself and it is _not_ `is_regional`, and either `parent_id` is null (a root) or
   `parent_is_genre = True` and the parent is not itself regional. For `5_regional_hierarchy`, keep
   a row only if the item _is_ `is_regional` (seed items included), and either `parent_id` is null
   or the parent is itself `is_regional = True`. Either way, an edge into a non-regional, non-genre
   parent, or across the canonical/regional boundary, doesn't count as a legitimate hierarchy
   parent.
2. **Collapse multi-parent items to one row.** If an item still has more than one surviving parent
   within its own output, keep only the edge to the parent with the lowest numeric QID.

> ⚠️ **Provisional / tâtonnement:** the lowest-QID rule in stage 2 is an arbitrary placeholder, not
> a considered design decision. QIDs are assigned by Wikidata in creation order and carry no
> taxonomic meaning. It exists only because no better signal is currently available: live SPARQL
> queries against the real genre extension found that **2,727 of ~6,337 genre items (~43%)** have
> more than one `P279` parent even after Wikidata's own "best rank" resolution, and only **1 item
> in the entire genre extension** has any `P279` statement marked `preferred` rank — Wikidata's own
> disambiguation mechanism is essentially unused here. `musicbrainz/README.md` already documents
> that the eventual single-parent hierarchy format for TheMusicTreeAPI is "not yet decided" — this
> rule is a stand-in until that product/curation decision exists, and should be expected to change,
> likely once real curation input (e.g. via GrowTheMusicTree) is available.

**Known consequence — the two outputs diverge here.** In `5_hierarchy` (canonical), an item whose
every parent edge points to a non-genre or regional parent (and which isn't itself a root) has all
its rows dropped in stage 1 — it disappears entirely, not even as an implicit root (e.g. "opera" →
"composed musical work"). In `5_regional_hierarchy`, a `regional_overview` seed like "music of Cape
Verde" is now a real node with its own real parent chain (or a genuine root, if it has no `P279`/
`P361` parent at all) rather than being dropped — so an item like "morna," whose only parent is
that seed, keeps its real parent edge instead of being promoted to a synthetic root itself. Check
`profile_hierarchy`'s "zero surviving rows in either output" count for how often the canonical
vanishing still happens.

**Data profile (as of this writing):**

| Metric                                    | Canonical (`5_hierarchy`) | Regional (`5_regional_hierarchy`) |
| ----------------------------------------- | ------------------------: | --------------------------------: |
| Genre items in scope (distinct `item_id`) |                     1,016 |                             5,328 |
| Final rows (= distinct `item_id`s)        |                       805 |                             5,328 |
| Root rows (`parent_id = null`)            |                       297 |                                417 |

Genre items with zero surviving rows in _either_ output (the canonical-style silent vanish): **211**
— all non-regional, i.e. every one is an "opera"-shaped item, not a regional one.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/4_genre_parents.parquet`, `SILVER_OUTPUT_DIR/5_hierarchy.parquet`, and
`SILVER_OUTPUT_DIR/5_regional_hierarchy.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

> ⚠️ **Under exploration:** `5_hierarchy` (canonical) surfaces a high number of root items
> (`parent_id = null`) — **297 of 805 rows as of this writing** — not the small handful a genre tree
> with one or two top-level categories (e.g. "music") would suggest. Whether that many roots is a real property of the source
> data (genuinely disconnected genre subtrees) or an artifact of upstream pruning/collapse rules
> (e.g. stage 2's lowest-QID collapse severing an item from its more meaningful parent) is not yet
> determined — see `pipelines/wikidata/notebooks/explore_genre_tree.ipynb` for the current
> exploration of these roots' relevance.
>
> **Ruled out:** `?item wdt:P279 wd:Q188451` (items directly subclass-of "music genre" itself,
> rather than `P31`-instance-of it) was considered as an alternate, smaller root/seed list. Live
> Wikidata returns only 12 items, not a clean top-level genre list — one is unrelated ("game
> piece"), two are specific traditions rather than roots ("gharana", "palo"), and seven are
> meta-classes describing a _category of genre_ (e.g. "jazz genre", "rock genre") rather than the
> genre item itself (jazz music is the separate `P31` instance `Q1298934`, not this `P279`
> subclass). No prior art found for using this pattern to seed a Wikidata music genre tree. Doesn't
> resolve the root-count question above.
>
> **Target shape (design intent, not yet reached):** the canonical tree should collapse down to a
> handful of root genre families — rock, blues, jazz, funk/disco, electronic, hip-hop, reggae/dub,
> classical music, etc. — not the hundreds of roots it currently produces. Getting there is expected
> to be mostly a linking/cleaning problem (correcting mis-collapsed parent edges, e.g. the
> multi-parent lowest-QID heuristic above) rather than a new extraction or classification mechanism.
> `5_regional_hierarchy` follows different logic entirely and is **not** expected to converge to a
> small root count: one root per cultural/geographic region (e.g. "music of Cape Verde"), with that
> region's own genres nested underneath it.

### 6_canonical_roots

`6_canonical_roots.parquet`: `5_hierarchy.parquet` filtered to rows where `parent_id` is null
(root items) and reduced to the three item-identifying columns, sorted by `item_label`. Exists
purely to make manual exploration of the "too many roots" open question above easier — a ready-to-open
list of exactly the items in question, instead of re-deriving the filter each time (as
`notebooks/explore_genre_tree.ipynb` currently does inline). Not consumed by any later step and
not itself part of the pruning chain — it's a read view of `5_hierarchy`, not new information.

| Column     | Type | Meaning                                       |
| ---------- | ---- | ---------------------------------------------- |
| item_id    | str  | Wikidata QID of the root genre (e.g. `Q11399`) |
| item_label | str  | English label for `item_id`                    |
| item_url   | str  | `https://www.wikidata.org/wiki/` + `item_id`   |

**Data profile (as of this writing):** 297 rows — see the `5_hierarchy` root-count callout above
for context on why this count is expected to shrink as the multi-parent collapse heuristic and
regional classification rules mature.
