---
name: wikidata-regional-overrides
description: Curate pipelines/wikidata's canonical roots (6_canonical_roots.parquet) by giving each one a real place, a real parent genre, or a flag explaining why it's excluded — mapping nationally/ethnically-specific genres to their "music of <place>" overview item (manual_regional_overrides.csv), attaching orphaned subgenres to a real parent genre already in the tree (manual_canonical_parents.csv), and flagging roots that are a technique, not a genre at all (manual_technique_genres.csv), or aren't a music genre at all — a Wikidata misclassification (manual_out_of_scope_genres.csv). Use when asked to review/triage canonical roots, shrink the canonical root count, link roots to their actual parent genre, or curate/update manual_regional_overrides.csv / manual_canonical_parents.csv / manual_technique_genres.csv / manual_out_of_scope_genres.csv.
---

# Wikidata Silver: canonical roots curation

`5_hierarchy.parquet` (canonical) is supposed to collapse toward a handful of
real genre-family roots. It currently has a long tail of roots that fall into
one of four buckets the automated classification/parent-linking
(`3_regional_classification.py`, `4_genre_parents.py`) doesn't catch:

- **Regional**: national/ethnic genres with no `P279`/`P361` parent and no
  `P2341` (indigenous to) / `P495` (country of origin) value to seed from —
  handled via `manual_regional_overrides.csv` (this skill's main focus).
- **Canonical parent**: not regional at all — a genuine subgenre that's a
  root only because Wikidata never gave it a `P279`/`P361` parent edge (or
  the edge it had didn't survive Bronze/Silver), but a real parent genre
  already exists elsewhere in the canonical tree (e.g. "Foxcore", a 1990s
  female-fronted rock subgenre with no national/ethnic home, belongs under
  "rock music", not under any region) — handled via
  `manual_canonical_parents.csv` (see step 3, canonical-parent bullet, below).
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

## Where things live

- Root list to review: `<SILVER_OUTPUT_DIR>/6_canonical_roots.parquet` (git-ignored — regenerate with `uv run --package wikidata python -m wikidata.silver` if stale or missing; see `pipelines/wikidata/README.md` for `SILVER_OUTPUT_DIR`).
- File to edit (regional): `pipelines/wikidata/src/wikidata/silver/manual_regional_overrides.csv` (git-tracked, hand-curated — see the comment block atop `regional_classification.py` for why it exists).
- CSV columns: `item_id,item_label,reason,overview_item_id`. `overview_item_id` **must** be the `item_id` of an existing `is_regional_overview` item (a `"music of &lt;place&gt;"` article already in the dataset) — it is not free-form, and the pipeline raises if it isn't found or isn't flagged `is_regional_overview`. That `item_id` is normally a real Wikidata QID, but may be a synthetic `LOCAL:`-prefixed id (see step 4) when the overview item itself was added that way.
- Second file, only needed when the overview item itself doesn't exist in the dataset yet: `pipelines/wikidata/src/wikidata/silver/manual_regional_overview_additions.csv` (git-tracked, hand-curated — see the comment block atop `regional_overview_classification.py`). Columns: `item_id,item_label,reason`. `item_label` **must** start with `"music of "` and `item_id` not already present anywhere in the genre tree — the pipeline raises otherwise. `item_id` is normally a real Wikidata QID, but a **synthetic id** (no real Wikidata item behind it) is allowed when no matching Wikidata overview item exists — see step 4. Not every Gold-layer grouping concept has a Wikidata counterpart, so this backstop isn't strictly QID-only.
- File to edit (technique): `pipelines/wikidata/src/wikidata/silver/manual_technique_genres.csv` (git-tracked, hand-curated — see the comment block atop `hierarchy.py` for why it exists). CSV columns: `item_id,item_label,reason` — no `overview_item_id`, since technique items are dropped entirely from both `5_hierarchy.parquet` and `5_regional_hierarchy.parquet`, not nested anywhere. The pipeline raises on an unknown, blank, or duplicate `item_id` — the same `item_id` checks the regional files share, but without their extra `overview_item_id`/`is_regional_overview` and `"music of "`-label-prefix checks, which don't apply here.
- File to edit (out of scope): `pipelines/wikidata/src/wikidata/silver/manual_out_of_scope_genres.csv` (git-tracked, hand-curated — same mechanism as `manual_technique_genres.csv`, see the comment block atop `hierarchy.py`). CSV columns: `item_id,item_label,reason` — same shape and same validation (unknown/blank/duplicate `item_id` raises) as the technique file, and dropped identically from both outputs. Use this instead of the technique file when the item isn't a genre-adjacent concept at all, just a Wikidata `P31` misclassification.
- File to edit (canonical parent): `pipelines/wikidata/src/wikidata/silver/manual_canonical_parents.csv` (git-tracked, hand-curated — read by `genre_parents.py`, applied at step 4, after the regional cascade (step 3) and before the hierarchy collapse (step 5)). CSV columns: `item_id,item_label,reason,parent_item_id`. `parent_item_id` **must** be an existing `item_id` already in the genre tree, and must **not** itself be flagged `is_regional`/`is_regional_overview` — the pipeline raises otherwise, since this file is for attaching a root to a real canonical parent genre, not a region. It also raises if `item_id` is unknown/blank/duplicated, if `item_id` itself is flagged regional/regional-overview, or if `item_id` already has a parent edge from Bronze (this file is only for genuinely parentless roots, not for overriding an existing edge). Unlike the other three files, rows here don't drop the item — they give it a parent, so it moves from being its own root to a child node under `parent_item_id` in `5_hierarchy.parquet`.

## Procedure

1. **Regenerate/read the current root list**:
   ```sh
   duckdb -c ".mode csv" -c "SELECT item_id, item_label FROM '<SILVER_OUTPUT_DIR>/6_canonical_roots.parquet' ORDER BY item_label"
   ```
2. **Pull the valid overview-item catalogue** (the only legal `overview_item_id` values) so you're matching against what actually exists, not guessing QIDs:
   ```sh
   duckdb -c ".mode csv" -c "SELECT DISTINCT item_id, item_label FROM '<SILVER_OUTPUT_DIR>/3_regional_classification.parquet' WHERE is_regional_overview ORDER BY item_label"
   ```
   (Regenerate that parquet first via the silver run above if it's stale.)
3. **Triage each root by confidence, don't force-fit everything**:
   - High confidence: label names a place explicitly (e.g. `bunde (Panama)`, `bodabil in the Philippines`), or is a well-known national/regional tradition (Cajun fiddle → Louisiana, Irish fiddle → Ireland).
   - Medium confidence: strongly-associated ethnic/regional style where the country is well known from general knowledge (e.g. Andalusian flamenco substyles, Japanese regional folk-song names, Algerian raï-adjacent genres).
   - **Skip, don't guess**: genres whose country/region is genuinely ambiguous (spans multiple plausible countries that each already have their own valid overview item — e.g. "murga" is iconic to both Uruguay and Argentina). When in doubt, leave it out and say so — false positives corrupt the regional tree silently, while omissions just leave a root uncollapsed for next time.
   - **Canonical parent, not regional**: the root is a genuine subgenre with no national/ethnic home (so `manual_regional_overrides.csv` doesn't apply), but a real parent genre for it already exists elsewhere in the canonical tree (e.g. "Posi music", a "positive"-themed punk/hardcore subgenre, belongs under "rock music"). Append it to `manual_canonical_parents.csv` instead (columns `item_id,item_label,reason,parent_item_id`, reason following the existing style e.g. `"root item with no P279/P361 parent; a <style> subgenre with no single national/ethnic home so manual_regional_overrides.csv does not apply"`), with `parent_item_id` set to the real parent's `item_id` already present in the tree.
   - **Technique, not a genre**: the root isn't a style of music at all, but a compositional or performance technique (e.g. "crab canon", "fauxbourdon", "call and response", "rondellus") — Wikidata classified it `P31` music genre, but it describes a technique applicable across many genres, not a genre itself. Append it to `manual_technique_genres.csv` instead (columns `item_id,item_label,reason`, reason following the existing style e.g. `"compositional technique, not a genre"`), not to `manual_regional_overrides.csv` — it has no regional angle and no `overview_item_id` to assign.
   - **Out of scope, not a genre-adjacent concept at all**: the root isn't a music genre, technique, or theme in any sense — a near-empty Wikidata stub with no real description, a record label, an event, a person, etc. — Wikidata's `P31` "music genre" classification was simply wrong. Append it to `manual_out_of_scope_genres.csv` instead (columns `item_id,item_label,reason`, reason following the existing style e.g. `"near-empty Wikidata stub (no description, no substance), not a real music genre"`). Verify via WebSearch before concluding an item is out of scope rather than just an obscure real genre — a low-information stub isn't automatically out of scope if independent sources confirm it names a real style of music.
   - Note: `regional_overview_classification.py` (step 2) auto-promotes `"music of &lt;place&gt;"` items that appear only as a `parent_label` in Bronze (never their own `item_id` row) into their own root row, so they're flagged `is_regional_overview` and become legal `overview_item_id` targets too — e.g. "music of Wales" is now in the catalogue from step 2, even though it's never itself `P31` instance-of music genre in Bronze. Curating which broader region a promoted item nests under (e.g. Wales → "music of the United Kingdom") is still exactly what this file is for.
4. **When a root's country/region is clear but no matching `"music of &lt;place&gt;"` overview item exists in the catalogue at all** (not even via auto-promotion — e.g. "music of Trinidad and Tobago" for "kaiso"), don't force it onto a loose proxy region:
   - **First choice — real Wikidata item exists**: look up the item's real Wikidata QID (e.g. via WebFetch/WebSearch against `wikidata.org`) and confirm its label genuinely starts with `"music of "`; if found, append it to `manual_regional_overview_additions.csv` (columns `item_id,item_label,reason`) so it becomes a legal `overview_item_id` target. Never fabricate a QID and never add an item whose label doesn't literally start with `"music of "` — the pipeline enforces both.
   - **No real Wikidata item exists** (confirmed via WebSearch, e.g. no `"music of <place/group>"` item for a cross-national or non-national grouping like "indigenous peoples of the Americas"): a **synthetic overview item** is allowed as a last resort, since not every useful grouping concept has a Wikidata counterpart and Gold-layer items in general won't all map 1:1 to QIDs. Give it a synthetic `item_id` that cannot collide with or be mistaken for a real QID (real QIDs are always `Q` + digits — e.g. use a `LOCAL:` prefix, such as `LOCAL:indigenous-americas`), and an `item_label` that still starts with `"music of "` (e.g. `"music of Indigenous peoples of the Americas"`) so it satisfies the pipeline's prefix check and reads consistently in the hierarchy. Mark the `reason` column as `"synthetic (no matching Wikidata overview item)"` so it's obviously distinguishable from a real, QID-backed addition on inspection. Note the caveat: `item_url` for such rows is built as `WIKIDATA_ITEM_URL_PREFIX + item_id` and will not resolve to a real Wikidata page — that's expected and acceptable for a synthetic entry, not a bug.
   - Either way, this still isn't a live fetch by the pipeline itself — the id/label pair is authored by hand, same as `manual_regional_overrides.csv` (Silver never fetches raw data — see `CLAUDE.md`).
5. **Append rows** to `manual_regional_overrides.csv`, one per accepted item, with a reason following the existing style: `"root item with no P279/P361 parent and no P2341/P495 value, but a &lt;nationality&gt; genre missed by the automated seed/indigenous_to/country_of_origin classification"`. Use Python's `csv` module (via a scratch script) rather than hand-editing — several labels contain commas/accents that need correct quoting.
6. **Validate by running the pipeline**, not just eyeballing the CSVs — this is the real integrity check (unknown `item_id`, unknown/non-overview `overview_item_id`, duplicate rows, a `manual_regional_overview_additions.csv` label not starting with `"music of "`, or an addition already present in the tree all raise):
   ```sh
   uv run --package wikidata python -m wikidata.silver
   ```
   A clean run + a drop in `6_canonical_roots.parquet`'s row count confirms the additions were accepted.
7. **Check for duplicate `item_id`s** across each file (not just what you added — someone else may have added the same root since):
   ```sh
   python3 -c "
   import csv
   for path in (
       'pipelines/wikidata/src/wikidata/silver/manual_regional_overrides.csv',
       'pipelines/wikidata/src/wikidata/silver/manual_regional_overview_additions.csv',
       'pipelines/wikidata/src/wikidata/silver/manual_canonical_parents.csv',
       'pipelines/wikidata/src/wikidata/silver/manual_technique_genres.csv',
       'pipelines/wikidata/src/wikidata/silver/manual_out_of_scope_genres.csv',
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
8. **Report honestly**: this file is curated from general/world-music knowledge, not fact-checked against live Wikidata per item (except any newly-added `manual_regional_overview_additions.csv` rows, and any `manual_out_of_scope_genres.csv` rows, both of which are looked up live and should be reported as such). Say how many rows were added to each file (`manual_regional_overrides.csv`, `manual_regional_overview_additions.csv`, `manual_canonical_parents.csv`, `manual_technique_genres.csv`, and `manual_out_of_scope_genres.csv`), what the root count went from/to, and name the categories of items skipped for being too ambiguous/uncertain — so the user (or a future pass) knows what's still open, and can spot-check before committing. Explicitly call out any **synthetic** (non-QID) overview items added, since they read differently in the resulting hierarchy (no real Wikidata page behind them), and any items routed to the canonical-parent, technique, or out-of-scope files instead of the regional one.

## Non-goals

- Don't touch the automated classification logic (`regional_classification.py`, `regional_overview_classification.py`) — this skill is purely about the manual CSV backstops.
- Don't try to collapse `5_regional_hierarchy.parquet`'s root count — per CLAUDE.md, one root per region there is expected, not a bug.
- Prefer a real, confirmed Wikidata QID for `manual_regional_overview_additions.csv` whenever one exists — only fall back to a synthetic `LOCAL:`-prefixed id (per step 4) after confirming via WebSearch that no matching `"music of "` item exists on Wikidata. Never invent a QID-shaped id (`Q` + digits) that isn't real — that would silently masquerade as a genuine Wikidata reference.
