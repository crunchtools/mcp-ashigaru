# mcp-ashigaru

**Kagetora's dispatchable dev-runner corps.** An MCP server that lets Kagetora drive
Claude Code as a headless dev sub-agent across the crunchtools fleet: pull a GitHub
issue, fix it in an unprivileged sandbox, run the repo's gates, open a PR — and, on
explicit human approval, promote to production. The goal is light development from a
phone: text Kagetora "work `<repo>` #N," review what comes back, approve, ship.

Named for the *ashigaru* (足軽) — the foot-soldiers a daimyo dispatched into the
field. Kagetora is the commander; these are the units it sends.

> **Status:** alpha / under active construction. See [Roadmap](#roadmap-whats-in-place) for what's live vs. pending.

---

## Architecture

Three roles, deliberately kept apart so the component that can be *talked into*
something bad has the least authority, and the component *with* authority can't be
talked into anything:

```
you (phone) ──Signal──▶ Kagetora ──▶ airlock gateway ──▶ mcp-ashigaru ──▶ wrapper scripts ──▶ agent container
   (the boss)          (foreman, LLM)   (single secured     (this repo —     (deterministic;        (Claude Code,
                                         endpoint)           thin tool surface) hold the creds)       sealed sandbox)
```

| Role | What it is | Authority |
|------|-----------|-----------|
| **Kagetora** | The foreman (Hermes agent, Signal interface). Decides what work happens, holds the approval gates. | An LLM → persuadable → holds **no** dangerous powers directly. |
| **mcp-ashigaru** | This server. A **thin** MCP surface (`work_ticket`/`status`/`promote`). | Translates intent → wrapper invocations. No arbitrary command surface. |
| **Wrapper scripts** | Deterministic bash (`work-ticket.sh`, `promote.sh`). | Hold the GitHub token, run podman gates, do git/gh. **Not an LLM** → can't be prompt-injected. |
| **Agent container** | Claude Code (`claude -p`), sealed. | Edits code only. **Only** a Claude token — no GH token, no podman socket, no prod secrets. |

Reached by Kagetora through the **airlock gateway** (added as a backend in the
`kagetora` profile), so the same single-endpoint + defense pipeline that fronts the
rest of the fleet also fronts this. Part of the **Ashigaru** dev-runner platform —
see the fleet spec for the full design (pool of `ashigaru-1..5`, `code`/`webapp`
profiles, the merge-train, web previews).

## Tools

| Tool | Purpose |
|------|---------|
| `work_ticket(repo, issue, brief, model)` | Start a run: clone `repo`, fix issue `#issue`, run the repo's gates, open a PR. `brief` is a maintainer-supplied summary of the issue (airlock-filtered; the sub-agent never reads the raw GitHub issue). `model` optionally pins the starting model tier. Runs the **escalation ladder** internally (below). Returns a `run_id`. |
| `status(run_id)` | On-demand digest: phase, recent agent actions, which **model tier** the run reached, **live CI/build checks** for the PR, and the PR URL. This is what Kagetora answers from when you ask "what's the status of the builds?" |
| `promote(repo, pr)` | Squash-merge a reviewed PR to ship via the repo's pipeline. **Trust-based** — no approval token; authorized by your Signal instruction to Kagetora, acting on airlock-filtered content. |

## Model escalation (cost-tiered intelligence)

Every run starts cheap and escalates only when the work proves hard. **The gate is
the arbiter — never the agent's self-assessment.**

```
Tier 1  Sonnet              ──▶ gate ─pass─▶ PR
                                  └─fail─▶
Tier 2  Opus (failure fed back) ──▶ gate ─pass─▶ PR
                                       └─fail─▶
Tier 3  Opus, high/xhigh effort ──▶ gate ─pass─▶ PR
                                         └─fail─▶ escalate to human (Kagetora pings you)
```

Most routine fixes land at **Sonnet** prices; only sticky bugs spend **Opus** tokens.
The diff + gate failure from each tier is fed to the next so it iterates rather than
starting cold. `status` reports which tier a run reached.

## Security model

- **Unprivileged sandbox.** Everything runs as the `devrunner` user with rootless podman — no root, no sudo, no path to production, prod secrets, or other services. Blast radius = devrunner's sandbox.
- **Capability starvation for the agent.** The coding agent's container holds *only* a Claude token. No GitHub token (can't push or touch other repos), no podman socket, no prod creds. Its entire reach is "edit files in this one checkout."
- **Deterministic wrappers hold the keys.** git/gh, podman gates, and deploy live in fixed bash scripts that can't be prompt-injected — not in the LLM surface and not in the agent.
- **Production promotion is trust-based, not token-gated.** It is authorized by the maintainer's Signal instruction to Kagetora — designed for phone-driven ops — acting on airlock-filtered content. Defense in depth comes from that filtered content lane plus the fact that a squash-merge is revertable and host rollout is a separate step, not from an out-of-band token the agent would have to hold.

