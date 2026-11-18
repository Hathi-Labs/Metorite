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

TOF-2d (task 3545) adds the door sites D1 and D2, the route set of §3.2 rule
1, the MCP sites C5 to C7 and the map of §4.3. Each mutation was made by hand
on 2026-11-18, and the named test failed:

* drop the D1 record                                -> test_d1_a_route_that_raises_writes_one_unhandled_500_with_the_tenant
* drop the ``db_busy`` skip of D1                   -> test_d1_a_db_busy_503_writes_none
* drop the mark check of D2                         -> test_a_studio_live_read_that_times_out_writes_one_mcp_timeout
* the concrete path in place of the template        -> test_d1_a_route_that_raises_writes_one_unhandled_500_with_the_tenant
* every template counts, with no route set          -> test_a_route_off_the_route_set_writes_route_other
* D2 counts 501 and 503                             -> test_d2_an_answer_or_another_5xx_writes_none
* drop the C5 wrap of the gate                      -> test_c5_an_is_error_answer_writes_mcp_tool_error
* record a control signal of MAF at C5              -> test_c5_a_control_signal_of_maf_writes_none
* drop C6 in ``RunScopedMCPTool.__aenter__``        -> test_c6_a_refused_connect_writes_mcp_unreachable
* drop C6 at the connect of ``attach_mcp_tools``    -> test_c6_a_server_that_lists_too_many_tools_writes_mcp_too_large
* drop C7 in ``_list_stored``                       -> test_c7_a_list_writes_the_code_of_the_map
* drop C7 around ``shape_result``                   -> test_c7_an_is_error_answer_of_shape_result_writes_mcp_tool_error
* the refused row before the too-large row          -> test_the_mcp_map_of_4_3
* no context walk in the map, or in its deepest     -> test_the_mcp_map_of_4_3
* drop the timeout row of the map                   -> test_the_mcp_map_of_4_3
* ``record_tool_failure`` sets no request mark      -> test_a_studio_live_read_that_times_out_writes_one_mcp_timeout
* the surface reads the whole header                -> test_the_door_surface_reads_the_prefix_only

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

