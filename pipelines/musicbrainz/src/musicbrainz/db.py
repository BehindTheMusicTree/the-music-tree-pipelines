import adbc_driver_postgresql.dbapi as adbc
from common.env import require_env


def connect() -> adbc.Connection:
    host = require_env("MB_HOST")
    port = require_env("MB_PORT")
    db = require_env("MB_DB")
    user = require_env("MB_USER")
    return adbc.connect(f"postgresql://{user}@{host}:{port}/{db}?connect_timeout=3")
