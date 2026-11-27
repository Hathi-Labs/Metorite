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
from acb_auth import agent_tokens, deps, sandbox_capabilities
from fastapi.testclient import TestClient

from tests.unit import _sandbox_door_kit as kit

HASH = agent_tokens.hash_token(kit.RAW)
QUERY_VALUE = "q7secretvalue"


@pytest.fixture(autouse=True)
def _fresh_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each module of the door a fresh logger proxy.

    configure_logging sets cache_logger_on_first_use. A module `_log` that
    an earlier test of the batch used keeps the processor list of THAT
    configure call, and capture_logs edits the CURRENT list. So in a full
    batch, the line of a cached `_log` never reaches capture_logs, and a
    shard that runs this file early passes. The fix of
    test_permission_policy.py, for each door module. Mutation: drop this
    fixture, and the full batch fails at
    test_a_failed_lookup_logs_the_class_of_the_error_only again.
    """
    monkeypatch.setattr(deps, "_log", structlog.get_logger("acb_auth.deps"))
    monkeypatch.setattr(
        sandbox_capabilities, "_log", structlog.get_logger("acb_auth.sandbox"),
    )
    from gateway import sandbox_batch

    monkeypatch.setattr(sandbox_batch, "_log", structlog.get_logger("gateway.sandbox_batch"))


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


def test_a_refusal_at_step_3_logs_at_info_and_step_4_at_warning(monkeypatch):
    """Review note 1: a stranger causes a refusal at step 3 (an unknown,
    expired or revoked token), so it logs at info. Step 4 and later log at
    warning, because only a holder of a live capability reaches them."""
    door = kit.make_app(monkeypatch)
    kit.arm_door(monkeypatch, flag=kit.ORG_B)
    with structlog.testing.capture_logs() as logs, TestClient(door.app) as client:
        unknown = client.get("/crm/leads",
                             headers={"X-Sandbox-Capability": "msbx_" + "B" * 43})
        refused = client.get("/crm/leads", headers=kit.headers())
    assert unknown.status_code == 401 and refused.status_code == 401
    first, second = _door_lines(logs)
    assert first["code"] == "capability" and first["log_level"] == "info"
    assert second["code"] == "org" and second["log_level"] == "warning"
    _clean(logs)


# ── sandbox.door.write (SBX-3a, §12.5 decision 9, done-when 16) ─────────────

_BODY_VALUE = "q7bodysecretvalue"
_TASK_ID = "7f3c0000-0000-4000-8000-00000000abcd"


def test_the_write_line_holds_no_body(monkeypatch):
    """Done-when 16. The line ``sandbox.door.write`` holds no body, no path
    value, no token and no hash: the org, the run, the phase, the held id,
    the count of rows by class, the event and the count of rows that ran by
    status. Every other line of the batch holds none of them either."""
    from tests.unit import test_sandbox_door_writes as writes

    door = writes._door(monkeypatch, None)
    rows = [writes._row("PATCH", f"/projects/tasks/{_TASK_ID}", {"title": _BODY_VALUE}),
            writes._row("POST", "/crm/leads", {"last_name": _BODY_VALUE},
                        query={"source": _BODY_VALUE}),
            writes._row("POST", f"/crm/leads/{_TASK_ID}/convert", {"note": _BODY_VALUE})]
    tickets: list[str] = []
    real_issue = sandbox_capabilities.issue_ticket

    async def spy(**kwargs):
        ticket = await real_issue(**kwargs)
        tickets.append(ticket)
        return ticket

    monkeypatch.setattr(sandbox_capabilities, "issue_ticket", spy)

    async def act(task):
        held = await writes._wait_held(door.redis, 2, task)
        for card_id, record in held.items():
            ticked = [r["id"] for r in record["rows"]] if record["class"] == "B" else None
            await writes._decide(card_id, ticked)

    with structlog.testing.capture_logs() as logs:
        answer = asyncio.run(writes._batch(door, rows, act))
    assert answer.status_code == 200, answer.text
    assert len(tickets) == 2
    lines = [line for line in logs if line.get("event") == "sandbox.door.write"]
    assert [line["act"] for line in lines] == ["held", "approved", "refused"]
    held, *_ = lines
    assert held["rows_b"] == 2 and held["rows_c"] == 1 and held["phase"] == "run"
    assert held["org"] == kit.ORG_A and held["run"] == "run-1"
    assert set(held) <= {"event", "log_level", "act", "org", "run", "phase", "held",
                         "rows_b", "rows_c", "ran"}
    assert lines[1]["ran"] == {"200": 1, "201": 1}
    _clean(logs, _BODY_VALUE, _TASK_ID, *tickets)
    for line in lines:
        assert "/crm/" not in repr(line) and "/projects/" not in repr(line)
