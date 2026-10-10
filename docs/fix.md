# Fix

Fix answers the review on an Ashigaru pull request: every finding gets a reply in its thread, and failed checks get fixed.

## Why it matters

A required check that says "every review finding has an answer" stops a pull request until someone answers. For a pull request nobody is watching, that someone has to be the automation, and it has to stop when it is not converging.

## How it works

The caller runs `fix.yml` when the `Gatehouse` workflow completes. It acts only when all of these hold: the completed run was a `pull_request_target` run, the branch starts with `ashigaru/`, the branch is in this repository, and the open pull request for it was opened by the Ashigaru app. A reply to a finding re-runs Gatehouse under a different event, which is ignored, so a round cannot start the next one by replying.

**Fix agent** (read-only token):

1. `scripts/findings.py` waits up to 15 minutes for checks that are still running, then collects inline findings by the reviewer that nobody else has replied to, and failed checks with the tail of their logs. `Gatehouse triage` is not counted as a failure: it is red because findings are unanswered.
2. Nothing to answer ends the run. If the round limit is spent, the run goes straight to the hand-off.
3. The agent edits the branch and returns one disposition per finding: `fixed`, or `not_a_bug` with a reason.

**Push and answer findings** (app token, no model): `scripts/fix_publish.py` pushes the patch and replies `fixed in <sha>` or `not a bug: <reason>`. A finding the agent did not account for gets no reply, so the required check stays red.

Each round leaves a marker comment on the pull request; that is how rounds are counted. Rounds 1 and 2 use `model`, later rounds `escalation_model`. After `max_rounds` (default 5), or when a round changes no code while work remains, the pull request gets `needs-human`, auto-merge is turned off, and Ashigaru stops acting on it.

A `not a bug` reply is the agent judging a finding on its own pull request. Nothing independent checks the reason. The round limit and the repository's deterministic checks are the backstops.

## Example

```yaml
fix:
  uses: crunchtools/ashigaru/.github/workflows/fix.yml@v2.0.1
  with:
    max_rounds: 3
    escalate_at: 2
  secrets:
    CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
    ASHIGARU_APP_ID: ${{ secrets.ASHIGARU_APP_ID }}
    ASHIGARU_APP_KEY: ${{ secrets.ASHIGARU_APP_KEY }}
```

## Related

- [Code](code.md): where the pull request comes from
- [Authority and limits](authority.md)
