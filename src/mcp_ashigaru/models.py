"""Pydantic data models for Ashigaru run state."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Phase(str, Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    ANALYZING = "analyzing"
    IMPLEMENTING = "implementing"
    VALIDATING = "validating"
    BUILDING = "building"
    AWAITING_REVIEW = "awaiting-review"
    REVIEWING = "reviewing"
    PREVIEW_LIVE = "preview-live"
    SHIPPED = "shipped"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class Source(str, Enum):
    ASHIGARU = "ashigaru"
    EXTERNAL = "external"


class ActivityKind(str, Enum):
    PHASE_CHANGE = "phase_change"
    AGENT_READ = "agent_read"
    AGENT_EDIT = "agent_edit"
    AGENT_BASH = "agent_bash"
    AGENT_SEARCH = "agent_search"
    GATE_CHECK = "gate_check"
    GIT_OP = "git_op"
    PREVIEW_OP = "preview_op"
    FEEDBACK = "feedback"
    REGISTRATION = "registration"
    REVIEW_OP = "review_op"
    ERROR = "error"
    NOTE = "note"


class Attempt(BaseModel):
    tier: int
    model: str
    gate: str
    gate_output: str = ""
    started_at: str = ""
    finished_at: str = ""


class RunMeta(BaseModel):
    run_id: str
    repo: str
    issue: int
    title: str
    branch: str = ""
    phase: Phase = Phase.QUEUED
    source: Source = Source.ASHIGARU
    model: str = ""
    current_tier: int = 1
    pr_url: str | None = None
    preview_url: str | None = None
    preview_slot: int | None = None
    preview_domain: str | None = None
    gate: str | None = None
    failure_reason: str | None = None
    attempts: list[Attempt] = []
    created: str = ""
    updated: str = ""
    notes: str = ""


class Activity(BaseModel):
    timestamp: str
    kind: ActivityKind
    summary: str
    detail: str = ""
    phase_before: Phase | None = None
    phase_after: Phase | None = None
    files: list[str] = []


class SlotLock(BaseModel):
    run_id: str
    repo: str
    branch: str
    container: str
    slot: int
    port: int
    domain: str
    allocated_at: str


class RepoConfig(BaseModel):
    profile: str = "code"
    preview_domain: str = "crunchtools.com"
    prod_container: str = ""
    base_image: str = ""
