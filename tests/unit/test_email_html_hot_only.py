"""WS-17 EM-S3 — no writer stores the HTML of a cold message.

Spec: ``project-docs/specs/email_app_master_plan.md`` §14.4.3 and §14.6.3.
Owner decisions D-EM-48, D-EM-49 and D-EM-53.

With ``html_tier.hot_only()`` true, which needs BOTH ``EMAIL_HTML_FROM_PROVIDER``
and ``EMAIL_HTML_HOT_ONLY``, the four writers of ``body_html`` store no HTML
for a message older than the hot window:

1. the sync upsert (``persist.upsert_message``), through ``html_tier.drops_html``;
2. the body backfill (``body_backfill.write_bodies``);
3. the open (``transport/messages.py::get_message``);
4. ``core.hydrate_message_body``.

Writers 2 to 4 write ``html_tier.COLD_SAFE_HTML_SET``. No writer clears HTML
that a row holds. EM-S4 does that.

R7 fences named here, each a test class:

* ``email-hot-only-params`` (:class:`TestTheUpsertParams`). The upsert binds
  NULL HTML for a cold message, fills an empty text from the HTML, and binds
  the HTML as before for a hot message, a message with no date, and either
  flag alone.
* ``email-hot-only-helpers`` (:class:`TestTheHelpers`). ``drops_html`` and
  ``cold_before`` follow ``hot_only`` and ``is_cold``.
* ``email-hot-only-open`` (:class:`TestTheOpenAnswersTheHtml`). The open of a
  cold row answers the HTML that it fetched, binds the cutoff, and writes the
  HTML into the cache of the HTML route.
* ``email-hot-only-one-writer-set`` (:class:`TestOnlyTheNamedWritersWriteHtml`).
  No SQL text outside the named writers writes ``body_html``. Each of writers
  2 to 4 writes the cold-safe term, and nothing else, into ``body_html``.
  ``automation/drafting.py::_upsert_local_draft`` is allowed by name. It
  writes a draft row with ``received_at = now()``, so the row is hot, and
  today its SQL names no ``body_html``. ⚠️ Limit (R7): the scan reads string
  literals, f-strings and a ``+`` of the two. SQL that a module builds from a
  list of column names, as the SET of ``persist.py`` does, passes it. That
  module is a named writer.
* ``email-hot-only-r8`` (:class:`TestTheWritersOnARealDatabase`, R8). Each
  writer runs on the real catalog as the non-privileged role
  ``acb_app_h3rls``. The ``xmin`` cases of the #709 guard live in
  ``tests/unit/test_email_upsert_guard.py`` (:class:`TestColdHtml` there).

Mutations (§14.6.3). S3-M1 binds the HTML of a cold message in the upsert,
and the R8 INSERT case and the ``xmin`` case of the guard file fail. S3-M2
skips the text fill, and the HTML-only cases fail. One mutation for each of
writers 2 to 4 puts back ``body_html = :bh``, and the term fence and its R8
case fail.

Run::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_html_hot_only.py -v -rs
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis as tr
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.settings import get_settings
from acb_common.tenant_redis import TenantRedis
from email_ingestion import body_backfill, html_tier, persist
from email_ingestion.body_backfill import FetchedBody
from gateway.routes.email import core
from gateway.routes.email.transport import messages as m
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture and its DB gate (non-priv role
# acb_app_h3rls). ``promoted`` and ``app_engine`` are used by name for fixture
# injection, so the import is load-bearing even though it reads as unused.
from tests.unit.test_email_upsert_guard import _Msg
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

REPO = Path(__file__).resolve().parents[2]
ORG_A = "11111111-1111-4111-8111-111111111111"
MSG_ID = "44444444-4444-4444-8444-444444444444"
OWNER = "owner@em-s3.test"
COLD = datetime.now(UTC) - timedelta(days=200)
HOT = datetime.now(UTC) - timedelta(days=10)
HTML = "<p>Hello <b>there</b></p><div>second line</div>"


def _flags(monkeypatch: pytest.MonkeyPatch, *, from_provider: bool, hot_only: bool) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_html_from_provider", from_provider, raising=False)
    monkeypatch.setattr(settings, "email_html_hot_only", hot_only, raising=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with BOTH flags on and no Redis tenant bound."""
    _flags(monkeypatch, from_provider=True, hot_only=True)
    token = tr._ORGANIZATION_ID.set(None)
    try:
        yield
    finally:
        tr._ORGANIZATION_ID.reset(token)


