"""TOF-2c (task 3544): the chat capture sites C1 to C4, and one capture list.

Spec: ``project-docs/specs/tool_failure_log.md`` §4.1, §4.2 (C1 to C4, the C1
skip rule, the status errors to convert, the surfaces), §6 and §9 P6.

What this file proves, with no database (the drain is off, and each test
reads the queue of ``acb_common.failure_log``):

* Each site writes ONE record for ONE failure, through the one writer
  ``record_tool_failure``.
* A refusal of §4.1 writes none. A gateway 5xx seen in the chat writes none.
  A 401 from an outside host writes none. A 401 from the gateway writes
  ``auth_401``. A connect error to an outside host writes
  ``outside_unreachable``. A pack tool that catches a gateway 500 and raises
  ``KeyError`` with no ``from`` writes none.
* The capture never changes what a tool answers.
* The record takes the run's channel as its surface, and the executor binds
  the ``channel`` key in each run context.
* P6: only the listed sites call ``record_tool_failure``, and no module but
  ``failure_log.py`` names a ``failure_*`` SQL name.
* The two pack transports raise a ``GatewayRefusal`` that keeps its status.

Mutations, each red:

* drop the record in the ``except`` branch of ``guard``   -> test_c1_a_plain_fault_writes_one_exception
* drop the record in the ``take_bad`` branch              -> test_c2_a_bad_shape_writes_one_bad_arguments
* drop the skip of a §4.1 status                          -> test_c1_a_refusal_of_4_1_writes_none
* record a gateway 5xx in the chat                        -> test_c1_a_gateway_5xx_writes_none
* read a status of any host as the gateway's              -> test_c1_a_401_from_an_outside_host_writes_none
* drop the walk of ``__context__``                        -> test_c1_a_key_error_raised_in_a_handler_of_a_gateway_500_writes_none
* give every transport error ``gateway_unreachable``      -> test_c1_a_connect_error_names_its_host
* record each request of a run again (C3)                 -> test_c3_one_record_for_each_call_of_the_run
* record a result of the chat history (C3)                -> test_c3_a_result_of_the_history_writes_none
* record each refusal in ``refusals_as_text`` (C4)        -> test_c4_only_a_gateway_401_writes
* drop the ``channel`` key of one bind in the executor    -> test_the_executor_binds_the_channel_in_each_run_context
* a new caller of ``record_tool_failure``                 -> test_p6_only_the_listed_sites_call_the_writer
* a module that names a ``failure_*`` SQL name            -> test_p6_no_module_but_the_writer_names_a_failure_table
* ``_GatewayError`` or ``_MirrorBlocked`` as a plain class -> test_each_pack_transport_raises_a_gateway_refusal

Run: ``uv run pytest tests/unit/test_failure_capture_sites.py -q``.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import bind_run_context, get_settings, run_context_scope
from acb_common import failure_log as fl
from acb_common import tool_refusals as tr
from acb_common.db import bind_tenant, release_tenant
from acb_common.tool_refusals import GatewayRefusal, RefusalVoice, refusals_as_text
from acb_skills import pack_tool_guard as ptg
from acb_skills import unknown_tool as ut
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

REPO = Path(__file__).resolve().parents[2]
ORG = "0e7a4b5c-1d2e-4f30-8a41-5b6c7d8e9f21"
GATEWAY = "http://gw.metorite.internal:8080"
OUTSIDE = "https://www.googleapis.com"
#: A real built-in tool name, so the platform field keeps it.
TOOL = "crm_get_record"
VOICE = RefusalVoice(app="CRM", log_event="crm.tool_refused", not_found_next="Read again.")
_READ = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}


@pytest.fixture(autouse=True)
def flag_on(monkeypatch) -> Iterator[None]:
    """The flag on for ORG, a known gateway host, and no drain: each test
    reads the queue."""
    fl._reset_for_tests()
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", ORG)
    monkeypatch.setattr(fl, "_ensure_drain", lambda: None)
    monkeypatch.setenv("GATEWAY_URL", GATEWAY)
    yield
    fl._reset_for_tests()


@contextlib.contextmanager
def run(channel: str | None = "chat", *, org: str | None = ORG) -> Iterator[None]:
    """A bound tenant, a run context and an artifact context with *channel*."""
    token = bind_tenant(org) if org else None
    try:
        with run_context_scope(), artifact_context_scope():
            bind_run_context(run_id="run-1", thread_id="thread-1",
                             user="kiran@acme.test", member_verified=True)
            bind_artifact_context(run_id="run-1", session_id="thread-1",
                                  **({"channel": channel} if channel else {}))
            yield
    finally:
        release_tenant(token)


def records() -> list[fl.Record]:
    return fl._peek()


def codes() -> list[str]:
    return [r.error_code for r in records()]


def _status_error(base: str, status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"{base}/some/path")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("refused", request=request, response=response)


def _connect_error(base: str) -> httpx.ConnectError:
    return httpx.ConnectError("refused", request=httpx.Request("GET", f"{base}/x"))


def _raising(exc_factory: Any, *, read_only: bool = True) -> Any:
    async def crm_get_record() -> str:
        raise exc_factory()

    if read_only:
        crm_get_record.__tool_risk__ = _READ  # type: ignore[attr-defined]
    return crm_get_record


async def _guarded(fn: Any, name: str = TOOL) -> Any:
    return await ptg.guard(fn, name=name)()


# ── C1: the except branch of the guard ───────────────────────────────────────


async def test_c1_a_plain_fault_writes_one_exception() -> None:
    with run("whatsapp"):
        answer = await _guarded(_raising(lambda: KeyError("Amount for Ravi Kumar")))
    assert answer.startswith(tr.FAILED)
    [rec] = records()
    assert (rec.error_code, rec.tool, rec.source, rec.surface) == (
        "exception", TOOL, "chat_tool", "whatsapp")
    assert rec.exc_type == "builtins.KeyError"
    # A test file is in none of the four trees of §3.2 rule 4, so the deepest
    # frame of our code is the guard. A real pack tool names its own frame.
    assert rec.code_site == "acb_skills.pack_tool_guard:guarded"
    assert (rec.run_id, rec.session_id, rec.member_email) == (
        "run-1", "thread-1", "kiran@acme.test")
    assert "Ravi" not in repr(rec)


async def test_c1_the_capture_never_changes_the_answer(monkeypatch) -> None:
    fn = _raising(lambda: KeyError("x"))
    with run():
        on = await _guarded(fn)
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", "")
    with run():
        off = await _guarded(fn)
    assert on == off == ptg.failure_text(KeyError("x"), read_only=True)


async def test_c1_a_failed_writer_never_reaches_the_tool(monkeypatch) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the writer broke")

    monkeypatch.setattr(fl, "record_tool_failure", broken)
    monkeypatch.setattr(fl, "run_fields", broken)
    with run():
        answer = await _guarded(_raising(lambda: KeyError("x")))
    assert answer == ptg.failure_text(KeyError("x"), read_only=True)


@pytest.mark.parametrize("status", [400, 403, 404, 409, 412, 422, 429, 503])
async def test_c1_a_refusal_of_4_1_writes_none(status: int) -> None:
    with run():
        await _guarded(_raising(lambda: GatewayRefusal("no", status=status)))
        await _guarded(_raising(lambda: _status_error(GATEWAY, status)))
    assert records() == []


async def test_c1_a_refusal_of_the_client_writes_none() -> None:
    """A ``GatewayRefusal`` with no status is text that our code wrote: an
    answer, not a failure."""
    with run():
        await _guarded(_raising(lambda: GatewayRefusal("No such deal id.")))
    assert records() == []


@pytest.mark.parametrize("status", [500, 502, 504])
async def test_c1_a_gateway_5xx_writes_none(status: int) -> None:
    """D1 or D2 writes a gateway 5xx at the gateway, never in the chat."""
    with run():
        await _guarded(_raising(lambda: GatewayRefusal("boom", status=status)))
        await _guarded(_raising(lambda: _status_error(GATEWAY, status)))
    assert records() == []


async def test_c1_a_401_from_the_gateway_writes_auth_401() -> None:
    with run():
        await _guarded(_raising(lambda: GatewayRefusal("no", status=401)))
        await _guarded(_raising(lambda: _status_error(GATEWAY, 401)))
    assert codes() == ["auth_401", "auth_401"]
    assert {(r.exc_type, r.code_site) for r in records()} == {("-", "-")}


@pytest.mark.parametrize("status", [401, 404, 500])
async def test_c1_a_401_from_an_outside_host_writes_none(status: int) -> None:
    """Any status of an outside host is that host's answer."""
    with run():
        await _guarded(_raising(lambda: _status_error(OUTSIDE, status)))
    assert records() == []


