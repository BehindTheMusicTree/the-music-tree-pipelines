import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# Committed alongside the code (not a gitignored silver output): the set of roots a data expert has
# already triaged and accepted as genuinely standalone canonical genres (see
# .claude/skills/wikidata-canonical-roots/SKILL.md). A root that isn't in this list is new since the
# last triage pass: it's flagged (`is_accepted` false, carried into Gold as `isUnacceptedRoot`) for
# review rather than failing the run — either given a real parent (manual_main_parent.csv), flagged as
# theme/technique/out-of-scope, or added here once confirmed standalone.
MANUAL_ACCEPTED_ROOTS_PATH = Path(__file__).parent / "manual_accepted_canonical_roots.csv"


def extract_canonical_roots(hierarchy_path: Path, manual_accepted_roots_path: Path, output_dir: Path) -> Path:
    logger.info("extracting canonical roots from %s", hierarchy_path)
    df = pl.read_parquet(hierarchy_path)

    # A `parent_id` can point at a label that never has its own row in this file (e.g. "popular
    # music" only ever appears as a parent value, never as an item that itself resolved down to a
    # parent) — such a parent is a dead end, not a real ancestor, so the item pointing at it is a
    # root just as much as one with a null parent_id.
    known_item_ids = df.select("item_id").unique().to_series().to_list()
    is_root = pl.col("parent_id").is_null() | ~pl.col("parent_id").is_in(known_item_ids)
    roots = df.filter(is_root).select("item_id", "item_label", "item_url").sort("item_label")

    accepted_ids = set(pl.read_csv(manual_accepted_roots_path).select("item_id").to_series())
    roots = roots.with_columns(is_accepted=pl.col("item_id").is_in(accepted_ids))
    new_roots = roots.filter(~pl.col("is_accepted"))
    if not new_roots.is_empty():
        rows = [f"{r['item_id']} ({r['item_label']})" for r in new_roots.iter_rows(named=True)]
        logger.warning("%d unaccepted canonical root(s), flagged for review: %s", len(rows), rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "9_canonical_roots.parquet"
    roots.write_parquet(output_path)
    logger.info("wrote %d rows to %s", roots.height, output_path)

    return output_path
