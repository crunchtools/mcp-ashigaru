"""Per-repo configuration loaded from repos.json on the config volume."""

from __future__ import annotations

import json

from .config import Config
from .models import RepoConfig


def get_repo_config(repo: str, config: Config) -> RepoConfig:
    repos_path = config.config_dir / "repos.json"
    if not repos_path.exists():
        return RepoConfig()
    try:
        data = json.loads(repos_path.read_text())
    except (json.JSONDecodeError, OSError):
        return RepoConfig()
    entry = data.get(repo, {})
    if not entry:
        return RepoConfig()
    return RepoConfig(**entry)