async def test_c1_a_connect_error_names_its_host() -> None:
    with run():
        await _guarded(_raising(lambda: _connect_error(OUTSIDE)))
        await _guarded(_raising(lambda: _connect_error(GATEWAY)))
    assert codes() == ["outside_unreachable", "gateway_unreachable"]
    assert {(r.exc_type, r.code_site) for r in records()} == {("-", "-")}


async def test_c1_a_key_error_raised_in_a_handler_of_a_gateway_500_writes_none() -> None:
    """The skip rule reads the contexts too. A pack tool that catches a
    gateway 500 and raises ``KeyError`` with no ``from`` writes none."""

    async def crm_get_record() -> str:
        try:
            raise _status_error(GATEWAY, 500)
        except httpx.HTTPStatusError:
            raise KeyError("amount")  # noqa: B904 - the case under test

    async def from_refusal() -> str:
        try:
            raise GatewayRefusal("boom", status=500)
        except GatewayRefusal as exc:
            raise KeyError("amount") from exc

    with run():
        await _guarded(crm_get_record)
        await _guarded(from_refusal)
    assert records() == []


async def test_c1_a_group_is_read() -> None:
    def grouped() -> BaseException:
        return ExceptionGroup("g", [_connect_error(OUTSIDE)])

    with run():
        await _guarded(_raising(grouped))
    assert codes() == ["outside_unreachable"]


