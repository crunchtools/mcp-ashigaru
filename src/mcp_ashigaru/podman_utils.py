"""Shared podman helpers for running containers on the host."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .config import Config


async def hpodman(
    config: Config, *args: str, log_path: Path | None = None,
    capture: bool = False, use_host_socket: bool = True,
) -> tuple[int, str]:
    socket = config.host_podman if use_host_socket else config.container_host
    merged_env = {**os.environ, "CONTAINER_HOST": socket}
    if capture:
        proc = await asyncio.create_subprocess_exec(
            "podman", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=merged_env,
        )
        out, _ = await proc.communicate()
        output = out.decode() if out else ""
        if log_path:
            with log_path.open("a") as f:
                f.write(output)
        return proc.returncode or 0, output
    log_file = log_path.open("a") if log_path else None
    try:
        proc = await asyncio.create_subprocess_exec(
            "podman", *args,
            stdout=log_file or asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
            env=merged_env,
        )
        await proc.wait()
    finally:
        if log_file:
            log_file.close()
    return proc.returncode or 0, ""
