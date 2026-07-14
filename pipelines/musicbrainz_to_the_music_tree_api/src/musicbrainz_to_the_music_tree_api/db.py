import os

import psycopg
import psycopg.types.string


def connect() -> psycopg.Connection:
    host = os.environ["MB_HOST"]
    port = os.environ["MB_PORT"]
    db = os.environ["MB_DB"]
    user = os.environ["MB_USER"]
    dsn = f"postgresql://{user}@{host}:{port}/{db}"

    conn = psycopg.connect(dsn, connect_timeout=3)
    # Load Postgres uuid columns (e.g. gid) as str instead of the default uuid.UUID —
    # Polars can't map uuid.UUID to a native dtype and falls back to unwritable Object.
    conn.adapters.register_loader("uuid", psycopg.types.string.TextLoader)
    conn.adapters.register_loader("uuid", psycopg.types.string.TextBinaryLoader)
    return conn