async def test_c1_a_placeholder_tool_keeps_the_raw_name_in_the_tenant_column() -> None:
    with run():
        await _guarded(_raising(lambda: KeyError("x")), name="acme_secret_tool")
    [rec] = records()
    assert (rec.tool, rec.tool_name) == ("other", "acme_secret_tool")


async def test_c1_no_tenant_writes_none() -> None:
    with run(org=None):
        await _guarded(_raising(lambda: KeyError("x")))
    assert records() == []


async def test_c1_a_stopped_run_still_stops_and_writes_none() -> None:
    import asyncio

    with run(), pytest.raises(asyncio.CancelledError):
        await _guarded(_raising(asyncio.CancelledError))
    assert records() == []


# ── C2: the take_bad branch of the guard ─────────────────────────────────────


async def test_c2_a_bad_shape_writes_one_bad_arguments() -> None:
    ran: list[bool] = []

    async def create_tasks(tasks: str) -> str:
        ran.append(True)
        return "made"

    guarded = ptg.guard(create_tasks, name="create_tasks")
    with run("projects_assistant"):
        ptg._note_bad("create_tasks", "tasks", "array")
        answer = await guarded(tasks="")
    assert answer == ptg.bad_shape_text("tasks", "array")
    assert ran == []
    [rec] = records()
    assert (rec.error_code, rec.tool, rec.surface, rec.exc_type, rec.code_site) == (
        "bad_arguments", "create_tasks", "projects_assistant", "-", "-")


# ── C3: UnknownToolAnswer._rewrite ───────────────────────────────────────────


def _raw(call_id: str, name: str = "create_task") -> Any:
    text = f'Error: Requested function "{name}" not found.'
    return SimpleNamespace(type="function_result", call_id=call_id, result=text,
                           items=[SimpleNamespace(text=text)])


def _request(*results: Any) -> Any:
    message = SimpleNamespace(contents=list(results))
    return SimpleNamespace(options={"tools": []}, messages=[message])


async def test_c3_one_record_for_each_call_of_the_run() -> None:
    answer = ut.UnknownToolAnswer(loader=None)
    with run():
        await answer._rewrite(_request())
        fresh = _raw("call-2")
        await answer._rewrite(_request(fresh))
        assert codes() == ["unknown_tool"]
        # The next request holds the same call again, in a copy that a
        # middleware made before the rewrite.
        await answer._rewrite(_request(_raw("call-2")))
        await answer._rewrite(_request(copy.copy(fresh)))
    assert codes() == ["unknown_tool"]
    [rec] = records()
    assert (rec.tool, rec.exc_type, rec.code_site) == ("create_task", "-", "-")


async def test_c3_a_result_of_the_history_writes_none() -> None:
    answer = ut.UnknownToolAnswer(loader=None)
    with run():
        await answer._rewrite(_request(_raw("call-old")))
        await answer._rewrite(_request(_raw("call-old")))
    assert records() == []


async def test_c3_the_model_name_is_tenant_text() -> None:
    answer = ut.UnknownToolAnswer(loader=None)
    with run():
        await answer._rewrite(_request())
        await answer._rewrite(_request(_raw("call-9", name="ravi_kumar_tool")))
    [rec] = records()
    assert (rec.tool, rec.tool_name) == ("other", "ravi_kumar_tool")


# ── C4: refusals_as_text ─────────────────────────────────────────────────────


