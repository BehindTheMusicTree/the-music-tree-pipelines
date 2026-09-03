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
   - **Skip, don't guess**: genres whose country/region is ambiguous (spans multiple plausible countries), or where no matching `"music of &lt;place&gt;"` overview item exists in the catalogue from step 2 (e.g. no "music of Trinidad", no "music of Wales" — don't force these onto a loose proxy like "music of the United Kingdom" unless the user says that's acceptable). When in doubt, leave it out and say so — false positives corrupt the regional tree silently, while omissions just leave a root uncollapsed for next time.
4. **Append rows**, one per accepted item, with a reason following the existing style: `"root item with no P279/P361 parent and no P2341/P495 value, but a &lt;nationality&gt; genre missed by the automated seed/indigenous_to/country_of_origin classification"`. Use Python's `csv` module (via a scratch script) rather than hand-editing — several labels contain commas/accents that need correct quoting.
5. **Validate by running the pipeline**, not just eyeballing the CSV — this is the real integrity check (unknown `item_id`, unknown/non-overview `overview_item_id`, duplicate rows all raise):
   ```sh
   uv run --package wikidata python -m wikidata.silver
   ```
   A clean run + a drop in `6_canonical_roots.parquet`'s row count confirms the additions were accepted.
6. **Check for duplicate `item_id`s** across the whole file (not just what you added — someone else may have added the same root since):
   ```sh
   python3 -c "
   import csv
   seen = set()
   with open('pipelines/wikidata/src/wikidata/silver/manual_regional_overrides.csv') as f:
       for row in csv.DictReader(f):
           iid = row['item_id']
           assert iid not in seen, f'dup {iid}'
           seen.add(iid)
   print('ok')
   "
   ```
7. **Report honestly**: this file is curated from general/world-music knowledge, not fact-checked against live Wikidata per item. Say how many rows were added, what the root count went from/to, and name the categories of items skipped for being too ambiguous/uncertain — so the user (or a future pass) knows what's still open, and can spot-check before committing.

## Non-goals

- Don't touch the automated classification logic (`regional_classification.py`, `regional_overview_classification.py`) — this skill is purely about the manual CSV backstop.
- Don't try to collapse `5_regional_hierarchy.parquet`'s root count — per CLAUDE.md, one root per region there is expected, not a bug.
- Don't invent new `"music of &lt;place&gt;"` overview items — if one doesn't exist in the Bronze/Silver dataset, that genre isn't addressable by this mechanism yet; leave it out.
