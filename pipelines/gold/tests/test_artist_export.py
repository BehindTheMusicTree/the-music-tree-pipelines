import gzip
import json
from pathlib import Path

import polars as pl
import pytest

from gold import artist_export
from gold.artist_export import export_artists


def _mbid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


SINGER, BAND, DRUMMER, SIDE_PROJECT, SIDE_MEMBER, NESTED, STRANGER = (_mbid(i) for i in range(1, 8))


def _artist(mbid: str, name: str, type_: str | None = "person") -> dict:
    return {
        "musicbrainz_artist_id": mbid,
        "name": name,
        "sort_name": name,
        "disambiguation": None,
        "type": type_,
        "country": None,
        "begin_year": None,
        "end_year": None,
        "ended": False,
    }


ARTIST_ROWS = [
    {**_artist(SINGER, "Singer"), "country": "GB", "begin_year": 1970},
    {**_artist(BAND, "Band", "group"), "begin_year": 1990, "end_year": 2001, "ended": True},
    _artist(DRUMMER, "Drummer"),
    _artist(SIDE_PROJECT, "Side Project", "group"),
    _artist(SIDE_MEMBER, "Side Member"),
    _artist(NESTED, "Nested", "group"),
    _artist(STRANGER, "Stranger"),
]
MEMBER_ROWS = [
    {
        "group_musicbrainz_artist_id": BAND,
        "member_musicbrainz_artist_id": SINGER,
        "begin_year": 1990,
        "end_year": None,
        "ended": False,
        "roles": ["lead vocals"],
    },
    {
        "group_musicbrainz_artist_id": BAND,
        "member_musicbrainz_artist_id": DRUMMER,
        "begin_year": 1991,
        "end_year": 1995,
        "ended": True,
        "roles": [],
    },
    {
        "group_musicbrainz_artist_id": SIDE_PROJECT,
        "member_musicbrainz_artist_id": SIDE_MEMBER,
        "begin_year": None,
        "end_year": None,
        "ended": False,
        "roles": [],
    },
    # The drummer's own group isn't a song artist's group: reachable only two hops away, so out of scope.
    {
        "group_musicbrainz_artist_id": SIDE_PROJECT,
        "member_musicbrainz_artist_id": DRUMMER,
        "begin_year": None,
        "end_year": None,
        "ended": False,
        "roles": [],
    },
    # A nested group pulled in as the band's member brings no out-of-scope member reference along.
    {
        "group_musicbrainz_artist_id": BAND,
        "member_musicbrainz_artist_id": NESTED,
        "begin_year": None,
        "end_year": None,
        "ended": False,
        "roles": [],
    },
    {
        "group_musicbrainz_artist_id": NESTED,
        "member_musicbrainz_artist_id": STRANGER,
        "begin_year": None,
        "end_year": None,
        "ended": False,
        "roles": [],
    },
]
LINK_ROWS = [
    {"musicbrainz_artist_id": BAND, "kind": "official", "url": "https://band.example"},
    {"musicbrainz_artist_id": STRANGER, "kind": "official", "url": "https://stranger.example"},
]
ALIAS_ROWS = [
    {"musicbrainz_artist_id": BAND, "alias": "The Band"},
    {"musicbrainz_artist_id": BAND, "alias": "band"},
    {"musicbrainz_artist_id": STRANGER, "alias": "Out Of Scope"},
]
IMAGE_ROWS = [
    {
        "musicbrainz_artist_id": BAND,
        "thumb_url": "https://upload.wikimedia.org/band.jpg",
        "page_url": "https://commons.wikimedia.org/wiki/File:Band.jpg",
        "author": "Jane Doe",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "musicbrainz_artist_id": STRANGER,
        "thumb_url": "https://upload.wikimedia.org/stranger.jpg",
        "page_url": "https://commons.wikimedia.org/wiki/File:Stranger.jpg",
        "author": None,
        "license": "Public domain",
        "license_url": None,
    },
]
RELEASE_GROUP_ROWS = [
    {
        "musicbrainz_artist_id": BAND,
        "musicbrainz_release_group_id": _mbid(100),
        "title": "Debut",
        "primary_type": "album",
        "secondary_types": ["live"],
        "first_release_year": 1992,
    },
]


