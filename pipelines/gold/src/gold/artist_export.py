import json
import logging
from pathlib import Path

import polars as pl
from common.quality_checks import (
    ARTIST_NAME_LEN_MAX,
    MBID_PATTERN,
    RELEASE_GROUP_TITLE_LEN_MAX,
    check_max_length,
    check_non_empty,
    check_null_rate,
    check_unique_key,
)
from jsonschema import Draft202012Validator

from gold.delta_export import write_delta
from gold.ndjson_parts import write_ndjson_parts

logger = logging.getLogger(__name__)

# Rows are ~800 bytes of NDJSON (nested members, links, release groups) and grow's API workers are memory-capped,
# so 10k rows keeps each upload around 8 MB.
ROWS_PER_PART = 10_000
OUTPUT_DIRNAME = "3_artists"
ARTIST_SCHEMA_PATH = Path(__file__).parent / "schemas" / "artist.schema.json"
# Rows checked against ARTIST_SCHEMA_PATH: every row shares one Polars schema, so a sample catches shape drift;
# values are checked in Polars over every row.
SCHEMA_SAMPLE_SIZE = 1_000
_ARTIST_TYPES = ["person", "group", "orchestra", "choir", "character", "other"]
# MusicBrainz's special-purpose artists (https://musicbrainz.org/doc/Style/Unknown_and_untitled/Special_purpose_artist):
# placeholders credited on huge numbers of releases, not artists with a page of their own.
SPECIAL_PURPOSE_ARTIST_IDS = [
    "89ad4ac3-39f7-470e-963a-56509c546377",  # Various Artists
    "125ec42a-7229-4250-afc5-e057484327fe",  # [unknown]
    "f731ccc4-e22a-43af-a747-64213329e088",  # [anonymous]
    "9be7f096-97ec-4615-8957-8d40b5dcbc41",  # [traditional]
    "eec63d3c-3b81-4ad4-b1e4-7c147d4d2b61",  # [no artist]
    "33cf029c-63b0-41a0-9855-be2a3665fb3b",  # [data]
    "314e1c25-dde7-4e4d-b2f4-0a7b9f7c56dc",  # [dialogue]
    "66ea0139-149f-4a0c-8fbf-5ea9ec4a6e49",  # [Disney]
    "c06aa285-520e-40c0-b776-83d2c9e8a6d1",  # Deleted Artist
]


def _scope(song_artist_ids: pl.LazyFrame, members: pl.LazyFrame) -> pl.LazyFrame:
    """Song artists, the groups they're members of, then every member of those artists and groups, so a band's
    whole line-up gets a page. One hop only: a member's other bands are in scope only if they're song artists or
    have a song artist as a member. Special-purpose artists are excluded."""
    groups = members.join(
        song_artist_ids, left_on="member_musicbrainz_artist_id", right_on="musicbrainz_artist_id", how="semi"
    ).select(musicbrainz_artist_id="group_musicbrainz_artist_id")
    with_groups = pl.concat([song_artist_ids, groups]).unique()
    their_members = members.join(
        with_groups, left_on="group_musicbrainz_artist_id", right_on="musicbrainz_artist_id", how="semi"
    ).select(musicbrainz_artist_id="member_musicbrainz_artist_id")
    return (
        pl.concat([with_groups, their_members])
        .unique()
        .filter(~pl.col("musicbrainz_artist_id").is_in(SPECIAL_PURPOSE_ARTIST_IDS))
    )


def _check_artists(artists: pl.DataFrame) -> None:
    check_non_empty(artists, OUTPUT_DIRNAME)
    for column in ("musicbrainz_artist_id", "name"):
        check_null_rate(artists, column, OUTPUT_DIRNAME)
    check_unique_key(artists, "musicbrainz_artist_id", OUTPUT_DIRNAME)
    blank_name = artists.filter(pl.col("name").str.strip_chars() == "").height
    if blank_name:
        raise ValueError(f"{OUTPUT_DIRNAME}: {blank_name} row(s) with a blank name")
    check_max_length(artists, "name", ARTIST_NAME_LEN_MAX, OUTPUT_DIRNAME)
    check_max_length(
        artists.select(alias=pl.col("aliases").explode(empty_as_null=False)).drop_nulls(),
        "alias",
        ARTIST_NAME_LEN_MAX,
        OUTPUT_DIRNAME,
    )
    release_group_titles = artists.select(
        release_group_title=pl.col("release_groups")
        .list.eval(pl.element().struct.field("title"))
        .explode(empty_as_null=False)
    )
    check_max_length(release_group_titles, "release_group_title", RELEASE_GROUP_TITLE_LEN_MAX, OUTPUT_DIRNAME)
    release_group_ids = artists.select(
        pl.col("release_groups")
        .list.eval(pl.element().struct.field("musicbrainz_release_group_id"))
        .explode(empty_as_null=False)
    )
    malformed = {
        "musicbrainz_artist_id": artists.filter(~pl.col("musicbrainz_artist_id").str.contains(MBID_PATTERN)).height,
        "type": artists.filter(~pl.col("type").is_in(_ARTIST_TYPES)).height,
        "country": artists.filter(~pl.col("country").str.contains(r"^[A-Z]{2}$")).height,
        "musicbrainz_release_group_id": release_group_ids.filter(
            ~pl.col("release_groups").str.contains(MBID_PATTERN)
        ).height,
    }
    for column, count in malformed.items():
        if count:
            raise ValueError(f"{OUTPUT_DIRNAME}: {count} row(s) with a malformed {column}")
    images = artists.select(pl.col("image").struct.unnest()).drop_nulls("url")
    bad_images = {
        "image url": images.filter(~pl.col("url").str.starts_with("https://")).height,
        "image page_url": images.filter(~pl.col("page_url").str.starts_with("https://")).height,
        "image license": images.filter(pl.col("license").str.strip_chars().fill_null("") == "").height,
    }
    for field, count in bad_images.items():
        if count:
            raise ValueError(f"{OUTPUT_DIRNAME}: {count} row(s) with a malformed {field}")


