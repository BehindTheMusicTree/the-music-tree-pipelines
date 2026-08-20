import os
from pathlib import Path

from dotenv import load_dotenv


def _pipeline_root(pipeline_package_file: str) -> Path:
    return Path(pipeline_package_file).resolve().parents[2]


def load_pipeline_env(pipeline_package_file: str) -> None:
    """Load the .env at a pipeline's root, given a `__file__` inside `<pipeline>/src/<pkg>/...`."""
    load_dotenv(_pipeline_root(pipeline_package_file) / ".env")


def require_env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"{name} is required — see this pipeline's .env.example") from None


def resolve_pipeline_path(pipeline_package_file: str, value: str) -> Path:
    """Resolve a `.env` path value against a pipeline's root, given a `__file__` inside
    `<pipeline>/src/<pkg>/...`. Absolute values pass through unchanged, so production's
    absolute-path `.env` values are unaffected; relative values (dev convenience) resolve
    consistently regardless of the invoking process's current working directory."""
    path = Path(value)
    return path if path.is_absolute() else _pipeline_root(pipeline_package_file) / path
