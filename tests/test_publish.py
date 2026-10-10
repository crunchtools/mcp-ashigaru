"""What may become a pull request, and what may be pushed in a fix round."""

from common import touches_protected
from fix_publish import replies
from publish import commit_message, refusal

PATCH = "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n"
WORKFLOW_PATCH = PATCH.replace("src/app.py", ".github/workflows/ci.yml")
RENAME_OUT = "diff --git a/.github/workflows/ci.yml b/docs/ci.yml\nrename from x\nrename to y\n"


def test_a_fix_with_a_patch_is_published():
    assert refusal({"status": "fixed", "summary": "s"}, PATCH) is None


def test_no_fix_is_handed_to_a_human_with_the_reason():
    assert "already fixed" in refusal({"status": "no_change", "reason": "already fixed"}, "")
    assert refusal({}, PATCH) is not None


def test_a_claimed_fix_without_changes_is_refused():
    assert "changed no files" in refusal({"status": "fixed"}, "  \n")


ACTION_PATCH = PATCH.replace("src/app.py", ".github/actions/setup/action.yml")


def test_nothing_under_dot_github_is_ever_published():
    assert touches_protected(WORKFLOW_PATCH) and touches_protected(RENAME_OUT)
    assert touches_protected(ACTION_PATCH)
    assert not touches_protected(PATCH)
    assert ".github/" in refusal({"status": "fixed"}, WORKFLOW_PATCH)


def test_commit_message_closes_the_issue_and_carries_the_trailer():
    message = commit_message("Digest shows the wrong date", 12, "Off by one.")
    assert message.startswith("fix: Digest shows the wrong date (#12)\n")
    assert "Closes #12" in message and "Co-Authored-By:" in message


FINDINGS = [{"id": 1}, {"id": 2}, {"id": 3}]


def test_replies_use_the_two_forms_gatehouse_triage_accepts():
    dispositions = [
        {"id": 1, "disposition": "fixed", "reason": "x"},
        {"id": 2, "disposition": "not_a_bug", "reason": "the value is validated in parse()"},
    ]
    assert replies(FINDINGS, dispositions, "abc123") == {
        1: "fixed in abc123",
        2: "not a bug: the value is validated in parse()",
    }


def test_fixed_without_a_pushed_commit_is_not_answered():
    assert replies(FINDINGS, [{"id": 1, "disposition": "fixed", "reason": "x"}], None) == {}


def test_not_a_bug_without_a_reason_is_not_answered():
    assert replies(FINDINGS, [{"id": 2, "disposition": "not_a_bug", "reason": " "}], "abc") == {}


def test_dispositions_for_unknown_findings_are_ignored():
    assert replies(FINDINGS, [{"id": 99, "disposition": "not_a_bug", "reason": "r"}], "abc") == {}
