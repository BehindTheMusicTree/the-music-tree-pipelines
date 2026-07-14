import os


def require_env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise KeyError(f"{name} is required — see this pipeline's .env.example") from None
