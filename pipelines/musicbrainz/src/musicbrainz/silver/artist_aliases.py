import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def artist_aliases(bronze_dir: Path, output_dir: Path) -> Path:
    result = (
        pl.scan_parquet(bronze_dir / "artist_alias.parquet")
        .join(
            pl.scan_parquet(bronze_dir / "artist.parquet").select(artist="id", musicbrainz_artist_id="gid"), on="artist"
        )
        .select("musicbrainz_artist_id", pl.col("name"), pl.col("sort_name"))
        .unpivot(index="musicbrainz_artist_id", value_name="alias")
        .drop_nulls("alias")
        .filter(pl.col("alias").str.strip_chars() != "")
        .select("musicbrainz_artist_id", "alias")
        .unique()
        .sort("musicbrainz_artist_id", "alias")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "11_artist_aliases.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