#: The capture sites that call the one writer. TOF-2d added the gateway door
#: (D1, D2) and the MCP sites (C5 to C7) in the change that added the call.
CAPTURE_SITES = {
    "packages/acb_skills/acb_skills/pack_tool_guard.py",
    "packages/acb_skills/acb_skills/unknown_tool.py",
    "packages/acb_common/acb_common/tool_refusals.py",
    "apps/services/gateway/gateway/main.py",
    "apps/services/orchestrator/orchestrator/_maf_mcp.py",
    "apps/services/gateway/gateway/connections/mcp_tools.py",
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
            # A rename (``import ... as rec``) would hide a caller from the name check.
            if isinstance(node, ast.ImportFrom) and any(
                    a.name == "record_tool_failure" and a.asname for a in node.names):
                callers.add(rel + " (renamed import)")
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


# ══ TOF-2d (task 3545): the door sites D1 and D2, and the MCP sites C5 to C7 ══
#
# Spec: §3.2 rule 1 (the route set), §4.2 (D1, D2, C5, C6, C7, the mark), §4.3
# (the MCP map). The door tests run a small FastAPI app with the REAL
# ``TenantScopeMiddleware`` and ``_db_unavailable`` of ``gateway.main``. Its
# app-wide dependency binds the tenant as ``_resolve_and_bind`` does.

REFUSAL = ("(EMAXCONNSESSION) max clients reached in session mode - max clients are"
           " limited to pool_size: 15")
SERVER = "acme-erp"


def _db_refusal() -> BaseException:
    import asyncpg
    import sqlalchemy.exc as sa_exc

    orig = asyncpg.exceptions.InternalServerError(REFUSAL)
    return sa_exc.DBAPIError.instance("SELECT 1", {}, orig, Exception,
                                      connection_invalidated=False)


async def _slow_lister(_server: Any) -> list[Any]:
    import asyncio

    await asyncio.sleep(5)
    return []


def _door_client(monkeypatch) -> Any:
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient
    from gateway import main as gw
    from gateway.connections import mcp_tools

    monkeypatch.setattr(mcp_tools, "READ_TIMEOUT_S", 0.05)

    async def bind_org() -> None:
        # The auth dependency binds the tenant of the session (``_resolve_and_bind``).
        bind_tenant(ORG)

    app = FastAPI(dependencies=[Depends(bind_org)])
    app.add_exception_handler(Exception, gw._db_unavailable)
    app.add_middleware(gw.TenantScopeMiddleware)

    @app.get("/boom/{item_id}")
    async def boom(item_id: str) -> None:
        raise KeyError("Amount for Ravi Kumar")

    @app.get("/sync-boom")
    def sync_boom() -> None:
        raise KeyError("x")

    @app.get("/answers/{status}")
    async def answers(status: int) -> None:
        raise HTTPException(status_code=status, detail="no")

    @app.get("/db-busy")
    async def db_busy_route() -> None:
        raise _db_refusal()

    @app.get("/stream")
    async def stream() -> Any:
        async def body() -> Any:
            for i in range(3):
                yield f"chunk-{i};"

        return StreamingResponse(body())

    @app.post("/studio-live/{server}")
    async def studio_live(server: str) -> None:
        # As ``routes/studio/invoke.py`` answers a failed live read.
        try:
            await mcp_tools._list_stored(SimpleNamespace(name=server), _slow_lister)
        except mcp_tools.ListFailed as failed:
            raise HTTPException(status_code=502,
                                detail={"code": f"mcp_{failed.reason}"}) from None

    sub = FastAPI(dependencies=[Depends(bind_org)])

    @sub.get("/deep/{x}")
    async def deep(x: str) -> None:
        raise HTTPException(status_code=500, detail="no")

    app.mount("/sub", sub)
    return TestClient(app, raise_server_exceptions=False)


def test_d1_a_route_that_raises_writes_one_unhandled_500_with_the_tenant(monkeypatch) -> None:
    """Fact 1 of §2: the tenant of the request is still bound at D1."""
    client = _door_client(monkeypatch)
    response = client.get("/boom/4242?who=Ravi", headers={"X-Actor-Via": "chat:run-9"})
    assert (response.status_code, response.text) == (500, "Internal Server Error")
    [rec] = records()
    assert (rec.org, rec.source, rec.error_code, rec.surface) == (
        ORG, "door_5xx", "unhandled_500", "chat")
    assert rec.tool == "route:GET /boom/{item_id}"
    assert rec.exc_type == "builtins.KeyError"
    assert rec.tool_name is None
    # No concrete path, no query and no message reaches a record.
    assert "4242" not in repr(rec) and "Ravi" not in repr(rec)


def test_d1_a_sync_route_keeps_the_tenant_too(monkeypatch) -> None:
    client = _door_client(monkeypatch)
    assert client.get("/sync-boom").status_code == 500
    assert [(r.org, r.error_code, r.tool) for r in records()] == [
        (ORG, "unhandled_500", "route:GET /sync-boom")]


def test_d1_a_db_busy_503_writes_none(monkeypatch) -> None:
    client = _door_client(monkeypatch)
    response = client.get("/db-busy")
    assert response.status_code == 503
    assert response.json()["code"] == "db_busy"
    assert records() == []


def test_d2_a_route_that_answers_500_writes_one_route_500(monkeypatch) -> None:
    client = _door_client(monkeypatch)
    assert client.get("/answers/500").status_code == 500
    [rec] = records()
    assert (rec.error_code, rec.tool, rec.exc_type, rec.code_site, rec.surface) == (
        "route_500", "route:GET /answers/{status}", "-", "-", "app")


@pytest.mark.parametrize("status", [502, 504])
def test_d2_a_502_or_504_writes_route_upstream(monkeypatch, status: int) -> None:
    client = _door_client(monkeypatch)
    assert client.get(f"/answers/{status}").status_code == status
    assert codes() == ["route_upstream"]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 501, 503, 505])
