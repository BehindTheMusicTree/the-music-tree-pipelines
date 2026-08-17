# Schema

Data dictionary and lineage notes for `wikidata`. See [README.md#pipeline](README.md#pipeline) for the Bronze layer overview.

## Table of Contents

- [Schema](#schema)
  - [Table of Contents](#table-of-contents)
  - [Bronze](#bronze)

## Bronze

One Parquet file, `wikidata_genre_tree.parquet`, one row per (item, parent) edge: every Wikidata
item classified `P31` ("instance of") `Q188451` ("music genre") — the class extension, ~6,300
items as of this writing — plus each genre's direct `P279` ("subclass of") parent(s). See
`wikidata_client.GENRE_TREE_QUERY` for the exact SPARQL.

**Why `P31`, not a `P279*` walk from `Q188451`:** the intuitive query — "every item transitively
`P279` subclass-of music genre" — returns only 14 items. Wikidata doesn't model individual
genres as P279-descendants of `Q188451`; it models class membership via `P31` (e.g. "rock music"
`P31` "music genre") and the subgenre hierarchy separately via `P279` between genre items (e.g.
"rock music" `P279` "popular music"), which does not itself chain back up to `Q188451`.

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
entity URIs (`http://www.wikidata.org/entity/Q11399`), not bare QIDs — `bronze_wikidata.py`
strips the `http://www.wikidata.org/entity/` prefix before writing Parquet, since the QID is the
natural join key and the full URI is otherwise dead weight. Labels are passed through as-is.

**Independent of MusicBrainz for now**: this pipeline ingests Wikidata's genre taxonomy on its
own terms — it is not filtered or matched against `musicbrainz`'s `genre.name` list at this
stage. That matching (fuzzy name-match, no shared key between the two sources) is a future
step, not built yet.

A multi-parent item (Wikidata classes aren't a strict tree — a genre can have more than one
`P279` parent) produces one row per parent, so `item_id` is not unique on its own.
