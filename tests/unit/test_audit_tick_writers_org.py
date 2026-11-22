"""Task 2221, slice 2 — the no-database half: system ticks and webhooks.

Twelve audit writers record an event with no ``organization_id``. With
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit._persist`` filed each row under the
OPERATOR org (Option-A):

* ``ingestion.scheduler``: ``run_ok`` and ``run_failed`` of the Zoho job and of
  the reconciler job, and ``scheduler_started``.
* ``ingestion.sources.gmail.webhook.receive``: ``received`` (the mailbox).
* ``ingestion.sources.zoho.webhook.receive``: ``received`` (the CRM event).
* ``reconciler.main.run``: ``run_ok`` (the counts).
* ``scripts.reconciler.run``: two ``escalation`` rows (a task title, a
  customer name, a deal value) and ``run_summary`` (the counts).
* ``scripts.zoho_sync.main``: ``full_sync`` (the counts of one Zoho).

No writer has a tenant source. The jobs open the unbound ``get_session``.
Each webhook checks one shared secret for the deployment, and no code maps the
mailbox or the module to an org. So each row that holds tenant data goes
through a local ``_audit``: with no org and the bind on, ``refuse_tenantless``
skips it. Two rows hold no tenant data, the Zoho ``run_ok`` (an empty payload)
and ``scheduler_started`` (job ids and cron triggers). They carry the explicit
``system=True`` marker of slice 3 and keep Option-A, and this file proves that
their payload stays free of tenant data. With the bind
off, every row stays as before, so the change is dark. A payload field never
gives the org (R5).

This file proves the skip, the system rows and the dark path with no
database, and it holds the AST source fence (section 4). The real table under FORCE RLS
is ``test_audit_tick_writers_org_r8.py`` (R8).
"""
from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import json
import re
import uuid
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
GMAIL_TOKEN = "t2221-gmail-token"
ZOHO_SECRET = "t2221-zoho-secret"
#: Text that stands for one tenant's data in an error message.
TENANT_TEXT = "org B deal Acme owes 12 lakh"
COUNTS = {"stale_tasks": 3, "quiet_deals": 2}

#: The module and the source path of each writer.
WRITERS = {
    "ingestion.scheduler": "apps/services/ingestion/ingestion/scheduler.py",
    "ingestion.sources.gmail.webhook":
        "apps/services/ingestion/ingestion/sources/gmail/webhook.py",
    "ingestion.sources.zoho.webhook":
        "apps/services/ingestion/ingestion/sources/zoho/webhook.py",
    "reconciler.main": "apps/services/reconciler/reconciler/main.py",
    "scripts.reconciler": "scripts/reconciler.py",
    "scripts.zoho_sync": "scripts/zoho_sync.py",
}


# ==========================================================================
# Helpers. The R8 file imports these.
# ==========================================================================
async def _drained(coro) -> Any:
    from acb_audit import log as audit

    out = await coro
    await audit.drain()
    return out


def _zoho_job(mp, *, fail: bool) -> None:
    from ingestion import scheduler

    import scripts.zoho_sync as zoho_sync

    async def _main() -> None:
        if fail:
            raise RuntimeError(TENANT_TEXT)

    mp.setattr(zoho_sync, "main", _main)
    asyncio.run(_drained(scheduler._run_zoho()))


def _reconciler_job(mp, *, fail: bool) -> None:
    from ingestion import scheduler

    import scripts.reconciler as core

    def _run(**_k: Any) -> dict[str, int]:
        if fail:
            raise RuntimeError(TENANT_TEXT)
        return dict(COUNTS)

    mp.setattr(core, "run", _run)
    asyncio.run(_drained(scheduler._run_reconciler()))


def _scheduler_started(_mp) -> None:
    """Start the real scheduler, then stop it after the first audit write."""
    from ingestion import scheduler

    async def _go() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(scheduler._serve(), timeout=0.5)

    asyncio.run(_drained(_go()))


async def _post(router, path: str, body: dict, headers: dict[str, str]) -> int:
    import httpx
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ingest.test",
    ) as client:
        got = await client.post(path, json=body, headers=headers)
    return got.status_code


def _quiet_receivers(mp) -> None:
    """No Redis and no event sink. The consumer path enqueues only."""
    from ingestion.sources.gmail import webhook as gmail_webhook
    from ingestion.sources.zoho import webhook as zoho_webhook

    mp.setenv("INGESTION_CONSUMER", "1")
    for mod in (gmail_webhook, zoho_webhook):
        mp.setattr(mod, "enqueue", lambda *_a, **_k: "0-0")
    mp.setattr(gmail_webhook, "get_settings",
               lambda: SimpleNamespace(gmail_pubsub_token=GMAIL_TOKEN))
    mp.setattr(zoho_webhook, "get_settings",
               lambda: SimpleNamespace(zoho_webhook_secret=ZOHO_SECRET))


def _gmail_push(mp, mailbox: str | None = None) -> str:
    """One authenticated Pub/Sub push for *mailbox*. Returns the audit target."""
    from ingestion.sources.gmail import webhook as gmail_webhook

    _quiet_receivers(mp)
    mailbox = mailbox or f"ops-{uuid.uuid4().hex[:8]}@orgb.test"
    data = base64.b64encode(
        json.dumps({"emailAddress": mailbox, "historyId": 42}).encode(),
    ).decode()
    status = asyncio.run(_drained(_post(
        gmail_webhook.router, "/webhooks/gmail",
        {"message": {"data": data, "messageId": "1"}},
        {"Authorization": f"Bearer {GMAIL_TOKEN}"},
    )))
    assert status == 200, "a skipped audit row must not fail the push"
    return f"gmail:{mailbox}"


def _zoho_push(mp, module: str | None = None) -> str:
    """One authenticated Zoho push for *module*. Returns the audit target."""
    from ingestion.sources.zoho import webhook as zoho_webhook

    _quiet_receivers(mp)
    module = module or f"Deals{uuid.uuid4().hex[:6]}"
    status = asyncio.run(_drained(_post(
        zoho_webhook.router, "/webhooks/zoho",
        {"event": "Deals.update", "module": module},
        {"X-Zoho-Token": ZOHO_SECRET},
    )))
    assert status == 200, "a skipped audit row must not fail the push"
    return f"zoho:{module}"


def _reconciler_app(mp) -> None:
    reconciler_main = writer_module("reconciler.main")

    import scripts.reconciler as core

    mp.setattr(core, "run", lambda **_k: dict(COUNTS))
    mp.setattr(reconciler_main, "configure_logging", lambda *_a, **_k: None)
    assert asyncio.run(_drained(reconciler_main.run())) == sum(COUNTS.values())


#: The ids of the fake task and deal that ``scripts.reconciler`` escalates.
TASK_ID = uuid.uuid4()
DEAL_ID = uuid.uuid4()


