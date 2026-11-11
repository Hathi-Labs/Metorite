"""SW-23. ``checks._own_run`` follows the claim gate: a call with no session
key owns only a run with no key, so it never closes under a session's run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.routes.projects import checks


def _user(session: str | None):
    agent = SimpleNamespace(actor_via="mcp:claude.ai:claude-code", connection_id="c1",
                            session_key=session)
    return SimpleNamespace(agent=agent, email="a@example.com")


def _run(session: str | None):
    return SimpleNamespace(member="a@example.com", client_via="mcp:claude.ai:claude-code",
                           connection_id="c1", session_key=session)


@pytest.fixture(autouse=True)
def _actor(monkeypatch):
    monkeypatch.setattr(checks, "actor", lambda user: user.email)


def test_a_call_with_no_session_does_not_own_a_session_run():
    # Mutation: restore `or mine is None` and this turns red.
    assert checks._own_run(_run("sA"), _user(None)) is False


def test_the_same_session_and_a_run_with_no_key_are_own():
    assert checks._own_run(_run("sA"), _user("sA")) is True
    assert checks._own_run(_run(None), _user(None)) is True
    assert checks._own_run(_run(None), _user("sA")) is True


def test_another_session_is_not_own():
    assert checks._own_run(_run("sB"), _user("sA")) is False
