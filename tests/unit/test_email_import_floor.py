"""WS-17 EM-T6a — the import floor and the range choice (backend).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7, the part
"EM-T6a". Owner decisions D-EM-10, D-EM-11 and D-EM-13 (§10.2).

R7 fences named here:

* ``email-import-floor``: ``_sync_account`` passes the floor to the provider
  on every sync, deep or shallow. An explicit ``since`` of a member act binds
  when it is newer than the ceiling. Otherwise ``import_since`` binds. The
  ceiling of 180 days binds every floor.
* ``email-outlook-floor-every-sweep``: the recurring poll of Outlook sends
  ``receivedDateTime ge <floor>`` on the first page of each folder.
* ``email-floor-backstop``: the core drops a message older than the floor
  before phase (c), unless its row is already stored (R8).
* ``email-import-range-on-connect``: the callback writes ``import_since`` for a
  NEW mailbox only, and a reconnect keeps the sync point (R8, FORCE RLS).
* ``email-onboarding-done``: the PATCH writes ``onboarding_done_at`` under the
  owner predicate, and the account reads return the two new fields (R8).
* ``email-import-migration``: the migration applies to a fresh ladder and to a
  promoted catalog, and a second run changes nothing (R8).

The signed state and the authorize leg are fenced in
``test_email_oauth_state.py``.

The R8 tests run the REAL handlers as the non-privileged role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS) on the phase-4-promoted two-org
catalog of ``test_h3_rls_promotion_rehearsal``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_import_floor.py -v -rs
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from email_ingestion import import_window
from email_ingestion.providers.base import (
    EmailAddress,
    EmailFolder,
    EmailMessage,
    SyncResult,
)
from email_ingestion.providers.outlook import OutlookProvider
from fastapi import HTTPException
from gateway.routes.email.transport import accounts, oauth, signing
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import INIT_SCHEMA, _exec_file, ladder, tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    _apply_generated_phase,
    app_engine,
    promoted,
)

REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = REPO / "infra/postgres"
_SECRET = "em-t6a-test-secret"
_ORG = "11111111-2222-3333-4444-555555555555"
#: The tolerance of every time comparison, in seconds (the spec says 5).
_TOL = 5.0
_DAY = 86400.0

#: The eight columns of the migration, and the type of each.
_COLUMNS = {
    "import_since": "timestamp with time zone",
    "import_reached_at": "timestamp with time zone",
    "import_phase": "text",
    "import_count": "integer",
    "import_estimate": "integer",
    "stored_bytes": "bigint",
    "stored_bytes_at": "timestamp with time zone",
    "onboarding_done_at": "timestamp with time zone",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gateway_session_secret", _SECRET, raising=False)
    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", False,
                        raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")
    # The callback asks resolve_identity for the organization of the member.
    # Under FORCE RLS only the cutover read can answer an unbound question.
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")


def _now() -> datetime:
    return datetime.now(UTC)


def _near(a: datetime, b: datetime, tol: float = _TOL) -> bool:
    return abs((a - b).total_seconds()) <= tol


def _migration() -> Path:
    """Found by CONTENT, never by number: R1 can renumber it at merge."""
    found = [
        p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
        if "EM-T6" in p.read_text(encoding="utf-8")
        and "ADD COLUMN IF NOT EXISTS import_since" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, f"expected one EM-T6a migration, got {found}"
    return found[0]


# ── 1. The rules of the window ─────────────────────────────────────────────


NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_the_ceiling_is_six_months_of_thirty_days() -> None:
    assert import_window.DAYS_PER_MONTH == 30
    assert import_window.CEILING_DAYS == 180
    assert import_window.ceiling(NOW) == NOW - timedelta(days=180)


@pytest.mark.parametrize("months", range(0, 7))
def test_a_range_becomes_thirty_days_a_month(months: int) -> None:
    assert import_window.since_for_months(months, NOW) == NOW - timedelta(days=30 * months)


@pytest.mark.parametrize("bad", [7, -1, True, False, "1", 1.0, None])
def test_a_range_outside_zero_to_six_is_refused(bad) -> None:
    assert not import_window.is_import_months(bad)
    with pytest.raises(ValueError):
        import_window.since_for_months(bad, NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("since_days", "import_days", "want_days"),
    [
        (None, 30, 30),     # the choice of the member binds
        (None, None, 180),  # a mailbox from before EM-T6: the ceiling alone
        (365, 30, 180),     # an explicit since older than the ceiling
        (10, 30, 10),       # an explicit since newer than it wins
        (None, 400, 180),   # a stored range older than the ceiling
        (0, 30, 0),         # an explicit since of now
    ],
)
def test_the_floor_rule(since_days, import_days, want_days) -> None:
    def ago(days):
        return None if days is None else NOW - timedelta(days=days)

    got = import_window.sync_floor(
        since=ago(since_days), import_since=ago(import_days), now=NOW)
    assert got == NOW - timedelta(days=want_days)


def test_a_naive_date_is_read_as_utc() -> None:
    naive = (NOW - timedelta(days=10)).replace(tzinfo=None)
    assert import_window.sync_floor(since=naive, import_since=None, now=NOW) == (
        NOW - timedelta(days=10))
    assert import_window.below_floor(naive, NOW)
    assert not import_window.below_floor(None, NOW)


def test_no_initial_sync_days_remains() -> None:
    """Scope item 2: the constant of the year-long first sync is gone."""
    roots = [REPO / "apps/services/email_ingestion",
             REPO / "apps/services/gateway/gateway/routes/email"]
    hits = [str(p.relative_to(REPO)) for root in roots
            for p in root.rglob("*.py")
            if "INITIAL_SYNC_DAYS" in p.read_text(encoding="utf-8")]
    assert hits == []


# ── 2. The floor of every sync (hermetic) ──────────────────────────────────


class _Row:
    def __init__(self, row) -> None:
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def scalar(self):
        return None


def _fake_sessions(row):
    class _Db:
        async def execute(self, *_a, **_k):
            return _Row(row)

    @asynccontextmanager
    async def _ts(org=None):
        yield _Db()

    return _ts


class _Store:
    def decrypt(self, raw: str) -> str:
        return json.dumps({"access_token": "at"})

    def encrypt(self, raw: str) -> str:
        return f"enc:{raw}"


class _Recorder:
    """A provider that records what the core asks for, and ignores it."""

    def __init__(self, messages: list[EmailMessage] | None = None) -> None:
        self.calls: list[dict] = []
        self.messages = messages or []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {}

    async def sync_messages(self, **kw) -> SyncResult:
        self.calls.append(kw)
        return SyncResult(messages=list(self.messages), new_history_id=None)

    async def get_message(self, provider_message_id):
        raise RuntimeError("no body backfill here")


@pytest.fixture()
def core(monkeypatch):
    """Run ``_sync_account`` on a fake session with a recording provider."""
    from acb_llm import key_store

    provider = _Recorder()

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
    monkeypatch.setattr(sched, "upsert_message", _noop)
    monkeypatch.setattr(sched, "run_label_learn_hook", _noop)

    async def _run(*, done: bool, import_since, **kw) -> dict:
        row = SimpleNamespace(
            id="log-1", provider="microsoft", credentials_encrypted="x",
            last_history_id=None, sync_interval_secs=300,
            initial_sync_done=done, import_since=import_since)
        monkeypatch.setattr(sched, "tenant_session", _fake_sessions(row))
        res = await sched._sync_account("acc-1", organization_id=_ORG, **kw)
        assert "error" not in res, res
        return provider.calls[-1]

    return _run


async def test_the_first_import_reads_back_to_the_chosen_range(core) -> None:
    chosen = _now() - timedelta(days=30)
    call = await core(done=False, import_since=chosen)
    assert call["deep"] is True
    assert call["since"] == chosen


async def test_a_recurring_poll_passes_the_floor_too(core) -> None:
    chosen = _now() - timedelta(days=60)
    call = await core(done=True, import_since=chosen)
    assert call["deep"] is False
    assert call["since"] == chosen, "the shallow poll had no floor (D-EM-10)"


async def test_a_resync_takes_the_choice_of_the_member(core) -> None:
    """A Resync passes ``deep=True`` and no ``since`` (transport/sync.py)."""
    chosen = _now() - timedelta(days=90)
    call = await core(done=True, import_since=chosen, deep=True)
    assert call["deep"] is True
    assert call["since"] == chosen


async def test_an_explicit_since_older_than_the_ceiling_stops_at_180_days(core) -> None:
    """Process past emails with "Last year": the provider gets 180 days."""
    call = await core(done=True, import_since=_now() - timedelta(days=30),
                      deep=True, since=_now() - timedelta(days=365))
    assert _near(call["since"], _now() - timedelta(days=180))


async def test_an_explicit_since_newer_than_the_ceiling_binds(core) -> None:
    """A member act wins over the range when it is inside the ceiling."""
    asked = _now() - timedelta(days=100)
    call = await core(done=True, import_since=_now() - timedelta(days=30),
                      deep=True, since=asked)
    assert call["since"] == asked


async def test_a_mailbox_from_before_em_t6_gets_the_ceiling(core) -> None:
    call = await core(done=True, import_since=None)
    assert _near(call["since"], _now() - timedelta(days=180))


# ── 3. Outlook applies the floor on every sweep (fake Graph) ───────────────


def _graph(value: list, next_link: str | None = None) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    body: dict = {"value": value}
    if next_link:
        body["@odata.nextLink"] = next_link
    r.json = MagicMock(return_value=body)
    return r


async def test_a_recurring_poll_sends_the_floor_on_each_first_page() -> None:
    p = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    gets: list[tuple[str, dict | None]] = []

    async def _get(url, params=None):
        gets.append((url, params))
        return _graph([], next_link="https://graph.microsoft.com/v1.0/next")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    p._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]
    p.list_folders = AsyncMock(return_value=[  # type: ignore[method-assign]
        EmailFolder(provider_folder_id="F-user", name="Projects", type="user"),
    ])
    floor = datetime(2026, 9, 2, 8, 30, 15, tzinfo=UTC)

    res = await p.sync_messages(deep=False, since=floor)

    firsts = [(url, params) for url, params in gets if params is not None]
    folders = sorted(url.split("/")[3] for url, _ in firsts)
    assert folders == sorted(["inbox", "sentitems", "drafts", "archive",
                              "junkemail", "deleteditems", "F-user"])
    for url, params in firsts:
        assert params["$filter"] == "receivedDateTime ge 2026-09-02T08:30:15Z", url
    # The page count of the recurring poll does not change in EM-T6a.
    assert len(gets) == len(firsts) * p.RECURRING_SYNC_MAX_PAGES
    assert res.full_snapshot is True


# ── 4. The backstop (hermetic) ─────────────────────────────────────────────


def _msg(pid: str, days_ago: float | None, subject: str = "s") -> EmailMessage:
    return EmailMessage(
        provider_message_id=pid, thread_id=f"t-{pid}", folder="inbox",
        from_address=EmailAddress(name="S", email="s@sender.test"),
        subject=subject,
        received_at=None if days_ago is None else _now() - timedelta(days=days_ago),
    )


class _StoredDb:
    def __init__(self, stored: set[str]) -> None:
        self.stored = stored
        self.queries: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.queries.append((str(stmt), dict(params or {})))
        rows = [SimpleNamespace(provider_message_id=p)
                for p in params["pids"] if p in self.stored]
        return SimpleNamespace(fetchall=lambda: rows)


async def test_the_backstop_drops_old_mail_that_is_not_stored(caplog) -> None:
    floor = _now() - timedelta(days=30)
    messages = [_msg("new", 1), _msg("old-stored", 90), _msg("old-a", 100),
                _msg("old-b", 120), _msg("no-date", None)]
    db = _StoredDb({"old-stored"})
    with caplog.at_level(logging.INFO, logger=sched.logger.name):
        kept = await sched._drop_below_floor(db, "acc-1", messages, floor)
    assert [m.provider_message_id for m in kept] == ["new", "old-stored", "no-date"]
    assert len(db.queries) == 1, "the stored ids take ONE query"
    assert sorted(db.queries[0][1]["pids"]) == ["old-a", "old-b", "old-stored"]
    assert any("sync.dropped_below_floor" in r.getMessage()
               and "count=2" in r.getMessage() for r in caplog.records)


async def test_the_backstop_makes_no_query_when_nothing_is_old() -> None:
    db = _StoredDb(set())
    messages = [_msg("new", 1), _msg("no-date", None)]
    kept = await sched._drop_below_floor(
        db, "acc-1", messages, _now() - timedelta(days=30))
    assert kept == messages
    assert db.queries == []


# ── 5. R8: the migration ───────────────────────────────────────────────────


def _columns(engine) -> dict[str, tuple[str, str, str | None]]:
    with engine.connect() as c:
        return {
            r.column_name: (r.data_type, r.is_nullable, r.column_default)
            for r in c.execute(text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'email_accounts' "
                "AND column_name = ANY(:cols)"), {"cols": list(_COLUMNS)})
        }


def _checks(engine) -> list[str]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'email_accounts'::regclass AND contype = 'c'"))]


def _row(engine, account_id: str) -> dict:
    with engine.connect() as c:
        return dict(c.execute(text(
            "SELECT " + ", ".join(_COLUMNS) + " FROM email_accounts "
            "WHERE id = CAST(:a AS uuid)"), {"a": account_id}).mappings().one())


@pytest.fixture(scope="module")
def upgraded():
    """A PRIVATE promoted catalog that meets the migration last.

    The ladder without the migration, then the four generated phases (the
    shape of production), then one seeded mailbox, then the migration, then
    the migration again. Dropped at the end. It never touches the shared
    ladder database."""
    admin_url = make_url(_URL)
    name = f"{admin_url.database}_emt6a_{uuid.uuid4().hex[:8]}"
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    maint.dispose()

    eng = create_engine(admin_url.set(database=name), future=True)
    try:
        mig = _migration()
        files = [p for p in ladder() if os.path.basename(p) != mig.name]
        with eng.begin() as conn:
            _exec_file(conn, INIT_SCHEMA)
            for p in files:
                _exec_file(conn, p)
        for phase in ("01_add_columns.sql", "02_backfill.sql",
                      "03_constraints.sql", "04_policies.sql"):
            with eng.begin() as conn:
                _apply_generated_phase(conn, phase)
        before = _columns(eng)

        org = str(uuid.uuid4())
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :s, :s)"), {"id": org, "s": f"t6a-{org[:8]}"})
            account = str(conn.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) "
                "VALUES ('old@t6a.test', 'microsoft', 'old@t6a.test', 'x', "
                "CAST(:o AS uuid)) RETURNING id"), {"o": org}).scalar_one())

        with eng.begin() as conn:
            _exec_file(conn, str(mig))
        first = (_columns(eng), _row(eng, account), _checks(eng))
        with eng.begin() as conn:
            _exec_file(conn, str(mig))
        second = (_columns(eng), _row(eng, account), _checks(eng))

        yield SimpleNamespace(engine=eng, before=before, first=first,
                              second=second)
    finally:
        eng.dispose()
        maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maint.dispose()


def _assert_shape(cols: dict) -> None:
    assert set(cols) == set(_COLUMNS)
    for col, (dtype, nullable, default) in cols.items():
        assert dtype == _COLUMNS[col], col
        assert nullable == "YES", f"{col} must be nullable (R6)"
        assert default is None, f"{col} must have no default (R6)"


@_DB_GATE
class TestTheMigrationOnARealDatabase:

    def test_the_fresh_ladder_has_the_eight_columns(self, promoted) -> None:  # noqa: F811
        """The ``promoted`` fixture replays the WHOLE ladder on a fresh
        database, this migration in its place, and then promotes it."""
        _assert_shape(_columns(promoted.admin_engine))

    def test_the_promoted_catalog_had_none_before(self, upgraded) -> None:
        assert upgraded.before == {}

    def test_the_promoted_catalog_gets_the_eight_columns(self, upgraded) -> None:
        _assert_shape(upgraded.first[0])
        assert not any("import_" in c or "stored_bytes" in c or "onboarding" in c
                       for c in upgraded.first[2]), "the migration adds no CHECK"

    def test_an_existing_row_is_not_backfilled(self, upgraded) -> None:
        assert all(v is None for v in upgraded.first[1].values())

    def test_a_second_run_changes_nothing(self, upgraded) -> None:
        assert upgraded.second == upgraded.first


# ── 6. R8: the callback writes the range of a new mailbox ──────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_identity(admin_engine, *, org_id: str, email: str) -> None:
    with admin_engine.begin() as c:
        ident = str(c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "RETURNING id"), {"e": email}).scalar_one())
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active')"),
            {"o": org_id, "u": ident})


def _purge(admin_engine, *, email: str, mailbox: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE email_address = :m"),
                  {"m": mailbox})
        c.execute(text(
            "DELETE FROM org_membership m USING user_identity ui "
            " WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e)"),
            {"e": email})
        c.execute(text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                  {"e": email})


def _account(admin_engine, mailbox: str) -> dict:
    with admin_engine.connect() as c:
        return dict(c.execute(text(
            "SELECT import_since, initial_sync_done, import_phase, "
            "last_synced_at, last_history_id, sync_status, "
            "credentials_encrypted, now() AS db_now "
            "FROM email_accounts WHERE email_address = :m"),
            {"m": mailbox}).mappings().one())


def _count_as(app_url, org: str, mailbox: str) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": org})
            return c.execute(text(
                "SELECT count(*) FROM email_accounts WHERE email_address = :m"),
                {"m": mailbox}).scalar_one()
    finally:
        eng.dispose()


class _CredStore:
    def __init__(self) -> None:
        self.n = 0

    def encrypt(self, raw: str) -> str:
        self.n += 1
        return f"enc-{self.n}:{raw}"


@pytest.fixture()
def connect(monkeypatch, promoted):  # noqa: F811
    """Run the REAL callback for one new member of org B, as the non-priv
    role. The provider legs are fake. The database legs are real."""
    from acb_llm import key_store

    p = promoted
    email = f"member-{uuid.uuid4().hex[:8]}@em-t6a.test"
    mailbox = f"box-{uuid.uuid4().hex[:8]}@contoso.test"
    _seed_identity(p.admin_engine, org_id=p.org_b, email=email)
    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=p.org_b)

    async def _exchange(code, redirect_uri):
        return {"access_token": f"at-{code}", "refresh_token": f"rt-{code}"}

    async def _mailbox(provider, token):
        return mailbox

    async def _no_sync(account_id, organization_id=None):
        return None

    monkeypatch.setattr(oauth, "_exchange_msft_token", _exchange)
    monkeypatch.setattr(oauth, "_get_provider_email", _mailbox)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _CredStore())
    monkeypatch.setattr(sched, "refresh_account_sync", _no_sync)
    app_dsn = p.app_url.render_as_string(hide_password=False)

    async def _run(state: str, code: str = "c1") -> str | None:
        async with tenant_engine_scope(app_dsn):
            resp = await oauth.oauth_callback(
                "microsoft", user=user, code=code, state=state, error=None)
        assert resp.status_code == 302
        return parse_qs(urlparse(resp.headers["location"]).query).get(
            "error", [None])[0]

    def _state(**over) -> str:
        kw = {"org": p.org_b, "member": email, "provider": "microsoft"}
        kw.update(over)
        return signing.sign_oauth_state(**kw)

    try:
        yield SimpleNamespace(run=_run, state=_state, mailbox=mailbox,
                              email=email, p=p)
    finally:
        _purge(p.admin_engine, email=email, mailbox=mailbox)


def _old_state(org: str, member: str) -> str:
    """A state as the signer of BEFORE EM-T6a wrote it: no ``import_months``."""
    payload = {
        "v": 1, "nonce": uuid.uuid4().hex, "org": org, "member": member,
        "provider": "microsoft", "redirect_after": "",
        "exp": int(_now().timestamp()) + 600,
    }
    body = signing._b64(json.dumps(payload, separators=(",", ":"),
                                   sort_keys=True).encode())
    return f"{body}.{signing._mac(_SECRET, signing.OAUTH_STATE_PURPOSE, body)}"


def _days_back(row: dict) -> float:
    return (row["db_now"] - row["import_since"]).total_seconds() / _DAY


@_DB_GATE
class TestTheCallbackWritesTheRange:

    async def test_two_months_in_org_b_and_org_a_reads_none_of_it(
        self, connect, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        c = connect
        assert await c.run(c.state(import_months=2)) is None
        row = _account(c.p.admin_engine, c.mailbox)
        assert abs(_days_back(row) - 60) * _DAY <= _TOL
        assert row["initial_sync_done"] is False
        assert row["import_phase"] is None
        assert _count_as(c.p.app_url, c.p.org_b, c.mailbox) == 1
        assert _count_as(c.p.app_url, c.p.org_a, c.mailbox) == 0, (
            "org A read the mailbox row of org B"
        )

    async def test_zero_months_marks_the_import_done(self, connect):
        c = connect
        assert await c.run(c.state(import_months=0)) is None
        row = _account(c.p.admin_engine, c.mailbox)
        assert abs(_days_back(row)) * _DAY <= _TOL, "import_since is not now"
        assert row["initial_sync_done"] is True
        assert row["import_phase"] == "done"

    async def test_a_state_with_no_claim_gives_thirty_days(self, connect):
        """A state signed before the deploy completes, with the default."""
        c = connect
        assert await c.run(_old_state(c.p.org_b, c.email)) is None
        row = _account(c.p.admin_engine, c.mailbox)
        assert abs(_days_back(row) - 30) * _DAY <= _TOL

    async def test_a_reconnect_keeps_the_sync_point(self, connect):
        """D-EM-13: the reconnect writes the new credentials and nothing of
        the range or the cursor, whatever its ``import_months``."""
        c = connect
        assert await c.run(c.state(import_months=2), code="c1") is None
        synced_at = _now() - timedelta(hours=7)
        with c.p.admin_engine.begin() as conn:
            conn.execute(text(
                "UPDATE email_accounts SET last_synced_at = :t, "
                "last_history_id = 'h-keep', initial_sync_done = true, "
                "sync_status = 'error', sync_error = 'token revoked' "
                "WHERE email_address = :m"), {"t": synced_at, "m": c.mailbox})
        before = _account(c.p.admin_engine, c.mailbox)

        assert await c.run(c.state(import_months=5), code="c2") is None
        after = _account(c.p.admin_engine, c.mailbox)
        assert after["import_since"] == before["import_since"]
        assert after["initial_sync_done"] is True
        assert after["last_synced_at"] == before["last_synced_at"]
        assert after["last_history_id"] == "h-keep"
        assert after["sync_status"] == "idle"
        assert "at-c2" in after["credentials_encrypted"], (
            "the reconnect did not write the new credentials"
        )
        assert _count_as(c.p.app_url, c.p.org_b, c.mailbox) == 1


# ── 7. R8: the backstop on a real database ─────────────────────────────────


def _seed_account(admin_engine, *, org: str, owner: str,
                  import_since: datetime | None = None) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, import_since, "
            "organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', true, :since, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t6a.test",
             "since": import_since, "o": org}).scalar_one())


def _delete_account(admin_engine, account_id: str) -> None:
    with admin_engine.begin() as c:
        for table in ("email_messages", "email_sync_log"):
            c.execute(text(f"DELETE FROM {table} WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": account_id})
        c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                  {"a": account_id})


@_DB_GATE
class TestTheBackstopOnARealDatabase:

    async def test_old_mail_lands_only_when_already_stored(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6a.test",
                                   import_since=_now() - timedelta(days=30))
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "folder, from_address, to_addresses, subject, received_at, "
                "organization_id) VALUES (CAST(:a AS uuid), 'old-stored', "
                "'inbox', '{}'::jsonb, '[]'::jsonb, 'before', :r, "
                "CAST(:o AS uuid))"),
                {"a": account_id, "r": _now() - timedelta(days=90),
                 "o": p.org_b})

        from acb_llm import key_store

        ignores_since = _Recorder([
            _msg("old-stored", 90, subject="after"),
            _msg("old-new-1", 100),
            _msg("old-new-2", 120),
        ])
        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider",
                            lambda name, creds: ignores_since)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b)
            assert res.get("synced") == 1, res
            assert _near(ignores_since.calls[0]["since"],
                         _now() - timedelta(days=30), tol=60)
            with p.admin_engine.connect() as c:
                rows = dict(c.execute(text(
                    "SELECT provider_message_id, subject FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).all())
            assert rows == {"old-stored": "after"}, (
                "the sync wrote mail older than the floor, or did not update "
                "the stored row"
            )
        finally:
            release_tenant(token)
            _delete_account(p.admin_engine, account_id)


# ── 8. R8: the guided setup and the account reads ──────────────────────────


@pytest.fixture()
def restarts(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def _refresh(account_id, organization_id=None):
        seen.append(account_id)

    async def _remove(account_id):
        seen.append(account_id)

    monkeypatch.setattr(sched, "refresh_account_sync", _refresh)
    monkeypatch.setattr(sched, "remove_account_sync", _remove)
    return seen


def _onboarding_done_at(admin_engine, account_id: str):
    with admin_engine.connect() as c:
        return c.execute(text(
            "SELECT onboarding_done_at FROM email_accounts "
            "WHERE id = CAST(:a AS uuid)"), {"a": account_id}).scalar_one()


@_DB_GATE
class TestTheGuidedSetupFlag:

    async def test_the_owner_closes_it_and_another_member_gets_404(
        self, promoted, app_engine, restarts,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t6a.test"
        chosen = _now() - timedelta(days=60)
        account_id = _seed_account(p.admin_engine, org=p.org_b, owner=owner,
                                   import_since=chosen)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        other = UserContext(email="other@em-t6a.test", role=UserRole.EMPLOYEE,
                            organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                [before] = await accounts.list_accounts(user=me)
                assert before.onboarding_done is False
                assert datetime.fromisoformat(before.import_since) == chosen

                done = await accounts.update_account(
                    account_id, accounts.AccountUpdateModel(onboarding_done=True),
                    user=me)
                assert done.onboarding_done is True
                stamped = _onboarding_done_at(p.admin_engine, account_id)
                assert stamped is not None and _near(stamped, _now(), tol=60)
                assert restarts == [], "closing the setup restarted the sync loop"

                with pytest.raises(HTTPException) as exc:
                    await accounts.update_account(
                        account_id,
                        accounts.AccountUpdateModel(onboarding_done=False),
                        user=other)
                assert exc.value.status_code == 404
                assert _onboarding_done_at(p.admin_engine, account_id) == stamped

                [listed] = await accounts.list_accounts(user=me)
                assert listed.onboarding_done is True
                assert datetime.fromisoformat(listed.import_since) == chosen
                made = await accounts.set_default_account(account_id, user=me)
                assert made.onboarding_done is True
                assert datetime.fromisoformat(made.import_since) == chosen

                reopened = await accounts.update_account(
                    account_id, accounts.AccountUpdateModel(onboarding_done=False),
                    user=me)
                assert reopened.onboarding_done is False
                assert _onboarding_done_at(p.admin_engine, account_id) is None
        finally:
            release_tenant(token)
            _delete_account(p.admin_engine, account_id)

    async def test_a_mailbox_from_before_em_t6_reads_no_range(
        self, promoted, restarts,  # noqa: F811
    ):
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t6a.test"
        account_id = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                [listed] = await accounts.list_accounts(user=me)
            assert listed.import_since is None
            assert listed.onboarding_done is False
        finally:
            release_tenant(token)
            _delete_account(p.admin_engine, account_id)
