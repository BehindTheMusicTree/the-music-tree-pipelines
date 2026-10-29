from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from wikidata.silver import artist_images as ai
from wikidata.silver import commons_image_metadata as cim

ARTIST_A = "00000000-0000-0000-0000-000000000001"
ARTIST_B = "00000000-0000-0000-0000-000000000002"
BAD_ARTIST = "ffffffff-0000-0000-0000-000000000000"


def _metadata(image_file: str, **overrides: object) -> dict:
    return {
        "image_file": image_file,
        "thumb_url": f"https://upload.wikimedia.org/{image_file}",
        "page_url": f"https://commons.wikimedia.org/wiki/File:{image_file}",
        "author": "Jane Doe",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0",
        "attribution_required": True,
        "non_free": False,
        "checked_at": datetime(2026, 1, 1, tzinfo=UTC),
        **overrides,
    }


def _write(tmp_path: Path, images: list[tuple[str, str]], metadata: list[dict]) -> tuple[Path, Path]:
    bronze_dir, silver_dir = tmp_path / "bronze", tmp_path / "silver"
    bronze_dir.mkdir()
    silver_dir.mkdir()
    pl.DataFrame(
        [{"item_id": "Q1", "musicbrainz_artist_id": mbid, "image_file": file} for mbid, file in images],
        schema={"item_id": pl.Utf8, "musicbrainz_artist_id": pl.Utf8, "image_file": pl.Utf8},
    ).write_parquet(bronze_dir / "wikidata_artist_images.parquet")
    pl.DataFrame(metadata, schema=cim._SCHEMA).write_parquet(silver_dir / cim.OUTPUT_FILENAME)
    return bronze_dir, silver_dir


def test_keeps_the_smallest_usable_file_per_artist(tmp_path: Path) -> None:
    images = [
        (ARTIST_A, "b.jpg"),
        (ARTIST_A, "a.jpg"),
        (ARTIST_B, "nonfree.jpg"),
        (ARTIST_B, "unlicensed.jpg"),
        (ARTIST_B, "anonymous.jpg"),
        (ARTIST_B, "missing.jpg"),
        (ARTIST_B, "unchecked.jpg"),
        (ARTIST_B, "pd.jpg"),
    ]
    metadata = [
        _metadata("a.jpg"),
        _metadata("b.jpg"),
        _metadata("nonfree.jpg", non_free=True),
        _metadata("unlicensed.jpg", license=None),
        _metadata("anonymous.jpg", author=None),
        {"image_file": "missing.jpg", "checked_at": datetime(2026, 1, 1, tzinfo=UTC)},
        _metadata("pd.jpg", author=None, attribution_required=False, license="Public domain", license_url=None),
    ]

    result = pl.read_parquet(ai.artist_images(*_write(tmp_path, images, metadata)))

    assert result.to_dicts() == [
        {
            "musicbrainz_artist_id": ARTIST_A,
            "thumb_url": "https://upload.wikimedia.org/a.jpg",
            "page_url": "https://commons.wikimedia.org/wiki/File:a.jpg",
            "author": "Jane Doe",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0",
        },
        {
            "musicbrainz_artist_id": ARTIST_B,
            "thumb_url": "https://upload.wikimedia.org/pd.jpg",
            "page_url": "https://commons.wikimedia.org/wiki/File:pd.jpg",
            "author": None,
            "license": "Public domain",
            "license_url": None,
        },
    ]


def _many_artists(count: int) -> tuple[list[tuple[str, str]], list[dict]]:
    images = [(f"00000000-0000-0000-0000-{i:012d}", f"f{i}.jpg") for i in range(count)]
    return images, [_metadata(file) for _, file in images]


@pytest.mark.parametrize(
    "override",
    [
        {"thumb_url": "https://example.org/a.jpg"},
        {"page_url": "http://commons.wikimedia.org/wiki/File:a.jpg"},
        {"license_url": "ftp://example.org/licence"},
        {"thumb_url": "https://upload.wikimedia.org/" + "é" * 1025},
    ],
)
def test_drops_a_row_that_breaks_the_grow_contract_within_the_budget(tmp_path: Path, override: dict) -> None:
    images, metadata = _many_artists(99)
    inputs = _write(tmp_path, [*images, (BAD_ARTIST, "a.jpg")], [*metadata, _metadata("a.jpg", **override)])

    result = pl.read_parquet(ai.artist_images(*inputs))

    assert result.height == 99
    assert BAD_ARTIST not in result["musicbrainz_artist_id"].to_list()


def test_raises_when_too_many_rows_break_the_grow_contract(tmp_path: Path) -> None:
    inputs = _write(tmp_path, [(ARTIST_A, "a.jpg")], [_metadata("a.jpg", thumb_url="https://example.org/a.jpg")])

    with pytest.raises(ValueError, match="rejected 1 of 1"):
        ai.artist_images(*inputs)