def test_d2_an_answer_or_another_5xx_writes_none(monkeypatch, status: int) -> None:
    client = _door_client(monkeypatch)
    assert client.get(f"/answers/{status}").status_code == status
    assert records() == []


def test_a_route_off_the_route_set_writes_route_other(monkeypatch) -> None:
    """A template that no route of ``app.routes`` holds never names a route."""
    client = _door_client(monkeypatch)
    assert client.get("/sub/deep/acme-secret").status_code == 500
    [rec] = records()
    assert rec.tool == "route:other"
    assert "acme" not in repr(rec)


@pytest.mark.parametrize(("actor", "surface"), [
    ("chat:r-1", "chat"), ("mcp:m-1", "mcp_edge"), ("workflow:w-1", "automation"),
    ("system:github", "automation"), ("browser", "app"), ("", "app"),
    ("CHAT:Ravi Kumar", "chat"), ("evil:chat:", "app"),
])
def test_the_door_surface_reads_the_prefix_only(actor: str, surface: str) -> None:
    assert fl.door_surface(actor) == surface


def test_a_streaming_answer_is_unchanged_and_writes_none(monkeypatch) -> None:
    client = _door_client(monkeypatch)
    response = client.get("/stream")
    assert (response.status_code, response.text) == (200, "chunk-0;chunk-1;chunk-2;")
    assert records() == []


def test_the_door_never_changes_the_answer(monkeypatch) -> None:
    client = _door_client(monkeypatch)
    on = [client.get(p) for p in ("/boom/1", "/answers/500", "/answers/502")]
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", "")
    off = [client.get(p) for p in ("/boom/1", "/answers/500", "/answers/502")]
    assert [(r.status_code, r.text) for r in on] == [(r.status_code, r.text) for r in off]


def test_a_failed_writer_never_reaches_the_door(monkeypatch) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the writer broke")

    monkeypatch.setattr(fl, "record_tool_failure", broken)
    client = _door_client(monkeypatch)
    assert client.get("/boom/1").status_code == 500
    assert client.get("/answers/502").status_code == 502


def test_a_studio_live_read_that_times_out_writes_one_mcp_timeout(monkeypatch) -> None:
    """C7 records, and its mark stops D2: no ``route_upstream`` for the 502."""
    client = _door_client(monkeypatch)
    response = client.post(f"/studio-live/{SERVER}")
    assert (response.status_code, response.json()["detail"]["code"]) == (502, "mcp_timeout")
    [rec] = records()
    assert (rec.source, rec.error_code, rec.surface, rec.tool, rec.tool_name) == (
        "mcp_call", "mcp_timeout", "studio", "mcp", SERVER)


def test_the_gateway_runs_the_door() -> None:
    from gateway.main import TenantScopeMiddleware, app

    assert TenantScopeMiddleware in {m.cls for m in app.user_middleware}


# ── The MCP map of §4.3 ──────────────────────────────────────────────────────


def _raised(exc: BaseException, *, cause: BaseException | None = None,
            context: BaseException | None = None) -> BaseException:
    """*exc* with a traceback, raised in this file (no tree of §3.2 rule 4)."""
    try:
        try:
            if context is not None:
                raise context
        except BaseException:
            raise exc from cause
        raise exc from cause
    except BaseException as caught:
        return caught


def _raised_in(rel: str, module: str, exc: BaseException,
               context: BaseException | None = None) -> BaseException:
    """*exc*, raised by a frame whose file is *rel* of the repository, as a
    fault of our own code there would be. With *context*, it raises inside a
    handler of *context*."""
    source = ("def our_code(exc, context):\n"
              "    try:\n"
              "        if context is not None:\n"
              "            raise context\n"
              "    except BaseException:\n"
              "        raise exc\n"
              "    raise exc\n")
    space = {"__name__": module}
    exec(compile(source, str(REPO / rel), "exec"), space)
    try:
        space["our_code"](exc, context)
    except BaseException as caught:
        return caught
    raise AssertionError("not raised")


