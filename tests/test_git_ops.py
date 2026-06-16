"""Tests for git_ops — gate check, env merging, safe.directory."""

import os
from pathlib import Path

import pytest

from mcp_ashigaru.git_ops import _run, check_gate


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    await _run("git", "init", cwd=repo)
    await _run("git", "config", "user.email", "test@test.com", cwd=repo)
    await _run("git", "config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("initial")
    await _run("git", "add", "-A", cwd=repo)
    await _run("git", "commit", "-m", "init", cwd=repo)
    return repo


async def test_gate_no_changes(git_repo: Path) -> None:
    result, output = await check_gate(git_repo)
    assert result == "no-change"
    assert "No files modified" in output


async def test_gate_modified_file(git_repo: Path) -> None:
    (git_repo / "README.md").write_text("modified")
    result, _ = await check_gate(git_repo)
    assert result == "passed"


async def test_gate_new_untracked_file(git_repo: Path) -> None:
    (git_repo / "NEW_FILE.md").write_text("new content")
    result, _ = await check_gate(git_repo)
    assert result == "passed"


async def test_gate_staged_file(git_repo: Path) -> None:
    (git_repo / "README.md").write_text("staged change")
    await _run("git", "add", "README.md", cwd=git_repo)
    result, _ = await check_gate(git_repo)
    assert result == "passed"


async def test_run_merges_env_with_os_environ() -> None:
    rc, output = await _run(
        "bash", "-c", "echo PATH=$PATH",
        env={"CUSTOM_VAR": "test"},
    )
    assert rc == 0
    assert "PATH=" in output
    assert output.strip() != "PATH="


async def test_run_without_env_inherits() -> None:
    rc, output = await _run("bash", "-c", "echo HOME=$HOME")
    assert rc == 0
    assert os.environ.get("HOME", "") in output


async def test_run_logs_to_file(tmp_path: Path) -> None:
    log = tmp_path / "test.log"
    await _run("echo", "hello", log_path=log)
    assert "hello" in log.read_text()
