"""The guard decides who may put an issue in front of an agent (profile III)."""

from guard import decide

WRITERS = {"maintainer"}


def event(action, author, sender, sender_type="User", state="open", **issue):
    return {
        "action": action,
        "sender": {"login": sender, "type": sender_type},
        "issue": {"state": state, "user": {"login": author}, **issue},
    }


def allowed(*args, **kwargs):
    return decide(event(*args, **kwargs), WRITERS.__contains__)[0]


def test_issue_opened_by_an_account_with_write_access_is_allowed():
    assert allowed("opened", "maintainer", "maintainer")


def test_issue_opened_by_an_account_without_write_access_is_refused():
    assert not allowed("opened", "stranger", "stranger")


def test_a_maintainers_label_admits_a_strangers_issue():
    assert allowed("labeled", "stranger", "maintainer")


def test_a_strangers_label_does_not_admit_a_strangers_issue():
    assert not allowed("labeled", "stranger", "stranger")


def test_a_bot_is_never_the_maintainer_whose_label_admits_an_issue():
    assert not allowed("labeled", "stranger", "maintainer", sender_type="Bot")


def test_a_label_from_automation_admits_a_maintainers_issue():
    assert allowed("labeled", "maintainer", "sweeper[bot]", sender_type="Bot")


def test_closed_issues_and_pull_requests_are_refused():
    assert not allowed("opened", "maintainer", "maintainer", state="closed")
    assert not allowed("opened", "maintainer", "maintainer", pull_request={})


def test_other_actions_are_refused():
    assert not allowed("edited", "maintainer", "maintainer")
