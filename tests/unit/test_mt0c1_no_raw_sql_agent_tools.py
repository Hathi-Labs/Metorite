"""MT-0c-1 — no agent tool accepts SQL.

``saas_multitenancy.md`` §0.9.3 states this as a **condition on the pooled
tenancy decision**, not a preference:

    "No agent ever gets a raw-SQL tool, and no agent-reachable code path can set
     ``app.tenant_id``. … If either of those is violated, pooled tenancy is not
     defensible and §1 should be re-taken."

The condition was already violated when it was written. ``query_history`` took a
model-generated SQL string and executed it via ``acb_graph.get_session()`` —
registered in ``apps/agents/agent-orchestrator/config.json``, injected at
``orchestrator/_tool_injection.py:623``, and advertised to the model in
``addendum.py`` as "Run a SELECT-only SQL query".

Its guard was a keyword-substring check and was wrong in **both** directions,
measured 2026-08-08:

* ``SELECT role, content, created_at FROM chat_message`` — the tool's own
  documented example — was **rejected**, because ``CREATED_AT`` contains
  ``CREATE``;
* ``SELECT * FROM provider_keys`` passed **cleanly**. The guard constrained
  verbs; nothing constrained tables.

This module pins the replacement and, more importantly, pins that the shape
cannot come back: :func:`test_no_agent_tool_accepts_a_sql_parameter` is a
build-failing ratchet in the spirit of ``test_db_engine_seam.py``.

Spec: ``saas_multitenancy.md`` §0.9.3 / MT-0c-1 · WS-29 · D16.
"""
from __future__ import annotations

import inspect

import acb_skills
import pytest
from acb_skills.history_tools import query_history

# --------------------------------------------------------------------------
# The ratchet: the shape must not return.
# --------------------------------------------------------------------------
#: Parameter names that mean "the model writes the query". A tool taking one of
#: these hands query composition to a language model reading untrusted email.
_SQL_PARAM_NAMES = {"sql", "query_sql", "statement", "stmt", "select", "where_clause"}


def _agent_tools() -> list:
    """Every callable ``acb_skills`` exports as an agent tool."""
    return [
        getattr(acb_skills, name)
        for name in getattr(acb_skills, "__all__", [])
        if callable(getattr(acb_skills, name, None))
    ]


def test_no_agent_tool_accepts_a_sql_parameter() -> None:
    """Build-failing ratchet — a new tool taking SQL fails here, not in prod."""
    offenders: list[str] = []
    for fn in _agent_tools():
        try:
            params = set(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            continue
        hit = params & _SQL_PARAM_NAMES
        if hit:
            offenders.append(f"{getattr(fn, '__name__', fn)}{sorted(hit)}")
    assert not offenders, (
        "agent tools must never accept SQL — the model composes the value, not "
        f"the syntax (saas_multitenancy.md §0.9.3): {offenders}"
    )


def test_query_history_takes_criteria_not_sql() -> None:
    params = inspect.signature(query_history).parameters
    assert "query" not in params, "query_history still takes a SQL string"
    assert {"search", "thread_id", "agent_name", "since_days", "limit"} <= set(params)


def test_history_tool_holds_no_model_supplied_table_name() -> None:
    """The two reachable tables are literals in the module, not arguments.

    A table name that arrives as data is exactly the hole the old guard left:
    it policed verbs and never touched the FROM clause.
    """
    import ast

    import acb_skills.history_tools as ht

    src = inspect.getsource(ht)
    assert "FROM chat_message" in src and "JOIN chat_session" in src

    # Check the CODE, not the prose — this module's docstrings quote the old
    # vulnerable queries on purpose, and that is documentation, not a statement
    # anyone can execute. Strip every docstring, then look at what is left.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                src = src.replace(doc, "")

    for banned in ("SELECT * FROM", '" + ', ".format(", "f'''", 'f"""'):
        assert banned not in src, f"history_tools composes SQL dynamically: {banned}"


def test_model_facing_description_no_longer_advertises_sql() -> None:
    """The addendum is what the model reads. If it still says "SELECT", the
    model will still try to send SQL — and get a confusing failure."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in ("packages/acb_skills/acb_skills/addendum.py",
                "packages/acb_skills/acb_skills/skill_families.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "SELECT-only" not in text, f"{rel} still advertises a SQL tool"


# --------------------------------------------------------------------------
# The old guard, reproduced — so the reason survives the fix.
# --------------------------------------------------------------------------
_OLD_DANGEROUS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                  "CREATE", "TRUNCATE", "EXEC", "EXECUTE"}


def _old_guard_rejects(q: str) -> bool:
    """The pre-MT-0c-1 check, verbatim."""
    if not q.strip().upper().startswith("SELECT"):
        return True
    return any(kw in q.upper() for kw in _OLD_DANGEROUS)


def test_the_old_guard_rejected_its_own_documented_example() -> None:
    """False positive: any query selecting ``created_at`` was refused."""
    assert _old_guard_rejects(
        "SELECT role, content, created_at FROM chat_message LIMIT 5"
    ), "if this passes, the false-positive analysis in the module docstring is wrong"


def test_the_old_guard_allowed_reading_the_credential_table() -> None:
    """False negative, and the one that mattered: verbs were policed, not tables."""
    assert not _old_guard_rejects("SELECT * FROM provider_keys"), (
        "if this is rejected, the false-negative analysis is wrong"
    )
    assert not _old_guard_rejects("SELECT * FROM email_messages")
    assert not _old_guard_rejects("SELECT * FROM app_user")


# --------------------------------------------------------------------------
# Behaviour of the replacement.
# --------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, rows, cols):
        self._rows, self._cols = rows, cols

    def fetchmany(self, n):
        return self._rows[:n]

    def keys(self):
        return self._cols


class _FakeSession:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self.sink["sql"] = str(stmt)
        self.sink["params"] = params
        return _FakeResult([("user", "hello", "2026-08-01", "t1", "orchestrator", "T")],
                           ["role", "content", "created_at", "thread_id",
                            "agent_name", "title"])


@pytest.fixture
def captured(monkeypatch):
    """A member acts, and the run opener yields a recording session.

    H-201: ``query_history`` opens its session through the executor opener,
    and it answers ``[]`` with no verified member. The R8 half is
    ``test_h201_readers_under_rls.py``.
    """
    import acb_skills.history_tools as ht
    from orchestrator import executor

    sink: dict = {}
    monkeypatch.setattr(ht, "_acting_user", lambda: "alice@example.test")
    monkeypatch.setattr(executor, "_graph_session_opener_current",
                        lambda: (lambda: _FakeSession(sink)))
    return sink


@pytest.mark.asyncio
async def test_criteria_are_bound_parameters_not_interpolated(captured) -> None:
    """A hostile 'search' term reaches the DB as a VALUE, never as syntax."""
    hostile = "'; DROP TABLE chat_message; --"

    out = await query_history(search=hostile)

    assert hostile not in captured["sql"], "the search term was interpolated into SQL"
    assert captured["params"]["search_like"] == f"%{hostile}%"
    assert out.startswith("[")


@pytest.mark.asyncio
async def test_limit_is_capped(captured) -> None:
    await query_history(limit=10_000)
    assert captured["params"]["limit"] == 20


@pytest.mark.asyncio
async def test_degenerate_arguments_do_not_raise(captured) -> None:
    for kwargs in ({}, {"limit": "abc"}, {"since_days": "x"}, {"search": "   "}):
        out = await query_history(**kwargs)  # type: ignore[arg-type]
        assert out.startswith("[")
