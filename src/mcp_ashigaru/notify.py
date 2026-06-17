"""Webhook notifications — phase changes and heartbeat."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import shlex
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from .config import Config
    from .models import Phase, RunMeta

logger = logging.getLogger(__name__)


def _format_heartbeat(meta: RunMeta, elapsed_minutes: int, last_activity: str) -> str:
    return (
        f"Heartbeat: {meta.run_id} still in \"{meta.phase.value}\" "
        f"({elapsed_minutes}m elapsed)\n"
        f"Last activity: {last_activity}"
    )


def _send_webhook(url: str, secret: str, message: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Webhook URL must be http(s): {url}")
    payload = json.dumps({"body": message}).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    req = Request(  # noqa: S310
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={sig}",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        resp.read()


async def _send_cmd(cmd: str, message: str) -> None:
    cmd_parts = shlex.split(cmd)
    cmd_parts.append(message)
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


async def _deliver(message: str, config: Config) -> None:
    if config.notify_webhook:
        await asyncio.to_thread(
            _send_webhook, config.notify_webhook, config.notify_webhook_secret, message,
        )
    elif config.notify_cmd:
        await _send_cmd(config.notify_cmd, message)


def fire_phase_change(
    meta: RunMeta, old_phase: Phase, new_phase: Phase, config: Config,
) -> None:
    repo_issue = f"{meta.repo} #{meta.issue}"
    title = meta.title or repo_issue
    lines = [
        f"Phase change: {old_phase.value} → {new_phase.value}",
        f"Run {meta.run_id} ({repo_issue})",
        title,
    ]
    if new_phase.value == "failed" and meta.failure_reason:
        lines.append(f"Error: {meta.failure_reason}")
    if new_phase.value == "awaiting-review" and meta.pr_url:
        lines.append(f"PR: {meta.pr_url}")
    message = "\n".join(lines)
    loop = asyncio.get_running_loop()
    loop.create_task(_deliver(message, config))


async def send_heartbeat(
    meta: RunMeta, elapsed_minutes: int, last_activity: str, config: Config,
) -> None:
    message = _format_heartbeat(meta, elapsed_minutes, last_activity)
    try:
        await _deliver(message, config)
    except TimeoutError:
        logger.warning("Heartbeat notification timed out")
    except Exception:
        logger.exception("Heartbeat notification failed")
