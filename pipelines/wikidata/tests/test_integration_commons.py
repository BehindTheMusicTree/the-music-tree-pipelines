import httpx
import pytest

from wikidata.silver import commons_image_metadata as cim
from wikidata.wikidata_client import USER_AGENT


@pytest.mark.integration
def test_fetch_batch_reads_license_and_author_from_the_live_api() -> None:
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        [row] = cim._fetch_batch(client, ["RadioheadO2211125 composite.jpg"])

    assert row["thumb_url"].endswith("/500px-RadioheadO2211125_composite.jpg")
    assert "/archive/" not in row["thumb_url"]
    assert row["page_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    assert (row["author"], row["license"]) == ("Raph_PH", "CC BY 4.0")
    assert row["non_free"] is False
