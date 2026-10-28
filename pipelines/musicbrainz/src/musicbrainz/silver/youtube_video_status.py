import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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
FETCH_WORKERS = 4
YOUTUBE_STATUS_TTL_DAYS = 7
OUTPUT_FILENAME = "4_youtube_video_status.parquet"

_SCHEMA: dict[str, pl.DataType] = {
    "youtube_video_id": pl.Utf8(),
    "youtube_unplayable_reason": pl.Utf8(),
    "youtube_view_count": pl.Int64(),
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


def view_count(item: dict[str, Any] | None) -> int | None:
    if item is None or "viewCount" not in item.get("statistics", {}):
        return None
    return int(item["statistics"]["viewCount"])


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
def _fetch_batch(
    client: httpx.Client, api_key: str, video_ids: Sequence[str]
) -> dict[str, tuple[str | None, int | None]]:
    # Key sent as a header, not a query param, so httpx's request logging never prints it.
    response = client.get(
        VIDEOS_ENDPOINT,
        params={"part": "status,contentDetails,statistics", "id": ",".join(video_ids), "maxResults": BATCH_SIZE},
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
    return {
        video_id: (unplayable_reason(items.get(video_id)), view_count(items.get(video_id))) for video_id in video_ids
    }


def youtube_video_status(
    silver_dir: Path, output_dir: Path, client: httpx.Client, api_key: str, now: datetime, max_batches: int
) -> Path:
    videos = (
        pl.read_parquet(silver_dir / "3_youtube_candidates.parquet")
        .group_by("youtube_video_id")
        .agg(pl.col("pinned").any())
    )
    video_ids = videos["youtube_video_id"]
    output_path = output_dir / OUTPUT_FILENAME

    cached = pl.DataFrame(schema=_SCHEMA)
    if output_path.exists():
        cached = pl.read_parquet(output_path).filter(pl.col("youtube_video_id").is_in(video_ids.implode()))
        # Parquets written before view counts were fetched lack the column; their rows fill in as the TTL refreshes them.
        if "youtube_view_count" not in cached.columns:
            cached = cached.with_columns(youtube_view_count=pl.lit(None, dtype=pl.Int64()))
        cached = cached.select(list(_SCHEMA))

    # Stale rows stay usable until refreshed: the per-run budget can't cover every candidate of the full catalog,
    # so curated (pinned) ids go first, then never-checked ids, then the stalest ones, and whatever's left waits for
    # the next run.
    to_fetch = (
        videos.join(cached, on="youtube_video_id", how="left")
        .filter(pl.col("checked_at").is_null() | (pl.col("checked_at") < now - timedelta(days=YOUTUBE_STATUS_TTL_DAYS)))
        .sort(
            "pinned",
            "checked_at",
            "youtube_video_id",
            descending=[True, False, False],
            nulls_last=[False, False, False],
        )
        .head(max_batches * BATCH_SIZE)["youtube_video_id"]
        .to_list()
    )
    fetched_rows = []
    # Written in a finally so batches that already spent quota survive a mid-run quota/5xx failure.
    try:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            futures = [
                pool.submit(_fetch_batch, client, api_key, to_fetch[start : start + BATCH_SIZE])
                for start in range(0, len(to_fetch), BATCH_SIZE)
            ]
            try:
                for future in as_completed(futures):
                    for video_id, (reason, count) in future.result().items():
                        fetched_rows.append(
                            {
                                "youtube_video_id": video_id,
                                "youtube_unplayable_reason": reason,
                                "youtube_view_count": count,
                                "checked_at": now,
                            }
                        )
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    finally:
        fetched = pl.DataFrame(fetched_rows, schema=_SCHEMA)
        cached = cached.join(fetched, on="youtube_video_id", how="anti")
        result = pl.concat([cached, fetched]).sort("youtube_video_id")
        output_dir.mkdir(parents=True, exist_ok=True)
        result.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d fetched, %d from cache, %d candidates never checked yet, %d unplayable, %d with a view count)",
        result.height,
        output_path,
        fetched.height,
        cached.height,
        video_ids.len() - result.height,
        result["youtube_unplayable_reason"].is_not_null().sum(),
        result["youtube_view_count"].is_not_null().sum(),
    )
    return output_path
