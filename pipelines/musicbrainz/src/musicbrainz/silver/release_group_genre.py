import logging
from pathlib import Path

import polars as pl
from common.musicbrainz_artists import SPECIAL_PURPOSE_ARTIST_IDS

from musicbrainz.silver.recording_genre import genre_tags
from musicbrainz.silver.release_group_types import primary_types, secondary_types

logger = logging.getLogger(__name__)

# Release groups whose genre is a song's own: a compilation, soundtrack, live album or mix gathers songs of other
# genres, or other versions of them.
ELIGIBLE_PRIMARY_TYPES = ["album", "single", "ep"]
EXCLUDED_SECONDARY_TYPES = ["compilation", "soundtrack", "live", "dj-mix", "remix", "mixtape/street"]


def eligible_release_groups(bronze_dir: Path) -> pl.LazyFrame:
    """`(release_group,)` of release groups whose genre tags describe their songs: an album, single, EP or untyped
    release group, of none of `EXCLUDED_SECONDARY_TYPES`, credited to no special-purpose artist (Various Artists...)."""
    special_purpose_credit = (
        pl.scan_parquet(bronze_dir / "artist_credit_name.parquet")
        .join(
            pl.scan_parquet(bronze_dir / "artist.parquet")
            .filter(pl.col("gid").is_in(SPECIAL_PURPOSE_ARTIST_IDS))
            .select(artist="id"),
            on="artist",
        )
        .select("artist_credit")
        .unique()
    )
    return (
        pl.scan_parquet(bronze_dir / "release_group.parquet")
        .select("type", "artist_credit", release_group="id")
        .join(primary_types(bronze_dir), on="type", how="left")
        .filter(pl.col("type").is_null() | pl.col("primary_type").is_in(ELIGIBLE_PRIMARY_TYPES))
        .join(secondary_types(bronze_dir), on="release_group", how="left")
        .filter(
            pl.col("secondary_types").is_null()
            | ~pl.col("secondary_types").list.eval(pl.element().is_in(EXCLUDED_SECONDARY_TYPES)).list.any()
        )
        .join(special_purpose_credit, on="artist_credit", how="anti")
        .select("release_group")
    )


def release_group_genre(bronze_dir: Path, output_dir: Path) -> Path:
    """One row per (eligible release group, genre): its own genre tags plus its releases' (an edition carries its
    album's genre), weights summed."""
    release_group_tags = genre_tags(
        pl.scan_parquet(bronze_dir / "release_group_tag.parquet"), "release_group", bronze_dir
    ).rename({"entity_id": "release_group_id"})
    release_tags = (
        genre_tags(pl.scan_parquet(bronze_dir / "release_tag.parquet"), "release", bronze_dir)
        .join(
            pl.scan_parquet(bronze_dir / "release.parquet").select(entity_id="id", release_group_id="release_group"),
            on="entity_id",
        )
        .select("release_group_id", "genre_id", "weight")
    )
    result = (
        pl.concat([release_group_tags, release_tags])
        .join(eligible_release_groups(bronze_dir), left_on="release_group_id", right_on="release_group", how="semi")
        .group_by("release_group_id", "genre_id")
        .agg(pl.col("weight").sum())
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "12_release_group_genre.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
