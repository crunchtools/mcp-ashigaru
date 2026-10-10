#!/usr/bin/env python3
"""Decide whether an agent may read this issue.

Content written by an account without write access must not reach an agent
unless a maintainer acted on the item. The decision is plain code over the
event payload and the effective repository permission of two accounts: the
issue's author and whoever triggered the event. No LLM is involved.
"""

from __future__ import annotations

import functools
import json
import os
import sys
from collections.abc import Callable

from common import can_write, set_output


def decide(event: dict, writes: Callable[[str], bool]) -> tuple[bool, str]:
    """`writes(login)` says whether an account has write access to the repository."""
    issue = event.get("issue", {})
    action = event.get("action")
    author = issue.get("user", {}).get("login", "")
    sender = event.get("sender", {})
    author_writes = bool(author) and writes(author)
    sender_writes = sender.get("type") == "User" and writes(sender.get("login", ""))

    if issue.get("state") != "open" or "pull_request" in issue:
        return False, "not an open issue"
    if action == "opened" and author_writes:
        return True, "opened by an account with write access"
    if action == "labeled" and sender_writes:
        return True, "labeled by an account with write access"
    if action == "labeled" and author_writes:
        return True, "labeled by automation or a triager; the author has write access"
    if action in ("opened", "labeled"):
        return False, "neither the author nor the labeler has write access"
    return False, f"action {action} is not handled"


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    allowed, reason = False, "not an issue event"
    if os.environ.get("GITHUB_EVENT_NAME") == "issues":
        allowed, reason = decide(event, functools.partial(can_write, os.environ["GITHUB_REPOSITORY"]))
    print(f"guard: {'allowed' if allowed else 'refused'} ({reason})")
    set_output("allowed", "true" if allowed else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
