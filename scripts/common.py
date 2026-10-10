"""Shared helpers: the `gh` CLI, step outputs, and the label vocabulary."""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
from typing import Any

READY_FOR_TRIAGE = "ready-for-triage"
READY_TO_CODE = "ready-to-code"
TRIAGED = "triaged"
NEEDS_INFO = "needs-info"
NEEDS_HUMAN = "needs-human"

STATE_LABELS = {
    READY_FOR_TRIAGE: ("c5def5", "Ashigaru: queued for triage"),
    READY_TO_CODE: ("0e8a16", "Ashigaru: bug-class work, will be fixed automatically"),
    TRIAGED: ("bfd4f2", "Ashigaru: triaged, waiting for a human decision"),
    NEEDS_INFO: ("fbca04", "Ashigaru: cannot act without more information"),
    NEEDS_HUMAN: ("d93f0b", "Ashigaru: automation stopped, a human takes over"),
}

WRITE_PERMISSIONS = {"admin", "write"}  # the API folds maintain into write, triage into read
BRANCH_PREFIX = "ashigaru/"
PROTECTED_DIR = ".github/"
DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)


def gh(*args: str, stdin: str | None = None) -> str:
    """Run `gh` and return stdout. Raises CalledProcessError on a non-zero exit."""
    done = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, input=stdin)
    return done.stdout


def api(path: str, *args: str) -> Any:
    return json.loads(gh("api", path, *args))


def api_pages(path: str) -> list[Any]:
    """Every item of a paginated list endpoint."""
    pages = json.loads(gh("api", "--paginate", "--slurp", path))
    return [item for page in pages for item in page]


@functools.cache
def can_write(repo: str, login: str) -> bool:
    """True when the account's effective permission on the repo is write or higher.

    Asked of the API, not read off `author_association`: MEMBER and COLLABORATOR
    say how an account relates to the repo, not what it may do there.
    """
    try:
        found = api(f"repos/{repo}/collaborators/{login}/permission")
    except subprocess.CalledProcessError as error:
        if "404" not in error.stderr:  # 404 is the API's answer for "not a collaborator"
            print(f"::warning::permission lookup for {login} on {repo} failed: {error.stderr.strip()}")
        return False
    return found.get("permission") in WRITE_PERMISSIONS


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return done.stdout


def set_output(name: str, value: str) -> None:
    """Write a step output; print it when not running in Actions."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        print(f"{name}={value}")
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}<<ASHIGARU_EOF\n{value}\nASHIGARU_EOF\n")


def ensure_labels(repo: str, names: list[str]) -> set[str]:
    """Create the state labels a repo is missing and return every label it has."""
    existing = {label["name"] for label in api_pages(f"repos/{repo}/labels?per_page=100")}
    for name in names:
        if name in existing or name not in STATE_LABELS:
            continue
        color, description = STATE_LABELS[name]
        try:
            gh("label", "create", name, "--repo", repo, "--color", color, "--description", description)
        except subprocess.CalledProcessError as error:
            if "already exists" not in error.stderr:  # another run created it first
                raise
        existing.add(name)
    return existing


def set_state(repo: str, number: int, state: str, extra: tuple[str, ...] = ()) -> None:
    """Give an issue or PR exactly one state label, plus extra labels the repo already has."""
    existing = ensure_labels(repo, [state])
    add = [state, *(name for name in extra if name in existing)]
    current = {label["name"] for label in api(f"repos/{repo}/issues/{number}")["labels"]}
    args = ["issue", "edit", str(number), "--repo", repo, "--add-label", ",".join(add)]
    drop = [name for name in STATE_LABELS if name != state and name in current]
    if drop:
        args += ["--remove-label", ",".join(drop)]
    gh(*args)


def touches_protected(patch: str) -> bool:
    """True when a git diff changes a file under .github/ (either side of a rename).

    Workflows, local actions, CODEOWNERS and Dependabot config decide what CI runs
    and who reviews; Ashigaru leaves all of it to people.
    """
    return any(
        path.startswith(PROTECTED_DIR) for match in DIFF_HEADER.finditer(patch) for path in match.groups()
    )
