# Schema

Data dictionary and lineage notes for `wikidata`. See [README.md#pipeline](README.md#pipeline) for the Bronze layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Wikidata properties used](#wikidata-properties-used)
  - [Bronze](#bronze)
  - [Silver](#silver)
    - [Overview](#overview)
    - [1_genre_classification](#1_genre_classification)
    - [2_regional_classification](#2_regional_classification)
    - [3_genre_parents](#3_genre_parents)
    - [4_hierarchy](#4_hierarchy)

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
[`2_genre_parents`](#2_genre_parents) (flagging) and [`4_hierarchy`](#4_hierarchy) (pruning) below.
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

All four steps below are produced by `wikidata.silver`. `1_genre_classification`,
`2_regional_classification`, and `3_genre_parents` preserve the Bronze edge-list grain 1:1
(`item_id` still not unique) — none of them drop rows; downstream consumers filter on the added
columns themselves. `4_hierarchy` is different: it's the first step that actually drops rows, and
the first where `item_id` is unique — see below.

### Overview

| Step                          | Reads                            | Writes                                                          | Adds                                    | Key result (as of this writing)                                                                 |
| ------------------------------ | --------------------------------- | ----------------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------- |
| [`1_genre_classification`](#1_genre_classification) | Bronze `wikidata_genre_tree.parquet` | `1_genre_classification.parquet`                    | `is_genre`, `classification_reason`     | 401 of 9,724 rows (299 of 6,338 items) tagged `is_genre = false` / `regional_overview` (e.g. "music of Kenya") — not dropped |
| [`2_regional_classification`](#2_regional_classification) | `1_genre_classification.parquet` | `2_regional_classification.parquet`                | `is_regional`, `regional_reason`        | 3,377 of 6,338 items (~53%) flagged `is_regional` — 299 seed, 1,392 direct, 1,686 inherited (exploration-phase finding, see callout below) |
| [`3_genre_parents`](#3_genre_parents)   | `2_regional_classification.parquet` | `3_genre_parents.parquet`                                    | `parent_is_genre`                       | 2,988 of 9,724 rows have a non-genre parent; 488 rows are roots (`parent_is_genre = null`)       |
| [`4_hierarchy`](#4_hierarchy)          | `3_genre_parents.parquet`         | `4_hierarchy.parquet` (canonical), `4_regional_hierarchy.parquet` (regional) | prunes to one row per `item_id`         | canonical: 2,662 final rows from 2,961 items; regional: 3,377 final rows from 3,377 items (seed items are real nodes, not promoted synthetic roots); 299 items vanish from both |

Each step's own section below has the full column definitions, rules, and profiling detail behind
these numbers — this table is just the fast top-to-bottom path through the chain.

### 1_genre_classification

`1_genre_classification.parquet`: the Bronze edge list unchanged, plus two columns classifying
each row's `item_id` as a real genre or not.

| Column                 | Type | Meaning                                                                  |
| ----------------------- | ---- | ------------------------------------------------------------------------ |
| is_genre               | bool | `False` if `item_label` was classified as not a genre                    |
| classification_reason | str? | Why `is_genre` is `False` (see below), or null when `is_genre` is `True` |

**Why classification is needed:** Wikidata's `P31` "instance of" `Q188451` ("music genre") class
extension — Bronze's source query — is noisy. It includes items that are not themselves genres,
e.g. "music of Kenya" (a country's music scene overview, not a genre). Left unflagged, these would
pollute any genre hierarchy or genre-matching built on top of this data.

**`classification_reason` values:**

| Value               | Rule                                   | Rationale                                                                                                                                                                                |
| ------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `regional_overview` | `item_label` starts with `"music of "` | Wikidata's national/regional music overview articles (e.g. "music of France", "music of Kenya") — ~300 of ~6,300 items as of this writing, always this exact prefix, never a genre name. |

This tags, it does not exclude: `regional_overview` items stay in every downstream Parquet file
and are the seed set [`2_regional_classification`](#2_regional_classification) propagates
`is_regional` down from — any genre item with a parent edge into one of these becomes a regional
genre, and the seeds themselves become regional genre nodes in their own right (see
[`4_hierarchy`](#4_hierarchy)).

This is a first classification pass covering the single highest-confidence, most mechanical rule
found during analysis. Other non-genre categories are known to exist in the Bronze data (musical
forms/techniques like "fugue" or "polyphony", ensemble/format labels like "big band music") but
aren't covered here yet — they don't reduce to one clean, false-positive-free rule the way
`regional_overview` does, and are left for a later Silver step.

**Data profile (as of this writing):**

| Metric                                   |  Rows | Distinct `item_id`s |
| ----------------------------------------- | ----: | -------------------: |
| Total                                    | 9,724 |               6,338 |
| `is_genre = true`                        | 9,323 |               6,039 |
| `is_genre = false` (`regional_overview`) |   401 |                 299 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/1_genre_classification.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

### 2_regional_classification

`2_regional_classification.parquet`: `1_genre_classification.parquet` unchanged, plus two columns
flagging whether each row's `item_id` is a **regional genre** — nationally or ethnically specific
(e.g. "morna", "fado", and the "music of X" seed items themselves), as opposed to a genre with no
particular regional grounding (e.g. "rock music").

| Column          | Type  | Meaning                                                                                     |
| --------------- | ----- | --------------------------------------------------------------------------------------------- |
| is_regional     | bool | Whether `item_id` is a regional genre — set for every item, including non-genre items         |
| regional_reason | str?  | `"seed"`, `"direct"`, `"inherited"`, or null (see rule below)                                |

**Rule:** `regional_overview` items (from `1_genre_classification`, e.g. "music of Kenya", "music
of Cape Verde") are the seed set and are themselves flagged `is_regional = True` /
`regional_reason = "seed"` — they are regional genre nodes in their own right, not merely a
launching point for other items. A genre item is regional if **any one** of its parent edges
points at a seed, or at an item already flagged regional — propagated down as a multi-source
cascade, repeated to a fixpoint. `regional_reason` is `"direct"` when the item's own parent set
includes a seed directly, `"inherited"` when it only reaches regional status via an
already-flagged parent.

> ⚠️ **ANY-parent, not ALL-parent — confirmed by a real multi-parent case.** A naive "every parent
> trail dead-ends in a seed" rule would miss real regional genres that also happen to have a clean
> secondary parent: "Portuguese folk music" has one parent edge into "music of Portugal" (a seed)
> and another into "European folk music" (not a seed) — live data confirms "European folk music"
> is itself also considered a regional genre. Having *any* parent edge into a regional item is
> sufficient, regardless of whether the item also has a clean parent elsewhere. No curated
> override list is needed: this structural rule alone catches both "morna" (direct seed hit) and
> "fado" (inherited, two hops through "Portuguese folk music").

> ⚠️ **Exploration phase — this rule will evolve.** Cascading from *every* `regional_overview`
> seed, including continent-level overview articles ("music of Asia", "music of Europe", "music of
> Africa", "music of the Americas") alongside country/ethnic-level ones ("music of Kenya", "music
> of Cape Verde"), currently flags **~53% of all items** as regional (see profile below) — far more
> than the ~367-item vanished-from-hierarchy baseline that originally motivated this step. That's
> because continent-level seeds have large direct fan-out (e.g. "A-pop" is a direct child of
> "music of Asia"). This is being kept as-is for now since the pipeline is still in an exploration
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

| Metric                              | Distinct items |
| -------------------------------------- | -------------: |
| Total items                            |          6,338 |
| `is_regional = true`                   |          3,377 |
| `is_regional = false`                  |          2,961 |
| `regional_reason = "seed"`             |            299 |
| `regional_reason = "direct"`           |          1,392 |
| `regional_reason = "inherited"`        |          1,686 |

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/2_regional_classification.parquet`, read-only, no new data fetched) — these
numbers will drift as Wikidata's live genre tree changes.

### 3_genre_parents

`3_genre_parents.parquet`: `2_regional_classification.parquet` unchanged, plus one column flagging
whether each row's `parent_id` is itself a real genre.

| Column          | Type  | Meaning                                                                            |
| --------------- | ----- | ------------------------------------------------------------------------------------ |
| parent_is_genre | bool? | Whether `parent_id` is `is_genre = True` in `1_genre_classification`; null for root rows (`parent_id` is null) |

**Rule:** a parent counts as a genre only if it is flagged `is_genre = True` by
`1_genre_classification` — not merely present in Bronze's raw `P31` "music genre" extension. This
keeps the Silver steps agreeing with each other: an edge into a `regional_overview` item like
"music of Kenya" is `parent_is_genre = False`, the same as an edge into a concept that was never
`P31` "music genre" at all (e.g. "opera" → "composed musical work").

**Data profile (as of this writing):**

| Metric                           |  Rows |
| ----------------------------------- | ----: |
| Total                             | 9,724 |
| `parent_is_genre = true`          | 6,248 |
| `parent_is_genre = false`         | 2,988 |
| `parent_is_genre = null` (root, no parent) |   488 |

Non-genre parents span both a genre item tagged non-genre in step 1 (e.g. an edge into "music of
Tanzania") and a parent that was never in Bronze's `P31` "music genre" extension at all (e.g.
"national song" → "national anthem", "Renaissance music" → "Renaissance art") — both count as
`parent_is_genre = false` under the rule above.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/3_genre_parents.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.

### 4_hierarchy

`4_hierarchy.parquet` (canonical) and `4_regional_hierarchy.parquet` (regional): the first Silver
step that actually prunes rather than flags. Reads `3_genre_parents.parquet` and reduces it to one
row per genre item, split into two clean, directly-consumable genre hierarchy edge lists — a
canonical one, excluding every `is_regional = true` item, and a regional one, containing only
`is_regional = true` items (which now includes the `regional_overview` seed items themselves).

| Column        | Type | Meaning                                                                          |
| ------------- | ---- | ------------------------------------------------------------------------------------ |
| item_id       | str  | Wikidata QID of the genre (e.g. `Q11399`) — **unique in this table**             |
| item_label    | str  | English label for `item_id`                                                      |
| parent_id     | str? | QID of the single chosen parent, or null for a root                              |
| parent_label  | str? | English label for `parent_id`, or null                                          |
| relation_type | str? | `"P279"` or `"P361"` — which property produced this edge, or null for a root row |

**Rule**, applied in two stages, run separately for the two outputs:

1. **Prune to same-graph edges.** For `4_hierarchy`, keep a row only if `is_genre = True` for the
   item itself and it is *not* `is_regional`, and either `parent_id` is null (a root) or
   `parent_is_genre = True` and the parent is not itself regional. For `4_regional_hierarchy`, keep
   a row only if the item *is* `is_regional` (seed items included), and either `parent_id` is null
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

**Known consequence — the two outputs diverge here.** In `4_hierarchy` (canonical), an item whose
every parent edge points to a non-genre or regional parent (and which isn't itself a root) has all
its rows dropped in stage 1 — it disappears entirely, not even as an implicit root (e.g. "opera" →
"composed musical work"). In `4_regional_hierarchy`, a `regional_overview` seed like "music of Cape
Verde" is now a real node with its own real parent chain (or a genuine root, if it has no `P279`/
`P361` parent at all) rather than being dropped — so an item like "morna," whose only parent is
that seed, keeps its real parent edge instead of being promoted to a synthetic root itself. Check
`profile_hierarchy`'s "zero surviving rows in either output" count for how often the canonical
vanishing still happens.

**Data profile (as of this writing):**

| Metric                                    | Canonical (`4_hierarchy`) | Regional (`4_regional_hierarchy`) |
| -------------------------------------------- | ------------------------: | ----------------------------------: |
| Genre items in scope (distinct `item_id`)    |                      2,961 |                              3,377 |
| Final rows (= distinct `item_id`s)           |                      2,662 |                              3,377 |

Genre items with zero surviving rows in *either* output (the canonical-style silent vanish): **299**
— all non-regional, i.e. every one is an "opera"-shaped item, not a regional one.

Regenerate with `uv run --package wikidata python -m wikidata.silver.profile` (reads
`SILVER_OUTPUT_DIR/3_genre_parents.parquet`, `SILVER_OUTPUT_DIR/4_hierarchy.parquet`, and
`SILVER_OUTPUT_DIR/4_regional_hierarchy.parquet`, read-only, no new data fetched) — these numbers
will drift as Wikidata's live genre tree changes.

> ⚠️ **Under exploration:** `4_hierarchy` (canonical) surfaces a high number of root items
> (`parent_id = null`) — hundreds, not the small handful a genre tree with one or two top-level
> categories (e.g. "music") would suggest. Whether that many roots is a real property of the source
> data (genuinely disconnected genre subtrees) or an artifact of upstream pruning/collapse rules
> (e.g. stage 2's lowest-QID collapse severing an item from its more meaningful parent) is not yet
> determined — see `pipelines/wikidata/notebooks/explore_genre_tree.ipynb` for the current
> exploration of these roots' relevance.
>
> **Ruled out:** `?item wdt:P279 wd:Q188451` (items directly subclass-of "music genre" itself,
> rather than `P31`-instance-of it) was considered as an alternate, smaller root/seed list. Live
> Wikidata returns only 12 items, not a clean top-level genre list — one is unrelated ("game
> piece"), two are specific traditions rather than roots ("gharana", "palo"), and seven are
> meta-classes describing a *category of genre* (e.g. "jazz genre", "rock genre") rather than the
> genre item itself (jazz music is the separate `P31` instance `Q1298934`, not this `P279`
> subclass). No prior art found for using this pattern to seed a Wikidata music genre tree. Doesn't
> resolve the root-count question above.
