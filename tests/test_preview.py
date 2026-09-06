"""Tests for preview lifecycle — teardown image cleanup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta, SlotLock, Source
from mcp_ashigaru.slots import SlotManager
from mcp_ashigaru.state import RunState


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    slots_dir = tmp_path / "slots"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    slots_dir.mkdir(parents=True)
    return Config(state_dir=state_dir, slots_dir=slots_dir)


@pytest.fixture
def run_with_slot(cfg: Config) -> RunState:
    meta = RunMeta(
        run_id="rotv-490-abc123",
        repo="rotv",
        issue=490,
        title="Fix map layer",
        branch="fix/490-map-layer",
        phase=Phase.PREVIEW_LIVE,
        source=Source.ASHIGARU,
    )
    state = RunState.create(meta, cfg)
    lock = SlotLock(
        run_id="rotv-490-abc123",
        repo="rotv",
        branch="fix/490-map-layer",
        container="ashigaru-preview-1",
        slot=1,
        port=8081,
        domain="crunchtools.com",
        allocated_at="2026-06-22T00:00:00+00:00",
    )
    lock_dir = cfg.slots_dir / "development1"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "lock.json").write_text(lock.model_dump_json(indent=2))
    return state


@pytest.mark.asyncio
@pytest.mark.usefixtures("run_with_slot")
async def test_teardown_calls_prune_images(cfg: Config) -> None:
    from mcp_ashigaru.preview import teardown

    calls: list[tuple[str, ...]] = []

    async def fake_hpodman(_config, *args, **_kwargs):
        calls.append(args)
        if args[0] == "images":
            images = "localhost/ashigaru-preview-rotv-490-old\n"
            images += "localhost/ashigaru-preview-rotv-490-abc123\n"
            return (0, images)
        return (0, "")

    with (
        patch("mcp_ashigaru.preview._hpodman", side_effect=fake_hpodman),
        patch.object(SlotManager, "release", new_callable=AsyncMock, return_value=True),
    ):
        result = await teardown("rotv-490-abc123", cfg)

    assert result["freed"] is True
    cmds = [c[0] for c in calls]
    assert "rmi" in cmds
    assert ("image", "prune", "-f") in calls
