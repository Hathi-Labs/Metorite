"""WS-61 KC-3 — the Knowledge sync and the tenant: the R7 fences, hermetic.

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 and §19, and
``project-docs/specs/knowledge_base.md`` §7.1 ("No session is open during a
provider call"). The shape of ``test_crm_sync_tenancy.py``.

Three fences, each with a self-test that proves it is not vacuous:

1. **No provider call inside a session** (R7 fence
   ``kb-sync-no-provider-call-in-session``). An AST scan of each file of
   ``gateway/kb_sync/``: inside an ``async with`` block whose context calls
   ``tenant_session``, no call may reach the adapter (``discover``,
   ``list_changed``, ``changes_start_token``, ``fetch_text`` and the rest) or
   a helper that does (``_call``, ``_credential``, ``_flat``, ``_walk``).
   A session held across a Google call holds a pooled connection, and its row
   locks, for as long as Google takes.
2. **No ``.commit()`` inside a session.** A commit ends ``SET LOCAL``, so each
   statement after it runs with no tenant.
3. **One unbound read.** ``scheduler._organization_ids`` holds exactly one
   ``get_db()``, and no other function of the package calls ``get_db``.
4. **Each tenant table names the org** (R7 fence ``kb-sync-org-predicate``,
   verifier F4). Each ``*_SQL`` constant of ``gateway/kb_sync/`` that reads
   or writes ``kb_sources``, ``kb_source_nodes``, ``kb_sync_cursors`` or
   ``kb_sync_runs`` carries an ``organization_id`` predicate for EACH such
   reference: ``<alias>.organization_id =`` for an alias, and a bare
   ``organization_id =`` for a reference with no alias. An INSERT names
   ``CAST(:org AS uuid)``. Row level security is the first wall. This is the
   second, for a session with row level security off.

And one runtime fence: with ``KNOWLEDGE_SYNC`` empty, a tick of the loop opens
no session at all, on any opener.

The R8 half (org B reads, writes and runs nothing of org A) is
``test_kc_sync_r8.py``.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gateway.kb_sync.scheduler", reason="gateway not installed")

import acb_common.db as shared_db
from acb_common import get_settings
from gateway.kb_sync import scheduler

_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "apps/services/gateway/gateway/kb_sync"
_FILES = sorted(p for p in _PKG.glob("*.py") if p.name != "__init__.py")

#: A call with one of these names reaches the provider.
_PROVIDER_CALLS = frozenset({
    # The adapter protocol (kb_sources/base.py).
    "consent_url", "exchange_code", "refresh", "revoke", "account_identity",
    "discover", "changes_start_token", "list_changed", "fetch_text", "fetch_bytes",
    # The helpers of kb_sync that make a provider call.
    "_call", "_discover", "_credential", "_flat", "_walk", "fresh_credential",
    "relist", "bootstrap", "changes", "reconcile", "run_source",
})


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _session_blocks(tree: ast.AST) -> list[ast.AsyncWith]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncWith) and any(
            isinstance(item.context_expr, ast.Call)
            and _call_name(item.context_expr).endswith("tenant_session")
            for item in node.items)
    ]


def provider_calls_inside_sessions(source: str) -> list[tuple[int, str]]:
    """``(line, name)`` of each provider call inside a ``tenant_session`` block."""
    hits: list[tuple[int, str]] = []
    for block in _session_blocks(ast.parse(source)):
        for stmt in block.body:
            for inner in ast.walk(stmt):
                if isinstance(inner, ast.Call) and _call_name(inner) in _PROVIDER_CALLS:
                    hits.append((inner.lineno, _call_name(inner)))
    return hits


def commits_inside_sessions(source: str) -> list[int]:
    hits: list[int] = []
    for block in _session_blocks(ast.parse(source)):
        for stmt in block.body:
            for inner in ast.walk(stmt):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "commit"):
                    hits.append(inner.lineno)
    return hits


def test_the_scan_sees_the_package() -> None:
    names = {p.name for p in _FILES}
    assert {"discovery.py", "engine.py", "runs.py", "scheduler.py"} <= names, names
    # Each file that writes opens sessions, so the scan has blocks to read.
    for name in ("discovery.py", "engine.py", "scheduler.py"):
        tree = ast.parse((_PKG / name).read_text(encoding="utf-8"))
        assert _session_blocks(tree), f"{name} has no tenant_session block"


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_provider_call_inside_a_tenant_session(path: Path) -> None:
    """R7 fence ``kb-sync-no-provider-call-in-session``."""
    hits = provider_calls_inside_sessions(path.read_text(encoding="utf-8"))
    assert hits == [], (
        f"{path.name} calls the provider inside a tenant_session at {hits}. "
        "Close the session first: the call holds a pooled connection and its "
        "row locks for as long as the provider takes.")


def test_the_provider_fence_is_not_vacuous() -> None:
    planted = (
        "async def f(org, source, cred, t):\n"
        "    async with tenant_session(org) as db:\n"
        "        await db.execute(x)\n"
        "        page = await source.list_changed(cred, t, ())\n"
        "        await self._call(fn)\n"
        "    async with other() as db:\n"
        "        await source.discover(cred)\n"
        "    page = await source.list_changed(cred, t, ())\n"
    )
    assert provider_calls_inside_sessions(planted) == [(4, "list_changed"), (5, "_call")]


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_commit_inside_a_tenant_session(path: Path) -> None:
    hits = commits_inside_sessions(path.read_text(encoding="utf-8"))
    assert hits == [], f"{path.name} calls .commit() inside a tenant_session at {hits}"


def test_the_commit_fence_is_not_vacuous() -> None:
    planted = (
        "async def f(org):\n"
        "    async with tenant_session(org) as db:\n"
        "        await db.commit()\n"
        "    async with other() as db:\n"
        "        await db.commit()\n"
    )
    assert commits_inside_sessions(planted) == [3]


def _get_db_calls(tree: ast.AST) -> dict[str, int]:
    """The number of ``get_db()`` calls in each function of a module."""
    out: dict[str, int] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            n = sum(1 for c in ast.walk(fn)
                    if isinstance(c, ast.Call) and _call_name(c) == "get_db")
            if n:
                out[fn.name] = n
    return out


def test_one_unbound_read_and_only_in_the_scheduler() -> None:
    found = {p.name: _get_db_calls(ast.parse(p.read_text(encoding="utf-8"))) for p in _FILES}
    assert {k: v for k, v in found.items() if v} == {
        "scheduler.py": {"_organization_ids": 1}}, found


def test_the_get_db_count_is_not_vacuous() -> None:
    tree = ast.parse("async def a():\n    db = await get_db()\n    db2 = await get_db()\n")
    assert _get_db_calls(tree) == {"a": 2}


# ── Each tenant table names the org (R7 fence kb-sync-org-predicate) ────────

import re  # noqa: E402

_TENANT = r"(kb_sources|kb_source_nodes|kb_sync_cursors|kb_sync_runs)"
#: A read or a write of a tenant table, and its alias. SQL words are upper
#: case here, and an alias is lower case, so a keyword is never an alias.
_REF = re.compile(rf"\b(FROM|JOIN|UPDATE|DELETE FROM)\s+{_TENANT}\b(?:\s+(?:AS\s+)?([a-z]\w*))?")
_INSERT = re.compile(rf"\bINSERT INTO\s+{_TENANT}\b")
_BARE_PRED = re.compile(r"(?<![\w.])organization_id\s*=")
_ORG = "CAST(:org AS uuid)"


def org_predicate_findings(name: str, sql: str) -> list[str]:
    """What *sql* reads or writes of a tenant table with no org predicate."""
    out: list[str] = []
    refs: dict[str | None, int] = {}
    for m in _REF.finditer(sql):
        if m.group(1) == "UPDATE" and sql[max(0, m.start() - 3):m.start()] == "DO ":
            continue  # ON CONFLICT ... DO UPDATE SET names no table
        refs[m.group(3)] = refs.get(m.group(3), 0) + 1
    for alias, n in refs.items():
        if alias is None:
            preds = len(_BARE_PRED.findall(sql))
        else:
            preds = len(re.findall(rf"\b{re.escape(alias)}\.organization_id\s*=", sql))
        if preds < n:
            out.append(f"{name}: {n} reference(s) as {alias or 'no alias'} and "
                       f"{preds} organization_id predicate(s)")
    if _INSERT.search(sql) and _ORG not in sql:
        out.append(f"{name}: an INSERT that does not name {_ORG}")
    if (refs or _INSERT.search(sql)) and _ORG not in sql:
        out.append(f"{name}: no {_ORG} anywhere, so nothing binds the org of the caller")
    return out


def _sql_constants() -> dict[str, str]:
    from gateway.kb_sync import discovery, engine, runs

    out: dict[str, str] = {}
    for mod in (discovery, engine, runs, scheduler):
        for attr in dir(mod):
            value = getattr(mod, attr)
            if attr.endswith("_SQL") and isinstance(value, str):
                out[f"{mod.__name__.rsplit('.', 1)[-1]}.{attr}"] = value
    return out


def test_the_org_fence_sees_the_sql() -> None:
    names = _sql_constants()
    for must in ("runs.UPSERT_CURSOR_SQL", "engine._CASCADE_GONE_SQL", "engine._CAP_SQL",
                 "runs._PRUNE_SQL", "runs._DUE_SQL", "discovery.UPSERT_NODES_SQL"):
        assert must in names, must
    assert sum(1 for s in names.values() if re.search(_TENANT, s)) >= 25


def test_each_tenant_table_reference_names_the_org() -> None:
    """R7 fence ``kb-sync-org-predicate``."""
    findings = [f for name, sql in _sql_constants().items()
                for f in org_predicate_findings(name, sql)]
    assert findings == [], findings


@pytest.mark.parametrize(("sql", "found"), [
    # The mutation of verifier F4: the guard of the cursor write lost its org.
    ("INSERT INTO kb_sync_cursors (organization_id) SELECT CAST(:org AS uuid) "
     "WHERE EXISTS (SELECT 1 FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
     "FOR KEY SHARE OF s) ON CONFLICT (organization_id, kind) DO UPDATE SET x = 1", True),
    ("SELECT 1 FROM kb_sync_runs WHERE source_id = :s", True),
    ("DELETE FROM kb_sync_runs d WHERE d.source_id = :s AND organization_id = 1", True),
    ("UPDATE kb_source_nodes n SET state = 'gone' FROM kb_source_nodes c "
     "WHERE n.organization_id = CAST(:org AS uuid)", True),
    ("INSERT INTO kb_sync_runs (organization_id) VALUES (:org)", True),
    ("SELECT 1 FROM kb_sync_runs r WHERE r.organization_id = CAST(:org AS uuid)", False),
    ("UPDATE kb_sources SET x = 1 WHERE organization_id = CAST(:org AS uuid)", False),
    ("SELECT id FROM organization ORDER BY id", False),
])
def test_the_org_fence_is_not_vacuous(sql: str, found: bool) -> None:
    assert bool(org_predicate_findings("planted", sql)) is found


# ── With the flag off, a tick opens nothing ─────────────────────────────────


def _tripwire(opened: list[str], name: str) -> Any:
    def _open(*_a: Any, **_k: Any) -> Any:
        opened.append(name)
        raise AssertionError(f"{name} opened with KNOWLEDGE_SYNC empty")
    return _open


@pytest.mark.parametrize("knowledge_on", [True, False])
def test_the_flag_off_opens_no_session(monkeypatch: pytest.MonkeyPatch,
                                       knowledge_on: bool) -> None:
    settings = get_settings()
    for name, value in (("knowledge_enabled", knowledge_on), ("knowledge_orgs", "*"),
                        ("knowledge_sources", "*"), ("knowledge_sync", "")):
        monkeypatch.setattr(settings, name, value, raising=False)
    opened: list[str] = []
    monkeypatch.setattr(scheduler, "tenant_session", _tripwire(opened, "tenant_session"))
    monkeypatch.setattr(scheduler, "get_db", _tripwire(opened, "get_db"))
    monkeypatch.setattr(shared_db, "get_session_factory",
                        _tripwire(opened, "get_session_factory"))

    def no_adapter() -> Any:
        raise AssertionError("an adapter was built with the flag off")

    assert asyncio.run(scheduler.run_tick(no_adapter)) == 0
    assert asyncio.run(scheduler.start_kb_sync()) is False
    assert opened == [], opened


def test_knowledge_off_with_the_sync_list_set_opens_nothing(monkeypatch) -> None:
    """``KNOWLEDGE_SYNC`` alone does not wake the loop while Knowledge is off."""
    settings = get_settings()
    for name, value in (("knowledge_enabled", False), ("knowledge_orgs", "*"),
                        ("knowledge_sources", "*"), ("knowledge_sync", "*")):
        monkeypatch.setattr(settings, name, value, raising=False)
    opened: list[str] = []
    monkeypatch.setattr(scheduler, "tenant_session", _tripwire(opened, "tenant_session"))
    monkeypatch.setattr(scheduler, "get_db", _tripwire(opened, "get_db"))
    assert asyncio.run(scheduler.run_tick(lambda: None)) == 0
    assert opened == []