def _user(email: str = OWNER, org: str | None = ORG_A) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


# ── 1. The upsert params ────────────────────────────────────────────────────


class TestTheUpsertParams:

    def test_a_cold_message_binds_no_html(self) -> None:
        p = persist._message_params("acc", _Msg(received_at=COLD))
        assert p["body_html"] is None
        assert p["body_text"] == "the body"

    def test_a_cold_html_only_message_binds_a_text_made_from_its_html(self) -> None:
        p = persist._message_params(
            "acc", _Msg(received_at=COLD, body_text="", body_html=HTML))
        assert p["body_html"] is None
        assert p["body_text"] == body_backfill._html_to_text(HTML)
        assert "Hello there" in p["body_text"]

    def test_a_blank_text_counts_as_empty(self) -> None:
        p = persist._message_params(
            "acc", _Msg(received_at=COLD, body_text="  \n", body_html=HTML))
        assert "Hello there" in p["body_text"]

    def test_a_provider_text_is_never_replaced(self) -> None:
        p = persist._message_params(
            "acc", _Msg(received_at=COLD, body_text="plain part", body_html=HTML))
        assert p["body_text"] == "plain part"

    def test_a_hot_message_binds_its_html(self) -> None:
        p = persist._message_params("acc", _Msg(received_at=HOT, body_html=HTML))
        assert p["body_html"] == HTML

    def test_a_message_with_no_date_binds_its_html(self) -> None:
        p = persist._message_params("acc", _Msg(received_at=None, body_html=HTML))
        assert p["body_html"] == HTML

    @pytest.mark.parametrize(("from_provider", "hot_only"), [
        (False, False), (True, False), (False, True),
    ])
    def test_either_flag_alone_binds_the_html_as_before(
        self, monkeypatch, from_provider: bool, hot_only: bool,
    ) -> None:
        _flags(monkeypatch, from_provider=from_provider, hot_only=hot_only)
        p = persist._message_params(
            "acc", _Msg(received_at=COLD, body_text="", body_html=HTML))
        assert p["body_html"] == HTML
        assert p["body_text"] == "", "the text fill runs only when the HTML goes"

    def test_the_html_is_still_cut_at_its_cap(self) -> None:
        big = "<p>" + "x" * (persist.MAX_BODY_HTML_BYTES + 10) + "</p>"
        p = persist._message_params("acc", _Msg(received_at=HOT, body_html=big))
        assert len(p["body_html"].encode()) <= persist.MAX_BODY_HTML_BYTES


# ── 2. The helpers ──────────────────────────────────────────────────────────


class TestTheHelpers:

    def test_drops_html_needs_hot_only_and_a_cold_message(self, monkeypatch) -> None:
        assert html_tier.drops_html(COLD) is True
        assert html_tier.drops_html(HOT) is False
        assert html_tier.drops_html(None) is False
        _flags(monkeypatch, from_provider=True, hot_only=False)
        assert html_tier.drops_html(COLD) is False

    def test_cold_before_is_the_cutoff_only_under_hot_only(self, monkeypatch) -> None:
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        assert html_tier.cold_before(now) == html_tier.hot_cutoff(now)
        _flags(monkeypatch, from_provider=False, hot_only=True)
        assert html_tier.cold_before(now) is None

    def test_the_term_keeps_a_cold_value_and_writes_the_fetched_one_else(self) -> None:
        term = html_tier.COLD_SAFE_HTML_SET
        assert term.startswith("body_html = CASE WHEN received_at < :html_cold_before")
        assert "THEN NULLIF(body_html, '')" in term
        assert term.endswith("ELSE :bh END")


