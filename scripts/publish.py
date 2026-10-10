#!/usr/bin/env python3
"""Publish the code agent's patch: branch, commit, push, pull request, auto-merge.

Runs in a fresh checkout that holds the app token. The agent never ran here and
never saw that token; all it handed over is a patch file and a JSON result.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import BRANCH_PREFIX, NEEDS_HUMAN, api, gh, git, set_output, set_state, touches_protected

BOT_NAME = "ashigaru-crunchtools[bot]"
BOT_EMAIL = "ashigaru-crunchtools[bot]@users.noreply.github.com"
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
FOOTER = "Opened by Ashigaru. Merges automatically once the required checks pass."


def refusal(result: dict, patch: str) -> str | None:
    """Why this run must not become a pull request, or None when it may."""
    if result.get("status") != "fixed":
        reason = str(result.get("reason", "")).strip() or "the agent did not report a fix"
        return f"No fix was produced: {reason}"
    if not patch.strip():
        return "The agent reported a fix but changed no files."
    if touches_protected(patch):
        return "The change touches `.github/`, which Ashigaru does not modify."
    return None


def commit_message(title: str, issue: int, summary: str) -> str:
    return f"fix: {title} (#{issue})\n\n{summary}\n\nCloses #{issue}\n\n{TRAILER}\n"


def hand_off(repo: str, issue: int, why: str) -> None:
    set_state(repo, issue, NEEDS_HUMAN)
    gh("issue", "comment", str(issue), "--repo", repo, "--body", f"**Ashigaru stopped.** {why}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()

    patch = args.patch.read_text(encoding="utf-8") if args.patch.exists() else ""
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = {}
    branch = f"{BRANCH_PREFIX}issue-{args.issue}"

    open_prs = json.loads(
        gh("pr", "list", "--repo", args.repo, "--head", branch, "--state", "open", "--json", "number")
    )
    if open_prs:
        print(f"publish: PR #{open_prs[0]['number']} is already open for {branch}; nothing to do")
        return 0

    why = refusal(result, patch)
    if why:
        hand_off(args.repo, args.issue, why)
        set_output("pr", "")
        return 0

    title = api(f"repos/{args.repo}/issues/{args.issue}")["title"].strip()
    summary = str(result.get("summary", "")).strip() or "Automated fix."
    git("config", "user.name", BOT_NAME)
    git("config", "user.email", BOT_EMAIL)
    git("checkout", "-B", branch)
    try:
        git("apply", "--index", "--3way", str(args.patch))
    except subprocess.CalledProcessError:
        hand_off(args.repo, args.issue, "The fix no longer applies to the default branch.")
        set_output("pr", "")
        return 0
    git("commit", "-m", commit_message(title, args.issue, summary))
    git("push", "--force", "origin", branch)

    body = f"{summary}\n\nCloses #{args.issue}\n\n{FOOTER}\n"
    url = gh(
        "pr", "create", "--repo", args.repo, "--head", branch, "--base", args.base,
        "--title", f"fix: {title} (#{args.issue})", "--body", body,
    ).strip()  # fmt: skip
    try:
        gh("pr", "merge", url, "--auto", "--squash")
    except subprocess.CalledProcessError as error:
        gh("pr", "comment", url, "--body",
           f"**Ashigaru could not enable auto-merge**, so this needs a manual merge.\n\n"
           f"```\n{error.stderr.strip()}\n```")  # fmt: skip
    print(f"publish: opened {url}")
    set_output("pr", url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
