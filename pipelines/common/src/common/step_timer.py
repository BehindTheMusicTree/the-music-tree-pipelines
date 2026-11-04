import json
import logging
import os
import resource
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from common.env import require_env

logger = logging.getLogger(__name__)

_KB_PER_GB = 1024 * 1024
TIMINGS_FILE_ENV = "PIPELINE_TIMINGS_FILE"


def _timing_target() -> tuple[Path, str, str] | None:
    timings_file = os.environ.get(TIMINGS_FILE_ENV)
    if not timings_file:
        return None
    return Path(timings_file), require_env("PIPELINE_RUN_ID"), require_env("PIPELINE_NAME")


def _append_timing(target: tuple[Path, str, str], name: str, seconds: float, max_rss_gb: float) -> None:
    path, run_id, pipeline = target
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_id": run_id,
        "pipeline": pipeline,
        "step": name,
        "seconds": round(seconds, 1),
        "max_rss_gb": round(max_rss_gb, 2),
    }
    # One short O_APPEND write per record: lines from the concurrent wikidata and musicbrainz chains never interleave.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(record) + "\n").encode())
    finally:
        os.close(fd)


@contextmanager
def step(name: str) -> Iterator[None]:
    """Logs `step=<name> seconds=<n> max_rss_gb=<n>` when the block ends, including when it raises.

    With `PIPELINE_TIMINGS_FILE` set (the daily run sets it), also appends one NDJSON record per step, so run
    times can be compared across releases without parsing journals. The run id and pipeline name are required then
    and checked before the step starts, so a misconfigured run fails at once, not after hours of work.

    `ru_maxrss` is in KB on Linux (the VPS) and is the process-lifetime peak, so it only ever grows.
    """
    target = _timing_target()
    started = perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        seconds = perf_counter() - started
        max_rss_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _KB_PER_GB
        logger.info("step=%s seconds=%.1f max_rss_gb=%.2f", name, seconds, max_rss_gb)
        if target is not None:
            try:
                _append_timing(target, name, seconds, max_rss_gb)
            except OSError:
                # A failed step's own exception must reach the caller; on success a lost timing is still an error.
                if not failed:
                    raise
                logger.exception("could not record timing for failed step %s", name)
