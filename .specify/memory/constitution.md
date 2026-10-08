# mcp-ashigaru-crunchtools Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-10-02
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.21.0
> **Profile:** MCP Server

This file holds what is specific to mcp-ashigaru. The fleet rules and the
MCP Server profile apply at the inherited version and are checked against
this repo's files by `constitution.yml`. They are not restated here; where
this repo departs from the profile, the departure and its reason are below.

## Purpose

The crunchtools dev-ops backbone: an MCP server that dispatches Claude Code
as sealed, headless worker containers to take a GitHub issue from a
maintainer-supplied brief to a gated PR, a preview deployment, and, on
instruction, a squash-merge to production. Kagetora is the foreman that
calls it; the human holds the approval.

## Authority Split

The component that can be talked into something has the least authority:

- **Worker container** (`mcp-ashigaru-agent-claude`) receives only
  `CLAUDE_CODE_OAUTH_TOKEN` and the checkout mounted at `/work`. No GitHub
  token, no podman socket, no production secrets. Its tools are limited to
  `Read,Edit,Write,Bash,Glob,Grep`.
- **This server** holds `GH_TOKEN` and the podman sockets, and does git, gh
  and podman through fixed subprocess calls, never through an LLM.
- **The worker never reads the raw GitHub issue**; it gets the brief passed
  to `create_run`, which arrives through the gateway's filtered lane.

## Gate Is the Arbiter

A worker's own claim of success counts for nothing; the repo's gates decide.
A run climbs three tiers (40 turns at medium effort, 60 at high, 80 at
xhigh). Each iteration gets the prior diff, the last gate output and any
reviewer notes; an iteration without notes moves up a tier, and past tier 3
the run is marked `escalated` for a human. `promote` runs the gates before squash-merging unless
the caller passes `skip_gates`, and tears down the run's preview afterwards.

## Credentials

| Variable | Use |
|----------|-----|
| `GH_TOKEN` | git clone/push and `gh` against the target repo |
| `CLAUDE_CODE_OAUTH_TOKEN` | Passed into the worker container; its only credential |
| `ASHIGARU_NOTIFY_WEBHOOK_SECRET` | HMAC-SHA256 signing (`X-Hub-Signature-256`) for the notification webhook |
| `ASHIGARU_MATRIX_ACCESS_TOKEN` | Matrix E2EE notification channel |

All are read from the environment once at start.

## Host Integration and State

- Runs as uid 1000 (`devrunner`) on the `crunchtools` network. Workers are
  launched through devrunner's rootless podman socket (`CONTAINER_HOST`);
  preview deployments use the host podman socket (`ASHIGARU_HOST_PODMAN`).
- Run state lives under `ASHIGARU_STATE_DIR` (default
  `/home/devrunner/ashigaru`): `runs/<run_id>/` (events, logs, metadata),
  `work/` (checkouts) and `matrix-crypto/` (persistent E2EE keys). Preview
  slot locks live in `ASHIGARU_SLOTS_DIR` (default `/srv/ashigaru/slots`,
  at most `ASHIGARU_MAX_SLOTS`, default 5) and per-repo gate config in
  `ASHIGARU_CONFIG_DIR` (default `/srv/ashigaru/config`).
- Runs orphaned by a restart are recovered at startup.
- Besides MCP tools, the server exposes a read-mostly REST API under
  `/api/runs` and `/api/slots` for the `cockpit-ashigaru` Cockpit plugin
  (spec 001); `DELETE /api/runs/{run_id}` is its only write.

## Base Image Exception

Both images build on `registry.access.redhat.com/ubi10/ubi-minimal`, not
Hummingbird. The server shells out to git, `gh` (from the GitHub CLI RPM
repo) and `podman-remote`, and builds `matrix-nio[e2e]` with a C++
toolchain; the worker needs Node.js for the Claude Code CLI plus Python, uv,
git and build tools to run fleet gates. Neither fits a distroless runtime.

## Distribution Exception

Container images only (`container.yml` and `agent-container.yml`, dual-push
to Quay and GHCR); the package is not published to PyPI. The server is only
useful next to a podman socket and the devrunner state directory, so there
is no uvx or pip use case. The worker's Claude Code CLI version is pinned
with the `CLAUDE_VERSION` build arg.

## Instance

| Context | Name |
|---------|------|
| GitHub repo | `crunchtools/mcp-ashigaru` |
| Python package | `mcp-ashigaru-crunchtools` (module `mcp_ashigaru`) |
| Server image | `quay.io/crunchtools/mcp-ashigaru` |
| Worker image | `quay.io/crunchtools/mcp-ashigaru-agent-claude` |
| HTTP port | 8020 |

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-10-02 | Initial constitution, written as a v1.18.0 manifest from the README and code |
