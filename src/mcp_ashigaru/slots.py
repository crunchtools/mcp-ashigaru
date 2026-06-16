"""Preview slot allocation with leak prevention."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .models import SlotLock


class SlotManager:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._max = config.max_slots
        self._dir = config.slots_dir

    def _lock_path(self, slot: int) -> Path:
        return self._dir / f"development{slot}" / "lock.json"

    def _read_lock(self, slot: int) -> SlotLock | None:
        p = self._lock_path(slot)
        if not p.exists():
            return None
        try:
            return SlotLock.model_validate_json(p.read_text())
        except (json.JSONDecodeError, OSError, ValueError):
            return None

    def _write_lock(self, slot: int, lock: SlotLock) -> None:
        slot_dir = self._dir / f"development{slot}"
        slot_dir.mkdir(parents=True, exist_ok=True)
        (slot_dir / "lock.json").write_text(lock.model_dump_json(indent=2))

    def _clear_lock(self, slot: int) -> None:
        p = self._lock_path(slot)
        if p.exists():
            p.unlink()

    async def _container_exists(self, container: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "podman", "inspect", container,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "CONTAINER_HOST": self._config.host_podman},
        )
        await proc.wait()
        return proc.returncode == 0

    def find_by_run(self, run_id: str) -> SlotLock | None:
        for n in range(1, self._max + 1):
            lock = self._read_lock(n)
            if lock and lock.run_id == run_id:
                return lock
        return None

    async def cleanup_stale(self) -> int:
        freed = 0
        for n in range(1, self._max + 1):
            lock = self._read_lock(n)
            if lock and not await self._container_exists(lock.container):
                self._clear_lock(n)
                freed += 1
        return freed

    async def allocate(
        self,
        run_id: str,
        repo: str,
        branch: str,
        domain: str,
    ) -> SlotLock | None:
        existing = self.find_by_run(run_id)
        if existing:
            await self._teardown_container(existing.container)
            new_lock = SlotLock(
                run_id=run_id, repo=repo, branch=branch,
                container=existing.container, slot=existing.slot,
                port=existing.port, domain=domain,
                allocated_at=datetime.now(UTC).isoformat(),
            )
            self._write_lock(existing.slot, new_lock)
            return new_lock

        await self.cleanup_stale()

        for n in range(1, self._max + 1):
            if not self._read_lock(n):
                port = 8099 + n
                lock = SlotLock(
                    run_id=run_id, repo=repo, branch=branch,
                    container=f"development{n}", slot=n,
                    port=port, domain=domain,
                    allocated_at=datetime.now(UTC).isoformat(),
                )
                self._write_lock(n, lock)
                return lock

        return None

    async def release(self, run_id: str) -> bool:
        lock = self.find_by_run(run_id)
        if not lock:
            return False
        await self._teardown_container(lock.container)
        self._clear_lock(lock.slot)
        return True

    async def _teardown_container(self, container: str) -> None:
        merged_env = {**os.environ, "CONTAINER_HOST": self._config.host_podman}
        for cmd in (["podman", "stop", container], ["podman", "rm", "-f", container]):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=merged_env,
            )
            await proc.wait()

    def list_all(self) -> list[dict]:
        result = []
        for n in range(1, self._max + 1):
            lock = self._read_lock(n)
            if lock:
                result.append(lock.model_dump())
            else:
                result.append({"slot": n, "status": "free"})
        return result
