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
    from .state import RunState

# Sentinel written to RunMeta.matrix_thread_id while the thread-root message is
# being sent, so concurrent phase changes wait for the real ID instead of racing
# to create a second thread.
_PENDING_THREAD = "pending"

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

        client_config = AsyncClientConfig(
            max_limit_exceeded=0,
            max_timeouts=0,
            store_sync_tokens=True,
            encryption_enabled=True,
        )

        self._client = AsyncClient(
            homeserver=self._homeserver,
            device_id=self._device_id,
            store_path=store_path or "",
            config=client_config,
        )

        self._client.access_token = self._access_token

        resp = await self._client.whoami()
        if isinstance(resp, WhoamiError):
            msg = f"Matrix whoami failed: {resp.message}"
            raise ConnectionError(msg)
        self._client.user_id = resp.user_id

        if store_path:
            self._client.load_store()

        if self._client.should_upload_keys:
            await self._client.keys_upload()

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

    async def send(
        self, message: str, msgtype: str = "m.notice", thread_id: str | None = None,
    ) -> str | None:
        """Send a message, optionally threaded under ``thread_id``.

        Returns the event ID of the sent message, or ``None`` on failure.
        """
        if not self._ready or not self._client:
            logger.warning("Matrix notifier not ready, dropping message")
            return None

        body = message
        formatted_body = message.replace("\n", "<br>")

        if self._mention_user and msgtype == "m.text":
            display_name = self._mention_user.split(":", maxsplit=1)[0].lstrip("@")
            body = f"{self._mention_user}: {message}"
            formatted_body = (
                f'<a href="https://matrix.to/#/{self._mention_user}">{display_name}</a>: '
                + formatted_body
            )

        content: dict[str, Any] = {
            "msgtype": msgtype,
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted_body,
        }

        if thread_id:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_id,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_id},
            }

        try:
            resp = await self._client.room_send(
                room_id=self._room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=True,
            )
        except Exception:
            logger.exception("Matrix send failed")
            return None

        event_id = getattr(resp, "event_id", None)
        if not event_id:
            logger.error("Matrix room_send returned no event ID: %r", resp)
            return None
        return event_id


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


async def _wait_for_thread_id(
    state: RunState, timeout: float = 10.0, interval: float = 0.25,
) -> str | None:
    """Wait for the thread-root task to publish the real ``matrix_thread_id``.

    Returns the resolved thread ID, or ``None`` if it stays pending past
    ``timeout`` (e.g. the root message failed to send).
    """
    waited = 0.0
    while waited < timeout:
        thread_id = state.read_meta().matrix_thread_id
        if thread_id and thread_id != _PENDING_THREAD:
            return thread_id
        await asyncio.sleep(interval)
        waited += interval
    logger.warning("Timed out waiting for matrix_thread_id; sending unthreaded")
    return None


async def _deliver(
    message: str,
    config: Config,
    msgtype: str = "m.notice",
    state: RunState | None = None,
    create_root: bool = False,
) -> None:
    if _matrix_notifier:
        try:
            if state is not None and create_root:
                # This call creates the thread; persist the resulting event ID
                # (or clear the pending sentinel if it failed) for later calls.
                event_id = await _matrix_notifier.send(message, msgtype)
                state.update_meta(matrix_thread_id=event_id or None)
            elif state is not None:
                thread_id = await _wait_for_thread_id(state)
                await _matrix_notifier.send(message, msgtype, thread_id=thread_id)
            else:
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
    meta: RunMeta,
    old_phase: Phase,
    new_phase: Phase,
    config: Config,
    state: RunState | None = None,
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

    # Decide synchronously whether this call owns thread creation. Writing the
    # "pending" sentinel before dispatching the async send closes the race where
    # a second phase change reads matrix_thread_id=None and creates a duplicate
    # thread before the first task persists the real ID.
    create_root = False
    if (
        state is not None
        and _matrix_notifier is not None
        and config.matrix_homeserver
        and config.matrix_room_id
        and meta.matrix_thread_id is None
    ):
        create_root = True
        state.update_meta(matrix_thread_id=_PENDING_THREAD)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _deliver(message, config, "m.text", state=state, create_root=create_root),
        )
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
