#!/usr/bin/env python3
"""Publish a fix round: push the patch and answer each finding in its thread.

A reply is `fixed in <sha>` or `not a bug: <reason>`, the two forms Gatehouse
triage accepts. A finding the agent did not account for gets no reply, so the
required check stays red rather than being cleared by default.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import NEEDS_HUMAN, gh, git, set_state, touches_protected
from findings import MAX_ROUNDS, ROUND_MARKER
from publish import BOT_EMAIL, BOT_NAME, TRAILER


def replies(findings: list[dict], dispositions: list[dict], sha: str | None) -> dict[int, str]:
    """The reply for each finding that was accounted for, keyed by comment id."""
    known = {finding["id"] for finding in findings}
    out: dict[int, str] = {}
    for item in dispositions:
        finding_id = item.get("id")
        if finding_id not in known:
            continue
        reason = str(item.get("reason", "")).strip()
        if item.get("disposition") == "fixed" and sha:
            out[finding_id] = f"fixed in {sha}"
        elif item.get("disposition") == "not_a_bug" and reason:
            out[finding_id] = f"not a bug: {reason}"
    return out


def hand_off(repo: str, number: int, why: str) -> None:
    set_state(repo, number, NEEDS_HUMAN)
    gh("pr", "merge", str(number), "--repo", repo, "--disable-auto")
    gh("pr", "comment", str(number), "--repo", repo, "--body", f"**Ashigaru stopped.** {why}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mode", required=True, choices=("fix", "cap"))
    parser.add_argument("--findings", required=True, type=Path)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--branch")
    parser.add_argument("--max-rounds", default=MAX_ROUNDS, type=int)
    args = parser.parse_args()

    collected = json.loads(args.findings.read_text(encoding="utf-8"))
    number, round_number = collected["pr"], collected["round"]
    if args.mode == "cap":
        hand_off(args.repo, number,
                 f"{args.max_rounds} fix rounds are spent and the PR is still not green.")  # fmt: skip
        return 0

    patch = args.patch.read_text(encoding="utf-8") if args.patch and args.patch.exists() else ""
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        result = {}
    if touches_protected(patch):
        hand_off(args.repo, number, "The fix touches `.github/`, which Ashigaru does not modify.")
        return 0

    sha = None
    if patch.strip():
        git("config", "user.name", BOT_NAME)
        git("config", "user.email", BOT_EMAIL)
        try:
            git("apply", "--index", "--3way", str(args.patch))
        except subprocess.CalledProcessError:
            hand_off(args.repo, number, "The fix no longer applies to the pull request branch.")
            return 0
        git("commit", "-m", f"fix: address review findings (round {round_number})\n\n{TRAILER}\n")
        git("push", "origin", f"HEAD:{args.branch}")
        sha = git("rev-parse", "HEAD").strip()

    answered = replies(collected["findings"], result.get("dispositions") or [], sha)
    for finding_id, body in answered.items():
        gh("api", f"repos/{args.repo}/pulls/{number}/comments/{finding_id}/replies", "-f", f"body={body}")

    left = len(collected["findings"]) - len(answered)
    summary = str(result.get("summary", "")).strip() or "No summary returned."
    gh(
        "pr", "comment", str(number), "--repo", args.repo, "--body",
        f"{ROUND_MARKER}\n**Ashigaru fix round {round_number}:** {summary}\n\n"
        f"{len(answered)} finding(s) answered, {left} left, "
        f"{'pushed ' + sha if sha else 'no code change'}.",
    )  # fmt: skip
    if not sha and (left or collected["failed_checks"]):
        hand_off(
            args.repo, number, "The round changed no code and work remains, so another round would not help."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
