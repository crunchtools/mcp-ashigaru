"""mcp-ashigaru v1.0.0 — the CrunchTools dev-ops backbone.

Single source of truth for all feature development across all agents. Josui
(desktop) and Kagetora (phone) compose these tools to drive features from
ticket to production. Cross-agent handoff is seamless — the run_id is the
shared handle; any agent can pick up at any phase.

14 MCP tools: 10 lifecycle (write), 4 read-only.
REST API on the same port (8020) for Cockpit and external callers.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from . import preview as preview_mod
from . import review as review_mod
from . import runner
from .config import Config
from .git_ops import commit_and_push, merge_pr
from .heartbeat import heartbeat_loop
from .models import Activity, ActivityKind, Phase, RunMeta, Source
from .slots import SlotManager
from .state import RunState

REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
DEFAULT_PORT = 8020

CFG = Config()


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    task = asyncio.create_task(heartbeat_loop(CFG))
    try:
        yield
    finally:
        task.cancel()


mcp = FastMCP(
    "mcp-ashigaru",
    version="1.0.0",
    instructions=(
        "CrunchTools dev-ops backbone. Any agent (Josui, Kagetora) composes "
        "these tools to drive features from ticket to production. All state "
        "lives here — the run_id is the cross-agent handoff token. "
        "Tools: create_run, dispatch_worker, run_build, create_pr, "
        "deploy_preview, request_changes, run_review, promote, "
        "teardown_preview, cancel_run, status, list_runs, run_activity, run_log."
    ),
    lifespan=_lifespan,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _strip_org(repo: str) -> str:
    return repo.rsplit("/", 1)[-1] if "/" in repo else repo


# =============================================================================
# LIFECYCLE TOOLS (write)
# =============================================================================


@mcp.tool()
async def create_run(
    repo: str,
    issue: int,
    title: str,
    brief: str = "",
    source: str = "ashigaru",
    change_type: str = "fix",
) -> dict[str, Any]:
    """Register a new run, clone the repo, create a branch. Returns run_id.
    Use get_prompt("feature_workflow") for the full playbook."""
    repo = _strip_org(repo)
    if not REPO_RE.match(repo):
        return {"error": f"invalid repo name: {repo!r}"}

    run_id = f"{repo}-{issue}-{int(datetime.now(UTC).timestamp())}"
    branch = f"{change_type}/issue-{issue}"

    src = Source.EXTERNAL
    for s in Source:
        if s.value == source:
            src = s
            break

    meta = RunMeta(
        run_id=run_id,
        repo=repo,
        issue=issue,
        title=title,
        branch=branch,
        phase=Phase.QUEUED,
        source=src,
        created=_now(),
    )
    state = RunState.create(meta, CFG)

    if brief:
        state.write_brief(brief)

    asyncio.create_task(_clone_and_prepare(run_id, repo, CFG))

    return {
        "run_id": run_id,
        "title": title,
        "repo": repo,
        "issue": issue,
        "branch": branch,
        "phase": Phase.QUEUED.value,
    }


async def _clone_and_prepare(run_id: str, repo: str, config: Config) -> None:
    state = RunState.load(run_id, config)
    if state is None:
        return
    from .git_ops import clone, create_branch

    state.set_phase(Phase.CLONING, f"Cloning {config.org}/{repo}")
    repodir = config.work_dir / run_id / repo
    if not await clone(repo, repodir, config, state.run_dir / "setup.log"):
        state.set_phase(Phase.FAILED, "Git clone failed")
        return
    meta = state.read_meta()
    await create_branch(repodir, meta.branch)
    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.GIT_OP,
        summary=f"Cloned and branched: {meta.branch}",
    ))
    state.set_phase(Phase.QUEUED, "Clone complete, awaiting dispatch")


@mcp.tool()
async def dispatch_worker(
    run_id: str,
    prompt: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Dispatch a sealed Claude Code agent in the background. Sub-phases
    update in real-time (analyzing/implementing/validating)."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    meta = state.read_meta()
    brief = prompt or state.read_brief()
    if not brief:
        return {"error": "no prompt or brief available for this run"}

    model_hint = model or CFG.default_model
    asyncio.create_task(runner.run_new(run_id, meta.repo, meta.issue, brief, model_hint, CFG))

    return {
        "run_id": run_id,
        "title": meta.title,
        "status": "dispatched",
        "model": model_hint,
    }


@mcp.tool()
async def run_build(
    run_id: str,
    command: str = "",
) -> dict[str, Any]:
    """Run a build/lint/test gate. Returns pass/fail + output."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    meta = state.read_meta()
    repodir = CFG.work_dir / run_id / meta.repo
    state.set_phase(Phase.BUILDING, "Running build gate")

    cmd = command or "echo 'no build gate configured'"
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(repodir),
    )
    out, _ = await proc.communicate()
    output = out.decode()[-3000:] if out else ""
    passed = proc.returncode == 0

    state.activity.append(Activity(
        timestamp=_now(), kind=ActivityKind.GATE_CHECK,
        summary=f"Build gate: {'passed' if passed else 'FAILED'}",
        detail=output[-500:],
    ))

    return {
        "run_id": run_id,
        "passed": passed,
        "exit_code": proc.returncode,
        "output": output,
    }


