from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from wikidata import ingest as bw

ROCK_ROW = {
    "item": "http://www.wikidata.org/entity/Q11399",
    "itemLabel": "rock music",
    "parent": "http://www.wikidata.org/entity/Q188451",
    "parentLabel": "music genre",
}
ROOT_ROW = {
    "item": "http://www.wikidata.org/entity/Q188451",
    "itemLabel": "music genre",
    "parent": None,
    "parentLabel": None,
}


def test_ingest_genre_tree_writes_parquet_with_qids_extracted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_query = MagicMock(return_value=[ROCK_ROW, ROOT_ROW])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    output_dir = tmp_path / "bronze"
    result = bw.ingest_genre_tree(output_dir)

    run_query.assert_called_once_with(bw.wikidata_client.GENRE_TREE_QUERY)
    assert result == output_dir / "wikidata_genre_tree.parquet"
    assert pl.read_parquet(result).to_dicts() == [
        {"item_id": "Q11399", "item_label": "rock music", "parent_id": "Q188451", "parent_label": "music genre"},
        {"item_id": "Q188451", "item_label": "music genre", "parent_id": None, "parent_label": None},
    ]


def test_ingest_genre_tree_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    output_dir = tmp_path / "does" / "not" / "exist"
    bw.ingest_genre_tree(output_dir)

    assert output_dir.is_dir()
