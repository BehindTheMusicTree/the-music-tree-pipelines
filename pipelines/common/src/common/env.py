import os
from pathlib import Path

from dotenv import load_dotenv


def load_pipeline_env(pipeline_package_file: str) -> None:
    """Load the .env at a pipeline's root, given a `__file__` inside `<pipeline>/src/<pkg>/...`."""
    pipeline_root = Path(pipeline_package_file).resolve().parents[2]
    load_dotenv(pipeline_root / ".env")


def require_env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise KeyError(f"{name} is required — see this pipeline's .env.example") from None
