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
        "genre of popular music", "Rock music", "https://en.wikipedia.org/wiki/Rock_music", ["rock", "rock & roll"]
    )
    fetch_descriptions = MagicMock(
        return_value={
            "Q1049": bw.wikidata_client.Description("music of a country", None, None, []),
            "Q11399": rock,
            "Q188451": bw.wikidata_client.Description(None, None, None, []),
            "Q9": bw.wikidata_client.Description(None, None, None, []),
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
    assert pl.read_parquet(tmp_path / "bronze" / "wikidata_genre_aliases.parquet").to_dicts() == [
        {"item_id": "Q11399", "alias": "rock"},
        {"item_id": "Q11399", "alias": "rock & roll"},
    ]


def test_ingest_descriptions_raises_when_no_item_has_an_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree_path, additions_path = _description_inputs(tmp_path, [])
    description = bw.wikidata_client.Description(None, None, None, [])
    monkeypatch.setattr(
        bw.wikidata_client, "fetch_descriptions", MagicMock(return_value={"Q9": description, "Q11399": description})
    )
    monkeypatch.setattr(bw.wikipedia_client, "fetch_intros", MagicMock(return_value={}))

    with pytest.raises(ValueError, match="wikidata_genre_aliases"):
        bw.ingest_descriptions(tree_path, additions_path, tmp_path / "bronze")


def test_ingest_descriptions_raises_on_duplicate_item_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tree_path, additions_path = _description_inputs(tmp_path, [])
    description = bw.wikidata_client.Description(None, None, None, [])
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
        "civilization": [_entity_row("Q6", "Maya", None)],
        "historical_country": [_entity_row("Q1", "Limburgish", None)],
        "country": [_entity_row("Q4", "Mexico", None)],
        "historical_region": [_entity_row("Q7", "Bohemia", None)],
        "ethnic_group": [_entity_row("Q8", "Berbers", None)],
        "cultural_region": [_entity_row("Q5", "Maghreb", None)],
        "macro_region": [_entity_row("Q9", "Caribbean", None)],
        "time_period": [_entity_row("Q3", "Bronze Age", None)],
        "curated": [_entity_row("Q10", "Oued Beht", None)],
    }
    kinds = iter(rows_by_kind)
    run_query = MagicMock(side_effect=lambda *args, **kwargs: rows_by_kind[next(kinds)])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    result = bw.ingest_cultural_entities(tmp_path / "bronze", bw.wikidata_client.curated_entity_patterns(["Q10"]))

    assert result == tmp_path / "bronze" / "wikidata_cultural_entity.parquet"
    assert pl.read_parquet(result).select("item_id", "kind", "glottocode").rows() == [
        ("Q2", "archaeological_culture", None),
        ("Q6", "civilization", None),
        ("Q4", "country", None),
        ("Q5", "cultural_region", None),
        ("Q10", "curated", None),
        ("Q8", "ethnic_group", None),
        ("Q1", "historical_country", None),
        ("Q7", "historical_region", None),
        ("Q1", "languoid", "limb1263"),
        ("Q1", "languoid", "limb1265"),
        ("Q9", "macro_region", None),
        ("Q3", "time_period", None),
    ]
    assert run_query.call_args.kwargs["timeout"] == bw.wikidata_client.CULTURAL_ENTITY_QUERY_TIMEOUT


def test_read_entity_additions_returns_the_listed_qids(tmp_path: Path) -> None:
    path = tmp_path / "manual_entity_additions.csv"
    path.write_text("entity_id,reason\nQ123559,al-Andalus\nQ141814080,Oued Beht\n")

    assert bw.read_entity_additions(path) == ["Q123559", "Q141814080"]


def test_ingest_cultural_entities_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_cultural_entities(tmp_path, bw.wikidata_client.CULTURAL_ENTITY_KIND_PATTERNS)


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

    result = bw.ingest_cultural_entity_claims(tmp_path, bw.wikidata_client.CULTURAL_ENTITY_KIND_PATTERNS)

    assert pl.read_parquet(result).rows() == [
        ("Q35505", "P1366", "Q294", None),
        ("Q153836", "P571", "-3500-01-01T00:00:00Z", 7),
    ]


def test_ingest_cultural_entity_claims_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_cultural_entity_claims(tmp_path, bw.wikidata_client.CULTURAL_ENTITY_KIND_PATTERNS)


def test_tradition_extra_qids_covers_parent_only_overviews_curated_overviews_and_additions(tmp_path: Path) -> None:
    tree = tmp_path / "tree.parquet"
    pl.DataFrame(
        {
            "item_id": ["Q1", "Q2", "Q3"],
            "parent_id": ["Q10", "Q11", "LOCAL:x"],
            "parent_label": ["music of Wales", "rock music", "music of nowhere"],
        }
    ).write_parquet(tree)
    overview_additions = tmp_path / "overviews.csv"
    overview_additions.write_text("item_id,label,reason\nQ20,music of Tuva,r\nLOCAL:y,music of Mars,r\n")

    assert bw.tradition_extra_qids(tree, overview_additions, ["Q30"]) == ["Q10", "Q20", "Q30"]


def test_read_tradition_additions_returns_the_listed_qids(tmp_path: Path) -> None:
    path = tmp_path / "additions.csv"
    path.write_text("wikidata_id,reason\nQ5,r\n")

    assert bw.read_tradition_additions(path) == ["Q5"]


def test_ingest_music_tradition_claims_keeps_literals_sitelinks_labels_and_qid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows_by_marker = {
        "p:P571 ": [{"item": "http://www.wikidata.org/entity/Q1", "value": "+1890-01-01T00:00:00Z", "precision": "9"}],
        "wdt:P2596 ": [
            {
                "item": "http://www.wikidata.org/entity/Q1",
                "value": "http://www.wikidata.org/entity/Q2",
                "precision": None,
            }
        ],
        "wikibase:sitelinks ?value": [{"item": "http://www.wikidata.org/entity/Q1", "value": "42", "precision": None}],
        "?item rdfs:label ?value": [{"item": "http://www.wikidata.org/entity/Q1", "value": "blues", "precision": None}],
    }

    def fake_run_query(query: str, **kwargs: object) -> list[dict[str, str | None]]:
        for marker, rows in rows_by_marker.items():
            if marker in query:
                return rows
        return []

    monkeypatch.setattr(bw.wikidata_client, "run_query", fake_run_query)

    result = bw.ingest_music_tradition_claims(tmp_path, bw.wikidata_client.tradition_candidate_patterns([]))

    assert pl.read_parquet(result).rows() == [
        ("Q1", "P2596", "Q2", None),
        ("Q1", "P571", "+1890-01-01T00:00:00Z", 9),
        ("Q1", "label", "blues", None),
        ("Q1", "sitelinks", "42", None),
    ]


def test_ingest_music_tradition_claims_raises_on_unbound_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unbound = [{"item": "http://www.wikidata.org/entity/Q1", "value": None, "precision": None}]
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=unbound))

    with pytest.raises(ValueError, match="'value' null rate"):
        bw.ingest_music_tradition_claims(tmp_path, bw.wikidata_client.tradition_candidate_patterns([]))


