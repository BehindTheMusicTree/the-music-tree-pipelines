import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import tenacity

logger = logging.getLogger(__name__)

VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
BATCH_SIZE = 50
YOUTUBE_STATUS_TTL_DAYS = 7
OUTPUT_FILENAME = "4_youtube_video_status.parquet"

_SCHEMA: dict[str, pl.DataType] = {
    "youtube_video_id": pl.Utf8(),
    "youtube_unplayable_reason": pl.Utf8(),
    "checked_at": pl.Datetime("us", "UTC"),
}


class YoutubeQuotaExceededError(RuntimeError):
    pass


def unplayable_reason(item: dict[str, Any] | None) -> str | None:
    if item is None:
        return "not_found"
    status = item["status"]
    if not status["embeddable"]:
        return "not_embeddable"
    if status["privacyStatus"] == "private":
        return "private"
    if status["uploadStatus"] != "processed":
        return "not_processed"
    # Region restrictions are left to the runtime playback fallback: allow-lists are typically 100+ countries,
    # and whether one blocks playback depends on the viewer's country, which isn't known here.
    return None


def _is_retryable(exception: BaseException) -> bool:
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500
    return isinstance(exception, httpx.TransportError)


@tenacity.retry(
    retry=tenacity.retry_if_exception(_is_retryable),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(5),
    reraise=True,
)
def _fetch_batch(client: httpx.Client, api_key: str, video_ids: Sequence[str]) -> dict[str, str | None]:
    # Key sent as a header, not a query param, so httpx's request logging never prints it.
    response = client.get(
        VIDEOS_ENDPOINT,
        params={"part": "status,contentDetails", "id": ",".join(video_ids), "maxResults": BATCH_SIZE},
        headers={"X-goog-api-key": api_key},
        timeout=30.0,
    )
    if response.status_code == 403:
        try:
            errors = response.json().get("error", {}).get("errors", [])
        except ValueError:
            errors = []
        if any(error.get("reason") == "quotaExceeded" for error in errors):
            raise YoutubeQuotaExceededError(
                "YouTube Data API daily quota exceeded — rerun after the quota resets (midnight Pacific)"
            )
    response.raise_for_status()
    items = {item["id"]: item for item in response.json()["items"]}
    return {video_id: unplayable_reason(items.get(video_id)) for video_id in video_ids}


def youtube_video_status(silver_dir: Path, output_dir: Path, client: httpx.Client, api_key: str, now: datetime) -> Path:
    video_ids = pl.read_parquet(silver_dir / "3_youtube_candidates.parquet")["youtube_video_id"].unique()
    output_path = output_dir / OUTPUT_FILENAME

    cached = pl.DataFrame(schema=_SCHEMA)
    if output_path.exists():
        cached = pl.read_parquet(output_path).filter(
            pl.col("youtube_video_id").is_in(video_ids.implode())
            & (pl.col("checked_at") >= now - timedelta(days=YOUTUBE_STATUS_TTL_DAYS))
        )

    to_fetch = sorted(set(video_ids) - set(cached["youtube_video_id"]))
    fetched_rows = []
    # Written in a finally so batches that already spent quota survive a mid-run quota/5xx failure.
    try:
        for start in range(0, len(to_fetch), BATCH_SIZE):
            for video_id, reason in _fetch_batch(client, api_key, to_fetch[start : start + BATCH_SIZE]).items():
                fetched_rows.append(
                    {"youtube_video_id": video_id, "youtube_unplayable_reason": reason, "checked_at": now}
                )
    finally:
        fetched = pl.DataFrame(fetched_rows, schema=_SCHEMA)
        result = pl.concat([cached, fetched]).sort("youtube_video_id")
        output_dir.mkdir(parents=True, exist_ok=True)
        result.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d fetched, %d from cache, %d unplayable)",
        result.height,
        output_path,
        fetched.height,
        cached.height,
        result["youtube_unplayable_reason"].is_not_null().sum(),
    )
    return output_path
