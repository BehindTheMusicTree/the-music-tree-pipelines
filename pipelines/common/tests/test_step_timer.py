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
