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
    "relation": "P279",
}
ROOT_ROW = {
    "item": "http://www.wikidata.org/entity/Q188451",
    "itemLabel": "music genre",
    "parent": None,
    "parentLabel": None,
    "relation": None,
}


def test_ingest_genre_tree_writes_parquet_with_qids_extracted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_query = MagicMock(return_value=[ROCK_ROW, ROOT_ROW])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    output_dir = tmp_path / "bronze"
    result = bw.ingest_genre_tree(output_dir)

    run_query.assert_called_once_with(bw.wikidata_client.GENRE_TREE_QUERY)
    assert result == output_dir / "wikidata_genre_tree.parquet"
    assert pl.read_parquet(result).to_dicts() == [
        {
            "item_id": "Q11399",
            "item_label": "rock music",
            "parent_id": "Q188451",
            "parent_label": "music genre",
            "relation_type": "P279",
        },
        {
            "item_id": "Q188451",
            "item_label": "music genre",
            "parent_id": None,
            "parent_label": None,
            "relation_type": None,
        },
    ]


def test_ingest_genre_tree_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    output_dir = tmp_path / "does" / "not" / "exist"
    bw.ingest_genre_tree(output_dir)

    assert output_dir.is_dir()


INDIGENOUS_TO_ROW = {
    "item": "http://www.wikidata.org/entity/Q10376827",
    "indigenousTo": "http://www.wikidata.org/entity/Q49103",
    "indigenousToLabel": "Han Chinese",
}


def test_ingest_indigenous_to_writes_parquet_with_qids_extracted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_query = MagicMock(return_value=[INDIGENOUS_TO_ROW])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    output_dir = tmp_path / "bronze"
    result = bw.ingest_indigenous_to(output_dir)

    run_query.assert_called_once_with(
        bw.wikidata_client.INDIGENOUS_TO_QUERY, variables=bw.wikidata_client.INDIGENOUS_TO_QUERY_VARIABLES
    )
    assert result == output_dir / "wikidata_genre_indigenous_to.parquet"
    assert pl.read_parquet(result).to_dicts() == [
        {
            "item_id": "Q10376827",
            "indigenous_to_id": "Q49103",
            "indigenous_to_label": "Han Chinese",
        }
    ]


def test_ingest_indigenous_to_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    output_dir = tmp_path / "does" / "not" / "exist"
    bw.ingest_indigenous_to(output_dir)

    assert output_dir.is_dir()


COUNTRY_OF_ORIGIN_ROW = {
    "item": "http://www.wikidata.org/entity/Q1198131",
    "countryOfOrigin": "http://www.wikidata.org/entity/Q1011",
    "countryOfOriginLabel": "Cape Verde",
}


def test_ingest_country_of_origin_writes_parquet_with_qids_extracted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_query = MagicMock(return_value=[COUNTRY_OF_ORIGIN_ROW])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    output_dir = tmp_path / "bronze"
    result = bw.ingest_country_of_origin(output_dir)

    run_query.assert_called_once_with(
        bw.wikidata_client.COUNTRY_OF_ORIGIN_QUERY, variables=bw.wikidata_client.COUNTRY_OF_ORIGIN_QUERY_VARIABLES
    )
    assert result == output_dir / "wikidata_genre_country_of_origin.parquet"
    assert pl.read_parquet(result).to_dicts() == [
        {
            "item_id": "Q1198131",
            "country_of_origin_id": "Q1011",
            "country_of_origin_label": "Cape Verde",
        }
    ]


def test_ingest_country_of_origin_creates_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    output_dir = tmp_path / "does" / "not" / "exist"
    bw.ingest_country_of_origin(output_dir)

    assert output_dir.is_dir()
