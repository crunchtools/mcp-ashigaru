"""Code review — containerized gate runners (Gatehouse, Gourmand, etc.)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config
from .models import Activity, ActivityKind, GateConfig
from .podman_utils import hpodman
from .repo_config import get_repo_config
from .state import RunState


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def run_gate(
    gate: GateConfig, repodir: Path, log_path: Path, config: Config,
) -> tuple[int, str]:
    """Run a quality gate as a sidecar container with a read-only bind mount."""
    return await hpodman(
        config,
        "run", "--rm",
        "-v", f"{repodir}:/work:ro,z",
        gate.image, "/work",
        log_path=log_path,
        capture=True,
    )


async def run_gates(run_id: str, config: Config) -> dict[str, Any]:
    """Run all configured quality gates for a repo. Returns pass/fail + per-gate output."""
    state = RunState.load(run_id, config)
    if state is None:
        return {"error": f"unknown run_id: {run_id}", "passed": False}

    meta = state.read_meta()
    repo_cfg = get_repo_config(meta.repo, config)

    if not repo_cfg.gates:
        return {"run_id": run_id, "passed": True, "skipped": True, "detail": "no gates configured"}

    repodir = config.work_dir / run_id / meta.repo
    results: list[dict[str, Any]] = []
    all_passed = True

    for gate in repo_cfg.gates:
        log_path = state.run_dir / f"gate-{gate.name}.log"

        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.GATE_CHECK,
            summary=f"Running gate: {gate.name}",
        ))

        rc, output = await run_gate(gate, repodir, log_path, config)
        passed = rc == 0

        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.GATE_CHECK,
            summary=f"Gate {gate.name}: {'passed' if passed else 'FAILED'}",
            detail=output[-500:],
        ))

        results.append({
            "name": gate.name,
            "passed": passed,
            "output": output[-2000:],
        })

        if not passed:
            all_passed = False
            break

    return {"run_id": run_id, "passed": all_passed, "gates": results}


async def run_review(run_id: str, config: Config) -> dict[str, Any]:
    """Execute code review via configured gates. Falls back to Gemini Pro hint."""
    return await run_gates(run_id, config)
