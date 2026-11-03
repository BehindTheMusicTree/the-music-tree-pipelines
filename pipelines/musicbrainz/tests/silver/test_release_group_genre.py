from pathlib import Path

import polars as pl
from common.musicbrainz_artists import SPECIAL_PURPOSE_ARTIST_IDS

from musicbrainz.silver.release_group_genre import release_group_genre

VARIOUS_ARTISTS = SPECIAL_PURPOSE_ARTIST_IDS[0]


def _write_bronze(tmp_path: Path) -> Path:
    bronze_dir = tmp_path / "bronze"
    bronze_dir.mkdir()
    pl.DataFrame([{"id": 1, "name": "Rock"}, {"id": 2, "name": "Jazz"}]).write_parquet(bronze_dir / "tag.parquet")
    pl.DataFrame([{"id": 100, "name": "rock"}, {"id": 101, "name": "jazz"}]).write_parquet(bronze_dir / "genre.parquet")
    pl.DataFrame([{"id": 1, "gid": "a1"}, {"id": 2, "gid": VARIOUS_ARTISTS}]).write_parquet(
        bronze_dir / "artist.parquet"
    )
    pl.DataFrame([{"artist_credit": 10, "artist": 1}, {"artist_credit": 20, "artist": 2}]).write_parquet(
        bronze_dir / "artist_credit_name.parquet"
    )
    # 50 album, 51 untyped, 52 compilation album, 53 broadcast, 54 Various Artists album.
    pl.DataFrame(
        [
            {"id": 50, "type": 1, "artist_credit": 10},
            {"id": 51, "type": None, "artist_credit": 10},
            {"id": 52, "type": 1, "artist_credit": 10},
            {"id": 53, "type": 12, "artist_credit": 10},
            {"id": 54, "type": 1, "artist_credit": 20},
        ],
        schema_overrides={"type": pl.Int64},
    ).write_parquet(bronze_dir / "release_group.parquet")
    pl.DataFrame([{"id": 1, "name": "Album"}, {"id": 12, "name": "Broadcast"}]).write_parquet(
        bronze_dir / "release_group_primary_type.parquet"
    )
    pl.DataFrame([{"id": 1, "name": "Compilation"}]).write_parquet(bronze_dir / "release_group_secondary_type.parquet")
    pl.DataFrame([{"release_group": 52, "secondary_type": 1}]).write_parquet(
        bronze_dir / "release_group_secondary_type_join.parquet"
    )
    pl.DataFrame(
        [{"release_group": rg, "tag": 1, "count": 2} for rg in (50, 51, 52, 53, 54)]
        + [{"release_group": 50, "tag": 2, "count": 0}]
    ).write_parquet(bronze_dir / "release_group_tag.parquet")
    pl.DataFrame([{"id": 500, "release_group": 50}, {"id": 501, "release_group": 50}]).write_parquet(
        bronze_dir / "release.parquet"
    )
    pl.DataFrame([{"release": 500, "tag": 1, "count": 3}, {"release": 501, "tag": 2, "count": 1}]).write_parquet(
        bronze_dir / "release_tag.parquet"
    )
    return bronze_dir


def test_release_group_genre_folds_release_tags_into_eligible_release_groups(tmp_path: Path) -> None:
    result = release_group_genre(_write_bronze(tmp_path), tmp_path / "silver")

    assert result == tmp_path / "silver" / "12_release_group_genre.parquet"
    assert pl.read_parquet(result).sort("release_group_id", "genre_id").to_dicts() == [
        {"release_group_id": 50, "genre_id": 100, "weight": 5},
        {"release_group_id": 50, "genre_id": 101, "weight": 1},
        {"release_group_id": 51, "genre_id": 100, "weight": 2},
    ]
