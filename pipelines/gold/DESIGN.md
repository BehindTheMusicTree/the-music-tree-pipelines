# Design

Rationale for `gold`'s match cascade. See [SCHEMA.md](SCHEMA.md) for column/shape detail.

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

1. `exact` — case-insensitive equality against a canonical `item_label`.
2. `music_suffix` — same, after stripping a trailing `" music"` from the musicbrainz name (covers the
   bulk of the naming-convention gap between the two sources).
3. `manual_alias` — looked up in `manual_genre_alias.csv`, a data expert's curated real-genre alias.
4. `accepted_non_genre` — looked up in `manual_accepted_non_genre_tags.csv`, a data expert's curated
   permanent-noise list. Kept in the output (not dropped) with a `null` `wikidata_genre_name`, for
   audit visibility — a reviewer can see it was triaged, not silently missed.
5. `unmatched` — anything left. **Not a hard failure.**

## Why `unmatched` is a soft warning, not a raise

`wikidata`'s `canonical_roots.py` raises on an untriaged new root, blocking that pipeline's run until a
data expert reviews it — appropriate there because a new root is rare and reviewable in isolation.
Genre-name reconciliation is different: musicbrainz's tag vocabulary is large, uncurated folksonomy, and
new unmatched names will appear routinely as the sample data or the tag vocabulary shifts. Blocking the
daily Gold run on every new unmatched name would make the pipeline fragile for no benefit — a missing
tag alias doesn't corrupt the tree or the songs export, it just means fewer songs get a resolved genre
this run.

Instead, `genre_match` logs a warning and writes every unmatched `(genre_name, title, artist,
youtube_video_id)` combination to `1_genre_match_unresolved.csv` every run (even when empty) — a durable,
human-scannable triage surface. A data expert reviews it and promotes each name into
`manual_genre_alias.csv` (real genre, different name) or `manual_accepted_non_genre_tags.csv` (permanent
noise), same closing-the-loop shape as `wikidata`'s manual-CSV backstops, just non-blocking.

## Malformed `youtube_video_id` is dropped, not raised

`genre_match` also drops any row whose `youtube_video_id` isn't exactly 11 characters (a real YouTube
video id's fixed length) before genre matching, logging a warning rather than raising — same rationale
as `unmatched` above: `musicbrainz`'s `3_songs` step already filters these at extraction time (see
`pipelines/musicbrainz/SCHEMA.md`), so this is a defensive second check against a regression there, and
a handful of malformed ids shouldn't block the whole daily run over rows that were always going to be
dropped. Filtering happens here rather than in `song_export`'s schema validation deliberately — that
validation raises hard, and raising over a single bad row would abort the entire `2_songs.json` export
(and, transitively, block the systemd job from syncing the already-good `1_canonical_genre_tree.json`
too) — the same all-or-nothing failure mode that motivated this check in the first place (see
`CHANGELOG.md`). The schema's `youtube_video_id` pattern still enforces the same 11-character constraint
as a last-resort net, but by construction should never actually trigger.

## Manual CSV validation

Both manual CSVs are validated the same way `wikidata`'s manual CSVs are (see
`non_genre_pruning.py::_load_dropped_ids`): a `ValueError` naming the CSV and offending value(s) for a
blank/null required column, a duplicate key (case-insensitive), or an unknown reference. Additionally,
each CSV is checked against the auto-match cascade and against each other, since an alias/non-genre entry
that would already resolve automatically (or that conflicts with the other CSV) indicates stale or
contradictory triage data:

- `manual_genre_alias.csv`: `wikidata_genre_name` must exist in the canonical hierarchy;
  `musicbrainz_genre_name` must not already match via `exact`/`music_suffix` (dead-weight alias).
- `manual_accepted_non_genre_tags.csv`: `musicbrainz_genre_name` must not already match via
  `exact`/`music_suffix`, nor appear in `manual_genre_alias.csv` (can't be both a real alias and
  permanent noise).

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
- `genre_match` checks `3_songs.parquet`'s `genre_name` (the musicbrainz-side join key) and
  `7_canonical_hierarchy.parquet`'s `item_label` (the wikidata-side join key) are non-null, and that
  the matched output's row count exactly equals the input song count (a per-row lookup can never
  legitimately change height).

Deliberately plain Polars, not a dedicated data-quality tool (Great Expectations, dbt tests): this is
a handful of checks at one boundary in one pipeline, and a new dependency plus its own DSL isn't
worth it here.
