---
name: wikidata-regional-overrides
description: Curate pipelines/wikidata's manual_regional_overrides.csv by reviewing 6_canonical_roots.parquet for genres that are actually nationally/ethnically specific (missed by the automated seed/indigenous_to/country_of_origin classification) and mapping each to its "music of <place>" overview item. Use when asked to review canonical roots for regional genres, shrink the canonical root count, or curate/update manual_regional_overrides.csv.
---

# Wikidata Silver: manual regional overrides curation

`5_hierarchy.parquet` (canonical) is supposed to collapse toward a handful of
real genre-family roots. It currently has a long tail of roots that are
actually national/ethnic genres the automated classification (`3_regional_classification.py`)
didn't catch — because they have no `P279`/`P361` parent and no `P2341`
(indigenous to) / `P495` (country of origin) value to seed from. Each generation of
this file is a manual audit pass over that root list.

## Where things live

- Root list to review: `<SILVER_OUTPUT_DIR>/6_canonical_roots.parquet` (git-ignored — regenerate with `uv run --package wikidata python -m wikidata.silver` if stale or missing; see `pipelines/wikidata/README.md` for `SILVER_OUTPUT_DIR`).
- File to edit: `pipelines/wikidata/src/wikidata/silver/manual_regional_overrides.csv` (git-tracked, hand-curated — see the comment block atop `regional_classification.py` for why it exists).
- CSV columns: `item_id,item_label,reason,overview_item_id`. `overview_item_id` **must** be the QID of an existing `is_regional_overview` item (a `"music of &lt;place&gt;"` article already in the dataset) — it is not free-form, and the pipeline raises if it isn't found or isn't flagged `is_regional_overview`.
- Second file, only needed when the overview item itself doesn't exist in the dataset yet: `pipelines/wikidata/src/wikidata/silver/manual_regional_overview_additions.csv` (git-tracked, hand-curated — see the comment block atop `regional_overview_classification.py`). Columns: `item_id,item_label,reason`. `item_label` **must** start with `"music of "` and `item_id` must be a real Wikidata QID not already present anywhere in the genre tree — the pipeline raises otherwise.

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
   - Note: `regional_overview_classification.py` (step 2) auto-promotes `"music of &lt;place&gt;"` items that appear only as a `parent_label` in Bronze (never their own `item_id` row) into their own root row, so they're flagged `is_regional_overview` and become legal `overview_item_id` targets too — e.g. "music of Wales" is now in the catalogue from step 2, even though it's never itself `P31` instance-of music genre in Bronze. Curating which broader region a promoted item nests under (e.g. Wales → "music of the United Kingdom") is still exactly what this file is for.
4. **When a root's country/region is clear but no matching `"music of &lt;place&gt;"` overview item exists in the catalogue at all** (not even via auto-promotion — e.g. "music of Trinidad and Tobago" for "kaiso"), don't force it onto a loose proxy region and don't invent a QID. Instead, look up the item's real Wikidata QID (e.g. via WebFetch/WebSearch against `wikidata.org`) and confirm its label genuinely starts with `"music of "`; if found, append it to `manual_regional_overview_additions.csv` (columns `item_id,item_label,reason`) so it becomes a legal `overview_item_id` target — never fabricate a QID, and never add an item whose label doesn't literally start with `"music of "`, since the pipeline enforces both. This still isn't a live fetch by the pipeline itself — the QID/label pair is authored by hand, same as `manual_regional_overrides.csv` (Silver never fetches raw data — see `CLAUDE.md`).
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
8. **Report honestly**: this file is curated from general/world-music knowledge, not fact-checked against live Wikidata per item (except any newly-added `manual_regional_overview_additions.csv` rows, which are looked up live and should be reported as such). Say how many rows were added to each file, what the root count went from/to, and name the categories of items skipped for being too ambiguous/uncertain — so the user (or a future pass) knows what's still open, and can spot-check before committing.

## Non-goals

- Don't touch the automated classification logic (`regional_classification.py`, `regional_overview_classification.py`) — this skill is purely about the manual CSV backstops.
- Don't try to collapse `5_regional_hierarchy.parquet`'s root count — per CLAUDE.md, one root per region there is expected, not a bug.
- Don't fabricate a QID for `manual_regional_overview_additions.csv` — only add an item after confirming its real Wikidata QID and that its label genuinely starts with `"music of "`; if it can't be confirmed, leave the root uncurated rather than guess.