def _write_inputs(
    tmp_path: Path,
    artist_rows: list[dict] = ARTIST_ROWS,
    song_artist_ids: list[str] = [SINGER, SINGER],
    link_rows: list[dict] = LINK_ROWS,
    release_group_rows: list[dict] = RELEASE_GROUP_ROWS,
    alias_rows: list[dict] = ALIAS_ROWS,
    member_rows: list[dict] = MEMBER_ROWS,
    image_rows: list[dict] = IMAGE_ROWS,
) -> tuple[Path, Path, Path]:
    genre_match_path = tmp_path / "1_genre_match.parquet"
    pl.DataFrame({"artist_musicbrainz_id": song_artist_ids}).write_parquet(genre_match_path)
    silver_dir = tmp_path / "silver"
    silver_dir.mkdir()
    pl.DataFrame(artist_rows, schema_overrides={"begin_year": pl.Int64, "end_year": pl.Int64}).write_parquet(
        silver_dir / "6_artists.parquet"
    )
    pl.DataFrame(
        member_rows,
        schema_overrides={"begin_year": pl.Int64, "end_year": pl.Int64, "roles": pl.List(pl.Utf8)},
    ).write_parquet(silver_dir / "7_artist_members.parquet")
    pl.DataFrame(link_rows).write_parquet(silver_dir / "8_artist_links.parquet")
    pl.DataFrame(release_group_rows).write_parquet(silver_dir / "9_artist_release_groups.parquet")
    pl.DataFrame(alias_rows).write_parquet(silver_dir / "11_artist_aliases.parquet")
    wikidata_silver_dir = tmp_path / "wikidata_silver"
    wikidata_silver_dir.mkdir()
    pl.DataFrame(image_rows, schema={column: pl.Utf8 for column in IMAGE_ROWS[0]}).write_parquet(
        wikidata_silver_dir / "11_artist_images.parquet"
    )
    return genre_match_path, silver_dir, wikidata_silver_dir


def _read_parts(parts_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for part in sorted(parts_dir.iterdir())
        for line in gzip.decompress(part.read_bytes()).decode().splitlines()
    ]


def test_export_artists_scopes_song_artists_their_groups_and_members(tmp_path: Path) -> None:
    result = export_artists(*_write_inputs(tmp_path), tmp_path / "gold")

    assert result == tmp_path / "gold" / "3_artists"
    artists = {artist["musicbrainz_artist_id"]: artist for artist in _read_parts(result)}
    assert sorted(artists) == [SINGER, BAND, DRUMMER, NESTED]
    assert artists[BAND] == {
        "musicbrainz_artist_id": BAND,
        "name": "Band",
        "type": "group",
        "country": None,
        "begin_year": 1990,
        "end_year": 2001,
        "ended": True,
        "disambiguation": None,
        "aliases": ["The Band"],
        "members": [
            {
                "musicbrainz_artist_id": SINGER,
                "begin_year": 1990,
                "end_year": None,
                "ended": False,
                "roles": ["lead vocals"],
            },
            {"musicbrainz_artist_id": DRUMMER, "begin_year": 1991, "end_year": 1995, "ended": True, "roles": []},
            {"musicbrainz_artist_id": NESTED, "begin_year": None, "end_year": None, "ended": False, "roles": []},
        ],
        "links": [{"kind": "official", "url": "https://band.example"}],
        "release_groups": [
            {
                "musicbrainz_release_group_id": _mbid(100),
                "title": "Debut",
                "primary_type": "album",
                "secondary_types": ["live"],
                "first_release_year": 1992,
            }
        ],
        "image": {
            "url": "https://upload.wikimedia.org/band.jpg",
            "page_url": "https://commons.wikimedia.org/wiki/File:Band.jpg",
            "author": "Jane Doe",
            "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
        },
    }
    assert artists[SINGER]["image"] is None
    assert artists[SINGER]["country"] == "GB"
    assert artists[NESTED]["members"] == []
    assert artists[SINGER]["aliases"] == []
    assert (artists[SINGER]["members"], artists[SINGER]["links"], artists[SINGER]["release_groups"]) == ([], [], [])


