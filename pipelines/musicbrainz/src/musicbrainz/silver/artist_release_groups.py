import logging
from pathlib import Path

import polars as pl
from common.quality_checks import FIRST_RELEASE_YEAR_RANGE

logger = logging.getLogger(__name__)


def artist_release_groups(bronze_dir: Path, output_dir: Path) -> Path:
    primary_type = pl.scan_parquet(bronze_dir / "release_group_primary_type.parquet").select(
        type="id", primary_type=pl.col("name").str.to_lowercase()
    )
    secondary_types = (
        pl.scan_parquet(bronze_dir / "release_group_secondary_type_join.parquet")
        .join(
            pl.scan_parquet(bronze_dir / "release_group_secondary_type.parquet").select(
                secondary_type="id", secondary_type_name=pl.col("name").str.to_lowercase()
            ),
            on="secondary_type",
        )
        .group_by("release_group")
        .agg(secondary_types=pl.col("secondary_type_name").unique().sort())
    )
    first_release_year = pl.scan_parquet(bronze_dir / "release_group_meta.parquet").select(
        release_group="id",
        first_release_year=pl.when(pl.col("first_release_date_year").is_between(*FIRST_RELEASE_YEAR_RANGE)).then(
            "first_release_date_year"
        ),
    )
    # Every credited artist, not just position 0: a collaboration belongs to each of its artists' discographies.
    credited_artist = (
        pl.scan_parquet(bronze_dir / "artist_credit_name.parquet")
        .join(
            pl.scan_parquet(bronze_dir / "artist.parquet").select(artist="id", musicbrainz_artist_id="gid"), on="artist"
        )
        .select("artist_credit", "musicbrainz_artist_id")
        .unique()
    )
    result = (
        pl.scan_parquet(bronze_dir / "release_group.parquet")
        .select("type", "artist_credit", release_group="id", musicbrainz_release_group_id="gid", title="name")
        .join(credited_artist, on="artist_credit")
        .join(primary_type, on="type", how="left")
        .join(secondary_types, on="release_group", how="left")
        .join(first_release_year, on="release_group", how="left")
        .select(
            "musicbrainz_artist_id",
            "musicbrainz_release_group_id",
            "title",
            "primary_type",
            secondary_types=pl.col("secondary_types").fill_null([]),
            first_release_year="first_release_year",
        )
        .sort("musicbrainz_artist_id", "first_release_year", "musicbrainz_release_group_id", nulls_last=True)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "9_artist_release_groups.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
