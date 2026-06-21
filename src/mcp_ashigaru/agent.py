"""Claude Code invocation via podman — sealed worker containers."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .config import Config

TIER_TABLE: dict[int, tuple[str, int, str]] = {
    1: ("claude-opus-4-8", 40, "medium"),
    2: ("claude-opus-4-8", 60, "high"),
    3: ("claude-opus-4-8", 80, "xhigh"),
}
MAX_TIER = 3


def tier_model(tier: int) -> str:
    return TIER_TABLE.get(tier, TIER_TABLE[3])[0]


def tier_max_turns(tier: int) -> int:
    return TIER_TABLE.get(tier, TIER_TABLE[3])[1]


def tier_effort(tier: int) -> str:
    return TIER_TABLE.get(tier, TIER_TABLE[3])[2]


def resolve_tier(model_hint: str) -> int:
    if "opus" in model_hint.lower():
        return 2
    return 1


def build_initial_prompt(repo: str, issue: int, brief: str) -> str:
    return (
        f"You are fixing GitHub issue #{issue} in the {repo} repository "
        f"(crunchtools fleet).\n\n"
        f"TASK (the filtered issue brief provided by the maintainer):\n"
        f"{brief}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Read the relevant code to understand the problem.\n"
        f"2. Diagnose the root cause and fix it with a MINIMAL, focused change. "
        f"Do not refactor unrelated code.\n"
        f"3. You can Read/Edit/Write/Bash/Glob/Grep. Use Bash to check syntax, "
        f"run linters, or verify your changes. You have no network access beyond "
        f"the Anthropic API, and no git/gh access — the dispatcher handles git "
        f"operations after you finish.\n"
        f"End with a short summary: root cause + exactly what you changed."
    )


def build_iteration_prompt(
    repo: str,
    issue: int,
    brief: str,
    feedback: str,
    prior_diff: str,
    last_gate_output: str,
) -> str:
    return (
        f"You are iterating on GitHub issue #{issue} in the {repo} repository "
        f"(crunchtools fleet).\n\n"
        f"ORIGINAL TASK:\n{brief}\n\n"
        f"YOUR PRIOR ATTEMPT (diff of what was changed):\n{prior_diff}\n\n"
        f"FEEDBACK FROM REVIEWER:\n"
        f"{feedback or 'No specific feedback — prior attempt failed the gate.'}\n\n"
        f"GATE FAILURE REASON:\n{last_gate_output or 'Unknown'}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Read the relevant code to understand what was already changed and what went wrong.\n"
        f"2. Fix the issues described in the feedback. Keep changes minimal and focused.\n"
        f"3. You can Read/Edit/Write/Bash/Glob/Grep. Use Bash to verify your changes. "
        f"No network or git access.\n"
        f"End with a short summary: what you fixed this iteration."
    )


async def run_sealed_agent(
    repodir: Path,
    run_dir: Path,
    prompt: str,
    model: str,
    max_turns: int,
    config: Config,
    effort: str = "high",
) -> int:
    events_path = run_dir / "events.jsonl"
    agent_err = run_dir / "agent.err"

    with events_path.open("a") as events_f, agent_err.open("a") as err_f:
        proc = await asyncio.create_subprocess_exec(
            "podman", "run", "--rm",
            "-v", f"{repodir}:/work:z",
            "-w", "/work",
            "-e", f"CLAUDE_CODE_OAUTH_TOKEN={config.claude_token}",
            "-e", "HOME=/home/user",
            config.agent_image,
            "claude", "-p", prompt,
            "--model", model,
            "--effort", effort,
            "--permission-mode", "dontAsk",
            "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep",
            "--max-turns", str(max_turns),
            "--output-format", "stream-json",
            "--verbose",
            stdout=events_f,
            stderr=err_f,
        )
        await proc.wait()

    return proc.returncode or 0