def test_export_artists_orders_nested_lists_regardless_of_input_order(tmp_path: Path) -> None:
    link_rows = [
        *LINK_ROWS,
        {"musicbrainz_artist_id": BAND, "kind": "bandcamp", "url": "https://band.bandcamp.example"},
    ]
    release_group_rows = [
        *RELEASE_GROUP_ROWS,
        {
            **RELEASE_GROUP_ROWS[0],
            "musicbrainz_release_group_id": _mbid(101),
            "title": "Early",
            "first_release_year": 1991,
        },
    ]
    result = export_artists(
        *_write_inputs(
            tmp_path,
            link_rows=link_rows[::-1],
            release_group_rows=release_group_rows[::-1],
            member_rows=MEMBER_ROWS[::-1],
        ),
        tmp_path / "gold",
    )

    band = next(artist for artist in _read_parts(result) if artist["musicbrainz_artist_id"] == BAND)
    assert [member["musicbrainz_artist_id"] for member in band["members"]] == [SINGER, DRUMMER, NESTED]
    assert [link["kind"] for link in band["links"]] == ["bandcamp", "official"]
    assert [group["title"] for group in band["release_groups"]] == ["Early", "Debut"]


def test_export_artists_orders_members_tied_on_member_and_begin_year(tmp_path: Path) -> None:
    stints = [
        {**MEMBER_ROWS[1], "begin_year": 1991, "end_year": end_year, "ended": True, "roles": roles}
        for end_year, roles in [(1993, ["drums"]), (1995, ["backing vocals"]), (1995, ["drums"])]
    ]
    exported = []
    for name, stint_rows in (("forward", stints), ("reversed", stints[::-1])):
        run_dir = tmp_path / name
        run_dir.mkdir()
        result = export_artists(*_write_inputs(run_dir, member_rows=[MEMBER_ROWS[0], *stint_rows]), run_dir / "gold")
        band = next(artist for artist in _read_parts(result) if artist["musicbrainz_artist_id"] == BAND)
        exported.append([m for m in band["members"] if m["musicbrainz_artist_id"] == DRUMMER])

    assert exported[0] == exported[1]
    assert [(m["end_year"], m["roles"]) for m in exported[0]] == [
        (1993, ["drums"]),
        (1995, ["backing vocals"]),
        (1995, ["drums"]),
    ]


def test_export_artists_splits_parts_and_clears_stale_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artist_export, "ROWS_PER_PART", 3)
    parts_dir = tmp_path / "gold" / "3_artists"
    parts_dir.mkdir(parents=True)
    (parts_dir / "part-00009.ndjson.gz").write_bytes(b"")

    export_artists(*_write_inputs(tmp_path), tmp_path / "gold")

    assert sorted(p.name for p in parts_dir.iterdir()) == ["part-00000.ndjson.gz", "part-00001.ndjson.gz"]


