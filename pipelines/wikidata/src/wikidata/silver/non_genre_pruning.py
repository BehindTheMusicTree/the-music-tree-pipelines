import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# Committed alongside the code (not a gitignored bronze/silver output) because they're hand-curated,
# not fetched from Wikidata: each lists items that no automated signal distinguishes from a real
# genre, so a data expert reviewing the root lists adds them by hand, with a `reason` column
# explaining why. See DESIGN.md#21-2_non_genre_pruning.
MANUAL_THEME_GENRES_PATH = Path(__file__).parent / "manual_theme_genres.csv"
MANUAL_TECHNIQUE_GENRES_PATH = Path(__file__).parent / "manual_technique_genres.csv"
MANUAL_OUT_OF_SCOPE_GENRES_PATH = Path(__file__).parent / "manual_out_of_scope_genres.csv"
MANUAL_UMBRELLA_CANONICAL_GENRES_PATH = Path(__file__).parent / "manual_umbrella_canonical_genres.csv"
MANUAL_DUPLICATE_GENRES_PATH = Path(__file__).parent / "manual_duplicate_genres.csv"


def _load_dropped_ids(df: pl.DataFrame, manual_csv: pl.DataFrame, csv_name: str) -> set[str]:
    if manual_csv.is_empty():
        return set()

    blank = manual_csv.filter(pl.col("item_id").is_null() | (pl.col("item_id").str.strip_chars() == ""))
    if not blank.is_empty():
        raise ValueError(f"{csv_name} has row(s) with a null/blank 'item_id'")

    ids = manual_csv.with_columns(item_id=pl.col("item_id").str.strip_chars()).select("item_id").to_series().to_list()
    if len(ids) != len(set(ids)):
        raise ValueError(f"{csv_name} contains duplicate item_id rows")

    known_item_ids = set(df.select("item_id").unique().to_series())
    unknown_ids = [item_id for item_id in ids if item_id not in known_item_ids]
    if unknown_ids:
        raise ValueError(f"{csv_name} rows reference item_id(s) not found in the genre tree: {unknown_ids}")

    return set(ids)


def prune_non_genre_items(
    item_links_path: Path,
    manual_theme_genres_path: Path,
    manual_technique_genres_path: Path,
    manual_out_of_scope_genres_path: Path,
    manual_umbrella_canonical_genres_path: Path,
    manual_duplicate_genres_path: Path,
    output_dir: Path,
) -> Path:
    logger.info("pruning non-genre items from %s", item_links_path)
    df = pl.read_parquet(item_links_path)

    manual_theme_genres = pl.read_csv(manual_theme_genres_path)
    manual_technique_genres = pl.read_csv(manual_technique_genres_path)
    manual_out_of_scope_genres = pl.read_csv(manual_out_of_scope_genres_path)
    manual_umbrella_canonical_genres = pl.read_csv(manual_umbrella_canonical_genres_path)
    manual_duplicate_genres = pl.read_csv(manual_duplicate_genres_path)
    theme_ids = _load_dropped_ids(df, manual_theme_genres, "manual_theme_genres.csv")
    technique_ids = _load_dropped_ids(df, manual_technique_genres, "manual_technique_genres.csv")
    out_of_scope_ids = _load_dropped_ids(df, manual_out_of_scope_genres, "manual_out_of_scope_genres.csv")
    umbrella_ids = _load_dropped_ids(df, manual_umbrella_canonical_genres, "manual_umbrella_canonical_genres.csv")
    duplicate_ids = _load_dropped_ids(df, manual_duplicate_genres, "manual_duplicate_genres.csv")
    dropped_ids = theme_ids | technique_ids | out_of_scope_ids | umbrella_ids | duplicate_ids
    df = df.filter(~pl.col("item_id").is_in(list(dropped_ids)))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "2_non_genre_pruning.parquet"
    df.write_parquet(output_path)
    logger.info("wrote %d rows to %s (%d non-genre item(s) dropped)", df.height, output_path, len(dropped_ids))

    return output_path
