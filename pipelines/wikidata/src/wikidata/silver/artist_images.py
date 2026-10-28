import logging
from pathlib import Path

import polars as pl
from common.quality_checks import check_null_rate, check_reject_rate, check_unique_key

from wikidata.silver.commons_image_metadata import OUTPUT_FILENAME as COMMONS_METADATA_FILENAME

logger = logging.getLogger(__name__)

OUTPUT_FILENAME = "11_artist_images.parquet"
OUTPUT_COLUMNS = ("musicbrainz_artist_id", "thumb_url", "page_url", "author", "license", "license_url")
THUMB_URL_PATTERN = r"^https://(upload|thumb)\.wikimedia\.org/"
# grow-api's part_loader rejects a longer URL, failing the whole import.
URL_BYTES_MAX = 2048
MAX_REJECT_RATIO = 0.01


def artist_images(bronze_dir: Path, silver_dir: Path) -> Path:
    images = pl.read_parquet(bronze_dir / "wikidata_artist_images.parquet")
    metadata = pl.read_parquet(silver_dir / COMMONS_METADATA_FILENAME)
    usable = images.join(metadata, on="image_file").filter(
        ~pl.col("non_free"),
        pl.col("thumb_url").is_not_null(),
        pl.col("page_url").is_not_null(),
        pl.col("license").is_not_null(),
        # Every CC BY licence makes the author's name a condition of reuse.
        pl.col("author").is_not_null() | (pl.col("attribution_required") == False),  # noqa: E712
    )
    # Several QIDs can share an MBID and an item can hold several best-rank images: the smallest file name keeps the
    # pick stable from run to run, so Gold's delta doesn't churn.
    result = (
        usable.sort("musicbrainz_artist_id", "image_file")
        .unique("musicbrainz_artist_id", keep="first", maintain_order=True)
        .select(OUTPUT_COLUMNS)
    )
    check_unique_key(result, "musicbrainz_artist_id", OUTPUT_FILENAME)
    for column in ("thumb_url", "page_url", "license"):
        check_null_rate(result, column, OUTPUT_FILENAME)
    # Gold enforces grow-api's image contract strictly: a row Commons gave us that breaks it is dropped here instead.
    meets_contract = (
        pl.col("thumb_url").str.contains(THUMB_URL_PATTERN)
        & pl.col("page_url").str.starts_with("https://")
        & (pl.col("license_url").is_null() | pl.col("license_url").str.contains(r"^https?://"))
        & pl.all_horizontal(
            pl.col(column).is_null() | (pl.col(column).str.len_bytes() <= URL_BYTES_MAX)
            for column in ("thumb_url", "page_url", "license_url")
        )
    )
    contract_violations = result.filter(~meets_contract).height
    check_reject_rate(contract_violations, result.height, OUTPUT_FILENAME, MAX_REJECT_RATIO)
    result = result.filter(meets_contract)

    silver_dir.mkdir(parents=True, exist_ok=True)
    output_path = silver_dir / OUTPUT_FILENAME
    result.write_parquet(output_path)
    logger.info(
        "wrote %d rows to %s (%d artist image row(s) dropped: not checked yet, missing, non-free or unattributed)",
        result.height,
        output_path,
        images.height - usable.height,
    )
    return output_path