def _mcp_error(code: int) -> BaseException:
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    return McpError(ErrorData(code=code, message="Ravi Kumar"))


def _tool_exc(*args: Any) -> BaseException:
    from agent_framework.exceptions import ToolExecutionException

    return ToolExecutionException(*args)


def _timeout_from_cancel() -> BaseException:
    import asyncio

    return _raised(TimeoutError(), cause=asyncio.CancelledError())


def _map_cases() -> list[tuple[str, Any, str]]:
    import anyio
    from gateway import outbound_guard
    from gateway.connections import mcp_tools
    from orchestrator import _maf_mcp

    our_key = _raised_in("apps/services/orchestrator/orchestrator/_maf_mcp.py",
                         "orchestrator._maf_mcp", KeyError("tools"))
    return [
        ("timeout", _timeout_from_cancel, "mcp_timeout"),
        ("mcp-408", lambda: _mcp_error(408), "mcp_timeout"),
        ("timeout-beats-refused",
         lambda: _raised(outbound_guard.OutboundRefused("h", "private"), cause=TimeoutError()),
         "mcp_timeout"),
        ("answer-too-large", lambda: outbound_guard.AnswerTooLarge("h", "too large"),
         "mcp_too_large"),
        ("list-too-long", lambda: _raised_in(
            "apps/services/orchestrator/orchestrator/_maf_mcp.py", "orchestrator._maf_mcp",
            _maf_mcp.McpListTooLong("tools")), "mcp_too_large"),
        ("refused", lambda: outbound_guard.OutboundRefused("h", "private"), "mcp_refused"),
        ("connect", lambda: _connect_error("https://acme.example"), "mcp_unreachable"),
        ("os-error", lambda: _raised(ConnectionRefusedError()), "mcp_unreachable"),
        ("closed", lambda: _raised(anyio.ClosedResourceError()), "mcp_unreachable"),
        ("mcp-closed", lambda: _mcp_error(-32000), "mcp_unreachable"),
        ("status", lambda: _status_error("https://acme.example", 500), "mcp_unreachable"),
        ("group", lambda: ExceptionGroup("g", [_connect_error("https://a.example")]),
         "mcp_unreachable"),
        ("is-error", lambda: _raised(_tool_exc("Ravi Kumar is not found")), "mcp_tool_error"),
        # The SDK wraps an outage with no ``from``: the context walk finds it.
        ("connect-in-a-context", lambda: _raised(
            _tool_exc("x"), context=_connect_error("https://acme.example")), "mcp_unreachable"),
        ("mcp-other-code", lambda: _raised(_tool_exc("x"), cause=_mcp_error(-32602)),
         "mcp_tool_error"),
        ("shape-result", lambda: mcp_tools.ListFailed(mcp_tools.REASON_TOOL_ERROR),
         "mcp_tool_error"),
        ("our-key-error", lambda: our_key, "mcp_exception"),
        ("our-key-error-in-maf", lambda: _raised(_tool_exc("failed"), cause=our_key),
         "mcp_exception"),
        ("our-class", lambda: _raised(mcp_tools.ServerNotFound("x")), "mcp_exception"),
        ("their-key-error", lambda: _raised(KeyError("x")), "mcp_unreachable"),
        # The edge that §4.3 accepts: our fault inside a handler of an error
        # that no row names sorts by the last row.
        ("our-fault-in-a-handler-of-theirs", lambda: _raised_in(
            "apps/services/orchestrator/orchestrator/_maf_mcp.py", "orchestrator._maf_mcp",
            KeyError("x"), context=_raised(ValueError())), "mcp_unreachable"),
    ]


def test_the_mcp_map_of_4_3() -> None:
    got = {name: fl.mcp_error_code(make()) for name, make, _want in _map_cases()}
    assert got == {name: want for name, _make, want in _map_cases()}


