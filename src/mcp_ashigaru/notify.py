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
                 mention_user: str, device_id: str, crypto_dir: str = "") -> None:
        self._homeserver = homeserver
        self._access_token = access_token
        self._room_id = room_id
        self._mention_user = mention_user
        self._device_id = device_id
        self._crypto_dir = crypto_dir
        self._client: Any = None
        self._crypto: Any = None
        self._pickle_path: str = ""
        self._ready = False

    async def start(self) -> None:
        import json as _json
        from pathlib import Path

        from mautrix.client import Client
        from mautrix.client.state_store.memory import MemoryStateStore
        from mautrix.crypto import MemoryCryptoStore, OlmMachine
        from mautrix.types import (
            EncryptionAlgorithm,
            Member,
            Membership,
            RoomEncryptionStateEventContent,
            RoomID,
        )

        self._client = Client(
            base_url=self._homeserver,
            token=self._access_token,
            device_id=self._device_id,
        )
        whoami = await self._client.whoami()
        self._client.mxid = whoami.user_id

        crypto_store = MemoryCryptoStore(account_id=str(whoami.user_id), pickle_key="ashigaru")
        await crypto_store.open()

        if self._crypto_dir:
            crypto_path = Path(self._crypto_dir)
            crypto_path.mkdir(parents=True, exist_ok=True)
            self._pickle_path = str(crypto_path / "olm_state.json")
            if Path(self._pickle_path).exists():
                try:
                    from olm import Account as _OlmAccount

                    with open(self._pickle_path) as f:
                        saved = _json.load(f)
                    account = _OlmAccount.from_pickle(
                        saved["account_pickle"].encode(), "ashigaru",
                    )
                    account.shared = saved.get("shared", True)  # type: ignore[attr-defined]
                    crypto_store._account = account  # type: ignore[assignment]
                    logger.info("Restored Matrix crypto state from %s", self._pickle_path)
                except Exception:
                    logger.warning("Failed to restore crypto state, starting fresh")

        state_store = MemoryStateStore()
        room_id = RoomID(self._room_id)

        await state_store.set_encryption_info(
            room_id,
            RoomEncryptionStateEventContent(algorithm=EncryptionAlgorithm.MEGOLM_V1),
        )

        joined = await self._client.get_joined_members(room_id)
        members = {
            uid: Member(membership=Membership.JOIN, displayname=info.displayname)
            for uid, info in joined.items()
        }
        await state_store.set_members(room_id, members, only_membership=Membership.JOIN)  # type: ignore[arg-type]

        self._client.state_store = state_store
        self._crypto = OlmMachine(
            client=self._client, crypto_store=crypto_store, state_store=state_store,  # type: ignore[arg-type]
        )
        self._client.crypto = self._crypto
        await self._crypto.load()
        try:
            if not self._crypto.account.shared:
                await self._crypto.share_keys()
        except Exception:
            logger.warning("Key upload failed (stale server keys?), marking as shared")
            self._crypto.account.shared = True
        await self._save_crypto_state()

        room_id = RoomID(self._room_id)
        user_ids = list(joined.keys())
        try:
            await self._crypto.share_group_session(room_id, user_ids)
            print("[ashigaru] Megolm session created for ops room")
        except Exception as exc:
            print(f"[ashigaru] Failed to pre-create megolm session: {exc}")

        self._ready = True
        print(f"[ashigaru] Matrix E2EE notifier ready: {whoami.user_id}")

    async def _save_crypto_state(self) -> None:
        if not self._pickle_path or not self._crypto:
            return
        import json as _json
        try:
            account = self._crypto.crypto_store._account
            if account is None:
                return
            state = {
                "account_pickle": account.pickle("ashigaru").decode(),
                "shared": account.shared,
            }
            with open(self._pickle_path, "w") as f:
                _json.dump(state, f)
        except Exception:
            logger.exception("Failed to save crypto state")

    async def stop(self) -> None:
        await self._save_crypto_state()
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
                room_id, EventType.ROOM_MESSAGE, content,
            )
            await self._client.send_message_event(room_id, EventType.ROOM_ENCRYPTED, encrypted)
        except Exception:
            logger.exception("E2EE send failed, sending plaintext via raw API")
            from mautrix.api import Method, Path
            txn = f"ashigaru-{int(asyncio.get_event_loop().time() * 1000)}"
            await self._client.api.request(
                Method.PUT,
                Path.v3.rooms[room_id].send["m.room.message"][txn],
                content.serialize(),
            )
        await self._save_crypto_state()


async def init_matrix(config: Config) -> None:
    global _matrix_notifier
    if not (config.matrix_homeserver and config.matrix_access_token and config.matrix_room_id):
        return
    crypto_dir = config.matrix_crypto_dir or str(config.state_dir / ".matrix-crypto")
    _matrix_notifier = MatrixNotifier(
        homeserver=config.matrix_homeserver,
        access_token=config.matrix_access_token,
        room_id=config.matrix_room_id,
        mention_user=config.matrix_mention_user,
        device_id=config.matrix_device_id,
        crypto_dir=crypto_dir,
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
