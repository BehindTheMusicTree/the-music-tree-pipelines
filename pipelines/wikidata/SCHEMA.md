# Schema

Data dictionary and lineage notes for `wikidata`. See [README.md#pipeline](README.md#pipeline) for the Bronze layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Wikidata properties used](#wikidata-properties-used)
  - [Bronze](#bronze)
  - [Silver](#silver)
    - [1_classification](#1_classification)
    - [2_genre_parents](#2_genre_parents)
    - [3_hierarchy](#3_hierarchy)

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

The three are not interchangeable and don't chain into each other the way you might expect — see
below.

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
[`2_genre_parents`](#2_genre_parents) (flagging) and [`3_hierarchy`](#3_hierarchy) (pruning) below.
An item with neither a `P279`
nor a `P361` parent (a root, ~488 of
them as of this writing — down from ~510 pre-`P361`, since 22 formerly-root items turned out to
have only a `P361` parent) gets a single row with `parent_id`/`parent_label`/`relation_type` all
null.

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | -------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`)                                        |
| item_label    | str  | English label for `item_id` (e.g. "rock music")                                  |
| parent_id     | str? | QID of a direct `P279`/`P361` parent within the genre tree, or null              |
| parent_label  | str? | English label for `parent_id`, or null                                           |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or null for a root row |

**Deliberate deviation from the raw query response**: Wikidata's SPARQL results return full
entity URIs (`http://www.wikidata.org/entity/Q11399`), not bare QIDs — `ingest.py`
strips the `http://www.wikidata.org/entity/` prefix before writing Parquet, since the QID is the
natural join key and the full URI is otherwise dead weight. Labels are passed through as-is.

A multi-parent item (Wikidata classes aren't a strict tree — a genre can have more than one
`P279`/`P361` parent) produces one row per parent, so `item_id` is not unique on its own.

## Silver

All three steps below are produced by `wikidata.silver`. `1_classification` and `2_genre_parents`
preserve the Bronze edge-list grain 1:1 (`item_id` still not unique) — neither drops rows;
downstream consumers filter on the added columns themselves. `3_hierarchy` is different: it's the
first step that actually drops rows, and the first where `item_id` is unique — see below.

### 1_classification

`1_classification.parquet`: the Bronze edge list unchanged, plus two columns classifying each
row's `item_id` as a real genre or not.

| Column           | Type | Meaning                                                                  |
| ---------------- | ---- | ------------------------------------------------------------------------ |
| is_genre         | bool | `False` if `item_label` was classified as not a genre                    |
| exclusion_reason | str? | Why `is_genre` is `False` (see below), or null when `is_genre` is `True` |

**Why classification is needed:** Wikidata's `P31` "instance of" `Q188451` ("music genre") class
extension — Bronze's source query — is noisy. It includes items that are not themselves genres,
e.g. "music of Kenya" (a country's music scene overview, not a genre). Left unfiltered, these
would pollute any genre hierarchy or genre-matching built on top of this data.

**`exclusion_reason` values:**

| Value               | Rule                                   | Rationale                                                                                                                                                                                |
| ------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `regional_overview` | `item_label` starts with `"music of "` | Wikidata's national/regional music overview articles (e.g. "music of France", "music of Kenya") — ~300 of ~6,300 items as of this writing, always this exact prefix, never a genre name. |

This is a first classification pass covering the single highest-confidence, most mechanical rule
found during analysis. Other non-genre categories are known to exist in the Bronze data (musical
forms/techniques like "fugue" or "polyphony", ensemble/format labels like "big band music") but
aren't covered here yet — they don't reduce to one clean, false-positive-free rule the way
`regional_overview` does, and are left for a later Silver step.

**Data profile (as of this writing):**

| Metric                                   |  Rows | Distinct `item_id`s |
| ---------------------------------------- | ----: | ------------------: |
| Total                                    | 9,722 |               6,337 |
| `is_genre = true`                        | 9,321 |               6,038 |
| `is_genre = false` (`regional_overview`) |   401 |                 299 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/1_classification.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 2_genre_parents

`2_genre_parents.parquet`: `1_classification.parquet` unchanged, plus one column flagging whether
each row's `parent_id` is itself a real genre.

| Column          | Type  | Meaning                                                                            |
| --------------- | ----- | ----------------------------------------------------------------------------------- |
| parent_is_genre | bool? | Whether `parent_id` is `is_genre = True` in `1_classification`; null for root rows (`parent_id` is null) |

**Rule:** a parent counts as a genre only if it is flagged `is_genre = True` by `1_classification`
— not merely present in Bronze's raw `P31` "music genre" extension. This keeps the two Silver
steps agreeing with each other: an edge into a `regional_overview` item like "music of Kenya" is
`parent_is_genre = False`, the same as an edge into a concept that was never `P31` "music genre" at
all (e.g. "opera" → "composed musical work").

**Open question, not resolved here:** should an item excluded by step 1 (e.g. `regional_overview`)
still be allowed to count as a legitimate hierarchy parent for some other genre? For now it does
not — `parent_is_genre` is `False` for such edges — but this is worth revisiting once a concrete
hierarchy-building step needs the answer.

**Data profile (as of this writing):**

| Metric                           |  Rows |
| --------------------------------- | ----: |
| Total                             | 9,722 |
| `parent_is_genre = true`          | 6,247 |
| `parent_is_genre = false`         | 2,987 |
| `parent_is_genre = null` (root, no parent) |   488 |

Non-genre parents span both a genre item excluded by step 1 (e.g. an edge into "music of
Tanzania") and a parent that was never in Bronze's `P31` "music genre" extension at all (e.g.
"national song" → "national anthem", "Renaissance music" → "Renaissance art") — both count as
`parent_is_genre = false` under the rule above.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/2_genre_parents.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 3_hierarchy

`3_hierarchy.parquet`: the first Silver step that actually prunes rather than flags. Reads
`2_genre_parents.parquet` and reduces it to one row per genre item — a clean, directly-consumable
genre hierarchy edge list.

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | -------------------------------------------------------------------------------- |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`) — **unique in this table**             |
| item_label    | str  | English label for `item_id`                                                      |
| parent_id     | str? | QID of the single chosen parent, or null for a root                              |
| parent_label  | str? | English label for `parent_id`, or null                                          |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or null for a root row |

**Rule**, applied in two stages:

1. **Prune to genre-only edges.** Keep a row only if `is_genre = True` for the item itself, and
   either `parent_id` is null (a genre root) or `parent_is_genre = True`. This drops every row for
   a non-genre item entirely, and any edge into a non-genre parent — including a parent excluded by
   `1_classification` (e.g. "music of Kenya"), per the "open question" noted above, which resolves
   the same way here: excluded items don't count as legitimate hierarchy parents.
2. **Collapse multi-parent items to one row.** If an item still has more than one surviving genre
   parent, keep only the edge to the parent with the lowest numeric QID.

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

**Known consequence:** an item whose every parent edge points to a non-genre parent (and which
isn't itself a root) has all its rows dropped in stage 1 — it disappears from `3_hierarchy`
entirely, not even as an implicit root. Check `profile_hierarchy`'s vanished-items count for how
often this happens.

**Data profile (as of this writing):**

| Metric                                              |  Rows/items |
| ---------------------------------------------------- | ----------: |
| `2_genre_parents` rows (input)                        |       9,722 |
| Non-genre edges dropped (stage 1)                     |       3,009 |
| Multi-parent edges collapsed to lowest QID (stage 2)  |       1,437 |
| `3_hierarchy` rows (= distinct `item_id`s)            |       5,276 |
| Genre items in `2_genre_parents` (`is_genre = true`)  |       6,038 |
| Genre items with zero surviving rows (vanished)       |         762 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/2_genre_parents.parquet` and `SILVER_OUTPUT_DIR/3_hierarchy.parquet`,
read-only, no new data fetched) — these numbers will drift as Wikidata's live genre tree changes.
