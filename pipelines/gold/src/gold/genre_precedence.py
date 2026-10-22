import logging
from pathlib import Path

import polars as pl

from gold.curation_csv import ITEM_ID_PATTERN, read_curation_csv
from gold.genre_tree_builder import load_extra_parent_edges

logger = logging.getLogger(__name__)

PRECEDENCE_CSV = "manual_genre_precedence.csv"


def _lineage_edges(hierarchy: pl.DataFrame, secondary_parents_path: Path) -> pl.DataFrame:
    item_ids = set(hierarchy.get_column("item_id"))
    primary = hierarchy.filter(pl.col("parent_id").is_in(list(item_ids))).select(winner="item_id", loser="parent_id")
    secondary = load_extra_parent_edges(secondary_parents_path, item_ids, item_ids).select(
        winner="item_id", loser="parent_id"
    )
    return pl.concat([primary, secondary])


def _manual_edges(path: Path, item_ids: set[str]) -> pl.DataFrame:
    rules = read_curation_csv(
        path,
        required=("item_id", "over_item_id", "reason"),
        key=("item_id", "over_item_id"),
        patterns={"item_id": ITEM_ID_PATTERN, "over_item_id": ITEM_ID_PATTERN},
    ).select(winner="item_id", loser="over_item_id")
    unknown = sorted((set(rules["winner"]) | set(rules["loser"])) - item_ids)
    if unknown:
        raise ValueError(f"{path.name} references item_id(s) not found in the canonical hierarchy: {unknown}")
    return rules


def precedence_closure(
    hierarchy: pl.DataFrame, secondary_parents_path: Path, manual_precedence_path: Path
) -> pl.DataFrame:
    """Every `(winner, loser)` pair where a song carrying both genres keeps only `winner`: a genre beats
    its canonical ancestors (primary and secondary parents) and whatever `manual_genre_precedence.csv`
    says, transitively (ska > reggae > dub => ska > dub). Raises on a self-pair or cycle, e.g. a manual
    rule reversing a lineage edge."""
    edges = pl.concat(
        [
            _lineage_edges(hierarchy, secondary_parents_path),
            _manual_edges(manual_precedence_path, set(hierarchy.get_column("item_id"))),
        ]
    ).unique()
    closure = edges
    while True:
        extended = pl.concat(
            [closure, closure.join(closure, left_on="loser", right_on="winner").select("winner", loser="loser_right")]
        ).unique()
        if extended.height == closure.height:
            break
        closure = extended

    cyclic = sorted(closure.filter(pl.col("winner") == pl.col("loser"))["winner"])
    if cyclic:
        raise ValueError(f"genre precedence (lineage + {PRECEDENCE_CSV}) has a self-pair or cycle involving: {cyclic}")
    logger.info("genre precedence closure: %d (winner, loser) pair(s)", closure.height)
    return closure


def apply_precedence(song_genre: pl.LazyFrame, closure: pl.DataFrame) -> pl.LazyFrame:
    """`song_genre` (`musicbrainz_recording_id, item_id, weight, ...`) minus every genre beaten by another
    genre of the same song; the winner inherits the highest beaten weight when above its own."""
    rules = closure.lazy()
    beaten = song_genre.join(rules, left_on="item_id", right_on="loser").join(
        song_genre.select("musicbrainz_recording_id", winner="item_id"),
        on=["musicbrainz_recording_id", "winner"],
        how="semi",
    )
    inherited = beaten.group_by("musicbrainz_recording_id", "winner").agg(pl.col("weight").max().alias("beaten_weight"))
    return (
        song_genre.join(
            beaten.select("musicbrainz_recording_id", "item_id").unique(),
            on=["musicbrainz_recording_id", "item_id"],
            how="anti",
        )
        .join(
            inherited,
            left_on=["musicbrainz_recording_id", "item_id"],
            right_on=["musicbrainz_recording_id", "winner"],
            how="left",
        )
        .with_columns(pl.max_horizontal("weight", "beaten_weight").alias("weight"))
        .drop("beaten_weight")
    )
