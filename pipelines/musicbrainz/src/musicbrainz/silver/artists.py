import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# ISO 3166-1 user-assigned codes: never an officially assigned country.
_USER_ASSIGNED_CODE_PATTERN = r"^(AA|Q[M-Z]|X[A-Z]|ZZ)$"
# Kosovo: user-assigned, but a country in practice (EU, ISO itself and MusicBrainz all use it).
_ALLOWED_USER_ASSIGNED_CODES = ["XK"]


def artists(bronze_dir: Path, output_dir: Path) -> Path:
    artist_type = pl.scan_parquet(bronze_dir / "artist_type.parquet").select(
        type="id", type_name=pl.col("name").str.to_lowercase()
    )
    # Only an artist whose area is itself a country has a code; a city/subdivision area stays null. MusicBrainz also
    # codes regions (XE Europe, XW Worldwide) and historical states in ISO's user-assigned ranges, which aren't countries.
    country = (
        pl.scan_parquet(bronze_dir / "iso_3166_1.parquet")
        .filter(
            ~pl.col("code").str.contains(_USER_ASSIGNED_CODE_PATTERN)
            | pl.col("code").is_in(_ALLOWED_USER_ASSIGNED_CODES)
        )
        .group_by("area")
        .agg(country=pl.col("code").min())
    )
    result = (
        pl.scan_parquet(bronze_dir / "artist.parquet")
        .join(artist_type, on="type", how="left")
        .join(country, on="area", how="left")
        .select(
            musicbrainz_artist_id="gid",
            name="name",
            sort_name="sort_name",
            disambiguation=pl.when(pl.col("comment") != "").then(pl.col("comment")),
            type="type_name",
            country="country",
            begin_year="begin_date_year",
            end_year="end_date_year",
            ended="ended",
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "6_artists.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