def _inner_reconciler(mp) -> None:
    """Run the real ``scripts.reconciler.run`` over one stale task and one quiet deal.

    The scan is a fake, so no database is read. The audit rows are the real
    ones: two ``escalation`` rows and ``run_summary``.
    """
    import scripts.reconciler as core

    task = SimpleNamespace(
        id=TASK_ID, project=SimpleNamespace(name="Org B launch"),
        owner=SimpleNamespace(canonical_name="Asha of org B"), clickup_id=None,
        title="Ship the org B order", stage="doing", days_in_stage=30, updated_at=None,
    )
    deal = SimpleNamespace(
        id=DEAL_ID, owner=None, customer=SimpleNamespace(name="Acme of org B"),
        last_activity_at=None, zoho_id="z-2221", name="Acme renewal",
        stage="Negotiation", value_inr=1200000,
    )
    mp.setattr(core, "get_session", lambda: contextlib.nullcontext(None))
    mp.setattr(core, "repo", SimpleNamespace(
        stale_tasks=lambda _s, **_k: [task], quiet_deals=lambda _s, **_k: [deal],
    ))
    assert core.run() == {"stale_tasks": 1, "quiet_deals": 1}


def _inner_zoho_sync(mp) -> None:
    """Run the real ``scripts.zoho_sync.main`` with no Zoho call and no database."""
    import scripts.zoho_sync as zoho_sync

    async def _none() -> list:
        return []

    for name in ("list_accounts", "list_contacts", "list_users", "list_deals"):
        mp.setattr(zoho_sync.client, name, _none)
    for name in ("normalise_users", "normalise_accounts", "normalise_contacts",
                 "normalise_deals"):
        mp.setattr(zoho_sync, name, lambda _s, _rows: 7)
    mp.setattr(zoho_sync, "get_session", lambda: contextlib.nullcontext(None))
    asyncio.run(_drained(zoho_sync.main()))


#: A row that holds tenant data. With no org and the bind on, it is skipped.
GUARDED = "guarded"
#: A pure system row. It keeps Option-A: the operator org.
SYSTEM = "system"

#: One case for each event: (id, driver, actor, action, target or None, kind).
#: A ``None`` target is the one that the driver returns.
CASES: list[tuple[str, Callable[[Any], Any], str, str, str | None, str]] = [
    ("zoho_sync_run_ok", lambda mp: _zoho_job(mp, fail=False),
     "job:scheduler", "run_ok", "job:zoho_sync", SYSTEM),
    ("zoho_sync_run_failed", lambda mp: _zoho_job(mp, fail=True),
     "job:scheduler", "run_failed", "job:zoho_sync", GUARDED),
    ("reconciler_run_ok", lambda mp: _reconciler_job(mp, fail=False),
     "job:scheduler", "run_ok", "job:reconciler", GUARDED),
    ("reconciler_run_failed", lambda mp: _reconciler_job(mp, fail=True),
     "job:scheduler", "run_failed", "job:reconciler", GUARDED),
    ("scheduler_started", _scheduler_started,
     "job:scheduler", "scheduler_started", "scheduler", SYSTEM),
    ("gmail_received", _gmail_push, "webhook:gmail", "received", None, GUARDED),
    ("zoho_received", _zoho_push, "webhook:zoho", "received", None, GUARDED),
    ("reconciler_app_run_ok", _reconciler_app,
     "job:reconciler-app", "run_ok", "job:reconciler", GUARDED),
    ("escalation_task", _inner_reconciler,
     "job:reconciler", "escalation", f"task:{TASK_ID}", GUARDED),
    ("escalation_deal", _inner_reconciler,
     "job:reconciler", "escalation", f"deal:{DEAL_ID}", GUARDED),
    ("reconciler_run_summary", _inner_reconciler,
     "job:reconciler", "run_summary", "reconciler", GUARDED),
    ("zoho_full_sync", _inner_zoho_sync,
     "job:zoho_sync", "full_sync", "source:zoho", GUARDED),
]
CASE_IDS = [c[0] for c in CASES]
CASE_ARGS = ("case", "drive", "actor", "action", "target", "kind")
GUARDED_CASES = [c for c in CASES if c[5] == GUARDED]
SYSTEM_CASES = [c for c in CASES if c[5] == SYSTEM]


def matching(events: list[Any], actor: str, action: str, target: str) -> list[Any]:
    return [e for e in events if (e.actor, e.action, e.target) == (actor, action, target)]


def assert_no_tenant_payload(case: str, event: Any) -> None:
    """A system row holds no tenant data: nothing but what the code fixes."""
    if case == "zoho_sync_run_ok":
        assert event.payload == {}, f"{case}: a system row gained a payload"
        return
    assert case == "scheduler_started", case
    assert set(event.payload) == {"jobs"}, f"{case}: {sorted(event.payload)}"
    jobs = event.payload["jobs"]
    assert {j["id"] for j in jobs} == {"zoho_sync", "reconciler"}
    assert all(set(j) == {"id", "trigger"} for j in jobs), jobs


def writer_module(name: str) -> Any:
    """Import one writer module by name, whatever order the test run has.

    Several test files put ``scripts/`` first on ``sys.path`` when they load.
    Then the name ``reconciler`` resolves to ``scripts/reconciler.py``, a
    plain module, and ``reconciler.main`` cannot import. So this import takes
    ``scripts/`` off ``sys.path`` for the one call, and it drops a plain
    ``reconciler`` module that took the package name.
    """
    import importlib
    import os
    import sys

    if not name.startswith("reconciler"):
        return importlib.import_module(name)
    cached = sys.modules.get("reconciler")
    if cached is not None and not hasattr(cached, "__path__"):
        del sys.modules["reconciler"]
    scripts = os.path.normcase(os.path.abspath(REPO / "scripts"))
    saved = list(sys.path)
    sys.path[:] = [
        p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != scripts
    ]
    try:
        return importlib.import_module(name)
    finally:
        sys.path[:] = saved


def spy_on_writers(mp, sink: Callable[[Any], None]) -> None:
    """Point the ``record`` name of each writer module at *sink*."""
    for name in WRITERS:
        mp.setattr(writer_module(name), "record", sink)


# ==========================================================================
# Fixtures
# ==========================================================================
@pytest.fixture
def events(monkeypatch) -> list[Any]:
    calls: list[Any] = []
    spy_on_writers(monkeypatch, calls.append)
    return calls


# ==========================================================================
# 1. The bind on: a row with tenant data never reaches the audit seam.
# ==========================================================================
@pytest.mark.parametrize(CASE_ARGS, GUARDED_CASES, ids=[c[0] for c in GUARDED_CASES])
def test_with_the_bind_on_a_tenant_row_with_no_tenant_records_nothing(
    events, monkeypatch, case, drive, actor, action, target, kind,
) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    got_target = drive(monkeypatch)
    assert matching(events, actor, action, target or got_target) == [], (
        f"{case}: a row with tenant data and no tenant reached the audit "
        "seam, so the operator org fallback would take it"
    )


