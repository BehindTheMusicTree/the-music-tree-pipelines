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


def test_ingest_genre_tree_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_genre_tree(tmp_path / "does" / "not" / "exist")


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


def test_ingest_indigenous_to_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_indigenous_to(tmp_path / "does" / "not" / "exist")


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


def test_ingest_country_of_origin_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_country_of_origin(tmp_path / "does" / "not" / "exist")


def _description_inputs(tmp_path: Path, additions: list[dict[str, str]]) -> tuple[Path, Path]:
    tree_path = tmp_path / "wikidata_genre_tree.parquet"
    pl.DataFrame(
        {
            "item_id": ["Q11399", "Q188451", "Q9"],
            "parent_id": ["Q188451", None, "Q11399"],
        }
    ).write_parquet(tree_path)
    additions_path = tmp_path / "manual_regional_overview_additions.csv"
    pl.DataFrame(additions, schema={"item_id": pl.Utf8, "item_label": pl.Utf8, "reason": pl.Utf8}).write_csv(
        additions_path
    )
    return tree_path, additions_path


def test_ingest_descriptions_covers_tree_and_overview_qids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree_path, additions_path = _description_inputs(
        tmp_path,
        [
            {"item_id": "Q1049", "item_label": "music of France", "reason": "x"},
            {"item_id": "LOCAL:music-of-x", "item_label": "music of X", "reason": "x"},
        ],
    )
    rock = bw.wikidata_client.Description(
        "genre of popular music", "Rock music", "https://en.wikipedia.org/wiki/Rock_music"
    )
    fetch_descriptions = MagicMock(
        return_value={
            "Q1049": bw.wikidata_client.Description("music of a country", None, None),
            "Q11399": rock,
            "Q188451": bw.wikidata_client.Description(None, None, None),
            "Q9": bw.wikidata_client.Description(None, None, None),
        }
    )
    monkeypatch.setattr(bw.wikidata_client, "fetch_descriptions", fetch_descriptions)
    fetch_intros = MagicMock(return_value={"Rock music": "Rock is a genre."})
    monkeypatch.setattr(bw.wikipedia_client, "fetch_intros", fetch_intros)

    result = bw.ingest_descriptions(tree_path, additions_path, tmp_path / "bronze")

    fetch_descriptions.assert_called_once_with(["Q1049", "Q11399", "Q188451", "Q9"])
    fetch_intros.assert_called_once_with(["Rock music"])
    assert result == tmp_path / "bronze" / "wikidata_genre_descriptions.parquet"
    rows = {row["item_id"]: row for row in pl.read_parquet(result).to_dicts()}
    assert rows["Q1049"]["description"] == "music of a country"
    assert rows["Q11399"] == {
        "item_id": "Q11399",
        "description": "genre of popular music",
        "wikipedia_url": "https://en.wikipedia.org/wiki/Rock_music",
        "wikipedia_intro": "Rock is a genre.",
    }
    assert rows["Q9"] == {"item_id": "Q9", "description": None, "wikipedia_url": None, "wikipedia_intro": None}


def test_ingest_descriptions_raises_on_duplicate_item_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree_path, additions_path = _description_inputs(tmp_path, [])
    description = bw.wikidata_client.Description(None, None, None)
    monkeypatch.setattr(
        bw.wikidata_client, "fetch_descriptions", MagicMock(return_value={"Q9": description, "Q11399": description})
    )
    monkeypatch.setattr(bw.wikipedia_client, "fetch_intros", MagicMock(return_value={}))
    real_check = bw.check_unique_key

    def check_on_duplicated(df: pl.DataFrame, column: str, name: str) -> None:
        real_check(df.with_columns(pl.lit("Q9").alias("item_id")), column, name)

    monkeypatch.setattr(bw, "check_unique_key", check_on_duplicated)

    with pytest.raises(ValueError, match="'item_id' is not unique"):
        bw.ingest_descriptions(tree_path, additions_path, tmp_path / "bronze")
    assert not (tmp_path / "bronze").exists()


def _entity_row(qid: str, label: str, glottocode: str | None) -> dict[str, str | None]:
    return {"item": f"http://www.wikidata.org/entity/{qid}", "itemLabel": label, "glottocode": glottocode}


def test_ingest_cultural_entities_keeps_one_row_per_item_kind_and_glottocode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows_by_kind = {
        "languoid": [_entity_row("Q1", "Limburgish", "limb1263"), _entity_row("Q1", "Limburgish", "limb1265")],
        "archaeological_culture": [_entity_row("Q2", "Caral", None), _entity_row("Q2", "Caral", None)],
        "historical_country": [_entity_row("Q1", "Limburgish", None)],
        "time_period": [_entity_row("Q3", "Bronze Age", None)],
    }
    kinds = iter(rows_by_kind)
    run_query = MagicMock(side_effect=lambda *args, **kwargs: rows_by_kind[next(kinds)])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    result = bw.ingest_cultural_entities(tmp_path / "bronze")

    assert result == tmp_path / "bronze" / "wikidata_cultural_entity.parquet"
    assert pl.read_parquet(result).select("item_id", "kind", "glottocode").rows() == [
        ("Q2", "archaeological_culture", None),
        ("Q1", "historical_country", None),
        ("Q1", "languoid", "limb1263"),
        ("Q1", "languoid", "limb1265"),
        ("Q3", "time_period", None),
    ]
    assert run_query.call_args.kwargs["timeout"] == bw.wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT


def test_ingest_cultural_entities_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_cultural_entities(tmp_path)


def test_ingest_cultural_entity_claims_keeps_bce_literals_precision_and_qid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows_by_pid = {
        "P571": [
            {"item": "http://www.wikidata.org/entity/Q153836", "value": "-3500-01-01T00:00:00Z", "precision": "7"}
        ],
        "P1366": [
            {
                "item": "http://www.wikidata.org/entity/Q35505",
                "value": "http://www.wikidata.org/entity/Q294",
                "precision": None,
            }
        ],
    }

    def fake_run_query(query: str, **kwargs: object) -> list[dict[str, str | None]]:
        for pid, rows in rows_by_pid.items():
            if f"p:{pid} " in query or f"wdt:{pid} " in query:
                return rows
        return []

    monkeypatch.setattr(bw.wikidata_client, "run_query", fake_run_query)

    result = bw.ingest_cultural_entity_claims(tmp_path)

    assert pl.read_parquet(result).rows() == [
        ("Q35505", "P1366", "Q294", None),
        ("Q153836", "P571", "-3500-01-01T00:00:00Z", 7),
    ]


def test_ingest_cultural_entity_claims_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_cultural_entity_claims(tmp_path)
