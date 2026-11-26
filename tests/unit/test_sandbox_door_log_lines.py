"""SBX-F29: the log lines of the sandbox door hold no secret (SBX-2, task 3801).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.2 decision 13, done-when 13.

``sandbox.capability`` (mint, revoke, revoke of a step, sweep) and
``sandbox.door.call`` (each refusal of the sibling, each admitted call, the
size cap, an outage) hold ids and counts only. Each test captures every
line that the act writes, and searches it for the raw token, its hash, a
header value, a query value and the answer text.

It is a new file, because SBX-1 makes ``test_sandbox_log_lines.py``.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog
from acb_auth import agent_tokens, sandbox_capabilities
from fastapi.testclient import TestClient

from tests.unit import _sandbox_door_kit as kit

HASH = agent_tokens.hash_token(kit.RAW)
QUERY_VALUE = "q7secretvalue"


def _clean(logs: list[dict], *secrets: str) -> None:
    assert logs, "the act wrote no line"
    for line in logs:
        text = repr(line)
        for secret in (kit.RAW, HASH, kit.RAW[5:], *secrets):
            assert secret not in text, (line.get("event"), secret[:12])


def _door_lines(logs: list[dict]) -> list[dict]:
    return [line for line in logs if line.get("event") == "sandbox.door.call"]


# ── sandbox.capability ──────────────────────────────────────────────────────


class _FakeRun:
    """``agent_tokens._run`` with one canned answer, so no database is read."""

    def __init__(self, result) -> None:
        self.result = result

    async def __call__(self, work):
        return self.result


def test_the_mint_line_holds_ids_and_never_the_token(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(agent_tokens, "_run", _FakeRun(
        SimpleNamespace(created_at=now, expires_at=now + timedelta(minutes=15))))
    with structlog.testing.capture_logs() as logs:
        minted = asyncio.run(sandbox_capabilities.mint(
            org=kit.ORG_A, member=kit.MEMBER, run_id="run-1", thread_id="thread-1",
            phase="run", read_scopes=("crm:read",)))
    token_hash = agent_tokens.hash_token(minted.token)
    lines = [line for line in logs if line.get("event") == "sandbox.capability"]
    assert [line["event"] for line in lines] == ["sandbox.capability"]
    assert lines[0]["org"] == kit.ORG_A and lines[0]["read_scopes"] == 1
    _clean(logs, minted.token, token_hash, minted.token[5:])
    assert minted.token not in repr(minted), "the token never shows in a repr"


def test_the_revoke_lines_hold_ids_and_never_the_hash(monkeypatch):
    row = SimpleNamespace(organization_id=kit.ORG_A, run_id="run-1", thread_id="thread-1",
                          phase="run", expires_at=datetime.now(UTC), read_scopes=["crm:read"])
    with structlog.testing.capture_logs() as logs:
        monkeypatch.setattr(agent_tokens, "_run", _FakeRun(row))
        assert asyncio.run(sandbox_capabilities.revoke(HASH)) is True
        monkeypatch.setattr(agent_tokens, "_run", _FakeRun([row]))
        assert asyncio.run(sandbox_capabilities.revoke_step(
            org=kit.ORG_A, run_id="run-1", phase="run")) == 1
        monkeypatch.setattr(agent_tokens, "_run", _FakeRun([1, 1]))
        assert asyncio.run(sandbox_capabilities.sweep()) == 2
    events = [line.get("event") for line in logs]
    assert events.count("sandbox.capability") == 3
    _clean(logs)


# ── sandbox.door.call ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("path", "extra", "query", "status"), [
    ("/crm/leads", {}, "", 200),
    ("/crm/aggregate", {}, "", 404),
    ("/crm/export/all.zip", {}, "", 403),
    ("/crm/leads", {"X-User-Email": kit.OTHER_MEMBER}, "", 401),
    ("/crm/leads", {}, f"page_size={QUERY_VALUE}", 422),
    ("/crm/leads", {}, f"a={QUERY_VALUE}&a={QUERY_VALUE}", 422),
    ("/crm/leads", {}, f"q={QUERY_VALUE}", 200),
])
def test_each_door_line_holds_no_token_hash_header_or_query(
        monkeypatch, path, extra, query, status):
    door = kit.make_app(monkeypatch)
    kit.arm_door(monkeypatch)
    url = f"{path}?{query}" if query else path
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        answer = client.get(url, headers=kit.headers(**extra))
    assert answer.status_code == status, answer.text
    lines = _door_lines(logs)
    assert len(lines) == 1, lines
    assert lines[0]["status"] == status
    _clean(logs, kit.OTHER_MEMBER, QUERY_VALUE)


def test_an_admitted_line_has_the_call_count_and_the_bytes(monkeypatch):
    door = kit.make_app(monkeypatch)
    kit.arm_door(monkeypatch)
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        answer = client.get("/crm/leads", headers=kit.headers())
    [line] = _door_lines(logs)
    assert line["code"] == "allowed" and line["call_n"] == 1
    assert line["bytes"] == len(answer.content)
    assert line["org"] == kit.ORG_A and line["run"] == "run-1" and line["phase"] == "run"
    assert line["template"] == "/crm/leads" and line["method"] == "GET"
    assert kit.MEMBER not in repr(line), "the line names no member"
    _clean(logs, kit.MEMBER)


def test_a_refusal_before_the_lookup_has_no_org_and_no_run(monkeypatch):
    door = kit.make_app(monkeypatch)
    kit.arm_door(monkeypatch, flag="")
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        client.get("/crm/leads", headers=kit.headers())
    [line] = _door_lines(logs)
    assert line["status"] == 401 and line["org"] is None and line["run"] is None
    assert line["log_level"] == "info"
    _clean(logs)


def test_the_too_large_line_holds_no_answer_text(monkeypatch):
    door = kit.make_app(monkeypatch, big=True)
    kit.arm_door(monkeypatch)
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        client.get("/crm/owners", headers=kit.headers())
    [line] = _door_lines(logs)
    assert line["code"] == "sandbox_response_too_large" and line["bytes"] == 0
    _clean(logs, kit.BIG_BYTE.decode("latin-1") * 8)


def test_a_failed_lookup_logs_the_class_of_the_error_only(monkeypatch):
    """A database error can print its bound values, and one is the hash."""
    door = kit.make_app(monkeypatch)
    kit.arm_door(monkeypatch)

    async def broken(raw):
        raise ValueError(f"could not read token_sha256={HASH} for {raw}")

    monkeypatch.setattr(sandbox_capabilities, "lookup", broken)
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        answer = client.get("/crm/leads", headers=kit.headers())
    assert answer.status_code == 401
    assert any(line.get("event") == "sandbox.capability_read_failed" for line in logs)
    _clean(logs)


def test_the_outage_lines_hold_no_secret(monkeypatch):
    door = kit.make_app(monkeypatch)
    state = kit.arm_door(monkeypatch)
    state.redis.down = True
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        answer = client.get("/crm/leads", headers=kit.headers())
    assert answer.status_code == 503
    _clean(logs)
