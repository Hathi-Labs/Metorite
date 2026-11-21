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
and ``scheduler_started`` (job ids and cron triggers). They keep Option-A, and
this file proves that their payload stays free of tenant data. With the bind
off, every row stays as before, so the change is dark. A payload field never
gives the org (R5).

This file proves the skip, the system rows and the dark path with no
database, and it holds the AST source fence. The real table under FORCE RLS
is ``test_audit_tick_writers_org_r8.py`` (R8).
"""
from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import json
import uuid
from collections.abc import Callable
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
    from reconciler import main as reconciler_main

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


def spy_on_writers(mp, sink: Callable[[Any], None]) -> None:
    """Point the ``record`` name of each writer module at *sink*."""
    import importlib

    for name in WRITERS:
        mp.setattr(importlib.import_module(name), "record", sink)


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
# 4. The source fence (AST). Each audit write sits inside ``_audit``, or it
#    is one of the named Option-A system rows.
# ==========================================================================
#: The only names a writer here may import from ``acb_audit``, never renamed.
_IMPORTABLE = {"AuditEvent", "record", "refuse_tenantless"}
#: The names that write a row. ``_persist`` is never imported here.
_SINKS = {"record", "_persist"}
#: The Option-A system rows: (path, function, action). Nothing else may call
#: ``record`` outside ``_audit``.
SYSTEM_CALLS = {
    ("apps/services/ingestion/ingestion/scheduler.py", "_run_zoho", "run_ok"),
    ("apps/services/ingestion/ingestion/scheduler.py", "_serve", "scheduler_started"),
}
_GUARD = "if not refuse_tenantless(event):\n    record(event)"


def _action_of(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Call):
        for kw in call.args[0].keywords:
            if kw.arg == "action" and isinstance(kw.value, ast.Constant):
                return kw.value.value
    return None


def _import_violations(node: ast.AST, at: str) -> list[str]:
    """An import of ``acb_audit`` that is not a plain allowed name."""
    if isinstance(node, ast.Import):
        return [f"{at}: import {a.name}" for a in node.names
                if a.name.split(".")[0] == "acb_audit"]
    if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "acb_audit":
        return [
            f"{at}: from {node.module} import {a.name} as {a.asname}"
            for a in node.names
            if node.module != "acb_audit" or a.name not in _IMPORTABLE or a.asname
        ]
    return []


def _sink_violation(node: ast.Name, parents: dict[ast.AST, ast.AST], path: str,
                    at: str) -> str | None:
    """A use of ``record`` or ``_persist`` outside ``_audit`` and the system rows."""
    parent = parents.get(node)
    if not (isinstance(parent, ast.Call) and parent.func is node):
        return f"{at}: {node.id} used as a value, not called"
    fn, up = None, node
    while up in parents and fn is None:
        up = parents[up]
        if isinstance(up, ast.FunctionDef | ast.AsyncFunctionDef):
            fn = up.name
    if fn == "_audit" or (path, fn, _action_of(parent)) in SYSTEM_CALLS:
        return None
    return f"{at}: {node.id}() in {fn}, outside _audit"


def audit_fence_violations(src: str, path: str) -> list[str]:
    """Each way that *src* writes an audit row around the guard."""
    tree = ast.parse(src)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    out: list[str] = []
    for node in ast.walk(tree):
        at = f"{path}:{getattr(node, 'lineno', '?')}"
        out += _import_violations(node, at)
        if isinstance(node, ast.Attribute) and node.attr in _SINKS:
            out.append(f"{at}: .{node.attr} reaches the audit seam around _audit")
        elif isinstance(node, ast.Name) and node.id in _SINKS:
            found = _sink_violation(node, parents, path, at)
            out += [found] if found else []
        elif isinstance(node, ast.keyword) and node.arg == "organization_id":
            out.append(f"{at}: organization_id= on an audit row with no tenant source")
    audits = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_audit"]
    if len(audits) != 1:
        out.append(f"{path}: expected one _audit, found {len(audits)}")
    else:
        body = [s for s in audits[0].body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if [ast.unparse(s) for s in body] != [_GUARD]:
            out.append(f"{path}: _audit is not the refuse_tenantless guard")
    return out


def _source(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("path", sorted(WRITERS.values()))
def test_each_writer_records_only_through_the_guard(path: str) -> None:
    assert audit_fence_violations(_source(path), path) == []


def test_each_named_system_row_is_in_the_source() -> None:
    """The allow-list names real calls, so a stale entry fails."""
    for path, fn, action in SYSTEM_CALLS:
        tree = ast.parse(_source(path))
        found = [
            c
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == fn
            for c in ast.walk(n)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == "record" and _action_of(c) == action
        ]
        assert len(found) == 1, (path, fn, action)


_GMAIL = "apps/services/ingestion/ingestion/sources/gmail/webhook.py"
_IMPORT = "from acb_audit import AuditEvent, record, refuse_tenantless"
_CALL = "    _audit(\n        AuditEvent("


@pytest.mark.parametrize(
    ("label", "old", "new"),
    [
        ("aliased record", _IMPORT, _IMPORT + "\nfrom acb_audit import record as _write"),
        ("_persist import", _IMPORT, _IMPORT + "\nfrom acb_audit.log import _persist"),
        ("module import", _IMPORT, "import acb_audit\n" + _IMPORT),
        ("bare call", _CALL, "    record(\n        AuditEvent("),
        ("attribute call", _CALL, "    acb_audit.record(\n        AuditEvent("),
        ("value alias", _CALL, "    w = record\n    w(\n        AuditEvent("),
        ("no guard", "    if not refuse_tenantless(event):\n        record(event)",
         "    record(event)"),
    ],
)
def test_the_fence_catches_a_way_around_the_guard(label: str, old: str, new: str) -> None:
    src = _source(_GMAIL)
    assert old in src, label
    mutant = src.replace(old, new, 1)
    if label == "aliased record":
        mutant = mutant.replace(_CALL, "    _write(\n        AuditEvent(", 1)
    assert audit_fence_violations(mutant, _GMAIL), f"the fence missed: {label}"
