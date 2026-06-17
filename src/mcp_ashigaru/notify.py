"""Completion callback — fire-and-forget notification when runs finish."""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .models import RunMeta

logger = logging.getLogger(__name__)


def _format_message(meta: RunMeta) -> str:
    phase = meta.phase.value
    repo_issue = f"{meta.repo} #{meta.issue}"
    title = meta.title or repo_issue

    if phase == "awaiting-review":
        pr = meta.pr_url or "no PR URL"
        return (
            f"Run {meta.run_id} completed — PR ready\n"
            f"{title} ({repo_issue})\n"
            f"PR: {pr}"
        )

    if phase == "escalated":
        return (
            f"Run {meta.run_id} escalated — all tiers exhausted\n"
            f"{title} ({repo_issue})\n"
            f"Needs human intervention"
        )

    reason = meta.failure_reason or "unknown error"
    return (
        f"Run {meta.run_id} failed\n"
        f"{title} ({repo_issue})\n"
        f"Error: {reason}"
    )


async def send_notification(meta: RunMeta, config: Config) -> None:
    if not config.notify_cmd:
        return
    message = _format_message(meta)
    cmd_parts = shlex.split(config.notify_cmd)
    cmd_parts.append(message)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.warning(
                "Notify command exited %d: %s",
                proc.returncode,
                stderr.decode()[:200] if stderr else "",
            )
    except TimeoutError:
        logger.warning("Notify command timed out after 30s")
    except Exception:
        logger.exception("Notify command failed")