# ==========================================================================
# 2. The bind on: a pure system row keeps Option-A, with no tenant data.
# ==========================================================================
@pytest.mark.parametrize(CASE_ARGS, SYSTEM_CASES, ids=[c[0] for c in SYSTEM_CASES])
def test_with_the_bind_on_a_system_row_stays_with_no_tenant_data(
    events, monkeypatch, case, drive, actor, action, target, kind,
) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    drive(monkeypatch)
    [ev] = matching(events, actor, action, target)
    assert ev.organization_id is None, "a system row takes the operator org"
    assert ev.system is True, (
        f"{case}: a system row with no system marker is refused by record"
    )
    assert_no_tenant_payload(case, ev)


# ==========================================================================
# 3. The bind off: each row stays as before (dark).
# ==========================================================================
@pytest.mark.parametrize(CASE_ARGS, CASES, ids=CASE_IDS)
def test_with_the_bind_off_each_row_stays(
    events, monkeypatch, case, drive, actor, action, target, kind,
) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    got_target = drive(monkeypatch)
    got = matching(events, actor, action, target or got_target)
    assert [e.organization_id for e in got] == [None], (
        f"{case}: with the bind off the row must stay as before"
    )
    assert [e.system for e in got] == [kind == SYSTEM], (
        f"{case}: only a named system row carries the system marker"
    )


def test_a_failure_row_keeps_its_error_text_with_the_bind_off(events, monkeypatch) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    _zoho_job(monkeypatch, fail=True)
    [ev] = events
    assert TENANT_TEXT in ev.payload["error"]


def test_an_escalation_row_keeps_its_tenant_text_with_the_bind_off(events, monkeypatch) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    _inner_reconciler(monkeypatch)
    [ev] = matching(events, "job:reconciler", "escalation", f"deal:{DEAL_ID}")
    assert ev.payload["customer"] == "Acme of org B"


# ==========================================================================
# 4. The source fence (AST). Each audit write sits inside the one guard, or
#    it is one of the named Option-A system rows.
# ==========================================================================
# Six modules write an audit row with no tenant source. Each row that holds
# tenant data must go through the module's one ``_audit`` guard. Two named rows
# hold no tenant data, and they call ``record`` directly (Option-A).
#
# **The threat model.** This fence claims two things only. It catches an
# honest new audit write that skips the guard. It also catches each case of
# the corpus in ``MUTANTS`` and ``ENTRY_MUTANTS``: an alias, a second or nested
# ``_audit``, a decorator or a default on the guard, a rebound name, a write to
# a pinned attribute, a pinned name imported from another module, a dunder
# attribute and the listed reflection names. The behaviour tests in sections
# 1 to 3 and the R8 tests in ``test_audit_tick_writers_org_r8.py`` pin the
# twelve named rows at run time.
#
# **Known limits.** A hostile author can always reach a function by
# reflection, and no source fence can stop that. Review catches these. The
# fence does not catch, for example:
#
# * A3: ``pkgutil.resolve_name("acb_audit:record")``.
# * A4: ``(_ for _ in ()).gi_frame.f_globals["record"]``.
# * A5: ``string.Formatter().get_field("0.record", ...)``.
#
# The checks are allow-lists where possible:
#
# * The guard has one allowed text. The fence compares the whole
#   ``ast.unparse`` of the function, without its docstring.
# * A ``record`` call is legal at two places only. One is the single statement
#   of the guard's ``if``, by node identity. The other is a statement in the
#   body of a module-level function that ``SYSTEM_CALLS`` names with its action.
# * Slice 3: a named system row carries ``system=True``, as a constant. A
#   ``system=`` keyword anywhere else is refused. Section 5 checks the same
#   rule across ``apps/``, ``packages/`` and ``scripts/``.
# * ``acb_audit`` is imported once, at module level, with the allowed names.
#   A pinned name enters a module only through that one line.
# * A writer module imports another writer module only where
#   ``WRITER_IMPORTS`` names it.

#: The source path and the dotted name of each writer module.
FENCED_WRITERS = {
    "apps/services/ingestion/ingestion/scheduler.py": "ingestion.scheduler",
    "apps/services/ingestion/ingestion/sources/gmail/webhook.py":
        "ingestion.sources.gmail.webhook",
    "apps/services/ingestion/ingestion/sources/zoho/webhook.py":
        "ingestion.sources.zoho.webhook",
    "apps/services/reconciler/reconciler/main.py": "reconciler.main",
    "scripts/reconciler.py": "scripts.reconciler",
    "scripts/zoho_sync.py": "scripts.zoho_sync",
}
#: The Option-A system rows: (path, module-level function, action). Each one
#: carries ``system=True`` (task 2221 slice 3). Why each is a system row:
#:
#: * ``_run_zoho`` ``run_ok``: the payload is ``{}``. It says only that the
#:   deployment's Zoho job ran, and no tenant owns that job.
#: * ``_serve`` ``scheduler_started``: the job ids and the cron triggers that
#:   the code fixes. The scheduler process serves the whole deployment.
SYSTEM_CALLS = {
    ("apps/services/ingestion/ingestion/scheduler.py", "_run_zoho", "run_ok"),
    ("apps/services/ingestion/ingestion/scheduler.py", "_serve", "scheduler_started"),
}
#: The only import of one writer module by another: (path, dotted name, alias).
WRITER_IMPORTS = {
    ("apps/services/ingestion/ingestion/scheduler.py", "scripts.zoho_sync", None),
    ("apps/services/ingestion/ingestion/scheduler.py", "scripts.reconciler", None),
    ("apps/services/reconciler/reconciler/main.py", "scripts.reconciler", "core"),
}
#: The one allowed line that imports from ``acb_audit``.
AUDIT_IMPORT = ("AuditEvent", "record", "refuse_tenantless")
#: The names that the guard depends on. Nothing else may bind them, import
#: them or write them as an attribute.
PINNED = {"record", "_persist", "refuse_tenantless", "AuditEvent"}
#: The names that write a row.
SINKS = {"record", "_persist"}
#: Modules that reach an attribute by a string or a frame.
FORBIDDEN_MODULES = {
    "operator", "inspect", "functools", "importlib", "sys", "builtins", "types",
    "ctypes", "gc", "runpy", "pickle", "marshal", "code", "codeop",
}
#: Built-in names that reach an attribute by a string.
REFLECTION = {
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "__import__",
    "eval", "exec", "compile", "breakpoint",
}
#: The only dunder names that a writer module uses.
ALLOWED_DUNDER_NAMES = {"__name__", "__all__", "__doc__"}

