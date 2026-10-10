# Enrollment

Setting up an organization, enrolling a repository, and releasing Ashigaru itself.

## Why it matters

The supported wiring is one caller file, copied unchanged. Everything a repository might tune is an input, so the file stays identical across a fleet and Dependabot can keep its pin current.

## How it works

**Organization**

1. Create a GitHub App. Repository permissions: Contents, Issues, Pull requests read and write; Metadata read. No Workflows permission, no organization permissions. Install it on the repositories to enroll.
   - Permissions added after installation are not live until accepted. If the app is created or installed before its repository permissions are set, the installation keeps its old permissions until an organization owner accepts the request on the installation's settings page. Until then `actions/create-github-app-token` fails when a workflow asks for `issues: write` or `pull-requests: write`. Check with `gh api orgs/<org>/installations --jq '.installations[] | select(.app_slug=="<slug>") | .permissions'`; `contents`, `issues` and `pull_requests` must show `write`.
   - The sweep needs the app installed on every repository it should reach. `sweep.yml` mints an owner-wide token and can only label issues in repositories the installation covers.
2. Secrets: `ASHIGARU_APP_ID`, `ASHIGARU_APP_KEY` (the app's private key), `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), and optionally `OPENROUTER_API_KEY` for the Gatehouse pre-commit hook.
3. Variable: `ASHIGARU_ENABLED` = `true`.
4. A branch ruleset with your required checks, without a bypass for the app. Allow auto-merge on each repository.

On a GitHub Free organization, organization secrets reach public repositories only.

**Repository.** Copy [`examples/ashigaru.yml`](../examples/ashigaru.yml) to `.github/workflows/ashigaru.yml`. The state labels are created the first time they are needed.

**Inputs**

| Workflow | Input | Default |
|----------|-------|---------|
| `triage.yml` | `auto_code_categories` | `bug,documentation,performance` |
| | `min_confidence` | `0.7` |
| | `model`, `max_turns` | `sonnet`, 40 |
| `code.yml` | `allowed_tools` | `Read,Edit,Write,Glob,Grep` |
| | `model`, `max_turns` | `sonnet`, 80 |
| `fix.yml` | `max_rounds` | 5 |
| | `model`, `escalation_model`, `escalate_at` | `sonnet`, `opus`, 3 |
| | `reviewer`, `bot` | `github-actions[bot]`, `ashigaru-crunchtools` |
| | `allowed_tools`, `max_turns` | as `code.yml` |

**Turning it off.** Set `ASHIGARU_ENABLED` to `false` to stop every repository at once. Delete the caller file to remove one repository.

## Releasing

A called workflow cannot name its own ref, so each reusable workflow fetches the scripts at a literal tag. To release `X.Y.Z`: set `VERSION`, replace the previous tag in `.github/workflows/{triage,code,fix,ashigaru}.yml` and `examples/ashigaru.yml`, add the CHANGELOG entry, merge, then tag `vX.Y.Z` and publish the GitHub Release. `tests/test_workflows.py` fails if any of those disagree. Between the merge and the tag, this repository's own caller points at a tag that does not exist yet; tag promptly.

## Related

- [Authority and limits](authority.md)
- [Triage](triage.md), [Code](code.md), [Fix](fix.md), [Sweep](sweep.md)
