import polars as pl

OUTPUT_COLUMNS = [
    "item_id",
    "item_label",
    "item_display_label",
    "item_url",
    "parent_id",
    "parent_label",
    "parent_display_label",
    "parent_url",
    "relation_type",
]


def promote_orphans_to_roots(items: pl.DataFrame, collapsed: pl.DataFrame) -> pl.DataFrame:
    orphans = (
        items.select("item_id", "item_label", "item_display_label", "item_url")
        .unique(subset="item_id")
        .join(collapsed.select("item_id"), on="item_id", how="anti")
        .with_columns(
            parent_id=pl.lit(None, dtype=pl.Utf8),
            parent_label=pl.lit(None, dtype=pl.Utf8),
            parent_display_label=pl.lit(None, dtype=pl.Utf8),
            parent_url=pl.lit(None, dtype=pl.Utf8),
            relation_type=pl.lit(None, dtype=pl.Utf8),
        )
        .select(OUTPUT_COLUMNS)
    )
    return pl.concat([collapsed, orphans])
