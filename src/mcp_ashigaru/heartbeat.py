"""Background heartbeat — pings Kagetora for active runs every N seconds."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .config import Config
from .models import ACTIVE_PHASES, Phase
from .notify import send_heartbeat
from .podman_utils import hpodman
from .state import RunState

logger = logging.getLogger(__name__)


async def heartbeat_loop(config: Config) -> None:
    if config.heartbeat_interval <= 0:
        return
    while True:
        await asyncio.sleep(config.heartbeat_interval)
        try:
            await _tick(config)
        except Exception:
            logger.exception("Heartbeat tick failed")


async def _tick(config: Config) -> None:
    await hpodman(config, "image", "prune", "-f")
    runs = RunState.list_all(config)
    now = datetime.now(UTC)
    for run_summary in runs:
        phase_str = run_summary.get("phase", "")
        try:
            phase = Phase(phase_str)
        except ValueError:
            continue
        if phase not in ACTIVE_PHASES:
            continue

        run_id = run_summary["run_id"]
        state = RunState.load(run_id, config)
        if state is None:
            continue

        meta = state.read_meta()
        created = datetime.fromisoformat(meta.created)
        elapsed = int((now - created).total_seconds() / 60)

        recent = state.activity.latest(1)
        last_activity = recent[0]["summary"][:80] if recent else "no activity"

        await send_heartbeat(meta, elapsed, last_activity, config)
