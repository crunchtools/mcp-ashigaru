"""Preview lifecycle — build, launch, teardown."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config
from .models import Activity, ActivityKind, Phase, SlotLock
from .repo_config import RepoConfig, get_repo_config
from .slots import SlotManager
from .state import RunState


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _hpodman(
    config: Config, *args: str, log_path: Path | None = None,
    capture: bool = False,
) -> tuple[int, str]:
    merged_env = {**os.environ, "CONTAINER_HOST": config.host_podman}
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


async def _build_image(
    config: Config, repodir: Path, image_tag: str,
    repo_cfg: RepoConfig, log_path: Path,
) -> bool:
    build_args = ["build", "--no-cache"]
    if repo_cfg.base_image:
        build_args.extend(["--build-arg", f"BASE_IMAGE={repo_cfg.base_image}"])
    build_args.extend(["-t", image_tag, str(repodir)])
    rc, _ = await _hpodman(config, *build_args, log_path=log_path)
    return rc == 0


async def _seed_db(
    config: Config, lock: SlotLock, repo_cfg: RepoConfig, _log_path: Path,
) -> None:
    prod = repo_cfg.prod_container
    if not prod:
        return
    slot_dir = config.slots_dir / lock.container
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "data").mkdir(exist_ok=True)
    seed_path = slot_dir / "data" / "seed.sql"
    rc, _ = await _hpodman(config, "exec", prod, "pg_dump", "-U", "rotv", "rotv",
                           log_path=seed_path)
    if rc != 0:
        seed_path.unlink(missing_ok=True)


async def _launch_container(
    config: Config, lock: SlotLock, image_tag: str, log_path: Path,
) -> bool:
    await _hpodman(config, "stop", lock.container)
    await _hpodman(config, "rm", "-f", lock.container)

    slot_dir = config.slots_dir / lock.container
    template = config.config_dir / "preview-rotv-template.env"
    if template.exists():
        slot_dir.mkdir(parents=True, exist_ok=True)
        env_content = template.read_text().replace("SLOT_NUM", str(lock.slot))
        (slot_dir / "rotv.env").write_text(env_content)

    run_args = [
        "run", "-d", "--systemd=always",
        "--memory=2g", "--memory-swap=2g",
        "--name", lock.container,
        "--network", "rotv",
        "-p", f"127.0.0.1:{lock.port}:8080",
    ]
    env_file = slot_dir / "rotv.env"
    if env_file.exists():
        run_args.extend(["--env-file", str(env_file)])
    run_args.extend(["-v", f"{slot_dir / 'data'}:/data:Z", image_tag])

    rc, _ = await _hpodman(config, *run_args, log_path=log_path)
    return rc == 0


async def _restore_db(
    config: Config, lock: SlotLock, log_path: Path,
) -> None:
    slot_dir = config.slots_dir / lock.container
    seed_path = slot_dir / "data" / "seed.sql"
    if not seed_path.exists() or seed_path.stat().st_size == 0:
        return
    for _ in range(30):
        rc, _ = await _hpodman(
            config, "exec", lock.container,
            "su", "-", "postgres", "-c", "pg_isready",
        )
        if rc == 0:
            break
        await asyncio.sleep(2)
    setup_cmd = (
        "createuser rotv 2>/dev/null; "
        "createdb -O rotv rotv 2>/dev/null; "
        "psql rotv -c 'CREATE EXTENSION IF NOT EXISTS postgis' 2>/dev/null"
    )
    await _hpodman(
        config, "exec", lock.container,
        "su", "-", "postgres", "-c", setup_cmd,
        log_path=log_path,
    )
    await _hpodman(
        config, "exec", "-i", lock.container,
        "su", "-", "postgres", "-c", "psql rotv",
        log_path=log_path,
    )


async def _healthcheck(port: int, retries: int = 12) -> bool:
    for _ in range(retries):
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-sf", f"http://127.0.0.1:{port}/",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0:
                return True
        except OSError:
            pass
        await asyncio.sleep(5)
    return False


async def deploy(run_id: str, config: Config) -> dict[str, Any]:
    state = RunState.load(run_id, config)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    meta = state.read_meta()
    repodir = config.work_dir / run_id / meta.repo
    log_path = state.run_dir / "preview.log"

    if not repodir.is_dir():
        return {"error": f"work dir missing: {repodir}"}

    repo_cfg = get_repo_config(meta.repo, config)
    slot_mgr = SlotManager(config)
    lock = await slot_mgr.allocate(run_id, meta.repo, meta.branch, repo_cfg.preview_domain)
    if lock is None:
        return {"error": f"all {config.max_slots} slots busy"}

    preview_url = f"https://{lock.container}.{lock.domain}"
    state.update_meta(
        preview_slot=lock.slot, preview_url=preview_url,
        preview_domain=lock.domain,
    )

    image_tag = f"localhost/ashigaru-preview-{run_id}:latest"
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.PREVIEW_OP,
        summary=f"Building preview from {meta.branch}",
    ))

    if not await _build_image(config, repodir, image_tag, repo_cfg, log_path):
        await slot_mgr.release(run_id)
        state.set_phase(Phase.FAILED, "Preview image build failed")
        return {"error": "image build failed", "run_id": run_id}

    await _seed_db(config, lock, repo_cfg, log_path)

    if not await _launch_container(config, lock, image_tag, log_path):
        await slot_mgr.release(run_id)
        state.set_phase(Phase.FAILED, "Preview container launch failed")
        return {"error": "container launch failed", "run_id": run_id}

    await _restore_db(config, lock, log_path)
    healthy = await _healthcheck(lock.port)

    health_msg = "healthy" if healthy else "started (healthcheck pending)"
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.PREVIEW_OP,
        summary=f"Preview {health_msg} at {preview_url}",
    ))
    state.set_phase(Phase.PREVIEW_LIVE, f"Preview live at {preview_url}")

    return {
        "run_id": run_id, "deployed": True,
        "preview_url": preview_url, "slot": lock.slot, "healthy": healthy,
    }


async def teardown(run_id: str, config: Config) -> dict[str, Any]:
    state = RunState.load(run_id, config)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    slot_mgr = SlotManager(config)
    lock = slot_mgr.find_by_run(run_id)
    if lock is None:
        return {"run_id": run_id, "freed": False, "detail": "no slot allocated"}

    await _hpodman(config, "rmi", f"localhost/ashigaru-preview-{run_id}:latest")
    freed = await slot_mgr.release(run_id)
    state.update_meta(preview_slot=None, preview_url=None)
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.PREVIEW_OP,
        summary=f"Preview slot {lock.slot} freed",
    ))
    return {"run_id": run_id, "freed": freed}
