from pathlib import Path

import pytest

from wikidata.artist_images.locks import LockDeferred, exclusive_lock


def test_competing_writer_defers_and_lock_is_released(tmp_path: Path) -> None:
    path = tmp_path / "writer.lock"
    with exclusive_lock(path):
        with pytest.raises(LockDeferred):
            with exclusive_lock(path):
                pytest.fail("A second writer entered the protected region")
    with exclusive_lock(path):
        pass
    assert path.exists()


def test_catalogue_lock_cannot_be_silently_created(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        with exclusive_lock(tmp_path / "absent.lock", existing=True):
            pytest.fail("An absent catalogue lock was accepted")


def test_inherited_lock_is_verified_and_keeps_other_writers_out(tmp_path):
    import fcntl

    path = tmp_path / "writer.lock"
    with path.open("a") as wrapper:
        fcntl.flock(wrapper, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with exclusive_lock(path, inherited_fd=wrapper.fileno()):
            with pytest.raises(LockDeferred):
                with exclusive_lock(path):
                    pytest.fail("Competing writer entered inherited lock")
        with exclusive_lock(path):
            pass
        wrong = tmp_path / "wrong.lock"
        wrong.touch()
        with pytest.raises(ValueError, match="descriptor"):
            with exclusive_lock(wrong, inherited_fd=wrapper.fileno()):
                pytest.fail("Wrong inherited descriptor accepted")