@mcp.tool()
async def create_pr(
    run_id: str,
    pr_title: str = "",
    body: str = "",
) -> dict[str, Any]:
    """Commit, push, and open a draft PR. Returns pr_url."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    meta = state.read_meta()
    repodir = CFG.work_dir / run_id / meta.repo
    log_path = state.run_dir / "setup.log"

    pushed = await commit_and_push(repodir, meta.issue, meta.branch, CFG, log_path)
    if not pushed:
        return {"error": "push failed", "run_id": run_id}

    from .git_ops import _run, get_default_branch

    base = await get_default_branch(meta.repo, CFG)
    final_title = pr_title or f"{meta.branch.split('/')[0]}: {meta.title} (#{meta.issue})"
    final_body = body or (
        f"Closes #{meta.issue}.\n\n"
        f"Run: `{run_id}` (source: {meta.source.value})\n"
        f"Model: {meta.model or 'interactive'}, tier {meta.current_tier}"
    )

    env = {"GH_TOKEN": CFG.gh_token}
    rc, out = await _run(
        "gh", "pr", "create", "--draft",
        "-R", f"{CFG.org}/{meta.repo}",
        "--base", base, "--head", meta.branch,
        "--title", final_title,
        "--body", final_body,
        env=env, log_path=log_path,
    )
    pr_url = out.strip().splitlines()[-1] if rc == 0 and out.strip() else None

    if pr_url:
        state.update_meta(pr_url=pr_url)
        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.GIT_OP,
            summary=f"PR created: {pr_url}",
        ))
    state.set_phase(Phase.AWAITING_REVIEW, f"PR: {pr_url or 'creation failed'}")

    return {"run_id": run_id, "pr_url": pr_url, "title": final_title}


@mcp.tool()
async def deploy_preview(run_id: str) -> dict[str, Any]:
    """Build and launch a webapp preview in the background. Returns immediately.
    Poll status(run_id) — phase goes to preview-live when ready, or failed."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}
    state.set_phase(Phase.BUILDING, "Preview build starting")
    asyncio.create_task(preview_mod.deploy(run_id, CFG))
    return {"run_id": run_id, "status": "deploying"}


@mcp.tool()
async def request_changes(run_id: str, notes: str = "") -> dict[str, Any]:
    """Re-invoke worker with feedback. Escalates model tier unless notes provided."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    if notes:
        state.write_feedback(notes)

    meta = state.read_meta()
    asyncio.create_task(runner.run_iterate(run_id, notes, CFG))

    return {
        "run_id": run_id,
        "title": meta.title,
        "status": "iterating",
        "current_tier": meta.current_tier,
    }


@mcp.tool()
async def run_review(run_id: str) -> dict[str, Any]:
    """Run Gatehouse code review. Falls back to Gemini Pro recommendation."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}
    state.set_phase(Phase.REVIEWING, "Code review in progress")
    return await review_mod.run_review(run_id, CFG)


