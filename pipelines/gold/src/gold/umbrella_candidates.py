import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

OUTPUT_NAME = "1_umbrella_candidates.csv"


def export_umbrella_candidates(canonical_hierarchy_path: Path, genre_match_path: Path, output_dir: Path) -> Path:
    """Triage aid for the `umbrella_genres` curation list: canonical genres with children that are not yet
    umbrellas, most directly-assigned songs first (a genre with many direct songs and many children is the
    likeliest umbrella)."""
    hierarchy = pl.read_parquet(canonical_hierarchy_path)
    direct_counts = dict(
        pl.scan_parquet(genre_match_path)
        .filter(pl.col("wikidata_genre_item_id").is_not_null())
        .group_by("wikidata_genre_item_id")
        .len()
        .collect()
        .iter_rows()
    )
    children: dict[str, list[str]] = {}
    for item_id, parent_id in hierarchy.select("item_id", "parent_id").iter_rows():
        children.setdefault(parent_id, []).append(item_id)

    def descendant_song_count(item_id: str) -> int:
        return sum(direct_counts.get(child, 0) + descendant_song_count(child) for child in children.get(item_id, []))

    rows = [
        {
            "item_id": row["item_id"],
            "item_label": row["item_label"],
            "child_count": len(children[row["item_id"]]),
            "direct_song_count": direct_counts.get(row["item_id"], 0),
            "descendant_song_count": descendant_song_count(row["item_id"]),
        }
        for row in hierarchy.filter(~pl.col("is_umbrella")).iter_rows(named=True)
        if row["item_id"] in children
    ]
    candidates = pl.DataFrame(
        rows,
        schema={
            "item_id": pl.Utf8,
            "item_label": pl.Utf8,
            "child_count": pl.Int64,
            "direct_song_count": pl.Int64,
            "descendant_song_count": pl.Int64,
        },
    ).sort(["direct_song_count", "item_id"], descending=[True, False])

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_NAME
    candidates.write_csv(output_path)
    logger.info("wrote %d umbrella candidate(s) to %s", candidates.height, output_path)
    return output_path
