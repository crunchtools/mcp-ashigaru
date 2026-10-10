"""Tests for the registered MCP tools: read-only classification and behavior."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_ashigaru import server
from mcp_ashigaru.config import Config
from mcp_ashigaru.models import Phase, RunMeta, Source
from mcp_ashigaru.server import mcp
from mcp_ashigaru.state import LOG_FILES, RunState

RUN_ID = "test-repo-42-1"

READ_ONLY = frozenset({"status", "list_runs", "run_activity", "run_log"})
WRITES = frozenset(
    {
        "create_run",
        "dispatch_worker",
        "run_build",
        "create_pr",
        "deploy_preview",
        "request_changes",
        "run_review",
        "promote",
        "teardown_preview",
        "cancel_run",
    }
)

# Arguments that satisfy each read-only tool's parameters, one call per code path.
READ_ONLY_CALLS: list[tuple[str, dict[str, object]]] = [
    ("status", {"run_id": RUN_ID}),
    ("status", {"run_id": "no-such-run"}),
    ("list_runs", {}),
    ("list_runs", {"repo": "test-repo", "phase": "queued", "source": "ashigaru", "limit": 5}),
    ("run_activity", {"run_id": RUN_ID, "tail": 5}),
    ("run_log", {"run_id": RUN_ID}),
    ("run_log", {"run_id": RUN_ID, "log_name": "setup.log"}),
    ("run_log", {"run_id": RUN_ID, "log_name": "absent.log"}),
]


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """A populated run on disk, with the server pointed at it."""
    state_dir = tmp_path / "ashigaru"
    (state_dir / "runs").mkdir(parents=True)
    (state_dir / "work" / RUN_ID / "test-repo").mkdir(parents=True)
    config = Config(
        state_dir=state_dir,
        slots_dir=tmp_path / "slots",
        config_dir=tmp_path / "config",
        notify_cmd="",
        notify_webhook="",
        matrix_homeserver="",
    )
    meta = RunMeta(
        run_id=RUN_ID,
        repo="test-repo",
        issue=42,
        title="Test feature",
        branch="fix/issue-42",
        phase=Phase.QUEUED,
        source=Source.ASHIGARU,
    )
    state = RunState.create(meta, config)
    state.write_brief("do the thing")
    for name in LOG_FILES:
        (state.run_dir / name).write_text(f"{name} line\n")
    monkeypatch.setattr(server, "CFG", config)
    return config


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Every path under root with its mtime, and its bytes when it is a file."""
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    }


def _spawn_mock() -> AsyncMock:
    proc = MagicMock(returncode=0)
    proc.communicate = AsyncMock(return_value=(b"ok\n", None))
    return AsyncMock(return_value=proc)


class TestReadOnlyAnnotation:
    """Every registered tool is classified, and the reads really only read."""

    async def test_every_tool_is_classified(self) -> None:
        tools = await mcp.list_tools()
        assert READ_ONLY.isdisjoint(WRITES)
        assert {tool.name for tool in tools} == READ_ONLY | WRITES
        annotated = {
            tool.name
            for tool in tools
            if tool.annotations is not None
            and tool.annotations.model_dump(by_alias=True).get("readOnlyHint") is True
        }
        assert annotated == READ_ONLY

    def test_every_read_only_tool_is_exercised(self) -> None:
        assert {name for name, _ in READ_ONLY_CALLS} == READ_ONLY

    @pytest.mark.usefixtures("cfg")
    @pytest.mark.parametrize(("name", "args"), READ_ONLY_CALLS)
    async def test_read_only_tool_leaves_disk_alone(
        self, tmp_path: Path, name: str, args: dict[str, object]
    ) -> None:
        """Ashigaru's backend is its state directory and the processes it spawns.

        A read changes no file under it (content, mtime, or the set of paths)
        and starts no subprocess.
        """
        before = _snapshot(tmp_path)
        with patch("asyncio.create_subprocess_exec", _spawn_mock()) as spawn:
            await mcp.call_tool(name, args)
        assert _snapshot(tmp_path) == before
        assert spawn.await_count == 0

    @pytest.mark.usefixtures("cfg")
    async def test_write_tool_trips_the_same_checks(self, tmp_path: Path) -> None:
        """Control: the checks above fail for a tool that writes and spawns."""
        before = _snapshot(tmp_path)
        with patch("asyncio.create_subprocess_exec", _spawn_mock()) as spawn:
            await mcp.call_tool("run_build", {"run_id": RUN_ID, "command": "true"})
        assert _snapshot(tmp_path) != before
        assert spawn.await_count == 1