## Run

```bash
mcp-ashigaru-crunchtools --transport streamable-http --host 0.0.0.0 --port 8020
# or: python -m mcp_ashigaru --transport streamable-http --port 8020
```

Deployed as a systemd unit **run under the `devrunner` user**, on the
`crunchtools` network, so it inherits the unprivileged sandbox and can reach
devrunner's rootless podman socket to launch agent containers and run gates.

## Environment Variables

All variables are optional and read once at process start (dataclass defaults
below are exactly what `Config` falls back to; none are re-read at runtime).

| Variable | Default | Description |
|----------|---------|-------------|
| `ASHIGARU_STATE_DIR` | `/home/devrunner/ashigaru` | Root directory for run state; `runs/` and `work/` are subdirectories of this. |
| `ASHIGARU_ORG` | `crunchtools` | GitHub org Ashigaru operates against. |
| `ASHIGARU_AGENT_IMAGE` | `quay.io/crunchtools/mcp-ashigaru-agent-claude:latest` | Container image launched to run the coding agent. |
| `CONTAINER_HOST` | `unix:///run/podman/podman.sock` | Podman socket for containers created from *inside* Ashigaru's own container. |
| `ASHIGARU_HOST_PODMAN` | `unix:///run/host-podman/podman.sock` | Podman socket for the *host's* podman (bind-mounted in), used for privileged host-level operations such as preview deployments. |
| `ASHIGARU_SLOTS_DIR` | `/srv/ashigaru/slots` | Directory holding per-slot lock files for the preview-slot allocator. |
| `ASHIGARU_CONFIG_DIR` | `/srv/ashigaru/config` | Directory for repo-specific gate/config files. |
| `ASHIGARU_MAX_SLOTS` | `5` | Maximum number of concurrent preview deployment slots. |
| `GH_TOKEN` | `""` | GitHub token used for git clone/push and `gh` CLI operations against the target repo. Not validated at startup -- git/gh operations fail downstream if unset. |
| `CLAUDE_CODE_OAUTH_TOKEN` | `""` | Claude Code OAuth token passed into the coding agent's container (its *only* credential -- see Security model). |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Default Claude model for agent runs when a run doesn't request a specific tier. |
| `ASHIGARU_NOTIFY_CMD` | `""` | Shell-command notification channel; disabled unless set. The command is `shlex.split()` into argv (no shell) with the message appended as the final argument. Independent of the webhook and Matrix channels -- all configured channels fire on every notification. |
| `ASHIGARU_NOTIFY_WEBHOOK` | `""` | HTTP(S) webhook notification channel; disabled unless set. POSTed as JSON with an `X-Hub-Signature-256` HMAC-SHA256 header. |
| `ASHIGARU_NOTIFY_WEBHOOK_SECRET` | `""` | HMAC signing secret for `ASHIGARU_NOTIFY_WEBHOOK`. |
| `ASHIGARU_HEARTBEAT_INTERVAL` | `300` | Seconds between heartbeat notifications while a run is active. Heartbeats are skipped entirely if set to `0` or less. |
| `ASHIGARU_MATRIX_HOMESERVER` | `""` | Matrix homeserver URL for E2EE notifications. The Matrix channel only activates when this, `ASHIGARU_MATRIX_ACCESS_TOKEN`, and `ASHIGARU_MATRIX_ROOM_ID` are all set. |
| `ASHIGARU_MATRIX_ACCESS_TOKEN` | `""` | Matrix account access token. |
| `ASHIGARU_MATRIX_ROOM_ID` | `""` | Matrix room ID notifications are posted to. |
| `ASHIGARU_MATRIX_MENTION_USER` | `""` | Matrix user ID to @-mention on each notification. No mention is added if unset. |
| `ASHIGARU_MATRIX_DEVICE_ID` | `ASHIGARU_BOT` | Matrix device ID for the E2EE session. |
| `ASHIGARU_MATRIX_CRYPTO_DIR` | `""` | Directory for the Matrix E2EE crypto store. Empty means no persistent store is loaded (session-only). |

