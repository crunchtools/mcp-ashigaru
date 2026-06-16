"""Code review — Gatehouse runner with Gemini Pro MCP fallback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config
from .models import Activity, ActivityKind
from .state import RunState


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def run_review(run_id: str, config: Config) -> dict[str, Any]:
    """Execute code review on the run's working copy.

    Tries Gatehouse first (5 concurrent Gemini Flash agents). If that fails
    (rate limit), returns a message suggesting the caller use the Gemini Pro
    MCP tool directly.
    """
    state = RunState.load(run_id, config)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    meta = state.read_meta()
    repodir = config.work_dir / run_id / meta.repo
    log_path = state.run_dir / "review.log"

    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.REVIEW_OP,
        summary="Starting code review (Gatehouse)",
    ))

    rc, output = await _run_gatehouse(repodir, log_path)
    if rc == 0:
        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.REVIEW_OP,
            summary="Code review complete (Gatehouse)",
            detail=output[-1000:],
        ))
        return {
            "run_id": run_id,
            "tool": "gatehouse",
            "passed": True,
            "output": output[-2000:],
        }

    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.REVIEW_OP,
        summary="Gatehouse failed (rate limit?), manual review recommended",
        detail=output[-500:],
    ))
    return {
        "run_id": run_id,
        "tool": "gatehouse",
        "passed": False,
        "output": output[-2000:],
        "fallback_hint": (
            "Gatehouse failed (likely Gemini free-tier rate limit). "
            "Use the Gemini Pro MCP tool (gemini_analyze_code) "
            "with the PR diff for a single-shot review."
        ),
    }


async def _run_gatehouse(repodir: Path, log_path: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "gatehouse", "--full", str(repodir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(repodir),
    )
    out, _ = await proc.communicate()
    output = out.decode() if out else ""
    with log_path.open("a") as f:
        f.write(output)
    return proc.returncode or 0, output