async def test_c4_only_a_gateway_401_writes() -> None:
    texts = []
    for status in (401, 403, 404, 409, 422, 500, 503, None):
        async def crm_get_record(status: int | None = status) -> str:
            raise GatewayRefusal("CRM GET /crm/deals/9 failed", status=status)

        with run():
            texts.append(await refusals_as_text(crm_get_record, VOICE)())
    assert codes() == ["auth_401"]
    [rec] = records()
    assert (rec.tool, rec.exc_type, rec.code_site) == (TOOL, "-", "-")
    assert all(t.startswith(tr.REFUSED) for t in texts)


async def test_c4_the_capture_never_changes_the_answer(monkeypatch) -> None:
    async def crm_get_record() -> str:
        raise GatewayRefusal("no", status=401)

    with run():
        on = await refusals_as_text(crm_get_record, VOICE)()
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", "")
    with run():
        off = await refusals_as_text(crm_get_record, VOICE)()
    assert on == off == tr.refusal_text(GatewayRefusal("no", status=401), VOICE)


async def test_c4_and_c1_write_one_record_for_one_401() -> None:
    """The guard sits outside ``refusals_as_text``. A 401 that C4 answers
    reaches the guard as text, so C1 writes nothing more."""

    async def crm_get_record() -> str:
        raise GatewayRefusal("no", status=401)

    guarded = ptg.guard(refusals_as_text(crm_get_record, VOICE), name=TOOL)
    with run():
        await guarded()
    assert codes() == ["auth_401"]


# ── The channel ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("parent", "source", "agent", "batch", "want"), [
    (None, "chat", "orchestrator", False, "chat"),
    (None, "", "orchestrator", False, "chat"),
    (None, "whatsapp", "whatsapp-assistant", True, "whatsapp"),
    (None, "chat", "projects-assistant", False, "projects_assistant"),
    (None, "workflow", "crm-assistant", True, "automation"),
    (None, "projects.agent_dispatch", "projects-assistant", True, "automation"),
    (None, "", "crm-assistant", True, "automation"),
    (None, "something-new", "orchestrator", False, "other"),
    ("whatsapp", "chat", "projects-assistant", True, "whatsapp"),
    ("chat", "", "projects-assistant", True, "chat"),
    ("not-a-surface", "chat", "orchestrator", False, "chat"),
])
def test_run_channel_maps_to_the_closed_list(parent, source, agent, batch, want) -> None:
    """A delegated run keeps the channel of the run that asked."""
    assert fl.run_channel(parent, source, agent=agent, batch=batch) == want


async def test_a_run_with_no_channel_writes_other() -> None:
    with run(channel=None):
        await _guarded(_raising(lambda: KeyError("x")))
    assert [r.surface for r in records()] == ["other"]


def test_an_unverified_member_is_not_recorded() -> None:
    token = bind_tenant(ORG)
    try:
        with run_context_scope():
            bind_run_context(run_id="r", user="claimed@acme.test", member_verified=False)
            assert fl.run_fields()["member_email"] is None
    finally:
        release_tenant(token)


def test_the_executor_binds_the_channel_in_each_run_context() -> None:
    path = REPO / "apps/services/orchestrator/orchestrator/executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    binds = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) == "bind_artifact_context"]
    assert len(binds) == 2, "the executor has a new run context: bind its channel too"
    for call in binds:
        assert "channel" in {k.arg for k in call.keywords}, f"line {call.lineno}"


# ── The pack transports ──────────────────────────────────────────────────────


def _crm_agent() -> Any:
    from tests.unit._crm_agent_fakes import load_agent_module

    return load_agent_module("crm_assistant_agent_capture")


def test_each_pack_transport_raises_a_gateway_refusal() -> None:
    """§4.2: a pack transport raises a gateway status as a ``GatewayRefusal``
    that keeps its status, so the C1 skip rule reads it."""
    from acb_skills import crm_tools
    from skill_my_tasks import core as my_tasks

    crm = _crm_agent()
    transports = {
        "skill_my_tasks._GatewayError": my_tasks._GatewayError("Tasks GET /x failed (412)", 412),
        "agent-crm._MirrorBlocked": crm._MirrorBlocked("Zoho owns this."),
        "crm_tools._GatewayError": crm_tools._GatewayError("CRM GET /x", status=404),
    }
    for name, exc in transports.items():
        assert isinstance(exc, GatewayRefusal), name
    assert transports["skill_my_tasks._GatewayError"].status == 412
    assert transports["agent-crm._MirrorBlocked"].status == 409
    # The words that a caller reads stay as they were.
    assert str(transports["skill_my_tasks._GatewayError"]) == "Tasks GET /x failed (412)"
    assert str(transports["agent-crm._MirrorBlocked"]) == "Zoho owns this."
    assert type(transports["skill_my_tasks._GatewayError"]).__name__ == "_GatewayError"


