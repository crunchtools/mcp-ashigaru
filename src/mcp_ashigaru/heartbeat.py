"""5-minute heartbeat watchdog — notifies Kagetora about active runs."""

from __future__ import annotations

import asyncio
import logging

from .config import Config
from .models import TERMINAL_PHASES
from .notify import _format_heartbeat, _send_webhook
from .state import RunState

logger = logging.getLogger(__name__)


async def heartbeat_loop(config: Config) -> None:
    while True:
        await asyncio.sleep(config.heartbeat_interval)
        await _tick(config)


async def _tick(config: Config) -> None:
    if not config.notify_webhook:
        return
    try:
        runs = RunState.list_all(config)
    except Exception:
        logger.exception("Heartbeat tick failed listing runs")
        return
    active = [r for r in runs if r.get("phase") not in TERMINAL_PHASES]
    for run in active:
        try:
            message = _format_heartbeat(run)
            await asyncio.to_thread(
                _send_webhook, config.notify_webhook, config.notify_webhook_secret, message,
            )
        except Exception:
            logger.exception("Heartbeat failed for run %s", run.get("run_id"))
