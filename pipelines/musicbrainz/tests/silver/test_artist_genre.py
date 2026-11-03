from pathlib import Path

import polars as pl
from common.musicbrainz_artists import SPECIAL_PURPOSE_ARTIST_IDS

from musicbrainz.silver.artist_genre import artist_genre


def test_artist_genre_names_each_artist_s_genre_tags_except_special_purpose_artists(tmp_path: Path) -> None:
    pl.DataFrame([{"id": 1, "name": "Jazz"}, {"id": 2, "name": "not-a-genre"}]).write_parquet(tmp_path / "tag.parquet")
    pl.DataFrame([{"id": 101, "name": "jazz"}]).write_parquet(tmp_path / "genre.parquet")
    pl.DataFrame([{"id": 1, "gid": "a1"}, {"id": 2, "gid": SPECIAL_PURPOSE_ARTIST_IDS[0]}]).write_parquet(
        tmp_path / "artist.parquet"
    )
    pl.DataFrame(
        [{"artist": 1, "tag": 1, "count": 3}, {"artist": 1, "tag": 2, "count": 5}, {"artist": 2, "tag": 1, "count": 9}]
    ).write_parquet(tmp_path / "artist_tag.parquet")

    result = artist_genre(tmp_path, tmp_path / "silver")

    assert result == tmp_path / "silver" / "13_artist_genre.parquet"
    assert pl.read_parquet(result).to_dicts() == [{"artist_musicbrainz_id": "a1", "genre_name": "jazz", "weight": 3}]
