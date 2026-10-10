#!/usr/bin/env python3
"""Turn the triage agent's structured verdict into labels and a comment."""

from __future__ import annotations

import argparse
import json
import sys

from common import NEEDS_INFO, READY_TO_CODE, TRIAGED, gh, set_output, set_state

CATEGORIES = ("bug", "documentation", "performance", "feature", "question", "unclear")
TYPE_LABELS = {"bug": "bug", "documentation": "documentation", "feature": "enhancement"}
NEXT_STEP = {
    READY_TO_CODE: "Ashigaru will open a pull request for this.",
    TRIAGED: "Left for a maintainer. Add `ready-to-code` to have Ashigaru take it.",
    NEEDS_INFO: "Answer the question above, then add `ready-for-triage`.",
}
MARKER = "<!-- ashigaru:triage -->"


def decide(result: dict, auto_categories: set[str], min_confidence: float) -> str:
    """The state label for a verdict. Anything malformed goes to a human."""
    category = result.get("category")
    confidence = result.get("confidence")
    if category not in CATEGORIES or not isinstance(confidence, (int, float)):
        return TRIAGED
    if result.get("needs_info") or category in ("question", "unclear"):
        return NEEDS_INFO
    if category in auto_categories and confidence >= min_confidence:
        return READY_TO_CODE
    return TRIAGED


def comment(result: dict, state: str) -> str:
    category = result.get("category", "unknown")
    confidence = result.get("confidence")
    shown = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "n/a"
    summary = str(result.get("summary", "")).strip() or "No summary returned."
    return f"{MARKER}\n**Ashigaru triage:** {category} ({shown})\n\n{summary}\n\n{NEXT_STEP[state]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--result", required=True, help="structured output JSON from the agent")
    parser.add_argument("--auto-categories", default="bug,documentation,performance")
    parser.add_argument("--min-confidence", default=0.7, type=float)
    args = parser.parse_args()

    try:
        result = json.loads(args.result)
    except json.JSONDecodeError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    auto = {name.strip() for name in args.auto_categories.split(",") if name.strip()}
    state = decide(result, auto, args.min_confidence)

    type_label = TYPE_LABELS.get(result.get("category", ""))
    set_state(args.repo, args.issue, state, extra=(type_label,) if type_label else ())
    gh("issue", "comment", str(args.issue), "--repo", args.repo, "--body", comment(result, state))
    set_output("state", state)
    set_output("auto_code", "true" if state == READY_TO_CODE else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
