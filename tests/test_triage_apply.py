"""Verdict to label: only confident bug-class verdicts reach the code agent."""

import pytest

from common import NEEDS_INFO, READY_TO_CODE, TRIAGED
from triage_apply import comment, decide

AUTO = {"bug", "documentation", "performance"}


def verdict(category, confidence=0.9, needs_info=False):
    return {"category": category, "confidence": confidence, "needs_info": needs_info, "summary": "s"}


@pytest.mark.parametrize("category", sorted(AUTO))
def test_bug_class_work_goes_to_code(category):
    assert decide(verdict(category), AUTO, 0.7) == READY_TO_CODE


def test_feature_waits_for_a_maintainer():
    assert decide(verdict("feature"), AUTO, 0.7) == TRIAGED


def test_low_confidence_bug_waits_for_a_maintainer():
    assert decide(verdict("bug", confidence=0.5), AUTO, 0.7) == TRIAGED


@pytest.mark.parametrize("category", ["question", "unclear"])
def test_questions_need_info(category):
    assert decide(verdict(category), AUTO, 0.7) == NEEDS_INFO


def test_bug_that_needs_info_is_not_coded():
    assert decide(verdict("bug", needs_info=True), AUTO, 0.7) == NEEDS_INFO


@pytest.mark.parametrize("bad", [{}, {"category": "bug"}, {"category": "rm -rf", "confidence": 1}])
def test_malformed_verdict_goes_to_a_human(bad):
    assert decide(bad, AUTO, 0.7) == TRIAGED


def test_category_can_be_taken_out_of_the_automatic_set():
    assert decide(verdict("performance"), {"bug"}, 0.7) == TRIAGED


def test_comment_names_the_next_step():
    text = comment(verdict("feature"), TRIAGED)
    assert "feature (90%)" in text and "ready-to-code" in text
