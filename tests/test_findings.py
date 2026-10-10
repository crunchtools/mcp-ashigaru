"""What a fix round must answer, and when the rounds are spent (profile IV)."""

from findings import ROUND_MARKER, failed, model_for, opened_by, pending, plan, rounds_used, unanswered

REVIEWER = "github-actions[bot]"


def inline(comment_id, login=REVIEWER, reply_to=None):
    return {"id": comment_id, "user": {"login": login}, "in_reply_to_id": reply_to,
            "path": "a.py", "line": 3, "body": "**HIGH** finding"}  # fmt: skip


def check(name, status="completed", conclusion="success", run_id="100"):
    return {"name": name, "status": status, "conclusion": conclusion,
            "details_url": f"https://github.com/o/r/actions/runs/{run_id}/job/7"}  # fmt: skip


def test_findings_without_a_reply_from_someone_else_are_unanswered():
    comments = [
        inline(1),
        inline(2),
        inline(3, login="ashigaru-crunchtools[bot]", reply_to=2),
        inline(4, login=REVIEWER, reply_to=1),  # the reviewer replying to itself is not an answer
    ]
    assert [finding["id"] for finding in unanswered(comments, REVIEWER)] == [1]


def test_rounds_are_counted_from_marker_comments():
    rounds = [{"body": f"{ROUND_MARKER}\nround"}, {"body": ROUND_MARKER}]
    assert rounds_used([*rounds, {"body": "chatter"}]) == len(rounds)


def test_round_cap_hands_off_instead_of_fixing():
    assert plan([{"id": 1}], [], used=4, max_rounds=5) == "fix"
    assert plan([{"id": 1}], [], used=5, max_rounds=5) == "cap"
    assert plan([], [check("CI", conclusion="failure")], used=5, max_rounds=5) == "cap"


def test_nothing_to_answer_means_no_round():
    assert plan([], [], used=0, max_rounds=5) == "none"


def test_failed_checks_exclude_triage_and_this_run():
    runs = [
        check("CI / test", conclusion="failure"),
        check("Gatehouse triage / Gatehouse triage", conclusion="failure"),
        check("Fix / Fix agent", conclusion="failure", run_id="555"),
        check("Lint", conclusion="success"),
    ]
    assert [run["name"] for run in failed(runs, own_run_id="555")] == ["CI / test"]


def test_pending_ignores_this_run():
    runs = [check("CI", status="in_progress", conclusion=None),
            check("Fix / Fix agent", status="in_progress", conclusion=None, run_id="555")]  # fmt: skip
    assert pending(runs, own_run_id="555") == ["CI"]


def test_model_escalates_at_the_configured_round():
    assert [model_for(n, "sonnet", "opus", 3) for n in (1, 2, 3, 5)] == ["sonnet", "sonnet", "opus", "opus"]


def test_only_pull_requests_the_app_opened_are_touched():
    assert opened_by({"author": {"login": "app/ashigaru-crunchtools"}}, "ashigaru-crunchtools")
    assert not opened_by({"author": {"login": "somebody"}}, "ashigaru-crunchtools")