_THROUGH = (
    "Send the row through _audit(), or add a SYSTEM_CALLS entry with a reason"
)
_MARKER = (
    "Stamp the org of the event. Only a SYSTEM_CALLS row with a reason takes "
    "system=True"
)
_LINE = "Import record, refuse_tenantless and AuditEvent only through the one acb_audit line"
_BODY = "    if not refuse_tenantless(event):\n        record(event)"
#: The allowed texts of the guard: the annotations are optional.
GUARD_TEXTS = {
    f"def _audit({arg}){ret}:\n{_BODY}"
    for arg in ("event: AuditEvent", "event")
    for ret in (" -> None", "")
}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def action_of(call: ast.Call) -> str | None:
    """The ``action=`` constant of the ``AuditEvent`` in *call*, if any."""
    if call.args and isinstance(call.args[0], ast.Call):
        for kw in call.args[0].keywords:
            if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                return kw.value.value
    return None


def _without_docstring(fn: ast.FunctionDef) -> ast.FunctionDef:
    copy = deepcopy(fn)
    body = copy.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        copy.body = body[1:]
    return copy


def _guard(tree: ast.Module, path: str, out: list[str]) -> ast.Call | None:
    """The ``record(event)`` call node of THE guard, or ``None`` with a reason."""
    audits = [n for n in ast.walk(tree) if isinstance(n, _FUNCTIONS) and n.name == "_audit"]
    if len(audits) != 1:
        out.append(f"{path}: expected one _audit in the module, found {len(audits)}. "
                   "Keep one module-level _audit, and send each tenant row through it")
        return None
    [fn] = audits
    if fn not in tree.body or not isinstance(fn, ast.FunctionDef):
        out.append(f"{path}: _audit must be a module-level def. "
                   "Keep the guard a plain def at module level")
        return None
    if fn.decorator_list:
        out.append(f"{path}: _audit has a decorator. Remove it, the guard takes none")
    a = fn.args
    if (a.defaults or a.kw_defaults or a.vararg or a.kwarg or a.kwonlyargs
            or a.posonlyargs or len(a.args) != 1):
        out.append(f"{path}: _audit must take one positional parameter and no default. "
                   "Keep the signature _audit(event: AuditEvent) -> None")
    text = ast.unparse(_without_docstring(fn))
    if text not in GUARD_TEXTS:
        out.append(f"{path}: the guard text is not the allowed one. Keep the body "
                   f"'if not refuse_tenantless(event): record(event)':\n{text}")
        return None
    return fn.body[-1].body[0].value


def _system_call_ok(call: ast.Call, parents: dict, top: set[int], path: str) -> bool:
    """*call* is a statement in the body of a named module-level system function."""
    child, up = call, parents.get(call)
    while up is not None and not isinstance(up, _FUNCTIONS):
        if isinstance(up, ast.Lambda | ast.ClassDef):
            return False
        child, up = up, parents.get(up)
    if up is None or id(up) not in top or child not in up.body:
        return False
    return (path, up.name, action_of(call)) in SYSTEM_CALLS


def has_system_marker(call: ast.Call) -> bool:
    """The ``AuditEvent`` in *call* passes ``system=True`` as a constant."""
    if not (call.args and isinstance(call.args[0], ast.Call)):
        return False
    return any(
        kw.arg == "system" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.args[0].keywords
    )


def _marker_ok(kw: ast.keyword, parents: dict, top: set[int], path: str) -> bool:
    """*kw* is ``system=True`` on the ``AuditEvent`` of a named system row."""
    event = parents.get(kw)
    call = parents.get(event)
    return (
        isinstance(kw.value, ast.Constant) and kw.value.value is True
        and isinstance(event, ast.Call) and isinstance(event.func, ast.Name)
        and event.func.id == "AuditEvent"
        and isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id == "record" and call.args and call.args[0] is event
        and _system_call_ok(call, parents, top, path)
    )


def _binds(node: ast.AST) -> list[str]:
    """The names that *node* binds, other than through an import."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        return [node.id]
    if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Global | ast.Nonlocal):
        return list(node.names)
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
        return [node.name]
    return []


def _import_violations(node: ast.AST, path: str, at: str, tree: ast.Module) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Import | ast.ImportFrom) and getattr(node, "module", "") != "acb_audit":
        for a in node.names:
            if a.name == "*":
                out.append(f"{at}: a star import. Name each imported name")
            entered = {*a.name.split("."), a.asname} & PINNED
            if entered:
                out.append(f"{at}: an import of the pinned name {sorted(entered)[0]}. {_LINE}")
    if isinstance(node, ast.Import):
        pairs = [(a.name, a.asname, a.asname or a.name.split(".")[0]) for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            return [*out, f"{at}: a relative import. Use an absolute import"]
        base = node.module or ""
        if base == "acb_audit":
            names = tuple(a.name for a in node.names)
            if (node not in tree.body or names != AUDIT_IMPORT
                    or any(a.asname for a in node.names)):
                out.append(f"{at}: the acb_audit import is not the one allowed line. Use "
                            "'from acb_audit import AuditEvent, record, refuse_tenantless' at module level")
            return out
        pairs = [(f"{base}.{a.name}", a.asname, a.asname or a.name) for a in node.names]
        pairs.append((base, None, ""))
    else:
        return out
    for dotted, alias, _bound in pairs:
        root = dotted.split(".")[0]
        if root == "acb_audit":
            out.append(f"{at}: an import of {dotted}. {_LINE}")
        if root in FORBIDDEN_MODULES:
            out.append(f"{at}: an import of {root} can reach record around _audit. "
                       f"Do the work without {root}, or ask review to change the fence")
        if dotted in FENCED_WRITERS.values() and (path, dotted, alias) not in WRITER_IMPORTS:
            out.append(f"{at}: an import of the writer module {dotted}. Import a writer "
                       "module only where WRITER_IMPORTS names it, with a reason")
    return out


def _sink_violations(node: ast.Name, at: str, parents: dict, sink: ast.Call | None,
                     top: set[int], path: str) -> list[str]:
    """A ``record`` or ``_persist`` name: the guard's call, or a marked system row."""
    call = parents.get(node)
    if not (isinstance(call, ast.Call) and call.func is node):
        return [f"{at}: {node.id} used as a value, not called. Call _audit(event)"]
    if call is sink:
        return []
    if not _system_call_ok(call, parents, top, path):
        return [f"{at}: {node.id}() outside the guard and the system rows. {_THROUGH}"]
    if not has_system_marker(call):
        return [f"{at}: a system row with no system=True marker. Add "
                "system=True to its AuditEvent, or record refuses it"]
    return []


