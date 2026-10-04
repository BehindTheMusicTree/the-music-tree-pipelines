from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from musicbrainz.silver import youtube_video_status as yvs

NOW = datetime(2026, 1, 10, tzinfo=UTC)


def _item(video_id: str, **status: object) -> dict:
    base = {"embeddable": True, "privacyStatus": "public", "uploadStatus": "processed"}
    return {"id": video_id, "status": {**base, **status}, "contentDetails": {}}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (None, "not_found"),
        (_item("a", embeddable=False), "not_embeddable"),
        (_item("a", privacyStatus="private"), "private"),
        (_item("a", privacyStatus="unlisted"), None),
        (_item("a", uploadStatus="uploaded"), "not_processed"),
        ({**_item("a"), "contentDetails": {"regionRestriction": {"allowed": ["FR"]}}}, None),
        ({**_item("a"), "contentDetails": {"regionRestriction": {"blocked": ["DE"]}}}, None),
        (_item("a"), None),
    ],
)
def test_unplayable_reason(item: dict | None, expected: str | None) -> None:
    assert yvs.unplayable_reason(item) == expected


def _write_candidates(silver_dir: Path, video_ids: list[str]) -> None:
    silver_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"recording_id": list(range(len(video_ids))), "youtube_video_id": video_ids, "rank": [1] * len(video_ids)}
    ).write_parquet(silver_dir / "3_youtube_candidates.parquet")


def test_fetches_uncached_and_stale_ids_and_reuses_fresh_cache(tmp_path: Path) -> None:
    _write_candidates(tmp_path, ["fresh", "stale", "new", "gone"])
    pl.DataFrame(
        [
            {
                "youtube_video_id": "fresh",
                "youtube_unplayable_reason": "private",
                "checked_at": NOW - timedelta(days=1),
            },
            {"youtube_video_id": "stale", "youtube_unplayable_reason": None, "checked_at": NOW - timedelta(days=8)},
            {"youtube_video_id": "dropped", "youtube_unplayable_reason": None, "checked_at": NOW},
        ],
        schema=yvs._SCHEMA,
    ).write_parquet(tmp_path / yvs.OUTPUT_FILENAME)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-goog-api-key"] == "secret"
        assert "secret" not in str(request.url)
        requested.extend(request.url.params["id"].split(","))
        return httpx.Response(200, json={"items": [_item("new"), _item("stale", embeddable=False)]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        path = yvs.youtube_video_status(tmp_path, tmp_path, client, "secret", NOW)

    assert sorted(requested) == ["gone", "new", "stale"]
    rows = pl.read_parquet(path).select("youtube_video_id", "youtube_unplayable_reason").to_dicts()
    assert rows == [
        {"youtube_video_id": "fresh", "youtube_unplayable_reason": "private"},
        {"youtube_video_id": "gone", "youtube_unplayable_reason": "not_found"},
        {"youtube_video_id": "new", "youtube_unplayable_reason": None},
        {"youtube_video_id": "stale", "youtube_unplayable_reason": "not_embeddable"},
    ]


def test_batches_requests(tmp_path: Path) -> None:
    _write_candidates(tmp_path, [f"v{i:03d}" for i in range(yvs.BATCH_SIZE + 1)])
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["id"].split(",")
        batch_sizes.append(len(ids))
        return httpx.Response(200, json={"items": [_item(i) for i in ids]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yvs.youtube_video_status(tmp_path, tmp_path, client, "k", NOW)

    assert batch_sizes == [yvs.BATCH_SIZE, 1]


def test_raises_clear_error_on_quota_exceeded(tmp_path: Path) -> None:
    _write_candidates(tmp_path, ["a"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(yvs.YoutubeQuotaExceededError):
        yvs.youtube_video_status(tmp_path, tmp_path, client, "k", NOW)


def test_retries_server_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yvs._fetch_batch.retry, "wait", lambda _state: 0)  # type: ignore[attr-defined]
    _write_candidates(tmp_path, ["a"])
    responses = [httpx.Response(503), httpx.Response(200, json={"items": [_item("a")]})]

    with httpx.Client(transport=httpx.MockTransport(lambda _r: responses.pop(0))) as client:
        path = yvs.youtube_video_status(tmp_path, tmp_path, client, "k", NOW)

    assert pl.read_parquet(path)["youtube_unplayable_reason"].to_list() == [None]


def test_keeps_completed_batches_when_a_later_batch_fails(tmp_path: Path) -> None:
    _write_candidates(tmp_path, [f"v{i:03d}" for i in range(yvs.BATCH_SIZE + 1)])

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["id"].split(",")
        if len(ids) < yvs.BATCH_SIZE:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
        return httpx.Response(200, json={"items": [_item(i) for i in ids]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(yvs.YoutubeQuotaExceededError):
        yvs.youtube_video_status(tmp_path, tmp_path, client, "k", NOW)

    assert pl.read_parquet(tmp_path / yvs.OUTPUT_FILENAME).height == yvs.BATCH_SIZE


def test_non_json_403_surfaces_the_http_error(tmp_path: Path) -> None:
    _write_candidates(tmp_path, ["a"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>Forbidden</html>")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError, match="403"),
    ):
        yvs.youtube_video_status(tmp_path, tmp_path, client, "k", NOW)
