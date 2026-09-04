import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = ["item_id", "item_label", "item_url", "parent_id", "parent_label", "parent_url", "relation_type"]

# Committed alongside the code (not a gitignored bronze/silver output) because it's hand-curated,
# not fetched from Wikidata: genre items organized around a subject/theme/subculture (e.g. "LGBT
# music", "steampunk music", "bronycore") rather than a geography, ethnicity, or musical style
# don't belong in either the canonical or regional genre tree. A data expert reviewing the root
# lists adds them here with a `reason`; they're dropped entirely from both 5_hierarchy.parquet and
# 5_regional_hierarchy.parquet, and any child edge pointing at one is severed the same way an edge
# to a non-genre/non-regional parent already is (see _prune_canonical/_prune_regional).
MANUAL_THEME_GENRES_PATH = Path(__file__).parent / "manual_theme_genres.csv"


def _load_theme_ids(df: pl.DataFrame, manual_theme_genres: pl.DataFrame) -> set[str]:
    known_item_ids = set(df.select("item_id").unique().to_series())
    theme_ids = set(manual_theme_genres.select("item_id").unique().to_series())
    unknown_item_ids = sorted(item_id for item_id in theme_ids if item_id not in known_item_ids)
    if unknown_item_ids:
        raise ValueError(
            f"manual_theme_genres.csv rows reference item_id(s) not found in the genre tree: {unknown_item_ids}"
        )
    return theme_ids


def _collapse_to_lowest_qid(edges: pl.DataFrame) -> pl.DataFrame:
    # Wikidata's P279/P361 graph isn't a strict tree: ~43% of genre items have more than one
    # surviving genre parent, and only 1 item in the whole extension has a "preferred rank" P279
    # statement to disambiguate with (checked live). No reliable signal exists, so — provisionally,
    # pending a real product/curation decision — keep only the lowest-QID parent per item. This is
    # a tâtonnement placeholder, not a considered rule; see SCHEMA.md.
    return (
        edges.with_columns(parent_numeric_id=pl.col("parent_id").str.slice(1).cast(pl.Int64, strict=False))
        .sort(["item_id", "parent_numeric_id"])
        .unique(subset="item_id", keep="first")
        .select(OUTPUT_COLUMNS)
    )


def _prune_canonical(items: pl.DataFrame) -> pl.DataFrame:
    is_genre_edge = pl.col("parent_id").is_null() | pl.col("parent_is_genre")
    return _collapse_to_lowest_qid(items.filter(is_genre_edge))


def _prune_regional(items: pl.DataFrame) -> pl.DataFrame:
    # `items` here includes the "music of <place>" seed items themselves (they're now regional
    # genre nodes in their own right, not dropped) alongside actual regional genres, so most items
    # keep their real parent chain (e.g. morna -> "music of Cape Verde") instead of losing it. An
    # item whose parent edges all lead outside the regional set entirely (a genuine top-level seed
    # with no parent at all, e.g. a continent-level "music of X" with nothing above it) still
    # becomes a root of the regional graph rather than vanishing.
    is_regional_edge = pl.col("parent_id").is_null() | pl.col("parent_is_regional")
    collapsed = _collapse_to_lowest_qid(items.filter(is_regional_edge))

    orphans = (
        items.select("item_id", "item_label", "item_url")
        .unique(subset="item_id")
        .join(collapsed.select("item_id"), on="item_id", how="anti")
        .with_columns(
            parent_id=pl.lit(None, dtype=pl.Utf8),
            parent_label=pl.lit(None, dtype=pl.Utf8),
            parent_url=pl.lit(None, dtype=pl.Utf8),
            relation_type=pl.lit(None, dtype=pl.Utf8),
        )
        .select(OUTPUT_COLUMNS)
    )
    return pl.concat([collapsed, orphans])


def prune_genre_hierarchy(
    genre_parents_path: Path, manual_theme_genres_path: Path, output_dir: Path
) -> tuple[Path, Path]:
    logger.info("pruning genre hierarchy from %s", genre_parents_path)
    df = pl.read_parquet(genre_parents_path)
    manual_theme_genres = pl.read_csv(manual_theme_genres_path)
    theme_ids = _load_theme_ids(df, manual_theme_genres)

    df = df.filter(~pl.col("item_id").is_in(list(theme_ids))).with_columns(
        parent_is_genre=pl.when(pl.col("parent_id").is_in(list(theme_ids)))
        .then(pl.lit(False))
        .otherwise(pl.col("parent_is_genre"))
    )

    parent_is_regional = df.select(
        pl.col("item_id").alias("parent_id"), pl.col("is_regional").alias("parent_is_regional")
    ).unique()

    # Canonical: real genres that are not regional. Regional: everything flagged `is_regional`,
    # which now includes the "music of <place>" seed items themselves — they're regional genre
    # nodes here, not dropped, even though `is_regional_overview` is True for them.
    canonical_items = df.filter(~pl.col("is_regional_overview") & ~pl.col("is_regional")).join(
        parent_is_regional, on="parent_id", how="left"
    )
    regional_items = df.filter(pl.col("is_regional")).join(parent_is_regional, on="parent_id", how="left")

    canonical = _prune_canonical(canonical_items)
    regional = _prune_regional(regional_items)

    output_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = output_dir / "5_hierarchy.parquet"
    regional_path = output_dir / "5_regional_hierarchy.parquet"
    canonical.write_parquet(canonical_path)
    regional.write_parquet(regional_path)
    logger.info(
        "wrote %d rows to %s (canonical, %d items) and %d rows to %s (regional, %d items)",
        canonical.height,
        canonical_path,
        canonical_items.select("item_id").n_unique(),
        regional.height,
        regional_path,
        regional_items.select("item_id").n_unique(),
    )

    return canonical_path, regional_path
