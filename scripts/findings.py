#!/usr/bin/env python3
"""Collect what a fix round has to answer: unanswered review findings and failed checks.

Read-only. Decides between three actions: `none` (nothing to answer), `fix`
(run the agent), `cap` (the round limit is spent, hand the PR to a human).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from common import api_pages, gh, set_output

ROUND_MARKER = "<!-- ashigaru:fix-round -->"
IGNORED_CHECKS = ("Gatehouse triage",)
FAILED = ("failure", "timed_out")
JOB_URL = re.compile(r"/actions/runs/(\d+)/job/(\d+)")
LOG_TAIL_LINES = 150
POLL_SECONDS = 30
WAIT_SECONDS = 900
MAX_ROUNDS = 5
ESCALATE_AT = 3


def unanswered(comments: list[dict], reviewer: str) -> list[dict]:
    """Inline findings by the reviewer that nobody else has replied to."""
    answered = {
        comment["in_reply_to_id"]
        for comment in comments
        if comment.get("in_reply_to_id") and comment["user"]["login"] != reviewer
    }
    return [
        {
            "id": comment["id"],
            "path": comment.get("path"),
            "line": comment.get("line") or comment.get("original_line"),
            "body": comment["body"],
        }
        for comment in comments
        if not comment.get("in_reply_to_id")
        and comment["user"]["login"] == reviewer
        and comment["id"] not in answered
    ]


def rounds_used(issue_comments: list[dict]) -> int:
    return sum(ROUND_MARKER in comment.get("body", "") for comment in issue_comments)


def _own(run: dict, own_run_id: str) -> bool:
    match = JOB_URL.search(run.get("details_url") or "")
    return bool(match) and match.group(1) == own_run_id


def pending(check_runs: list[dict], own_run_id: str) -> list[str]:
    return [run["name"] for run in check_runs if run["status"] != "completed" and not _own(run, own_run_id)]


def failed(check_runs: list[dict], own_run_id: str) -> list[dict]:
    return [
        run
        for run in check_runs
        if run.get("conclusion") in FAILED
        and not _own(run, own_run_id)
        and not any(name in run["name"] for name in IGNORED_CHECKS)
    ]


def opened_by(pr: dict, bot: str) -> bool:
    """True when the app opened this pull request. `gh` reports an app as `app/<slug>`."""
    return pr.get("author", {}).get("login") in (f"app/{bot}", f"{bot}[bot]")


def plan(findings: list[dict], failures: list[dict], used: int, max_rounds: int) -> str:
    with_work = "cap" if used >= max_rounds else "fix"
    return with_work if findings or failures else "none"


def model_for(round_number: int, model: str, escalation_model: str, escalate_at: int) -> str:
    return escalation_model if round_number >= escalate_at else model


def failure_log(repo: str, run: dict) -> str:
    match = JOB_URL.search(run.get("details_url") or "")
    if not match:
        return ""
    try:
        log = gh("run", "view", match.group(1), "--repo", repo, "--job", match.group(2), "--log-failed")
    except subprocess.CalledProcessError as error:
        print(f"::warning::no log for {run['name']}: {error.stderr.strip()}")
        return "(the log could not be retrieved)"
    return "\n".join(log.splitlines()[-LOG_TAIL_LINES:])


def check_runs(repo: str, sha: str) -> list[dict]:
    pages = json.loads(
        gh("api", "--paginate", "--slurp", f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    )
    return [run for page in pages for run in page["check_runs"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--reviewer", default="github-actions[bot]")
    parser.add_argument("--bot", default="ashigaru-crunchtools")
    parser.add_argument("--max-rounds", default=MAX_ROUNDS, type=int)
    parser.add_argument("--wait-seconds", default=WAIT_SECONDS, type=int)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--escalation-model", default="opus")
    parser.add_argument("--escalate-at", default=ESCALATE_AT, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    prs = json.loads(
        gh("pr", "list", "--repo", args.repo, "--head", args.branch, "--state", "open",
           "--json", "number,headRefOid,author")
    )  # fmt: skip
    if not prs or not opened_by(prs[0], args.bot):
        print(f"findings: no open pull request by {args.bot} for {args.branch}")
        set_output("action", "none")
        return 0
    number, sha = prs[0]["number"], prs[0]["headRefOid"]
    own_run_id = os.environ.get("GITHUB_RUN_ID", "")

    deadline = time.monotonic() + args.wait_seconds
    runs = check_runs(args.repo, sha)
    while pending(runs, own_run_id) and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        runs = check_runs(args.repo, sha)

    findings = unanswered(api_pages(f"repos/{args.repo}/pulls/{number}/comments?per_page=100"),
                          args.reviewer)  # fmt: skip
    failures = failed(runs, own_run_id)
    used = rounds_used(api_pages(f"repos/{args.repo}/issues/{number}/comments?per_page=100"))
    action = plan(findings, failures, used, args.max_rounds)
    round_number = used + 1

    args.out.write_text(
        json.dumps(
            {
                "pr": number,
                "round": round_number,
                "findings": findings,
                "failed_checks": [
                    {"name": run["name"], "log": failure_log(args.repo, run)} for run in failures
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"findings: PR #{number} round {round_number}: {len(findings)} finding(s), "
          f"{len(failures)} failed check(s) -> {action}")  # fmt: skip
    set_output("action", action)
    set_output("pr", str(number))
    set_output("round", str(round_number))
    set_output("model", model_for(round_number, args.model, args.escalation_model, args.escalate_at))
    return 0


if __name__ == "__main__":
    sys.exit(main())