# ── 3. The open, hermetic ───────────────────────────────────────────────────


class _TextRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, name: str) -> str | None:
        return self.store.get(name)

    async def setex(self, name: str, _seconds: int, value: str) -> bool:
        self.store[name] = value
        return True


class _OpenDb:
    """The session of the open route: one row, and each statement recorded."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((str(statement), dict(params or {})))
        return SimpleNamespace(fetchone=lambda: self.row, fetchall=lambda: [])

    def body_writes(self) -> list[tuple[str, dict[str, Any]]]:
        return [(s, p) for s, p in self.calls if "SET body_text" in s]


def _open_row(received_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=MSG_ID, provider_message_id="p", thread_id="t", account_id="a",
        folder="inbox", labels=[], from_address=None, to_addresses=[],
        cc_addresses=[], bcc_addresses=[], subject="s", body_text="",
        body_html=None, snippet="", has_attachments=False, is_read=True,
        is_starred=False, is_flagged=False, importance="normal", categories=[],
        received_at=received_at, synced_at=None, snoozed_until=None, stored_bytes=0,
    )


def _open_harness(monkeypatch, *, received_at: datetime | None) -> tuple[_OpenDb, _TextRedis]:
    db = _OpenDb(_open_row(received_at))
    redis = _TextRedis()

    @asynccontextmanager
    async def _tenant_session():
        yield db

    class _Provider:
        async def authenticate(self) -> bool:
            return True

        async def get_message(self, _pmid: str) -> Any:
            return SimpleNamespace(body_text="the text", body_html=HTML,
                                   has_attachments=False, attachments=[])

        def credentials_dirty(self) -> bool:
            return False

    async def _provider_for_message(*_a: Any, **_k: Any) -> Any:
        return _Provider(), "p", "a", object()

    monkeypatch.setattr(m, "_tenant_session", _tenant_session)
    monkeypatch.setattr(m, "_provider_for_message", _provider_for_message)
    monkeypatch.setattr(m, "get_tenant_redis", lambda *, binary=False: TenantRedis(redis))
    return db, redis


class TestTheOpenAnswersTheHtml:

    async def test_a_cold_open_answers_the_html_and_caches_it(self, monkeypatch) -> None:
        db, redis = _open_harness(monkeypatch, received_at=COLD)
        msg = await m.get_message(MSG_ID, user=_user())
        assert msg.body_html == HTML, "the open answers the HTML that it fetched"
        assert msg.html_remote is False
        [(sql, params)] = db.body_writes()
        assert html_tier.COLD_SAFE_HTML_SET in sql
        assert abs(params["html_cold_before"] - html_tier.hot_cutoff()) < timedelta(seconds=30)
        assert json.loads(redis.store[f"cc:{ORG_A}:email-html:{MSG_ID}"]) == {"body_html": HTML}

    async def test_a_hot_open_caches_nothing(self, monkeypatch) -> None:
        _db, redis = _open_harness(monkeypatch, received_at=HOT)
        msg = await m.get_message(MSG_ID, user=_user())
        assert msg.body_html == HTML
        assert redis.store == {}

    async def test_the_flag_off_binds_no_cutoff_and_caches_nothing(self, monkeypatch) -> None:
        _flags(monkeypatch, from_provider=True, hot_only=False)
        db, redis = _open_harness(monkeypatch, received_at=COLD)
        await m.get_message(MSG_ID, user=_user())
        [(_sql, params)] = db.body_writes()
        assert params["html_cold_before"] is None
        assert redis.store == {}


# ── 4. One set of writers ───────────────────────────────────────────────────

_SCAN_ROOTS = (REPO / "apps", REPO / "packages")
_INGESTION = "apps/services/email_ingestion/email_ingestion"
_ROUTES = "apps/services/gateway/gateway/routes/email"
_TERM_NAME = "COLD_SAFE_HTML_SET"

#: Each scope that may hold SQL that writes ``body_html``, with the reason.
ALLOWED_HTML_WRITERS: dict[tuple[str, str], str] = {
    (f"{_INGESTION}/persist.py", "<module>"):
        "writer 1, the sync upsert: `_INSERT`, and the SET of `_SYNCED_COLUMNS`",
    (f"{_INGESTION}/body_backfill.py", "write_bodies"): "writer 2, the body backfill",
    (f"{_ROUTES}/transport/messages.py", "get_message"): "writer 3, the open",
    (f"{_ROUTES}/core.py", "hydrate_message_body"): "writer 4, the drafter's hydrate",
    (f"{_INGESTION}/html_tier.py", "<module>"): "the cold-safe term itself",
    (f"{_ROUTES}/automation/drafting.py", "_upsert_local_draft"):
        "a draft row, received_at = now(), so always hot. Its SQL names no "
        "body_html today",
}
#: The writers that must write the cold-safe term, and only it.
TERM_WRITERS = frozenset({
    (f"{_INGESTION}/body_backfill.py", "write_bodies"),
    (f"{_ROUTES}/transport/messages.py", "get_message"),
    (f"{_ROUTES}/core.py", "hydrate_message_body"),
})
#: The scopes that the scan must find, so a list that went stale fails.
REQUIRED = frozenset(ALLOWED_HTML_WRITERS) - {
    (f"{_ROUTES}/automation/drafting.py", "_upsert_local_draft"),
}

_INSERT_HTML = re.compile(r"\bINSERT\s+INTO\s+email_messages\b[^;]*?\bbody_html\b", re.I | re.S)
_SET_HTML = re.compile(r"\bbody_html\s*=(?!=)", re.I)


def _flatten(node: ast.AST) -> str | None:
    """The text of a string expression. A ``{...}`` part of an f-string reads
    as ``{}``, except the cold-safe term, which reads as its own text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                src = ast.unparse(part.value)
                out.append(html_tier.COLD_SAFE_HTML_SET if src.endswith(_TERM_NAME) else "{}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _flatten(node.left), _flatten(node.right)
        if left is not None and right is not None:
            return left + right
    return None


class _Scan(ast.NodeVisitor):
    """Each string expression that writes ``body_html``, with its scope."""

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.hits: list[tuple[str, int, str]] = []

    def _function(self, node: ast.AST) -> None:
        self.scope.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function
    visit_ClassDef = _function

    def visit_Expr(self, node: ast.Expr) -> None:
        # A docstring, or a bare string statement, is no SQL.
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return
        self.generic_visit(node)

    def _string(self, node: ast.AST) -> None:
        body = _flatten(node)
        if body is None:
            self.generic_visit(node)
            return
        if _INSERT_HTML.search(body) or _SET_HTML.search(body):
            scope = self.scope[0] if self.scope else "<module>"
            self.hits.append((scope, node.lineno, body))

    visit_Constant = _string
    visit_JoinedStr = _string
    visit_BinOp = _string


def _scan(source: str) -> list[tuple[str, int, str]]:
    scan = _Scan()
    scan.visit(ast.parse(source))
    return scan.hits


def _tree_hits() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if any(p in {".venv", "node_modules", "__pycache__"} for p in path.parts):
                continue
            rel = path.relative_to(REPO).as_posix()
            for scope, line, body in _scan(path.read_text(encoding="utf-8-sig")):
                found.setdefault((rel, scope), []).append(f"{line}: {body}")
    return found


class TestOnlyTheNamedWritersWriteHtml:

    def test_no_sql_outside_the_named_writers_writes_body_html(self) -> None:
        stray = {k: v for k, v in _tree_hits().items() if k not in ALLOWED_HTML_WRITERS}
        assert stray == {}, (
            "SQL that writes body_html outside the named writers of EM-S3. A new "
            "writer must store no cold HTML (html_tier.drops_html or "
            f"COLD_SAFE_HTML_SET) and join ALLOWED_HTML_WRITERS: {sorted(stray)}"
        )

    def test_each_named_writer_is_still_found(self) -> None:
        missing = REQUIRED - set(_tree_hits())
        assert missing == set(), f"a named writer moved, so update the list: {missing}"

    def test_each_update_writer_writes_the_cold_safe_term_only(self) -> None:
        hits = _tree_hits()
        for writer in sorted(TERM_WRITERS):
            bodies = hits.get(writer, [])
            assert bodies, f"{writer} writes no body_html"
            for body in bodies:
                assert html_tier.COLD_SAFE_HTML_SET in body, (
                    f"{writer} writes body_html without the cold-safe term: {body}")
                rest = body.replace(html_tier.COLD_SAFE_HTML_SET, "")
                assert not _SET_HTML.search(rest), (
                    f"{writer} writes body_html a second time: {body}")

    def test_the_draft_insert_stays_hot(self) -> None:
        import inspect

        from gateway.routes.email.automation import drafting

        src = inspect.getsource(drafting._upsert_local_draft)
        assert "'drafts'" in src and "now(), now())" in src, (
            "the draft row must keep received_at = now(), or it needs the cold check"
        )

    def test_the_scan_can_go_red(self) -> None:
        assert _scan('x = "UPDATE email_messages SET body_html = :h WHERE id = :i"\n')
        assert _scan(
            'def f():\n    return "INSERT INTO email_messages (id, body_html) VALUES (1, 2)"\n'
        )[0][0] == "f"
        assert _scan('x = f"UPDATE email_messages SET {COLD_SAFE_HTML_SET} WHERE id = 1"\n')
        assert _scan('x = "UPDATE email_messages SET " + "body_html = NULL"\n')
        assert _scan('"""SET body_html = NULL in a docstring."""\n') == []
        assert _scan('x = "body_html == other"\n') == []
        assert _scan('x = "INSERT INTO email_messages (id, body_text) VALUES (1, 2)"\n') == []


# ── 5. R8: the writers on a real database ───────────────────────────────────


@contextmanager
def _bound(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_account(admin, *, org: str, owner: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) VALUES (:u, 'microsoft', :m, "
            "'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-s3.test",
             "o": org}).scalar_one())


