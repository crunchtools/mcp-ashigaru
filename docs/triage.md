# Triage

Triage reads a new issue against the code and gives it one state label. It is the gate that decides whether an issue is fixed automatically or waits for a person.

## Why it matters

Issues filed in passing pile up unlabeled, and nobody goes back to sort them. Sorting is cheap for a model that can read the code, and a wrong sort is cheap to correct as long as the default is cautious: when in doubt, the issue goes to a human.

## How it works

`triage.yml` has two jobs. `Classify` runs the agent with a read-only token and the tools `Read`, `Glob` and `Grep`; it returns a verdict as structured output. `Apply verdict` runs no model: `scripts/triage_apply.py` turns the verdict into a label and a comment.

| Verdict | Label | What happens next |
|---------|-------|-------------------|
| `bug`, `documentation` or `performance`, confidence at or above `min_confidence` | `ready-to-code` | The caller chains [code](code.md) in the same run |
| `feature`, or any category below the threshold, or a malformed verdict | `triaged` | Waits. Add `ready-to-code` to have it fixed |
| `question`, `unclear`, or `needs_info` | `needs-info` | Waits. Answer, then add `ready-for-triage` |

The matching type label (`bug`, `documentation`, `enhancement`) is added when the repository has it.

Triage runs when someone with write access opens an issue, and when `ready-for-triage` is added. An issue opened by an account without write access is not read until someone with write access labels it; see [authority](authority.md).

## Example

```yaml
triage:
  uses: crunchtools/ashigaru/.github/workflows/triage.yml@v2.0.0
  with:
    auto_code_categories: "bug,documentation"   # keep performance work for a person
    min_confidence: "0.8"
  secrets:
    CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

## Related

- [Code](code.md): what `ready-to-code` starts
- [Sweep](sweep.md): how old issues reach triage
- [Enrollment](enrollment.md): all inputs
