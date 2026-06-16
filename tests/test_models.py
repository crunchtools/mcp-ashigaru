"""Tests for Pydantic models — serialization, defaults, legacy compat."""

import json

from mcp_ashigaru.models import Phase, RunMeta, Source


def test_runmeta_defaults() -> None:
    meta = RunMeta(run_id="test-1", repo="rotv", issue=1)
    assert meta.title == ""
    assert meta.branch == ""
    assert meta.phase == Phase.QUEUED
    assert meta.source == Source.ASHIGARU
    assert meta.current_tier is None or meta.current_tier == 1
    assert meta.pr_url is None
    assert meta.attempts == []


def test_runmeta_serialization_roundtrip() -> None:
    meta = RunMeta(
        run_id="test-1", repo="rotv", issue=42,
        title="Test feature", branch="fix/issue-42",
        phase=Phase.ANALYZING, source=Source.EXTERNAL,
    )
    json_str = meta.model_dump_json()
    loaded = RunMeta.model_validate_json(json_str)
    assert loaded.run_id == "test-1"
    assert loaded.phase == Phase.ANALYZING
    assert loaded.source == Source.EXTERNAL


def test_legacy_meta_without_title() -> None:
    legacy = json.dumps({
        "run_id": "old-run",
        "repo": "rotv",
        "issue": 100,
        "phase": "editing",
        "current_tier": None,
    })
    meta = RunMeta.model_validate_json(legacy)
    assert meta.title == ""
    assert meta.phase == Phase.EDITING
    assert meta.current_tier is None


def test_legacy_meta_without_source() -> None:
    legacy = json.dumps({
        "run_id": "old-run",
        "repo": "rotv",
        "issue": 100,
        "phase": "awaiting-approval",
    })
    meta = RunMeta.model_validate_json(legacy)
    assert meta.source == Source.ASHIGARU
    assert meta.phase == Phase.AWAITING_APPROVAL


def test_all_legacy_phases_valid() -> None:
    legacy_phases = ["editing", "gating", "awaiting-approval", "on-dev", "deploying-preview"]
    for p in legacy_phases:
        meta = RunMeta(run_id="t", repo="r", issue=1, phase=Phase(p))
        assert meta.phase.value == p


def test_all_v1_phases_valid() -> None:
    v1_phases = [
        "queued", "cloning", "analyzing", "implementing", "validating",
        "building", "awaiting-review", "reviewing", "preview-live",
        "shipped", "failed", "escalated", "cancelled",
    ]
    for p in v1_phases:
        meta = RunMeta(run_id="t", repo="r", issue=1, phase=Phase(p))
        assert meta.phase.value == p
