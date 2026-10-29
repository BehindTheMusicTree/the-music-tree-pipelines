import io
import zipfile
from pathlib import Path

import httpx
import polars as pl
import pytest
import shapely

from geohistory import glottolog_client, ingest, natural_earth_client

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


def _square(x: float) -> dict:
    return {"type": "Polygon", "coordinates": [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]]}


def _layer(*features: tuple[dict, dict | None]) -> dict:
    return {"type": "FeatureCollection", "features": [{"properties": p, "geometry": g} for p, g in features]}


ADMIN_0 = _layer(
    ({"ADM0_A3": "FRA", "WIKIDATAID": "Q142", "NAME": "France", "ISO_A2_EH": "FR", "TYPE": "Country"}, _square(0)),
)
ADMIN_1 = _layer(
    (
        {
            "adm1_code": "FRA-5327",
            "wikidataid": "Q12642",
            "adm0_a3": "FRA",
            "name": "Morbihan",
            "iso_3166_2": "FR-56",
            "type_en": "Metropolitan department",
        },
        _square(0),
    ),
    (
        {
            "adm1_code": "FRA-0000",
            "wikidataid": "",
            "adm0_a3": "FRA",
            "name": "Unknown",
            "iso_3166_2": "FR-XX",
            "type_en": None,
        },
        _square(0.5),
    ),
)


def test_download_layer_pins_version(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, follow_redirects: bool, timeout: float) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, json={"features": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert natural_earth_client.download_layer("v5.1.2", natural_earth_client.ADMIN_0_LAYER) == {"features": []}
    assert calls == [
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson"
    ]


def test_build_natural_earth_admin_flattens_both_levels() -> None:
    df = ingest.build_natural_earth_admin(ADMIN_0, ADMIN_1)

    assert df.drop("geometry_wkb").to_dicts() == [
        {
            "ne_id": "FRA",
            "level": 0,
            "wikidata_id": "Q142",
            "adm0_a3": "FRA",
            "name": "France",
            "iso_code": "FR",
            "ne_type": "Country",
        },
        {
            "ne_id": "FRA-0000",
            "level": 1,
            "wikidata_id": None,
            "adm0_a3": "FRA",
            "name": "Unknown",
            "iso_code": "FR-XX",
            "ne_type": None,
        },
        {
            "ne_id": "FRA-5327",
            "level": 1,
            "wikidata_id": "Q12642",
            "adm0_a3": "FRA",
            "name": "Morbihan",
            "iso_code": "FR-56",
            "ne_type": "Metropolitan department",
        },
    ]
    assert shapely.from_wkb(df["geometry_wkb"][0]).equals(shapely.geometry.shape(_square(0)))


def test_build_natural_earth_admin_raises_on_orphan_admin_1() -> None:
    orphan = _layer(({**ADMIN_1["features"][0]["properties"], "adm0_a3": "ESP"}, _square(0)))

    with pytest.raises(ValueError, match="FRA-5327"):
        ingest.build_natural_earth_admin(ADMIN_0, orphan)


def test_build_natural_earth_admin_raises_on_missing_geometry() -> None:
    with pytest.raises(ValueError, match="no geometry"):
        ingest.build_natural_earth_admin(ADMIN_0, _layer((ADMIN_1["features"][0]["properties"], None)))


def test_ingest_natural_earth_admin_writes_parquet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    layers = {natural_earth_client.ADMIN_0_LAYER: ADMIN_0, natural_earth_client.ADMIN_1_LAYER: ADMIN_1}
    monkeypatch.setattr(natural_earth_client, "download_layer", lambda version, name: layers[name])

    path = ingest.ingest_natural_earth_admin("v5.1.2", tmp_path / "bronze")

    assert path == tmp_path / "bronze" / "natural_earth_admin.parquet"
    assert pl.read_parquet(path)["ne_id"].to_list() == ["FRA", "FRA-0000", "FRA-5327"]


def test_ingest_natural_earth_admin_raises_on_duplicate_ne_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    duplicated = _layer((ADMIN_1["features"][0]["properties"], _square(0)))
    duplicated["features"] *= 2
    layers = {natural_earth_client.ADMIN_0_LAYER: ADMIN_0, natural_earth_client.ADMIN_1_LAYER: duplicated}
    monkeypatch.setattr(natural_earth_client, "download_layer", lambda version, name: layers[name])

    with pytest.raises(ValueError, match="not unique"):
        ingest.ingest_natural_earth_admin("v5.1.2", tmp_path)
