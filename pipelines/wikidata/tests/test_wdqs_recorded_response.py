import json
from pathlib import Path

import httpx
import polars as pl
import pytest

from wikidata import ingest, wikidata_client

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GENID_IMAGE = "http://www.wikidata.org/.well-known/genid/4d48a18cd830fb0fe30ac52c4ad5f68b"


def _recorded_response(*, with_genid: bool) -> dict:
    # Recorded from WDQS: four FilePath rows, then the "unknown value" P18 that broke staging run a3997baf.
    response = json.loads((FIXTURES_DIR / "wdqs_artist_images_response.json").read_text())
    if not with_genid:
        response["results"]["bindings"] = [
            binding for binding in response["results"]["bindings"] if binding["image"]["value"] != GENID_IMAGE
        ]
    return response


def _serve(monkeypatch: pytest.MonkeyPatch, response: dict) -> None:
    def get(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json=response, request=httpx.Request("GET", url))

    monkeypatch.setattr(wikidata_client.httpx, "get", get)


def test_run_query_reads_a_recorded_wdqs_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _recorded_response(with_genid=True))

    rows = wikidata_client.run_query(
        wikidata_client.artist_image_query("0"), variables=wikidata_client.ARTIST_IMAGE_QUERY_VARIABLES
    )

    assert len(rows) == 5
    assert set(rows[0]) == set(wikidata_client.ARTIST_IMAGE_QUERY_VARIABLES)
    assert rows[-1]["image"] == GENID_IMAGE


def test_ingest_artist_images_decodes_recorded_commons_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _serve(monkeypatch, _recorded_response(with_genid=False))

    df = pl.read_parquet(ingest.ingest_artist_images(tmp_path))

    assert df.height == 4
    assert "Cocteau Twins, The Moon and the Melodies 1986 (cropped) (cropped).jpg" in df["image_file"].to_list()
    assert df["musicbrainz_artist_id"].str.contains(ingest.MBID_PATTERN).all()


def test_ingest_artist_images_rejects_a_recorded_unknown_value_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, _recorded_response(with_genid=True))

    with pytest.raises(ValueError, match="not a Commons file path"):
        ingest.ingest_artist_images(tmp_path)
