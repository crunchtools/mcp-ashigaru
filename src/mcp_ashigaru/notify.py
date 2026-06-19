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
    """Persistent mautrix client with E2EE for sending Matrix notifications."""

    def __init__(self, homeserver: str, access_token: str, room_id: str,
                 mention_user: str, device_id: str) -> None:
        self._homeserver = homeserver
        self._access_token = access_token
        self._room_id = room_id
        self._mention_user = mention_user
        self._device_id = device_id
        self._client: Any = None
        self._crypto: Any = None
        self._ready = False

    async def start(self) -> None:
        from mautrix.client import Client
        from mautrix.crypto import MemoryCryptoStore, OlmMachine, StateStore
        from mautrix.types import (
            EncryptionAlgorithm,
            RoomEncryptionStateEventContent,
            RoomID,
            UserID,
        )

        class _AlwaysEncryptedStateStore(StateStore):
            async def is_encrypted(self, _room_id: RoomID) -> bool:
                return True

            async def get_encryption_info(
                self, _room_id: RoomID,
            ) -> RoomEncryptionStateEventContent | None:
                return RoomEncryptionStateEventContent(
                    algorithm=EncryptionAlgorithm.MEGOLM_V1,
                )

            async def find_shared_rooms(self, _user_id: UserID) -> list[RoomID]:
                return []

        self._client = Client(
            base_url=self._homeserver,
            token=self._access_token,
        )
        whoami = await self._client.whoami()
        self._client.mxid = whoami.user_id

        crypto_store = MemoryCryptoStore(account_id=str(whoami.user_id), pickle_key="ashigaru")
        await crypto_store.open()

        state_store = _AlwaysEncryptedStateStore()
        self._crypto = OlmMachine(
            client=self._client, crypto_store=crypto_store, state_store=state_store,
        )
        self._client.crypto = self._crypto
        self._crypto.device_id = self._device_id
        await self._crypto.load()
        if not self._crypto.account.shared:
            await self._crypto.share_keys()

        self._ready = True
        logger.info("Matrix E2EE notifier ready: %s", whoami.user_id)

    async def stop(self) -> None:
        if self._client:
            await self._client.api.session.close()
        self._ready = False

    async def send(self, message: str, msgtype: str = "m.notice") -> None:
        if not self._ready or not self._client:
            logger.warning("Matrix notifier not ready, dropping message")
            return

        from mautrix.types import (
            EventType,
            Format,
            MessageType,
            RoomID,
            TextMessageEventContent,
        )

        body = message
        formatted_body = message.replace("\n", "<br>")

        if self._mention_user and msgtype == "m.text":
            display_name = self._mention_user.split(":", maxsplit=1)[0].lstrip("@")
            body = f"{self._mention_user}: {message}"
            formatted_body = (
                f'<a href="https://matrix.to/#/{self._mention_user}">{display_name}</a>: '
                + formatted_body
            )

        content = TextMessageEventContent(
            msgtype=MessageType(msgtype),
            body=body,
            format=Format.HTML,
            formatted_body=formatted_body,
        )

        room_id = RoomID(self._room_id)

        try:
            encrypted = await self._crypto.encrypt_megolm_event(
                content, room_id, EventType.ROOM_MESSAGE,
            )
            await self._client.send_message_event(room_id, EventType.ROOM_ENCRYPTED, encrypted)
        except Exception:
            logger.warning("E2EE send failed, falling back to plaintext")
            await self._client.send_message_event(room_id, EventType.ROOM_MESSAGE, content)


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
    )
    try:
        await _matrix_notifier.start()
    except Exception:
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
