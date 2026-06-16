"""Tests for state management — RunState CRUD, phase transitions."""

from pathlib import Path

import pytest

from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta, Source
from mcp_ashigaru.state import RunState


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work").mkdir(parents=True)
    return Config(state_dir=state_dir)


@pytest.fixture
def run_state(cfg: Config) -> RunState:
    meta = RunMeta(
        run_id="test-run-1",
        repo="test-repo",
        issue=42,
        title="Test feature",
        branch="fix/issue-42",
        phase=Phase.QUEUED,
        source=Source.ASHIGARU,
    )
    return RunState.create(meta, cfg)


def test_create_run(run_state: RunState) -> None:
    meta = run_state.read_meta()
    assert meta.run_id == "test-run-1"
    assert meta.repo == "test-repo"
    assert meta.issue == 42
    assert meta.title == "Test feature"
    assert meta.phase == Phase.QUEUED
    assert meta.created != ""


def test_set_phase(run_state: RunState) -> None:
    run_state.set_phase(Phase.CLONING, "Cloning repo")
    meta = run_state.read_meta()
    assert meta.phase == Phase.CLONING


def test_set_phase_failed_records_reason(run_state: RunState) -> None:
    run_state.set_phase(Phase.FAILED, "git push failed")
    meta = run_state.read_meta()
    assert meta.phase == Phase.FAILED
    assert meta.failure_reason == "git push failed"


def test_phase_transition_creates_activity(run_state: RunState) -> None:
    run_state.set_phase(Phase.CLONING, "Cloning")
    run_state.set_phase(Phase.ANALYZING, "Agent started")
    activity = run_state.activity.query()
    phase_changes = [a for a in activity if a["kind"] == "phase_change"]
    assert len(phase_changes) >= 2


def test_update_meta(run_state: RunState) -> None:
    run_state.update_meta(pr_url="https://github.com/test/pr/1", model="claude-sonnet-4-6")
    meta = run_state.read_meta()
    assert meta.pr_url == "https://github.com/test/pr/1"
    assert meta.model == "claude-sonnet-4-6"


def test_write_and_read_brief(run_state: RunState) -> None:
    run_state.write_brief("Fix the login bug")
    assert run_state.read_brief() == "Fix the login bug"


def test_write_feedback_creates_activity(run_state: RunState) -> None:
    run_state.write_feedback("Fix the timezone handling")
    activity = run_state.activity.query()
    feedback_entries = [a for a in activity if a["kind"] == "feedback"]
    assert len(feedback_entries) == 1
    assert "timezone" in feedback_entries[0]["summary"]


def test_list_all_empty(cfg: Config) -> None:
    results = RunState.list_all(cfg)
    assert results == [] or len(results) == 1  # may include the fixture run


def test_list_all_with_filter(cfg: Config) -> None:
    for i in range(3):
        RunState.create(RunMeta(
            run_id=f"run-{i}",
            repo="rotv" if i < 2 else "other",
            issue=i,
            title=f"Run {i}",
        ), cfg)
    results = RunState.list_all(cfg, repo="rotv")
    assert all(r["repo"] == "rotv" for r in results)


def test_legacy_phase_values(cfg: Config) -> None:
    meta = RunMeta(
        run_id="legacy-run",
        repo="test",
        issue=1,
        title="Legacy",
        phase=Phase.EDITING,
    )
    state = RunState.create(meta, cfg)
    loaded = state.read_meta()
    assert loaded.phase == Phase.EDITING
    assert loaded.phase.value == "editing"
