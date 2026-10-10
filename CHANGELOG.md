# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Documentation

- Enrollment: say that permissions added after installation must be accepted by an organization owner, how to check, and that the sweep reaches only repositories the app is installed on.

## [2.0.0] - 2026-10-10

Ashigaru is rebuilt from scratch. Version 1 was an MCP server on a host that
worked a ticket when an operator told another agent to dispatch it; it opened
its last pull request in June. Version 2 is a set of reusable GitHub Actions
workflows that watch a repository's issues and fix the bug-class ones without
being asked. Nothing from version 1 is kept: no server, no container image, no
MCP tools.

### Added
- `triage.yml`: classifies a new issue. Bug, documentation and performance
  issues are labeled `ready-to-code`; features are labeled `triaged` and left
  for a maintainer; unclear ones get `needs-info`.
- `code.yml`: fixes a `ready-to-code` issue. The agent edits a checkout with a
  read-only token, the repository's pre-commit hooks run over the result on the
  same runner and failures go back to the same session (at most twice), then a
  second job on a fresh runner pushes `ashigaru/issue-N`, opens the pull
  request and turns on auto-merge.
- `fix.yml`: after each Gatehouse review of an Ashigaru pull request, answers
  every finding in its thread (`fixed in <sha>` or `not a bug: <reason>`) and
  fixes failed checks, for at most five rounds; then labels the pull request
  `needs-human`, turns auto-merge off and stops.
- `sweep.yml`: each night queues the five oldest untriaged issues across
  enrolled repos.
- `examples/ashigaru.yml`: the one file that enrolls a repository.
- Organization variable `ASHIGARU_ENABLED`: every workflow stops at its first
  step unless it is `true`.

### Removed
- The MCP server and all of its tools (`create_run`, `dispatch_worker`,
  `status`, `promote` and the rest), the worker container image, the preview
  deployer, the Matrix and webhook notifications and the Cockpit plugin.
- The `quay.io/crunchtools/mcp-ashigaru` and `mcp-ashigaru-agent-claude`
  images are no longer built.

### Changed
- The repository is renamed from `mcp-ashigaru` to `ashigaru` and moves from
  the MCP Server profile to Workflow Automation (constitution v1.23.0).

## [1.1.0] - 2026-10-10

### Added

- The four tools that only read (`status`, `list_runs`, `run_activity`,
  `run_log`) publish `readOnlyHint: true`. A gateway uses it to decide whether
  an invalid optional argument may be dropped or must refuse the call
  (crunchtools/constitution#35).
- Tests pin every registered tool into `READ_ONLY` or `WRITES`, and check that
  a read-only tool changes no file under the state directory and starts no
  subprocess. `run_build` is the control that trips both checks.

### Fixed

- `run_log` and `GET /api/runs/{run_id}/log` took the log name as a path:
  an absolute path or `../` read any file the server could. The name now has
  to resolve to a file directly inside the run directory.

### Changed

- Inherits constitution v1.22.0; the workflow pins and the pre-commit hook rev
  move with it.

- Constitution is now a v1.18.0 manifest: it holds only what is specific to
  this repo; fleet and profile rules apply by reference.
- Constitution validation is pinned to the inherited release via
  `.github/workflows/constitution.yml`.
- Dependabot auto-merges GitHub Actions minor and patch updates.

## [1.0.0] - 2026-09-20

First tagged release. This image has been running in production since
before it had version control; this release marks the current state as
the baseline going forward.
