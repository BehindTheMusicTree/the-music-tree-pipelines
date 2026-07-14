import os

import psycopg
import psycopg.types.string

MB_HOST = os.environ.get("MB_HOST", "127.0.0.1")
MB_PORT = int(os.environ.get("MB_PORT", "5432"))
MB_DB = os.environ.get("MB_DB", "musicbrainz_db")
MB_USER = os.environ.get("MB_USER", "musicbrainz")
MB_DSN = f"postgresql://{MB_USER}@{MB_HOST}:{MB_PORT}/{MB_DB}"


def connect() -> psycopg.Connection:
    conn = psycopg.connect(MB_DSN, connect_timeout=3)
    # Load Postgres uuid columns (e.g. gid) as str instead of the default uuid.UUID —
    # Polars can't map uuid.UUID to a native dtype and falls back to unwritable Object.
    conn.adapters.register_loader("uuid", psycopg.types.string.TextLoader)
    conn.adapters.register_loader("uuid", psycopg.types.string.TextBinaryLoader)
    return conn
