import httpx
import tenacity

GEOJSON_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{version}/geojson/{name}.geojson"
ADMIN_0_LAYER = "ne_10m_admin_0_countries"
ADMIN_1_LAYER = "ne_10m_admin_1_states_provinces"


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(5),
    reraise=True,
)
def download_layer(version: str, name: str, timeout: float = 300.0) -> dict:
    response = httpx.get(GEOJSON_URL.format(version=version, name=name), follow_redirects=True, timeout=timeout)
    response.raise_for_status()
    return response.json()