def _seed_message(admin, *, org: str, account: str, received_at: datetime,
                  body_text: str = "", body_html: str | None = None) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
            "folder, from_address, to_addresses, subject, body_text, body_html, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :pm, :t, "
            "'inbox', CAST(:f AS jsonb), '[]'::jsonb, 'old', :bt, :bh, :r, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "t": f"t-{uuid.uuid4().hex[:8]}",
             "f": json.dumps({"email": "sender@contoso.test", "name": "S"}),
             "bt": body_text, "bh": body_html, "r": received_at,
             "o": org}).scalar_one())


def _bodies(admin, message_id: str) -> tuple[Any, ...]:
    with admin.connect() as c:
        return tuple(c.execute(text(
            "SELECT body_text, body_html FROM email_messages "
            "WHERE id = CAST(:m AS uuid)"), {"m": message_id}).one())


def _by_pmid(admin, account: str, pmid: str) -> tuple[Any, ...]:
    with admin.connect() as c:
        return tuple(c.execute(text(
            "SELECT body_text, body_html FROM email_messages "
            "WHERE account_id = CAST(:a AS uuid) AND provider_message_id = :p"),
            {"a": account, "p": pmid}).one())


def _purge(admin, account: str) -> None:
    with admin.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                  {"a": account})


@pytest.fixture()
def world(promoted, app_engine, monkeypatch):  # noqa: F811
    """One mailbox of org B, the app DSN, and a fake provider and cache.

    The SQL is real and runs as ``acb_app_h3rls``. The provider and the cache
    are fakes. Torn down by deleting the mailbox."""
    _assert_non_priv(app_engine)
    p = promoted
    owner = f"o-{uuid.uuid4().hex[:8]}@em-s3.test"
    account = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    redis = _TextRedis()
    state: dict[str, Any] = {"html": HTML, "text": "", "calls": 0}

    class _Provider:
        async def authenticate(self) -> bool:
            return True

        async def get_message(self, _pmid: str) -> Any:
            state["calls"] += 1
            return SimpleNamespace(body_text=state["text"], body_html=state["html"],
                                   has_attachments=False, attachments=[])

        async def get_message_body(self, pmid: str) -> Any:
            return await self.get_message(pmid)

        def credentials_dirty(self) -> bool:
            return False

    for mod in (core, m):
        monkeypatch.setattr(mod, "_decrypt_credentials", lambda _blob: ({}, object()))
        monkeypatch.setattr(mod, "_instantiate_provider", lambda _n, _c: _Provider())
    monkeypatch.setattr(m, "get_tenant_redis", lambda *, binary=False: TenantRedis(redis))
    yield SimpleNamespace(
        admin=p.admin_engine, org=p.org_b, owner=owner, account=account,
        dsn=p.app_url.render_as_string(hide_password=False), state=state, redis=redis,
    )
    _purge(p.admin_engine, account)


