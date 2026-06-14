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
  - `promote` is trust-based (no token): it squash-merges a reviewed PR on the
    maintainer's Signal instruction, relying on airlock-filtered content and a
    revertable merge rather than an out-of-band token the agent would hold.
"""

from __future__ import annotations

import argparse
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
RUNNER_DIR = Path(os.environ.get("ASHIGARU_RUNNER_DIR", "/app/scripts"))
STATE_DIR = Path(os.environ.get("ASHIGARU_STATE_DIR", "/home/devrunner/ashigaru"))
RUNS_DIR = STATE_DIR / "runs"
WRAPPER = RUNNER_DIR / "work-ticket.sh"

REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")

# Default streamable-http port for the lotor systemd unit (see Containerfile).
DEFAULT_PORT = 8020

DEPLOY_PREVIEW = RUNNER_DIR / "deploy-preview.sh"
TEARDOWN_PREVIEW = RUNNER_DIR / "teardown-preview.sh"

mcp = FastMCP(
    "mcp-ashigaru",
    version="0.5.0",
    instructions=(
        "Drive Claude Code as a headless dev sub-agent on the crunchtools fleet. "
        "work_ticket starts a run (clone repo, fix a GitHub issue, auto-escalate "
        "through model tiers, open a PR). deploy_preview launches a live webapp "
        "preview at development{N}.crunchtools.com. request_changes feeds back "
        "notes and re-invokes the worker on the same branch. status returns a "
        "digest of a run. promote squash-merges a reviewed PR. teardown_preview "
        "frees a preview slot."
    ),
)


def _digest(run_id: str) -> dict[str, Any]:
    """Summarize a run's persisted event stream into a phone-readable digest."""
    if not re.fullmatch(r"[a-z0-9-]{1,64}", run_id):
        raise ValueError("invalid run_id")
    d = RUNS_DIR / run_id
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    events_path = d / "events.jsonl"
    phase = meta.get("phase", "unknown")
    pr_url = meta.get("pr_url")
    last_actions: list[str] = []
    is_error = None
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
        "phase": phase,
        "recent_actions": last_actions[-6:],
        "gate": meta.get("gate"),
        "pr_url": pr_url,
        "preview_url": meta.get("preview_url"),
        "preview_slot": meta.get("preview_slot"),
        "current_tier": meta.get("current_tier"),
        "attempts": meta.get("attempts", []),
        "agent_error": is_error,
    }