@mcp.tool()
async def promote(run_id: str) -> dict[str, Any]:
    """Squash-merge the PR, teardown preview, ship. Trust-based promotion."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    meta = state.read_meta()
    if not meta.pr_url:
        return {"error": "no PR URL — create a PR first", "run_id": run_id}

    pr_num = meta.pr_url.rstrip("/").rsplit("/", 1)[-1]
    ok, detail = await merge_pr(meta.repo, int(pr_num), CFG)

    if ok:
        await preview_mod.teardown(run_id, CFG)
        state.set_phase(Phase.SHIPPED, f"PR #{pr_num} merged and shipped")
    else:
        state.activity.append(Activity(
            timestamp=_now(), kind=ActivityKind.ERROR,
            summary=f"Promote failed: {detail[:200]}",
        ))

    return {"run_id": run_id, "promoted": ok, "detail": detail[-500:]}


@mcp.tool()
async def teardown_preview(run_id: str) -> dict[str, Any]:
    """Free a preview slot without promoting."""
    return await preview_mod.teardown(run_id, CFG)


@mcp.tool()
async def cancel_run(run_id: str, reason: str = "") -> dict[str, Any]:
    """Cancel a run and free any preview slot."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}

    await preview_mod.teardown(run_id, CFG)
    state.set_phase(Phase.CANCELLED, reason or "Cancelled by foreman")
    return {"run_id": run_id, "cancelled": True}


# =============================================================================
# READ-ONLY TOOLS
# =============================================================================


@mcp.tool()
async def status(run_id: str) -> dict[str, Any]:
    """Phone-readable digest: title, phase, latest activity, PR/preview URLs."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}
    meta = state.read_meta()
    recent = state.activity.latest(5)
    return {
        "run_id": run_id,
        "title": meta.title,
        "repo": meta.repo,
        "issue": meta.issue,
        "phase": meta.phase.value,
        "source": meta.source.value,
        "current_tier": meta.current_tier,
        "model": meta.model,
        "pr_url": meta.pr_url,
        "preview_url": meta.preview_url,
        "attempts": [a.model_dump() for a in meta.attempts],
        "recent_activity": recent,
        "failure_reason": meta.failure_reason,
    }


@mcp.tool()
async def list_runs(
    repo: str | None = None,
    phase: str | None = None,
    source: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List runs newest-first. Title field is the human-readable name."""
    return {"runs": RunState.list_all(CFG, repo=repo, phase=phase, source=source, limit=limit)}


@mcp.tool()
async def run_activity(run_id: str, tail: int = 50) -> dict[str, Any]:
    """Structured activity log — the full story of a run in human-readable form."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}
    meta = state.read_meta()
    return {
        "run_id": run_id,
        "title": meta.title,
        "phase": meta.phase.value,
        "activity": state.activity.query(tail=tail),
    }


@mcp.tool()
async def run_log(run_id: str, log_name: str | None = None) -> dict[str, Any]:
    """Raw log content (agent.err, setup.log, runner.log, preview.log, or combined)."""
    state = RunState.load(run_id, CFG)
    if state is None:
        return {"error": f"unknown run_id: {run_id}"}
    return {"run_id": run_id, "log": state.get_log(log_name)}


# =============================================================================
# PROMPTS — workflow playbooks for agents
# =============================================================================


@mcp.prompt()
def feature_workflow(repo: str, issue: str, title: str) -> str:
    """Complete feature development workflow — ticket to production.

    Provides the step-by-step playbook for driving a feature using Ashigaru
    tools. Any agent (Josui on desktop, Kagetora on phone) can follow this.
    Cross-agent handoff is seamless at any step via the run_id.
    """
    return f"""# Feature Workflow: {title} ({repo} #{issue})

You are driving a feature from ticket to production using Ashigaru tools.
Follow these steps in order. You can hand off to another agent at ANY step
— just pass the run_id. All state lives in Ashigaru, not in your session.

## Step 1: Create the run
Call `create_run(repo="{repo}", issue={issue}, title="{title}",
  brief=<the filtered issue text>, source=<your agent name>)`
Save the returned `run_id` — this is your handle for every subsequent step.

## Step 2: Dispatch the worker
Build a detailed implementation prompt from the issue brief and any project
context you have. Then:
  `dispatch_worker(run_id=<run_id>, prompt=<your prompt>, model="sonnet")`
