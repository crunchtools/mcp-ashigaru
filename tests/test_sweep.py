"""The sweep's per-run limit is the nightly budget (profile IV)."""

from sweep import MAX_LIMIT, candidates, select


def issue(number, created, author="maintainer", labels=(), pr=False):
    item = {"number": number, "created_at": created, "user": {"login": author},
            "labels": [{"name": name} for name in labels]}  # fmt: skip
    if pr:
        item["pull_request"] = {}
    return item


def writes(repo, login):
    return login == "maintainer"


def test_candidates_skip_touched_issues_and_pull_requests():
    issues = [
        issue(1, "2026-01-01"),
        issue(2, "2026-01-02", labels=("triaged",)),
        issue(4, "2026-01-04", pr=True),
        issue(5, "2026-01-05", labels=("enhancement",)),
    ]
    assert [item["number"] for item in candidates("o/r", issues)] == [1, 5]


def test_select_takes_the_oldest_across_repos_up_to_the_limit():
    found = candidates("o/a", [issue(1, "2026-03-01"), issue(2, "2026-01-01")])
    found += candidates("o/b", [issue(9, "2026-02-01")])
    chosen = select(found, 2, writes)
    assert [(item["repo"], item["number"]) for item in chosen] == [("o/a", 2), ("o/b", 9)]


def test_authors_without_write_access_are_skipped_without_using_the_budget():
    found = candidates("o/a", [issue(1, "2026-01-01", author="stranger"), issue(2, "2026-02-01")])
    assert [item["number"] for item in select(found, 1, writes)] == [2]


def test_limit_zero_queues_nothing():
    assert select(candidates("o/a", [issue(1, "2026-03-01")]), 0, writes) == []


def test_no_run_queues_more_than_the_hard_maximum():
    found = candidates("o/a", [issue(n, f"2026-01-{n:02d}") for n in range(1, 29)])
    assert len(select(found, 500, writes)) == MAX_LIMIT
