"""Environment configuration for Ashigaru."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    state_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("ASHIGARU_STATE_DIR", "/home/devrunner/ashigaru")
    ))
    org: str = field(default_factory=lambda: os.environ.get("ASHIGARU_ORG", "crunchtools"))
    agent_image: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_AGENT_IMAGE", "localhost/rotv-dev-runner:latest"
    ))
    container_host: str = field(default_factory=lambda: os.environ.get(
        "CONTAINER_HOST", "unix:///run/podman/podman.sock"
    ))
    host_podman: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_HOST_PODMAN", "unix:///run/host-podman/podman.sock"
    ))
    slots_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("ASHIGARU_SLOTS_DIR", "/srv/ashigaru/slots")
    ))
    config_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("ASHIGARU_CONFIG_DIR", "/srv/ashigaru/config")
    ))
    max_slots: int = field(default_factory=lambda: int(
        os.environ.get("ASHIGARU_MAX_SLOTS", "5")
    ))
    gh_token: str = field(default_factory=lambda: os.environ.get("GH_TOKEN", ""))
    claude_token: str = field(default_factory=lambda: os.environ.get(
        "CLAUDE_CODE_OAUTH_TOKEN", ""
    ))
    default_model: str = field(default_factory=lambda: os.environ.get(
        "ANTHROPIC_MODEL", "claude-sonnet-4-6"
    ))

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def work_dir(self) -> Path:
        return self.state_dir / "work"
