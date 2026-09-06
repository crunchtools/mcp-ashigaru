"""Structured activity log — human-readable milestones, not raw tool calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Activity, ActivityKind


class ActivityLog:
    """Append-only structured activity log stored as activity.jsonl."""

    def __init__(self, run_dir: Path) -> None:
        self._path = run_dir / "activity.jsonl"

    def append(self, activity: Activity) -> None:
        with self._path.open("a") as f:
            f.write(activity.model_dump_json() + "\n")

    def query(
        self,
        tail: int = 50,
        kind: ActivityKind | None = None,
    ) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if kind and entry.get("kind") != kind.value:
                continue
            entries.append(entry)
        if tail and len(entries) > tail:
            return entries[-tail:]
        return entries

    def latest(self, count: int = 3) -> list[dict[str, Any]]:
        return self.query(tail=count)

    @staticmethod
    def classify_tool_call(tool_name: str) -> ActivityKind:
        if tool_name in ("Read",):
            return ActivityKind.AGENT_READ
        if tool_name in ("Edit", "Write"):
            return ActivityKind.AGENT_EDIT
        if tool_name in ("Grep", "Glob"):
            return ActivityKind.AGENT_SEARCH
        if tool_name == "Bash":
            return ActivityKind.AGENT_BASH
        return ActivityKind.NOTE

    @staticmethod
    def summarize_tool_call(
        tool_name: str, tool_input: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """Return (summary, files_touched)."""
        fp = tool_input.get("file_path", "")
        files = [fp] if fp else []
        summarizers: dict[str, tuple[str, list[str]]] = {
            "Read": (
                f"Read {fp}" + (
                    f" (lines {tool_input.get('offset', '')}"
                    f"-{tool_input.get('limit', '')})"
                    if tool_input.get("offset") else ""
                ),
                files,
            ),
            "Edit": (f"Edit {fp}: '{(tool_input.get('old_string', '') or '')[:40]}...'", files),
            "Write": (f"Write {fp} ({len(tool_input.get('content', ''))} bytes)", files),
            "Bash": (f"Bash: {(tool_input.get('command', '') or '')[:80]}", []),
            "Grep": (f"Grep: {tool_input.get('pattern', '')} in {tool_input.get('path', '')}", []),
            "Glob": (f"Glob: {tool_input.get('pattern', '')}", []),
        }
        return summarizers.get(tool_name, (f"{tool_name}: {str(tool_input)[:60]}", []))
