import logging
import resource
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

logger = logging.getLogger(__name__)

_KB_PER_GB = 1024 * 1024


@contextmanager
def step(name: str) -> Iterator[None]:
    """Logs `step=<name> seconds=<n> max_rss_gb=<n>` when the block ends, including when it raises.

    `ru_maxrss` is in KB on Linux (the VPS) and is the process-lifetime peak, so it only ever grows.
    """
    started = perf_counter()
    try:
        yield
    finally:
        max_rss_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _KB_PER_GB
        logger.info("step=%s seconds=%.1f max_rss_gb=%.2f", name, perf_counter() - started, max_rss_gb)
