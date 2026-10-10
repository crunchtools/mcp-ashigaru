#!/usr/bin/env python3
"""Build an agent prompt from a template plus the issue or the round's findings.

Issue comments from accounts without write access are dropped here, before the
agent runs: the guard decides whether the issue may be read at all, and this
keeps a stranger's later comment from riding along.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import api, api_pages, can_write

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
BODY_LIMIT = 20_000
COMMENT_LIMIT = 4_000
COMMENTS_KEPT = 20
LOG_LIMIT = 20_000
SLOT = re.compile(r"\{\{(\w+)\}\}")


def trusted_comments(repo: str, comments: list[dict]) -> str:
    """The latest comments by people with write access. Bots and everyone else are dropped."""
    kept = [
        f"[{comment['user']['login']}] {comment['body'][:COMMENT_LIMIT]}"
        for comment in comments
        if comment["user"].get("type") == "User" and can_write(repo, comment["user"]["login"])
    ]
    return "\n\n".join(kept[-COMMENTS_KEPT:]) or "(none)"


def render(template: str, values: dict[str, str]) -> str:
    """Fill `{{name}}` slots in one pass, so inserted text is never scanned for slots."""
    return SLOT.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def issue_values(repo: str, number: int) -> dict[str, str]:
    issue = api(f"repos/{repo}/issues/{number}")
    comments = api_pages(f"repos/{repo}/issues/{number}/comments?per_page=100")
    return {
        "repo": repo,
        "number": str(number),
        "title": issue["title"],
        "body": (issue.get("body") or "(empty)")[:BODY_LIMIT],
        "comments": trusted_comments(repo, comments),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("triage", "code", "fix", "hooks"))
    parser.add_argument("--repo", default="")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--findings", type=Path)
    parser.add_argument("--hooks-log", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    values = {"repo": args.repo}
    if args.issue:
        values |= issue_values(args.repo, args.issue)
    if args.findings:
        values["findings"] = args.findings.read_text(encoding="utf-8")
    if args.hooks_log:
        values["hooks_log"] = args.hooks_log.read_text(encoding="utf-8", errors="replace")[-LOG_LIMIT:]
    template = (PROMPTS / f"{args.kind}.md").read_text(encoding="utf-8")
    args.out.write_text(render(template, values), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
