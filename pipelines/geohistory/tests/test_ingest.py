import io
import zipfile
from pathlib import Path

import httpx
import polars as pl
import pytest

from geohistory import glottolog_client, ingest

LANGUAGES_CSV = (
    "ID,Name,Macroarea,Latitude,Longitude,Glottocode,ISO639P3code,Level,Countries,Family_ID,Language_ID,"
    "Closest_ISO369P3code,First_Year_Of_Documentation,Last_Year_Of_Documentation,Is_Isolate\n"
    "indo1319,Indo-European,Eurasia,45.0,10.0,indo1319,,family,,,,,,,\n"
    "bret1244,Breton,Eurasia,48.2452,-3.78934,bret1244,bre,language,,indo1319,,,1450,1990,\n"
    "unat1234,Unattested Tongue,,,,unat1234,,language,,,,,,,\n"
)
VALUES_CSV = (
    "ID,Language_ID,Parameter_ID,Value,Code_ID,Comment,Source,Codeset_ID\n"
    "indo1319-level,indo1319,level,family,level-family,,,\n"
    "indo1319-category,indo1319,category,Family,category-Family,,,\n"
    "bret1244-category,bret1244,category,Spoken_L1_Language,category-Spoken_L1_Language,,,\n"
    'bret1244-classification,bret1244,classification,"indo1319/celt1248/bret1245",,,,\n'
    "unat1234-category,unat1234,category,Unattested,category-Unattested,,,\n"
)


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"glottolog-cldf-5.3/cldf/{name}", content)
    return buffer.getvalue()


def test_read_cldf_table_returns_string_columns() -> None:
    table = glottolog_client.read_cldf_table(_archive({"languages.csv": LANGUAGES_CSV}), "languages.csv")

    assert table.height == 3
    assert set(table.schema.values()) == {pl.String}


def test_read_cldf_table_raises_on_missing_table() -> None:
    with pytest.raises(ValueError, match="values.csv"):
        glottolog_client.read_cldf_table(_archive({"languages.csv": LANGUAGES_CSV}), "values.csv")


def test_download_cldf_archive_follows_redirect_and_pins_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_get(url: str, follow_redirects: bool, timeout: float) -> httpx.Response:
        calls.append((url, follow_redirects))
        return httpx.Response(200, content=b"zip", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert glottolog_client.download_cldf_archive("v5.3") == b"zip"
    assert calls == [("https://github.com/glottolog/glottolog-cldf/archive/refs/tags/v5.3.zip", True)]


def test_build_languoids_pivots_values_and_types_columns() -> None:
    archive = _archive({"languages.csv": LANGUAGES_CSV, "values.csv": VALUES_CSV})
    df = ingest.build_languoids(
        glottolog_client.read_cldf_table(archive, "languages.csv"),
        glottolog_client.read_cldf_table(archive, "values.csv"),
        "v5.3",
    )

    breton = df.filter(pl.col("glottocode") == "bret1244").to_dicts()[0]
    assert breton == {
        "glottocode": "bret1244",
        "name": "Breton",
        "level": "language",
        "category": "Spoken_L1_Language",
        "classification": "indo1319/celt1248/bret1245",
        "latitude": 48.2452,
        "longitude": -3.78934,
        "macroarea": "Eurasia",
        "iso639_3": "bre",
        "first_year_of_documentation": 1450,
        "last_year_of_documentation": 1990,
        "glottolog_version": "v5.3",
    }
    assert df.filter(pl.col("glottocode") == "indo1319")["classification"].to_list() == [None]
    unattested = df.filter(pl.col("glottocode") == "unat1234").to_dicts()[0]
    assert unattested["latitude"] is None
    assert unattested["category"] == "Unattested"


def test_build_languoids_raises_when_a_parameter_is_absent() -> None:
    values = pl.DataFrame({"Language_ID": ["bret1244"], "Parameter_ID": ["category"], "Value": ["Spoken_L1_Language"]})
    languages = glottolog_client.read_cldf_table(_archive({"languages.csv": LANGUAGES_CSV}), "languages.csv")

    with pytest.raises(ValueError, match="classification"):
        ingest.build_languoids(languages, values, "v5.3")


def test_ingest_glottolog_languoids_writes_parquet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = _archive({"languages.csv": LANGUAGES_CSV, "values.csv": VALUES_CSV})
    monkeypatch.setattr(glottolog_client, "download_cldf_archive", lambda version: archive)

    path = ingest.ingest_glottolog_languoids("v5.3", tmp_path / "bronze")

    assert path == tmp_path / "bronze" / "glottolog_languoid.parquet"
    assert pl.read_parquet(path)["glottocode"].to_list() == ["bret1244", "indo1319", "unat1234"]


def test_ingest_glottolog_languoids_raises_on_duplicate_glottocode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    duplicated = LANGUAGES_CSV + "bret1244,Breton again,Eurasia,1.0,1.0,bret1244,,language,,,,,,,\n"
    archive = _archive({"languages.csv": duplicated, "values.csv": VALUES_CSV})
    monkeypatch.setattr(glottolog_client, "download_cldf_archive", lambda version: archive)

    with pytest.raises(ValueError, match="not unique"):
        ingest.ingest_glottolog_languoids("v5.3", tmp_path)
