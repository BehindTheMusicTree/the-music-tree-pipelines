import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import tenacity
from common.quality_checks import check_reject_rate

logger = logging.getLogger(__name__)

COMMONS_API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
BATCH_SIZE = 50
# A standard Wikimedia thumbnail step: non-standard widths are rendered on demand and rate limited.
THUMB_WIDTH = 500
COMMONS_IMAGE_TTL_DAYS = 30
OUTPUT_FILENAME = "10_commons_image_metadata.parquet"
MAX_REJECT_RATIO = 0.01
FILE_PREFIX = "File:"

_SCHEMA: dict[str, pl.DataType] = {
    "image_file": pl.Utf8(),
    "thumb_url": pl.Utf8(),
    "page_url": pl.Utf8(),
    "author": pl.Utf8(),
    "license": pl.Utf8(),
    "license_url": pl.Utf8(),
    "attribution_required": pl.Boolean(),
    "non_free": pl.Boolean(),
    "checked_at": pl.Datetime("us", "UTC"),
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str | None) -> str | None:
    """extmetadata values are HTML fragments (an author is often a link to their user page)."""
    if value is None:
        return None
    extractor = _TextExtractor()
    extractor.feed(value)
    extractor.close()
    return " ".join("".join(extractor.parts).split()) or None


def _metadata_value(extmetadata: dict[str, Any], key: str) -> str | None:
    return html_to_text(extmetadata.get(key, {}).get("value"))


def _strip_query(url: str | None) -> str | None:
    """Commons appends utm_* tracking parameters: dropped so the URL stays stable across runs (Gold's delta)."""
    return None if url is None else url.split("?", 1)[0]


def metadata_row(image_file: str, page: dict[str, Any] | None) -> dict[str, Any]:
    if page is None or page.get("missing") or not page.get("imageinfo"):
        return {"image_file": image_file}
    info = page["imageinfo"][0]
    # Commons serializes a file with no extended metadata as `[]`, not `{}`.
    extmetadata = info.get("extmetadata") or {}
    attribution_required = _metadata_value(extmetadata, "AttributionRequired")
    return {
        "image_file": image_file,
        "thumb_url": _strip_query(info.get("thumburl")),
        "page_url": info.get("descriptionurl"),
        # Credit is the uploader's own wording, the fallback when the Artist field is empty (e.g. "Own work").
        "author": _metadata_value(extmetadata, "Artist") or _metadata_value(extmetadata, "Credit"),
        "license": _metadata_value(extmetadata, "LicenseShortName"),
        "license_url": _metadata_value(extmetadata, "LicenseUrl"),
        "attribution_required": None if attribution_required is None else attribution_required == "true",
        "non_free": _metadata_value(extmetadata, "NonFree") == "true",
    }


def _is_retryable(exception: BaseException) -> bool:
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500 or exception.response.status_code == 429
    return isinstance(exception, (httpx.TransportError, ValueError))


@tenacity.retry(
    retry=tenacity.retry_if_exception(_is_retryable),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(5),
    reraise=True,
)
def _get_page(client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
    # POST: a batch of 50 titles overflows the GET URL limit (414).
    response = client.post(COMMONS_API_ENDPOINT, data=params, timeout=60.0)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"Commons imageinfo query failed for {params['titles']}: {body['error']}")
    return body


def _fetch_batch(client: httpx.Client, image_files: Sequence[str]) -> list[dict[str, Any]]:
    base_params = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": THUMB_WIDTH,
        "iiextmetadatafilter": "Artist|Credit|LicenseShortName|LicenseUrl|AttributionRequired|NonFree",
        "redirects": 1,
        "titles": "|".join(f"{FILE_PREFIX}{image_file}" for image_file in image_files),
        "format": "json",
        "formatversion": 2,
    }
    renamed: dict[str, str] = {}
    pages: dict[str, dict[str, Any]] = {}
    continue_params: dict[str, Any] = {}
    # A response carries only as much extmetadata as fits; `iicontinue` brings the rest. A bare `iistart` would only
    # page through each file's older revisions, which aren't wanted.
    while True:
        query = _get_page(client, {**base_params, **continue_params})
        body = query.get("query", {})
        for change in body.get("normalized", []) + body.get("redirects", []):
            renamed[change["from"]] = change["to"]
        for page in body.get("pages", []):
            if not pages.get(page["title"], {}).get("imageinfo"):
                pages[page["title"]] = page
        if "iicontinue" not in query.get("continue", {}):
            break
        continue_params = query["continue"]

    rows = []
    for image_file in image_files:
        title = f"{FILE_PREFIX}{image_file}"
        # Normalization maps the requested title to its canonical form, then a redirect maps that on.
        for _ in range(2):
            title = renamed.get(title, title)
        try:
            rows.append(metadata_row(image_file, pages.get(title)))
        except (AttributeError, IndexError, KeyError, TypeError):
            # A file with unparseable metadata is skipped and, not being cached, retried on the next run.
            logger.warning("skipping %s: unparseable Commons metadata", image_file, exc_info=True)
    return rows


def commons_image_metadata(
    bronze_dir: Path, output_dir: Path, client: httpx.Client, now: datetime, max_batches: int
) -> Path:
    image_files = (
        pl.read_parquet(bronze_dir / "wikidata_artist_images.parquet").select("image_file").unique()["image_file"]
    )
    output_path = output_dir / OUTPUT_FILENAME

    cached = pl.DataFrame(schema=_SCHEMA)
    if output_path.exists():
        cached = pl.read_parquet(output_path).filter(pl.col("image_file").is_in(image_files.implode()))

    # Stale rows stay usable until refreshed: the first run can't cover every file within the per-run budget, so
    # never-checked files go first, then the stalest ones, and whatever's left waits for the next run.
    to_fetch = (
        image_files.to_frame()
        .join(cached.select("image_file", "checked_at"), on="image_file", how="left")
        .filter(pl.col("checked_at").is_null() | (pl.col("checked_at") < now - timedelta(days=COMMONS_IMAGE_TTL_DAYS)))
        .sort("checked_at", "image_file", nulls_last=False)
        .head(max_batches * BATCH_SIZE)["image_file"]
        .to_list()
    )
    fetched_rows: list[dict[str, Any]] = []
    rejected = 0
    # Written in a finally so batches already fetched survive a mid-run failure.
    try:
        for start in range(0, len(to_fetch), BATCH_SIZE):
            batch = to_fetch[start : start + BATCH_SIZE]
            batch_rows = _fetch_batch(client, batch)
            rejected += len(batch) - len(batch_rows)
            fetched_rows.extend({**row, "checked_at": now} for row in batch_rows)
    finally:
        fetched = pl.DataFrame(fetched_rows, schema=_SCHEMA)
        cached = cached.join(fetched, on="image_file", how="anti")
        result = pl.concat([cached, fetched]).sort("image_file")
        output_dir.mkdir(parents=True, exist_ok=True)
        result.write_parquet(output_path)
    check_reject_rate(rejected, len(to_fetch), OUTPUT_FILENAME, MAX_REJECT_RATIO)
    logger.info(
        "wrote %d rows to %s (%d fetched, %d from cache, %d files never checked yet, %d missing on Commons)",
        result.height,
        output_path,
        fetched.height,
        cached.height,
        image_files.len() - result.height,
        result["thumb_url"].is_null().sum(),
    )
    return output_path
