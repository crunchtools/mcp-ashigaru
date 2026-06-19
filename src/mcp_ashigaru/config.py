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
    notify_cmd: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_NOTIFY_CMD", ""
    ))
    notify_webhook: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_NOTIFY_WEBHOOK", ""
    ))
    notify_webhook_secret: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_NOTIFY_WEBHOOK_SECRET", ""
    ))
    heartbeat_interval: int = field(default_factory=lambda: int(
        os.environ.get("ASHIGARU_HEARTBEAT_INTERVAL", "300")
    ))
    matrix_homeserver: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_MATRIX_HOMESERVER", ""
    ))
    matrix_access_token: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_MATRIX_ACCESS_TOKEN", ""
    ))
    matrix_room_id: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_MATRIX_ROOM_ID", ""
    ))
    matrix_mention_user: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_MATRIX_MENTION_USER", ""
    ))
    matrix_device_id: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_MATRIX_DEVICE_ID", "ASHIGARU_BOT"
    ))
    matrix_crypto_dir: str = field(default_factory=lambda: os.environ.get(
        "ASHIGARU_MATRIX_CRYPTO_DIR", ""
    ))

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def work_dir(self) -> Path:
        return self.state_dir / "work"
