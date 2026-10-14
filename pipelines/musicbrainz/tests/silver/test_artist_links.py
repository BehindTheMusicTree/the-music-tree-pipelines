from pathlib import Path

import polars as pl

from musicbrainz.silver.artist_links import artist_links


def test_artist_links_maps_curated_kinds_and_drops_the_rest(tmp_path: Path) -> None:
    pl.DataFrame([{"id": 1, "gid": "artist"}]).write_parquet(tmp_path / "artist.parquet")
    link_types = [
        "official homepage",
        "social network",
        "myspace",
        "free streaming",
        "free streaming",
        "allmusic",
        "official homepage",
    ]
    pl.DataFrame([{"id": i, "name": name} for i, name in enumerate(link_types)]).write_parquet(
        tmp_path / "link_type.parquet"
    )
    pl.DataFrame([{"id": 100 + i, "link_type": i, "ended": i == 6} for i in range(len(link_types))]).write_parquet(
        tmp_path / "link.parquet"
    )
    urls = [
        "https://band.example",
        "https://twitter.com/band",
        "https://myspace.com/band",
        "https://open.spotify.com/artist/abc",
        "https://deezer.com/artist/1",  # streaming, but not a curated platform
        "https://allmusic.com/artist/x",
        "https://lapsed.example",  # ended
    ]
    pl.DataFrame([{"id": 200 + i, "url": url} for i, url in enumerate(urls)]).write_parquet(tmp_path / "url.parquet")
    pl.DataFrame(
        [{"id": i, "link": 100 + i, "entity0": 1, "entity1": 200 + i} for i in range(len(urls))]
    ).write_parquet(tmp_path / "l_artist_url.parquet")

    result = artist_links(tmp_path, tmp_path / "silver")

    assert result == tmp_path / "silver" / "8_artist_links.parquet"
    assert pl.read_parquet(result).rows() == [
        ("artist", "official", "https://band.example"),
        ("artist", "social", "https://myspace.com/band"),
        ("artist", "social", "https://twitter.com/band"),
        ("artist", "spotify", "https://open.spotify.com/artist/abc"),
    ]
