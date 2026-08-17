import psycopg
import psycopg.types.string
from common.env import require_env


def connect() -> psycopg.Connection:
    host = require_env("MB_HOST")
    port = require_env("MB_PORT")
    db = require_env("MB_DB")
    user = require_env("MB_USER")
    dsn = f"postgresql://{user}@{host}:{port}/{db}"

    conn = psycopg.connect(dsn, connect_timeout=3)
    # Load Postgres uuid columns (e.g. gid) as str instead of the default uuid.UUID —
    # Polars can't map uuid.UUID to a native dtype and falls back to unwritable Object.
    conn.adapters.register_loader("uuid", psycopg.types.string.TextLoader)
    conn.adapters.register_loader("uuid", psycopg.types.string.TextBinaryLoader)
    return conn
