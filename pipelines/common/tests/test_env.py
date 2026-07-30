from pathlib import Path

import pytest

from common.env import load_pipeline_env, require_env


def test_require_env_returns_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_VAR", "some-value")
    assert require_env("SOME_VAR") == "some-value"


def test_require_env_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(RuntimeError, match="MISSING_VAR is required"):
        require_env("MISSING_VAR")


def test_load_pipeline_env_loads_dotenv_from_pipeline_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pipeline_root = tmp_path / "some_pipeline"
    package_file = pipeline_root / "src" / "some_pipeline" / "module.py"
    package_file.parent.mkdir(parents=True)
    package_file.touch()

    loaded_paths = []
    monkeypatch.setattr("common.env.load_dotenv", lambda path: loaded_paths.append(path))

    load_pipeline_env(str(package_file))

    assert loaded_paths == [pipeline_root / ".env"]
