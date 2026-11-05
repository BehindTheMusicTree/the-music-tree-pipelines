from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from wikidata.artist_images.bootstrap import capture_inputs, prepare_bootstrap
from wikidata.silver.artist_images import select_artist_images


def inputs() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    claims = pl.DataFrame(
        {
            "item_id": ["Q2", "Q1"],
            "musicbrainz_artist_id": ["artist", "artist"],
            "image_file": ["a.jpg", "a.jpg"],
            "fetched_at": [datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)],
        }
    )
    metadata = pl.DataFrame(
        {
            "image_file": ["a.jpg"],
            "thumb_url": ["https://upload.wikimedia.org/a.jpg"],
            "page_url": ["https://commons.wikimedia.org/a.jpg"],
            "author": ["author"],
            "license": ["CC BY"],
            "license_url": [None],
            "attribution_required": [True],
            "non_free": [False],
            "checked_at": [datetime(2026, 1, 3, tzinfo=UTC)],
        },
        schema_overrides={"license_url": pl.String},
    )
    return claims, metadata, select_artist_images(claims, metadata)


def test_reproduces_images_and_selects_deterministic_lineage() -> None:
    claims, metadata, expected = inputs()
    selected, lineage = prepare_bootstrap(claims, metadata, expected)
    assert selected.equals(expected)
    assert lineage.row(0, named=True) == {
        "musicbrainz_artist_id": "artist",
        "item_id": "Q1",
        "image_file": "a.jpg",
        "claims_checked_at": datetime(2026, 1, 1, tzinfo=UTC),
        "metadata_checked_at": datetime(2026, 1, 3, tzinfo=UTC),
        "attribution_required": True,
    }


def test_missing_timestamps_remain_unknown() -> None:
    claims, metadata, expected = inputs()
    _, lineage = prepare_bootstrap(claims.drop("fetched_at"), metadata.drop("checked_at"), expected)
    assert lineage["claims_checked_at"].null_count() == 1
    assert lineage["metadata_checked_at"].null_count() == 1


def test_rejects_captured_output_from_a_different_generation() -> None:
    claims, metadata, expected = inputs()
    with pytest.raises(ValueError, match="do not match"):
        prepare_bootstrap(claims, metadata, expected.with_columns(pl.lit("other").alias("author")))


def test_registry_includes_unselected_qids_and_marks_them_due() -> None:
    from wikidata.artist_images.bootstrap import seed_registry

    claims, _, _ = inputs()
    registry = seed_registry(claims)
    assert registry["item_id"].to_list() == ["Q1", "Q2"]
    assert registry["claims_status"].to_list() == ["unfetched", "unfetched"]
    assert registry["claims_checked_at"].null_count() == 2
    assert registry["next_check_at"].null_count() == 2
    assert registry["first_seen_at"].to_list() == [
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
    ]
    assert registry["last_seen_in_discovery_at"].null_count() == 2
    assert registry["consecutive_failures"].to_list() == [0, 0]


def test_registry_rejects_invalid_qids() -> None:
    from wikidata.artist_images.bootstrap import seed_registry

    with pytest.raises(ValueError, match="QID"):
        seed_registry(pl.DataFrame({"item_id": ["Q1", "bad"]}))


