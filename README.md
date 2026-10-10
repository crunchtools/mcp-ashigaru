# ashigaru

Issues in, fixed bugs out. Ashigaru is a set of reusable GitHub Actions workflows that watch a repository's issues, sort bug-class work from feature work, and fix the bugs: it opens the pull request, answers the code review, and lets GitHub merge when the required checks pass. Feature requests are labeled and left for a maintainer. It runs on GitHub-hosted runners with Claude Code, on a Claude subscription token; there is no server to operate.

Named for the *ashigaru* (足軽), the foot soldiers a daimyo sent into the field.

## Capabilities

1. **[Triage](docs/triage.md)**: every new issue from an account with write access is read against the code and labeled. Bug, documentation and performance issues go to the code agent; features wait for a person.
2. **[Code](docs/code.md)**: a `ready-to-code` issue becomes a pull request from `ashigaru/issue-N` with auto-merge on. The repository's own pre-commit hooks run before the pull request opens, and failures go back to the same agent session.
3. **[Fix](docs/fix.md)**: after each review of an Ashigaru pull request, every finding gets an answer in its thread and failed checks get fixed, for at most five rounds.
4. **[Sweep](docs/sweep.md)**: each night the oldest untriaged issues across enrolled repositories are queued, a few at a time, so a backlog drains without spending the day's model budget.
5. **[Authority and limits](docs/authority.md)**: the job that runs the model holds no credential that can write; the job that writes runs no model. One organization variable stops everything.

## Quick Start

Organization, once:

- Create a GitHub App with repository permissions Contents, Issues and Pull requests set to read and write, install it on the organization, and store its id and private key as the secrets `ASHIGARU_APP_ID` and `ASHIGARU_APP_KEY`.
- Run `claude setup-token` and store the result as the secret `CLAUDE_CODE_OAUTH_TOKEN`.
- Set the variable `ASHIGARU_ENABLED` to `true`.
- Require your CI and review checks in a branch ruleset the app cannot bypass, and allow auto-merge.

Each repository:

```bash
mkdir -p .github/workflows
curl -fsSL https://raw.githubusercontent.com/crunchtools/ashigaru/v2.0.0/examples/ashigaru.yml \
  -o .github/workflows/ashigaru.yml
```

Details and the full list of inputs are in [docs/enrollment.md](docs/enrollment.md).

## Documentation

| Page | What it covers |
|------|----------------|
| [docs/triage.md](docs/triage.md) | Categories, the confidence threshold, which labels mean what |
| [docs/code.md](docs/code.md) | The two-job split, the pre-commit inner loop, what is refused |
| [docs/fix.md](docs/fix.md) | What triggers a round, how findings are answered, the round limit |
| [docs/sweep.md](docs/sweep.md) | Backlog selection and the nightly limit |
| [docs/authority.md](docs/authority.md) | Who holds which credential, untrusted input, the switch and the caps |
| [docs/enrollment.md](docs/enrollment.md) | Organization setup, enrolling a repository, inputs, releasing |

## Development

A developer machine needs `git`, `podman`, `pre-commit` and `gh`.

```bash
pre-commit install
podman run --rm -v .:/src:Z -w /src docker.io/library/python:3.12-slim \
  bash -c 'pip -q install pytest==9.1.1 pyyaml==6.0.3 ruff==0.16.9 && ruff check . && pytest -q'
podman run --rm -v .:/repo:Z -w /repo docker.io/rhysd/actionlint:1.7.12
```

## Credits

The design follows [Fullsend](https://github.com/fullsend-ai/fullsend): labels as the state, a triage gate that routes bugs to a code agent and parks features, and deterministic scripts that hold push and merge authority. The label names are theirs, so a repository can move between the two.

## License

AGPL-3.0-or-later
