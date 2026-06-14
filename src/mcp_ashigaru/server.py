"""mcp-ashigaru — lets Kagetora drive Claude Code as a headless dev sub-agent.

This is the always-on bridge between Kagetora (the foreman) and the deterministic
wrapper scripts that do the real work. On lotor it runs like every other MCP
server: a root-managed system container on the `crunchtools` network, reachable
by Kagetora through the airlock gateway (added as a backend in the kagetora
profile). The unprivileged `devrunner` user's rootless podman socket is bind
-mounted in, so the wrapper scripts launch the *workers* (Claude Code) rootless
as devrunner — only the workers run rootless, never this surface.

Design invariants (mirror the security model of the sandbox):
  - The LLM-facing surface is THIN. These tools take a repo + issue number and
    return status; they do not let the caller run arbitrary commands.
  - The privileged work (git/gh, podman gates, prod deploy) lives in the
    deterministic wrapper scripts, NOT here and NOT in the coding agent.
  - `promote` is GATED: it refuses unless a human-approval marker is present on
    the PR, so a confused or injected caller cannot ship to production.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# Wrapper scripts are baked into the image (RUNNER_DIR). Per-run state — the repo
# clone and the event stream — lives on a host volume (STATE_DIR) that is mounted
# at the SAME path here and is visible to devrunner's rootless podman, so the
# worker containers can bind-mount the clone. (A `-v` issued over the mounted
# socket is resolved host-side, not inside this container — hence the shared path.)
RUNNER_DIR = Path(os.environ.get("ASHIGARU_RUNNER_DIR", "/app/runner"))
STATE_DIR = Path(os.environ.get("ASHIGARU_STATE_DIR", "/home/devrunner/ashigaru"))
RUNS_DIR = STATE_DIR / "runs"
WRAPPER = RUNNER_DIR / "work-ticket.sh"

REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")

mcp = FastMCP(
    "mcp-ashigaru",
    version="0.1.0",
    instructions=(
        "Drive Claude Code as a headless dev sub-agent on the crunchtools fleet. "
        "work_ticket starts a run (clone repo, fix a GitHub issue with Sonnet, run "
        "the repo's gates, open a PR). status returns a digest of a run on demand. "
        "promote ships a reviewed PR to production and is GATED on human approval."
    ),
)


def _run_dir(run_id: str) -> Path:
    # run_id is server-minted, but constrain anyway (defense in depth).
    if not re.fullmatch(r"[a-z0-9-]{1,64}", run_id):
        raise ValueError("invalid run_id")
    return RUNS_DIR / run_id


def _digest(run_id: str) -> dict[str, Any]:
    """Summarize a run's persisted event stream into a phone-readable digest."""
    d = _run_dir(run_id)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    events_path = d / "events.jsonl"
    phase, last_actions, gate, pr_url, is_error = meta.get("phase", "unknown"), [], None, meta.get("pr_url"), None
    if events_path.exists():
        for line in events_path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "assistant":
                for b in e.get("message", {}).get("content", []):
                    if b.get("type") == "tool_use":
                        last_actions.append(b.get("name", ""))
            elif e.get("type") == "result":
                is_error = e.get("is_error")
    return {
        "run_id": run_id,
        "repo": meta.get("repo"),
        "issue": meta.get("issue"),
        "phase": phase,  # diagnosing | editing | gating | awaiting-approval | shipped | failed
        "recent_actions": last_actions[-6:],
        "gate": meta.get("gate"),
        "pr_url": pr_url,
        "agent_error": is_error,
    }


@mcp.tool()
async def work_ticket(repo: str, issue: int) -> dict[str, Any]:
    """Start a dev run: clone <repo>, fix GitHub issue #<issue> with Claude (Sonnet),
    run the repo's quality gates, and open a PR. Returns a run_id immediately; the
    run continues in the background. Poll status(run_id) for progress.

    Args:
        repo: crunchtools repo name (e.g. "rotv").
        issue: GitHub issue number to work.
    """
    if not REPO_RE.match(repo):
        return {"error": f"invalid repo name: {repo!r}"}
    if issue <= 0:
        return {"error": "issue must be a positive integer"}
    # The wrapper mints the run_id, sets up runs/<id>/, and detaches the agent.
    proc = await asyncio.create_subprocess_exec(
        "bash", str(WRAPPER), repo, str(issue),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    run_id = out.decode().strip().splitlines()[-1] if out else ""
    if not run_id:
        return {"error": "wrapper did not return a run_id", "raw": out.decode()[-300:]}
    return {"run_id": run_id, "repo": repo, "issue": issue, "status": "started"}


@mcp.tool()
async def status(run_id: str) -> dict[str, Any]:
    """Return an on-demand digest of a run: current phase, recent agent actions,
    gate result, and PR URL. This is what Kagetora answers from when you ask
    "what's the runner doing?"."""
    try:
        return _digest(run_id)
    except (ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}


@mcp.tool()
async def promote(pr: int, approval_token: str) -> dict[str, Any]:
    """GATED. Promote a reviewed PR to production via the repo's deploy path.

    Refuses unless `approval_token` matches the human-approval marker recorded
    for this PR (set only through a confirmed Signal approval). This is the hard
    gate on the one irreversible action — the coding agent never reaches it.

    Args:
        pr: PR number to promote.
        approval_token: the approval marker from the confirmed human gate.
    """
    proc = await asyncio.create_subprocess_exec(
        "bash", str(RUNNER_DIR / "promote.sh"), str(pr), approval_token,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    ok = proc.returncode == 0
    return {"pr": pr, "promoted": ok, "detail": out.decode()[-500:]}


def main() -> None:
    """Entry point. Default stdio; the lotor systemd unit runs streamable-http:8020
    on the crunchtools network so the airlock gateway can reach it."""
    import argparse

    parser = argparse.ArgumentParser(description="MCP server for the Ashigaru dev runners")
    parser.add_argument(
        "--transport", choices=["stdio", "sse", "streamable-http"], default="stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)
