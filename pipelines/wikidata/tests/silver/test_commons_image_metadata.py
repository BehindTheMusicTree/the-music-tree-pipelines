from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from urllib.parse import parse_qs

import httpx
import polars as pl
import pytest

from wikidata.silver import commons_image_metadata as cim

NOW = datetime(2026, 1, 10, tzinfo=UTC)


def _write_images(bronze_dir: Path, image_files: list[str]) -> None:
    bronze_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "item_id": [f"Q{i}" for i in range(len(image_files))],
            "musicbrainz_artist_id": [f"00000000-0000-0000-0000-{i:012d}" for i in range(len(image_files))],
            "image_file": image_files,
        }
    ).write_parquet(bronze_dir / "wikidata_artist_images.parquet")


def _page(title: str, artist: str = '<a href="//commons.wikimedia.org/wiki/User:Jane">Jane  Doe</a>') -> dict:
    name = title.removeprefix("File:").replace(" ", "_")
    return {
        "title": title,
        "imageinfo": [
            {
                "thumburl": f"https://thumb.wikimedia.org/thumb/{name}/500px-{name}?utm_source=commons.wikimedia.org",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                "extmetadata": {
                    "Artist": {"value": artist},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                    "AttributionRequired": {"value": "true"},
                },
            }
        ],
    }


def _form(request: httpx.Request) -> dict[str, str]:
    assert request.method == "POST"
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def _client(responses: list[dict[str, Any]], requests: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses[len(requests) - 1])

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ('<a href="x">Jane</a>\n <span>Doe</span>', "Jane Doe"),
        ("Own work", "Own work"),
    ],
)
def test_html_to_text(value: str | None, expected: str | None) -> None:
    assert cim.html_to_text(value) == expected


def test_metadata_row_keeps_only_the_file_name_for_a_missing_file() -> None:
    assert cim.metadata_row("Gone.jpg", None) == {"image_file": "Gone.jpg"}
    assert cim.metadata_row("Gone.jpg", {"title": "File:Gone.jpg", "missing": True}) == {"image_file": "Gone.jpg"}


def test_metadata_row_falls_back_to_credit_and_reads_public_domain_flags() -> None:
    page = _page("File:A.jpg", artist="")
    page["imageinfo"][0]["extmetadata"] = {
        "Credit": {"value": "Own work"},
        "LicenseShortName": {"value": "Public domain"},
        "AttributionRequired": {"value": "false"},
        "NonFree": {"value": "false"},
    }

    row = cim.metadata_row("A.jpg", page)

    assert (row["author"], row["license"], row["license_url"]) == ("Own work", "Public domain", None)
    assert (row["attribution_required"], row["non_free"]) == (False, False)


def test_fetch_batch_follows_normalization_redirects_and_continue() -> None:
    requests: list[httpx.Request] = []
    responses = [
        {
            "continue": {"iicontinue": "x", "continue": "||"},
            "query": {
                "normalized": [{"from": "File:a_b.jpg", "to": "File:A b.jpg"}],
                "redirects": [{"from": "File:A b.jpg", "to": "File:Target.jpg"}],
                "pages": [{"title": "File:Target.jpg"}, {"title": "File:Gone.jpg", "missing": True}],
            },
        },
        {
            "continue": {"iistart": "2020-01-01T00:00:00Z", "continue": "||"},
            "query": {"pages": [_page("File:Target.jpg"), _page("File:Target.jpg", artist="Older revision")]},
        },
    ]

    rows = cim._fetch_batch(_client(responses, requests), ["a_b.jpg", "Gone.jpg"])

    assert _form(requests[0])["titles"] == "File:a_b.jpg|File:Gone.jpg"
    assert _form(requests[0])["iiurlwidth"] == "500"
    assert len(requests) == 2
    assert _form(requests[1])["iicontinue"] == "x"
    assert rows[0]["image_file"] == "a_b.jpg"
    assert rows[0]["author"] == "Jane Doe"
    assert rows[0]["thumb_url"] == "https://thumb.wikimedia.org/thumb/Target.jpg/500px-Target.jpg"
    assert rows[1] == {"image_file": "Gone.jpg"}


def test_get_page_raises_on_an_api_error() -> None:
    requests: list[httpx.Request] = []
    with pytest.raises(RuntimeError, match="Commons imageinfo query failed"):
        cim._get_page(_client([{"error": {"code": "badvalue"}}], requests), {"titles": "File:A.jpg"})


def _cached_row(image_file: str, checked_at: datetime) -> dict:
    return {**cim.metadata_row(image_file, _page(f"File:{image_file}")), "checked_at": checked_at}


def test_fetches_unchecked_then_stalest_files_within_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cim, "BATCH_SIZE", 2)
    _write_images(tmp_path / "bronze", ["fresh.jpg", "stale.jpg", "staler.jpg", "new.jpg"])
    silver_dir = tmp_path / "silver"
    silver_dir.mkdir()
    stale = NOW - timedelta(days=cim.COMMONS_IMAGE_TTL_DAYS + 1)
    pl.DataFrame(
        [
            _cached_row("fresh.jpg", NOW - timedelta(days=1)),
            _cached_row("stale.jpg", stale),
            _cached_row("staler.jpg", stale - timedelta(days=1)),
            _cached_row("dropped.jpg", NOW),
        ],
        schema=cim._SCHEMA,
    ).write_parquet(silver_dir / cim.OUTPUT_FILENAME)
    requests: list[httpx.Request] = []
    client = _client([{"query": {"pages": [_page("File:new.jpg"), _page("File:staler.jpg")]}}], requests)

    result = pl.read_parquet(cim.commons_image_metadata(tmp_path / "bronze", silver_dir, client, NOW, max_batches=1))

    assert [_form(r)["titles"] for r in requests] == ["File:new.jpg|File:staler.jpg"]
    checked = dict(zip(result["image_file"], result["checked_at"], strict=True))
    assert sorted(checked) == ["fresh.jpg", "new.jpg", "stale.jpg", "staler.jpg"]
    assert checked["new.jpg"] == checked["staler.jpg"] == NOW
    assert checked["stale.jpg"] == stale


def test_writes_fetched_batches_when_a_later_batch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cim, "BATCH_SIZE", 1)
    _write_images(tmp_path / "bronze", ["a.jpg", "b.jpg"])
    requests: list[httpx.Request] = []
    client = _client([{"query": {"pages": [_page("File:a.jpg")]}}, {"error": {"code": "internal"}}], requests)

    with pytest.raises(RuntimeError):
        cim.commons_image_metadata(tmp_path / "bronze", tmp_path / "silver", client, NOW, max_batches=2)

    assert pl.read_parquet(tmp_path / "silver" / cim.OUTPUT_FILENAME)["image_file"].to_list() == ["a.jpg"]


def test_metadata_row_treats_an_empty_list_extmetadata_as_none() -> None:
    page = {
        "title": "File:a.jpg",
        "imageinfo": [{"thumburl": "https://t/a.jpg", "descriptionurl": "https://p/a", "extmetadata": []}],
    }

    row = cim.metadata_row("a.jpg", page)

    assert row["license"] is None
    assert row["author"] is None
    assert row["non_free"] is False