## Build & deploy pipeline

- **Image is built and pushed by GHA only — never hand-pushed.** `quay.io/crunchtools/mcp-ashigaru` (+ ghcr) via `.github/workflows/container.yml`, dual-push per the crunchtools constitution. A local `podman push` to the registry is **not** part of the flow.
- **The repo is public.** Required because crunchtools is a GitHub **Free** org, and Free orgs cannot expose org-level Actions secrets (`QUAY_USERNAME`/`QUAY_PASSWORD`) to **private** repos — the secrets list as "available" via the API but arrive empty at runtime. Public repos get them. (No secrets live in this repo; tokens are runtime env on the deployment host.)
- **Deploy** pulls the GHA-built image and runs it as the `devrunner` systemd unit; making it reachable from an MCP client is the deployment's decision (a gateway backend entry, a tunnel, etc.), not something this repo prescribes.

## Design decisions & gotchas (the record)

- **Gate is the arbiter, not the agent.** Maiden run (ROTV #475): the agent produced a confident, plausible fix that *failed CI* — caught before prod. That's the system working: an agent whose mistakes are reliably gated, with a human holding the prod key.
- **Tool scoping is a reliability lever, not just a security one.** Giving the agent `Bash` in a no-podman container let it launch a build command that hung until timeout (and `--output-format json` buffers, so a kill left zero output). Scope tools to exactly what the task needs (`Read,Edit,Write,Glob,Grep` for a code fix); denials are instant.
- **Observability via `--output-format stream-json --verbose`.** Streams one event per action (file reads, edits, reasoning), so progress is visible live and a timeout still leaves partial output. The `status` tool summarizes this on demand — pull, not push; Kagetora pings only on milestone transitions.
- **`./run.sh test` is NOT safe on the prod host.** ROTV's gate uses `--network=host --privileged -p 8080` and needs prod seed data — it's for an isolated dev box. The PR's GitHub Actions CI is the prod-safe gate.

## Roadmap (what's in place)

- [x] Unprivileged `devrunner` sandbox + rootless podman
- [x] Headless Claude Code on subscription token, in a container, validated
- [x] This server scaffolded (`work_ticket`/`status`/`promote`), GHA → quay (public)
- [x] Model-escalation model specced
- [ ] `work-ticket.sh` wrapper implementing the Sonnet→Opus ladder + event persistence
- [ ] `status` wired to live CI/build checks; `promote.sh` gated deploy
- [ ] Deploy (devrunner systemd unit) + wire into an MCP gateway backend
- [ ] **Dogfood:** iterate on `mcp-ashigaru` *with* `mcp-ashigaru`
- [ ] The pool (`ashigaru-1..5`), `webapp` previews, merge-train (see fleet spec)
