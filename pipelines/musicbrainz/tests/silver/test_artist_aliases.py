from pathlib import Path

import polars as pl

from musicbrainz.silver.artist_aliases import artist_aliases


def test_artist_aliases_unions_names_and_sort_names_per_artist(tmp_path: Path) -> None:
    pl.DataFrame([{"id": 1, "gid": "artist"}, {"id": 2, "gid": "other"}]).write_parquet(tmp_path / "artist.parquet")
    pl.DataFrame(
        [
            {"artist": 1, "name": "Bjork", "sort_name": "Bjork"},
            {"artist": 1, "name": "Bjork", "sort_name": "Gudmundsdottir, Bjork"},
            {"artist": 1, "name": "  ", "sort_name": None},
            {"artist": 2, "name": "Beatles", "sort_name": "Beatles, The"},
        ]
    ).write_parquet(tmp_path / "artist_alias.parquet")

    result = artist_aliases(tmp_path, tmp_path / "silver")

    assert result == tmp_path / "silver" / "11_artist_aliases.parquet"
    assert pl.read_parquet(result).rows() == [
        ("artist", "Bjork"),
        ("artist", "Gudmundsdottir, Bjork"),
        ("other", "Beatles"),
        ("other", "Beatles, The"),
    ]
