import io
import zipfile

import httpx
import polars as pl
import tenacity

ARCHIVE_URL = "https://github.com/glottolog/glottolog-cldf/archive/refs/tags/{version}.zip"


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    wait=tenacity.wait_exponential(multiplier=1, max=30),
    stop=tenacity.stop_after_attempt(5),
    reraise=True,
)
def download_cldf_archive(version: str, timeout: float = 300.0) -> bytes:
    response = httpx.get(ARCHIVE_URL.format(version=version), follow_redirects=True, timeout=timeout)
    response.raise_for_status()
    return response.content


def read_cldf_table(archive: bytes, name: str) -> pl.DataFrame:
    """Reads `cldf/<name>` from the archive's single top-level folder, every column as a string."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        members = [member for member in zf.namelist() if member.endswith(f"/cldf/{name}")]
        if len(members) != 1:
            raise ValueError(f"expected exactly one cldf/{name} in the Glottolog archive, found {members}")
        return pl.read_csv(io.BytesIO(zf.read(members[0])), infer_schema=False)
