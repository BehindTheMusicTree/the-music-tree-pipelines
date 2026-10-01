---
name: wikidata-canonical-roots
description: Curate pipelines/wikidata's canonical roots (9_canonical_roots.parquet) by giving each one a real place, a real parent genre, or a flag explaining why it's excluded — mapping nationally/ethnically-specific genres to their "music of <place>" overview item (manual_regional_overrides.csv), attaching orphaned subgenres to a real parent genre already in the tree (manual_main_parent.csv), and flagging roots that are a technique, not a genre at all (manual_technique_genres.csv), or aren't a music genre at all — a Wikidata misclassification (manual_out_of_scope_genres.csv). Use when asked to review/triage canonical roots, shrink the canonical root count, link roots to their actual parent genre, or curate/update manual_regional_overrides.csv / manual_main_parent.csv / manual_technique_genres.csv / manual_out_of_scope_genres.csv.
---

# Wikidata Silver: canonical roots curation

`7_canonical_hierarchy.parquet` (canonical) is supposed to collapse toward a handful of
real genre-family roots. It currently has a long tail of roots that fall into
one of four buckets the automated classification/parent-selection
(`regional_classification.py`, `main_parent_selection.py`) doesn't catch:

- **Regional**: national/ethnic genres with no `P279`/`P361` parent and no
  `P2341` (indigenous to) / `P495` (country of origin) value to seed from —
  handled via `manual_regional_overrides.csv` (this skill's main focus).
- **Canonical parent**: not regional at all — a genuine subgenre that's a
  root only because Wikidata never gave it a `P279`/`P361` parent edge (or
  the edge it had didn't survive Bronze/Silver), but a real parent genre
  already exists elsewhere in the canonical tree (e.g. "Foxcore", a 1990s
  female-fronted rock subgenre with no national/ethnic home, belongs under
  "rock music", not under any region) — handled via
  `manual_main_parent.csv` (see step 3, canonical-parent bullet, below).
  This is the "link to its actual parent" fix, as opposed to regional
  (link to a place) or technique/out-of-scope (flag and drop).
- **Technique**: not a genre at all, but a compositional/performance
  technique (e.g. "crab canon", "fauxbourdon", "call and response") that only
  ended up in the genre tree because Wikidata classified it `P31` music genre
  — handled via `manual_technique_genres.csv` (see step 3, technique bullet, below).
- **Out of scope**: not a music genre at all — Wikidata's `P31` "music
  genre" classification was simply wrong (e.g. a near-empty stub with no
  real description, a record label, an event, a person) — handled via
  `manual_out_of_scope_genres.csv` (see step 3, out-of-scope bullet, below).
  This is distinct from a real but off-topic genre (`manual_theme_genres.csv`,
  e.g. "LGBT music" — organized around a subject/theme rather than a
  geography or style, but still a genuine genre) and from a technique
  (`manual_technique_genres.csv`) — out of scope means the item isn't a
  genre in any sense.

Each generation of this file is a manual audit pass over the root list,
triaging each root into one of: regional, canonical parent, technique, out
of scope, or genuinely a standalone root genre (left alone).

## Review queue: unaccepted roots

Triage is an async review queue. A root not in the
`accepted_canonical_roots` curation list doesn't fail the
nightly run: `canonical_roots.py` writes it with `is_accepted = false` and logs a Silver WARNING
(`N unaccepted canonical root(s), flagged for review: ['Q… (label)', …]`), and Gold emits it with
`"isUnacceptedRoot": true` so it shows up in grow-the-music-tree-api's admin "Root review".

- Accepting a root in grow locks that row there, but resolving it in the curation lists (per the
  procedure below) is the canonical path — giving it a parent clears the flag on the next import.
- For a root confirmed genuinely standalone (including one already accepted in grow), still add it to
  the `accepted_canonical_roots` curation list (columns `item_id,item_label`) to stop the warning and the flag.
- Find the current queue with:
  ```sh
  duckdb -c ".mode csv" -c "SELECT item_id, item_label FROM '<SILVER_OUTPUT_DIR>/9_canonical_roots.parquet' WHERE NOT is_accepted ORDER BY item_label"
  ```

## Where things live

- **Curation rules live in grow-the-music-tree-api.** Each `manual_<list>.csv` named below is a
  curation list owned by grow-the-music-tree-api: add/edit/remove rows through its
  `curation/<list>/entries/` endpoints (e.g. `curation/regional_overrides/entries/`) or the grow admin
  UI. The `curation` pipeline (`uv run --package curation python -m curation.ingest`)
  pulls every list into `CURATION_BRONZE_DIR` as `manual_<list>.csv`; run it
  before Silver to pick up your changes locally.

- Root list to review: `<SILVER_OUTPUT_DIR>/9_canonical_roots.parquet` (git-ignored — regenerate with `uv run --package curation python -m curation.ingest && uv run --package wikidata python -m wikidata.silver` if stale or missing; see `pipelines/wikidata/README.md` for `SILVER_OUTPUT_DIR`). `canonical_roots.py` treats an item as a root if its `parent_id` is null **or** if `parent_id` points at a label that never has its own row in `7_canonical_hierarchy.parquet` — a "phantom" parent (e.g. "art music") that Wikidata never itself resolved down to a genre item. Most of this case is caught upstream: `hierarchy_utils.py`'s `promote_orphans_to_roots`, shared by `canonical_hierarchy.py` and `regional_hierarchy.py`, already recovers any item whose *only* parent edge is non-genre as its own root (`parent_id = null`) at step 7 — e.g. "electronic music" is a root outright, not a child of unresolvable "music". See the triage note in step 3 below before trying to link a phantom-parent root to a real parent.
- List to edit (regional): `manual_regional_overrides.csv` (see the comment block atop `regional_classification.py` for why it exists).
- CSV columns: `item_id,item_label,reason,overview_item_id`. `overview_item_id` **must** be the `item_id` of an existing `is_regional_overview` item (a `"music of &lt;place&gt;"` article already in the dataset) — it is not free-form, and the pipeline raises if it isn't found or isn't flagged `is_regional_overview`. That `item_id` is normally a real Wikidata QID, but may be a synthetic `LOCAL:`-prefixed id (see step 4) when the overview item itself was added that way.
- Second file, only needed when the overview item itself doesn't exist in the dataset yet: `manual_regional_overview_additions.csv` (see the comment block atop `regional_overview_classification.py`). Columns: `item_id,item_label,reason`. `item_label` **must** start with `"music of "` and `item_id` not already present anywhere in the genre tree — the pipeline raises otherwise. `item_id` is normally a real Wikidata QID, but a **synthetic id** (no real Wikidata item behind it) is allowed when no matching Wikidata overview item exists — see step 4. Not every Gold-layer grouping concept has a Wikidata counterpart, so this backstop isn't strictly QID-only.
- List to edit (technique): `manual_technique_genres.csv` (see the comment block atop `non_genre_pruning.py` for why it exists). CSV columns: `item_id,item_label,reason` — no `overview_item_id`, since technique items are dropped from the genre tree entirely at step 2 (`2_non_genre_pruning`, before regional classification or the cascade runs), so they never reach either `7_canonical_hierarchy.parquet` or `8_regional_hierarchy.parquet`. The pipeline raises on an unknown, blank, or duplicate `item_id` — the same `item_id` checks the regional files share, but without their extra `overview_item_id`/`is_regional_overview` and `"music of "`-label-prefix checks, which don't apply here.
- List to edit (out of scope): `manual_out_of_scope_genres.csv` (same mechanism as `manual_technique_genres.csv`, see the comment block atop `non_genre_pruning.py`). CSV columns: `item_id,item_label,reason` — same shape and same validation (unknown/blank/duplicate `item_id` raises) as the technique file, dropped at the same step-2 point, before any hierarchy output exists. Use this instead of the technique file when the item isn't a genre-adjacent concept at all, just a Wikidata `P31` misclassification.
- List to edit (canonical parent): `manual_main_parent.csv` (read by `regional_classification.py`, applied as a synthetic parent edge *before* the regional cascade runs at step 4, so `is_regional` flows naturally through it). CSV columns: `item_id,item_label,reason,parent_item_id`. `parent_item_id` **must** be an existing `item_id` already in the genre tree, and must **not** itself be flagged `is_regional`/`is_regional_overview` — the pipeline raises otherwise, since this file is for attaching a root to a real canonical parent genre, not a region. It also raises if `item_id` is unknown/blank/duplicated, or if `item_id` itself is flagged regional/regional-overview. `item_id` does **not** need to be parentless — the override always wins as the item's main parent in `5_main_parent_selection.parquet` regardless of how many other candidate parent edges the item already has, so this file can also be used to correct a mis-selected main parent on a non-root item, not just to attach an orphaned root. Rows here don't drop the item — they give it a parent, so it moves from being its own root to a child node under `parent_item_id` in `7_canonical_hierarchy.parquet`.

## Procedure

1. **Regenerate/read the current root list**:
   ```sh
   duckdb -c ".mode csv" -c "SELECT item_id, item_label FROM '<SILVER_OUTPUT_DIR>/9_canonical_roots.parquet' ORDER BY item_label"
   ```
2. **Pull the valid overview-item catalogue** (the only legal `overview_item_id` values) so you're matching against what actually exists, not guessing QIDs:
   ```sh
   duckdb -c ".mode csv" -c "SELECT DISTINCT item_id, item_label FROM '<SILVER_OUTPUT_DIR>/4_regional_classification.parquet' WHERE is_regional_overview ORDER BY item_label"
   ```
   (Regenerate that parquet first via the silver run above if it's stale.)
3. **Triage each root by confidence, don't force-fit everything**:
   - **Phantom-parent root, likely genuinely standalone**: most items whose only real-world parent is a non-genre umbrella (e.g. "electronic music" under "music") show up here with `parent_id` null — `promote_orphans_to_roots` recovers them as roots directly (step 7/8), so there's no dangling pointer left to notice. The residual case is a root whose `parent_id`/`parent_label` in `7_canonical_hierarchy.parquet` is non-null but points at a genre-classified label that itself never survived to its own row (e.g. a genre-classified umbrella that got excluded via `manual_out_of_scope_genres.csv`, or another as-yet-unnoticed phantom) — that parent won't survive into the hierarchy either way. Don't force this into `manual_main_parent.csv` pointing back at the same phantom (it would just raise, since that `parent_item_id` isn't a real `item_id` in the tree) or invent an unrelated parent for it — a major genre family sitting under a phantom umbrella term is usually a real root worth leaving standalone, not a mis-triage.
   - High confidence: label names a place explicitly (e.g. `bunde (Panama)`, `bodabil in the Philippines`), or is a well-known national/regional tradition (Cajun fiddle → Louisiana, Irish fiddle → Ireland).
   - Medium confidence: strongly-associated ethnic/regional style where the country is well known from general knowledge (e.g. Andalusian flamenco substyles, Japanese regional folk-song names, Algerian raï-adjacent genres).
   - **Ask, don't guess**: genres whose country/region is genuinely ambiguous (spans multiple plausible countries that each already have their own valid overview item — e.g. "murga" is iconic to both Uruguay and Argentina). Don't silently drop these and don't pick one on your own judgment — collect them during triage and ask the user to disambiguate (e.g. via `AskUserQuestion`, batched at the end of the triage pass rather than one at a time) before finalizing that item's row. Only fall back to leaving an item out untriaged if the user declines to answer or the ambiguity can't be resolved even with their input — false positives corrupt the regional tree silently, while omissions just leave a root uncollapsed for next time.
   - **Canonical parent, not regional**: the root is a genuine subgenre with no national/ethnic home (so `manual_regional_overrides.csv` doesn't apply), but a real parent genre for it already exists elsewhere in the canonical tree (e.g. "Posi music", a "positive"-themed punk/hardcore subgenre, belongs under "rock music"). Append it to `manual_main_parent.csv` instead (columns `item_id,item_label,reason,parent_item_id`, reason following the existing style e.g. `"root item with no P279/P361 parent; a <style> subgenre with no single national/ethnic home so manual_regional_overrides.csv does not apply"`), with `parent_item_id` set to the real parent's `item_id` already present in the tree.
   - **Technique, not a genre**: the root isn't a style of music at all, but a compositional or performance technique (e.g. "crab canon", "fauxbourdon", "call and response", "rondellus") — Wikidata classified it `P31` music genre, but it describes a technique applicable across many genres, not a genre itself. Append it to `manual_technique_genres.csv` instead (columns `item_id,item_label,reason`, reason following the existing style e.g. `"compositional technique, not a genre"`), not to `manual_regional_overrides.csv` — it has no regional angle and no `overview_item_id` to assign.
   - **Out of scope, not a genre-adjacent concept at all**: the root isn't a music genre, technique, or theme in any sense — a near-empty Wikidata stub with no real description, a record label, an event, a person, etc. — Wikidata's `P31` "music genre" classification was simply wrong. Append it to `manual_out_of_scope_genres.csv` instead (columns `item_id,item_label,reason`, reason following the existing style e.g. `"near-empty Wikidata stub (no description, no substance), not a real music genre"`). Verify via WebSearch before concluding an item is out of scope rather than just an obscure real genre — a low-information stub isn't automatically out of scope if independent sources confirm it names a real style of music.
   - Note: `regional_overview_classification.py` (step 3) auto-promotes `"music of &lt;place&gt;"` items that appear only as a `parent_label` in Bronze (never their own `item_id` row) into their own root row, so they're flagged `is_regional_overview` and become legal `overview_item_id` targets too — e.g. "music of Wales" is now in the catalogue from step 3, even though it's never itself `P31` instance-of music genre in Bronze. Curating which broader region a promoted item nests under (e.g. Wales → "music of the United Kingdom") is still exactly what this file is for.
4. **When a root's country/region is clear but no matching `"music of &lt;place&gt;"` overview item exists in the catalogue at all** (not even via auto-promotion — e.g. "music of Trinidad and Tobago" for "kaiso"), don't force it onto a loose proxy region:
   - **First choice — real Wikidata item exists**: look up the item's real Wikidata QID (e.g. via WebFetch/WebSearch against `wikidata.org`) and confirm its label genuinely starts with `"music of "`; if found, append it to `manual_regional_overview_additions.csv` (columns `item_id,item_label,reason`) so it becomes a legal `overview_item_id` target. Never fabricate a QID and never add an item whose label doesn't literally start with `"music of "` — the pipeline enforces both.
   - **No real Wikidata item exists** (confirmed via WebSearch, e.g. no `"music of <place/group>"` item for a cross-national or non-national grouping like "indigenous peoples of the Americas"): a **synthetic overview item** is allowed as a last resort, since not every useful grouping concept has a Wikidata counterpart and Gold-layer items in general won't all map 1:1 to QIDs. Give it a synthetic `item_id` that cannot collide with or be mistaken for a real QID (real QIDs are always `Q` + digits — e.g. use a `LOCAL:` prefix, such as `LOCAL:indigenous-americas`), and an `item_label` that still starts with `"music of "` (e.g. `"music of Indigenous peoples of the Americas"`) so it satisfies the pipeline's prefix check and reads consistently in the hierarchy. Mark the `reason` column as `"synthetic (no matching Wikidata overview item)"` so it's obviously distinguishable from a real, QID-backed addition on inspection. Note the caveat: `item_url` for such rows is built as `WIKIDATA_ITEM_URL_PREFIX + item_id` and will not resolve to a real Wikidata page — that's expected and acceptable for a synthetic entry, not a bug.
   - Either way, this still isn't a live fetch by the pipeline itself — the id/label pair is authored by hand, same as `manual_regional_overrides.csv` (Silver never fetches raw data — see `CLAUDE.md`).
5. **Add entries** to the `regional_overrides` list (via grow's `curation/regional_overrides/entries/` endpoint or admin UI), one per accepted item, with a reason following the existing style: `"root item with no P279/P361 parent and no P2341/P495 value, but a &lt;nationality&gt; genre missed by the automated seed/indigenous_to/country_of_origin classification"`.
6. **Validate by running the pipeline**, not just eyeballing the CSVs — this is the real integrity check (unknown `item_id`, unknown/non-overview `overview_item_id`, duplicate rows, a `manual_regional_overview_additions.csv` label not starting with `"music of "`, or an addition already present in the tree all raise):
   ```sh
   uv run --package curation python -m curation.ingest && uv run --package wikidata python -m wikidata.silver
   ```
   A clean run + a drop in `9_canonical_roots.parquet`'s row count (and no unaccepted-root WARNING for the items you resolved) confirms the additions were accepted.
7. **Check for duplicate `item_id`s** across each file (not just what you added — someone else may have added the same root since):
   ```sh
   python3 -c "
   import csv, os
   d = os.environ['CURATION_BRONZE_DIR']
   for path in (
       f'{d}/manual_regional_overrides.csv',
       f'{d}/manual_regional_overview_additions.csv',
       f'{d}/manual_main_parent.csv',
       f'{d}/manual_technique_genres.csv',
       f'{d}/manual_out_of_scope_genres.csv',
   ):
       seen = set()
       with open(path) as f:
           for row in csv.DictReader(f):
               iid = row['item_id']
               assert iid not in seen, f'dup {iid} in {path}'
               seen.add(iid)
   print('ok')
   "
   ```
8. **Report honestly**: these lists are curated from general/world-music knowledge, not fact-checked against live Wikidata per item (except any newly-added `manual_regional_overview_additions.csv` rows, and any `manual_out_of_scope_genres.csv` rows, both of which are looked up live and should be reported as such). Say how many rows were added to each file (`manual_regional_overrides.csv`, `manual_regional_overview_additions.csv`, `manual_main_parent.csv`, `manual_technique_genres.csv`, and `manual_out_of_scope_genres.csv`), what the root count went from/to, and list the specific items still left untriaged (not just the categories) — those are exactly the ones that should have already been raised via the "Ask, don't guess" step, so by this point they're only the ones the user declined or couldn't resolve; call these out by name so the user (or a future pass) knows what's still open, and can spot-check before committing. Explicitly call out any **synthetic** (non-QID) overview items added, since they read differently in the resulting hierarchy (no real Wikidata page behind them), and any items routed to the canonical-parent, technique, or out-of-scope files instead of the regional one.

## Non-goals

- Don't touch the automated classification logic (`regional_classification.py`, `regional_overview_classification.py`) — this skill is purely about the manual CSV backstops.
- Don't try to collapse `8_regional_hierarchy.parquet`'s root count — per CLAUDE.md, one root per region there is expected, not a bug.
- Prefer a real, confirmed Wikidata QID for `manual_regional_overview_additions.csv` whenever one exists — only fall back to a synthetic `LOCAL:`-prefixed id (per step 4) after confirming via WebSearch that no matching `"music of "` item exists on Wikidata. Never invent a QID-shaped id (`Q` + digits) that isn't real — that would silently masquerade as a genuine Wikidata reference.