@pytest.mark.parametrize(
    ("artist_rows", "match"),
    [
        ([*ARTIST_ROWS, ARTIST_ROWS[0]], "'musicbrainz_artist_id' is not unique"),
        ([{**ARTIST_ROWS[0], "name": " "}, *ARTIST_ROWS[1:]], "blank name"),
        ([{**ARTIST_ROWS[0], "name": None}, *ARTIST_ROWS[1:]], "'name' null rate"),
        ([{**ARTIST_ROWS[0], "type": "band"}, *ARTIST_ROWS[1:]], "malformed type"),
        ([{**ARTIST_ROWS[0], "country": "gb"}, *ARTIST_ROWS[1:]], "malformed country"),
        ([{**ARTIST_ROWS[0], "name": "x" * 2049}, *ARTIST_ROWS[1:]], "1 row\\(s\\) with 'name' longer than 2048"),
    ],
)
def test_export_artists_raises_on_bad_artist(tmp_path: Path, artist_rows: list[dict], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        export_artists(*_write_inputs(tmp_path, artist_rows), tmp_path / "gold")


def test_export_artists_excludes_special_purpose_artists(tmp_path: Path) -> None:
    various = artist_export.SPECIAL_PURPOSE_ARTIST_IDS[0]
    inputs = _write_inputs(tmp_path, [*ARTIST_ROWS, _artist(various, "Various Artists", "other")], [SINGER, various])

    artists = _read_parts(export_artists(*inputs, tmp_path / "gold"))

    assert various not in {artist["musicbrainz_artist_id"] for artist in artists}


def test_export_artists_raises_on_malformed_release_group_id(tmp_path: Path) -> None:
    rows = [{**RELEASE_GROUP_ROWS[0], "musicbrainz_release_group_id": "bad"}]
    with pytest.raises(ValueError, match="malformed musicbrainz_release_group_id"):
        export_artists(*_write_inputs(tmp_path, release_group_rows=rows), tmp_path / "gold")


def test_export_artists_raises_on_too_long_release_group_title(tmp_path: Path) -> None:
    rows = [{**RELEASE_GROUP_ROWS[0], "title": "x" * 2049}]
    with pytest.raises(ValueError, match="1 row\\(s\\) with 'release_group_title' longer than 2048"):
        export_artists(*_write_inputs(tmp_path, release_group_rows=rows), tmp_path / "gold")


def test_export_artists_validates_a_sample_against_the_schema(tmp_path: Path) -> None:
    rows = [{**LINK_ROWS[0], "url": ""}]
    with pytest.raises(ValueError, match="failed schema validation"):
        export_artists(*_write_inputs(tmp_path, link_rows=rows), tmp_path / "gold")


def test_export_artists_raises_on_malformed_artist_id(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path, [*ARTIST_ROWS, _artist("not-an-mbid", "Bad")], ["not-an-mbid"])
    with pytest.raises(ValueError, match="malformed musicbrainz_artist_id"):
        export_artists(*inputs, tmp_path / "gold")


def test_export_artists_adds_sort_name_and_disambiguation_but_not_the_display_name(tmp_path: Path) -> None:
    artist_rows = [
        {**row, "sort_name": "Band, The", "disambiguation": "UK band"} if row["musicbrainz_artist_id"] == BAND else row
        for row in ARTIST_ROWS
    ]

    result = export_artists(*_write_inputs(tmp_path, artist_rows=artist_rows), tmp_path / "gold")

    band = next(a for a in _read_parts(result) if a["musicbrainz_artist_id"] == BAND)
    assert (band["disambiguation"], band["aliases"]) == ("UK band", ["Band, The", "The Band"])


def test_export_artists_exports_an_unattributed_public_domain_image(tmp_path: Path) -> None:
    rows = [{**IMAGE_ROWS[1], "musicbrainz_artist_id": SINGER}]

    artists = _read_parts(export_artists(*_write_inputs(tmp_path, image_rows=rows), tmp_path / "gold"))

    singer = next(artist for artist in artists if artist["musicbrainz_artist_id"] == SINGER)
    assert singer["image"] == {
        "url": "https://upload.wikimedia.org/stranger.jpg",
        "page_url": "https://commons.wikimedia.org/wiki/File:Stranger.jpg",
        "author": None,
        "license": "Public domain",
        "license_url": None,
    }


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"thumb_url": "http://upload.wikimedia.org/band.jpg"}, "malformed image url"),
        ({"page_url": "commons.wikimedia.org/wiki/File:Band.jpg"}, "malformed image page_url"),
        ({"license": " "}, "malformed image license"),
        ({"license": None}, "malformed image license"),
        ({"author": " "}, "malformed image author"),
        ({"license_url": "creativecommons.org/licenses/by-sa/4.0"}, "malformed image license_url"),
        ({"license_url": "https://creativecommons.org/" + "é" * 1024}, "malformed image license_url"),
        ({"thumb_url": "https://upload.wikimedia.org/" + "a" * 2048}, "malformed image url"),
    ],
)
def test_export_artists_raises_on_bad_image(tmp_path: Path, override: dict, match: str) -> None:
    rows = [{**IMAGE_ROWS[0], **override}]
    with pytest.raises(ValueError, match=match):
        export_artists(*_write_inputs(tmp_path, image_rows=rows), tmp_path / "gold")
