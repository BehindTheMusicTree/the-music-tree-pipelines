from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from wikidata.artist_images.state import ImageState, checkpoint_state, compact_state, load_state


def initial(root: Path) -> ImageState:
    return compact_state(
        root,
        ImageState(
            "",
            0,
            {
                "registry": pl.DataFrame({"item_id": ["Q1"], "status": ["has_image"]}),
                "claims": pl.DataFrame({"item_id": ["Q1"], "image_file": ["a.jpg"]}),
                "commons": pl.DataFrame({"image_file": ["a.jpg"], "status": ["usable"]}),
            },
            {"coverage": "bootstrap_seed"},
        ),
    )


def test_negative_claim_checkpoint_survives_restart_and_compaction(tmp_path: Path) -> None:
    state = initial(tmp_path)
    updated = checkpoint_state(
        tmp_path,
        state,
        {
            "registry": pl.DataFrame({"item_id": ["Q1"], "status": ["no_image"]}),
            "claims": state.tables["claims"].head(0),
        },
        {"registry": ["Q1"], "claims": ["Q1"]},
    )
    recovered = load_state(tmp_path)
    assert recovered.sequence == 1
    assert recovered.tables["claims"].is_empty()
    assert recovered.tables["registry"]["status"].to_list() == ["no_image"]
    assert recovered.tables["commons"].equals(state.tables["commons"])
    compacted = compact_state(tmp_path, updated)
    assert load_state(tmp_path).generation == compacted.generation
    assert load_state(tmp_path).tables["claims"].is_empty()
    assert (tmp_path / "base" / state.generation).exists()


def test_uncommitted_checkpoint_is_ignored(tmp_path: Path) -> None:
    state = initial(tmp_path)
    (tmp_path / "checkpoints" / ".checkpoint-interrupted").mkdir(parents=True)
    assert load_state(tmp_path).sequence == state.sequence


def test_corrupt_checkpoint_fails_instead_of_reusing_stale_state(tmp_path: Path) -> None:
    state = initial(tmp_path)
    checkpoint_state(tmp_path, state, {"claims": state.tables["claims"].head(0)}, {"claims": ["Q1"]})
    (tmp_path / "checkpoints" / "000000000001" / "claims.parquet").write_bytes(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        load_state(tmp_path)


def test_failed_compaction_keeps_previous_pointer(tmp_path: Path) -> None:
    state = initial(tmp_path)
    with patch("wikidata.artist_images.state.os.replace", side_effect=OSError("interrupted")):
        with pytest.raises(OSError, match="interrupted"):
            compact_state(tmp_path, state)
    assert load_state(tmp_path).generation == state.generation


def test_replacement_cannot_change_uncovered_qid(tmp_path: Path) -> None:
    state = initial(tmp_path)
    with pytest.raises(ValueError, match="outside coverage"):
        checkpoint_state(tmp_path, state, {"claims": state.tables["claims"]}, {"claims": ["Q2"]})
    assert load_state(tmp_path).sequence == 0
