import json
import logging
from pathlib import Path

import polars as pl
from common.quality_checks import check_non_empty
from jsonschema import ValidationError, validate

SONGS_SCHEMA_PATH = Path(__file__).parent / "schemas" / "songs.schema.json"

logger = logging.getLogger(__name__)


def export_songs(genre_match_path: Path, output_dir: Path) -> Path:
    logger.info("exporting reconciled songs from %s", genre_match_path)
    matched = pl.read_parquet(genre_match_path)
    songs = (
        matched.filter(~pl.col("match_method").is_in(["unmatched", "accepted_non_genre"]))
        .select("title", "artist", "youtube_video_id", "wikidata_genre_name")
        .rename({"wikidata_genre_name": "genre_name"})
    )
    check_non_empty(songs, "2_songs")
    songs_list = songs.to_dicts()

    schema = json.loads(SONGS_SCHEMA_PATH.read_text())
    try:
        validate(songs_list, schema)
    except ValidationError as e:
        raise ValueError(f"songs export failed schema validation ({SONGS_SCHEMA_PATH}): {e.message}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "2_songs.json"
    output_path.write_text(json.dumps(songs_list, indent=2, ensure_ascii=False))
    logger.info("wrote %d songs to %s", len(songs_list), output_path)
    return output_path
