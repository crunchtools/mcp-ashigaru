"""Tests for heartbeat — periodic image pruning."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_ashigaru.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(state_dir=state_dir)


@pytest.mark.asyncio
async def test_tick_prunes_dangling_images(cfg: Config) -> None:
    from mcp_ashigaru.heartbeat import _tick

    calls: list[tuple[str, ...]] = []

    async def fake_hpodman(_config, *args, **_kwargs):
        calls.append(args)
        return (0, "")

    with patch("mcp_ashigaru.heartbeat.hpodman", side_effect=fake_hpodman):
        await _tick(cfg)

    assert ("image", "prune", "-f") in calls
