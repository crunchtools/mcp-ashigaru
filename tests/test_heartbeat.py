"""Tests for heartbeat watchdog."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.heartbeat import _tick


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    return Config(
        state_dir=state_dir,
        notify_webhook="http://kagetora:8644/webhooks/ashigaru-heartbeat",
        notify_webhook_secret="test-secret",
    )


ACTIVE_RUN = {
    "run_id": "rotv-5-123456",
    "repo": "rotv",
    "issue": 5,
    "title": "Fix auth header",
    "phase": "implementing",
    "pr_url": None,
}

TERMINAL_RUN = {
    "run_id": "rotv-4-999999",
    "repo": "rotv",
    "issue": 4,
    "title": "Old run",
    "phase": "shipped",
    "pr_url": "https://github.com/crunchtools/rotv/pull/10",
}


async def test_tick_skips_terminal_runs(cfg: Config) -> None:
    with patch("mcp_ashigaru.heartbeat.RunState.list_all", return_value=[ACTIVE_RUN, TERMINAL_RUN]):
        with patch("mcp_ashigaru.heartbeat._send_webhook") as mock_send:
            await _tick(cfg)
            assert mock_send.call_count == 1
            _, _, msg = mock_send.call_args[0]
            assert "rotv-5-123456" in msg
            assert "heartbeat" in msg


async def test_tick_skips_when_no_webhook(tmp_path: Path) -> None:
    cfg = Config(state_dir=tmp_path / "ashigaru")
    with patch("mcp_ashigaru.heartbeat.RunState.list_all") as mock_list:
        await _tick(cfg)
        mock_list.assert_not_called()


async def test_tick_continues_after_single_run_failure(cfg: Config) -> None:
    """A webhook failure on one run must not stop heartbeats for the others."""
    run_a = {**ACTIVE_RUN, "run_id": "rotv-1-aaa"}
    run_b = {**ACTIVE_RUN, "run_id": "rotv-2-bbb"}

    call_count = 0

    def _flaky_send(url: str, secret: str, message: str) -> None:
        nonlocal call_count
        call_count += 1
        if "rotv-1-aaa" in message:
            raise OSError("connection refused")

    with patch("mcp_ashigaru.heartbeat.RunState.list_all", return_value=[run_a, run_b]):
        with patch("mcp_ashigaru.heartbeat._send_webhook", side_effect=_flaky_send):
            await _tick(cfg)
            assert call_count == 2
