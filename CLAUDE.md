# Dev Commands

Everything runs from containers; the host needs only git, podman, pre-commit and gh.

- **Lint and test:** `podman run --rm -v .:/src:Z -w /src docker.io/library/python:3.12-slim bash -c 'pip -q install pytest==9.1.1 pyyaml==6.0.3 ruff==0.16.9 && ruff check . && ruff format --check . && pytest -q'`
- **Workflows:** `podman run --rm -v .:/repo:Z -w /repo docker.io/rhysd/actionlint:1.7.12`

# Layout

- `.github/workflows/{triage,code,fix}.yml`: the reusable workflows, the product.
- `.github/workflows/sweep.yml`: scheduled here only.
- `.github/workflows/ashigaru.yml`: this repo's own caller; must equal `examples/ashigaru.yml`.
- `scripts/`: every decision and every write. Standard library and `gh` only. Pure functions are tested in `tests/`; `main()` does the I/O.
- `prompts/`, `schemas/`: what each agent is told and what it must return.

# Rules that are easy to break

- A job that runs `claude-code-action/base-action` gets a read-only token, `persist-credentials: false`, and never the app token. `tests/test_workflows.py` enforces it.
- Logic goes in `scripts/`, not in workflow shell.
- Event values reach scripts through `env:`, never interpolated into `run:`.
- Releasing: see `docs/enrollment.md`. `VERSION`, the literal `ref:` in each reusable workflow, the caller pins and the CHANGELOG move together.
- Label names, input names, the `ashigaru/` branch prefix and job names are public interface: a change is a MAJOR release.