The agent runs in background. The activity log updates in real-time with
sub-phases (analyzing → implementing → validating).

## Step 3: Monitor progress
Poll `status(run_id)` periodically. Watch for:
- `phase: "analyzing"` — agent is reading code
- `phase: "implementing"` — agent is editing files
- `phase: "validating"` — agent is running tests/lint
- `phase: "awaiting-review"` — agent finished, PR created automatically
- `phase: "failed"` — check `failure_reason`, consider `request_changes()`

## Step 4: Run the build gate
Once the agent finishes:
  `run_build(run_id=<run_id>, command=<project-specific build command>)`
Check the `passed` field in the response. If it failed, use
`request_changes(run_id, notes="build failed: <details>")` to iterate.

## Step 5: Create PR (if not auto-created)
  `create_pr(run_id=<run_id>, pr_title="feat: {title} (#{issue})",
    body="Closes #{issue}")`
Returns the `pr_url`.

## Step 6: Deploy preview (webapp projects only)
  `deploy_preview(run_id=<run_id>)`
Returns `preview_url` with the correct domain (e.g. rootsofthevalley.org
for ROTV). Ask Scott to verify the preview in his browser.

## Step 7: Run code review
  `run_review(run_id=<run_id>)`
Returns Gatehouse findings. If Gatehouse fails (rate limit), use the
Gemini Pro MCP tool with the PR diff as a fallback. Present findings to
Scott — each issue should be fixed or justified.

## Step 8: Iterate if needed
If review or verification found issues:
  `request_changes(run_id=<run_id>, notes="fix the timezone bug in...")`
This re-invokes the worker with feedback. Repeat from Step 3.

## Step 9: Ship to production
  `promote(run_id=<run_id>)`
This squash-merges the PR and tears down the preview.

After promote returns, deploy to lotor:
```bash
ssh -p 22422 root@lotor.dc3.crunchtools.com "podman pull <IMAGE>:latest"
ssh -p 22422 root@lotor.dc3.crunchtools.com "systemctl restart <SERVICE>"
ssh -p 22422 root@lotor.dc3.crunchtools.com "systemctl status <SERVICE> --no-pager -l"
```

Look up the IMAGE and SERVICE from the service registry:
- rotv → quay.io/crunchtools/rotv, rootsofthevalley.org
- mcp-ashigaru → quay.io/crunchtools/mcp-ashigaru, mcp-ashigaru.crunchtools.com
- acquacotta → quay.io/fatherlinux/acquacotta, acquacotta.crunchtools.com

## Cross-agent handoff
At any step, you can hand off to another agent. Just tell them the run_id.
They call `status(run_id)` to see where things stand and pick up from there.
Scott can start on Josui (desktop), walk away, Kagetora (phone) continues,
Scott comes back to Josui — seamless, because all state is in Ashigaru."""


@mcp.prompt()
def deploy_workflow(repo: str) -> str:
    """Deploy a merged PR to production on lotor.

    Provides the step-by-step playbook for deploying code that has already
    been merged to the default branch via GHA → container registry → lotor.
    """
    return f"""# Deploy Workflow: {repo}

You are deploying merged code to production. The GHA pipeline builds the
container image on merge — your job is to pull it on lotor and restart.

## Step 1: Identify the target
Look up the deployment target for `{repo}`:

| Repo | Image | Service |
|------|-------|---------|
| rotv | quay.io/crunchtools/rotv | rootsofthevalley.org |
| mcp-ashigaru | quay.io/crunchtools/mcp-ashigaru | mcp-ashigaru.crunchtools.com |
| mcp-airlock | quay.io/crunchtools/mcp-airlock | mcp-airlock.crunchtools.com |
| acquacotta | quay.io/fatherlinux/acquacotta | acquacotta.crunchtools.com |
| proxy | quay.io/crunchtools/proxy | proxy.crunchtools.com |
| postiz | quay.io/crunchtools/postiz | postiz.crunchtools.com |
| openclaw | quay.io/crunchtools/openclaw | openclaw.crunchtools.com |

