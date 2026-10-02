"""Offline tests for grokbot ephemeral create→delete policy."""

from __future__ import annotations

import agent.grokbot.client as c


def test_want_ephemeral_defaults_off(monkeypatch):
    monkeypatch.delenv("GROKBOT_EPHEMERAL_AGENT", raising=False)
    monkeypatch.delenv("GROKBOT_AGENT_ID", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_ONESHOT", raising=False)
    monkeypatch.delenv("HERMES_PLATFORM", raising=False)
    assert c.want_ephemeral() is False
    assert c.want_ephemeral(True) is True
    assert c.want_ephemeral(False) is False


def test_want_ephemeral_kanban_and_flag(monkeypatch):
    monkeypatch.delenv("GROKBOT_AGENT_ID", raising=False)
    monkeypatch.delenv("GROKBOT_EPHEMERAL_AGENT", raising=False)
    monkeypatch.delenv("HERMES_ONESHOT", raising=False)
    monkeypatch.delenv("HERMES_PLATFORM", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-123")
    assert c.want_ephemeral() is True
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("GROKBOT_EPHEMERAL_AGENT", "1")
    assert c.want_ephemeral() is True
    monkeypatch.setenv("GROKBOT_EPHEMERAL_AGENT", "0")
    assert c.want_ephemeral() is False


def test_pinned_agent_disables_ephemeral(monkeypatch):
    monkeypatch.setenv("GROKBOT_AGENT_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.setenv("GROKBOT_EPHEMERAL_AGENT", "1")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-123")
    assert c.want_ephemeral() is False


def test_delete_refuses_main_agent(monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("must not call delete RPC for main agent")

    monkeypatch.setattr(c, "_json_rpc", boom)
    c.delete_agent(c._MAIN_AGENT_UUID, token="x")
    assert called == []
