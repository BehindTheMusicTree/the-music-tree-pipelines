# Design

Rationale for `gold`'s match cascade. See [SCHEMA.md](SCHEMA.md) for column/shape detail.

All `manual_*.csv` files below are curation rules owned by `grow-the-music-tree-api` (edited through its
`curation/<list>/entries/` endpoints or the grow admin UI), pulled at the start of each run into
`CURATION_BRONZE_DIR` by the `curation` pipeline (`python -m curation.ingest`).

## Why reconciliation lives here, not in either source pipeline

Matching musicbrainz's raw tag names against wikidata's canonical genre labels needs both pipelines'
Silver output at once — the first genuine cross-pipeline join in this repo. Neither `wikidata` nor
`musicbrainz` should depend on the other's output (they stay independently runnable, independently
testable), so the join lives in a new layer downstream of both.

## Match cascade

Empirical check (musicbrainz `tag.name`, as used by `2_recording_genre`, against wikidata canonical
`item_label`s): 77.0% match case-insensitive exact, 83.4% after stripping a trailing `" music"` suffix.
The remaining ~17% splits between real naming-convention mismatches (fixable via alias, e.g. musicbrainz
"drum and bass" vs. wikidata's actual label) and permanent non-genre folksonomy noise (`asmr`,
`birdsong`, personal-taste tags, etc.) that should never match.

`genre_match` tags each row with a `match_method`, trying each of the following in order and stopping
at the first that succeeds:

0. `no_genre` — the song has no tag in `10_song_genre` (none of its recordings carries a genre tag). Kept with
   a `null` `wikidata_genre_name`: every song is exported, genre or not.
1. `exact` — case-insensitive equality against a canonical `item_label`.
2. `normalized` — equality of *genre keys*, computed the same way on both sides: lowercase, `-`/`_`/runs of
   whitespace collapsed to one space, trailing `" music"` dropped. Covers the bulk of the naming-convention
   gap in both directions: musicbrainz `rock`/`hip hop`/`jazz music` meet wikidata `rock music`/`hip-hop`/
   `jazz`. When two canonical labels share a key (`dance`/`dance music`, `bass`/`bass music` are distinct
   wikidata genres), neither is reachable by that key (logged); each still matches by `exact`.
3. `manual_alias` — looked up in `manual_genre_alias.csv`, a data expert's curated real-genre alias.
4. `accepted_non_genre` — looked up in `manual_accepted_non_genre_tags.csv`, a data expert's curated
   permanent-noise list. Kept in the output (not dropped) with a `null` `wikidata_genre_name`, for
   audit visibility — a reviewer can see it was triaged, not silently missed.
5. `wikidata_alias` — genre-key equality against an English Wikidata alias (`skos:altLabel`, wikidata
   Bronze `wikidata_genre_aliases.parquet`) of a canonical item: IDM, EBM, chillout, psytrance. It runs after
   both curation lists, so `manual_genre_alias.csv` re-targets a wrong alias and
   `manual_accepted_non_genre_tags.csv` blocks one. Dropped (one warning lists them): an alias whose key equals
   its own label's (adds nothing), equals **any** canonical label key (the label always wins — `soft rock` is an
   alias of sunshine pop and the label of soft rock; a label key shared by two labels is only reachable by
   exact name, so a differently spelled tag would otherwise fall to the alias). A key shared by aliases of
   several items keeps only the most precise of them (precedence closure): one left is a plain alias, several
   left (on different lineages: `rap`, `club`) become automatic disambiguation candidates with no default.
