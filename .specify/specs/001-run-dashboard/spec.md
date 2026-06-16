# Spec 001: Run Dashboard

- **Status**: Implemented
- **Version**: 0.6.0
- **Author**: Scott McCarty
- **Date**: 2026-06-15

## Overview

Add a REST API, Cockpit plugin, and MCP tools to browse, inspect, and search Ashigaru dev runs. Run data is already persisted to disk (`$STATE_DIR/runs/{run_id}/`); this feature makes it discoverable without SSH.

## Architecture

The REST API (served on port 8020 via FastMCP `custom_route`) is the shared data backbone. The Cockpit plugin calls it via `cockpit.http()`. MCP tools share the same data-reading functions.

## User Stories

1. **As a human operator**, I want to see all Ashigaru runs in a Cockpit dashboard so I can monitor activity, inspect event streams, and review run history from a browser.

2. **As an AI agent (Kagetora)**, I want to list all runs and filter by repo/status so I can find prior work without knowing run_ids by heart, and interrogate event streams for debugging.

3. **As an auditor**, I want run data to persist indefinitely so I can trace what was done, when, by which model tier, and what the outcome was.

## Components

### Data Layer (`src/mcp_ashigaru/runs.py`)

Shared functions used by both REST and MCP:
- `list_all_runs(repo?, status?, limit?)` — scan runs, filter, sort newest-first
- `get_run_detail(run_id)` — full metadata + event count + log sizes + brief
- `get_run_events(run_id, tail?)` — parsed tool_use actions from events.jsonl
- `get_run_log(run_id)` — combined log from agent.err, setup.log, runner.log, preview.log

### REST API (port 8020, same as MCP)

| Endpoint | Returns |
|----------|---------|
| `GET /api/runs` | JSON array of run summaries |
| `GET /api/runs/{run_id}` | JSON run detail |
| `GET /api/runs/{run_id}/events` | JSON event stream |
| `GET /api/runs/{run_id}/log` | Plain text combined log |

### MCP Tools (2 new, 8 total)

- `list_runs(repo?, status?, limit?)` — discover runs
- `run_events(run_id, tail?)` — interrogate event stream

### Cockpit Plugin (`cockpit-ashigaru/`)

- `manifest.json`, `index.html`, `ashigaru.js`, `ashigaru.css`
- Vanilla JS, PatternFly 6 CSS classes (no React)
- Run list view with filter bar + auto-refresh
- Run detail view with metadata, attempts, events, and combined log

## Deployment

- REST API + MCP tools: baked into container image via GHA build
- Cockpit plugin: SCP to `/usr/share/cockpit/ashigaru/` on lotor
