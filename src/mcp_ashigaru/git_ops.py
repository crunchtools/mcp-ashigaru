"""Git and GitHub CLI operations via subprocess."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from .config import Config

# The ashigaru container runs as root but clones are chowned to 1000 (devrunner)
# for the worker containers. Suppress git's dubious-ownership check.
subprocess.run(
    ["git", "config", "--global", "--add", "safe.directory", "*"],
    capture_output=True, check=False,
)


async def _run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> tuple[int, str]:
    merged_env = {**os.environ, **env} if env else None
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
    )
    out, _ = await proc.communicate()
    output = out.decode() if out else ""
    if log_path:
        with log_path.open("a") as f:
            f.write(output)
    return proc.returncode or 0, output


async def clone(
    repo: str,
    target: Path,
    config: Config,
    log_path: Path | None = None,
) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://x-access-token:{config.gh_token}@github.com/{config.org}/{repo}.git"
    rc, _ = await _run("git", "clone", "--depth", "50", url, str(target), log_path=log_path)
    if rc == 0:
        clean_url = f"https://github.com/{config.org}/{repo}.git"
        await _run("git", "remote", "set-url", "origin", clean_url, cwd=target)
        await _run("chown", "-R", "1000:1000", str(target))
    return rc == 0


async def create_branch(repodir: Path, branch: str) -> bool:
    rc, _ = await _run("git", "checkout", "-B", branch, cwd=repodir)
    return rc == 0


async def check_gate(repodir: Path) -> tuple[str, str]:
    rc, _ = await _run("git", "diff", "--quiet", cwd=repodir)
    rc2, _ = await _run("git", "diff", "--cached", "--quiet", cwd=repodir)
    _, untracked = await _run("git", "ls-files", "--others", "--exclude-standard", cwd=repodir)
    has_changes = rc != 0 or rc2 != 0 or bool(untracked.strip())
    if not has_changes:
        return "no-change", "No files modified"
    return "passed", ""


async def commit_and_push(
    repodir: Path,
    issue: int,
    branch: str,
    config: Config,
    log_path: Path | None = None,
    force: bool = False,
) -> bool:
    await _run("git", "add", "-A", cwd=repodir)
    await _run(
        "git",
        "-c", "user.email=devrunner@crunchtools.com",
        "-c", "user.name=Kagetora Dev Runner",
        "commit", "-q", "-m",
        f"fix: address issue #{issue} (Ashigaru autonomous run)",
        "--allow-empty-message",
        cwd=repodir,
    )
    push_url = f"https://x-access-token:{config.gh_token}@github.com/{config.org}/{repodir.name}.git"
    push_args = ["git", "push", "-u"]
    if force:
        push_args.append("--force-with-lease")
    push_args.extend([push_url, branch])
    rc, _ = await _run(*push_args, cwd=repodir, log_path=log_path)
    return rc == 0


async def get_default_branch(repo: str, config: Config) -> str:
    env = {"GH_TOKEN": config.gh_token}
    rc, out = await _run(
        "gh", "repo", "view", f"{config.org}/{repo}",
        "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name",
        env=env,
    )
    return out.strip() if rc == 0 and out.strip() else "main"


async def create_pr(
    repo: str,
    branch: str,
    base: str,
    issue: int,
    run_id: str,
    model: str,
    tier: int,
    config: Config,
    log_path: Path | None = None,
) -> str | None:
    env = {"GH_TOKEN": config.gh_token}
    body = (
        f"Fixes #{issue}.\n\n"
        f"Autonomous **Ashigaru** run — Claude Code (`{model}`, tier {tier}) "
        f"in a sealed, unprivileged sandbox. Worked from an airlock-filtered brief. "
        f"**Draft, pending CI + human review.** run_id: `{run_id}`"
    )
    rc, out = await _run(
        "gh", "pr", "create", "--draft",
        "-R", f"{config.org}/{repo}",
        "--base", base, "--head", branch,
        "--title", f"fix: issue #{issue} (Ashigaru)",
        "--body", body,
        env=env,
        log_path=log_path,
    )
    if rc == 0 and out.strip():
        return out.strip().splitlines()[-1]
    return None


async def merge_pr(repo: str, pr: int, config: Config) -> tuple[bool, str]:
    env = {"GH_TOKEN": config.gh_token}
    await _run("gh", "pr", "ready", str(pr), "-R", f"{config.org}/{repo}", env=env)
    rc, out = await _run(
        "gh", "pr", "merge", str(pr),
        "-R", f"{config.org}/{repo}",
        "--squash", "--delete-branch",
        env=env,
    )
    return rc == 0, out[-500:]


async def delete_remote_branch(repo: str, branch: str, config: Config) -> bool:
    env = {"GH_TOKEN": config.gh_token}
    rc, _ = await _run(
        "gh", "api", "-X", "DELETE",
        f"repos/{config.org}/{repo}/git/refs/heads/{branch}",
        env=env,
    )
    return rc == 0


async def get_prior_diff(repodir: Path, max_lines: int = 200) -> str:
    _, out = await _run("git", "diff", "HEAD~1..HEAD", cwd=repodir)
    lines = out.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + "\n[...truncated...]"
    return out
