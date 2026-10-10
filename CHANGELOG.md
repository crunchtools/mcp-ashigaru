# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