def test_ingest_music_tradition_claims_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError, match="is empty"):
        bw.ingest_music_tradition_claims(tmp_path, bw.wikidata_client.tradition_candidate_patterns([]))


def _image_row(qid: str, mbid: str, image: str) -> dict[str, str]:
    return {
        "item": f"http://www.wikidata.org/entity/{qid}",
        "mbid": mbid,
        "image": f"http://commons.wikimedia.org/wiki/Special:FilePath/{image}",
    }


RADIOHEAD = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


def test_ingest_artist_images_decodes_files_and_drops_malformed_mbids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = [
        _image_row("Q44190", f" {RADIOHEAD.upper()} ", "Radiohead%20%C3%A0%20Paris.jpg"),
        _image_row("Q44190", RADIOHEAD, "Radiohead%20%C3%A0%20Paris.jpg"),
        _image_row("Q1", "not-an-mbid", "Bad.jpg"),
    ]
    run_query = MagicMock(side_effect=lambda query, **_: rows if 'STRSTARTS(LCASE(?mbid), "a")' in query else [])
    monkeypatch.setattr(bw.wikidata_client, "run_query", run_query)

    result = bw.ingest_artist_images(tmp_path / "bronze")

    assert run_query.call_count == 16
    assert pl.read_parquet(result).to_dicts() == [
        {"item_id": "Q44190", "musicbrainz_artist_id": RADIOHEAD, "image_file": "Radiohead à Paris.jpg"}
    ]


def test_ingest_artist_images_raises_on_a_non_commons_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    row = {**_image_row("Q44190", RADIOHEAD, "x.jpg"), "image": "https://example.org/x.jpg"}
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[row]))

    with pytest.raises(ValueError, match="not a Commons file path"):
        bw.ingest_artist_images(tmp_path / "bronze")


def test_ingest_artist_images_raises_on_empty_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bw.wikidata_client, "run_query", MagicMock(return_value=[]))

    with pytest.raises(ValueError):
        bw.ingest_artist_images(tmp_path / "bronze")


def test_artist_image_query_skips_unknown_value_images() -> None:
    assert "isIRI(?image)" in bw.wikidata_client.artist_image_query("0")
