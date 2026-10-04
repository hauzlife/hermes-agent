"""Tests for PR lifecycle enforcement, auto git binding, and acceptance."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as kbw
from hermes_cli.kanban_pr_acceptance import collect_acceptance
from tools.kanban_tools import _handle_complete


@pytest.fixture
def kanban_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_collect_acceptance_merged_state(monkeypatch):
    """PR acceptance passes immediately if PR state is MERGED."""
    mock_pr = {
        "headRefOid": "a" * 40,
        "baseRefName": "main",
        "state": "MERGED",
        "baseRef": {"branchProtectionRule": None}
    }
    monkeypatch.setattr(
        "hermes_cli.kanban_pr_acceptance._api",
        lambda endpoint, **kw: {"data": {"repository": {"pullRequest": mock_pr}}}
    )
    receipt = collect_acceptance("https://github.com/hauzlife/social/pull/1", None)
    assert receipt["ok"] is True
    assert receipt["classification"] == "success"
    assert "merged" in receipt["detail"]


def test_collect_acceptance_no_protection_rules_clean_head(monkeypatch):
    """PR acceptance passes on open PR when no branch protection exists and check-runs/statuses are clean."""
    mock_pr = {
        "headRefOid": "b" * 40,
        "baseRefName": "main",
        "state": "OPEN",
        "baseRef": {"branchProtectionRule": None}
    }
    def mock_api(endpoint, **kw):
        if endpoint == "graphql":
            return {"data": {"repository": {"pullRequest": mock_pr}}}
        if "rules/branches" in endpoint:
            return []
        if "check-runs" in endpoint:
            return [{"total_count": 0, "check_runs": []}]
        if "statuses" in endpoint:
            return [[]]
        return {}

    monkeypatch.setattr("hermes_cli.kanban_pr_acceptance._api", mock_api)
    receipt = collect_acceptance("https://github.com/hauzlife/social/pull/2", None)
    assert receipt["ok"] is True
    assert receipt["classification"] == "success"
    assert "open and verified" in receipt["detail"]


def test_kanban_complete_blocked_without_pr_on_contract_task(kanban_env):
    """Implementers cannot complete a running task under a repository contract without published_pr."""
    with kbc.connect() as conn:
        tid = kb.create_task(
            conn,
            title="Implement feature",
            assignee="backend-engineer",
            created_by="user",
            completion_contract="hauzlife/social",
            initial_status="running",
        )
    # Attempting kanban_complete without published_pr must return tool_error
    result = _handle_complete({"task_id": tid, "summary": "Finished without PR"})
    assert "blocked: task" in result
    assert "requires a Pull Request and code review" in result


def test_auto_bind_git_contract(kanban_env, tmp_path, monkeypatch):
    """Auto-binds git contract when worktree or dir is inside a git repo with a github remote."""
    repo = tmp_path / "mock_repo"
    repo.mkdir()
    # Mock _git output for remote get-url origin
    import subprocess
    monkeypatch.setattr(
        kbw,
        "_git",
        lambda repo_root, *args, **kw: subprocess.CompletedProcess(
            ["git", *args], 0, "git@github.com:hauzlife/bloopu-backend.git\n", ""
        ),
    )
    with kbc.connect() as conn:
        tid = kb.create_task(
            conn,
            title="Work in repo",
            assignee="backend-engineer",
            created_by="user",
        )
        task = kb.get_task(conn, tid)
        assert task.completion_contract in {None, "local-only"}

    kbw._auto_bind_git_contract(tid, repo)

    with kbc.connect() as conn:
        updated = kb.get_task(conn, tid)
        assert updated.completion_contract == "hauzlife/bloopu-backend"