async def test_a_status_of_a_pack_transport_skips_c1() -> None:
    from skill_my_tasks import core as my_tasks

    crm = _crm_agent()
    with run():
        await _guarded(_raising(lambda: my_tasks._GatewayError("Tasks PUT /x (412)", 412)))
        await _guarded(_raising(lambda: crm._MirrorBlocked("Zoho owns this.")))
        await _guarded(_raising(lambda: my_tasks._GatewayError("Tasks GET /x (401)", 401)))
    assert codes() == ["auth_401"]


def _pack_trees() -> Iterator[Path]:
    for tree in ("apps/skills", "apps/agents", "packages/acb_skills"):
        for path in sorted((REPO / tree).rglob("*.py")):
            if "node_modules" in path.parts or "tests" in path.parts:
                continue
            yield path


def test_no_pack_transport_holds_a_status_in_a_plain_class() -> None:
    """A class of a pack tree that keeps ``self.status`` must be a
    ``GatewayRefusal``. Mutation: a new plain status class -> red."""
    found = []
    for path in _pack_trees():
        text = path.read_text(encoding="utf-8")
        if "self.status" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.ClassDef):
                continue
            sets = any(isinstance(t, ast.Attribute) and t.attr == "status"
                       and getattr(t.value, "id", "") == "self"
                       for sub in ast.walk(node) if isinstance(sub, ast.Assign)
                       for t in sub.targets)
            bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
            if sets and "GatewayRefusal" not in bases:
                found.append(f"{path.relative_to(REPO)}:{node.name}")
    assert found == []


# ── P6: one writer, one capture list ─────────────────────────────────────────

#: The capture sites that call the one writer. TOF-2d adds the gateway door
#: and the MCP sites here, in the same change that adds the call.
CAPTURE_SITES = {
    "packages/acb_skills/acb_skills/pack_tool_guard.py",
    "packages/acb_skills/acb_skills/unknown_tool.py",
    "packages/acb_common/acb_common/tool_refusals.py",
}
#: The SQL names of the store (§9 P6). Whole words only.
SQL_NAMES = re.compile(
    r"\b(failure_events|failure_org_days|failure_fingerprints|failure_daily|failure_config"
    r"|failure_reports|failure_tally|failure_org_band|failure_due|failure_set"
    r"|failure_prune_daily)\b")
#: The modules that may name one. TOF-2e adds the sync job.
SQL_OWNERS = {"packages/acb_common/acb_common/failure_log.py"}


def _sources(word: re.Pattern[str] | str) -> Iterator[tuple[str, ast.Module]]:
    for top in ("apps", "packages"):
        for path in (REPO / top).rglob("*.py"):
            parts = path.parts
            if any(p in parts for p in ("node_modules", ".venv", "tests", "__pycache__")):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hit = word.search(text) if isinstance(word, re.Pattern) else word in text
            if hit:
                yield path.relative_to(REPO).as_posix(), ast.parse(text)


def _docstrings(tree: ast.Module) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _names_sql(node: ast.AST, docs: set[int]) -> bool:
    """True when *node* is code or SQL text that names a store table."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return id(node) not in docs and SQL_NAMES.search(node.value) is not None
    if isinstance(node, ast.Name):
        return SQL_NAMES.fullmatch(node.id) is not None
    if isinstance(node, ast.Attribute):
        return SQL_NAMES.fullmatch(node.attr) is not None
    return False


def test_p6_only_the_listed_sites_call_the_writer() -> None:
    callers = set()
    for rel, tree in _sources("record_tool_failure"):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name == "record_tool_failure":
                    callers.add(rel)
    assert callers == CAPTURE_SITES


def test_p6_no_module_but_the_writer_names_a_failure_table() -> None:
    """Code and SQL text only: a docstring and a comment may name a table."""
    named = set()
    for rel, tree in _sources(SQL_NAMES):
        docs = _docstrings(tree)
        if any(_names_sql(node, docs) for node in ast.walk(tree)):
            named.add(rel)
    assert named <= SQL_OWNERS, sorted(named - SQL_OWNERS)
    assert named == SQL_OWNERS, "the writer names its tables: the fence reads nothing"
