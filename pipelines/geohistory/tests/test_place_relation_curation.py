import pytest

from geohistory.silver.curation import read_place_relation_overrides


HEADER = "child_entity_id,parent_entity_id,extent,reason\n"


def test_missing_export_requires_refresh(tmp_path):
    with pytest.raises(FileNotFoundError, match="refresh curation Bronze"):
        read_place_relation_overrides(tmp_path)


def test_empty_and_valid_exports(tmp_path):
    path = tmp_path / "place_relation_overrides.csv"
    path.write_text(HEADER)
    assert read_place_relation_overrides(tmp_path) == {}
    path.write_text(HEADER + "Q1,Q2,partial,reviewed boundary\n")
    assert read_place_relation_overrides(tmp_path) == {("Q1", "Q2"): ("partial", "reviewed boundary")}


@pytest.mark.parametrize(
    "rows",
    [
        "Q1,Q1,full,reviewed\n",
        "Q1,Q2,unknown,reviewed\n",
        "Q1,Q2,full, \n",
        ",Q2,full,reviewed\n",
        "Q1,Q2,full,reviewed\nQ1,Q2,partial,reviewed\n",
    ],
)
def test_invalid_and_duplicate_decisions_fail(tmp_path, rows):
    (tmp_path / "place_relation_overrides.csv").write_text(HEADER + rows)
    with pytest.raises(ValueError, match="place relation override"):
        read_place_relation_overrides(tmp_path)
