"""Write access is the effective permission the API reports, and unknown means no."""

import subprocess

import pytest

import common


@pytest.fixture(autouse=True)
def fresh_cache():
    common.can_write.cache_clear()


@pytest.mark.parametrize(
    ("permission", "expected"), [("admin", True), ("write", True), ("read", False), ("none", False)]
)
def test_only_write_and_admin_count(monkeypatch, permission, expected):
    monkeypatch.setattr(common, "api", lambda path: {"permission": permission})
    assert common.can_write("o/r", "someone") is expected


def test_a_failed_lookup_is_not_write_access(monkeypatch):
    def fail(path):
        raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 404")

    monkeypatch.setattr(common, "api", fail)
    assert common.can_write("o/r", "ghost") is False


def test_lookups_are_made_once_per_account(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "api", lambda path: calls.append(path) or {"permission": "write"})
    assert common.can_write("o/r", "a") and common.can_write("o/r", "a")
    assert len(calls) == 1


def test_a_label_created_by_a_concurrent_run_is_not_an_error(monkeypatch):
    def create(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "gh", stderr="label already exists")

    monkeypatch.setattr(common, "api_pages", lambda path: [])
    monkeypatch.setattr(common, "gh", create)
    assert common.ensure_labels("o/r", [common.TRIAGED]) == {common.TRIAGED}


def test_an_unexpected_lookup_failure_is_refused_and_reported(monkeypatch, capsys):
    def fail(path):
        raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")

    monkeypatch.setattr(common, "api", fail)
    assert common.can_write("o/r", "someone") is False
    assert "permission lookup for someone on o/r failed" in capsys.readouterr().out


def test_other_label_creation_failures_propagate(monkeypatch):
    def create(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 403")

    monkeypatch.setattr(common, "api_pages", lambda path: [])
    monkeypatch.setattr(common, "gh", create)
    with pytest.raises(subprocess.CalledProcessError):
        common.ensure_labels("o/r", [common.TRIAGED])