def _node_violations(node: ast.AST, path: str, at: str, parents: dict,
                     sink: ast.Call | None, top: set[int]) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Attribute):
        if node.attr in PINNED and isinstance(node.ctx, ast.Store | ast.Del):
            out.append(f"{at}: a write to the pinned attribute .{node.attr}. Do not "
                       "reassign a pinned name on any module")
        if node.attr.startswith("__") and node.attr.endswith("__"):
            out.append(f"{at}: the dunder attribute .{node.attr}. Use a public name, "
                       "or ask review to change the fence")
        if node.attr in SINKS:
            out.append(f"{at}: .{node.attr} reaches the audit seam around _audit. {_THROUGH}")
    elif isinstance(node, ast.Name):
        if node.id in REFLECTION:
            out.append(f"{at}: {node.id} can reach record around _audit. Use a plain "
                       "name, or ask review to change the fence")
        if (node.id.startswith("__") and node.id.endswith("__")
                and node.id not in ALLOWED_DUNDER_NAMES):
            out.append(f"{at}: the dunder name {node.id}. Use a public name")
        if node.id in SINKS and isinstance(node.ctx, ast.Load):
            out += _sink_violations(node, at, parents, sink, top, path)
    elif isinstance(node, ast.keyword) and node.arg == "system":
        if not _marker_ok(node, parents, top, path):
            out.append(f"{at}: system= outside a named system row. {_MARKER}")
    elif isinstance(node, ast.keyword) and node.arg == "organization_id":
        out.append(f"{at}: organization_id= on an audit row with no tenant source. This "
                   "module has no bound tenant, so never take the org from the payload")
    bound = [n for n in _binds(node) if n in PINNED]
    if bound and not (isinstance(node, ast.arg) and node.arg == "event"):
        out.append(f"{at}: {bound[0]} is bound again. Do not reassign a pinned name")
    return out


def audit_fence_violations(src: str, path: str) -> list[str]:
    """Each way that *src* writes an audit row around the guard."""
    tree = ast.parse(src)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out: list[str] = []
    sink = _guard(tree, path, out)
    top = {id(n) for n in tree.body if isinstance(n, _FUNCTIONS)}
    audit_imports = [n for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module == "acb_audit"]
    if len(audit_imports) != 1:
        out.append(f"{path}: expected one acb_audit import, found {len(audit_imports)}. "
                   "Keep the one acb_audit line")
    for node in ast.walk(tree):
        at = f"{path}:{getattr(node, 'lineno', '?')}"
        out += _import_violations(node, path, at, tree)
        out += _node_violations(node, path, at, parents, sink, top)
    return out


