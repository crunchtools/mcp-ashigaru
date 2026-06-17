"""Tests for notification callback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta
from mcp_ashigaru.notify import _format_message, send_notification


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(state_dir=state_dir, notify_cmd="hermes send --to signal:+15551234567")


def _make_meta(phase: Phase, **kwargs) -> RunMeta:
    defaults = dict(
        run_id="rotv-5-123456",
        repo="rotv",
        issue=5,
        title="Fix auth header",
        branch="fix/issue-5",
        phase=phase,
    )
    defaults.update(kwargs)
    return RunMeta(**defaults)


def test_format_success() -> None:
    meta = _make_meta(Phase.AWAITING_REVIEW, pr_url="https://github.com/crunchtools/rotv/pull/42")
    msg = _format_message(meta)
    assert "completed" in msg
    assert "rotv-5-123456" in msg
    assert "pull/42" in msg


def test_format_failed() -> None:
    meta = _make_meta(Phase.FAILED, failure_reason="pytest exit code 1 — 3 tests failed")
    msg = _format_message(meta)
    assert "failed" in msg
    assert "pytest" in msg


def test_format_escalated() -> None:
    meta = _make_meta(Phase.ESCALATED)
    msg = _format_message(meta)
    assert "escalated" in msg
    assert "tiers exhausted" in msg


def test_format_failed_no_reason() -> None:
    meta = _make_meta(Phase.FAILED)
    msg = _format_message(meta)
    assert "unknown error" in msg


@pytest.mark.asyncio
async def test_send_skipped_when_no_cmd(tmp_path: Path) -> None:
    cfg = Config(
        state_dir=tmp_path / "ashigaru",
        notify_cmd="",
    )
    meta = _make_meta(Phase.FAILED)
    with patch("mcp_ashigaru.notify.asyncio.create_subprocess_exec") as mock_exec:
        await send_notification(meta, cfg)
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_send_calls_command(cfg: Config) -> None:
    meta = _make_meta(Phase.AWAITING_REVIEW, pr_url="https://github.com/crunchtools/rotv/pull/42")
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    with patch("mcp_ashigaru.notify.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await send_notification(meta, cfg)
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "hermes"
        assert args[1] == "send"
        assert "completed" in args[-1]
