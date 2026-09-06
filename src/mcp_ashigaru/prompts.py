"""Workflow playbooks returned to agents as MCP prompts.

The playbook bodies are markdown, so lines inside them begin with `#`.
They are string content, not comments.
"""

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Attach the workflow playbooks to the given server."""
    mcp.prompt()(feature_workflow)
    mcp.prompt()(deploy_workflow)



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
