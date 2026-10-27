import json
from pathlib import Path

import polars as pl
import pytest

from gold.genealogy_export import export_genealogy


def _write_silver(tmp_path: Path) -> tuple[Path, Path]:
    geohistory_dir, wikidata_dir = tmp_path / "geohistory", tmp_path / "wikidata"
    geohistory_dir.mkdir()
    wikidata_dir.mkdir()
    pl.DataFrame(
        {
            "wikidata_id": ["Q2", "Q1"],
            "label": ["blues", "gospel"],
            "start_year": [1890, None],
            "end_year": [1950, 1950],
            "anchor_id": ["aaaa1234", None],
            "anchor_rule": ["P495", None],
        },
        schema_overrides={"start_year": pl.Int32, "end_year": pl.Int32},
    ).write_parquet(geohistory_dir / "5_tradition.parquet")
    pl.DataFrame({"from_wikidata_id": ["Q1"], "to_wikidata_id": ["Q2"], "kind": ["lineage"]}).write_parquet(
        geohistory_dir / "5_tradition_link.parquet"
    )
    pl.DataFrame(
        {
            "entity_id": ["aaaa1234", "bbbb1234"],
            "label": ["Mississippi", "Delta"],
            "column_id": ["c0", "c0"],
            "start_year": [1800, 1900],
            "end_year": [1899, 1950],
        },
        schema_overrides={"start_year": pl.Int32, "end_year": pl.Int32},
    ).write_parquet(geohistory_dir / "6_genealogy_lane.parquet")
    pl.DataFrame(
        {
            "entity_id": ["root0000"],
            "label": [
                "South",
            ],
            "fragment": [0],
            "start_position": [0],
            "end_position": [0],
            "start_year": [1700],
            "end_year": [1950],
        },
        schema_overrides={"fragment": pl.Int32, "start_position": pl.Int32, "end_position": pl.Int32},
    ).write_parquet(geohistory_dir / "7_band_fragment.parquet")
    pl.DataFrame({"column_id": ["c0"], "position": [0]}, schema_overrides={"position": pl.Int32}).write_parquet(
        geohistory_dir / "7_lane_order.parquet"
    )
    pl.DataFrame(
        {
            "predecessor_id": ["aaaa1234", "aaaa1234"],
            "successor_id": ["bbbb1234", "zzzz0000"],
        }
    ).write_parquet(geohistory_dir / "3_entity_succession.parquet")
    pl.DataFrame({"item_id": ["Q2"]}).write_parquet(wikidata_dir / "7_canonical_hierarchy.parquet")
    return geohistory_dir, wikidata_dir


def test_export_genealogy_writes_document(tmp_path: Path) -> None:
    geohistory_dir, wikidata_dir = _write_silver(tmp_path)

    path = export_genealogy(geohistory_dir, wikidata_dir, tmp_path / "gold")

    document = json.loads(path.read_text())
    assert path.name == "4_genealogy.json"
    assert document["columns"] == [
        {
            "id": "c0",
            "position": 0,
            "lanes": [
                {"entityId": "aaaa1234", "label": "Mississippi", "startYear": 1800, "endYear": 1899},
                {"entityId": "bbbb1234", "label": "Delta", "startYear": 1900, "endYear": 1950},
            ],
        }
    ]
    assert document["bands"][0]["entityId"] == "root0000"
    assert document["successions"] == [{"predecessorId": "aaaa1234", "successorId": "bbbb1234"}]
    assert document["traditions"] == [
        {"wikidataId": "Q1", "label": "gospel", "start": None, "end": 1950, "anchorId": None, "inCanonicalTree": False},
        {
            "wikidataId": "Q2",
            "label": "blues",
            "start": 1890,
            "end": 1950,
            "anchorId": "aaaa1234",
            "inCanonicalTree": True,
        },
    ]
    assert document["traditionLinks"] == [{"sourceId": "Q1", "targetId": "Q2", "kind": "lineage"}]


def test_export_genealogy_raises_without_traditions(tmp_path: Path) -> None:
    geohistory_dir, wikidata_dir = _write_silver(tmp_path)
    pl.read_parquet(geohistory_dir / "5_tradition.parquet").clear().write_parquet(
        geohistory_dir / "5_tradition.parquet"
    )

    with pytest.raises(ValueError, match="0 tradition"):
        export_genealogy(geohistory_dir, wikidata_dir, tmp_path / "gold")


def test_export_genealogy_raises_on_column_without_lanes(tmp_path: Path) -> None:
    geohistory_dir, wikidata_dir = _write_silver(tmp_path)
    pl.DataFrame(
        {"column_id": ["c0", "c9"], "position": [0, 1]}, schema_overrides={"position": pl.Int32}
    ).write_parquet(geohistory_dir / "7_lane_order.parquet")

    with pytest.raises(ValueError, match="without lanes"):
        export_genealogy(geohistory_dir, wikidata_dir, tmp_path / "gold")


def test_export_genealogy_raises_on_schema_violation(tmp_path: Path) -> None:
    geohistory_dir, wikidata_dir = _write_silver(tmp_path)
    pl.read_parquet(geohistory_dir / "5_tradition.parquet").with_columns(
        pl.lit("not-a-qid").alias("wikidata_id")
    ).write_parquet(geohistory_dir / "5_tradition.parquet")

    with pytest.raises(ValueError, match="failed schema validation"):
        export_genealogy(geohistory_dir, wikidata_dir, tmp_path / "gold")
