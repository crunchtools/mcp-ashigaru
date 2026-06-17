"""Tests for notification callback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta
from mcp_ashigaru.notify import _format_heartbeat, _format_phase_change, send_phase_change


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(state_dir=state_dir, notify_cmd="hermes send --to signal:+15551234567")


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


# ---------------------------------------------------------------------------
# _format_phase_change
# ---------------------------------------------------------------------------

def test_format_phase_change_contains_run_id() -> None:
    meta = _make_meta(Phase.ANALYZING)
    msg = _format_phase_change(meta, Phase.QUEUED, Phase.ANALYZING)
    assert "rotv-5-123456" in msg


def test_format_phase_change_shows_transition() -> None:
    meta = _make_meta(Phase.IMPLEMENTING)
    msg = _format_phase_change(meta, Phase.ANALYZING, Phase.IMPLEMENTING)
    assert "analyzing" in msg
    assert "implementing" in msg
    assert "→" in msg


def test_format_phase_change_includes_title() -> None:
    meta = _make_meta(Phase.FAILED)
    msg = _format_phase_change(meta, Phase.ANALYZING, Phase.FAILED)
    assert "Fix auth header" in msg
    assert "rotv #5" in msg


def test_format_phase_change_fallback_title() -> None:
    meta = _make_meta(Phase.FAILED, title="")
    msg = _format_phase_change(meta, Phase.ANALYZING, Phase.FAILED)
    assert "rotv #5" in msg


# ---------------------------------------------------------------------------
# _format_heartbeat
# ---------------------------------------------------------------------------

def test_format_heartbeat_active_run() -> None:
    run = {
        "run_id": "rotv-5-123456",
        "repo": "rotv",
        "issue": 5,
        "title": "Fix auth header",
        "phase": "analyzing",
        "pr_url": None,
    }
    msg = _format_heartbeat(run)
    assert "heartbeat" in msg
    assert "rotv-5-123456" in msg
    assert "analyzing" in msg
    assert "Fix auth header" in msg


def test_format_heartbeat_includes_pr_url() -> None:
    run = {
        "run_id": "rotv-5-123456",
        "repo": "rotv",
        "issue": 5,
        "title": "Fix auth header",
        "phase": "awaiting-review",
        "pr_url": "https://github.com/crunchtools/rotv/pull/42",
    }
    msg = _format_heartbeat(run)
    assert "pull/42" in msg


def test_format_heartbeat_no_pr_url() -> None:
    run = {
        "run_id": "rotv-5-123456",
        "repo": "rotv",
        "issue": 5,
        "title": "Fix auth header",
        "phase": "implementing",
        "pr_url": None,
    }
    msg = _format_heartbeat(run)
    assert "PR:" not in msg


# ---------------------------------------------------------------------------
# send_phase_change
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_phase_change_skipped_when_no_config(tmp_path: Path) -> None:
    cfg = Config(state_dir=tmp_path / "ashigaru")
    meta = _make_meta(Phase.FAILED)
    with patch("mcp_ashigaru.notify.asyncio.create_subprocess_exec") as mock_exec:
        await send_phase_change(meta, Phase.ANALYZING, Phase.FAILED, cfg)
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_send_phase_change_calls_command(cfg: Config) -> None:
    meta = _make_meta(Phase.AWAITING_REVIEW, pr_url="https://github.com/crunchtools/rotv/pull/42")
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0

    with patch("mcp_ashigaru.notify.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await send_phase_change(meta, Phase.VALIDATING, Phase.AWAITING_REVIEW, cfg)
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "hermes"
        assert "validating" in args[-1] or "awaiting-review" in args[-1]


@pytest.mark.asyncio
async def test_send_phase_change_posts_webhook(webhook_cfg: Config) -> None:
    meta = _make_meta(Phase.FAILED)

    with patch("mcp_ashigaru.notify._send_webhook") as mock_webhook:
        await send_phase_change(meta, Phase.ANALYZING, Phase.FAILED, webhook_cfg)
        mock_webhook.assert_called_once()
        url, secret, message = mock_webhook.call_args[0]
        assert url == "http://kagetora:8644/webhooks/ashigaru-complete"
        assert secret == "test-secret"
        assert "analyzing" in message
        assert "failed" in message


@pytest.mark.asyncio
async def test_send_phase_change_webhook_preferred_over_cmd(tmp_path: Path) -> None:
    cfg = Config(
        state_dir=tmp_path / "ashigaru",
        notify_cmd="hermes send --to signal:+15551234567",
        notify_webhook="http://kagetora:8644/webhooks/ashigaru-complete",
        notify_webhook_secret="test-secret",
    )
    meta = _make_meta(Phase.FAILED)

    with patch("mcp_ashigaru.notify._send_webhook") as mock_webhook:
        with patch("mcp_ashigaru.notify.asyncio.create_subprocess_exec") as mock_exec:
            await send_phase_change(meta, Phase.ANALYZING, Phase.FAILED, cfg)
            mock_webhook.assert_called_once()
            mock_exec.assert_not_called()
