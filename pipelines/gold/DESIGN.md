# Design

Rationale for `gold`'s match cascade. See [SCHEMA.md](SCHEMA.md) for column/shape detail.

All curation CSVs below are curation rules owned by `grow-the-music-tree-api` (edited through its
`curation/<list>/entries/` endpoints or the grow admin UI), pulled at the start of each run into
`CURATION_BRONZE_DIR` by the `curation` pipeline (`python -m curation.ingest`).

## Why reconciliation lives here, not in either source pipeline

Matching musicbrainz's raw tag names against wikidata's genre labels needs both pipelines'
Silver output at once — the first genuine cross-pipeline join in this repo. Neither `wikidata` nor
`musicbrainz` should depend on the other's output (they stay independently runnable, independently
testable), so the join lives in a new layer downstream of both.

## Match cascade

Empirical check (musicbrainz `tag.name`, as used by `2_recording_genre`, against wikidata canonical
`item_label`s, before regional targets): 77.0% match case-insensitive exact, 83.4% after stripping a trailing `" music"` suffix.
The remaining ~17% splits between real naming-convention mismatches (fixable via alias, e.g. musicbrainz
"drum and bass" vs. wikidata's actual label) and permanent non-genre folksonomy noise (`asmr`,
`birdsong`, personal-taste tags, etc.) that should never match.

`genre_match` tags each row with a `match_method`, trying each of the following in order and stopping
at the first that succeeds:

0. `no_genre` — the song has no genre tag at any level (recording, release group, artist; see [Inherited genres](#inherited-genres)). Kept with
   a `null` `wikidata_genre_name`: every song is exported, genre or not.
1. `exact` — case-insensitive equality against a target `item_label` (canonical or regional, see
   [Regional targets](#regional-targets)).
2. `normalized` — (skipped for a key curated in `genre_disambiguation.csv`, which resolves as `ambiguous`
   instead: `trap` is `trap music` by key, yet means EDM trap on an EDM song) equality of *genre keys*, computed the same way on both sides: lowercase, `-`/`_`/runs of
   whitespace collapsed to one space, trailing `" music"` dropped. Covers the bulk of the naming-convention
   gap in both directions: musicbrainz `rock`/`hip hop`/`jazz music` meet wikidata `rock music`/`hip-hop`/
   `jazz`. When two target labels share a key (`dance`/`dance music`, `bass`/`bass music` are distinct
   wikidata genres), neither is reachable by that key (logged); each still matches by `exact`.
3. `manual_alias` — looked up in `genre_alias.csv`, a data expert's curated real-genre alias.
4. `accepted_non_genre` — looked up in `accepted_non_genre_tags.csv`, a data expert's curated
   permanent-noise list. Kept in the output (not dropped) with a `null` `wikidata_genre_name`, for
   audit visibility — a reviewer can see it was triaged, not silently missed.
5. `wikidata_alias` — genre-key equality against an English Wikidata alias (`skos:altLabel`, wikidata
   Bronze `wikidata_genre_aliases.parquet`) of a target item: IDM, EBM, chillout, psytrance. It runs after
   both curation lists, so `genre_alias.csv` re-targets a wrong alias and
   `accepted_non_genre_tags.csv` blocks one. Dropped (one warning lists them): an alias whose key equals
   its own label's (adds nothing), equals **any** target label key (the label always wins — `soft rock` is an
   alias of sunshine pop and the label of soft rock; a label key shared by two labels is only reachable by
   exact name, so a differently spelled tag would otherwise fall to the alias). A key shared by aliases of
   several items keeps only the most precise of them (precedence closure): one left is a plain alias, several
   left (on different lineages: `rap`, `club`) become automatic disambiguation candidates with no default.
6. `ambiguous` — the tag's key has disambiguation candidates (curated in `genre_disambiguation.csv`,
   else the automatic Wikidata ones). Picked per song from its other genres, see
   [Ambiguous tags](#ambiguous-tags); a tag a song picks a candidate for resolves as `disambiguated`.
7. `unmatched` — anything left. **Not a hard failure.**

The cascade runs per distinct tag name of `10_song_genre`. A song's resolved tags are summed per target
`item_id` (several MusicBrainz tags can resolve to one item), then precedence applies (next section). An
unresolved tag never wins: the best resolved item does, and the song's `match_method` is that item's
method (else the top unresolved tag's method, else `no_genre`).

## Regional targets

The cascade matches against the union of `7_canonical_hierarchy` and `8_regional_hierarchy`: on staging 60% of the
songs whose tags all failed to resolve carried a regional-only label (country, latin, j-pop, k-pop, reggaeton,
tango, mpb). grow imports both trees, so a regional genre is a valid song genre.

- **Canonical wins** on a label present in both trees (new wave, pub rock, …): `item_id_by_label` sorts canonical
  first. Aliases resolve straight to an `item_id`, so a regional item's alias keeps its own item even when its
  label is shared.
- **Overviews are umbrella targets**: the regional seed items (`is_regional_overview` in
  `4_regional_classification`, "music of Japan") are places, but a tag like `latin` has nowhere else to go. Each
  overview with sub-genres is a target with `is_umbrella = true`, so its songs are flagged "to refine" like a
  canonical umbrella's, and the regional tree export marks it `isUmbrella`. An overview without sub-genres can't be
  an umbrella (grow rejects a childless one) and stays no target (`is_leaf_overview`); it remains in the precedence
  closure as an ancestor.
- **Lineage** between a regional and a canonical genre only exists through a non-anchor parent (a canonical
  parent picked in `5_parent_selection` or a `5_secondary_parents` edge); without such an edge, k-pop + pop is
  decided by weight. Missing edges are wikidata Silver curation gaps, not Gold's.
- A regional item is an umbrella only if it is an overview with sub-genres. `genre_tree` (`canonical` | `regional`) records which tree the genre
  comes from, and `2_songs` ships its QID as `genre_wikidata_id` so grow resolves a shared label by id.

## Ambiguous tags

Some tags mean several genres: `r&b` is rhythm and blues or contemporary R&B. Per song and per
ambiguous tag, with the song's **context** = the genres its other tags resolved to without ambiguity, and
"under" = beating in the precedence closure (lineage + `genre_precedence.csv`):

1. A context genre is a candidate or under one (`r&b` + soul, `r&b` + quiet storm) → the tag is ignored on
   that song: no genre, no weight. The context genre already says more.
2. Otherwise a candidate **fits** when it is under a context genre (`r&b` + pop → contemporary R&B, under pop
   music) or the song carries one of its `genre_disambiguation_context.csv` hints, or a genre under it
   (`r&b` + hip hop or trap → contemporary R&B): styles related without one being a subgenre of the other.
3. Fitting candidates beaten by another fitting candidate are dropped. Exactly one left → `disambiguated` to
   it. None, or several → the curated default (`is_default`), also `disambiguated`.
4. No default (automatic Wikidata candidates, or a curated set without one) → the tag stays unresolved on that
   song (`ambiguous`), and counts in `1_genre_match_unresolved.csv` until a default is curated.

The pick then joins the song's other genres and goes through precedence like any resolved tag.

## Genre precedence and lineage

Picking by weight alone lets broad tags win systematically (pop beat pop rock 659 times on staging, rock
beat classic rock 330 times, reggae/dub beat ska on all 13 ska recordings). Per song, after resolution:

- **Lineage**: a descendant beats its ancestors. Edges are the anchor parents of
  `7_canonical_hierarchy` and `8_regional_hierarchy`, the other parents picked in `5_parent_selection` (canonical
  parent, regional genre parent, regional overviews) and the secondary parents of `5_secondary_parents` (so pop
  rock beats pop).
- **Manual precedence**: `genre_precedence.csv` (`item_id, over_item_id, reason`, QIDs) adds rules for
  pairs the tree does not relate (ska over reggae).
- Rules close transitively; every genre beaten by another on the same song is dropped, and each
  surviving winner's weight becomes the max of its own and the beaten genres' weights.
- The highest weight wins; ties go to the smallest `item_id`.

**Secondary genres**: the other survivors of precedence, by weight, become `secondary_genre_item_ids`. Since
precedence already dropped every genre beaten by another on the song, none is an ancestor of the chosen genre
or of another secondary (punk rock + hardcore punk keep only hardcore punk). Unresolved tags are never
secondaries, but every raw tag stays in `musicbrainz_genres` so it can be curated or linked later. When
`song_genre.csv` overrides the genre, `song_export` drops that genre and the genres it beats (same
closure) from the secondaries.

The run fails on a self-pair, a cycle (a manual pair reversing a lineage included), or an id absent from
both genre trees.

## Inherited genres

A song's genre comes from the most precise level that yields one: its recordings' tags, else its release
groups' (`10_song_genre` `level = release_group`), else its artist's (`13_artist_genre`). `genre_source` records
which. Eligibility of release groups is musicbrainz Silver's concern, see
`pipelines/musicbrainz/DESIGN.md#inherited-genres`.

- **Fallback after matching**: a level counts only if one of its tags resolves to a genre. A release
  group tagged only with unmatched folksonomy must not hide a good artist genre.
- **Never blended**: weights are summed within a level, never across. An artist's `rock` with weight 20 would
  otherwise outvote the song's own `shoegaze` with weight 1, the opposite of "most precise".
- **One pick per subject**: the cascade, disambiguation and precedence run per level with the level's own tags as
  context, per song for the recording and release-group levels and once per artist for the artist level (then
  joined to the artist's songs). Resolution runs once over every level's distinct names.
- `secondary_genre_item_ids`, `musicbrainz_genres` and `match_method` come from the deciding level, so they
  describe the tags the genre was picked from. An unresolved song keeps the most precise tagged level's method.
- Curation (aliases, disambiguation, precedence, `song_genre.csv`) applies at every level unchanged; a song
  override still beats an inherited genre.

## Umbrella genres

A song whose best genre is an umbrella (`is_umbrella` in `7_canonical_hierarchy`) keeps it: the genre
stays in `1_genre_match.parquet` with `is_umbrella = true`, and grow derives "to refine" from the genre.
A `song_genre.csv` row targeting an umbrella raises, mirroring grow's refusal; one targeting a regional overview without sub-genres raises too, being no song's genre.
`1_umbrella_candidates.csv` lists non-umbrella canonical genres with children, most direct songs first,
as triage for the `umbrella_genres` curation list.

## Why `unmatched` is a soft warning, not a raise

Same stance as `wikidata`'s `canonical_roots.py`, which flags (not raises on) an untriaged new root —
it's carried into the canonical tree export as `isUnacceptedRoot: true` for review in
grow-the-music-tree-api. Genre-name reconciliation is noisier still: musicbrainz's tag vocabulary is large, uncurated folksonomy, and
new unmatched names will appear routinely as the sample data or the tag vocabulary shifts. Blocking the
daily Gold run on every new unmatched name would make the pipeline fragile for no benefit — a missing
tag alias doesn't corrupt the tree or the songs export, it just means fewer songs get a resolved genre
this run (they're still exported, with a null genre).

Instead, `genre_match` logs a warning and writes every unmatched tag name with its song and weight totals to `1_genre_match_unresolved.csv` every run (even when empty) — a durable,
human-scannable triage surface. A data expert reviews it and promotes each name into
`genre_alias.csv` (real genre, different name) or `accepted_non_genre_tags.csv` (permanent
noise), same closing-the-loop shape as `wikidata`'s manual-CSV backstops, just non-blocking.

## Malformed `youtube_video_id` is cleared, not raised

`genre_match` nulls any `youtube_video_id` (and its `youtube_unplayable_reason` and `youtube_view_count`) that isn't exactly 11
characters, logging a warning rather than raising. `musicbrainz`'s `5_songs` step already extracts only
11-character ids (see `pipelines/musicbrainz/SCHEMA.md`), so this is a defensive second check against a
regression there. The song itself is kept, the same as a song with no video. Raising in `song_export`'s
checks over a single bad id would abort the whole songs export (and block the systemd job from syncing
the already-good trees), so `song_export`'s pattern check is a last-resort net that should never
trigger.

## Out-of-range `first_release_year` is nulled, not raised

Silver already enforces the valid year range (see
[`musicbrainz/DESIGN.md`](../musicbrainz/DESIGN.md#first-release-year)). `song_export` still nulls any
out-of-range value with a warning rather than raising, as a regression guard: the song is kept, and a
raise would abort the whole songs export.

## Text lengths mirror grow's limits

`grow-the-music-tree-api` rejects an artist name, release group title or song title longer than its
`ARTIST_NAME_LEN_MAX` / `ALBUM_NAME_LEN_MAX` / `TRACK_TITLE_LEN_MAX` with a 400, which aborts the import
hours into a daily run. `common.quality_checks` mirrors those limits, and `artist_export` and
`song_export` check every row (`check_max_length`) and raise with the offending count, so the contract
break fails Gold before anything is posted. Keep the constants in step with grow's `settings.py`.

## Manual CSV validation

Both manual CSVs are validated the same way `wikidata`'s manual CSVs are (see
`non_genre_pruning.py::_load_dropped_ids`): a `ValueError` naming the CSV and offending value(s) for a
blank/null required column, a duplicate key (same genre key, so `Hip-Hop` and `hip hop` collide), or an
unknown reference. Additionally,
each CSV is checked against the auto-match cascade and against each other, since an alias/non-genre entry
that would already resolve automatically (or that conflicts with the other CSV) indicates stale or
contradictory triage data:

- `genre_alias.csv`: `wikidata_genre_name` must be a target label (canonical or regional, not an overview without sub-genres);
  `musicbrainz_genre_name` must not already match via `exact`/`normalized` (dead-weight alias).
- `accepted_non_genre_tags.csv`: `musicbrainz_genre_name` must not already match via
  `exact`/`normalized`, nor share a genre key with a `genre_alias.csv` entry (can't be both a real alias and
  permanent noise).
- `genre_disambiguation.csv`: every `item_id` a target; a genre key not already
  resolved by a label (`exact`/`normalized`), `genre_alias.csv` or `accepted_non_genre_tags.csv`;
  at least 2 distinct candidates per key and at most one default. It replaces the key's automatic candidates.
- `genre_disambiguation_context.csv`: each `(musicbrainz_genre_name, item_id)` a candidate of
  `genre_disambiguation.csv`; `context_item_id` in either genre tree.

## Curation mechanics

grow-the-music-tree-api's curation lists reach Gold as `<list>.csv` (shapes in [SCHEMA.md](SCHEMA.md#3-manual-csvs)):

- `secondary_parents.csv`: `add` exports `(item_id, parent_id)` as a `secondaryParents` ref. A
  canonical item's add of a regional parent rides on the regional export's `crossTreeSecondaryParents`
  (the canonical tree is imported first, before regional rows exist); every other add raises when
  `parent_id` can't resolve in the tree being built (canonical or regional for the regional export),
  equals `item_id`, or is already the item's anchor parent (`parent_id` of the hierarchy): grow would
  reject such an edge, so the export fails instead of shipping it. `remove` drops a Wikidata extra parent
  edge (a selected parent other than the anchor, or a `5_secondary_parents` edge); it raises when the
  parent is resolvable but the edge is not an extra parent edge, and is logged and ignored when the
  parent is outside the resolvable set or is the item's anchor parent (a curated parent rule made it the
  anchor: the removal holds while it is the anchor and applies again if the anchor moves away).
- `essential_songs.csv`: a genre's `essentialSongs`, ordered by `position`. Every song id
  (`musicbrainz_recording_id`) is checked against `1_genre_match.parquet` before the songs and artists are
  exported, so an essential song missing from the songs fails the build.
- `summary_overrides.csv`: a genre's `summary`.
- `canonical_genre_scene.csv`: a genre's `scene` (`commercial` | `underground`), which the front uses to render the branch specially.
- `song_genre.csv`: replaces a song's genre in `2_songs`; `1_genre_match.parquet` stays
  pre-override so the match report reflects the computed cascade.
- `recording_youtube_video.csv` is not applied here: musicbrainz Silver pins it as a rank-0
  candidate so its playability gets checked (see `pipelines/musicbrainz/DESIGN.md`).

## Pop/core genre sides

`the-music-tree-genre-kit` distinguishes a "pop" (crossover/mainstream) side from an implicit "core"
side among a root genre's direct children (e.g. Electropop under Electronic) — a genre-kit/consumer
concept with no Wikidata equivalent, so it can't be derived from `wikidata`'s Silver output. It's
curated here in Gold, not Silver, following the same rationale as `genre_alias.csv`: Silver
stays a consumer-agnostic representation of Wikidata's genre graph, while Gold is where
consumer-specific (genre-kit) curation belongs.

`canonical_genre_pop_side.csv` (see [SCHEMA.md](SCHEMA.md)) holds one row per (canonical root,
pop child) pair — a root may have zero, one, or several pop children, but at least one direct child
must remain "core" (unmarked). `canonical_genre_tree_export.py::_load_pop_sides` validates it the
same way as the other manual CSVs — `ValueError` naming the CSV and offending value(s) for a
blank/null required column, a duplicate `(root_item_id, pop_child_item_id)` row, a
`root_item_id` that isn't an actual canonical root, a `pop_child_item_id` that isn't a direct
child of that root, or a root whose *every* direct child is marked pop (no core side left) — then
`genre_tree_builder.py::build_genre_tree` marks each matching direct child `"side": "pop"` in the
exported tree. Everything else is implicitly "core" (unmarked), matching the genre-kit's own
null-means-core convention, so `"side"` never appears with value `"core"`. Only the canonical tree
gets this treatment — `1_regional_genre_tree.json`'s roots are geographic regions, not genres, so the
pop/core distinction doesn't apply there.

## Quality checks at the Silver → Gold boundary

`gold`'s two lookups (`genre_match`'s musicbrainz-tag-to-wikidata-label match, and
`genre_tree_builder`'s recursive hierarchy-to-tree build) never `pl.join`, so today's code can't
fan out or drop rows by construction — but a future refactor could turn either into a real join
without anyone noticing the row-multiplication risk. `quality_checks.py` (`check_non_empty`,
`check_null_rate`, `check_unique_key`, `check_row_count_delta`) exists as that guardrail, raising
`ValueError` (fail fast, same as the manual-CSV validation above) rather than letting a silently
corrupted export reach `grow-the-music-tree-api`:

- `genre_tree_builder.build_genre_tree` checks the incoming hierarchy's `item_id` (the wikidata QID
  join key) is non-null and unique, then compares the hierarchy's unique `item_id` count against the
  built tree's total node count — catching a `parent_id` cycle, which leaves both items out of
  `roots` (each has a known parent) and unreachable from any real root, silently vanishing from the
  tree instead of raising.
- `genre_match` checks both hierarchies are non-empty with a non-null `item_label` (the wikidata-side join
  key) and an `item_id` unique across the two (`5_songs.parquet`'s `genre_name` may be null, matched as `no_genre`), and that
  the matched output's row count exactly equals the input song count (a per-row lookup can never
  legitimately change height).

Deliberately plain Polars, not a dedicated data-quality tool (Great Expectations, dbt tests): this is
a handful of checks at one boundary in one pipeline, and a new dependency plus its own DSL isn't
worth it here.

## Delta export, baseline owned by the sync

The nightly import into grow is a full snapshot (grow deletes whatever a run omits), yet almost no song or artist changes day to day. Gold therefore also writes `<name>_delta/` — rows whose stored columns differ, by value, from the last *successfully synced* run, plus the keys that disappeared — so a delta import can send only those. Gold never moves the baseline: `infrastructure` promotes `<name>_snapshot.parquet` into `_synced/` only after grow's merge task reports success, so a failed or skipped sync leaves the baseline behind and the next run re-sends everything it missed. The comparison is exact and per column (no hash, so a Polars upgrade or a dtype change cannot flip every row), and only the columns grow stores are exported, so a column grow ignores never causes a sync. Absent or reshaped baseline means no delta, i.e. a full sync, which is also the fallback after any data loss.

## Region map

**Song ↔ region contract.** A region's songs are the songs whose primary genre (`1_genre_match.parquet`'s `wikidata_genre_item_id`, before any curated override) is in the region's relevant genre set: the items anchored to the region by `5_music_anchor`, plus every canonical descendant of those. `songCount` sums the song counts over that set and `genreCount` is its size, so a region's counts include its sub-genres but never its sub-regions' own anchored genres.

**Admin versus zone.** Countries from Natural Earth and admin-1 units form the drill-down tree (`world` → country → admin unit, parents from `2_place_containment`); an anchored admin unit pulls its admin ancestors onto the map even when they carry no genre. Historical countries, historical and cultural regions and macro-regions are zones: they overlap the admin tree rather than nest in it, so they are separate tileset features and each admin region lists the zones whose footprint reaches it (`zoneIds`, propagated up the admin ancestors).

**Region map hierarchy.** The drill-down tree is built from `2_place_containment`, then corrected so a click path matches how people think of places:

- _Nearest admin ancestor._ A region's parent is the nearest admin region reached through containment, going through non-admin nodes (a country with no Natural Earth shape, a historical region) — Anglesey sits under the UK via Wales. Ties take the first parent in `(parent_id, child_id)` order.
- _Nested countries._ A Natural Earth country keeps its admin parent only when at least `NESTED_COUNTRY_MIN_OVERLAP` (0.9) of its area lies inside the parent's geometry; otherwise it is a world child (Algeria is not "in" France through a historical `P361`, Gujarat is in India).
- _Mainland intermediates._ An admin child holding at least `MAINLAND_MIN_SHARE` (0.5) of its admin parent's area, with at least one sibling, is dissolved: its children and genres move to the parent (metropolitan France into France). The dissolved regions are logged once at info. Dissolving runs deepest parents first and repeats until nothing changes, so a chained mainland (a grandchild holding half of its grandparent) dissolves whatever the QIDs sort like. A parent absorbs one mainland, the largest claimant (ties by id): geometries are not always consistent, e.g. a parent smaller than several sibling provinces, and absorbing them all would fold distinct provinces into it. A country child is only dissolved into a country parent; an admin unit can go anywhere. Areas are planar degrees²; the cos-latitude correction moves no verdict (England is 49.2% of the UK that way, 51.1% corrected) and England, a footprint-sourced country, is a zone, not an admin region, so the English regions already sit directly under the UK.
- _Regional-parent inheritance._ A canonical or regional genre with no anchor takes the region(s) of its nearest anchored ancestor in `8_regional_hierarchy` (rule `regional_parent`); an existing anchor always wins. A genre whose anchor could not be placed (no footprint or geometry, not a place) counts as unanchored for this walk, and leaves `5_region_map_unplaced.csv` once it inherits a region; the walk also passes through ancestors whose own anchor is unplaced.
- _Coverage and roll-up._ Once an admin region is on the map, all its admin children with a shape are too (count 0 when genre-less) so the choropleth has no holes. An admin region's counts roll up over its admin descendants' genres; zones count their own.

**Geometry lives in the tileset.** The map's geometry ships only as the vector tileset `regions/<sha256>.pmtiles`, built by tippecanoe from the unsimplified shapes and served from object storage; the frontend draws it with MapLibre. Gold writes no per-level GeoJSON, so there is no size cap or simplification ladder.

**Shipping to grow.** grow-api imports the map through the same chunked import run as songs and artists. The run holds one line, the index, hashed over its canonical bytes; the index's `tileset` is the sha256 of the tileset file, which the pipeline runner uploads to object storage and verifies before grow imports the index (grow-api's `_check_index` requires a bare sha256 string and forbids `files`). The index is the one document the front fetches whole on every map load, so it has its own 2 MB cap (canonical bytes).

**Footprint anchors.** A genre anchored to an entity that is not itself a region (an ethnic group, a language) but has an admin footprint is listed on each footprint admin region with relation `tradition`. `civilization` and `curated` anchors are never placed this way; they go through curation.

**Unplaced genres.** A genre anchored to a `civilization`/`curated` entity, or to an entity with no footprint or no geometry, is left off the map and listed in `5_region_map_unplaced.csv`; this is a warning, not a failure, like `1_genre_match_unresolved.csv`.


## Artist image snapshot consistency

Each build validates and pins an immutable image snapshot before exporting anything. `ARTIST_IMAGES_PUBLISHED_DIR`
is required; `ARTIST_IMAGES_SNAPSHOT_ID` can select a retained version explicitly. A pointer update during a build
cannot change its image input. `_manifest.json` records `artistImagesSnapshot` with snapshot ID, image artifact
SHA-256, producer commit, publication time, discovery provenance and source age summary. The artist join remains
optional per artist; a missing image association produces null. An absent or damaged publication fails the build.
