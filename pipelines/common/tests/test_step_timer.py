import json
import logging

import pytest

from common.step_timer import step


def test_step_logs_name_seconds_and_rss(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="common.step_timer"), step("demo"):
        pass
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage().startswith("step=demo seconds=")
    assert "max_rss_gb=" in caplog.records[0].getMessage()


def test_step_logs_when_block_raises(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="common.step_timer"), pytest.raises(ValueError, match="boom"):
        with step("failing"):
            raise ValueError("boom")
    assert caplog.records[0].getMessage().startswith("step=failing seconds=")


def test_step_appends_ndjson_record_when_timings_file_set(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    timings = tmp_path / "timings.ndjson"
    monkeypatch.setenv("PIPELINE_TIMINGS_FILE", str(timings))
    monkeypatch.setenv("PIPELINE_RUN_ID", "run-1")
    monkeypatch.setenv("PIPELINE_NAME", "gold")
    with step("a"):
        pass
    with pytest.raises(ValueError, match="boom"), step("b"):
        raise ValueError("boom")
    records = [json.loads(line) for line in timings.read_text().splitlines()]
    assert [(r["run_id"], r["pipeline"], r["step"]) for r in records] == [
        ("run-1", "gold", "a"),
        ("run-1", "gold", "b"),
    ]
    assert set(records[0]) == {"ts", "run_id", "pipeline", "step", "seconds", "max_rss_gb"}


@pytest.mark.parametrize("missing", ["PIPELINE_RUN_ID", "PIPELINE_NAME"])
def test_step_fails_fast_before_running_when_timing_env_incomplete(
    tmp_path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    timings = tmp_path / "timings.ndjson"
    monkeypatch.setenv("PIPELINE_TIMINGS_FILE", str(timings))
    monkeypatch.setenv("PIPELINE_RUN_ID", "run-1")
    monkeypatch.setenv("PIPELINE_NAME", "gold")
    monkeypatch.delenv(missing)
    ran = []
    with pytest.raises(Exception, match=missing), step("a"):
        ran.append(True)
    assert ran == []
    assert not timings.exists()


def test_step_keeps_the_step_exception_when_the_timing_write_fails(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_TIMINGS_FILE", str(tmp_path / "missing-dir" / "timings.ndjson"))
    monkeypatch.setenv("PIPELINE_RUN_ID", "run-1")
    monkeypatch.setenv("PIPELINE_NAME", "gold")
    with pytest.raises(ValueError, match="boom"), step("a"):
        raise ValueError("boom")


def test_step_raises_when_the_timing_write_fails_after_a_successful_step(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINE_TIMINGS_FILE", str(tmp_path / "missing-dir" / "timings.ndjson"))
    monkeypatch.setenv("PIPELINE_RUN_ID", "run-1")
    monkeypatch.setenv("PIPELINE_NAME", "gold")
    with pytest.raises(FileNotFoundError), step("a"):
        pass


def test_step_writes_no_file_without_timings_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPELINE_TIMINGS_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    with step("a"):
        pass
    assert list(tmp_path.iterdir()) == []
