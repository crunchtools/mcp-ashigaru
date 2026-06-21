"""Background worker orchestrator — replaces work-ticket.sh."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .activity import ActivityLog
from .agent import (
    MAX_TIER,
    build_initial_prompt,
    build_iteration_prompt,
    resolve_tier,
    run_sealed_agent,
    tier_effort,
    tier_max_turns,
    tier_model,
)
from .config import Config
from .git_ops import (
    check_gate,
    clone,
    commit_and_push,
    create_branch,
    create_pr,
    get_default_branch,
    get_prior_diff,
)
from .models import Activity, ActivityKind, Attempt, Phase
from .state import RunState


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def run_new(
    run_id: str,
    repo: str,
    issue: int,
    brief: str,
    model_hint: str,
    config: Config,
) -> None:
    """Full new-run lifecycle. Called detached via asyncio.create_task."""
    state = RunState.load(run_id, config)
    if state is None:
        return
    try:
        await _run_new_inner(state, run_id, repo, issue, brief, model_hint, config)
    except Exception as exc:
        state.set_phase(Phase.FAILED, f"Runner crash: {exc}")
        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.ERROR,
            summary=f"Runner crash: {exc}",
        ))


async def _run_new_inner(
    state: RunState,
    run_id: str,
    repo: str,
    issue: int,
    brief: str,
    model_hint: str,
    config: Config,
) -> None:
    repodir = config.work_dir / run_id / repo
    log_path = state.run_dir / "setup.log"

    # Clone if not already done by _clone_and_prepare in create_run
    if not repodir.is_dir():
        state.set_phase(Phase.CLONING, f"Cloning {config.org}/{repo}")
        if not await clone(repo, repodir, config, log_path):
            state.set_phase(Phase.FAILED, "Git clone failed")
            return
        meta = state.read_meta()
        if not meta.branch:
            branch = f"fix/issue-{issue}"
            await create_branch(repodir, branch)
            state.update_meta(branch=branch)
    else:
        for _ in range(10):
            meta_check = state.read_meta()
            if meta_check.branch:
                break
            await asyncio.sleep(1)

    meta = state.read_meta()
    branch = meta.branch

    start_tier = resolve_tier(model_hint)
    actual_model = tier_model(start_tier)
    max_turns = tier_max_turns(start_tier)
    prompt = build_initial_prompt(repo, issue, brief)

    sid = str(uuid.uuid4())
    state.set_phase(Phase.ANALYZING, f"Agent starting (tier {start_tier}, {actual_model})")
    state.update_meta(model=actual_model, current_tier=start_tier, session_id=sid)

    classifier = asyncio.create_task(
        _classify_events_live(state, state.run_dir / "events.jsonl")
    )
    effort = tier_effort(start_tier)
    exit_code = await run_sealed_agent(
        repodir, state.run_dir, prompt, actual_model, max_turns, config,
        effort=effort, session_id=sid,
    )
    classifier.cancel()

    if exit_code != 0:
        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.ERROR,
            summary=f"Agent exited with code {exit_code}",
        ))

    gate_result, gate_output = await check_gate(repodir)
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.GATE_CHECK,
        summary=f"Gate: {gate_result}", detail=gate_output,
    ))

    if gate_result != "passed":
        state.append_attempt(Attempt(
            tier=start_tier, model=actual_model,
            gate="failed", gate_output=gate_output,
            started_at=_now(),
        ))
        state.set_phase(Phase.FAILED, gate_output)
        return

    state.append_attempt(Attempt(
        tier=start_tier, model=actual_model,
        gate="passed", started_at=_now(),
    ))

    pushed = await commit_and_push(repodir, issue, branch, config, log_path, force=True)
    if not pushed:
        state.set_phase(Phase.FAILED, "git push failed")
        return

    base = await get_default_branch(repo, config)
    pr_url = await create_pr(
        repo, branch, base, issue, run_id, actual_model,
        start_tier, config, log_path,
    )
    if not pr_url:
        state.set_phase(Phase.FAILED, "PR creation failed")
        return

    state.update_meta(pr_url=pr_url)
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.GIT_OP,
        summary=f"PR created: {pr_url}",
    ))

    from .repo_config import get_repo_config
    repo_cfg = get_repo_config(repo, config)
    if repo_cfg.profile == "webapp":
        from . import preview as preview_mod
        result = await preview_mod.deploy(run_id, config)
        if result.get("error"):
            state.activity.append(Activity(
                timestamp=_now(), kind=ActivityKind.PREVIEW_OP,
                summary=f"Preview deploy failed: {result['error']}",
            ))
        else:
            state.activity.append(Activity(
                timestamp=_now(), kind=ActivityKind.PREVIEW_OP,
                summary=f"Preview live: {result.get('preview_url', '')}",
            ))

    state.set_phase(Phase.AWAITING_REVIEW, f"PR ready: {pr_url}")


async def run_iterate(
    run_id: str,
    notes: str,
    config: Config,
) -> None:
    """Re-invoke agent on existing branch with feedback. Called detached."""
    state = RunState.load(run_id, config)
    if state is None:
        return
    try:
        await _run_iterate_inner(state, run_id, notes, config)
    except Exception as exc:
        state.set_phase(Phase.FAILED, f"Runner crash: {exc}")
        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.ERROR,
            summary=f"Runner crash: {exc}",
        ))


async def _run_iterate_inner(
    state: RunState,
    run_id: str,
    notes: str,
    config: Config,
) -> None:
    meta = state.read_meta()
    repo = meta.repo
    branch = meta.branch
    current_tier = meta.current_tier or 1
    repodir = config.work_dir / run_id / repo

    has_feedback = bool(notes and notes.strip())
    if has_feedback:
        next_tier = current_tier
    else:
        next_tier = current_tier + 1
        if next_tier > MAX_TIER:
            state.set_phase(Phase.ESCALATED, f"All {MAX_TIER} tiers exhausted")
            return

    model = tier_model(next_tier)
    max_turns = tier_max_turns(next_tier)

    brief = state.read_brief()
    feedback = notes or state.read_feedback()
    prior_diff = await get_prior_diff(repodir)
    last_gate = ""
    if meta.attempts:
        last_gate = meta.attempts[-1].gate_output

    prompt = build_iteration_prompt(repo, meta.issue, brief, feedback, prior_diff, last_gate)

    sid = meta.session_id
    state.set_phase(Phase.ANALYZING, f"Iterating (tier {next_tier}, {model})")
    state.update_meta(current_tier=next_tier)

    classifier = asyncio.create_task(
        _classify_events_live(state, state.run_dir / "events.jsonl")
    )
    effort = tier_effort(next_tier)
    await run_sealed_agent(repodir, state.run_dir, prompt, model, max_turns, config,
                           effort=effort, session_id=sid,
                           resume_session=bool(sid))
    classifier.cancel()

    gate_result, gate_output = await check_gate(repodir)
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.GATE_CHECK,
        summary=f"Gate: {gate_result}", detail=gate_output,
    ))

    if gate_result == "passed":
        state.append_attempt(Attempt(
            tier=next_tier, model=model, gate="passed", started_at=_now(),
        ))
        await commit_and_push(repodir, meta.issue, branch, config,
                              state.run_dir / "setup.log", force=True)
        state.clear_feedback()
        state.set_phase(Phase.AWAITING_REVIEW, "Changes pushed after iteration")
    else:
        state.append_attempt(Attempt(
            tier=next_tier, model=model,
            gate="failed", gate_output=gate_output,
            started_at=_now(),
        ))
        state.set_phase(Phase.FAILED, gate_output)


async def _classify_events_live(state: RunState, events_path: Path) -> None:
    """Tail events.jsonl and classify tool calls into activity entries in real-time."""
    last_pos = 0
    seen_phases: set[str] = set()

    while True:
        await asyncio.sleep(2)
        if not events_path.exists():
            continue
        try:
            content = events_path.read_text()
        except OSError:
            continue
        if len(content) <= last_pos:
            continue

        new_content = content[last_pos:]
        last_pos = len(content)

        for line in new_content.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            _process_event(state, event, seen_phases)


def _process_event(
    state: RunState,
    event: dict[str, Any],
    seen_phases: set[str],
) -> None:
    if event.get("type") != "assistant":
        return
    for block in event.get("message", {}).get("content", []):
        if block.get("type") != "tool_use":
            continue
        tool_name = block.get("name", "")
        tool_input = block.get("input", {})
        kind = ActivityLog.classify_tool_call(tool_name, tool_input)
        summary, files = ActivityLog.summarize_tool_call(tool_name, tool_input)

        state.activity.append(Activity(
            timestamp=_now(), kind=kind, summary=summary, files=files,
        ))

        # Infer sub-phase transitions
        meta = state.read_meta()
        if meta.phase in (Phase.ANALYZING, Phase.IMPLEMENTING, Phase.VALIDATING):
            new_phase = _infer_sub_phase(kind, meta.phase)
            if new_phase and new_phase != meta.phase and new_phase.value not in seen_phases:
                seen_phases.add(new_phase.value)
                state.set_phase(new_phase, summary)


def _infer_sub_phase(kind: ActivityKind, current: Phase) -> Phase | None:
    if kind == ActivityKind.AGENT_EDIT:
        return Phase.IMPLEMENTING
    if kind == ActivityKind.AGENT_BASH and current == Phase.IMPLEMENTING:
        return Phase.VALIDATING
    return None
