"""Tests for the Sovereign Executive Guard and Anti-Flood in hermes_cli.kanban_db."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_sovereign_guard_blocks_root_task_in_vacuum(kanban_home):
    """Automated agent cannot create root tasks without an active Sovereign Directive."""
    with kbc.connect() as conn:
        with pytest.raises(ValueError, match=r"\[SOVEREIGN_GUARD\].*vacuum rejected"):
            kb.create_task(
                conn,
                title="Invented task in vacuum",
                assignee="backend-engineer",
                created_by="product-manager",
            )


def test_sovereign_guard_allows_human_and_test(kanban_home):
    """Human, dashboard, CLI, and test creators are unrestricted."""
    with kbc.connect() as conn:
        t1 = kb.create_task(conn, title="Human task", assignee="backend-engineer", created_by="user")
        assert t1 is not None

        t2 = kb.create_task(conn, title="Test task", assignee="backend-engineer", created_by="test_runner")
        assert t2 is not None

        t3 = kb.create_task(conn, title="Default task", assignee="backend-engineer", created_by=None)
        assert t3 is not None


def test_sovereign_guard_allows_emergency_incident(kanban_home):
    """Emergency, incident, and P0 cards are always allowed through."""
    with kbc.connect() as conn:
        t_inc = kb.create_task(
            conn,
            title="[INCIDENT-P0] Payment gateway failure",
            assignee="site-reliability-engineer",
            created_by="product-manager",
            priority=10,
        )
        assert t_inc is not None

        t_sre = kb.create_task(
            conn,
            title="NOC alert database pool exhausted",
            assignee="backend-engineer",
            created_by="site-reliability-engineer",
            priority=8,
        )
        assert t_sre is not None


def test_sovereign_guard_allows_root_task_when_directive_active(kanban_home):
    """When a Sovereign Directive is ACTIVE, automated root task creation is permitted."""
    directive_file = kanban_home / "sovereign_directive.md"
    directive_file.write_text(
        "# Sovereign Directive\n- **Status**: ACTIVE\n- **Focus**: hot-telegram\n",
        encoding="utf-8",
    )

    with kbc.connect() as conn:
        t_dir = kb.create_task(
            conn,
            title="Implement Funnel KPI calculation",
            assignee="backend-engineer",
            created_by="product-manager",
        )
        assert t_dir is not None


def test_sovereign_guard_antiflood_caps_active_tasks(kanban_home):
    """Anti-flood caps active tasks at 15 per board even with active directive."""
    directive_file = kanban_home / "sovereign_directive.md"
    directive_file.write_text(
        "# Sovereign Directive\n- **Status**: ACTIVE\n- **Focus**: hot-telegram\n",
        encoding="utf-8",
    )

    with kbc.connect() as conn:
        for i in range(15):
            kb.create_task(
                conn,
                title=f"Task {i}",
                assignee="backend-engineer",
                created_by="product-manager",
            )

        with pytest.raises(ValueError, match=r"\[SOVEREIGN_GUARD\] Anti-flood guard"):
            kb.create_task(
                conn,
                title="Task 16 flood",
                assignee="backend-engineer",
                created_by="product-manager",
            )