def _source(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("path", sorted(WRITERS.values()))
def test_each_writer_records_only_through_the_guard(path: str) -> None:
    assert audit_fence_violations(_source(path), path) == []


def test_the_fence_covers_each_writer() -> None:
    assert set(FENCED_WRITERS) == set(WRITERS.values())
    assert set(FENCED_WRITERS.values()) == set(WRITERS)


def test_each_named_system_row_is_in_the_source() -> None:
    """The allow-list names real calls, so a stale entry fails."""
    for path, fn, action in SYSTEM_CALLS:
        tree = ast.parse(_source(path))
        found = [
            c
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == fn
            for c in ast.walk(n)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == "record" and action_of(c) == action
        ]
        assert len(found) == 1, (path, fn, action)


# --------------------------------------------------------------------------
# Self-tests: each known bypass is red, for its own rule. The anchors come
# from the AST of the real file, so a mutant cannot hide by moving text.
# --------------------------------------------------------------------------
_GMAIL = "apps/services/ingestion/ingestion/sources/gmail/webhook.py"
_EV = 'AuditEvent(actor="webhook:gmail", action="leaked", target="gmail:x")'


def _lines(src: str) -> list[str]:
    return src.splitlines(keepends=True)


def _guard_line(src: str) -> int:
    """The 0-based line of ``def _audit``."""
    [fn] = [n for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name == "_audit"]
    return fn.lineno - 1


def _first_guarded_call(src: str) -> ast.Call:
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_audit"]
    return min(calls, key=lambda c: c.lineno)


def _replace_name_at(src: str, call: ast.Call, new: str) -> str:
    """Replace the called name of *call* with *new*, by its position."""
    lines = _lines(src)
    i, col = call.func.lineno - 1, call.func.col_offset
    lines[i] = lines[i][:col] + new + lines[i][col + len(call.func.id):]
    return "".join(lines)


def _add(src: str, code: str) -> str:
    return src.rstrip("\n") + "\n\n\n" + code


def mutant_g(src: str) -> str:
    """G: a decorator on the guard that writes the row itself."""
    lines = _lines(src)
    i = _guard_line(src)
    lines.insert(i, "@(lambda _f: lambda e: record(e))\n")
    return "".join(lines)


def mutant_h(src: str) -> str:
    """H: a default on the guard that writes the row, called through __defaults__."""
    lines = _lines(src)
    i = _guard_line(src)
    lines[i] = "def _audit(event: AuditEvent, _w=lambda e: record(e)) -> None:\n"
    return _add("".join(lines), f"def _other() -> None:\n    _audit.__defaults__[0]({_EV})\n")


def mutant_i(src: str) -> str:
    """I: the guard's check is bound again, so it never refuses."""
    return _add(src, "refuse_tenantless = lambda _e: False\n")


def mutant_j(src: str) -> str:
    """J: ``record`` of another writer module, through ``operator``."""
    return _add(src, (
        "import operator\n"
        "from ingestion.sources.zoho import webhook as _z\n\n\n"
        "def _other() -> None:\n"
        f"    operator.attrgetter('record')(_z)({_EV})\n"
    ))


def mutant_k(src: str) -> str:
    """K: ``record`` through the guard's ``__globals__``."""
    return _add(src, f"def _other() -> None:\n    _audit.__globals__['record']({_EV})\n")


#: Each mutant, and a part of the message that its own rule gives.
MUTANTS = {
    "G decorator on the guard": (mutant_g, "_audit has a decorator"),
    "H default on the guard": (mutant_h, "no default"),
    "I check bound again": (mutant_i, "refuse_tenantless is bound again"),
    "J operator and a writer import": (mutant_j, "an import of operator"),
    "J writer module import": (mutant_j, "an import of the writer module"),
    "K __globals__": (mutant_k, "the dunder attribute .__globals__"),
    "H __defaults__": (mutant_h, "the dunder attribute .__defaults__"),
    "nested async _audit (Clean C)": (
        lambda s: _add(s, (
            "async def _leak() -> None:\n"
            "    async def _audit(e):\n"
            "        record(e)\n"
            f"    await _audit({_EV})\n"
        )),
        "expected one _audit in the module, found 2",
    ),
    "nested sync _audit": (
        lambda s: _add(s, (
            "def _other() -> None:\n"
            "    def _audit(e):\n"
            "        record(e)\n"
            f"    _audit({_EV})\n"
        )),
        "expected one _audit in the module, found 2",
    ),
    "globals lookup": (
        lambda s: _add(s, f'def _other() -> None:\n    globals()["record"]({_EV})\n'),
        "globals can reach record",
    ),
    "getattr on import_module": (
        lambda s: _add(s, (
            "import importlib\n\n\n"
            "def _other() -> None:\n"
            f'    getattr(importlib.import_module("acb_audit"), "record")({_EV})\n'
        )),
        "an import of importlib",
    ),
    "__import__": (
        lambda s: _add(s, f'def _other() -> None:\n    __import__("acb_audit").record({_EV})\n'),
        "__import__ can reach record",
    ),
    "sys.modules": (
        lambda s: _add(s, (
            "import sys\n\n\n"
            "def _other() -> None:\n"
            f'    sys.modules["acb_audit"].record({_EV})\n'
        )),
        "an import of sys",
    ),
    "aliased record import": (
        lambda s: _add(s, "from acb_audit import record as _write\n"),
        "expected one acb_audit import",
    ),
    "_persist import": (
        lambda s: _add(s, "from acb_audit.log import _persist\n"),
        "an import of acb_audit.log",
    ),
    "module import": (
        lambda s: _add(s, "import acb_audit\n"),
        "an import of acb_audit",
    ),
    "bare call": (
        lambda s: _replace_name_at(s, _first_guarded_call(s), "record"),
        "record() outside the guard",
    ),
    "value alias": (
        lambda s: _add(s, f"def _other() -> None:\n    w = record\n    w({_EV})\n"),
        "record used as a value",
    ),
    "async guard": (
        lambda s: "".join(
            ("async " + ln) if i == _guard_line(s) else ln
            for i, ln in enumerate(_lines(s))
        ),
        "_audit must be a module-level def",
    ),
    "no guard": (
        lambda s: s.replace(
            "    if not refuse_tenantless(event):\n        record(event)",
            "    record(event)", 1,
        ),
        "the guard text is not the allowed one",
    ),
}


_SCHEDULER = "apps/services/ingestion/ingestion/scheduler.py"


def mutant_s1(src: str) -> str:
    """S1: ``record`` imported from another writer module that the fence allows."""
    return _add(src, (
        "from scripts.zoho_sync import record as _w\n\n\n"
        f"def _other() -> None:\n    _w({_EV})\n"
    ))


def mutant_a1(src: str) -> str:
    """A1: ``record`` imported from a module that is not a writer."""
    return _add(src, (
        "from orchestrator.executor import record as _w\n\n\n"
        f"def _other() -> None:\n    _w({_EV})\n"
    ))


def mutant_a2(src: str) -> str:
    """A2: the guard's check replaced on the module object."""
    return _add(src, (
        "from ingestion.sources import gmail as _pkg\n\n"
        "_pkg.webhook.refuse_tenantless = lambda _e: False\n"
    ))


#: (path, mutant, a part of the message of its own rule).
ENTRY_MUTANTS = {
    "S1 record from a writer module": (
        _SCHEDULER, mutant_s1, "an import of the pinned name record"),
    "A1 record from another module": (
        _GMAIL, mutant_a1, "an import of the pinned name record"),
    "A2 write to a pinned attribute": (
        _GMAIL, mutant_a2, "a write to the pinned attribute .refuse_tenantless"),
    "star import": (
        _GMAIL, lambda s: _add(s, "from orchestrator.executor import *\n"), "a star import"),
}


@pytest.mark.parametrize("label", sorted(ENTRY_MUTANTS))
def test_the_fence_catches_a_pinned_name_entry_or_write(label: str) -> None:
    path, make, reason = ENTRY_MUTANTS[label]
    src = _source(path)
    assert audit_fence_violations(src, path) == []
    found = audit_fence_violations(make(src), path)
    assert any(reason in v for v in found), f"{label}: expected {reason!r} in {found}"


def test_each_message_tells_the_developer_what_to_do() -> None:
    """Each refusal ends in an action: the message has a second sentence."""
    src = _source(_GMAIL)
    makers = [m for m, _r in MUTANTS.values()] + [m for _p, m, _r in ENTRY_MUTANTS.values()
                                                   if _p == _GMAIL]
    for make in makers:
        for v in audit_fence_violations(make(src), _GMAIL):
            assert ". " in v.split(": ", 1)[1], f"no action in: {v}"


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_the_fence_catches_each_known_bypass(label: str) -> None:
    make, reason = MUTANTS[label]
    src = _source(_GMAIL)
    mutant = make(src)
    assert mutant != src, f"{label}: the mutant did not change the source"
    found = audit_fence_violations(mutant, _GMAIL)
    assert any(reason in v for v in found), f"{label}: expected {reason!r} in {found}"


def test_the_fence_refuses_a_nested_function_with_a_system_name() -> None:
    """A system row counts only as a statement of its own module-level function."""
    path = "apps/services/ingestion/ingestion/scheduler.py"
    src = _source(path)
    [fn] = [n for n in ast.parse(src).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_reconciler"]
    lines = _lines(src)
    lines.insert(fn.body[0].lineno - 1, (
        "    async def _serve() -> None:\n"
        '        record(AuditEvent(actor="job:scheduler", action="scheduler_started",'
        ' target="scheduler", payload={"error": "tenant text"}))\n\n'
        "    await _serve()\n"
    ))
    found = audit_fence_violations("".join(lines), path)
    assert any("record() outside the guard" in v for v in found), found


# --------------------------------------------------------------------------
# Slice 3: the system marker. Each named system row carries it, and no other
# row in the fenced writers does.
# --------------------------------------------------------------------------
def test_each_named_system_row_carries_the_marker() -> None:
    for path, fn, action in SYSTEM_CALLS:
        tree = ast.parse(_source(path))
        [call] = [
            c
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == fn
            for c in ast.walk(n)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == "record" and action_of(c) == action
        ]
        assert has_system_marker(call), (path, fn, action)


def _system_row(src: str, action: str) -> ast.Call:
    """The ``AuditEvent`` call of the named system row with *action*."""
    [call] = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "record" and action_of(n) == action
    ]
    return call.args[0]


def mutant_unmarked(src: str) -> str:
    """M1: the ``scheduler_started`` row loses its marker."""
    event = _system_row(src, "scheduler_started")
    [kw] = [k for k in event.keywords if k.arg == "system"]
    lines = _lines(src)
    i = kw.value.lineno - 1
    lines[i] = lines[i].replace("system=True,", "", 1)
    return "".join(lines)


def mutant_marked_tenant_row(src: str) -> str:
    """M2: a tenant row in the guard gains the marker."""
    call = _first_guarded_call(src)
    event = call.args[0]
    lines = _lines(src)
    i, col = event.func.end_lineno - 1, event.func.end_col_offset + 1
    assert lines[i][col - 1] == "("
    lines[i] = lines[i][:col] + "system=True, " + lines[i][col:]
    return "".join(lines)


MARKER_MUTANTS = {
    "M1 a system row with no marker": (
        _SCHEDULER, mutant_unmarked, "a system row with no system=True marker"),
    "M2 a tenant row with the marker": (
        _GMAIL, mutant_marked_tenant_row, "system= outside a named system row"),
    "M3 a false marker on a system row": (
        _SCHEDULER, lambda s: s.replace("payload={}, system=True,",
                                        "payload={}, system=bool(1),", 1),
        "system= outside a named system row"),
}


@pytest.mark.parametrize("label", sorted(MARKER_MUTANTS))
def test_the_fence_catches_each_marker_mutant(label: str) -> None:
    path, make, reason = MARKER_MUTANTS[label]
    src = _source(path)
    mutant = make(src)
    assert mutant != src, f"{label}: the mutant did not change the source"
    found = audit_fence_violations(mutant, path)
    assert any(reason in v for v in found), f"{label}: expected {reason!r} in {found}"


# ==========================================================================
# 5. The system marker across the tree (task 2221 slice 3). ``record`` takes a
#    tenant-less event under the operator org only when it carries
#    ``system=True``. So the marker is the one way past the refusal, and it
#    may sit only on a row that ``SYSTEM_CALLS`` names.
# ==========================================================================
#: The roots that hold the audit writers.
MARKER_ROOTS = ("apps", "packages", "scripts")
#: Folders that hold no writer of ours.
_SKIP_PARTS = {"node_modules", ".venv", "__pycache__", "tests", ".next"}
#: The names that write an attribute by a string.
_SETATTR_NAMES = {"setattr", "delattr", "__setattr__", "__delattr__"}
#: The names that copy a dataclass with a changed field.
_COPY_NAMES = {"replace", "__replace__", "evolve"}
#: The names that make a class at run time.
_CLASS_MAKERS = {"type", "make_dataclass", "new_class"}

_SETATTR = "a setattr of system"
_COPY = "a copy with system="
_DICT_KEY = "a dict key system in an audit file"
_UNPACK = "an unpack into AuditEvent"
_POSITIONAL = "a positional system argument to AuditEvent"
_SUBCLASS = "a subclass of AuditEvent"
#: The texts that a rule on ``system`` needs: ``.system`` (a comment or a
#: line break may sit after the dot), the string ``"system"``, or
#: ``system=``. A file with none of them and no ``AuditEvent`` is not parsed.
_MAY_MARK = re.compile(r"""\.(?:\s|\\|#[^\n]*)*system\b|["']system["']|\bsystem\s*=""")


def _system_slot() -> int:
    """The position of ``system`` in the fields of the real ``AuditEvent``."""
    import dataclasses

    from acb_audit import AuditEvent

    return [f.name for f in dataclasses.fields(AuditEvent)].index("system")


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_event(node: ast.AST, names: set[str]) -> bool:
    """*node* names the event class: a known name, or ``<x>.AuditEvent``."""
    return ((isinstance(node, ast.Name) and node.id in names)
            or (isinstance(node, ast.Attribute) and node.attr == "AuditEvent"))


def _event_names(nodes: list[ast.AST]) -> set[str]:
    """``AuditEvent`` and each local name for it among *nodes*.

    An alias comes from ``from acb_audit... import AuditEvent as X``, or from
    a plain ``X = <an event name>`` at any depth.
    """
    names = {"AuditEvent"}
    for node in nodes:
        if (isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "acb_audit"):
            names |= {a.asname for a in node.names if a.name == "AuditEvent" and a.asname}
    assigns = [n for n in nodes if isinstance(n, ast.Assign)]
    grew = True
    while grew:
        grew = False
        for node in assigns:
            if _is_event(node.value, names):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id not in names:
                        names.add(t.id)
                        grew = True
    return names


def _call_violations(node: ast.Call, names: set[str], slot: int) -> list[str]:
    """The rules for one call. They hold in every file of the roots."""
    out: list[str] = []
    name = _called_name(node.func)
    if name in _SETATTR_NAMES and any(
            isinstance(a, ast.Constant) and a.value == "system" for a in node.args):
        out.append(_SETATTR)
    if name in _COPY_NAMES and any(kw.arg == "system" for kw in node.keywords):
        out.append(_COPY)
    if _is_event(node.func, names):
        if (any(isinstance(a, ast.Starred) for a in node.args)
                or any(kw.arg is None for kw in node.keywords)):
            out.append(_UNPACK)
        if len(node.args) > slot:
            out.append(_POSITIONAL)
    if name in _CLASS_MAKERS and any(
            _is_event(n, names) for a in [*node.args, *node.keywords]
            for n in ast.walk(a)):
        out.append(_SUBCLASS)
    return out


def marker_violations(src: str, path: str) -> list[str]:
    """Each way to set the marker in *src* that is not a named system row.

    Two groups of rules. The first group holds in every file, because each
    shape names the marker or the class itself:

    * a write to an attribute named ``system``, on any object;
    * ``setattr``, ``delattr``, ``__setattr__`` or ``__delattr__`` with the
      constant ``"system"``, as ``object.__setattr__(e, "system", True)``;
    * ``replace``, ``__replace__`` or ``evolve`` with ``system=``;
    * a ``*`` or ``**`` unpack into ``AuditEvent``, because no reader can
      see the keys. Pass each field by name;
    * a positional argument in the ``system`` slot of ``AuditEvent``;
    * a class with ``AuditEvent`` as a base, or a ``type`` or
      ``make_dataclass`` call that names it. A subclass can change the
      default of the marker.

    The second group holds only in a file that names ``acb_audit`` or
    ``AuditEvent``: a ``system=`` keyword on any call, and a dict literal
    with the key ``"system"``. A model call passes ``system=`` as its prompt
    in many other files, so these rules stay narrow.

    Limits, by design. The fence catches an honest marker write and the
    shapes of its self-tests. It does not prove the code against a hostile
    author. Review catches these known shapes, and the refusal in
    ``record`` and the R8 suites are the next layers:

    * a name built at run time (``"sys" + "tem"``), an event class reached
      by ``type(e)`` in a file that names neither token, and an edit of
      ``AuditEvent.__init__.__defaults__``;
    * a spelling that the parser joins or decodes but the text prefilter
      does not see: ``"sys" "tem"`` (two string parts), ``"\\x73ystem"``
      (an escape), the word spelled in full-width letters (NFKC), and a comment
      between ``system`` and ``=``;
    * a setter through an alias (``f = setattr``), through
      ``getattr(e, "__setattr__")``, and ``replace(e, **{"system": ...})``
      in a file that names no audit token.

    ``_MAY_MARK`` is a text prefilter. It skips a file whose text holds
    neither ``AuditEvent`` nor ``system`` as an attribute, a string or a
    keyword. That keeps the tree scan fast. It is not exact: the spellings
    in the second list pass it.
    """
    if "AuditEvent" not in src and not _MAY_MARK.search(src):
        return []
    tree = ast.parse(src)
    audit_file = "acb_audit" in src or "AuditEvent" in src
    # One pass over the tree: each node, and the parent of each node.
    nodes: list[ast.AST] = [tree]
    parents: dict[ast.AST, ast.AST] = {}
    for node in nodes:
        for child in ast.iter_child_nodes(node):
            parents[child] = node
            nodes.append(child)
    names = _event_names(nodes) if "AuditEvent" in src else {"AuditEvent"}
    slot = _system_slot()
    top = {id(n) for n in tree.body if isinstance(n, _FUNCTIONS)}
    out: list[str] = []
    for node in nodes:
        why: list[str] = []
        if (audit_file and isinstance(node, ast.keyword) and node.arg == "system"
                and not _marker_ok(node, parents, top, path)):
            why.append("system= outside a named system row")
        elif (isinstance(node, ast.Attribute) and node.attr == "system"
                and isinstance(node.ctx, ast.Store | ast.Del)):
            why.append("a write to .system")
        elif isinstance(node, ast.Call):
            why += _call_violations(node, names, slot)
        elif audit_file and isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "system" for k in node.keys):
            why.append(_DICT_KEY)
        elif isinstance(node, ast.ClassDef) and any(_is_event(b, names) for b in node.bases):
            why.append(_SUBCLASS)
        line = getattr(node, "lineno", None) or getattr(parents.get(node), "lineno", "?")
        out += [f"{path}:{line}: {w}. {_MARKER}" for w in why]
    return out


