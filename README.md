# mcp-ashigaru

**Kagetora's dispatchable dev-runner corps.** An MCP bridge that drives Claude Code
as a headless dev sub-agent on the crunchtools fleet: pull a GitHub issue, fix it
in an unprivileged sandbox, run the repo's gates, open a PR — and, on explicit
human approval, promote to production.

Named for the *ashigaru* (足軽) — the foot-soldiers a daimyo dispatches into the
field. Kagetora is the commander; these are the units it sends.

## Design

The LLM-facing surface is deliberately **thin**. The tools take a repo + issue
number and return status; the privileged work (git/gh, podman gates, prod deploy)
lives in deterministic wrapper scripts, **not** in this server and **not** in the
coding agent.

| Tool | Purpose |
|------|---------|
| `work_ticket(repo, issue)` | Start a run: clone, fix the issue with Claude (Sonnet), run gates, open a PR. Returns a `run_id`. |
| `status(run_id)` | On-demand digest of a run: phase, recent actions, gate result, PR URL. |
| `promote(pr, approval_token)` | **Gated.** Ship a reviewed PR to production. Refuses without a human-approval marker. |

## Security model

- Runs as the unprivileged **`devrunner`** user on lotor (rootless podman) — no path to root or production.
- The coding agent runs in a container with **only** a Claude token: no GitHub token, no podman socket, no prod secrets.
- **Production promotion is gated** behind explicit human approval and never reachable by the agent.

## Run

```bash
mcp-ashigaru-crunchtools --transport streamable-http --host 0.0.0.0 --port 8020
```

Reached by Kagetora through the airlock gateway (added as a backend in the
`kagetora` profile). Part of the **Ashigaru** dev-runner platform — see the
fleet spec for the full architecture.
