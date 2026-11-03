from pathlib import Path

import polars as pl


def primary_types(bronze_dir: Path) -> pl.LazyFrame:
    """`(type, primary_type)`, the lowercase name of each release-group primary type."""
    return pl.scan_parquet(bronze_dir / "release_group_primary_type.parquet").select(
        type="id", primary_type=pl.col("name").str.to_lowercase()
    )


def secondary_types(bronze_dir: Path) -> pl.LazyFrame:
    """`(release_group, secondary_types)`, the sorted lowercase secondary type names of each release group that has
    any."""
    return (
        pl.scan_parquet(bronze_dir / "release_group_secondary_type_join.parquet")
        .join(
            pl.scan_parquet(bronze_dir / "release_group_secondary_type.parquet").select(
                secondary_type="id", secondary_type_name=pl.col("name").str.to_lowercase()
            ),
            on="secondary_type",
        )
        .group_by("release_group")
        .agg(secondary_types=pl.col("secondary_type_name").unique().sort())
    )
