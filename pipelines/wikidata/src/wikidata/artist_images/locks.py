"""Nonblocking Linux flock coordination with the existing catalogue writer."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class LockDeferred(RuntimeError):
    """Another scheduled job currently owns a required lock."""


@contextmanager
def exclusive_lock(path: Path, *, existing: bool = False, shared: bool = False, inherited_fd: int | None = None):
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError("Lock path must be absolute with an existing parent")
    if existing and not path.is_file():
        raise ValueError("Required catalogue lock does not exist")
    if inherited_fd is not None:
        actual, expected = os.fstat(inherited_fd), path.stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("Inherited descriptor does not identify the configured lock")
    with os.fdopen(os.dup(inherited_fd), "a") if inherited_fd is not None else path.open("a") as lock:
        os.set_inheritable(lock.fileno(), False)
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockDeferred(f"Lock contention: {path.name}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
