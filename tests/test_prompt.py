"""Prompt assembly: no slot injection, no comments from strangers."""

import prompt
from prompt import render, trusted_comments


def test_inserted_text_is_not_scanned_for_slots():
    out = render("T: {{title}}\nF: {{findings}}", {"title": "{{findings}}", "findings": "secret"})
    assert out == "T: {{findings}}\nF: secret"


def test_unknown_slots_are_left_alone():
    assert render("{{nope}}", {}) == "{{nope}}"


def test_only_comments_from_accounts_with_write_access_reach_the_agent(monkeypatch):
    monkeypatch.setattr(prompt, "can_write", lambda repo, login: login == "maintainer")
    comments = [
        {"user": {"login": "maintainer", "type": "User"}, "body": "keep"},
        {"user": {"login": "stranger", "type": "User"}, "body": "drop"},
        {"user": {"login": "some[bot]", "type": "Bot"}, "body": "bot"},
    ]
    assert trusted_comments("o/r", comments) == "[maintainer] keep"
    assert trusted_comments("o/r", []) == "(none)"


def test_a_long_thread_is_cut_to_the_latest_comments(monkeypatch):
    monkeypatch.setattr(prompt, "can_write", lambda repo, login: True)
    comments = [{"user": {"login": "m", "type": "User"}, "body": f"c{n}"} for n in range(50)]
    kept = trusted_comments("o/r", comments).split("\n\n")
    assert len(kept) == prompt.COMMENTS_KEPT and kept[-1] == "[m] c49"
