"""Data-reading functions for Ashigaru run state.

Shared by the MCP tools and the REST API endpoints. All functions read from
the filesystem (RUNS_DIR) and return plain dicts — no I/O to external services.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any

RUNID_RE = re.compile(r"[a-z0-9-]{1,64}")

LOG_FILES = ("agent.err", "setup.log", "runner.log", "preview.log")


def _runs_dir() -> Path:
    state = os.environ.get("ASHIGARU_STATE_DIR", "/home/devrunner/ashigaru")
    return Path(state) / "runs"


def _read_meta(run_dir: Path) -> dict[str, Any] | None:
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def list_all_runs(
    repo: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Scan RUNS_DIR for all runs, returning summaries sorted newest-first."""
    runs_dir = _runs_dir()
    if not runs_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        meta = _read_meta(entry)
        if meta is None:
            continue
        if repo and meta.get("repo") != repo:
            continue
        if status and meta.get("phase") != status:
            continue
        results.append({
            "run_id": meta.get("run_id", entry.name),
            "repo": meta.get("repo"),
            "issue": meta.get("issue"),
            "phase": meta.get("phase"),
            "current_tier": meta.get("current_tier"),
            "model": meta.get("model"),
            "pr_url": meta.get("pr_url"),
            "preview_url": meta.get("preview_url"),
            "created": meta.get("created"),
            "attempts": len(meta.get("attempts", [])),
        })

    results.sort(key=lambda r: r.get("created") or "", reverse=True)
    return results[:limit]


def get_run_detail(run_id: str) -> dict[str, Any] | None:
    """Full run metadata plus event count and log file sizes."""
    if not RUNID_RE.fullmatch(run_id):
        return None
    run_dir = _runs_dir() / run_id
    meta = _read_meta(run_dir)
    if meta is None:
        return None

    events_path = run_dir / "events.jsonl"
    event_count = 0
    if events_path.exists():
        with contextlib.suppress(OSError):
            event_count = sum(1 for _ in events_path.open())

    log_sizes: dict[str, int] = {}
    for name in LOG_FILES:
        p = run_dir / name
        if p.exists():
            with contextlib.suppress(OSError):
                log_sizes[name] = p.stat().st_size

    brief_path = run_dir / "brief.txt"
    brief = ""
    if brief_path.exists():
        with contextlib.suppress(OSError):
            brief = brief_path.read_text()[:500]

    return {
        **meta,
        "event_count": event_count,
        "log_sizes": log_sizes,
        "brief": brief,
    }


def _parse_event(e: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract action records from a single parsed event line."""
    results: list[dict[str, Any]] = []
    if e.get("type") == "assistant":
        for block in e.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                results.append({
                    "tool": block.get("name", ""),
                    "input_preview": _truncate(json.dumps(block.get("input", {})), 200),
                    "timestamp": e.get("timestamp"),
                })
    elif e.get("type") == "result":
        results.append({
            "tool": "__result__",
            "input_preview": _truncate(str(e.get("result", "")), 200),
            "is_error": e.get("is_error"),
            "timestamp": e.get("timestamp"),
        })
    return results


def get_run_events(run_id: str, tail: int = 200) -> list[dict[str, Any]]:
    """Parse events.jsonl and extract tool_use actions with timestamps."""
    if not RUNID_RE.fullmatch(run_id):
        return []
    events_path = _runs_dir() / run_id / "events.jsonl"
    if not events_path.exists():
        return []

    actions: list[dict[str, Any]] = []
    try:
        for line in events_path.read_text().splitlines():
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                actions.extend(_parse_event(json.loads(line)))
    except OSError:
        return []

    if tail and len(actions) > tail:
        return actions[-tail:]
    return actions


def get_run_log(run_id: str, max_bytes: int = 50_000) -> str:
    """Concatenate log files into a combined plain-text log."""
    if not RUNID_RE.fullmatch(run_id):
        return ""
    run_dir = _runs_dir() / run_id
    if not run_dir.is_dir():
        return ""

    parts: list[str] = []
    total = 0
    for name in LOG_FILES:
        p = run_dir / name
        if not p.exists():
            continue
        try:
            content = p.read_text()
        except OSError:
            continue
        if not content.strip():
            continue
        header = f"=== {name} ==="
        parts.append(header)
        remaining = max_bytes - total - len(header) - 2
        if remaining <= 0:
            parts.append("[truncated — log budget exhausted]")
            break
        if len(content) > remaining:
            content = content[-remaining:]
            parts.append(f"[...truncated to last {remaining} bytes...]")
        parts.append(content)
        total += len(header) + len(content) + 2

    return "\n".join(parts)


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."