def test_captures_validated_legacy_inputs(tmp_path: Path) -> None:
    claims, metadata, selected = inputs()
    bronze = tmp_path / "bronze"
    silver = tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    claims.write_parquet(bronze / "wikidata_artist_images.parquet")
    metadata.write_parquet(silver / "10_commons_image_metadata.parquet")
    selected.write_parquet(silver / "11_artist_images.parquet")
    lock = tmp_path / "catalogue.lock"
    lock.touch()
    destination = tmp_path / "state" / "capture"
    hashes = capture_inputs(bronze, silver, destination, lock)
    assert json.loads((destination / "manifest.json").read_text())["sha256"] == hashes
    for name, digest in hashes.items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        capture_inputs(bronze, silver, destination, lock)
    (bronze / "wikidata_artist_images.parquet").unlink()
    assert capture_inputs(bronze, silver, destination, lock, resume=True) == hashes
    (destination / "11_artist_images.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        capture_inputs(bronze, silver, destination, lock, resume=True)


def test_capture_failure_does_not_expose_partial_input(tmp_path: Path) -> None:
    claims, metadata, selected = inputs()
    bronze = tmp_path / "bronze"
    silver = tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    claims.write_parquet(bronze / "wikidata_artist_images.parquet")
    metadata.write_parquet(silver / "10_commons_image_metadata.parquet")
    selected.with_columns(pl.lit("wrong").alias("author")).write_parquet(silver / "11_artist_images.parquet")
    lock = tmp_path / "catalogue.lock"
    lock.touch()
    destination = tmp_path / "capture"
    with pytest.raises(ValueError, match="do not match"):
        capture_inputs(bronze, silver, destination, lock)
    assert not destination.exists()


def test_bootstrap_publishes_and_resumes_same_snapshot(tmp_path: Path) -> None:
    from wikidata.artist_images.bootstrap import bootstrap_snapshot
    from wikidata.artist_images.state import load_state

    claims, metadata, selected = inputs()
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    claims.write_parquet(bronze / "wikidata_artist_images.parquet")
    metadata.write_parquet(silver / "10_commons_image_metadata.parquet")
    selected.write_parquet(silver / "11_artist_images.parquet")
    lock = tmp_path / "catalogue.lock"
    lock.touch()
    state, published = tmp_path / "state", tmp_path / "published"
    snapshot = bootstrap_snapshot(bronze, silver, state, published, lock, "commit")
    assert pl.read_parquet(snapshot.images_path).equals(selected)
    assert load_state(state).tables["registry"]["claims_status"].to_list() == ["unfetched", "unfetched"]
    resumed = bootstrap_snapshot(bronze, silver, state, published, lock, "commit", resume=True)
    assert resumed.snapshot_id == snapshot.snapshot_id


def test_bootstrap_resume_after_publication_failure(tmp_path: Path) -> None:
    from unittest.mock import patch

    from wikidata.artist_images.bootstrap import bootstrap_snapshot
    from wikidata.artist_images.state import load_state

    claims, metadata, selected = inputs()
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    bronze.mkdir()
    silver.mkdir()
    claims.write_parquet(bronze / "wikidata_artist_images.parquet")
    metadata.write_parquet(silver / "10_commons_image_metadata.parquet")
    selected.write_parquet(silver / "11_artist_images.parquet")
    lock = tmp_path / "catalogue.lock"
    lock.touch()
    state, published = tmp_path / "state", tmp_path / "published"
    with patch("wikidata.artist_images.publish.publish_snapshot", side_effect=OSError("disk failure")):
        with pytest.raises(OSError, match="disk failure"):
            bootstrap_snapshot(bronze, silver, state, published, lock, "commit")
    generation = load_state(state).generation
    snapshot = bootstrap_snapshot(bronze, silver, state, published, lock, "commit", resume=True)
    assert snapshot.manifest["sourceStateGeneration"] == generation


def test_unknown_attribution_flag_with_author_requires_credit_in_lineage():
    from wikidata.artist_images.candidate import assemble_candidate
    from wikidata.artist_images.state import ImageState

    claims, metadata, expected = inputs()
    metadata = metadata.with_columns(pl.lit(None, dtype=pl.Boolean).alias("attribution_required"))
    selected, lineage = prepare_bootstrap(claims, metadata, expected)
    assert selected.equals(expected)
    assert lineage["attribution_required"].to_list() == [True]
    state = ImageState(
        "generation",
        0,
        {"claims": claims.rename({"fetched_at": "claims_checked_at"}), "commons": metadata},
        {"discovery": {"coverage": "complete", "completedAt": datetime.now(UTC).isoformat()}},
    )
    _, lineage, _ = assemble_candidate(state, datetime.now(UTC), "commit", {})
    assert lineage["attribution_required"].to_list() == [True]