def test_the_mcp_map_never_raises(monkeypatch) -> None:
    def broken(_exc: BaseException) -> list[BaseException]:
        raise RuntimeError("the walk broke")

    monkeypatch.setattr(fl, "_mcp_chain", broken)
    assert fl.mcp_error_code(KeyError("x")) == "mcp_unreachable"


# ── C5: the gate of a chat run ───────────────────────────────────────────────


def _mcp_gate(monkeypatch) -> tuple[Any, Any]:
    from acb_skills import egress
    from agent_framework import FunctionTool
    from orchestrator import _maf_mcp

    async def clear(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(egress, "session_is_marked", clear)
    fn = FunctionTool(name="acme_erp_get_invoice", func=lambda **_k: "ran",
                      description="Reads one invoice.",
                      additional_properties={"_mcp_is_tool": True,
                                             "_mcp_remote_name": "get_invoice"})
    server = SimpleNamespace(name=SERVER, _functions=[fn], _ws8c_tool_meta={
        "get_invoice": _maf_mcp.ToolMeta(hints={"readOnlyHint": True}, description="")})
    return _maf_mcp.McpPermissionGate(tools=[server], member_present=True), fn


def _failing(exc: BaseException) -> Any:
    async def call_next() -> None:
        raise exc

    return call_next


async def _through_gate(gate: Any, fn: Any, exc: BaseException) -> BaseException:
    ctx = SimpleNamespace(function=fn, arguments={"invoice": "INV-7"}, result=None)
    with pytest.raises(BaseException) as raised:
        await gate.process(ctx, _failing(exc))
    return raised.value


async def test_c5_an_is_error_answer_writes_mcp_tool_error(monkeypatch) -> None:
    gate, fn = _mcp_gate(monkeypatch)
    exc = _tool_exc("Invoice INV-7 of Ravi Kumar is not found")
    with run("whatsapp"):
        assert await _through_gate(gate, fn, exc) is exc
    [rec] = records()
    assert (rec.source, rec.error_code, rec.tool, rec.surface, rec.run_id) == (
        "mcp_call", "mcp_tool_error", "mcp", "whatsapp", "run-1")
    assert rec.tool_name == f"{SERVER}/get_invoice"
    assert "Ravi" not in repr(rec) and "INV-7" not in repr(rec)


async def test_c5_a_timeout_writes_mcp_timeout_with_the_server_in_tool_name_only(
        monkeypatch) -> None:
    gate, fn = _mcp_gate(monkeypatch)
    exc = _raised(_tool_exc("Failed to call tool"), cause=_mcp_error(408))
    with run():
        await _through_gate(gate, fn, exc)
    [rec] = records()
    assert (rec.error_code, rec.tool, rec.exc_type, rec.code_site) == (
        "mcp_timeout", "mcp", "-", "-")
    platform = (rec.fingerprint, rec.tool, rec.error_code, rec.exc_type, rec.code_site)
    assert all(SERVER not in value for value in platform)
    assert SERVER in rec.tool_name


async def test_c5_a_control_signal_of_maf_writes_none(monkeypatch) -> None:
    from agent_framework._middleware import MiddlewareTermination

    gate, fn = _mcp_gate(monkeypatch)
    with run():
        await _through_gate(gate, fn, MiddlewareTermination("stop"))
    assert records() == []


async def test_c5_a_function_that_is_not_an_mcp_tool_writes_none(monkeypatch) -> None:
    from agent_framework import FunctionTool

    gate, _fn = _mcp_gate(monkeypatch)
    plain = FunctionTool(name="crm_get_record", func=lambda **_k: "ran", description="x")
    with run():
        await _through_gate(gate, plain, KeyError("x"))
    assert records() == []


async def test_c5_the_capture_never_changes_what_the_gate_raises(monkeypatch) -> None:
    gate, fn = _mcp_gate(monkeypatch)
    exc = _tool_exc("x")
    with run():
        on = await _through_gate(gate, fn, exc)
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", "")
    with run():
        off = await _through_gate(gate, fn, exc)
    assert on is off is exc


# ── C6: the connect of a chat run ────────────────────────────────────────────


async def test_c6_a_refused_connect_writes_mcp_unreachable(monkeypatch) -> None:
    from orchestrator import _maf_mcp

    async def no_close(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(_maf_mcp, "_close_one", no_close)
    tool = _maf_mcp._run_scoped_tool_class()(name=SERVER, url="https://acme.example/mcp")

    async def refused() -> None:
        raise _connect_error("https://acme.example")

    tool.connect = refused
    with run():
        assert await tool.__aenter__() is tool
    [rec] = records()
    assert (rec.error_code, rec.tool, rec.tool_name) == ("mcp_unreachable", "mcp", SERVER)


class _FakeTool:
    """A tool class whose connect raises the error of the test."""

    error: BaseException = RuntimeError()

    def __init__(self, **kw: Any) -> None:
        self.name = kw["name"]

    async def connect(self) -> None:
        raise type(self).error


async def _attach(monkeypatch, error: BaseException | None = None,
                  refuse: BaseException | None = None) -> list[str]:
    from gateway import outbound_guard
    from orchestrator import _maf_mcp

    async def client(_url: str, **_k: Any) -> Any:
        if refuse is not None:
            raise refuse
        return SimpleNamespace(event_hooks={})

    async def no_close(*_a: Any, **_k: Any) -> None:
        return None

    _FakeTool.error = error or RuntimeError()
    monkeypatch.setattr(outbound_guard, "mcp_client", client)
    monkeypatch.setattr(_maf_mcp, "_run_scoped_tool_class", lambda: _FakeTool)
    monkeypatch.setattr(_maf_mcp, "_close_one", no_close)
    agent = SimpleNamespace(default_options={}, mcp_tools=[], middleware=[])
    server = _maf_mcp.McpServer(name=SERVER, url="https://acme.example/mcp")
    with run():
        return await _maf_mcp.attach_mcp_tools(agent, [server], [])


async def test_c6_a_server_that_lists_too_many_tools_writes_mcp_too_large(monkeypatch) -> None:
    from orchestrator import _maf_mcp

    assert await _attach(monkeypatch, _maf_mcp.McpListTooLong("tools")) == []
    assert [(r.error_code, r.tool_name) for r in records()] == [("mcp_too_large", SERVER)]


async def test_c6_a_connect_that_the_guard_refuses_writes_mcp_refused(monkeypatch) -> None:
    from gateway import outbound_guard

    assert await _attach(monkeypatch, refuse=outbound_guard.OutboundRefused("h", "private")) == []
    assert codes() == ["mcp_refused"]


async def test_c6_a_connect_timeout_writes_mcp_timeout(monkeypatch) -> None:
    assert await _attach(monkeypatch, _timeout_from_cancel()) == []
    assert codes() == ["mcp_timeout"]


# ── C7: the Studio read ──────────────────────────────────────────────────────


async def _studio_list(lister: Any) -> str:
    from gateway.connections import mcp_tools

    with run(None), pytest.raises(mcp_tools.ListFailed) as failed:
        await mcp_tools._list_stored(SimpleNamespace(name=SERVER), lister)
    return failed.value.reason


async def test_c7_a_list_writes_the_code_of_the_map(monkeypatch) -> None:
    from gateway import outbound_guard

    async def too_large(_s: Any) -> list[Any]:
        raise outbound_guard.AnswerTooLarge("h", "too large")

    async def refused(_s: Any) -> list[Any]:
        raise _connect_error("https://acme.example")

    assert await _studio_list(too_large) == "too_large"
    assert await _studio_list(refused) == "unreachable"
    assert [(r.error_code, r.surface, r.tool, r.tool_name) for r in records()] == [
        ("mcp_too_large", "studio", "mcp", SERVER), ("mcp_unreachable", "studio", "mcp", SERVER)]


async def test_c7_a_key_error_in_our_mcp_client_writes_mcp_exception_with_its_site(
        monkeypatch) -> None:
    """The real read path: ``_pages`` reads ``streams[0]`` of a transport that
    gave a mapping, and raises ``KeyError`` in our code."""
    import mcp.client.streamable_http as transport
    from gateway import outbound_guard

    async def client(_url: str, **_k: Any) -> Any:
        async def aclose() -> None:
            return None

        return SimpleNamespace(headers={}, aclose=aclose)

    @contextlib.asynccontextmanager
    async def streams(*_a: Any, **_k: Any) -> Any:
        yield {}

    monkeypatch.setattr(outbound_guard, "mcp_client", client)
    monkeypatch.setattr(transport, "streamable_http_client", streams)
    stored = SimpleNamespace(name=SERVER, url="https://acme.example/mcp", headers={})
    assert await _studio_list_stored(stored) == "unreachable"
    [rec] = records()
    assert (rec.error_code, rec.exc_type, rec.code_site) == (
        "mcp_exception", "builtins.KeyError", "gateway.connections.mcp_tools:_pages")


async def _studio_list_stored(stored: Any) -> str:
    from gateway.connections import mcp_tools

    with run(None), pytest.raises(mcp_tools.ListFailed) as failed:
        await mcp_tools._list_stored(stored, None)
    return failed.value.reason


class _NoCache:
    async def get(self, _key: Any) -> None:
        return None

    async def setex(self, *_a: Any) -> None:
        return None


async def _studio_call(monkeypatch, caller: Any) -> str:
    from gateway.connections import mcp_tools

    meta = SimpleNamespace(name=SERVER, url="https://acme.example/mcp")

    async def listed(*_a: Any, **_k: Any) -> Any:
        return mcp_tools.ToolList(tools=[], truncated=False)

    async def load() -> Any:
        return meta

    monkeypatch.setattr(mcp_tools, "fresh_tool_list", listed)
    monkeypatch.setattr(mcp_tools, "_class_a_item", lambda *_a: None)
    monkeypatch.setattr(mcp_tools, "_redis", _NoCache)
    monkeypatch.setattr(mcp_tools, "_scope", lambda _org: contextlib.nullcontext())
    monkeypatch.setattr(mcp_tools, "_live_key", lambda *parts: "answer")
    monkeypatch.setattr(mcp_tools, "READ_TIMEOUT_S", 0.05)
    with run(None), pytest.raises(mcp_tools.ListFailed) as failed:
        await mcp_tools._live(ORG, "kiran@acme.test", SERVER, "get_invoice", {"id": "INV-7"},
                              meta, load, None, caller, None)
    return failed.value.reason


async def test_c7_an_is_error_answer_of_shape_result_writes_mcp_tool_error(monkeypatch) -> None:
    async def is_error(*_a: Any) -> Any:
        return {"isError": True, "content": [{"type": "text", "text": "Ravi Kumar"}]}

    assert await _studio_call(monkeypatch, is_error) == "tool_error"
    [rec] = records()
    assert (rec.error_code, rec.surface, rec.tool, rec.tool_name) == (
        "mcp_tool_error", "studio", "mcp", f"{SERVER}/get_invoice")
    assert "Ravi" not in repr(rec)


async def test_c7_a_call_that_times_out_writes_mcp_timeout(monkeypatch) -> None:
    import asyncio

    async def slow(*_a: Any) -> Any:
        await asyncio.sleep(5)

    assert await _studio_call(monkeypatch, slow) == "timeout"
    assert codes() == ["mcp_timeout"]


async def test_c7_the_capture_never_changes_the_reason(monkeypatch) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the writer broke")

    monkeypatch.setattr(fl, "record_tool_failure", broken)

    async def refused(_s: Any) -> list[Any]:
        raise _connect_error("https://acme.example")

    assert await _studio_list(refused) == "unreachable"
