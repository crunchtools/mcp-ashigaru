"""Notifications — phase changes and heartbeat via webhook, command, or Matrix E2EE."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config
    from .models import Phase, RunMeta

logger = logging.getLogger(__name__)

_matrix_notifier: MatrixNotifier | None = None


class MatrixNotifier:
    """Persistent matrix-nio client with E2EE for sending Matrix notifications."""

    def __init__(self, homeserver: str, access_token: str, room_id: str,
                 mention_user: str, device_id: str, crypto_dir: str) -> None:
        self._homeserver = homeserver
        self._access_token = access_token
        self._room_id = room_id
        self._mention_user = mention_user
        self._device_id = device_id
        self._crypto_dir = crypto_dir
        self._client: Any = None
        self._ready = False

    async def start(self) -> None:
        from pathlib import Path

        from nio import AsyncClient, AsyncClientConfig, WhoamiError

        store_path = self._crypto_dir
        if store_path:
            Path(store_path).mkdir(parents=True, exist_ok=True)

        config = AsyncClientConfig(
            store_sync_tokens=True,
            encryption_enabled=True,
        )

        self._client = AsyncClient(
            homeserver=self._homeserver,
            config=config,
            store_path=store_path or "",
        )

        self._client.access_token = self._access_token
        self._client.device_id = self._device_id

        resp = await self._client.whoami()
        if isinstance(resp, WhoamiError):
            msg = f"Matrix whoami failed: {resp.message}"
            raise ConnectionError(msg)
        self._client.user_id = resp.user_id

        if store_path:
            self._client.load_store()

        await self._client.sync(
            timeout=10000,
            full_state=True,
            sync_filter={"room": {"timeline": {"limit": 0}}},
        )

        self._ready = True
        print(f"[ashigaru] Matrix E2EE notifier ready: {resp.user_id}")

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        self._ready = False

    async def send(self, message: str, msgtype: str = "m.notice") -> None:
        if not self._ready or not self._client:
            logger.warning("Matrix notifier not ready, dropping message")
            return

        body = message
        formatted_body = message.replace("\n", "<br>")

        if self._mention_user and msgtype == "m.text":
            display_name = self._mention_user.split(":", maxsplit=1)[0].lstrip("@")
            body = f"{self._mention_user}: {message}"
            formatted_body = (
                f'<a href="https://matrix.to/#/{self._mention_user}">{display_name}</a>: '
                + formatted_body
            )

        content = {
            "msgtype": msgtype,
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted_body,
        }

        try:
            await self._client.room_send(
                room_id=self._room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=True,
            )
        except Exception:
            logger.exception("Matrix send failed")


async def init_matrix(config: Config) -> None:
    global _matrix_notifier
    if not (config.matrix_homeserver and config.matrix_access_token and config.matrix_room_id):
        return
    _matrix_notifier = MatrixNotifier(
        homeserver=config.matrix_homeserver,
        access_token=config.matrix_access_token,
        room_id=config.matrix_room_id,
        mention_user=config.matrix_mention_user,
        device_id=config.matrix_device_id,
        crypto_dir=config.matrix_crypto_dir,
    )
    try:
        await _matrix_notifier.start()
        print(f"[ashigaru] Matrix E2EE notifier initialized: {_matrix_notifier._room_id}")
    except Exception as exc:
        print(f"[ashigaru] Failed to initialize Matrix notifier: {exc}")
        logger.exception("Failed to initialize Matrix notifier")
        _matrix_notifier = None


async def shutdown_matrix() -> None:
    global _matrix_notifier
    if _matrix_notifier:
        await _matrix_notifier.stop()
        _matrix_notifier = None


def _format_heartbeat(meta: RunMeta, elapsed_minutes: int, last_activity: str) -> str:
    return (
        f"Heartbeat: {meta.run_id} still in \"{meta.phase.value}\" "
        f"({elapsed_minutes}m elapsed)\n"
        f"Last activity: {last_activity}"
    )


def _send_webhook(url: str, secret: str, message: str) -> None:
    from urllib.request import Request, urlopen

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


async def _deliver(message: str, config: Config, msgtype: str = "m.notice") -> None:
    if _matrix_notifier:
        try:
            await _matrix_notifier.send(message, msgtype)
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
    status = f"{old_phase.value} to {new_phase.value}"
    lines = [
        f"Run {meta.run_id} ({repo_issue}) has changed from {status}.",
        title,
    ]
    if new_phase.value == "failed" and meta.failure_reason:
        lines.append(f"Error: {meta.failure_reason}")
    if new_phase.value in ("failed", "escalated", "cancelled"):
        lines.append("DO NOT restart this run. Notify Scott and wait for instructions.")
    if new_phase.value == "awaiting-review" and meta.pr_url:
        lines.append(f"PR: {meta.pr_url}")
    message = "\n".join(lines)

    if new_phase not in TERMINAL_PHASES:
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_deliver(message, config, "m.text"))
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
