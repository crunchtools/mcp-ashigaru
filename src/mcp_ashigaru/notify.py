"""Notifications — phase changes and heartbeat via webhook, command, or Matrix."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import shlex
import time
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


def _send_matrix(
    homeserver: str,
    token: str,
    room_id: str,
    message: str,
    msgtype: str = "m.notice",
    mention_user: str = "",
) -> None:
    txn_id = f"ashigaru-{int(time.time() * 1000)}"
    url = (
        f"{homeserver.rstrip('/')}/_matrix/client/v3/rooms/"
        f"{room_id}/send/m.room.message/{txn_id}"
    )

    body = message
    formatted_body = message.replace("\n", "<br>")

    if mention_user and msgtype == "m.text":
        display_name = mention_user.split(":", maxsplit=1)[0].lstrip("@")
        body = f"{mention_user}: {message}"
        formatted_body = (
            f'<a href="https://matrix.to/#/{mention_user}">{display_name}</a>: '
            + formatted_body
        )

    payload = json.dumps({
        "msgtype": msgtype,
        "body": body,
        "format": "org.matrix.custom.html",
        "formatted_body": formatted_body,
    }).encode()

    req = Request(  # noqa: S310
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="PUT",
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


async def _deliver(message: str, config: Config, msgtype: str = "m.notice") -> None:
    if config.matrix_homeserver and config.matrix_access_token and config.matrix_room_id:
        try:
            await asyncio.to_thread(
                _send_matrix,
                config.matrix_homeserver,
                config.matrix_access_token,
                config.matrix_room_id,
                message,
                msgtype,
                config.matrix_mention_user,
            )
        except Exception:
            logger.exception("Matrix notification failed")

    if config.notify_webhook:
        try:
            await asyncio.to_thread(
                _send_webhook, config.notify_webhook, config.notify_webhook_secret, message,
            )
        except Exception:
            logger.exception("Webhook notification failed")

    if config.notify_cmd:
        try:
            await _send_cmd(config.notify_cmd, message)
        except Exception:
            logger.exception("Command notification failed")


def fire_phase_change(
    meta: RunMeta, old_phase: Phase, new_phase: Phase, config: Config,
) -> None:
    from .models import TERMINAL_PHASES

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

    msgtype = "m.text" if new_phase in TERMINAL_PHASES else "m.notice"

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_deliver(message, config, msgtype))
    except RuntimeError:
        pass


async def send_heartbeat(
    meta: RunMeta, elapsed_minutes: int, last_activity: str, config: Config,
) -> None:
    message = _format_heartbeat(meta, elapsed_minutes, last_activity)
    try:
        await _deliver(message, config, msgtype="m.notice")
    except TimeoutError:
        logger.warning("Heartbeat notification timed out")
    except Exception:
        logger.exception("Heartbeat notification failed")
