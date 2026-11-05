import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from gold import build

EXPORTED_ON = "2026-10-16T08:00:00+00:00"
MBID_A = "00000000-0000-0000-0000-00000000000a"
MBID_B = "00000000-0000-0000-0000-00000000000b"


@pytest.fixture(autouse=True)
def image_snapshot(monkeypatch, tmp_path):
    snapshot = SimpleNamespace(
        snapshot_id="pinned",
        images_path=tmp_path / "immutable.parquet",
        manifest={
            "artifacts": {"artist_images.parquet": {"sha256": "digest"}},
            "producerCommit": "worker",
            "publishedAt": EXPORTED_ON,
            "discovery": {"coverage": "complete", "completedAt": EXPORTED_ON},
            "ageSummary": {},
        },
    )
    monkeypatch.setattr(build, "resolve_artist_image_snapshot", lambda *_: snapshot)
    return snapshot


def _write_curation(curation_dir: Path, payload: dict[str, str], essential_song_ids: list[str] | None = None) -> None:
    curation_dir.mkdir()
    (curation_dir / "export.json").write_text(json.dumps(payload))
    pl.DataFrame(
        {"musicbrainz_recording_id": essential_song_ids or []}, schema={"musicbrainz_recording_id": pl.Utf8}
    ).write_csv(curation_dir / "essential_songs.csv")


def _fake_genre_match(gold_dir: Path, calls: list[str]) -> Callable[..., Path]:
    def genre_match(*_: object) -> Path:
        calls.append("match")
        path = gold_dir / "m.parquet"
        pl.DataFrame({"musicbrainz_recording_id": [MBID_A]}).write_parquet(path)
        return path

    return genre_match


def test_build_gold_writes_manifest_last(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_snapshot) -> None:
    curation_dir, gold_dir = tmp_path / "curation", tmp_path / "gold"
    _write_curation(curation_dir, {"exportedOn": EXPORTED_ON})
    gold_dir.mkdir()
    calls: list[str] = []

    def fake_tree(name: str) -> Callable[..., Path]:
        def export(_silver: Path, _out: Path, *curation: object) -> Path:
            assert curation[-1] == EXPORTED_ON
            assert not (gold_dir / build.MANIFEST_NAME).exists()
            # A pointer change during export cannot change the already pinned input.
            monkeypatch.setattr(build, "resolve_artist_image_snapshot", lambda *_: pytest.fail("Resolved twice"))
            calls.append(name)
            return _out

        return export

    monkeypatch.setattr(build, "export_canonical_genre_tree", fake_tree("canonical"))
    monkeypatch.setattr(build, "export_regional_genre_tree", fake_tree("regional"))
    monkeypatch.setattr(build, "genre_match", _fake_genre_match(gold_dir, calls))
    monkeypatch.setattr(build, "export_umbrella_candidates", lambda *_: calls.append("umbrella_candidates"))
    monkeypatch.setattr(build, "export_songs", lambda *_: calls.append("songs"))

    def artists(_match, _mb, images, _output):
        assert images == image_snapshot.images_path
        calls.append("artists")

    monkeypatch.setattr(build, "export_artists", artists)
    monkeypatch.setattr(build, "export_genealogy", lambda *_: calls.append("genealogy"))
    monkeypatch.setattr(build, "export_region_map", lambda *_: calls.append("region_map"))
    monkeypatch.setattr(build, "pipelines_commit", lambda: "abc123")

    path = build.build_gold(
        tmp_path / "mb", tmp_path / "wd", tmp_path / "wb", tmp_path / "gh", curation_dir, gold_dir, tmp_path / "images"
    )
    (gold_dir / "m.parquet").unlink()

    assert calls == [
        "canonical",
        "regional",
        "match",
        "umbrella_candidates",
        "songs",
        "artists",
        "genealogy",
        "region_map",
    ]
    manifest = json.loads(path.read_text())
    assert path == gold_dir / "_manifest.json"
    assert manifest["curationExportedOn"] == EXPORTED_ON
    assert manifest["pipelinesCommit"] == "abc123"
    assert manifest["artistImagesSnapshot"]["snapshotId"] == "pinned"
    assert manifest["builtOn"]
    assert [p.name for p in gold_dir.iterdir()] == ["_manifest.json"]


def test_build_gold_raises_on_unknown_essential_track(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    curation_dir, gold_dir = tmp_path / "curation", tmp_path / "gold"
    _write_curation(curation_dir, {"exportedOn": EXPORTED_ON}, essential_song_ids=[MBID_A, MBID_B])
    gold_dir.mkdir()
    calls: list[str] = []
    monkeypatch.setattr(build, "export_canonical_genre_tree", lambda *_: None)
    monkeypatch.setattr(build, "export_regional_genre_tree", lambda *_: None)
    monkeypatch.setattr(build, "genre_match", _fake_genre_match(gold_dir, calls))
    monkeypatch.setattr(build, "export_umbrella_candidates", lambda *_: None)
    monkeypatch.setattr(build, "export_songs", lambda *_: calls.append("songs"))
    monkeypatch.setattr(build, "pipelines_commit", lambda: "abc123")

    with pytest.raises(ValueError, match=f"essential_songs.csv references unknown.*{MBID_B}"):
        build.build_gold(
            tmp_path / "mb",
            tmp_path / "wd",
            tmp_path / "wb",
            tmp_path / "gh",
            curation_dir,
            gold_dir,
            tmp_path / "images",
        )

    assert calls == ["match"]


def test_build_gold_removes_stale_manifest_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    curation_dir, gold_dir = tmp_path / "curation", tmp_path / "gold"
    _write_curation(curation_dir, {"exportedOn": EXPORTED_ON})
    gold_dir.mkdir()
    (gold_dir / build.MANIFEST_NAME).write_text("{}")

    def failing_export(*_: object) -> Path:
        raise ValueError("boom")

    monkeypatch.setattr(build, "export_canonical_genre_tree", failing_export)

    with pytest.raises(ValueError, match="boom"):
        build.build_gold(
            tmp_path / "mb",
            tmp_path / "wd",
            tmp_path / "wb",
            tmp_path / "gh",
            curation_dir,
            gold_dir,
            tmp_path / "images",
        )

    assert not (gold_dir / build.MANIFEST_NAME).exists()


def test_build_gold_raises_without_curation_exported_on(tmp_path: Path) -> None:
    curation_dir = tmp_path / "curation"
    _write_curation(curation_dir, {})

    with pytest.raises(ValueError, match="exportedOn"):
        build.build_gold(
            tmp_path / "mb",
            tmp_path / "wd",
            tmp_path / "wb",
            tmp_path / "gh",
            curation_dir,
            tmp_path / "gold",
            tmp_path / "images",
        )


def test_pipelines_commit_is_a_sha() -> None:
    assert len(build.pipelines_commit()) == 40


def test_invalid_image_snapshot_fails_before_exports(monkeypatch, tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / build.MANIFEST_NAME).write_text("{}")

    def invalid(*_):
        raise ValueError("damaged snapshot")

    monkeypatch.setattr(build, "resolve_artist_image_snapshot", invalid)
    monkeypatch.setattr(build, "export_canonical_genre_tree", lambda *_: pytest.fail("Export ran"))
    with pytest.raises(ValueError, match="damaged snapshot"):
        build.build_gold(*(tmp_path / name for name in ("mb", "wd", "wb", "gh", "curation", "gold", "images")))
    assert not (gold / build.MANIFEST_NAME).exists()
