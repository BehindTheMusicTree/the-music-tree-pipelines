from pathlib import Path

import pytest

from common.env import load_pipeline_env, require_env, resolve_pipeline_path


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


def test_resolve_pipeline_path_resolves_relative_value_against_pipeline_root(tmp_path: Path) -> None:
    pipeline_root = tmp_path / "some_pipeline"
    package_file = pipeline_root / "src" / "some_pipeline" / "module.py"
    package_file.parent.mkdir(parents=True)
    package_file.touch()

    assert resolve_pipeline_path(str(package_file), "bronze") == pipeline_root / "bronze"


def test_resolve_pipeline_path_passes_through_absolute_value(tmp_path: Path) -> None:
    pipeline_root = tmp_path / "some_pipeline"
    package_file = pipeline_root / "src" / "some_pipeline" / "module.py"
    package_file.parent.mkdir(parents=True)
    package_file.touch()

    absolute_value = str(tmp_path / "elsewhere" / "bronze")
    assert resolve_pipeline_path(str(package_file), absolute_value) == Path(absolute_value)
