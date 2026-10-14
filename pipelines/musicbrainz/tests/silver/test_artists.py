from pathlib import Path

import polars as pl

from musicbrainz.silver.artists import artists


def test_artists_resolves_type_and_country(tmp_path: Path) -> None:
    pl.DataFrame(
        [
            {
                "id": 1,
                "gid": "gid-1",
                "name": "Band",
                "type": 2,
                "area": 10,
                "begin_date_year": 1990,
                "end_date_year": 2001,
                "ended": True,
            },
            # A city area has no ISO code; an unknown type stays null.
            {
                "id": 2,
                "gid": "gid-2",
                "name": "Solo",
                "type": None,
                "area": 11,
                "begin_date_year": None,
                "end_date_year": None,
                "ended": False,
            },
            # A region code (Europe) isn't a country.
            {
                "id": 3,
                "gid": "gid-3",
                "name": "Euro",
                "type": 1,
                "area": 12,
                "begin_date_year": None,
                "end_date_year": None,
                "ended": False,
            },
            # Kosovo's user-assigned code is allowlisted.
            {
                "id": 4,
                "gid": "gid-4",
                "name": "Kosovar",
                "type": 1,
                "area": 13,
                "begin_date_year": None,
                "end_date_year": None,
                "ended": False,
            },
        ],
        schema_overrides={"type": pl.Int64, "begin_date_year": pl.Int64, "end_date_year": pl.Int64},
    ).write_parquet(tmp_path / "artist.parquet")
    pl.DataFrame([{"id": 1, "name": "Person"}, {"id": 2, "name": "Group"}]).write_parquet(
        tmp_path / "artist_type.parquet"
    )
    pl.DataFrame([{"area": 10, "code": "GB"}, {"area": 12, "code": "XE"}, {"area": 13, "code": "XK"}]).write_parquet(
        tmp_path / "iso_3166_1.parquet"
    )

    result = artists(tmp_path, tmp_path / "silver")

    assert result == tmp_path / "silver" / "6_artists.parquet"
    assert pl.read_parquet(result).sort("musicbrainz_artist_id").to_dicts() == [
        {
            "musicbrainz_artist_id": "gid-1",
            "name": "Band",
            "type": "group",
            "country": "GB",
            "begin_year": 1990,
            "end_year": 2001,
            "ended": True,
        },
        {
            "musicbrainz_artist_id": "gid-2",
            "name": "Solo",
            "type": None,
            "country": None,
            "begin_year": None,
            "end_year": None,
            "ended": False,
        },
        {
            "musicbrainz_artist_id": "gid-3",
            "name": "Euro",
            "type": "person",
            "country": None,
            "begin_year": None,
            "end_year": None,
            "ended": False,
        },
        {
            "musicbrainz_artist_id": "gid-4",
            "name": "Kosovar",
            "type": "person",
            "country": "XK",
            "begin_year": None,
            "end_year": None,
            "ended": False,
        },
    ]