6. `ambiguous` — the tag's key has disambiguation candidates (curated in `manual_genre_disambiguation.csv`,
   else the automatic Wikidata ones). Picked per song from its other genres, see
   [Ambiguous tags](#ambiguous-tags); a tag a song picks a candidate for resolves as `disambiguated`.
7. `unmatched` — anything left. **Not a hard failure.**

The cascade runs per distinct tag name of `10_song_genre`. A song's resolved tags are summed per canonical
`item_id` (several MusicBrainz tags can resolve to one item), then precedence applies (next section). An
unresolved tag never wins: the best resolved item does, and the song's `match_method` is that item's
method (else the top unresolved tag's method, else `no_genre`).

## Ambiguous tags

Some tags mean several canonical genres: `r&b` is rhythm and blues or contemporary R&B. Per song and per
ambiguous tag, with the song's **context** = the genres its other tags resolved to without ambiguity, and
"under" = beating in the precedence closure (lineage + `manual_genre_precedence.csv`):

1. A context genre is a candidate or under one (`r&b` + soul, `r&b` + quiet storm) → the tag is ignored on
   that song: no genre, no weight. The context genre already says more.
2. Otherwise a candidate **fits** when it is under a context genre (`r&b` + pop → contemporary R&B, under pop
   music) or the song carries one of its `manual_genre_disambiguation_context.csv` hints, or a genre under it
   (`r&b` + hip hop or trap → contemporary R&B): styles related without one being a subgenre of the other.
3. Fitting candidates beaten by another fitting candidate are dropped. Exactly one left → `disambiguated` to
   it. None, or several → the curated default (`is_default`), also `disambiguated`.
4. No default (automatic Wikidata candidates, or a curated set without one) → the tag stays unresolved on that
   song (`ambiguous`), and counts in `1_genre_match_unresolved.csv` until a default is curated.

The pick then joins the song's other genres and goes through precedence like any resolved tag.

## Genre precedence and lineage

Picking by weight alone lets broad tags win systematically (pop beat pop rock 659 times on staging, rock
beat classic rock 330 times, reggae/dub beat ska on all 13 ska recordings). Per song, after resolution:

- **Lineage**: a descendant beats its ancestors. Edges are the primary parents of
  `7_canonical_hierarchy` plus the secondary parents of `5_secondary_parents` (so pop rock beats pop).
- **Manual precedence**: `manual_genre_precedence.csv` (`item_id, over_item_id, reason`, QIDs) adds rules for
  pairs the tree does not relate (ska over reggae).
- Rules close transitively; every genre beaten by another on the same song is dropped, and each
  surviving winner's weight becomes the max of its own and the beaten genres' weights.
- The highest weight wins; ties go to the smallest `item_id`.

**Secondary genres**: the other survivors of precedence, by weight, become `secondary_genre_item_ids`. Since
precedence already dropped every genre beaten by another on the song, none is an ancestor of the chosen genre
or of another secondary (punk rock + hardcore punk keep only hardcore punk). Unresolved tags are never
secondaries, but every raw tag stays in `musicbrainz_genres` so it can be curated or linked later. When
`manual_song_genre.csv` overrides the genre, `song_export` drops that genre and the genres it beats (same
closure) from the secondaries.

The run fails on a self-pair, a cycle (a manual pair reversing a lineage included), or an id absent from
the canonical hierarchy.

## Umbrella genres

A song whose best genre is an umbrella (`is_umbrella` in `7_canonical_hierarchy`) keeps it: the genre
stays in `1_genre_match.parquet` with `is_umbrella = true`, and grow derives "to refine" from the genre.
A `manual_song_genre.csv` row targeting an umbrella raises, mirroring grow's refusal.
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
`manual_genre_alias.csv` (real genre, different name) or `manual_accepted_non_genre_tags.csv` (permanent
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

- `manual_genre_alias.csv`: `wikidata_genre_name` must exist in the canonical hierarchy;
  `musicbrainz_genre_name` must not already match via `exact`/`normalized` (dead-weight alias).
- `manual_accepted_non_genre_tags.csv`: `musicbrainz_genre_name` must not already match via
  `exact`/`normalized`, nor share a genre key with a `manual_genre_alias.csv` entry (can't be both a real alias and
  permanent noise).
- `manual_genre_disambiguation.csv`: every `item_id` in the canonical hierarchy; a genre key not already
  resolved by a label (`exact`/`normalized`), `manual_genre_alias.csv` or `manual_accepted_non_genre_tags.csv`;
  at least 2 distinct candidates per key and at most one default. It replaces the key's automatic candidates.
- `manual_genre_disambiguation_context.csv`: each `(musicbrainz_genre_name, item_id)` a candidate of
  `manual_genre_disambiguation.csv`; `context_item_id` in the canonical hierarchy.

## Curation mechanics

grow-the-music-tree-api's curation lists reach Gold as `manual_*.csv` (shapes in [SCHEMA.md](SCHEMA.md#3-manual-csvs)):

- `manual_secondary_parents.csv`: `add` exports `(item_id, parent_id)` as a `secondaryParents` ref. It
  raises when `parent_id` can't resolve in the tree being built (the canonical tree for the canonical
  export, canonical or regional for the regional one), equals `item_id`, or is already the item's main
  parent: grow would reject such an edge, so the export fails instead of shipping it. `remove` drops a
  Wikidata extra parent edge; it raises when the parent is resolvable but the edge is not an extra
  parent edge, and is logged and ignored when the parent is outside the resolvable set or is the
  item's main parent (a `main_parent` rule promoted it: the removal holds while it is main and applies
  again if the main parent moves away).
- `manual_essential_songs.csv`: a genre's `essentialSongs`, ordered by `position`. Every song id
  (`musicbrainz_recording_id`) is checked against `1_genre_match.parquet` before the songs and artists are
  exported, so an essential song missing from the songs fails the build.
- `manual_summary_overrides.csv`: a genre's `summary`.
- `manual_canonical_genre_scene.csv`: a genre's `scene` (`commercial` | `underground`), which the front uses to render the branch specially.
- `manual_song_genre.csv`: replaces a song's genre in `2_songs`; `1_genre_match.parquet` stays
  pre-override so the match report reflects the computed cascade.
- `manual_recording_youtube_video.csv` is not applied here: musicbrainz Silver pins it as a rank-0
  candidate so its playability gets checked (see `pipelines/musicbrainz/DESIGN.md`).

## Pop/core genre sides

`the-music-tree-genre-kit` distinguishes a "pop" (crossover/mainstream) side from an implicit "core"
side among a root genre's direct children (e.g. Electropop under Electronic) — a genre-kit/consumer
concept with no Wikidata equivalent, so it can't be derived from `wikidata`'s Silver output. It's
curated here in Gold, not Silver, following the same rationale as `manual_genre_alias.csv`: Silver
stays a consumer-agnostic representation of Wikidata's genre graph, while Gold is where
consumer-specific (genre-kit) curation belongs.

`manual_canonical_genre_pop_side.csv` (see [SCHEMA.md](SCHEMA.md)) holds one row per (canonical root,
pop child) pair — a root may have zero, one, or several pop children, but at least one direct child
must remain "core" (unmarked). `canonical_genre_tree_export.py::_load_pop_sides` validates it the
same way as the other manual CSVs — `ValueError` naming the CSV and offending value(s) for a
blank/null required column, a duplicate `(root_genre_name, pop_child_genre_name)` row, a
`root_genre_name` that isn't an actual canonical root, a `pop_child_genre_name` that isn't a direct
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
- `genre_match` checks `7_canonical_hierarchy.parquet`'s `item_label` (the wikidata-side join key) is
  non-null (`5_songs.parquet`'s `genre_name` may be null, matched as `no_genre`), and that
  the matched output's row count exactly equals the input song count (a per-row lookup can never
  legitimately change height).

Deliberately plain Polars, not a dedicated data-quality tool (Great Expectations, dbt tests): this is
a handful of checks at one boundary in one pipeline, and a new dependency plus its own DSL isn't
worth it here.

## Delta export, baseline owned by the sync

The nightly import into grow is a full snapshot (grow deletes whatever a run omits), yet almost no song or artist changes day to day. Gold therefore also writes `<name>_delta/` — rows whose hash differs from the last *successfully synced* run, plus the keys that disappeared — so a delta import can send only those. Gold never moves the baseline: `infrastructure` promotes `<name>_hashes.parquet` into `_synced/` only after grow's merge task reports success, so a failed or skipped sync leaves the baseline behind and the next run re-sends everything it missed. Absent baseline means no delta, i.e. a full sync, which is also the fallback after any data loss.
