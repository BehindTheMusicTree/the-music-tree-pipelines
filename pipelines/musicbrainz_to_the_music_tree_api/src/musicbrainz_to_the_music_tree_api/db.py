import os

import psycopg

MB_HOST = os.environ.get("MB_HOST", "127.0.0.1")
MB_PORT = int(os.environ.get("MB_PORT", "5432"))
MB_DB = os.environ.get("MB_DB", "musicbrainz_db")
MB_USER = os.environ.get("MB_USER", "musicbrainz")
MB_DSN = f"postgresql://{MB_USER}@{MB_HOST}:{MB_PORT}/{MB_DB}"


def connect():
    return psycopg.connect(MB_DSN, connect_timeout=3)
