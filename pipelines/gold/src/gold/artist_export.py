import json
import logging
import shutil
from pathlib import Path

import polars as pl
from common.quality_checks import check_non_empty, check_null_rate, check_unique_key
from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)

ROWS_PER_PART = 250_000
OUTPUT_DIRNAME = "3_artists"
ARTIST_SCHEMA_PATH = Path(__file__).parent / "schemas" / "artist.schema.json"


def _scope(song_artist_ids: pl.LazyFrame, members: pl.LazyFrame) -> pl.LazyFrame:
    """Song artists, the groups they belong to, then every member of all of those, so a band's whole line-up
    and a member's other bands get a page."""
    groups = members.join(
        song_artist_ids, left_on="member_musicbrainz_artist_id", right_on="musicbrainz_artist_id", how="semi"
    ).select(musicbrainz_artist_id="group_musicbrainz_artist_id")
    with_groups = pl.concat([song_artist_ids, groups]).unique()
    their_members = members.join(
        with_groups, left_on="group_musicbrainz_artist_id", right_on="musicbrainz_artist_id", how="semi"
    ).select(musicbrainz_artist_id="member_musicbrainz_artist_id")
    return pl.concat([with_groups, their_members]).unique()


def _check_artists(artists: pl.DataFrame) -> None:
    check_non_empty(artists, OUTPUT_DIRNAME)
    for column in ("musicbrainz_artist_id", "name"):
        check_null_rate(artists, column, OUTPUT_DIRNAME)
    check_unique_key(artists, "musicbrainz_artist_id", OUTPUT_DIRNAME)
    blank_name = artists.filter(pl.col("name").str.strip_chars() == "").height
    if blank_name:
        raise ValueError(f"{OUTPUT_DIRNAME}: {blank_name} row(s) with a blank name")


def export_artists(genre_match_path: Path, musicbrainz_silver_dir: Path, output_dir: Path) -> Path:
    logger.info("exporting artists of %s from %s", genre_match_path, musicbrainz_silver_dir)
    members = pl.scan_parquet(musicbrainz_silver_dir / "7_artist_members.parquet")
    scope = _scope(
        pl.scan_parquet(genre_match_path)
        .select("artist_musicbrainz_id")
        .rename({"artist_musicbrainz_id": "musicbrainz_artist_id"}),
        members,
    )

    # A member of a nested group (a group itself member of an in-scope group) can still fall outside the scope;
    # it's dropped so every member reference resolves to an exported artist.
    nested_members = (
        members.join(scope, left_on="group_musicbrainz_artist_id", right_on="musicbrainz_artist_id", how="semi")
        .join(scope, left_on="member_musicbrainz_artist_id", right_on="musicbrainz_artist_id", how="semi")
        .group_by(musicbrainz_artist_id="group_musicbrainz_artist_id")
        .agg(
            members=pl.struct(
                musicbrainz_artist_id="member_musicbrainz_artist_id",
                begin_year="begin_year",
                end_year="end_year",
                roles="roles",
            )
        )
    )
    links = (
        pl.scan_parquet(musicbrainz_silver_dir / "8_artist_links.parquet")
        .join(scope, on="musicbrainz_artist_id", how="semi")
        .group_by("musicbrainz_artist_id")
        .agg(links=pl.struct("kind", "url"))
    )
    release_groups = (
        pl.scan_parquet(musicbrainz_silver_dir / "9_artist_release_groups.parquet")
        .join(scope, on="musicbrainz_artist_id", how="semi")
        .group_by("musicbrainz_artist_id")
        .agg(
            release_groups=pl.struct(
                "musicbrainz_release_group_id", "title", "primary_type", "secondary_types", "first_release_year"
            )
        )
    )
    artists = (
        pl.scan_parquet(musicbrainz_silver_dir / "6_artists.parquet")
        .join(scope, on="musicbrainz_artist_id", how="semi")
        .join(nested_members, on="musicbrainz_artist_id", how="left")
        .join(links, on="musicbrainz_artist_id", how="left")
        .join(release_groups, on="musicbrainz_artist_id", how="left")
        .sort("musicbrainz_artist_id")
        .collect()
    )
    artists = artists.with_columns(
        pl.col(column).fill_null(pl.lit([], dtype=artists.schema[column]))
        for column in ("members", "links", "release_groups")
    )
    _check_artists(artists)

    validator = Draft202012Validator(json.loads(ARTIST_SCHEMA_PATH.read_text()))
    # Wiped first so a smaller run never leaves a previous run's trailing parts behind.
    parts_dir = output_dir / OUTPUT_DIRNAME
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True)
    for index, part in enumerate(artists.iter_slices(ROWS_PER_PART)):
        for row in part.iter_rows(named=True):
            error = next(validator.iter_errors(row), None)
            if error is not None:
                raise ValueError(
                    f"{OUTPUT_DIRNAME}: artist {row['musicbrainz_artist_id']} failed schema validation "
                    f"({ARTIST_SCHEMA_PATH}): {error.message}"
                )
        part.write_ndjson(parts_dir / f"part-{index:05d}.ndjson.gz", compression="gzip")
    logger.info("wrote %d artists to %s in %d part(s)", artists.height, parts_dir, index + 1)
    return parts_dir
