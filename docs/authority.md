# Authority and limits

Who holds which credential, what input reaches an agent, and what stops a run.

## Why it matters

A model that reads issue text can be talked into things. The design assumes that will happen and arranges for the component that can be persuaded to hold nothing worth taking.

## How it works

**Credentials**

| Job | Runs a model | GitHub credential | Other secrets |
|-----|--------------|-------------------|---------------|
| Classify, Code agent, Fix agent | yes | job token, read-only | the Claude token; in Code agent, the reviewer key in the hook step only |
| Apply verdict | no | job token, `issues: write` | none |
| Open pull request, Push and answer findings | no | Ashigaru app token | none |
| Sweep | no | Ashigaru app token, `issues: write` and `contents: read` | none |

Agent jobs check out with `persist-credentials: false`. What leaves an agent job is structured output validated against a schema and a patch file; the writing job applies the patch on a fresh runner. The app has no Workflows permission, and the publish scripts refuse a patch that touches `.github/`.

**Merging.** No job merges. The publish job turns on auto-merge and GitHub merges when the repository's required checks pass. The app is not an organization admin and cannot bypass a ruleset.

**Untrusted input.** `scripts/guard.py` asks the API for the effective repository permission of the issue's author and of whoever triggered the event. An issue is read when its author has write access, or when someone with write access labeled it. Comments are filtered the same way when the prompt is built: only those from accounts with write access are included. Review findings and check logs on an Ashigaru pull request are treated as data in the prompt.

**Limits**

| Limit | Default | Where |
|-------|---------|-------|
| Switch: every workflow stops at its first job unless it is `true` | n/a | organization variable `ASHIGARU_ENABLED` |
| Turns per agent step | 40 triage, 80 code and fix | input `max_turns` |
| Job timeout | 20, 60 and 75 minutes | workflow files |
| Hook passes fed back to the code agent | 2 | `code.yml` |
| Fix rounds per pull request | 5 | input `max_rounds` |
| Issues queued per sweep | 5, never more than 20 | `sweep.yml` input `limit`, `MAX_LIMIT` in `scripts/sweep.py` |
| Runs at a time per issue or pull request branch | 1 | caller `concurrency` |

Each is covered in `tests/test_workflows.py`, `tests/test_findings.py` or `tests/test_sweep.py`.

## Known gaps

- The agent job's runner is the agent's: it can write any file there, and writing files is enough to run code (a git hook, a repository script a pre-commit hook calls). Two secrets are present on that runner. The Claude token is in the agent's process environment and cannot be elsewhere. The reviewer key is on disk only while the Gatehouse hook runs, after the other hooks, and is never in the environment of a hook that runs repository code; that narrows the exposure and does not remove it. Both are bounded by who can reach the agent: only issues from accounts with write access.
- A pull request from `ashigaru/issue-N` is a same-repository pull request, so the repository's own CI runs the changed code with whatever secrets that CI has. That is true of any contributor with write access; here the contributor is a model.
- With file tools only the agent has no direct network access. A repository that adds `Bash` to `allowed_tools` gives it one.
- Concurrency groups are per repository. Nothing limits how many repositories run an agent at once except the sweep limit and how often people file issues.

## Related

- [Enrollment](enrollment.md)
- [Fix](fix.md)