def export_artists(
    genre_match_path: Path, musicbrainz_silver_dir: Path, wikidata_silver_dir: Path, output_dir: Path
) -> Path:
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
                ended="ended",
                roles="roles",
            ).sort_by(
                "member_musicbrainz_artist_id",
                "begin_year",
                "end_year",
                "ended",
                pl.col("roles").list.join(","),
                nulls_last=True,
            )
        )
    )
    links = (
        pl.scan_parquet(musicbrainz_silver_dir / "8_artist_links.parquet")
        .join(scope, on="musicbrainz_artist_id", how="semi")
        .group_by("musicbrainz_artist_id")
        .agg(links=pl.struct("kind", "url").sort_by("kind", "url"))
    )
    release_groups = (
        pl.scan_parquet(musicbrainz_silver_dir / "9_artist_release_groups.parquet")
        .join(scope, on="musicbrainz_artist_id", how="semi")
        .group_by("musicbrainz_artist_id")
        .agg(
            release_groups=pl.struct(
                "musicbrainz_release_group_id", "title", "primary_type", "secondary_types", "first_release_year"
            ).sort_by("first_release_year", "musicbrainz_release_group_id", nulls_last=True)
        )
    )
    silver_artists = pl.scan_parquet(musicbrainz_silver_dir / "6_artists.parquet").join(
        scope, on="musicbrainz_artist_id", how="semi"
    )
    aliases = (
        pl.concat(
            [
                pl.scan_parquet(musicbrainz_silver_dir / "11_artist_aliases.parquet").join(
                    scope, on="musicbrainz_artist_id", how="semi"
                ),
                silver_artists.select("musicbrainz_artist_id", alias="sort_name"),
            ]
        )
        .join(silver_artists.select("musicbrainz_artist_id", "name"), on="musicbrainz_artist_id")
        .filter(
            pl.col("alias").str.strip_chars().str.to_lowercase() != pl.col("name").str.strip_chars().str.to_lowercase()
        )
        .group_by("musicbrainz_artist_id")
        .agg(aliases=pl.col("alias").unique().sort())
    )
    images = pl.scan_parquet(wikidata_silver_dir / "11_artist_images.parquet").select(
        "musicbrainz_artist_id",
        image=pl.struct(
            url="thumb_url", page_url="page_url", author="author", license="license", license_url="license_url"
        ),
    )
    artists = (
        silver_artists.drop("sort_name")
        .join(aliases, on="musicbrainz_artist_id", how="left")
        .join(nested_members, on="musicbrainz_artist_id", how="left")
        .join(links, on="musicbrainz_artist_id", how="left")
        .join(release_groups, on="musicbrainz_artist_id", how="left")
        .join(images, on="musicbrainz_artist_id", how="left")
        .sort("musicbrainz_artist_id")
        .collect()
    )
    artists = artists.with_columns(
        pl.col(column).fill_null(pl.lit([], dtype=artists.schema[column]))
        for column in ("aliases", "members", "links", "release_groups")
    )
    _check_artists(artists)
    validator = Draft202012Validator(json.loads(ARTIST_SCHEMA_PATH.read_text()))
    for row in artists.head(SCHEMA_SAMPLE_SIZE).iter_rows(named=True):
        error = next(validator.iter_errors(row), None)
        if error is not None:
            raise ValueError(
                f"{OUTPUT_DIRNAME}: artist {row['musicbrainz_artist_id']} failed schema validation "
                f"({ARTIST_SCHEMA_PATH}): {error.message}"
            )

    parts_dir = output_dir / OUTPUT_DIRNAME
    part_count = write_ndjson_parts(artists, parts_dir, ROWS_PER_PART)
    write_delta(
        artists,
        "musicbrainz_artist_id",
        OUTPUT_DIRNAME,
        output_dir,
        ROWS_PER_PART,
        [column for column in artists.columns if column != "musicbrainz_artist_id"],
    )
    logger.info(
        "wrote %d artists to %s in %d part(s), %.1f%% with an image",
        artists.height,
        parts_dir,
        part_count,
        100 * artists["image"].is_not_null().mean(),
    )
    return parts_dir
