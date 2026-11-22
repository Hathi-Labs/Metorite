"""Task 3844 — the notes keys are per org (the expand half, R6).

Migration 125 made ``owner_email`` alone the primary key of ``copilot_config``
(``copilot_config_pkey``). Migration 99 made ``(user_id, lower(term))`` unique
over all orgs on ``notes_glossary`` (``notes_glossary_user_term_idx``). FORCE
RLS hides the row of another org, but a unique index does not. So the insert
of org B fails when org A holds the key, and the error tells org B that
another org holds it.

The fix is an expand/contract pair (R6), because the deploy applies
migrations before it restarts the services:

* **Expand (the migration ``*_notes_keys_per_org.sql``, this release).** It
  adds ``copilot_config_org_owner_key`` on ``(organization_id, owner_email)``
  and ``notes_glossary_org_user_term_idx`` on
  ``(organization_id, user_id, lower(term))``. It keeps the old keys. The
  three writers name the new indexes as their conflict targets. A same-org
  write updates the row. A write of a key that another org holds still fails,
  on the old key.
* **Contract (a later release, task 3886).** It moves the primary key of
  ``copilot_config`` from ``owner_email`` to ``copilot_config_org_owner_key``
  (``PRIMARY KEY USING INDEX``, as PR 2719 does for ``agent_avatars``), so
  the table never has no primary key. It removes
  ``notes_glossary_user_term_idx``, and ``notes_glossary`` keeps its ``id``
  primary key. Then each org keeps its own row for one key, and the two
  strict xfails in ``test_notes_tenant_isolation_r8.py`` come out.

The DB tests run on a REAL, phase-4-promoted catalog, as the NOSUPERUSER
NOBYPASSRLS app role, through the real route functions and the real
``acb_common.db.tenant_session`` (``bind_tenant`` binds the org, as the auth
dependency does). The contract test drops the old keys in the module's
promoted database, and restores them at the end.

Mutations this suite catches (R7):

* a new index removed from the migration: the same-org write fails, because
  no unique rule matches the conflict target;
* a writer back to its old conflict target: the hermetic source fence goes
  red, and the write after the contract drop fails, because no unique rule on
  the old columns remains;
* an old key dropped in the expand migration: the pin of the cross-org
  failure goes red, and the old SQL strings fail.

Run (a PRIVATE ladder database, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<your_db> \\
        uv run pytest tests/unit/test_notes_keys_per_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit import _r8_lists as r8_lists

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]

_CFG_NEW = "copilot_config_org_owner_key"
_CFG_OLD = "copilot_config_pkey"
#: The primary key name that the contract release gives copilot_config.
_CFG_CONTRACT_PK = "copilot_config_org_owner_pkey"
_GLS_NEW = "notes_glossary_org_user_term_idx"
_GLS_OLD = "notes_glossary_user_term_idx"

#: The expand file, found by CONTENT, never by number (R1).
_EXPAND = sorted(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if f"CREATE UNIQUE INDEX IF NOT EXISTS {_CFG_NEW}" in p.read_text(encoding="utf-8")
)

TAG = uuid.uuid4().hex[:8]
_DOMAIN = f"{TAG}.k3844.test"
_LIKE = f"%@{_DOMAIN}"

#: The SQL of the OLD gateway, word for word. It runs during a deploy, after
#: the migration and before the restart, so it must still work.
_OLD_CFG_SQL = (
    "INSERT INTO copilot_config (owner_email, instructions) "
    "VALUES (:e, :i) ON CONFLICT (owner_email) DO UPDATE "
    "SET instructions = EXCLUDED.instructions, updated_at = now()"
)
_OLD_GLS_SQL = (
    "INSERT INTO notes_glossary (user_id, term) VALUES (:u, :t) "
    "ON CONFLICT (user_id, lower(term)) DO UPDATE SET term=EXCLUDED.term "
    "RETURNING id, term"
)


def _email(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:6]}@{_DOMAIN}"


# ==========================================================================
# 1. Hermetic: the migration and the writers. No database.
# ==========================================================================
def test_the_expand_migration_is_found_and_keeps_the_old_keys() -> None:
    """One file adds both new indexes. It drops no old key, because the old
    gateway's conflict targets need them until the restart (R6)."""
    assert len(_EXPAND) == 1, _EXPAND
    body = _EXPAND[0].read_text(encoding="utf-8")
    assert f"CREATE UNIQUE INDEX IF NOT EXISTS {_GLS_NEW}" in body
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("--"))
    assert not re.search(r"\bDROP\b", code, re.I), "the expand file drops something"


#: The writers of each table, and the old conflict target that they must not
#: name. The contract release drops the unique rule that each one needs.
_OLD_TARGETS = {
    "copilot_config": re.compile(r"ON\s+CONFLICT\s*\(\s*owner_email\s*\)", re.I),
    "notes_glossary": re.compile(
        r"ON\s+CONFLICT\s*\(\s*user_id\s*,\s*lower\s*\(\s*term\s*\)\s*\)", re.I),
}