@mcp.tool()
async def work_ticket(
    repo: str, issue: int, brief: str, model: str | None = None
) -> dict[str, Any]:
    """Start a dev run: clone <repo>, fix GitHub issue #<issue> from the provided
    brief, run the repo's quality gates, and open a draft PR. Returns a run_id
    immediately; the run continues in the background. Poll status(run_id).

    The issue text MUST be passed as `brief` — already airlock-filtered (fetched
    via mcp-github through the gateway). This server never reads the GitHub issue
    directly, and the sealed, network-isolated sub-agent only ever sees `brief`.

    Args:
        repo: repo name; accepts "rotv" or "crunchtools/rotv" (org is stripped).
        issue: GitHub issue number (used for branch naming / labeling only).
        brief: the filtered issue text / task description for the agent to work.
        model: optional starting model tier (e.g. "sonnet", "opus"). Sonnet->Opus
            escalation is handled internally; this only sets the starting point.
    """
    if "/" in repo:
        repo = repo.rsplit("/", 1)[-1]
    if not REPO_RE.match(repo):
        return {"error": f"invalid repo name: {repo!r}"}
    if issue <= 0:
        return {"error": "issue must be a positive integer"}
    if not brief or not brief.strip():
        return {"error": "brief is required (the filtered issue text for the agent)"}
    # The wrapper mints the run_id, sets up runs/<id>/, and detaches the agent.
    args = ["bash", str(WRAPPER), repo, str(issue), brief]
    if model:
        args.append(model)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
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
async def promote(repo: str, pr: int) -> dict[str, Any]:
    """Promote a reviewed PR to production by merging it (squash merge).

    Trust-based — there is NO approval token. Promotion is authorized by you, the
    foreman, acting on the maintainer's Signal instruction; the content you reason
    over is airlock-filtered. Confirm the PR is clear first with
    get_pull_request_checks (remember: skipped != failed). The merge is the
    promotion — the repo's GHA pipeline builds and ships from the default branch.

    Args:
        repo: repo name; accepts "rotv" or "crunchtools/rotv" (org is stripped).
        pr: pull request number to merge.
    """
    if "/" in repo:
        repo = repo.rsplit("/", 1)[-1]
    if not REPO_RE.match(repo):
        return {"error": f"invalid repo name: {repo!r}"}
    if pr <= 0:
        return {"error": "pr must be a positive integer"}
    proc = await asyncio.create_subprocess_exec(
        "bash", str(RUNNER_DIR / "promote.sh"), repo, str(pr),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    ok = proc.returncode == 0
    return {"repo": repo, "pr": pr, "promoted": ok, "detail": out.decode()[-500:]}


@mcp.tool()
async def deploy_preview(run_id: str) -> dict[str, Any]:
    """Launch an ephemeral webapp preview for a completed run. Builds the image
    from the PR branch code, seeds the DB from production, and routes
    development{N}.crunchtools.com to it. Returns the preview URL immediately;
    the build+seed runs in the background — poll status(run_id) for phase
    changes (deploying-preview → on-dev).

    Args:
        run_id: the run_id from a prior work_ticket call.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,64}", run_id):
        return {"error": "invalid run_id"}
    rundir = RUNS_DIR / run_id
    if not (rundir / "meta.json").exists():
        return {"error": f"unknown run_id: {run_id}"}
    proc = await asyncio.create_subprocess_exec(
        "bash", str(DEPLOY_PREVIEW), run_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    output = out.decode()[-500:] if out else ""
    ok = proc.returncode == 0
    meta = json.loads((rundir / "meta.json").read_text())
    return {
        "run_id": run_id,
        "deployed": ok,
        "preview_url": meta.get("preview_url"),
        "preview_slot": meta.get("preview_slot"),
        "detail": output,
    }


@mcp.tool()
async def request_changes(run_id: str, notes: str = "") -> dict[str, Any]:
    """Feed back changes to a completed or failed run. Re-invokes the sealed
    worker on the existing branch with the feedback (notes + prior gate failure),
    escalating to the next model tier. If notes are empty, auto-escalates from
    the gate failure alone.

    For webapp-profile runs, the preview is rebuilt after the iteration succeeds.
    Returns immediately; poll status(run_id) for progress.

    Args:
        run_id: the run_id from a prior work_ticket call.
        notes: feedback from the foreman (what to fix / change). Optional.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,64}", run_id):
        return {"error": "invalid run_id"}
    rundir = RUNS_DIR / run_id
    if not (rundir / "meta.json").exists():
        return {"error": f"unknown run_id: {run_id}"}
    args = ["bash", str(WRAPPER), "--iterate", run_id]
    if notes and notes.strip():
        (rundir / "feedback.txt").write_text(notes.strip())
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    output = out.decode()[-300:] if out else ""
    meta = json.loads((rundir / "meta.json").read_text())
    return {
        "run_id": run_id,
        "status": meta.get("phase", "unknown"),
        "current_tier": meta.get("current_tier"),
        "detail": output,
    }


@mcp.tool()
async def teardown_preview(run_id: str) -> dict[str, Any]:
    """Stop and clean up a preview slot. Frees the development{N} slot for reuse.
    Called automatically by promote, but can also be called manually.

    Args:
        run_id: the run_id whose preview to tear down.
    """
    if not re.fullmatch(r"[a-z0-9-]{1,64}", run_id):
        return {"error": "invalid run_id"}
    rundir = RUNS_DIR / run_id
    if not (rundir / "meta.json").exists():
        return {"error": f"unknown run_id: {run_id}"}
    proc = await asyncio.create_subprocess_exec(
        "bash", str(TEARDOWN_PREVIEW), run_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    ok = proc.returncode == 0
    return {"run_id": run_id, "freed": ok, "detail": (out.decode()[-300:] if out else "")}


def main() -> None:
    """Entry point. Default stdio; the lotor systemd unit runs streamable-http:8020
    on the crunchtools network so the airlock gateway can reach it."""
    parser = argparse.ArgumentParser(description="MCP server for the Ashigaru dev runners")
    parser.add_argument(
        "--transport", choices=["stdio", "sse", "streamable-http"], default="stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)
