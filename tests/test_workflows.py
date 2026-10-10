"""The workflow files hold the limits and the authority split the profile requires."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
VERSION = (ROOT / "VERSION").read_text().strip()
PRODUCT = ("triage.yml", "code.yml", "fix.yml", "sweep.yml")
REUSABLE = PRODUCT[:3]
BASE_ACTION = "anthropics/claude-code-action/base-action@"
WRITE = {"write"}


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def jobs(name):
    return load(name)["jobs"].items()


def steps_of(job):
    return job.get("steps", [])


def runs_llm(job):
    return any(str(step.get("uses", "")).startswith(BASE_ACTION) for step in steps_of(job))


@pytest.mark.parametrize("name", PRODUCT)
def test_workflows_start_from_no_permissions(name):
    assert load(name)["permissions"] == {}


@pytest.mark.parametrize("name", PRODUCT)
def test_the_switch_gates_the_first_job_and_every_job_has_a_timeout(name):
    gated = False
    for _, job in jobs(name):
        assert isinstance(job.get("timeout-minutes"), int)
        if "needs" not in job:
            assert "vars.ASHIGARU_ENABLED == 'true'" in job["if"]
            gated = True
    assert gated


@pytest.mark.parametrize("name", REUSABLE)
def test_a_job_that_runs_an_llm_holds_no_write_credential(name):
    seen = False
    for job_name, job in jobs(name):
        if not runs_llm(job):
            continue
        seen = True
        assert not set(job["permissions"].values()) & WRITE, job_name
        for step in steps_of(job):
            assert "create-github-app-token" not in str(step.get("uses", "")), job_name
            assert "ASHIGARU_APP" not in str(step), job_name
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, job_name
    assert seen


@pytest.mark.parametrize("name", REUSABLE)
def test_every_agent_step_has_a_turn_limit_and_a_tool_list(name):
    for _, job in jobs(name):
        for step in steps_of(job):
            if str(step.get("uses", "")).startswith(BASE_ACTION):
                args = step["with"]["claude_args"]
                assert "--max-turns" in args and "--allowedTools" in args


def test_the_triage_agent_cannot_edit():
    args = [
        step["with"]["claude_args"]
        for _, job in jobs("triage.yml")
        for step in steps_of(job)
        if str(step.get("uses", "")).startswith(BASE_ACTION)
    ]
    assert args and all('--allowedTools "Read,Glob,Grep"' in value for value in args)


@pytest.mark.parametrize("name", REUSABLE)
def test_reusable_workflows_fetch_their_own_release(name):
    """A called workflow cannot name its own ref, so the release is a literal."""
    fetches = [
        step["with"]["ref"]
        for _, job in jobs(name)
        for step in steps_of(job)
        if step.get("with", {}).get("repository") == "crunchtools/ashigaru"
    ]
    assert fetches and set(fetches) == {f"v{VERSION}"}


def test_the_caller_pins_this_release_and_matches_the_example():
    example = (ROOT / "examples" / "ashigaru.yml").read_text()
    assert (WORKFLOWS / "ashigaru.yml").read_text() == example
    pins = {name: job["uses"] for name, job in yaml.safe_load(example)["jobs"].items()}
    assert pins == {
        name: f"crunchtools/ashigaru/.github/workflows/{name}.yml@v{VERSION}"
        for name in ("triage", "code", "fix")
    }


def test_the_fix_job_ignores_runs_that_a_reply_started():
    condition = load("fix.yml")["jobs"]["agent"]["if"]
    assert "workflow_run.event == 'pull_request_target'" in condition
    assert "startsWith(github.event.workflow_run.head_branch, 'ashigaru/')" in condition
    assert "head_repository.full_name == github.repository" in condition


def test_changelog_has_this_version():
    assert f"## [{VERSION}]" in (ROOT / "CHANGELOG.md").read_text()