@pytest.mark.parametrize("table", sorted(_OLD_TARGETS))
def test_no_notes_writer_names_an_old_conflict_target(table: str) -> None:
    """Each file under ``apps/`` and ``packages/`` that inserts into the table
    names no old conflict target. The contract release drops the old key, and
    such a clause would then fail on each write."""
    insert = re.compile(rf"INSERT\s+INTO\s+{table}\s*\(", re.I)
    writers, offenders = [], []
    for top in ("apps", "packages"):
        for path in sorted((_ROOT / top).rglob("*.py")):
            if "node_modules" in path.parts:
                continue
            body = path.read_text(encoding="utf-8")
            if not insert.search(body):
                continue
            writers.append(path.relative_to(_ROOT).as_posix())
            if _OLD_TARGETS[table].search(body):
                offenders.append(path.relative_to(_ROOT).as_posix())
    assert writers, f"no writer of {table} found: the scan is broken"
    assert offenders == [], offenders


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    assert "tests/unit/test_notes_keys_per_org_r8.py" in r8_lists.must_run()


# ==========================================================================
# 2. R8: the real catalog, the app role, the real routes.
# ==========================================================================
@pytest.fixture(scope="module")
def keys_db(promoted) -> Iterator[Any]:  # noqa: F811
    """The promoted catalog. The rows of this module go at the end."""
    yield promoted
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM copilot_config WHERE owner_email LIKE :l"),
                  {"l": _LIKE})
        c.execute(text("DELETE FROM notes_glossary WHERE user_id LIKE :l"),
                  {"l": _LIKE})


@pytest.fixture
def as_app(keys_db, monkeypatch) -> Iterator[Any]:
    """``acb_common.db.tenant_session`` opens as the app role, one fresh
    backend each (no pool, so no connection crosses an event loop)."""
    from acb_common import db as common_db
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    url = keys_db.app_url.set(drivername="postgresql+asyncpg")
    eng = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(common_db, "get_session_factory", lambda: factory)
    yield keys_db


@contextmanager
def _as_tenant(org: str) -> Iterator[None]:
    """Bind *org* the way the auth dependency does."""
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _user(email: str, org: str) -> Any:
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _write_instructions(email: str, org: str, value: str) -> None:
    from gateway.routes.notes.copilot_agenda import (
        InstructionsRequest,
        write_instructions,
    )

    with _as_tenant(org):
        asyncio.run(write_instructions(InstructionsRequest(instructions=value),
                                       user=_user(email, org)))


def _put_settings(email: str, org: str, value: str) -> None:
    from gateway.routes.notes.settings import NotesSettings, put_settings

    with _as_tenant(org):
        asyncio.run(put_settings(NotesSettings(copilot_instructions=value),
                                 user=_user(email, org)))


def _add_term(email: str, org: str, term: str) -> Any:
    from gateway.routes.notes.glossary import AddTermRequest, add_term

    with _as_tenant(org):
        return asyncio.run(add_term(AddTermRequest(term=term), user=_user(email, org)))


def _cfg_rows(db, email: str) -> dict[str, str | None]:
    """Every settings row for *email*, as the owner sees it: ``{org: text}``."""
    with db.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text AS org, instructions FROM copilot_config "
            "WHERE owner_email = :e"), {"e": email}).all()
    return {r.org: r.instructions for r in rows}


def _gls_rows(db, email: str) -> dict[str, list[str]]:
    """Every glossary term for *email*, as the owner sees it: ``{org: terms}``."""
    with db.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text AS org, term FROM notes_glossary "
            "WHERE user_id = :u ORDER BY term"), {"u": email}).all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r.org, []).append(r.term)
    return out


def _index_def(db, name: str) -> str | None:
    with db.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
            {"n": name}).scalar()


# ── Expand: both keys exist ─────────────────────────────────────────────────

@_DB_GATE
def test_the_expand_schema_holds_both_keys(keys_db) -> None:
    """The new per-org indexes exist, and the old keys stay, so the old
    gateway's conflict targets still work during a deploy."""
    cfg_new = _index_def(keys_db, _CFG_NEW)
    gls_new = _index_def(keys_db, _GLS_NEW)
    assert cfg_new is not None and "UNIQUE" in cfg_new
    assert cfg_new.endswith("(organization_id, owner_email)"), cfg_new
    assert gls_new is not None and "UNIQUE" in gls_new
    assert gls_new.endswith("(organization_id, user_id, lower(term))"), gls_new
    assert _index_def(keys_db, _CFG_OLD).endswith("(owner_email)")
    assert _index_def(keys_db, _GLS_OLD).endswith("(user_id, lower(term))")


@_DB_GATE
def test_a_same_org_settings_write_updates_through_the_new_key(as_app) -> None:
    """Both settings writers update one row in one org. Without the index, the
    conflict target matches no unique rule and each write fails."""
    a = as_app.org_a
    email = _email("ana")
    _write_instructions(email, a, "first")
    _write_instructions(email, a, "second")
    assert _cfg_rows(as_app, email) == {a: "second"}
    _put_settings(email, a, "third")
    _put_settings(email, a, "fourth")
    assert _cfg_rows(as_app, email) == {a: "fourth"}