async def _upsert(w, msg) -> None:
    async with tenant_engine_scope(w.dsn), tenant_session(w.org) as db:
        await persist.upsert_message(db, w.account, msg)


async def _write_bodies(w, fetched: list[FetchedBody]) -> None:
    async with tenant_engine_scope(w.dsn), tenant_session(w.org) as db:
        await body_backfill.write_bodies(db, w.account, fetched)


@_DB_GATE
class TestTheWritersOnARealDatabase:

    # Writer 1, the upsert.

    async def test_an_insert_of_a_cold_html_only_message_stores_text_and_no_html(
        self, world,
    ) -> None:
        msg = _Msg(provider_message_id=f"pm-{uuid.uuid4().hex[:12]}",
                   received_at=COLD, body_text="", body_html=HTML)
        await _upsert(world, msg)
        body_text, body_html = _by_pmid(world.admin, world.account, msg.provider_message_id)
        assert body_html is None
        assert "Hello there" in body_text

    async def test_an_insert_of_a_hot_message_stores_its_html(self, world) -> None:
        msg = _Msg(provider_message_id=f"pm-{uuid.uuid4().hex[:12]}", received_at=HOT)
        await _upsert(world, msg)
        assert _by_pmid(world.admin, world.account, msg.provider_message_id) == (
            "the body", "<p>the body</p>")

    async def test_the_flag_off_stores_cold_html_as_before(self, world, monkeypatch) -> None:
        _flags(monkeypatch, from_provider=True, hot_only=False)
        msg = _Msg(provider_message_id=f"pm-{uuid.uuid4().hex[:12]}", received_at=COLD)
        await _upsert(world, msg)
        assert _by_pmid(world.admin, world.account, msg.provider_message_id)[1] == (
            "<p>the body</p>")

    async def test_an_inbound_insert_of_a_cold_message_stores_no_html(self, world) -> None:
        msg = _Msg(provider_message_id=f"pm-{uuid.uuid4().hex[:12]}", received_at=COLD)
        async with tenant_engine_scope(world.dsn), tenant_session(world.org) as db:
            await persist.upsert_message(db, world.account, msg, on_conflict="nothing")
        assert _by_pmid(world.admin, world.account, msg.provider_message_id)[1] is None

    # Writer 2, the body backfill.

    async def test_write_bodies_of_a_cold_message_stores_no_html(self, world) -> None:
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD)
        await _write_bodies(world, [FetchedBody(
            id=mid, body_text="the text", body_html=HTML, snippet="s",
            has_attachments=None)])
        assert _bodies(world.admin, mid) == ("the text", None)

    async def test_write_bodies_keeps_the_html_that_a_cold_row_holds(self, world) -> None:
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD, body_html="<p>kept</p>")
        await _write_bodies(world, [FetchedBody(
            id=mid, body_text="the text", body_html=HTML, snippet="s",
            has_attachments=None)])
        assert _bodies(world.admin, mid) == ("the text", "<p>kept</p>")

    async def test_write_bodies_of_a_hot_message_stores_its_html(self, world) -> None:
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=HOT)
        await _write_bodies(world, [FetchedBody(
            id=mid, body_text="the text", body_html=HTML, snippet="s",
            has_attachments=None)])
        assert _bodies(world.admin, mid) == ("the text", HTML)

    async def test_write_bodies_with_the_flag_off_stores_cold_html(
        self, world, monkeypatch,
    ) -> None:
        _flags(monkeypatch, from_provider=False, hot_only=True)
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD)
        await _write_bodies(world, [FetchedBody(
            id=mid, body_text="the text", body_html=HTML, snippet="s",
            has_attachments=None)])
        assert _bodies(world.admin, mid) == ("the text", HTML)

    # Writer 3, the open.

    async def test_the_open_of_a_cold_message_stores_text_and_answers_html(
        self, world,
    ) -> None:
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD)
        async with tenant_engine_scope(world.dsn):
            with _bound(world.org):
                msg = await m.get_message(mid, user=_user(world.owner, world.org))
                assert msg.body_html == HTML, "the open answers the HTML"
                html = await m.get_message_html(
                    mid, prefetch=False, user=_user(world.owner, world.org))
        assert _bodies(world.admin, mid) == ("the text", None)
        assert (html.source, html.body_html) == ("cache", HTML), (
            "the open wrote the HTML into the cache of the HTML route")
        assert world.state["calls"] == 1

    async def test_the_open_of_a_hot_message_stores_its_html(self, world) -> None:
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=HOT)
        async with tenant_engine_scope(world.dsn):
            with _bound(world.org):
                await m.get_message(mid, user=_user(world.owner, world.org))
        assert _bodies(world.admin, mid) == ("the text", HTML)
        assert world.redis.store == {}

    async def test_the_open_with_the_flag_off_stores_cold_html(
        self, world, monkeypatch,
    ) -> None:
        _flags(monkeypatch, from_provider=True, hot_only=False)
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD)
        async with tenant_engine_scope(world.dsn):
            with _bound(world.org):
                await m.get_message(mid, user=_user(world.owner, world.org))
        assert _bodies(world.admin, mid) == ("the text", HTML)

    # Writer 4, hydrate_message_body.

    async def _hydrate(self, world, mid: str) -> str:
        async with tenant_engine_scope(world.dsn):
            with _bound(world.org):
                async with core._tenant_session() as db:
                    return await core.hydrate_message_body(db, mid, world.owner)

    async def test_hydrate_of_a_cold_message_stores_text_and_no_html(self, world) -> None:
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD)
        assert await self._hydrate(world, mid) == "the text"
        assert _bodies(world.admin, mid) == ("the text", None)

    async def test_hydrate_keeps_the_html_that_a_cold_row_holds(self, world) -> None:
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD, body_html="<p>kept</p>")
        await self._hydrate(world, mid)
        assert _bodies(world.admin, mid) == ("the text", "<p>kept</p>")

    async def test_hydrate_of_a_hot_message_stores_its_html(self, world) -> None:
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=HOT)
        await self._hydrate(world, mid)
        assert _bodies(world.admin, mid) == ("the text", HTML)

    async def test_hydrate_with_the_flag_off_stores_cold_html(
        self, world, monkeypatch,
    ) -> None:
        _flags(monkeypatch, from_provider=False, hot_only=False)
        world.state["text"] = "the text"
        mid = _seed_message(world.admin, org=world.org, account=world.account,
                            received_at=COLD)
        await self._hydrate(world, mid)
        assert _bodies(world.admin, mid) == ("the text", HTML)


def test_replace_keeps_the_message_shape() -> None:
    """``_Msg`` comes from the guard file. A field it loses breaks this file."""
    assert replace(_Msg(), received_at=COLD).received_at == COLD
