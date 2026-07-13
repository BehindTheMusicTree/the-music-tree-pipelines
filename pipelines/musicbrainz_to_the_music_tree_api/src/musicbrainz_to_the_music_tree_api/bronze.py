import os
from pathlib import Path

import psycopg
import polars as pl


BRONZE_TABLES = ("recording", "artist", "release_tag", "recording_tag", "genre")


def ingest_table(conn: psycopg, table: str, output_dir: Path):
    if table not in BRONZE_TABLES:
        raise ValueError(f"Tablre {table} is not part of bronze tables")

    df = pl.read_database(f"SELECT * from musicbrainz.{table}", conn)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{table}.parquet"
    df.write_parquet(output_path)

    return output_path


def run_bronze_ingestion(conn: psycopg.Connection, output_dir: Path) -> list[Path]:
    return [ingest_table(conn, table, output_dir) for table in BRONZE_TABLES]


if __name__ == "__main__":
    from musicbrainz_to_the_music_tree_api import db

    output_dir = Path(os.environ.get("MB_BRONZE_OUTPUT_DIR", "bronze"))
    run_bronze_ingestion(db.connect(), output_dir)