@_DB_GATE
def test_a_same_org_glossary_add_is_idempotent_through_the_new_key(as_app) -> None:
    """A second add of one term, in another case, returns the same row."""
    a = as_app.org_a
    email = _email("ana")
    first = _add_term(email, a, "Kubernetes")
    second = _add_term(email, a, "kubernetes")
    assert first.id == second.id
    assert _gls_rows(as_app, email) == {a: ["kubernetes"]}


@_DB_GATE
def test_the_old_conflict_targets_still_resolve(as_app) -> None:
    """The OLD gateway's SQL, word for word, as the app role bound to org A.
    It runs between the migration and the restart, so it must not fail."""
    a = as_app.org_a
    email = _email("old")
    url = as_app.app_url
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    eng = create_engine(url, future=True, poolclass=NullPool)
    try:
        for value, term in (("one", "Grafana"), ("two", "grafana")):
            with eng.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": a})
                c.execute(text(_OLD_CFG_SQL), {"e": email, "i": value})
                c.execute(text(_OLD_GLS_SQL), {"u": email, "t": term})
    finally:
        eng.dispose()
    assert _cfg_rows(as_app, email) == {a: "two"}
    assert _gls_rows(as_app, email) == {a: ["grafana"]}


@_DB_GATE
def test_a_key_another_org_holds_still_fails_on_the_old_key(as_app) -> None:
    """Pins the EXPAND behaviour on purpose. Org B's write does not match org
    A's row on the new index, so it inserts, and the old key refuses it. The
    contract release flips this case. Org A's row stays as it was."""
    a, b = as_app.org_a, as_app.org_b
    email = _email("shared")
    _write_instructions(email, a, "a-value")
    _add_term(email, a, "Shared")

    with pytest.raises(IntegrityError) as cfg_err:
        _write_instructions(email, b, "b-value")
    assert _CFG_OLD in str(cfg_err.value)

    with pytest.raises(IntegrityError) as gls_err:
        _add_term(email, b, "shared")
    assert _GLS_OLD in str(gls_err.value)

    assert _cfg_rows(as_app, email) == {a: "a-value"}
    assert _gls_rows(as_app, email) == {a: ["Shared"]}


# ── Contract: the old keys are gone ─────────────────────────────────────────

@pytest.fixture
def contracted(as_app) -> Iterator[Any]:
    """The contract state, as the later release (task 3886) makes it: the
    primary key of copilot_config moves to the per-org index, and the old
    glossary key goes. Restored at the end, after the rows that share a key go."""
    with as_app.admin_engine.begin() as c:
        c.execute(text(f"ALTER TABLE copilot_config DROP CONSTRAINT {_CFG_OLD}"))
        c.execute(text(f"ALTER TABLE copilot_config ADD CONSTRAINT {_CFG_CONTRACT_PK} "
                       f"PRIMARY KEY USING INDEX {_CFG_NEW}"))
        c.execute(text(f"DROP INDEX {_GLS_OLD}"))
    try:
        yield as_app
    finally:
        with as_app.admin_engine.begin() as c:
            c.execute(text("DELETE FROM copilot_config WHERE owner_email LIKE :l"),
                      {"l": _LIKE})
            c.execute(text("DELETE FROM notes_glossary WHERE user_id LIKE :l"),
                      {"l": _LIKE})
            c.execute(text(f"ALTER TABLE copilot_config DROP CONSTRAINT {_CFG_CONTRACT_PK}"))
            c.execute(text(f"CREATE UNIQUE INDEX {_CFG_NEW} "
                           "ON copilot_config (organization_id, owner_email)"))
            c.execute(text(f"ALTER TABLE copilot_config ADD CONSTRAINT {_CFG_OLD} "
                           "PRIMARY KEY (owner_email)"))
            c.execute(text(f"CREATE UNIQUE INDEX {_GLS_OLD} "
                           "ON notes_glossary (user_id, lower(term))"))


@_DB_GATE
def test_after_the_contract_each_org_keeps_its_own_row(contracted) -> None:
    db = contracted
    a, b = db.org_a, db.org_b
    email = _email("both")

    # The contract never leaves copilot_config with no primary key.
    with db.admin_engine.connect() as c:
        pk = c.execute(text(
            "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i "
            "WHERE i.indrelid = 'copilot_config'::regclass AND i.indisprimary"
        )).scalar()
    assert pk is not None and "organization_id" in pk and "owner_email" in pk, (
        "the contract leaves copilot_config with no primary key on "
        f"(organization_id, owner_email): {pk!r}")

    # Each org writes one key, and a second write of each org updates its row.
    _write_instructions(email, a, "a-value")
    _write_instructions(email, b, "b-value")
    _put_settings(email, b, "b-second")
    assert _cfg_rows(db, email) == {a: "a-value", b: "b-second"}

    # Each org holds the same glossary term, and a re-add stays in its org.
    a_term = _add_term(email, a, "Shared")
    b_term = _add_term(email, b, "shared")
    b_again = _add_term(email, b, "SHARED")
    assert a_term.id != b_term.id
    assert b_term.id == b_again.id
    assert _gls_rows(db, email) == {a: ["Shared"], b: ["SHARED"]}
