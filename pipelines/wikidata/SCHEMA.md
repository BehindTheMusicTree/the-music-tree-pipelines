# Schema

Data dictionary and lineage notes for `wikidata`. See [README.md#pipeline](README.md#pipeline) for the Bronze layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Wikidata properties used](#wikidata-properties-used)
  - [Bronze](#bronze)
  - [Silver](#silver)

## Wikidata properties used

Wikidata models knowledge as items (`Q...` IDs) connected by properties (`P...` IDs). Two
properties drive this pipeline's whole shape:

- **`P31` ("instance of")** — links an item to the class it directly belongs to. `wd:Q11399`
  ("rock music") `wdt:P31` `wd:Q188451` ("music genre") means "rock music is a music genre." This
  is Wikidata's class-membership edge — it's how Bronze finds the full set of genre items in the
  first place (see `GENRE_TREE_QUERY`'s `?item wdt:P31 wd:Q188451` clause).
- **`P279` ("subclass of")** — links a class to its more general parent class(es), building a
  taxonomy. `wd:Q11399` ("rock music") `wdt:P279` `wd:Q373342` ("popular music") means "rock music
  is a kind of popular music." This is the edge that builds the genre *hierarchy* — a genre can
  have more than one `P279` parent, since Wikidata classes aren't a strict tree.

The two are not interchangeable and don't chain into each other the way you might expect — see
below.

## Bronze

One Parquet file, `wikidata_genre_tree.parquet`, one row per (item, parent) edge: every Wikidata
item classified `P31` ("instance of") `Q188451` ("music genre") — the class extension, ~6,300
items as of this writing — plus each genre's direct `P279` ("subclass of") parent(s). See
`wikidata_client.GENRE_TREE_QUERY` for the exact SPARQL.

**Why `P31`, not a `P279*` walk from `Q188451`:** the intuitive query — "every item transitively
`P279` subclass-of music genre" — returns only 14 items (verified live), mostly *meta-categories*
rather than actual genres:

> `gharana`, `palo`, `game piece`, `opera genre`, `fusion music genre`, `jazz genre`, `electronic
> music genre`, `blues genre`, `folk music genre`, `world music genre`, `rock genre`, `music by
> instrument`, `Shengqiang`, plus `Q188451` itself.

Note the pattern: `"jazz genre"`, `"rock genre"` are *classes of genre*, not genres — not the
~6,300 actual genres like "rock music" or "bebop" that Bronze needs.

That's because Wikidata keeps the two relationships separate:

- **Class membership** is `P31` — e.g. `wd:Q11399` "rock music" `wdt:P31` `wd:Q188451` "music genre".
- **Subgenre hierarchy** is `P279`, but *between genre items* — e.g. `wd:Q11399` "rock music"
  `wdt:P279` `wd:Q373342` "popular music".

That `P279` edge doesn't chain back up to `Q188451`. Confirmed live:

```sparql
ASK { wd:Q373342 wdt:P279* wd:Q188451 }   # → false
```

So a `P279*` walk from `Q188451` finds only the 14 meta-category items above, and silently misses
"rock music" and every other real genre — their `P279` parent chains lead to broader *concepts*
like "popular music", not back to the "music genre" class they're an *instance* of.

**Parents are not restricted to also being a music genre instance.** A genre's `P279` edges
routinely point at non-genre classes too — e.g. "opera" (`Q1344`) is `P279` both "classical
music" and "composed musical work" (`Q207628`, not itself `P31` music genre). Bronze ingests this
raw and unfiltered, consistent with the "as-is" bronze principle used for MusicBrainz's tables
(see [`../musicbrainz/SCHEMA.md`](../musicbrainz/SCHEMA.md)); pruning to genre-only parents, if
needed, is Silver-layer work. An item with no `P279` parent at all (a root, ~510 of them) gets a
single row with `parent_id`/`parent_label` both null.

| Column        | Type   | Meaning                                             |
| ------------- | ------ | ---------------------------------------------------- |
| item_id       | str    | Wikidata QID of the genre (e.g. `Q11399`)             |
| item_label    | str    | English label for `item_id` (e.g. "rock music")      |
| parent_id     | str?   | QID of a direct `P279` parent within the genre tree, or null |
| parent_label  | str?   | English label for `parent_id`, or null                |

**Deliberate deviation from the raw query response**: Wikidata's SPARQL results return full
entity URIs (`http://www.wikidata.org/entity/Q11399`), not bare QIDs — `ingest.py`
strips the `http://www.wikidata.org/entity/` prefix before writing Parquet, since the QID is the
natural join key and the full URI is otherwise dead weight. Labels are passed through as-is.

**Independent of MusicBrainz for now**: this pipeline ingests Wikidata's genre taxonomy on its
own terms — it is not filtered or matched against `musicbrainz`'s `genre.name` list at this
stage. That matching (fuzzy name-match, no shared key between the two sources) is a future
step, not built yet.

A multi-parent item (Wikidata classes aren't a strict tree — a genre can have more than one
`P279` parent) produces one row per parent, so `item_id` is not unique on its own.

## Silver

`1_classification.parquet`, produced by `wikidata.silver`: the Bronze edge list unchanged,
plus two columns classifying each row's `item_id` as a real genre or not.

| Column           | Type   | Meaning                                                                 |
| ---------------- | ------ | ------------------------------------------------------------------------ |
| is_genre         | bool   | `False` if `item_label` was classified as not a genre                    |
| exclusion_reason | str?   | Why `is_genre` is `False` (see below), or null when `is_genre` is `True` |

**Why classification is needed:** Wikidata's `P31` "instance of" `Q188451` ("music genre") class
extension — Bronze's source query — is noisy. It includes items that are not themselves genres,
e.g. "music of Kenya" (a country's music scene overview, not a genre). Left unfiltered, these
would pollute any genre hierarchy or genre-matching built on top of this data.

**`exclusion_reason` values:**

| Value                | Rule                                              | Rationale                                                                 |
| --------------------- | -------------------------------------------------- | --------------------------------------------------------------------------- |
| `regional_overview`   | `item_label` starts with `"music of "`             | Wikidata's national/regional music overview articles (e.g. "music of France", "music of Kenya") — ~300 of ~6,300 items as of this writing, always this exact prefix, never a genre name. |

This is a first classification pass covering the single highest-confidence, most mechanical rule
found during analysis. Other non-genre categories are known to exist in the Bronze data (musical
forms/techniques like "fugue" or "polyphony", ensemble/format labels like "big band music") but
aren't covered here yet — they don't reduce to one clean, false-positive-free rule the way
`regional_overview` does, and are left for a later Silver step.

Rows are preserved 1:1 from Bronze (same edge-list grain, `item_id` still not unique) so this step
never drops data — downstream consumers filter on `is_genre` themselves.

**Data profile (as of this writing):**

| Metric                       | Rows | Distinct `item_id`s |
| ----------------------------- | ---: | -------------------: |
| Total                          | 9,490 | 6,335                |
| `is_genre = true`               | 9,123 | 6,036                |
| `is_genre = false` (`regional_overview`) | 367  | 299                  |

Regenerate with `uv run --package wikidata python -m wikidata.profile_silver` (reads
`SILVER_OUTPUT_DIR/1_classification.parquet`, read-only, no new data fetched) — these numbers will
drift as Wikidata's live genre tree changes.
