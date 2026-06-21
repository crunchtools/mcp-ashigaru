"""Run state management — CRUD for meta.json on disk."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .activity import ActivityLog
from .config import Config
from .models import Activity, ActivityKind, Attempt, Phase, RunMeta
from .notify import fire_phase_change

RUNID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")
LOG_FILES = ("agent.err", "setup.log", "runner.log", "preview.log")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunState:
    """Manages a single run's state on disk."""

    def __init__(self, run_dir: Path, config: Config | None = None) -> None:
        self._dir = run_dir
        self._meta_path = run_dir / "meta.json"
        self._activity = ActivityLog(run_dir)
        self._config = config

    @classmethod
    def create(cls, meta: RunMeta, config: Config) -> RunState:
        run_dir = config.runs_dir / meta.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        meta.created = meta.created or _now()
        meta.updated = _now()
        (run_dir / "meta.json").write_text(meta.model_dump_json(indent=2))
        state = cls(run_dir, config)
        state._activity.append(Activity(
            timestamp=_now(),
            kind=ActivityKind.REGISTRATION,
            summary=f"Run created: {meta.title} ({meta.repo} #{meta.issue})",
            phase_after=meta.phase,
        ))
        return state

    @classmethod
    def load(cls, run_id: str, config: Config) -> RunState | None:
        if not RUNID_RE.fullmatch(run_id):
            return None
        run_dir = config.runs_dir / run_id
        if not (run_dir / "meta.json").exists():
            return None
        return cls(run_dir, config)

    @property
    def run_dir(self) -> Path:
        return self._dir

    @property
    def activity(self) -> ActivityLog:
        return self._activity

    def read_meta(self) -> RunMeta:
        return RunMeta.model_validate_json(self._meta_path.read_text())

    def _write_meta(self, meta: RunMeta) -> None:
        meta.updated = _now()
        self._meta_path.write_text(meta.model_dump_json(indent=2))

    def set_phase(self, phase: Phase, reason: str = "") -> None:
        meta = self.read_meta()
        old_phase = meta.phase
        meta.phase = phase
        if phase == Phase.FAILED and reason:
            meta.failure_reason = reason
        self._write_meta(meta)
        self._activity.append(Activity(
            timestamp=_now(),
            kind=ActivityKind.PHASE_CHANGE,
            summary=reason or f"Phase: {old_phase.value} → {phase.value}",
            phase_before=old_phase,
            phase_after=phase,
        ))
        if phase in (Phase.FAILED, Phase.ESCALATED, Phase.CANCELLED) and meta.branch:
            self._cleanup_remote_branch(meta)

        if (
            self._config
            and old_phase != phase
            and (
                self._config.notify_webhook
                or self._config.notify_cmd
                or (self._config.matrix_homeserver and self._config.matrix_room_id)
            )
        ):
            fire_phase_change(meta, old_phase, phase, self._config, state=self)

    def _cleanup_remote_branch(self, meta: RunMeta) -> None:
        if not self._config or not meta.branch:
            return
        import asyncio

        from .git_ops import delete_remote_branch

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(delete_remote_branch(meta.repo, meta.branch, self._config))
        except RuntimeError:
            pass

    def update_meta(self, **fields: Any) -> None:
        meta = self.read_meta()
        for k, v in fields.items():
            if hasattr(meta, k):
                setattr(meta, k, v)
        self._write_meta(meta)

    def append_attempt(self, attempt: Attempt) -> None:
        meta = self.read_meta()
        meta.attempts.append(attempt)
        meta.current_tier = attempt.tier
        self._write_meta(meta)

    def write_brief(self, brief: str) -> None:
        (self._dir / "brief.txt").write_text(brief)

    def read_brief(self) -> str:
        p = self._dir / "brief.txt"
        return p.read_text() if p.exists() else ""

    def write_feedback(self, notes: str) -> None:
        (self._dir / "feedback.txt").write_text(notes)
        self._activity.append(Activity(
            timestamp=_now(),
            kind=ActivityKind.FEEDBACK,
            summary=f"Feedback: {notes[:100]}",
            detail=notes,
        ))

    def read_feedback(self) -> str:
        p = self._dir / "feedback.txt"
        return p.read_text() if p.exists() else ""

    def clear_feedback(self) -> None:
        p = self._dir / "feedback.txt"
        if p.exists():
            p.unlink()

    def get_detail(self) -> dict[str, Any]:
        meta = self.read_meta()
        events_path = self._dir / "events.jsonl"
        event_count = 0
        if events_path.exists():
            event_count = sum(1 for _ in events_path.open())

        log_sizes: dict[str, int] = {}
        for name in LOG_FILES:
            p = self._dir / name
            if p.exists():
                log_sizes[name] = p.stat().st_size

        return {
            **meta.model_dump(),
            "event_count": event_count,
            "log_sizes": log_sizes,
            "brief": self.read_brief()[:500],
        }

    def delete(self) -> None:
        import shutil
        if self._config is not None:
            work_dir = self._config.work_dir / self._dir.name
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(self._dir, ignore_errors=True)

    def get_log(self, log_name: str | None = None, max_bytes: int = 50_000) -> str:
        if log_name:
            p = self._dir / log_name
            if not p.exists():
                return ""
            content = p.read_text()
            return content[-max_bytes:] if len(content) > max_bytes else content

        parts: list[str] = []
        total = 0
        for name in LOG_FILES:
            p = self._dir / name
            if not p.exists():
                continue
            content = p.read_text()
            if not content.strip():
                continue
            header = f"=== {name} ==="
            remaining = max_bytes - total - len(header) - 2
            if remaining <= 0:
                parts.append("[truncated]")
                break
            if len(content) > remaining:
                content = content[-remaining:]
            parts.append(header)
            parts.append(content)
            total += len(header) + len(content) + 2
        return "\n".join(parts)

    @staticmethod
    def list_all(
        config: Config,
        repo: str | None = None,
        phase: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        runs_dir = config.runs_dir
        if not runs_dir.is_dir():
            return []
        results: list[dict[str, Any]] = []
        for entry in runs_dir.iterdir():
            if not entry.is_dir() or not (entry / "meta.json").exists():
                continue
            try:
                meta = json.loads((entry / "meta.json").read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if repo and meta.get("repo") != repo:
                continue
            if phase and meta.get("phase") != phase:
                continue
            if source and meta.get("source") != source:
                continue
            results.append({
                "run_id": meta.get("run_id", entry.name),
                "repo": meta.get("repo"),
                "issue": meta.get("issue"),
                "title": meta.get("title", ""),
                "phase": meta.get("phase"),
                "source": meta.get("source", "ashigaru"),
                "current_tier": meta.get("current_tier"),
                "model": meta.get("model"),
                "pr_url": meta.get("pr_url"),
                "preview_url": meta.get("preview_url"),
                "created": meta.get("created"),
                "attempts": len(meta.get("attempts", [])),
            })
        results.sort(key=lambda r: r.get("created") or "", reverse=True)
        return results[:limit]
