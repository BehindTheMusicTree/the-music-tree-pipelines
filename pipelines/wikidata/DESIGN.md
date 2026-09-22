# Design

Design rationale, classification rules, and manual-CSV curation mechanics for `wikidata`'s Silver
pipeline. See [SCHEMA.md](SCHEMA.md) for column definitions and data profiles.

## Table of Contents

- [Design](#design)
  - [Table of Contents](#table-of-contents)
  - [1. Bronze](#1-bronze)
    - [1.1 wikidata_genre_tree.parquet](#11-wikidata_genre_treeparquet)
      - [1.1.1 Why `P31`, not a `P279*` walk from `Q188451`](#111-why-p31-not-a-p279-walk-from-q188451)
      - [1.1.2 Parents are not restricted to also being a music genre instance](#112-parents-are-not-restricted-to-also-being-a-music-genre-instance)
      - [1.1.3 Bare QIDs, not full entity URIs](#113-bare-qids-not-full-entity-uris)
    - [1.2 wikidata_genre_indigenous_to.parquet and wikidata_genre_country_of_origin.parquet](#12-wikidata_genre_indigenous_toparquet-and-wikidata_genre_country_of_originparquet)
      - [1.2.1 Why separate tables, not extra columns on `wikidata_genre_tree.parquet`](#121-why-separate-tables-not-extra-columns-on-wikidata_genre_treeparquet)
  - [2. Silver](#2-silver)
    - [2.0 1_item_links — display-label casing](#20-1_item_links--display-label-casing)
    - [2.1 2_non_genre_pruning](#21-2_non_genre_pruning)
    - [2.2 3_regional_overview_classification](#22-3_regional_overview_classification)
      - [2.2.1 Why classification is needed](#221-why-classification-is-needed)
      - [2.2.2 `classification_reason` values](#222-classification_reason-values)
      - [2.2.3 Auto-promotion of orphan `"music of "` parents](#223-auto-promotion-of-orphan-music-of--parents)
      - [2.2.4 Manual addition of overview items missing from Bronze entirely](#224-manual-addition-of-overview-items-missing-from-bronze-entirely)
      - [2.2.5 Manual reclassification of existing items as overview](#225-manual-reclassification-of-existing-items-as-overview)
      - [2.2.6 Scope of this first pass](#226-scope-of-this-first-pass)
    - [2.3 4_regional_classification](#23-4_regional_classification)
      - [2.3.1 Inputs](#231-inputs)
      - [2.3.2 Rule: four seed sources](#232-rule-four-seed-sources)
      - [2.3.3 Cascade](#233-cascade)
      - [2.3.4 `manual_main_parent.csv` main-parent override](#234-manual_main_parentcsv-main-parent-override)
    - [2.4 5_main_parent_selection](#24-5_main_parent_selection)
      - [2.4.1 Rule: manual override, else lowest-QID heuristic](#241-rule-manual-override-else-lowest-qid-heuristic)
      - [2.4.2 Secondary parents are kept, not dropped](#242-secondary-parents-are-kept-not-dropped)
    - [2.5 6_canonical_parents](#25-6_canonical_parents)
      - [2.5.1 Rule: what counts as a canonical parent](#251-rule-what-counts-as-a-canonical-parent)
    - [2.6 7_canonical_hierarchy](#26-7_canonical_hierarchy)
      - [2.6.1 Rule: prune to same-graph edges](#261-rule-prune-to-same-graph-edges)
      - [2.6.2 Known consequence — non-genre orphans vanish](#262-known-consequence--non-genre-orphans-vanish)
      - [2.6.3 Under exploration — root count](#263-under-exploration--root-count)
    - [2.7 8_regional_hierarchy](#27-8_regional_hierarchy)
      - [2.7.1 Rule: prune to same-graph edges](#271-rule-prune-to-same-graph-edges)
      - [2.7.2 Known consequence — regional seeds become real nodes](#272-known-consequence--regional-seeds-become-real-nodes)

## 1. Bronze

Three queries, three Parquet files — see [SCHEMA.md#2-bronze](SCHEMA.md#2-bronze) for their column
definitions and data profiles.

### 1.1 wikidata_genre_tree.parquet

#### 1.1.1 Why `P31`, not a `P279*` walk from `Q188451`

The intuitive query — "every item transitively `P279` subclass-of music genre" — returns only 14
items (verified live), mostly _meta-categories_ rather than actual genres:

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

#### 1.1.2 Parents are not restricted to also being a music genre instance

A genre's `P279`/`P361` edges routinely point at non-genre classes too — e.g. "opera" (`Q1344`) is
`P279` both "classical music" and "composed musical work" (`Q207628`, not itself `P31` music
genre).

Bronze ingests this raw and unfiltered, consistent with the "as-is" bronze principle used for
MusicBrainz's tables (see [`../musicbrainz/SCHEMA.md`](../musicbrainz/SCHEMA.md)). Picking a single
main parent per item is Silver-layer work — see
[`2.4 5_main_parent_selection`](#24-5_main_parent_selection) (single-parent selection),
[`2.5 6_canonical_parents`](#25-6_canonical_parents) (flagging whether that parent is itself a
genre), and [`2.6 7_canonical_hierarchy`](#26-7_canonical_hierarchy)/
[`2.7 8_regional_hierarchy`](#27-8_regional_hierarchy) (pruning) below.

#### 1.1.3 Bare QIDs, not full entity URIs

Wikidata's SPARQL results return full entity URIs (`http://www.wikidata.org/entity/Q11399`), not
bare QIDs. `ingest.py` strips the `http://www.wikidata.org/entity/` prefix before writing Parquet,
since the QID is the natural join key and the full URI is otherwise dead weight. Labels are passed
through as-is. This applies to all three Bronze queries, not just this one.

### 1.2 wikidata_genre_indigenous_to.parquet and wikidata_genre_country_of_origin.parquet

#### 1.2.1 Why separate tables, not extra columns on `wikidata_genre_tree.parquet`

`P2341` ("indigenous to") and `P495` ("country of origin") are per-item ethnographic/provenance
attributes, not genre-to-genre taxonomy edges like `P279`/`P361`. Their cardinality is independent
of an item's parent count — an item can have any number of `P279`/`P361` parents and, separately,
any number of `P2341` or `P495` values.

Querying either alongside `P279`/`P361` in a single row (the way `GENRE_TREE_QUERY` handles
`P279`/`P361` together, since those share the same "parent edge" semantics) would cross-multiply
the extra `OPTIONAL` into spurious combinations — e.g. an item with 2 parents and 3 `P2341` values
would produce 6 rows instead of 2 + 3.

Each is therefore its own query, producing its own (item, value) table. See
`wikidata_client.INDIGENOUS_TO_QUERY` / `COUNTRY_OF_ORIGIN_QUERY`, and
[2.3 4_regional_classification](#23-4_regional_classification) below for how each is used
downstream.

## 2. Silver

### 2.0 1_item_links — display-label casing

`item_label`/`parent_label` stay the raw, untouched Wikidata string everywhere — every exact-match
comparison downstream (`genre_match.py`, `genre_tree_builder.py`'s pop_sides matching,
`canonical_genre_tree_export.py`'s root/child lookups, every manual CSV that references a genre by
name) keys off it. `item_display_label`/`parent_display_label` is the separate, display-only field
gold's `genre_tree_builder` actually emits as a tree node's `"name"`, derived with this precedence:

1. `manual_label_overrides.csv` (highest — a data expert's explicit pick, e.g. "pop music" ->
   "Mainstream Pop").
2. Sentence-case the raw label (default, when no override exists): first, replace any whole word
   (hyphen- or space-delimited) that matches an entry in `manual_capitalized_words.csv` — a
   git-tracked, pre-seeded list of country demonyms, continent/region adjectives, and common
   compound-adjective prefixes (afro-, anglo-, etc.) — with its capitalized form, wherever it
   appears in the label (not just at the start); then capitalize the label's first character if
   it isn't already. This is deliberately not "lowercase everything then capitalize the first
   letter" — that would destroy legitimate mid-string proper-noun capitalization Wikidata already
   provides (e.g. "music of Kenya" must not become "Music of kenya").

Gaps in the seeded word list (a proper noun that isn't a demonym, e.g. a person or place name) get
added to `manual_capitalized_words.csv` the same way other manual CSVs in this pipeline grow over
time — this one just starts pre-seeded with a comprehensive list of demonyms instead of starting
empty.

### 2.1 2_non_genre_pruning

Five git-tracked, hand-curated CSVs (same columns: `item_id`, `item_label`, `reason`) each list
items that no automated signal distinguishes from a real genre, so a data expert reviewing the
root lists adds them by hand. Every `item_id` across all five is dropped from the genre tree
entirely (unknown `item_id`s raise), right after `1_item_links` and before any other classification
step runs — so a dropped item can never sit on a cascade path and hand its `is_regional` status
down to a real genre beneath it, and can never survive as a dangling parent for
[7_canonical_hierarchy](#26-7_canonical_hierarchy)/[8_regional_hierarchy](#27-8_regional_hierarchy)'s pruning stage to sever later:

- `manual_theme_genres.csv` — genre items organized around a subject/theme/subculture (e.g. "LGBT
  music", "steampunk music", "bronycore") rather than a geography, ethnicity, or musical style.
- `manual_technique_genres.csv` — compositional or performance techniques (e.g. "crab canon",
  "fauxbourdon", "call and response") rather than a genre at all.
- `manual_out_of_scope_genres.csv` — items that aren't a music genre at all, i.e. Wikidata's `P31`
  "music genre" classification was simply wrong (e.g. a near-empty stub with no real description,
  a record label, an event, a person) — as opposed to a real but off-topic genre
  (`manual_theme_genres.csv`) or a technique (`manual_technique_genres.csv`).
- `manual_umbrella_canonical_genres.csv` — genuine music genres, correctly classified, but too
  broad to serve as a useful canonical grouping node (e.g. "alternative music", spanning countless
  pop/rock-deviating subgenres with no single coherent style) — as opposed to a misclassification
  (`manual_out_of_scope_genres.csv`), an off-topic theme (`manual_theme_genres.csv`), or a
  technique (`manual_technique_genres.csv`). Dropping it lets its children (or the item itself, if
  parentless) surface as their own canonical roots instead of collapsing under one umbrella node.
- `manual_duplicate_genres.csv` — a genuine Wikidata duplicate: two distinct items sharing the same
  display name, where one is a near-empty stub duplicating a better-described item (e.g. "meme
  techno", `Q25408203`, a stub duplicating `Q114238485`) — as opposed to two real genres that
  happen to share a name (a homonym, resolved by renaming via
  `manual_label_overrides.csv`, not by dropping either). `grow-the-music-tree-api` rejects an
  imported tree containing duplicate node names, and `gold`'s `genre_tree_builder` raises before
  export if one slips through — this CSV, and homonym renaming, are how those duplicates get
  resolved upstream.

This runs as the very first classification step, before `3_regional_overview_classification` and
`4_regional_classification`, because none of this is about region — it's non-genre pruning, and
pruning it out as early as possible (rather than after regional-overview classification, which was
this step's original position) keeps every step downstream working with a smaller, cleaner tree
without changing what any of them actually decide.

### 2.2 3_regional_overview_classification

#### 2.2.1 Why classification is needed

Wikidata's `P31` "instance of" `Q188451` ("music genre") class extension — Bronze's source query —
is noisy. It includes items that are not themselves musical styles, e.g. "music of Kenya" (a
country's music scene overview, not a style). Left unflagged, these would pollute any genre
hierarchy or genre-matching built on top of this data.

#### 2.2.2 `classification_reason` values

| Value                              | Rule                                                        | Rationale                                                                                                                                                                                                                                                                                             |
| ---------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `regional_overview`                | `item_label` starts with `"music of "`                      | Wikidata's national/regional music overview articles (e.g. "music of France", "music of Kenya") — ~300 of ~6,300 items as of this writing, always this exact prefix, never a genre name.                                                                                                              |
| `manual_overview_reclassification` | `item_id` listed in `manual_overview_reclassifications.csv` | An existing genre item (already has its own Bronze row and a real `P279`/`P361` parent) that a data expert has decided plays the same non-genre regional-overview role despite not carrying the `"music of "` label prefix — see [2.2.5](#225-manual-reclassification-of-existing-items-as-overview). |

This tags, it does not exclude: `regional_overview` items stay in every downstream Parquet file
and are the seed set [`2.3 4_regional_classification`](#23-4_regional_classification) propagates
`is_regional` down from — any genre item with a parent edge into one of these becomes a regional
genre, and the seeds themselves become regional genre nodes in their own right (see
[`2.6 7_canonical_hierarchy`](#26-7_canonical_hierarchy) and [`2.7 8_regional_hierarchy`](#27-8_regional_hierarchy)).

#### 2.2.3 Auto-promotion of orphan `"music of "` parents

Before the `classification_reason` rule above runs, this step also scans `parent_label` (not just
`item_label`) for the `"music of "` prefix.

Some regional-overview items (e.g. "music of Wales", `Q6942327`) are never themselves classified
`P31` instance-of music genre, so Bronze never fetches their own row — they only ever show up as a
`parent_label` on their subgenres (e.g. "Welsh folk music"). Left alone, such an item is invisible
to the `classification_reason` rule above and cannot legally be used as a
`manual_regional_overrides.csv` `overview_item_id` target (`4_regional_classification` requires
the target to already exist and be flagged `is_regional_overview`).

For every distinct `(parent_id, parent_label)` pair matching the prefix whose `parent_id` has no
`item_id` row of its own, this step synthesizes one root row (`parent_id`/`parent_label`/
`relation_type` null, same shape as any other unparented item) before the prefix classification
runs, so the promoted row gets `is_regional_overview = True` "for free."

This is purely mechanical — the id/label pair is already present in Bronze as a `parent_label`, so
no live Wikidata fetch is involved (Silver never fetches raw data — see `CLAUDE.md`) — unlike
deciding which broader region a promoted item nests under (e.g. Wales → United Kingdom), which
stays a manual `manual_regional_overrides.csv` entry.

#### 2.2.4 Manual addition of overview items missing from Bronze entirely

Some real "music of `<place>`" Wikidata items never appear in Bronze at all — not as their own
`item_id` row (they're not `P31` instance-of music genre, e.g. "music of Dominica" is `P31`
instance-of a different class) and not as any item's `parent_label` either (so orphan-promotion
above can't reach them). Such an item is invisible to this step and cannot legally be used as a
`manual_regional_overrides.csv` `overview_item_id` target.

`manual_regional_overview_additions.csv` (`src/wikidata/silver/manual_regional_overview_additions.csv`,
git-tracked, hand-curated — columns `item_id,item_label,reason`) is the backstop: a data expert who
has looked up the item's real Wikidata QID adds a row here, and this step synthesizes it as a root
row (same shape as an auto-promoted orphan) before the `classification_reason` rule runs, so it
gets `is_regional_overview = True` "for free" and becomes a legal `overview_item_id` target.

This is still not a live fetch — the id/label pair is authored by hand, same as
`manual_regional_overrides.csv` (Silver never fetches raw data — see `CLAUDE.md`) — and the
pipeline fails fast if a row's `item_label` doesn't start with `"music of "` or its `item_id` is
already present in the genre tree (in which case it doesn't need manual addition).

`item_id` is normally a real Wikidata QID, but a synthetic id (e.g. `LOCAL:indigenous-americas`)
is allowed as a last resort when no matching real Wikidata "music of `<place>`" item exists at all
— not every Gold-layer grouping concept has a Wikidata counterpart. Such rows still need
`item_label` to start with `"music of "`; their `item_url` is built the same way as any other row
and simply won't resolve to a real Wikidata page.

#### 2.2.5 Manual reclassification of existing items as overview

Unlike 2.2.4's case, some items are the opposite problem: they already have their own row in
Bronze/`1_item_links` (usually with a real `P279`/`P361` parent edge, making them look like an
ordinary genre) but a data expert has decided the item actually functions as a non-genre regional
overview node — it just doesn't carry the `"music of "` label prefix the automated rule keys off
of. "European folk music" (`Q98528192`) is the motivating example: a continent-wide folk-music
umbrella that is parent to dozens of national folk genres (Hungarian folk music, Nordic folk
music, ...), functionally identical to a "music of Europe" overview item, but named without the
prefix.

`manual_overview_reclassifications.csv`
(`src/wikidata/silver/manual_overview_reclassifications.csv`, git-tracked, hand-curated — columns
`item_id,item_label,reason`) is the backstop for this case: a data expert lists the item's QID and
current label, and this step flags it `is_regional_overview = True` /
`classification_reason = "manual_overview_reclassification"` directly, without touching its label.

This is the mirror image of `manual_regional_overview_additions.csv` in every validation rule:
`item_label` here does **not** need the `"music of "` prefix (that's the whole point — these are
exactly the items the prefix rule misses), but `item_id` **must** already be present in the genre
tree with a matching `item_label`, and must not already be flagged `is_regional_overview` by the
prefix rule (nothing to reclassify in that case). As usual, the pipeline fails fast on any row that
violates these constraints, on blank `item_id`/`item_label`, or on duplicate `item_id` rows.

Reclassifying an item this way excludes it from `7_canonical_hierarchy` as a canonical genre — it becomes a
scaffolding node in `8_regional_hierarchy` only, the same as any other `regional_overview` item.
It's also included in `4_regional_classification`'s seed set (see
[2.3.2](#232-rule-four-seed-sources)), so regional status still cascades correctly to its
children.

#### 2.2.6 Scope of this first pass

This is a first classification pass covering the single highest-confidence, most mechanical rule
found during analysis. Other non-genre categories are pruned separately, in their own dedicated
step, `2_non_genre_pruning` (see [2.1](#21-2_non_genre_pruning)), which runs before this one — via
manual CSV backstops rather than an automated rule here.

### 2.3 4_regional_classification

#### 2.3.1 Inputs

`4_regional_classification` also reads Bronze `wikidata_genre_indigenous_to.parquet` (see
[SCHEMA.md#2-bronze](SCHEMA.md#2-bronze)) to catch nationally/ethnically-specific genres that have
no `P279`/`P361` parent for the cascade below to propagate through in the first place, plus a
git-tracked, hand-curated CSV (`src/wikidata/silver/manual_regional_overrides.csv`, not Bronze —
it's authored by a data expert, not fetched from Wikidata) for the rare item the automated sources
still miss.

Bronze `wikidata_genre_country_of_origin.parquet` (`P495`, "country of origin") is deliberately
**not** read here — see [SCHEMA.md#2-bronze](SCHEMA.md#2-bronze) for why (it's set on broad
canonical umbrella genres too, e.g. jazz, heavy metal music, which would wrongly cascade regional
status onto their real subgenres).

`P2341` has the same false-positive pathology on rare occasions (e.g. "classical music" carries
`indigenous_to = Europe`, a continent, not a specific people/culture), but unlike `P495` it can't be
blanket-excluded — it's also the *only* regional signal for real regional genres with no other
parent-based or property-based signal at all (e.g. "Han Chinese music"). A git-tracked,
hand-curated `src/wikidata/silver/manual_indigenous_to_exclusions.csv` (columns
`item_id,item_label,reason`) lets a data expert drop specific false-positive `item_id`s from the
`indigenous_to` seed source only, one at a time, before the seed set is built. Each `item_id` must
already carry a `P2341` value in Bronze `wikidata_genre_indigenous_to.parquet` — the pipeline
raises otherwise, since an exclusion with nothing to exclude is almost certainly a stale/typo'd
entry.

#### 2.3.2 Rule: four seed sources

Three kinds of items seed the regional graph and are themselves flagged `is_regional = True`, not
merely a launching point for other items:

- `regional_overview` items (from `3_regional_overview_classification`, e.g. "music of Kenya",
  "music of Cape Verde"), including items reclassified into that same role via
  `manual_overview_reclassifications.csv` despite not carrying the `"music of "` prefix (e.g.
  "European folk music" — see [2.2.5](#225-manual-reclassification-of-existing-items-as-overview))
  — `regional_reason = "seed"`.
- Items with at least one `P2341` ("indigenous to") value in Bronze
  `wikidata_genre_indigenous_to.parquet` (e.g. "Han Chinese music") — `regional_reason =
"indigenous_to"`. Unlike `regional_overview` seeds these are ordinary genre items, not non-genre
  overview articles, and are often roots with no `P279`/`P361` parent at all — the parent-based
  cascade has nothing to reach them through, so they need this direct, independent signal instead.
- Items listed by `item_id` in `manual_regional_overrides.csv` — `regional_reason =
"manual_override"`. A fallback for genres the structural/property-based sources above don't
  catch — typically a root item with no `P279`/`P361` parent and no `P2341` value either (e.g.
  "mezwed", a Tunisian genre with neither signal). Each entry carries a `reason` column explaining
  why a data expert added it; see the file itself for the current list.

  Because these override items typically have no `P279`/`P361` parent at all, they'd otherwise
  surface as their own orphan roots in `7_regional_hierarchy` instead of nesting under their
  region — a required `overview_item_id` column gives the override item's `item_id` the `item_id`
  of a `regional_overview` item (e.g. "music of Japan" — normally a real QID, but see
  `manual_regional_overview_additions.csv` above for the synthetic-id fallback) as a synthetic
  parent edge (`relation_type = "manual_override_parent"`), replacing its null-parent row. Every
  row must set it; a row with it missing or blank fails the pipeline at this step rather than
  silently leaving the item an orphan root.

  This synthetic edge does **not** automatically win `5_main_parent_selection`'s lowest-QID
  fallback the way `manual_main_parent.csv`'s edge always does — it's just another candidate
  parent edge. If the item already has a genuine competing parent edge with a lower numeric QID,
  that edge wins main-parent selection instead, and the override silently fails to nest the item
  under its intended overview even though the synthetic edge still exists (it stays `is_regional`
  either way, since regional status only needs *any* parent edge into a seed, but the item can end
  up parented to the wrong node instead of its overview umbrella). An optional
  `exclude_other_parents` column (same shape and meaning as `manual_main_parent.csv`'s, see
  [2.3.4](#234-manual_main_parentcsv-pinning-an-items-main-parent)) fixes this: set it to `"true"`
  to drop the item's other candidate parent edges too, leaving the override edge as the only
  candidate so it always wins main-parent selection. Leave it blank (the default) to preserve the
  existing behavior for items where the override only needs to establish `is_regional`, not control
  which node the item nests under.

#### 2.3.3 Cascade

A genre item is regional if **any one** of its parent edges points at any kind of seed, or at an
item already flagged regional — propagated down as a multi-source cascade, repeated to a fixpoint.
`regional_reason` is `"direct"` when the item's own parent set includes a seed
(`regional_overview`, `indigenous_to`, or `manual_override`) directly, `"inherited"` when it only
reaches regional status via an already-flagged parent that isn't itself a seed.

> ⚠️ **ANY-parent, not ALL-parent — confirmed by a real multi-parent case.** A naive "every parent
> trail dead-ends in a seed" rule would miss real regional genres that also happen to have a clean
> secondary parent: "Australian rock" has one parent edge into "rock music" (a clean canonical
> genre, no regional signal) and another into "music of Australia" (a seed) — live data confirms
> it's still correctly flagged regional.
>
> Having _any_ parent edge into a regional item is sufficient, regardless of whether the item also
> has a clean parent elsewhere. This structural rule alone catches both "morna" (direct seed hit)
> and "fado" (inherited, two hops through "Portuguese folk music") without any manual help — the
> curated override list above exists only for items the structural rule and the `P2341` signal all
> miss entirely.

> ⚠️ **Exploration phase — this rule will evolve.** Cascading from _every_ `regional_overview`
> seed, including continent-level overview articles ("music of Asia", "music of Europe", "music of
> Africa", "music of the Americas") alongside country/ethnic-level ones ("music of Kenya", "music
> of Cape Verde"), currently flags **~61% of all items** as regional (see
> [SCHEMA.md#35-4_regional_classification](SCHEMA.md#35-4_regional_classification) for the
> profile) — well more than the ~367-item vanished-from-hierarchy baseline that originally
> motivated this step. That's largely because continent-level seeds have large direct fan-out
> (e.g. "A-pop" is a direct child of "music of Asia").
>
> `P495` ("country of origin") was considered as an additional seed source but deliberately
> excluded — see [1. Bronze](#1-bronze) above — because it's also set on broad canonical umbrella
> genres (jazz, heavy metal music, etc.), which would wrongly flag their real subgenres regional
> too.
>
> This is being kept as-is for now since the pipeline is still in an exploration phase, not
> shipped as a settled design decision — narrowing the seed set to exclude continent-level
> overview articles (so only country/ethnic-level pages seed the cascade) is a likely future
> refinement once there's a concrete product need to get the regional/canonical split tighter.

> ⚠️ **Known Bronze gap, not yet investigated:** "variété française," a named example of a regional
> genre, does not appear anywhere in the current Bronze extraction at all — not a `P279`/`P361`
> gap, it's simply absent from the `P31` "music genre" class extension entirely. This needs deeper
> investigation into why Wikidata's own query misses it (wrong assumed label, different
> instance-of class, etc.) rather than being treated as a non-issue.

#### 2.3.4 `manual_main_parent.csv` main-parent override

Before the regional cascade ([2.3.3](#233-cascade)) runs, this step also reads a git-tracked,
hand-curated `manual_main_parent.csv` (columns: `item_id`, `item_label`, `reason`,
`parent_item_id`) and applies each row as a synthetic parent edge, replacing the item's null-parent
root row if it had one. Running this before the cascade lets `is_regional` flow naturally through
the injected edge, the same as any other parent edge.

It's a data expert's explicit pick of an item's main parent — usable for any item, not just roots
with no `P279`/`P361` parent (e.g. a cross-national fusion genre like "metal prehispánico" →
"heavy metal music", or overriding a mis-collapsed multi-parent case such as toypop → J-pop). The
synthetic edge is tagged `relation_type = "manual_main_parent"` and always wins as the item's main
parent in [2.4 5_main_parent_selection](#24-5_main_parent_selection), regardless of how many other
candidate parent edges the item already has — those other edges aren't dropped, they just become
secondary parents (see [2.4.2](#242-secondary-parents-are-kept-not-dropped)).

The pipeline fails fast if `parent_item_id` is missing/blank, if `item_id` or `parent_item_id`
isn't a known item in the tree, if `item_id` has more than one row in the CSV, or if `parent_item_id`
or `item_id` itself is flagged `is_regional_overview` (that's what `manual_regional_overrides.csv`
is for).

An optional `exclude_other_parents` column (`"true"`/blank, default blank = keep other edges) drops
an item's *other* candidate parent edges entirely, instead of just adding one alongside them. This
matters because `is_regional` is computed from *any* of an item's parent edges
([2.3.3](#233-cascade)), not just its eventual main parent — an item with a genuine conflicting edge
into the regional seed set (e.g. "reggae" → "music of Jamaica" via a real `P279` edge) would stay
`is_regional = True` via that edge regardless of a `manual_main_parent.csv` override, unless that
edge is excluded too. Used sparingly: it's a real behavior change (secondary parents are normally
kept, see [2.4.2](#242-secondary-parents-are-kept-not-dropped)), reserved for cases where an item
needs to leave the regional graph entirely, not just get a better main parent.

`manual_main_parent.csv`'s `parent_item_id` is usually a real Wikidata item already in the tree, but
can also be a synthetic grouping node with no Wikidata counterpart (e.g. "Reggae/Dub (grouping)",
grouping "reggae" and "dub music" — no single Wikidata item represents that pairing). Such nodes are
added via a third git-tracked, hand-curated CSV, `manual_canonical_parent_additions.csv` (columns:
`item_id`, `item_label`, `reason`), applied just before `manual_main_parent.csv` so the new node is a
legal `parent_item_id` target. `item_id` must start with `LOCAL:` (never a fabricated QID-shaped id)
and must not already exist in the tree — the mirror image of the synthetic-id fallback in
`manual_regional_overview_additions.csv` ([2.2](#22-3_regional_overview_classification)), but for
the canonical side instead of the regional-overview side.

#### 2.3.5 Naming synthetic grouping nodes

Gold's `slugify_genre_name` (`pipelines/gold/src/gold/genre_slug.py`) normalizes a node's display
name into an id by collapsing every run of non-alphanumeric characters (including both `/` and
plain spaces) to a single `-`. That means a synthetic grouping node named e.g. "Blues/Rock" and a
real Wikidata item labeled "blues rock" slugify to the same id (`blues-rock`) even though they're
different tree nodes — a real collision hit in production when the real Wikidata item "blues rock"
(Q193355, a genuine subgenre of blues) turned out to share every word with the unrelated synthetic
`LOCAL:blues-rock` grouping node.

Convention: every synthetic (`LOCAL:`-prefixed) grouping node's `item_label` in
`manual_canonical_parent_additions.csv` (and any place that references it by label, e.g.
`manual_canonical_genre_pop_side.csv`'s `root_genre_name`) carries a trailing `" (grouping)"` suffix,
e.g. `"Blues/Rock (grouping)"`, `"Disco/Funk (grouping)"`, `"Reggae/Dub (grouping)"`. This guarantees
the slug stays distinct from any real Wikidata item's slug regardless of word overlap, rather than
relying on no real genre ever sharing the same words as a grouping label. Apply this suffix to every
new `LOCAL:` grouping node going forward.

### 2.4 5_main_parent_selection

#### 2.4.1 Rule: manual override, else lowest-QID heuristic

Wikidata's `P279`/`P361` graph isn't a strict tree: many genre items have more than one surviving
genre parent after `4_regional_classification`. This step picks exactly one **main** parent per
item:

- If the item has a `manual_main_parent.csv` synthetic edge (`relation_type =
  "manual_main_parent"`, applied upstream in [2.3.4](#234-manual_main_parentcsv-main-parent-override)),
  that edge always wins — a data expert's explicit pick.
- Otherwise, among the item's candidate parents, one that is itself a genre item in this dataset
  (i.e. present as its own `item_id`, not just referenced as a `parent_label` — e.g. "modern
  classical music") is preferred over one that isn't (e.g. "Expressionism", an art-movement item
  with no `item_id` row of its own); the lowest numeric QID is the final tiebreak among whatever's
  left. Preferring a real genre item avoids picking a non-genre parent that can never itself be
  `parent_is_canonical` — which would otherwise orphan the item as a root in
  `7_canonical_hierarchy` (via `hierarchy_utils.py`'s `promote_orphans_to_roots`) even though a
  genuine genre parent was available all along.

> ⚠️ **Provisional / trial-and-error:** the lowest-QID fallback is a placeholder, not a considered
> rule. Live SPARQL queries against the real genre extension found that 2,727 of ~6,337 genre items
> (~43%) have more than one `P279` parent even after Wikidata's own "best rank" resolution, and only
> 1 item in the entire genre extension has any `P279` statement marked preferred rank —
> so there's no cheap Wikidata-native signal to prefer. `musicbrainz/README.md` already documents
> that the eventual single-parent hierarchy format for TheMusicTreeAPI is "not yet decided" — this
> heuristic is a stand-in until that product/curation decision exists, with `manual_main_parent.csv`
> as the escape hatch for any specific item it gets wrong in the meantime.

#### 2.4.2 Secondary parents are kept, not dropped

Every candidate parent edge that isn't selected as an item's main parent is written to a sibling
`5_secondary_parents.parquet` file rather than discarded — see
[SCHEMA.md#36-5_main_parent_selection](SCHEMA.md#36-5_main_parent_selection) for its columns. Root
items (a single null-parent candidate) and items with only one candidate parent never produce
secondary rows.

### 2.5 6_canonical_parents

#### 2.5.1 Rule: what counts as a canonical parent

A parent counts as an actual musical style only if it is flagged `is_regional_overview = False` by
`3_regional_overview_classification` — not merely present in Bronze's raw `P31` "music genre"
extension. This keeps the Silver steps agreeing with each other: an edge into a `regional_overview`
item like "music of Kenya" is `parent_is_canonical = False`, the same as an edge into a concept that
was never `P31` "music genre" at all (e.g. "opera" → "composed musical work").

Non-genre parents span both a genre item tagged non-genre in step 3 (e.g. an edge into "music of
Tanzania") and a parent that was never in Bronze's `P31` "music genre" extension at all (e.g.
"national song" → "national anthem", "Renaissance music" → "Renaissance art") — both count as
`parent_is_canonical = false` under the rule above.

### 2.6 7_canonical_hierarchy

`7_canonical_hierarchy.parquet` is the first step to prune based on the
`is_regional`/`is_regional_overview`/`parent_is_canonical` flags built up by the prior
classification steps: it reduces `6_canonical_parents.parquet` to one row per non-regional genre
item, producing a clean, directly-consumable canonical genre hierarchy edge list — excluding every
`is_regional = true` item, which now includes the `regional_overview` seed items themselves (see
[2.7 8_regional_hierarchy](#27-8_regional_hierarchy) for those).

#### 2.6.1 Rule: prune to same-graph edges

Keep a row only if the item itself is _not_ `is_regional` (which already excludes every
`is_regional_overview` item — see [2.3.2](#232-rule-four-seed-sources), `regional_overview` items
are always seeded as `is_regional = True` too), and either `parent_id` is null (a root) or
`parent_is_canonical = True` and the parent is not itself regional. An edge into a non-canonical or
regional parent doesn't count as a legitimate hierarchy parent.

No further multi-parent collapse happens here: `5_main_parent_selection` already reduced every item
to a single candidate parent upstream (see [2.4 5_main_parent_selection](#24-5_main_parent_selection)),
so this step only ever prunes edges, never chooses among several.

#### 2.6.2 Known consequence — non-genre orphans vanish

An item whose main parent edge points to a non-canonical or regional parent (and which isn't
itself a root) has its row dropped — it disappears entirely, not even as an implicit root (e.g.
"opera" → "composed musical work").

Check `profile_hierarchy`'s "zero surviving rows in either output" count (see
[SCHEMA.md#38-7_canonical_hierarchy](SCHEMA.md#38-7_canonical_hierarchy)) for how often this
vanishing happens.

#### 2.6.3 Under exploration — root count

> ⚠️ **Under exploration:** `7_canonical_hierarchy` surfaces a high number of root items
> (`parent_id = null`) — not the small handful a genre tree with one or two top-level categories
> (e.g. "music") would suggest. Whether that many roots is a real property of the source data
> (genuinely disconnected genre subtrees) or an artifact of upstream pruning/selection rules (e.g.
> [2.4.1](#241-rule-manual-override-else-lowest-qid-heuristic)'s lowest-QID fallback severing an
> item from its more meaningful parent) is not yet determined — see
> `pipelines/wikidata/notebooks/explore_genre_tree.ipynb` for the current exploration of these
> roots' relevance.
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
> classical music, etc. — not the hundreds of roots it currently produces. Getting there is
> expected to be mostly a linking/cleaning problem (correcting mis-selected main parent edges, e.g.
> the [2.4.1](#241-rule-manual-override-else-lowest-qid-heuristic) lowest-QID heuristic above)
> rather than a new extraction or classification mechanism. `8_regional_hierarchy` follows different
> logic entirely and is **not** expected to converge to a small root count: one root per
> cultural/geographic region (e.g. "music of Cape Verde"), with that region's own genres nested
> underneath it.

### 2.7 8_regional_hierarchy

`8_regional_hierarchy.parquet` mirrors `7_canonical_hierarchy`'s pruning, restricted to
`is_regional = true` items — which now includes the `regional_overview` seed items themselves,
rather than being dropped: it reduces `6_canonical_parents.parquet` to one row per regional genre
item, producing a clean, directly-consumable regional genre hierarchy edge list.

#### 2.7.1 Rule: prune to same-graph edges

Keep a row only if the item _is_ `is_regional`, and either `parent_id` is null or the parent is
itself `is_regional = True`. An edge into a non-regional parent, or across the canonical/regional
boundary, doesn't count as a legitimate hierarchy parent.

No further multi-parent collapse happens here either, for the same reason as
[2.6.1](#261-rule-prune-to-same-graph-edges): `5_main_parent_selection` already reduced every item
to a single candidate parent upstream.

#### 2.7.2 Known consequence — regional seeds become real nodes

A `regional_overview` seed like "music of Cape Verde" is now a real node with its own real parent
chain (or a genuine root, if it has no `P279`/`P361` parent at all) rather than being dropped — so
an item like "morna," whose only parent is that seed, keeps its real parent edge instead of being
promoted to a synthetic root itself.

### 2.8 9_canonical_roots

`9_canonical_roots.parquet` extracts `7_canonical_hierarchy`'s root items (`parent_id` null, or
pointing at an item with no row of its own) for manual triage — see
`.claude/skills/wikidata-canonical-roots/SKILL.md`.

#### 2.8.1 `manual_accepted_canonical_roots.csv` guard-rail

A root that isn't already in the git-tracked `manual_accepted_canonical_roots.csv` is new since the
last triage pass and raises, rather than silently reappearing in the output — a curator must give
it a real parent (`manual_main_parent.csv`), flag it as theme/technique/out-of-scope (§2.1), or add
it to `manual_accepted_canonical_roots.csv` once confirmed genuinely standalone.

This is deliberately diff-based rather than a blanket check: dropping a broad umbrella item (e.g.
"popular music") as theme/technique/out-of-scope legitimately orphans many real subgenres into
roots (rock, jazz, pop, ska, …) — that's the intended effect of the drop, not a bug. A check that
raised on any orphaned child, rather than only on ones absent from the previously-accepted set,
would fire on that entire pre-existing backlog every run.
