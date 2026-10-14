import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# The curated subset of MusicBrainz artist-url link types an artist page shows; every other type is dropped.
LINK_KINDS = {
    "official homepage": "official",
    "wikipedia": "wikipedia",
    "wikidata": "wikidata",
    "youtube": "youtube",
    "bandcamp": "bandcamp",
    "soundcloud": "soundcloud",
    "discogs": "discogs",
    "social network": "social",
    "myspace": "social",
}
# Spotify has no link type of its own (it's filed under "free streaming"/"streaming", shared with other
# platforms), so it's recognized by its URL.
_SPOTIFY_URL_PATTERN = r"^https?://open\.spotify\.com/"


def artist_links(bronze_dir: Path, output_dir: Path) -> Path:
    link = (
        pl.scan_parquet(bronze_dir / "link.parquet")
        # An ended link (a lapsed domain, a closed account) no longer points at the artist.
        .filter(~pl.col("ended"))
        .select(link="id", link_type="link_type")
        .join(
            pl.scan_parquet(bronze_dir / "link_type.parquet").select(link_type="id", link_type_name="name"),
            on="link_type",
        )
    )
    result = (
        pl.scan_parquet(bronze_dir / "l_artist_url.parquet")
        .join(link, on="link")
        .join(pl.scan_parquet(bronze_dir / "url.parquet").select(entity1="id", url="url"), on="entity1")
        .join(
            pl.scan_parquet(bronze_dir / "artist.parquet").select(entity0="id", musicbrainz_artist_id="gid"),
            on="entity0",
        )
        .with_columns(
            kind=pl.when(pl.col("url").str.contains(_SPOTIFY_URL_PATTERN))
            .then(pl.lit("spotify"))
            .otherwise(pl.col("link_type_name").replace_strict(LINK_KINDS, default=None, return_dtype=pl.Utf8))
        )
        .drop_nulls("kind")
        .select("musicbrainz_artist_id", "kind", "url")
        .unique()
        .sort("musicbrainz_artist_id", "kind", "url")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "8_artist_links.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