def _marker_files(root: Path = REPO) -> list[str]:
    """Each Python file under the roots, as a path relative to *root*.

    The scan reads every file, not only the files that name ``acb_audit``.
    A helper that gets an event as an argument names neither token, and it
    can still set the marker.
    """
    found: list[str] = []
    for top in MARKER_ROOTS:
        for f in (root / top).rglob("*.py"):
            if _SKIP_PARTS & set(f.relative_to(root).parts):
                continue
            found.append(f.relative_to(root).as_posix())
    return sorted(found)


def marker_scan(root: Path = REPO) -> list[str]:
    """Each marker violation in the files under *root*."""
    return [
        v for f in _marker_files(root)
        for v in marker_violations(
            (root / f).read_text(encoding="utf-8-sig", errors="replace"), f)
    ]


def test_the_marker_sits_only_on_the_named_system_rows() -> None:
    files = _marker_files()
    assert "apps/services/ingestion/ingestion/scheduler.py" in files
    assert len(files) > 500, f"the scan found too few files: {len(files)}"
    out = marker_scan()
    assert out == [], "\n".join(out)


#: One source per shape, and a part of the message of its own rule.
_DEF = "def _other(e: AuditEvent) -> None:\n"
_ROW = '"a", "b", "c", {}, None, None, None, True'
MARKER_SHAPES = {
    "a keyword on a tenant row": (None, "system= outside a named system row"),
    "an attribute write": (_DEF + "    e.system = True\n", "a write to .system"),
    "an attribute delete": (_DEF + "    del e.system\n", "a write to .system"),
    "setattr": (_DEF + '    setattr(e, "system", True)\n', _SETATTR),
    "object.__setattr__": (_DEF + '    object.__setattr__(e, "system", True)\n', _SETATTR),
    "a bound __setattr__": (_DEF + '    e.__setattr__("system", True)\n', _SETATTR),
    "dataclasses.replace": (
        "import dataclasses\n\n\n" + _DEF
        + "    dataclasses.replace(e, system=True)\n", _COPY),
    "a dict literal unpack": (
        _DEF + '    AuditEvent(**{"actor": "a", "action": "b", "target": "c", '
        '"system": True})\n', _UNPACK),
    "a dict literal that a name unpacks": (
        _DEF + '    kw = {"actor": "a", "action": "b", "target": "c", "system": True}\n'
        "    AuditEvent(**kw)\n", _DICT_KEY),
    "a dict() call that a name unpacks": (
        _DEF + '    kw = dict(actor="a", action="b", target="c", system=True)\n'
        "    AuditEvent(**kw)\n", _UNPACK),
    "a star unpack": (_DEF + "    AuditEvent(*e.args)\n", _UNPACK),
    "a positional argument in the slot": (
        _DEF + f"    AuditEvent({_ROW})\n", _POSITIONAL),
    "a positional argument through an alias": (
        "from acb_audit import AuditEvent as _AE\n\n\n" + _DEF
        + f"    _AE({_ROW})\n", _POSITIONAL),
    "a positional argument through a module": (
        "import acb_audit\n\n\n" + _DEF
        + f"    acb_audit.AuditEvent({_ROW})\n", _POSITIONAL),
    "a dataclass subclass with a default": (
        "import dataclasses\n\n\n@dataclasses.dataclass(slots=True)\n"
        "class _Sys(AuditEvent):\n    system: bool = True\n", _SUBCLASS),
    "a subclass of an assigned alias": (
        "_Base = AuditEvent\n\n\nclass _Sys(_Base):\n    pass\n", _SUBCLASS),
    "a class made by type()": (
        '_Sys = type("_Sys", (AuditEvent,), {})\n', _SUBCLASS),
}


