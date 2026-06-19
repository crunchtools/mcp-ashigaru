"""Tests for phase change and heartbeat notifications."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta
from mcp_ashigaru.notify import _format_heartbeat, fire_phase_change, send_heartbeat


@pytest.fixture
def webhook_cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(
        state_dir=state_dir,
        notify_webhook="http://kagetora:8644/webhooks/ashigaru-complete",
        notify_webhook_secret="test-secret",
    )


@pytest.fixture
def matrix_cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(
        state_dir=state_dir,
        matrix_homeserver="https://matrix.org",
        matrix_access_token="test-token",
        matrix_room_id="!test:matrix.org",
        matrix_mention_user="@kagetora:matrix.org",
    )


@pytest.fixture
def both_cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(
        state_dir=state_dir,
        notify_webhook="http://kagetora:8644/webhooks/ashigaru-complete",
        notify_webhook_secret="test-secret",
        matrix_homeserver="https://matrix.org",
        matrix_access_token="test-token",
        matrix_room_id="!test:matrix.org",
        matrix_mention_user="@kagetora:matrix.org",
    )


@pytest.fixture
def mock_matrix_notifier():
    mock = AsyncMock()
    mock.send = AsyncMock()
    with patch("mcp_ashigaru.notify._matrix_notifier", mock):
        yield mock


def _make_meta(phase: Phase, **kwargs) -> RunMeta:
    defaults = {
        "run_id": "rotv-5-123456",
        "repo": "rotv",
        "issue": 5,
        "title": "Fix auth header",
        "branch": "fix/issue-5",
        "phase": phase,
        **kwargs,
    }
    return RunMeta(**defaults)


def test_fire_phase_change_formats_message(webhook_cfg: Config) -> None:
    meta = _make_meta(Phase.ANALYZING)
    with patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop:
        mock_task = mock_loop.return_value.create_task
        fire_phase_change(meta, Phase.QUEUED, Phase.ANALYZING, webhook_cfg)
        mock_task.assert_called_once()


def test_fire_phase_change_includes_failure_reason(webhook_cfg: Config) -> None:
    meta = _make_meta(Phase.FAILED, failure_reason="pytest exit code 1")
    with patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.create_task = lambda coro: coro.close()
        fire_phase_change(meta, Phase.IMPLEMENTING, Phase.FAILED, webhook_cfg)


def test_fire_phase_change_includes_pr_url(webhook_cfg: Config) -> None:
    meta = _make_meta(Phase.AWAITING_REVIEW, pr_url="https://github.com/crunchtools/rotv/pull/42")
    with patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.create_task = lambda coro: coro.close()
        fire_phase_change(meta, Phase.VALIDATING, Phase.AWAITING_REVIEW, webhook_cfg)


def test_format_heartbeat() -> None:
    meta = _make_meta(Phase.ANALYZING)
    msg = _format_heartbeat(meta, 12, "Read Sidebar.jsx")
    assert "Heartbeat" in msg
    assert "12m elapsed" in msg
    assert "analyzing" in msg
    assert "Read Sidebar.jsx" in msg


@pytest.mark.asyncio
async def test_send_heartbeat_webhook(webhook_cfg: Config) -> None:
    meta = _make_meta(Phase.ANALYZING)
    with patch("mcp_ashigaru.notify._send_webhook") as mock_webhook:
        await send_heartbeat(meta, 12, "Read Sidebar.jsx", webhook_cfg)
        mock_webhook.assert_called_once()
        url, _secret, message = mock_webhook.call_args[0]
        assert url == "http://kagetora:8644/webhooks/ashigaru-complete"
        assert "Heartbeat" in message


@pytest.mark.asyncio
async def test_send_heartbeat_skipped_when_no_config(tmp_path: Path) -> None:
    cfg = Config(state_dir=tmp_path / "ashigaru")
    meta = _make_meta(Phase.ANALYZING)
    with patch("mcp_ashigaru.notify._send_webhook") as mock_wh:
        await send_heartbeat(meta, 5, "test", cfg)
        mock_wh.assert_not_called()


# --- Matrix notification tests ---


@pytest.mark.asyncio
async def test_send_heartbeat_matrix(mock_matrix_notifier) -> None:
    cfg = Config(state_dir=Path("/tmp/test"))  # noqa: S108
    meta = _make_meta(Phase.ANALYZING)
    await send_heartbeat(meta, 12, "Read Sidebar.jsx", cfg)
    mock_matrix_notifier.send.assert_called_once()
    assert mock_matrix_notifier.send.call_args[0][1] == "m.notice"


@pytest.mark.asyncio
async def test_terminal_phase_uses_m_text(mock_matrix_notifier) -> None:
    cfg = Config(state_dir=Path("/tmp/test"))  # noqa: S108
    meta = _make_meta(Phase.AWAITING_REVIEW)
    with patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.create_task = asyncio.ensure_future
        fire_phase_change(meta, Phase.VALIDATING, Phase.AWAITING_REVIEW, cfg)
        await asyncio.sleep(0.1)
        mock_matrix_notifier.send.assert_called_once()
        assert mock_matrix_notifier.send.call_args[0][1] == "m.text"


@pytest.mark.asyncio
async def test_nonterminal_phase_uses_m_notice(mock_matrix_notifier) -> None:
    cfg = Config(state_dir=Path("/tmp/test"))  # noqa: S108
    meta = _make_meta(Phase.ANALYZING)
    with patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.create_task = asyncio.ensure_future
        fire_phase_change(meta, Phase.QUEUED, Phase.ANALYZING, cfg)
        await asyncio.sleep(0.1)
        mock_matrix_notifier.send.assert_called_once()
        assert mock_matrix_notifier.send.call_args[0][1] == "m.notice"


@pytest.mark.asyncio
async def test_fanout_fires_both_backends(mock_matrix_notifier, both_cfg: Config) -> None:
    from mcp_ashigaru.notify import _deliver

    with patch("mcp_ashigaru.notify._send_webhook") as mock_wh:
        await _deliver("test", both_cfg)
        mock_wh.assert_called_once()
        mock_matrix_notifier.send.assert_called_once()


@pytest.mark.asyncio
async def test_fanout_tolerates_matrix_failure(both_cfg: Config) -> None:
    from mcp_ashigaru.notify import _deliver

    mock = AsyncMock()
    mock.send = AsyncMock(side_effect=OSError("connection refused"))
    with (
        patch("mcp_ashigaru.notify._matrix_notifier", mock),
        patch("mcp_ashigaru.notify._send_webhook") as mock_wh,
    ):
        await _deliver("test", both_cfg)
        mock_wh.assert_called_once()


@pytest.mark.asyncio
async def test_fanout_tolerates_webhook_failure(mock_matrix_notifier, both_cfg: Config) -> None:
    from mcp_ashigaru.notify import _deliver

    with patch("mcp_ashigaru.notify._send_webhook", side_effect=OSError("connection refused")):
        await _deliver("test", both_cfg)
        mock_matrix_notifier.send.assert_called_once()
