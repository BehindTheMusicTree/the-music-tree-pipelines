import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

_MEMBER_OF_BAND = "member of band"
# Other "member of band" attributes (original, additional, minor…) qualify the membership, not the role played.
_ROLE_ROOTS = ("instrument", "vocal")


def artist_members(bronze_dir: Path, output_dir: Path) -> Path:
    artist = pl.scan_parquet(bronze_dir / "artist.parquet").select("id", "gid")
    attribute_type = pl.scan_parquet(bronze_dir / "link_attribute_type.parquet")
    role_type = (
        attribute_type.join(attribute_type.select(root="id", root_name="name"), on="root")
        .filter(pl.col("root_name").is_in(_ROLE_ROOTS))
        .select(attribute_type="id", role="name")
    )
    roles = (
        pl.scan_parquet(bronze_dir / "link_attribute.parquet")
        .join(role_type, on="attribute_type")
        .group_by("link")
        .agg(pl.col("role").unique().sort())
    )
    member_link = (
        pl.scan_parquet(bronze_dir / "link.parquet")
        .join(
            pl.scan_parquet(bronze_dir / "link_type.parquet").filter(
                (pl.col("name") == _MEMBER_OF_BAND)
                & (pl.col("entity_type0") == "artist")
                & (pl.col("entity_type1") == "artist")
            ),
            left_on="link_type",
            right_on="id",
            how="semi",
        )
        .select(link="id", begin_year="begin_date_year", end_year="end_date_year", ended="ended")
    )
    result = (
        pl.scan_parquet(bronze_dir / "l_artist_artist.parquet")
        .join(member_link, on="link")
        .join(roles, on="link", how="left")
        .join(artist.select(entity0="id", member_musicbrainz_artist_id="gid"), on="entity0")
        .join(artist.select(entity1="id", group_musicbrainz_artist_id="gid"), on="entity1")
        .select(
            "group_musicbrainz_artist_id",
            "member_musicbrainz_artist_id",
            "begin_year",
            "end_year",
            "ended",
            roles=pl.col("role").fill_null([]),
        )
        .unique()
        .sort("group_musicbrainz_artist_id", "member_musicbrainz_artist_id", "begin_year", nulls_last=True)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "7_artist_members.parquet"
    result.sink_parquet(output_path)
    logger.info("wrote %d rows to %s", pl.scan_parquet(output_path).select(pl.len()).collect().item(), output_path)
    return output_path
