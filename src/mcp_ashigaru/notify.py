"""Completion callback — fire-and-forget notification when run phases change."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import shlex
from typing import TYPE_CHECKING, Any
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from .config import Config
    from .models import Phase, RunMeta

logger = logging.getLogger(__name__)


def _format_phase_change(meta: RunMeta, old_phase: Phase, new_phase: Phase) -> str:
    repo_issue = f"{meta.repo} #{meta.issue}"
    title = meta.title or repo_issue
    return (
        f"Run {meta.run_id} phase: {old_phase.value} → {new_phase.value}\n"
        f"{title} ({repo_issue})"
    )


def _format_heartbeat(run: dict[str, Any]) -> str:
    repo_issue = f"{run.get('repo')} #{run.get('issue')}"
    title = run.get("title") or repo_issue
    phase = run.get("phase", "unknown")
    lines = [
        f"[heartbeat] Run {run.get('run_id')} — {phase}",
        f"{title} ({repo_issue})",
    ]
    pr_url = run.get("pr_url")
    if pr_url:
        lines.append(f"PR: {pr_url}")
    return "\n".join(lines)


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


async def send_phase_change(
    meta: RunMeta, old_phase: Phase, new_phase: Phase, config: Config,
) -> None:
    if not config.notify_webhook and not config.notify_cmd:
        return
    message = _format_phase_change(meta, old_phase, new_phase)
    try:
        if config.notify_webhook:
            await asyncio.to_thread(
                _send_webhook, config.notify_webhook, config.notify_webhook_secret, message,
            )
        else:
            await _send_cmd(config.notify_cmd, message)
    except TimeoutError:
        logger.warning("Phase change notification timed out after 30s")
    except Exception:
        logger.exception("Phase change notification failed")
