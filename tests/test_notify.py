"""Tests for phase change and heartbeat notifications."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta
from mcp_ashigaru.notify import _format_heartbeat, _send_matrix, fire_phase_change, send_heartbeat


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
    with (
        patch("mcp_ashigaru.notify._send_webhook") as mock_wh,
        patch("mcp_ashigaru.notify._send_matrix") as mock_mx,
    ):
        await send_heartbeat(meta, 5, "test", cfg)
        mock_wh.assert_not_called()
        mock_mx.assert_not_called()


# --- Matrix notification tests ---


@pytest.mark.asyncio
async def test_send_heartbeat_matrix(matrix_cfg: Config) -> None:
    meta = _make_meta(Phase.ANALYZING)
    with patch("mcp_ashigaru.notify._send_matrix") as mock_matrix:
        await send_heartbeat(meta, 12, "Read Sidebar.jsx", matrix_cfg)
        mock_matrix.assert_called_once()
        assert mock_matrix.call_args[0][4] == "m.notice"


@pytest.mark.asyncio
async def test_terminal_phase_uses_m_text(matrix_cfg: Config) -> None:
    meta = _make_meta(Phase.AWAITING_REVIEW)
    with (
        patch("mcp_ashigaru.notify._send_matrix") as mock_matrix,
        patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop,
    ):
        mock_loop.return_value.create_task = asyncio.ensure_future
        fire_phase_change(meta, Phase.VALIDATING, Phase.AWAITING_REVIEW, matrix_cfg)
        await asyncio.sleep(0.1)
        mock_matrix.assert_called_once()
        assert mock_matrix.call_args[0][4] == "m.text"


@pytest.mark.asyncio
async def test_nonterminal_phase_uses_m_notice(matrix_cfg: Config) -> None:
    meta = _make_meta(Phase.ANALYZING)
    with (
        patch("mcp_ashigaru.notify._send_matrix") as mock_matrix,
        patch("mcp_ashigaru.notify.asyncio.get_running_loop") as mock_loop,
    ):
        mock_loop.return_value.create_task = asyncio.ensure_future
        fire_phase_change(meta, Phase.QUEUED, Phase.ANALYZING, matrix_cfg)
        await asyncio.sleep(0.1)
        mock_matrix.assert_called_once()
        assert mock_matrix.call_args[0][4] == "m.notice"


@pytest.mark.asyncio
async def test_mention_added_for_m_text() -> None:
    with patch("mcp_ashigaru.notify.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda _s: mock_urlopen.return_value
        mock_urlopen.return_value.__exit__ = lambda _s, *_a: None
        mock_urlopen.return_value.read = lambda: b""
        _send_matrix(
            "https://matrix.org", "tok", "!room:matrix.org",
            "test message", "m.text", "@kagetora:matrix.org",
        )
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        assert body["body"].startswith("@kagetora:matrix.org:")
        assert "matrix.to" in body["formatted_body"]
        assert body["msgtype"] == "m.text"


@pytest.mark.asyncio
async def test_no_mention_for_m_notice() -> None:
    with patch("mcp_ashigaru.notify.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda _s: mock_urlopen.return_value
        mock_urlopen.return_value.__exit__ = lambda _s, *_a: None
        mock_urlopen.return_value.read = lambda: b""
        _send_matrix(
            "https://matrix.org", "tok", "!room:matrix.org",
            "test message", "m.notice", "@kagetora:matrix.org",
        )
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        assert not body["body"].startswith("@kagetora")
        assert body["msgtype"] == "m.notice"


@pytest.mark.asyncio
async def test_fanout_fires_both_backends(both_cfg: Config) -> None:
    from mcp_ashigaru.notify import _deliver

    with (
        patch("mcp_ashigaru.notify._send_webhook") as mock_wh,
        patch("mcp_ashigaru.notify._send_matrix") as mock_mx,
    ):
        await _deliver("test", both_cfg)
        mock_wh.assert_called_once()
        mock_mx.assert_called_once()


@pytest.mark.asyncio
async def test_fanout_tolerates_matrix_failure(both_cfg: Config) -> None:
    from mcp_ashigaru.notify import _deliver

    with (
        patch("mcp_ashigaru.notify._send_matrix", side_effect=OSError("connection refused")),
        patch("mcp_ashigaru.notify._send_webhook") as mock_wh,
    ):
        await _deliver("test", both_cfg)
        mock_wh.assert_called_once()


@pytest.mark.asyncio
async def test_fanout_tolerates_webhook_failure(both_cfg: Config) -> None:
    from mcp_ashigaru.notify import _deliver

    with (
        patch("mcp_ashigaru.notify._send_webhook", side_effect=OSError("connection refused")),
        patch("mcp_ashigaru.notify._send_matrix") as mock_mx,
    ):
        await _deliver("test", both_cfg)
        mock_mx.assert_called_once()
