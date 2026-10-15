from pathlib import Path

import polars as pl

from musicbrainz.silver.artist_release_groups import artist_release_groups


def test_artist_release_groups_credits_every_artist_with_types_and_year(tmp_path: Path) -> None:
    pl.DataFrame([{"id": 1, "gid": "a1"}, {"id": 2, "gid": "a2"}]).write_parquet(tmp_path / "artist.parquet")
    pl.DataFrame(
        [
            {"artist_credit": 10, "position": 0, "artist": 1},
            {"artist_credit": 11, "position": 0, "artist": 1},
            {"artist_credit": 11, "position": 1, "artist": 2},
        ]
    ).write_parquet(tmp_path / "artist_credit_name.parquet")
    pl.DataFrame(
        [
            {"id": 100, "gid": "rg-live", "name": "Live", "artist_credit": 10, "type": 1},
            {"id": 101, "gid": "rg-duet", "name": "Duet", "artist_credit": 11, "type": None},
        ],
        schema_overrides={"type": pl.Int64},
    ).write_parquet(tmp_path / "release_group.parquet")
    pl.DataFrame([{"id": 1, "name": "Album"}]).write_parquet(tmp_path / "release_group_primary_type.parquet")
    pl.DataFrame([{"id": 1, "name": "Compilation"}, {"id": 6, "name": "Live"}]).write_parquet(
        tmp_path / "release_group_secondary_type.parquet"
    )
    pl.DataFrame(
        [{"release_group": 100, "secondary_type": 6}, {"release_group": 100, "secondary_type": 1}]
    ).write_parquet(tmp_path / "release_group_secondary_type_join.parquet")
    pl.DataFrame(
        [{"id": 100, "first_release_date_year": 1999}, {"id": 101, "first_release_date_year": None}],
        schema_overrides={"first_release_date_year": pl.Int64},
    ).write_parquet(tmp_path / "release_group_meta.parquet")

    result = artist_release_groups(tmp_path, tmp_path / "silver")

    assert result == tmp_path / "silver" / "9_artist_release_groups.parquet"
    assert pl.read_parquet(result).to_dicts() == [
        {
            "musicbrainz_artist_id": "a1",
            "musicbrainz_release_group_id": "rg-live",
            "title": "Live",
            "primary_type": "album",
            "secondary_types": ["compilation", "live"],
            "first_release_year": 1999,
        },
        {
            "musicbrainz_artist_id": "a1",
            "musicbrainz_release_group_id": "rg-duet",
            "title": "Duet",
            "primary_type": None,
            "secondary_types": [],
            "first_release_year": None,
        },
        {
            "musicbrainz_artist_id": "a2",
            "musicbrainz_release_group_id": "rg-duet",
            "title": "Duet",
            "primary_type": None,
            "secondary_types": [],
            "first_release_year": None,
        },
    ]


def test_artist_release_groups_nulls_out_of_range_years_and_keeps_rows(tmp_path: Path) -> None:
    pl.DataFrame([{"id": 1, "gid": "a1"}]).write_parquet(tmp_path / "artist.parquet")
    pl.DataFrame([{"artist_credit": 10, "position": 0, "artist": 1}]).write_parquet(
        tmp_path / "artist_credit_name.parquet"
    )
    pl.DataFrame(
        [
            {"id": 100, "gid": "rg-a", "name": "Ancient", "artist_credit": 10, "type": None},
            {"id": 101, "gid": "rg-b", "name": "Valid", "artist_credit": 10, "type": None},
            {"id": 102, "gid": "rg-c", "name": "Future", "artist_credit": 10, "type": None},
        ],
        schema_overrides={"type": pl.Int64},
    ).write_parquet(tmp_path / "release_group.parquet")
    pl.DataFrame(schema={"id": pl.Int64, "name": pl.String}).write_parquet(
        tmp_path / "release_group_primary_type.parquet"
    )
    pl.DataFrame(schema={"id": pl.Int64, "name": pl.String}).write_parquet(
        tmp_path / "release_group_secondary_type.parquet"
    )
    pl.DataFrame(schema={"release_group": pl.Int64, "secondary_type": pl.Int64}).write_parquet(
        tmp_path / "release_group_secondary_type_join.parquet"
    )
    pl.DataFrame(
        [
            {"id": 100, "first_release_date_year": 20},
            {"id": 101, "first_release_date_year": 2001},
            {"id": 102, "first_release_date_year": 3036},
        ]
    ).write_parquet(tmp_path / "release_group_meta.parquet")

    result = pl.read_parquet(artist_release_groups(tmp_path, tmp_path / "silver"))

    assert result.select("musicbrainz_release_group_id", "first_release_year").to_dicts() == [
        {"musicbrainz_release_group_id": "rg-b", "first_release_year": 2001},
        {"musicbrainz_release_group_id": "rg-a", "first_release_year": None},
        {"musicbrainz_release_group_id": "rg-c", "first_release_year": None},
    ]
