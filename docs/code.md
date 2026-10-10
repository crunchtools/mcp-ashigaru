# Code

Code turns a `ready-to-code` issue into a pull request that closes it, with auto-merge on.

## Why it matters

A coding agent that can also push is one prompt injection away from pushing something nobody asked for. Here the agent can only edit files on a runner that holds nothing worth stealing, and a separate job with no model in it does the pushing.

## How it works

`code.yml` has two jobs on two runners.

**Code agent** (read-only token, tools `Read,Edit,Write,Glob,Grep` by default):

1. The guard checks the event, then the issue and its maintainer comments are assembled into the prompt.
2. The agent edits the checkout and returns `status`, `summary` and `reason`.
3. The repository's pre-commit hooks run over the change, using the hook configuration as it was before the agent ran. If they fail, the output goes back to the same session with `--resume`, at most twice.
4. The change leaves the job as a patch file and a JSON result. Nothing else does.

**Open pull request** (app token, no model): `scripts/publish.py` applies the patch on a fresh checkout, commits, pushes `ashigaru/issue-N`, opens the pull request with `Closes #N` and runs `gh pr merge --auto --squash`.

The publish job refuses, labels the issue `needs-human` and says why when the agent reported no fix, when the patch is empty, or when it touches `.github/`. If a pull request is already open for the branch it does nothing.

Hook failures that survive two passes do not block the pull request: the repository's required checks are the arbiter, and [fix](fix.md) handles what they report.

## Example

```yaml
code:
  uses: crunchtools/ashigaru/.github/workflows/code.yml@v2.0.1
  with:
    model: "sonnet"
    max_turns: 80
  secrets:
    CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
    ASHIGARU_APP_ID: ${{ secrets.ASHIGARU_APP_ID }}
    ASHIGARU_APP_KEY: ${{ secrets.ASHIGARU_APP_KEY }}
    OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}   # for the Gatehouse hook; optional
```

`allowed_tools` can be widened per repository, for example to let the agent run tests. `Bash` runs repository code next to the Claude token, so widen it only where every issue author is trusted.

## Related

- [Triage](triage.md): how an issue becomes `ready-to-code`
- [Fix](fix.md): what happens after the pull request opens
- [Authority and limits](authority.md)
