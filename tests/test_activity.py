"""Tests for activity log — classification, summarization, persistence."""

from pathlib import Path

import pytest

from mcp_ashigaru.activity import ActivityLog
from mcp_ashigaru.models import Activity, ActivityKind


@pytest.fixture
def log(tmp_path: Path) -> ActivityLog:
    return ActivityLog(tmp_path)


def test_append_and_query(log: ActivityLog) -> None:
    log.append(Activity(
        timestamp="2026-06-16T10:00:00Z",
        kind=ActivityKind.PHASE_CHANGE,
        summary="Phase: queued -> cloning",
    ))
    log.append(Activity(
        timestamp="2026-06-16T10:01:00Z",
        kind=ActivityKind.AGENT_READ,
        summary="Read server.py",
        files=["server.py"],
    ))
    entries = log.query()
    assert len(entries) == 2
    assert entries[0]["kind"] == "phase_change"
    assert entries[1]["kind"] == "agent_read"


def test_query_with_tail(log: ActivityLog) -> None:
    for i in range(10):
        log.append(Activity(
            timestamp=f"2026-06-16T10:{i:02d}:00Z",
            kind=ActivityKind.NOTE,
            summary=f"Entry {i}",
        ))
    entries = log.query(tail=3)
    assert len(entries) == 3
    assert entries[0]["summary"] == "Entry 7"


def test_latest(log: ActivityLog) -> None:
    for i in range(5):
        log.append(Activity(
            timestamp=f"2026-06-16T10:{i:02d}:00Z",
            kind=ActivityKind.NOTE,
            summary=f"Entry {i}",
        ))
    latest = log.latest(2)
    assert len(latest) == 2
    assert latest[0]["summary"] == "Entry 3"


def test_classify_read() -> None:
    kind = ActivityLog.classify_tool_call("Read", {"file_path": "test.py"})
    assert kind == ActivityKind.AGENT_READ


def test_classify_edit() -> None:
    kind = ActivityLog.classify_tool_call("Edit", {"file_path": "test.py"})
    assert kind == ActivityKind.AGENT_EDIT


def test_classify_write() -> None:
    kind = ActivityLog.classify_tool_call("Write", {"file_path": "test.py"})
    assert kind == ActivityKind.AGENT_EDIT


def test_classify_bash() -> None:
    kind = ActivityLog.classify_tool_call("Bash", {"command": "npm test"})
    assert kind == ActivityKind.AGENT_BASH


def test_classify_grep() -> None:
    kind = ActivityLog.classify_tool_call("Grep", {"pattern": "foo"})
    assert kind == ActivityKind.AGENT_SEARCH


def test_summarize_read() -> None:
    summary, files = ActivityLog.summarize_tool_call("Read", {"file_path": "/app/server.py"})
    assert "server.py" in summary
    assert "/app/server.py" in files


def test_summarize_bash() -> None:
    summary, files = ActivityLog.summarize_tool_call("Bash", {"command": "npm test"})
    assert "npm test" in summary
    assert files == []


def test_summarize_write() -> None:
    summary, files = ActivityLog.summarize_tool_call("Write", {
        "file_path": "/app/new.py",
        "content": "x" * 100,
    })
    assert "100 bytes" in summary
    assert "/app/new.py" in files


def test_empty_log_query(log: ActivityLog) -> None:
    entries = log.query()
    assert entries == []