@pytest.mark.parametrize("label", sorted(MARKER_SHAPES))
def test_the_tree_scan_catches_each_marker_shape(label: str) -> None:
    src = _source(_GMAIL)
    assert marker_violations(src, _GMAIL) == []
    code, reason = MARKER_SHAPES[label]
    bad = mutant_marked_tenant_row(src) if code is None else _add(src, code)
    found = marker_violations(bad, _GMAIL)
    assert any(reason in v for v in found), f"{label}: expected {reason!r} in {found}"


def test_the_slot_rule_reads_the_real_field_order() -> None:
    """Seven positional arguments stop before the slot, so they pass."""
    assert _system_slot() == 7
    ok = _add(_source(_GMAIL), _DEF + '    AuditEvent("a", "b", "c", {}, None, None, "org")\n')
    assert not [v for v in marker_violations(ok, _GMAIL) if _POSITIONAL in v]


def test_the_tree_scan_reads_a_file_that_names_no_audit_token(tmp_path: Path) -> None:
    """A helper that gets the event as an argument names neither token."""
    helpers = {
        "apps/svc/helper_attr.py": "def mark(e):\n    e.system = True\n",
        "packages/pkg/helper_setattr.py":
            'def mark(e):\n    object.__setattr__(e, "system", True)\n',
        "scripts/helper_copy.py":
            "import dataclasses\n\n\ndef mark(e):\n"
            "    return dataclasses.replace(e, system=True)\n",
    }
    clean = {
        # A model call passes a system prompt. No audit token: not a marker.
        "apps/svc/model_call.py": 'def ask(client):\n    client.create(system="be brief")\n',
        "apps/svc/node_modules/x.py": "def mark(e):\n    e.system = True\n",
        "apps/svc/tests/test_x.py": "def mark(e):\n    e.system = True\n",
    }
    for rel, code in {**helpers, **clean}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(code, encoding="utf-8")
    joined = "".join(helpers.values())
    assert "acb_audit" not in joined and "AuditEvent" not in joined
    found = marker_scan(tmp_path)
    for rel in helpers:
        assert any(v.startswith(rel + ":") for v in found), f"{rel} was not scanned: {found}"
    for rel in clean:
        assert not any(v.startswith(rel + ":") for v in found), f"{rel}: {found}"
