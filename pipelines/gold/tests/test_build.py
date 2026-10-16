import json
from collections.abc import Callable
from pathlib import Path

import pytest

from gold import build

EXPORTED_ON = "2026-10-16T08:00:00+00:00"


def _write_curation(curation_dir: Path, payload: dict[str, str]) -> None:
    curation_dir.mkdir()
    (curation_dir / "manual_export.json").write_text(json.dumps(payload))


def test_build_gold_writes_manifest_last(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    curation_dir, gold_dir = tmp_path / "curation", tmp_path / "gold"
    _write_curation(curation_dir, {"exportedOn": EXPORTED_ON})
    gold_dir.mkdir()
    calls: list[str] = []

    def fake_tree(name: str) -> Callable[[Path, Path, Path, str], Path]:
        def export(_silver: Path, _out: Path, _csv: Path, curation_exported_on: str) -> Path:
            assert curation_exported_on == EXPORTED_ON
            assert not (gold_dir / build.MANIFEST_NAME).exists()
            calls.append(name)
            return _out

        return export

    monkeypatch.setattr(build, "export_canonical_genre_tree", fake_tree("canonical"))
    monkeypatch.setattr(build, "export_regional_genre_tree", fake_tree("regional"))
    monkeypatch.setattr(build, "genre_match", lambda *_: calls.append("match") or gold_dir / "m.parquet")
    monkeypatch.setattr(build, "export_songs", lambda *_: calls.append("songs"))
    monkeypatch.setattr(build, "export_artists", lambda *_: calls.append("artists"))
    monkeypatch.setattr(build, "pipelines_commit", lambda: "abc123")

    path = build.build_gold(tmp_path / "mb", tmp_path / "wd", curation_dir, gold_dir)

    assert calls == ["canonical", "regional", "match", "songs", "artists"]
    manifest = json.loads(path.read_text())
    assert path == gold_dir / "_manifest.json"
    assert manifest["curationExportedOn"] == EXPORTED_ON
    assert manifest["pipelinesCommit"] == "abc123"
    assert manifest["builtOn"]
    assert [p.name for p in gold_dir.iterdir()] == ["_manifest.json"]


def test_build_gold_removes_stale_manifest_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    curation_dir, gold_dir = tmp_path / "curation", tmp_path / "gold"
    _write_curation(curation_dir, {"exportedOn": EXPORTED_ON})
    gold_dir.mkdir()
    (gold_dir / build.MANIFEST_NAME).write_text("{}")

    def failing_export(*_: object) -> Path:
        raise ValueError("boom")

    monkeypatch.setattr(build, "export_canonical_genre_tree", failing_export)

    with pytest.raises(ValueError, match="boom"):
        build.build_gold(tmp_path / "mb", tmp_path / "wd", curation_dir, gold_dir)

    assert not (gold_dir / build.MANIFEST_NAME).exists()


def test_build_gold_raises_without_curation_exported_on(tmp_path: Path) -> None:
    curation_dir = tmp_path / "curation"
    _write_curation(curation_dir, {})

    with pytest.raises(ValueError, match="exportedOn"):
        build.build_gold(tmp_path / "mb", tmp_path / "wd", curation_dir, tmp_path / "gold")


def test_pipelines_commit_is_a_sha() -> None:
    assert len(build.pipelines_commit()) == 40
