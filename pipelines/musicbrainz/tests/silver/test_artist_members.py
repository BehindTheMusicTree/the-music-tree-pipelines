from pathlib import Path

import polars as pl

from musicbrainz.silver.artist_members import artist_members


def test_artist_members_keeps_member_of_band_links_with_role_attributes(tmp_path: Path) -> None:
    pl.DataFrame([{"id": 1, "gid": "band"}, {"id": 2, "gid": "singer"}, {"id": 3, "gid": "drummer"}]).write_parquet(
        tmp_path / "artist.parquet"
    )
    pl.DataFrame(
        [
            {"id": 7, "name": "member of band", "entity_type0": "artist", "entity_type1": "artist"},
            {"id": 8, "name": "collaboration", "entity_type0": "artist", "entity_type1": "artist"},
        ]
    ).write_parquet(tmp_path / "link_type.parquet")
    pl.DataFrame(
        [
            {"id": 100, "link_type": 7, "begin_date_year": 1990, "end_date_year": None},
            {"id": 101, "link_type": 7, "begin_date_year": 1991, "end_date_year": 1995},
            {"id": 102, "link_type": 8, "begin_date_year": None, "end_date_year": None},
        ],
        schema_overrides={"begin_date_year": pl.Int64, "end_date_year": pl.Int64},
    ).write_parquet(tmp_path / "link.parquet")
    pl.DataFrame(
        [
            {"id": 1, "link": 100, "entity0": 2, "entity1": 1},
            {"id": 2, "link": 101, "entity0": 3, "entity1": 1},
            {"id": 3, "link": 102, "entity0": 3, "entity1": 2},
        ]
    ).write_parquet(tmp_path / "l_artist_artist.parquet")
    pl.DataFrame(
        [
            {"id": 3, "root": 3, "name": "vocal"},
            {"id": 4, "root": 3, "name": "lead vocals"},
            {"id": 14, "root": 14, "name": "instrument"},
            {"id": 15, "root": 14, "name": "guitar"},
            {"id": 16, "root": 14, "name": "drums"},
            {"id": 20, "root": 20, "name": "original"},
        ]
    ).write_parquet(tmp_path / "link_attribute_type.parquet")
    pl.DataFrame(
        [
            {"link": 100, "attribute_type": 15},
            {"link": 100, "attribute_type": 4},
            {"link": 100, "attribute_type": 20},  # not a role
        ]
    ).write_parquet(tmp_path / "link_attribute.parquet")

    result = artist_members(tmp_path, tmp_path / "silver")

    assert result == tmp_path / "silver" / "7_artist_members.parquet"
    assert pl.read_parquet(result).to_dicts() == [
        {
            "group_musicbrainz_artist_id": "band",
            "member_musicbrainz_artist_id": "drummer",
            "begin_year": 1991,
            "end_year": 1995,
            "roles": [],
        },
        {
            "group_musicbrainz_artist_id": "band",
            "member_musicbrainz_artist_id": "singer",
            "begin_year": 1990,
            "end_year": None,
            "roles": ["guitar", "lead vocals"],
        },
    ]