If `{repo}` is not in this table, search memory for its deployment details.

## Step 2: Wait for GHA build
```bash
gh run list --repo crunchtools/{repo} --branch main --limit 3
gh run watch <RUN_ID> --repo crunchtools/{repo}
```
If the build fails: `gh run view <RUN_ID> --log-failed --repo crunchtools/{repo}`
Do NOT proceed if the build failed.

## Step 3: Check for database migrations
```bash
gh pr diff <PR_NUMBER> --repo crunchtools/{repo} | grep -l migration
```
If migrations exist, run them BEFORE restarting:
```bash
ssh -p 22422 root@lotor.dc3.crunchtools.com \\
  "podman exec <SERVICE> psql -U postgres -d <DB> -f /app/migrations/<FILE>.sql"
```

## Step 4: Pull and restart
```bash
ssh -p 22422 root@lotor.dc3.crunchtools.com "podman pull <IMAGE>:latest"
ssh -p 22422 root@lotor.dc3.crunchtools.com "systemctl restart <SERVICE>"
```

## Step 5: Verify
```bash
ssh -p 22422 root@lotor.dc3.crunchtools.com \\
  "systemctl status <SERVICE> --no-pager -l"
```
If the service fails:
```bash
ssh -p 22422 root@lotor.dc3.crunchtools.com \\
  "journalctl -u <SERVICE> --no-pager -n 30"
```

## Step 6: Sync local checkouts
```bash
cd /var/home/fatherlinux/Documents/Professional/Projects/crunchtools/{repo} \\
  && git fetch origin --prune && git pull --ff-only
cd /home/fatherlinux/Projects/crunchtools/{repo} \\
  && git pull 2>/dev/null || true
```

## Step 7: Restart dependent services (if needed)
If you deployed mcp-ashigaru or mcp-airlock, restart the airlock gateway
so it picks up new tool signatures:
```bash
ssh -p 22422 root@lotor.dc3.crunchtools.com \\
  "systemctl restart mcp-airlock.crunchtools.com"
```"""


# =============================================================================
# REST API
# =============================================================================


@mcp.custom_route("/api/runs", methods=["GET"])
async def api_list_runs(request: Request) -> JSONResponse:
    repo = request.query_params.get("repo")
    phase = request.query_params.get("phase")
    source = request.query_params.get("source")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return JSONResponse({"error": "limit must be an integer"}, status_code=400)
    return JSONResponse(RunState.list_all(CFG, repo=repo, phase=phase, source=source, limit=limit))


@mcp.custom_route("/api/runs/{run_id}", methods=["GET"])
async def api_run_detail(request: Request) -> JSONResponse:
    run_id = request.path_params["run_id"]
    state = RunState.load(run_id, CFG)
    if state is None:
        return JSONResponse({"error": f"unknown run_id: {run_id}"}, status_code=404)
    return JSONResponse(state.get_detail())


@mcp.custom_route("/api/runs/{run_id}/activity", methods=["GET"])
async def api_run_activity(request: Request) -> JSONResponse:
    run_id = request.path_params["run_id"]
    state = RunState.load(run_id, CFG)
    if state is None:
        return JSONResponse({"error": f"unknown run_id: {run_id}"}, status_code=404)
    try:
        tail = int(request.query_params.get("tail", "50"))
    except ValueError:
        return JSONResponse({"error": "tail must be an integer"}, status_code=400)
    return JSONResponse(state.activity.query(tail=tail))


@mcp.custom_route("/api/runs/{run_id}/log", methods=["GET"])
async def api_run_log(request: Request) -> PlainTextResponse:
    run_id = request.path_params["run_id"]
    state = RunState.load(run_id, CFG)
    if state is None:
        return PlainTextResponse(f"unknown run_id: {run_id}", status_code=404)
    log_name = request.query_params.get("name")
    return PlainTextResponse(state.get_log(log_name))


@mcp.custom_route("/api/slots", methods=["GET"])
async def api_slots(_request: Request) -> JSONResponse:
    mgr = SlotManager(CFG)
    return JSONResponse(mgr.list_all())


# =============================================================================
# ENTRY POINT
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Ashigaru dev-ops backbone MCP server")
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
