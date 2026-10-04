"""WS-17 EM-T6c — the storage meter, the limit, and "remove older mail from
Metorite".

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7, the part
"EM-T6c". Owner decision D-EM-14 and the owner answers Q1 to Q4 (§10.2).

R7 fences named here:

* ``email-storage-meter``: the meter sums ``pg_column_size`` of each column
  of variable length, and of nothing else. It never uses ``octet_length`` or
  the size of a whole row. The R8 half compares the columns that it names
  with ``pg_attribute``, so a new column of variable length fails here.
* ``email-storage-limit-import``: after the batch that takes the meter to the
  limit, the import fetches no next batch, and a first import ends at
  ``import_phase = 'limit'`` (R8).
* ``email-storage-limit-q2``: at the limit, the next poll still writes new
  mail (R8).
* ``email-storage-limit-q3``: at the limit, phases (e) and (f) make no
  provider call and no model call.
* ``email-storage-limit-q4``: at the limit, an open shows the body and writes
  no body to the row (R8).
* ``email-storage-removal``: a removal deletes the older mail of ONE mailbox
  and its rule history, keeps the rules and the guidance, and moves
  ``import_since`` (R8).
* ``email-storage-no-provider``: ``email_ingestion/storage.py`` and the route
  module ``transport/storage.py`` import no provider and call none. A
  companion test proves that the fence can fail.
* ``email-storage-no-commit``: ``email_ingestion/storage.py`` opens no session
  and never commits. The route module never commits.

The R8 tests run as the non-privileged role ``acb_app_h3rls`` (NOSUPERUSER,
NOBYPASSRLS) on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_storage_limit.py -v -rs
"""
from __future__ import annotations

import ast
import base64
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.email_embeddings as email_embeddings
import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant, release_tenant, tenant_session
from acb_common.settings import Settings, get_settings
from email_ingestion import storage
from email_ingestion.providers.base import EmailAddress, EmailMessage, SyncResult
from fastapi import HTTPException
from gateway.routes.email import core
from gateway.routes.email.transport import accounts, messages
from gateway.routes.email.transport import storage as storage_routes
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _commits_and_session_opens,
    _full,
    _purge,
    _seed_account,
    _Store,
)

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_INGEST_STORAGE = _REPO / "apps/services/email_ingestion/email_ingestion/storage.py"
_ROUTE_STORAGE = (
    _REPO / "apps/services/gateway/gateway/routes/email/transport/storage.py")
_ORG = "11111111-2222-3333-4444-555555555555"
_MB = 1_048_576


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", False,
                        raising=False)
    monkeypatch.setattr(get_settings(), "email_mailbox_storage_limit_mb", 500,
                        raising=False)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _flat(stmt) -> str:
    return " ".join(str(stmt).split())


def _random_b64(chars: int) -> str:
    """``chars`` characters of random base64. Postgres cannot compress it, so
    its stored size is its length."""
    return base64.b64encode(os.urandom(chars)).decode()[:chars]


# ── 1. The setting and the limit (hermetic) ─────────────────────────────────


def test_the_setting_is_500_mb() -> None:
    assert Settings.model_fields["email_mailbox_storage_limit_mb"].default == 500
    assert storage.BYTES_PER_MB == _MB


def test_the_limit_in_bytes_follows_the_setting(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_mailbox_storage_limit_mb", 3)
    assert storage.storage_limit_bytes() == 3 * _MB


def test_at_the_limit_means_at_or_over_it(monkeypatch) -> None:
    monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 1000)
    assert storage.at_limit(None) is False, "no meter yet is not the limit"
    assert storage.at_limit(999) is False
    assert storage.at_limit(1000) is True
    assert storage.at_limit(5000) is True


# ── 2. The SQL of the meter (hermetic) ──────────────────────────────────────


@pytest.mark.parametrize("stmt", [storage._MEASURE, storage._PREVIEW],
                         ids=["meter", "preview"])
def test_the_sql_sums_pg_column_size_of_each_column_only(stmt) -> None:
    """R7 fence ``email-storage-meter`` (items 2 and 3). Each column is inside
    its own ``pg_column_size``. Nothing takes the size of a whole row, and
    nothing reads a value with ``octet_length``."""
    sql = _flat(stmt)
    for alias, cols in (("em", storage.MESSAGE_COLUMNS),
                        ("ea", storage.ATTACHMENT_COLUMNS),
                        ("ee", storage.EMBEDDING_COLUMNS)):
        for col in cols:
            assert f"pg_column_size({alias}.{col})" in sql, (alias, col)
            # A bare read of the column would fetch the value.
            assert sql.count(f"{alias}.{col}") == sql.count(
                f"pg_column_size({alias}.{col})"), f"{alias}.{col} read bare"
    assert "octet_length" not in sql.lower()
    assert not re.search(r"pg_column_size\(\s*\w+\s*(\.\s*\*)?\s*\)", sql), (
        "the meter takes the size of a whole row")
    assert ".*" not in sql


# ── 3. The fences of the module (hermetic) ──────────────────────────────────

#: The names that reach the provider.
_PROVIDER_NAMES = frozenset({
    "build_provider", "provider_session", "_instantiate_provider",
    "_provider_for_message", "_provider_for_account",
    "_provider_for_account_any",
})


def provider_reach(source: str) -> list[str]:
    """Each place in *source* that imports the providers or names a call that
    builds one. Docstrings do not count, so a module can say what it must not
    do."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names
                      if a.name.startswith("email_ingestion.providers")]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("email_ingestion.providers"):
                found.append(mod)
            found += [f"{mod}.{a.name}" for a in node.names
                      if a.name in _PROVIDER_NAMES
                      or (mod == "email_ingestion" and a.name == "providers")]
        elif isinstance(node, ast.Name) and node.id in _PROVIDER_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _PROVIDER_NAMES:
            found.append(node.attr)
        elif (isinstance(node, ast.Call) and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and node.args[0].value.startswith("email_ingestion.providers")):
            found.append(node.args[0].value)
    return found


@pytest.mark.parametrize("path", [_INGEST_STORAGE, _ROUTE_STORAGE],
                         ids=lambda p: p.parent.name + "/" + p.name)
def test_the_removal_never_reaches_the_provider(path) -> None:
    """R7 fence ``email-storage-no-provider`` (item 13, D-EM-14). The two
    handlers live in ``transport/storage.py``, so the fence reads the whole
    module."""
    assert provider_reach(path.read_text(encoding="utf-8")) == [], (
        f"{path.name} reaches the provider. A removal deletes Metorite's copy "
        "only, and never touches the mailbox in Outlook (D-EM-14).")


@pytest.mark.parametrize("planted", [
    "from email_ingestion.providers.factory import build_provider\n",
    "import email_ingestion.providers.outlook\n",
    "from email_ingestion import providers\n",
    "from gateway.routes.email.core import provider_session\n",
    "async def f(db):\n    async with core.provider_session(db, 'u'):\n        pass\n",
    "def f():\n    return _instantiate_provider('microsoft', {})\n",
    "import importlib\nimportlib.import_module('email_ingestion.providers.gmail')\n",
])
def test_the_provider_fence_can_fail(planted) -> None:
    assert provider_reach(planted), planted


def test_a_clean_source_passes_the_provider_fence() -> None:
    clean = ('"""It never calls build_provider or email_ingestion.providers."""\n'
             "from email_ingestion import storage\n")
    assert provider_reach(clean) == []


def test_the_storage_steps_open_no_session_and_never_commit() -> None:
    """R7 fence ``email-storage-no-commit`` (done-when: no ``.commit()`` in
    ``storage.py``). The steps take the session of the caller, so each chunk
    is one block of the caller. The companion of this fence is
    ``test_the_sync_step_fence_is_not_vacuous``, on the same helper."""
    commits, opens = _commits_and_session_opens(
        _INGEST_STORAGE.read_text(encoding="utf-8"))
    assert commits == [] and opens == [], (commits, opens)
    route_commits, _ = _commits_and_session_opens(
        _ROUTE_STORAGE.read_text(encoding="utf-8"))
    assert route_commits == [], "the route module calls .commit()"


# ── 4. The cutoff of a request (hermetic) ───────────────────────────────────


@pytest.mark.parametrize("value", [
    "", "x", "2026-13-01",
    (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    "2999-01-01",
])
def test_a_before_that_is_not_a_past_date_answers_400(value) -> None:
    with pytest.raises(HTTPException) as err:
        storage_routes._cutoff(value)
    assert err.value.status_code == 400


def test_a_before_with_no_zone_is_utc() -> None:
    assert storage_routes._cutoff("2026-01-02") == datetime(2026, 1, 2, tzinfo=UTC)
    assert storage_routes._cutoff("2026-01-02T05:00:00+05:00") == datetime(
        2026, 1, 2, tzinfo=UTC)


# ── 5. The sync core (hermetic) ─────────────────────────────────────────────


class _Res:
    def __init__(self, row=None, rows=(), value=None) -> None:
        self.row = row
        self.rows = list(rows)
        self.value = value

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)

    def scalar(self):
        return self.value


def _account_row(**over) -> SimpleNamespace:
    now = _now()
    base = dict(id="acc-1", provider="microsoft", credentials_encrypted="x",
                last_history_id=None, sync_interval_secs=300,
                initial_sync_done=True, import_since=now - timedelta(days=30),
                import_reached_at=None, import_count=None,
                last_synced_at=now - timedelta(minutes=5), created_at=now,
                db_now=now, categories=[])
    base.update(over)
    return SimpleNamespace(**base)


def _sessions(row, log: list, *, meter, body_rows=(), embed_rows=()):
    """A ``tenant_session`` stand-in. ``meter`` is a list of values that the
    meter returns in turn, and the last one repeats."""
    readings = list(meter)

    @asynccontextmanager
    async def _ts(org=None):
        class _Db:
            async def execute(self, stmt, params=None, *_a, **_k):
                sql = _flat(stmt)
                log.append(sql)
                if "SET stored_bytes" in sql:
                    value = readings.pop(0) if len(readings) > 1 else readings[0]
                    return _Res(value=value)
                if "(body_text IS NULL OR body_text = '')" in sql:
                    return _Res(rows=body_rows)
                if "LEFT JOIN email_embeddings" in sql:
                    return _Res(rows=embed_rows)
                return _Res(row=row)

        yield _Db()
    return _ts


class _Provider:
    """A provider that counts the batches it fetches and the bodies and
    sweeps that the core asks for."""

    import_full_snapshot = True

    def __init__(self, batches=(), recurring=()) -> None:
        self.batches = list(batches)
        self.recurring = list(recurring)
        self.fetched = 0
        self.bodies: list[str] = []
        self.sweeps = 0

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {}

    async def import_batches(self, *, since, until=None, size=100,
                             on_estimate=None):
        if on_estimate is not None:
            await on_estimate(None)
        for batch in self.batches:
            self.fetched += 1
            yield batch

    async def sync_messages(self, **_kw) -> SyncResult:
        self.sweeps += 1
        return SyncResult(messages=list(self.recurring), new_history_id=None)

    async def get_message(self, provider_message_id):
        self.bodies.append(provider_message_id)
        return _full(provider_message_id)


def _msg(pid: str, at: datetime | None, body: str = "body") -> EmailMessage:
    return EmailMessage(provider_message_id=pid, thread_id=f"t-{pid}",
                        folder="inbox", subject=f"s {pid}", body_text=body,
                        from_address=EmailAddress(name="S", email="s@x.test"),
                        received_at=at)


@pytest.fixture()
def core_run(monkeypatch):
    """Run ``_sync_account`` on fake sessions whose meter is scripted."""
    from acb_llm import key_store

    embeds: list[int] = []
    reconciles: list[int] = []

    async def _noop(*_a, **_k):
        return None

    async def _embed(texts, model):
        embeds.append(len(texts))
        return [[0.5] for _ in texts]

    async def _reconcile(*_a, **_k):
        reconciles.append(1)

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "upsert_message", _noop)
    monkeypatch.setattr(sched, "run_label_learn_hook", _noop)
    # On main the reconcile of a member-act import is ``_reconcile_import``,
    # which takes the provider (EM-T6b fix round 3).
    monkeypatch.setattr(sched, "_reconcile_import", _reconcile)
    monkeypatch.setattr(email_embeddings, "_embed_batch", _embed)
    monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 1000)

    async def _run(provider, row, *, meter, body_rows=(), embed_rows=(), **kw):
        log: list[str] = []
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        monkeypatch.setattr(sched, "tenant_session", _sessions(
            row, log, meter=meter, body_rows=body_rows, embed_rows=embed_rows))
        res = await sched._sync_account("acc-1", organization_id=_ORG, **kw)
        return res, log

    return SimpleNamespace(run=_run, embeds=embeds, reconciles=reconciles)


@pytest.mark.parametrize(("meter", "stops"), [(999, False), (1000, True),
                                               (5000, True)])
async def test_phases_e_and_f_stop_at_the_limit(core_run, monkeypatch,
                                                meter, stops) -> None:
    """R7 fence ``email-storage-limit-q3`` (item 6). At or over the limit,
    the body backfill makes no provider call, and the embeddings make no
    model call. Under it, both run as before."""
    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", True)
    provider = _Provider()
    res, log = await core_run.run(
        provider, _account_row(), meter=[meter],
        body_rows=[SimpleNamespace(id="m-1", provider_message_id="pm-1")],
        embed_rows=[SimpleNamespace(id="m-1", subject="s", body_text="b")])
    assert "error" not in res, res
    assert provider.sweeps == 1, "the recurring sweep did not run (Q2)"
    if stops:
        assert provider.bodies == [], "phase (e) called the provider"
        assert core_run.embeds == [], "phase (f) called the model"
        assert not any("(body_text IS NULL OR body_text = '')" in s for s in log)
    else:
        assert provider.bodies == ["pm-1"]
        assert core_run.embeds == [1]


async def test_the_meter_runs_in_phase_d_after_the_new_mail(core_run) -> None:
    """Item 4. The meter runs at the end of each sync, in the block of phase
    (d), after the account and log rows."""
    res, log = await core_run.run(_Provider(recurring=[_msg("n1", _now())]),
                                  _account_row(), meter=[10])
    assert "error" not in res, res
    meters = [i for i, s in enumerate(log) if "SET stored_bytes" in s]
    success = [i for i, s in enumerate(log) if "SET status = 'success'" in s]
    assert len(meters) == 1 and success and meters[0] > success[0]


async def test_a_first_import_stops_after_the_batch_at_the_limit(core_run) -> None:
    """Item 5, on the core. The meter after batch 2 is at the limit, so the
    import fetches no batch 3 and ends at ``import_phase = 'limit'``."""
    now = _now()
    batches = [[_msg(f"m{b}{i}", now - timedelta(hours=b * 10 + i))
                for i in range(2)] for b in range(1, 5)]
    provider = _Provider(batches)
    res, log = await core_run.run(provider, _account_row(initial_sync_done=False),
                                  meter=[400, 1000, 1200])
    assert provider.fetched == 2, "the import fetched a batch after the limit"
    assert res.get("limit") is True, res
    assert any("import_phase = 'limit'" in s and "initial_sync_done = true" in s
               for s in log)
    assert not any("import_phase = 'done'" in s for s in log)
    assert provider.sweeps == 1, "new mail did not sync after the limit (Q2)"


async def test_a_deep_sync_at_the_limit_stops_and_skips_its_reconcile(
    core_run,
) -> None:
    """Item 5. A deep sync of a member act stops in the same way. It writes no
    progress, and it does not reconcile a snapshot that it did not finish."""
    now = _now()
    batches = [[_msg(f"d{b}", now - timedelta(hours=b))] for b in range(1, 5)]
    provider = _Provider(batches)
    res, log = await core_run.run(provider, _account_row(), meter=[1000],
                                  deep=True)
    assert provider.fetched == 1
    assert res.get("limit") is True, res
    assert core_run.reconciles == [], "a stopped import ran the reconcile"
    assert not any("import_phase" in s for s in log)


async def test_under_the_limit_the_result_has_no_limit_key(core_run) -> None:
    now = _now()
    provider = _Provider([[_msg("u1", now)], [_msg("u2", now - timedelta(hours=1))]])
    res, _ = await core_run.run(provider, _account_row(), meter=[10], deep=True)
    assert provider.fetched == 2
    assert "limit" not in res
    assert core_run.reconciles == [1]


# ── 6. R8 helpers ───────────────────────────────────────────────────────────


def _seed_msg(admin, *, account_id: str, org: str, received_at: datetime | None,
              body: str | None = None, thread_id: str | None = None,
              pid: str | None = None) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, body_text, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :p, :t, "
            "'inbox', '{}'::jsonb, '[]'::jsonb, 'subject', :b, :r, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": pid or f"pm-{uuid.uuid4().hex[:12]}",
             "t": thread_id, "b": body, "r": received_at, "o": org}).scalar_one())


def _set(admin, account_id: str, **cols) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with admin.begin() as c:
        c.execute(text(f"UPDATE email_accounts SET {sets} "
                       "WHERE id = CAST(:a AS uuid)"), {"a": account_id, **cols})


def _one(admin, sql: str, **params):
    with admin.connect() as c:
        return c.execute(text(sql), params).one()


def _scalar(admin, sql: str, **params):
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar()


def _wipe(admin, account_ids: list[str]) -> None:
    with admin.begin() as c:
        for aid in account_ids:
            for table in ("email_thread_status", "email_rule_guidance",
                          "email_executed_rules", "email_embeddings"):
                c.execute(text(f"DELETE FROM {table} WHERE account_id = "
                               "CAST(:a AS uuid)"), {"a": aid})
            c.execute(text("DELETE FROM email_messages WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": aid})
    _purge(admin, account_ids)


async def _measure(account_id: str, org: str) -> int | None:
    async with tenant_session(org) as db:
        return await storage.measure_stored_bytes(db, account_id)


def _dsn(p) -> str:
    return p.app_url.render_as_string(hide_password=False)


class _Offers:
    """A provider that offers fixed mail. Its import honours ``since`` unless
    ``ignore_since`` is set, and it reads the meter before each next batch.
    Its recurring sweep returns ``recurring``."""

    def __init__(self, mail, *, admin=None, account_id=None, recurring=(),
                 ignore_since=False) -> None:
        self.mail = list(mail)
        self.admin = admin
        self.account_id = account_id
        self.recurring = list(recurring)
        self.ignore_since = ignore_since
        self.fetched = 0
        self.meter_before_fetch: list[int | None] = []
        self.bodies: list[str] = []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {}

    def _window(self, since, until):
        return sorted((m for m in self.mail
                       if self.ignore_since or (
                           m.received_at >= since
                           and (until is None or m.received_at <= until))),
                      key=lambda m: m.received_at, reverse=True)

    async def import_batches(self, *, since, until=None, size=100,
                             on_estimate=None):
        msgs = self._window(since, until)
        if on_estimate is not None:
            await on_estimate(len(msgs))
        for start in range(0, len(msgs), size):
            if start and self.admin is not None:
                # The block of the batch before has committed by now.
                self.meter_before_fetch.append(_scalar(
                    self.admin, "SELECT stored_bytes FROM email_accounts "
                    "WHERE id = CAST(:a AS uuid)", a=self.account_id))
            self.fetched += 1
            yield msgs[start:start + size]

    async def sync_messages(self, **_kw) -> SyncResult:
        return SyncResult(messages=list(self.recurring), new_history_id=None)

    async def get_message(self, provider_message_id):
        self.bodies.append(provider_message_id)
        return _full(provider_message_id)


def _letters(n: int, now: datetime, *, chars: int = 4096) -> list[EmailMessage]:
    tag = uuid.uuid4().hex[:8]
    return [_msg(f"pm-{tag}-{k:03d}", now - timedelta(hours=k + 1),
                 body=_random_b64(chars)) for k in range(n)]


# ── 7. R8 ───────────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheMeterOnARealDatabase:

    async def test_a_body_of_100_kb_raises_its_mailbox_and_no_other(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Done-when: a body of 100 KB of random base64 raises the meter of
        mailbox X by 100,000 bytes or more, and the meter of Y not at all.
        Org A cannot measure the mailbox of org B."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"m-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        y = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _seed_msg(p.admin_engine, account_id=y, org=p.org_b, received_at=_now(),
                  body="small")
        token = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                x0, y0 = await _measure(x, p.org_b), await _measure(y, p.org_b)
                _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                          received_at=_now(), body=_random_b64(100_000))
                x1, y1 = await _measure(x, p.org_b), await _measure(y, p.org_b)
                from_a = await _measure(x, p.org_a)
            assert x0 == 0
            assert x1 - x0 >= 100_000, (x0, x1)
            assert y1 == y0 and y0 > 0
            stored, at = _one(p.admin_engine,
                              "SELECT stored_bytes, stored_bytes_at FROM "
                              "email_accounts WHERE id = CAST(:a AS uuid)", a=x)
            assert stored == x1 and at is not None
            assert from_a is None, "org A measured a mailbox of org B"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x, y])

    async def test_an_embedding_and_an_attachment_raise_the_meter(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        x = _seed_account(p.admin_engine, org=p.org_b, owner="e@em-t6c.test")
        mid = _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                        received_at=_now(), body="hello")
        token = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                before = await _measure(x, p.org_b)
                with p.admin_engine.begin() as c:
                    c.execute(text(
                        "INSERT INTO email_embeddings (message_id, account_id, "
                        "embedding, model, content_hash, organization_id) "
                        "VALUES (CAST(:m AS uuid), CAST(:a AS uuid), "
                        "CAST(:e AS vector), 'm', 'h', CAST(:o AS uuid))"),
                        {"m": mid, "a": x, "o": p.org_b,
                         "e": "[" + ",".join(["0.25"] * 1536) + "]"})
                with_embedding = await _measure(x, p.org_b)
                with p.admin_engine.begin() as c:
                    c.execute(text(
                        "INSERT INTO email_attachments (message_id, filename, "
                        "mime_type, provider_attachment_id, organization_id) "
                        "VALUES (CAST(:m AS uuid), 'report.pdf', "
                        "'application/pdf', 'att-1', CAST(:o AS uuid))"),
                        {"m": mid, "o": p.org_b})
                with_attachment = await _measure(x, p.org_b)
            assert with_embedding - before >= 1536 * 4, (before, with_embedding)
            assert with_attachment > with_embedding
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    def test_the_meter_names_every_column_of_variable_length(
        self, promoted,  # noqa: F811
    ):
        """R7 fence ``email-storage-meter``, R8 half. A column of variable
        length that the meter does not name fails here."""
        tables = (("email_messages", storage.MESSAGE_COLUMNS),
                  ("email_attachments", storage.ATTACHMENT_COLUMNS),
                  ("email_embeddings", storage.EMBEDDING_COLUMNS))
        with promoted.admin_engine.connect() as c:
            for table, named in tables:
                found = set(c.execute(text(
                    "SELECT attname FROM pg_attribute "
                    "WHERE attrelid = CAST(:t AS regclass) AND attnum > 0 "
                    "AND NOT attisdropped AND attlen = -1"),
                    {"t": table}).scalars())
                assert found == set(named), (table, found ^ set(named))


@_DB_GATE
class TestTheLimitOnARealDatabase:

    async def test_the_import_stops_at_the_batch_that_reaches_64_kb(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Done-when: with a limit of 64 KB, a fake provider offers 50
        messages with a body of 4 KB each. The import stops after the first
        batch that takes the meter to the limit, and fetches no next batch.
        It writes ``import_phase = 'limit'``. Each stored message is newer
        than each message that it did not store."""
        _assert_non_priv(app_engine)
        p = promoted
        limit = 64 * 1024
        now = _now()
        monkeypatch.setattr(storage, "storage_limit_bytes", lambda: limit)
        monkeypatch.setattr(sched, "IMPORT_BATCH_SIZE", 5)
        x = _seed_account(p.admin_engine, org=p.org_b, owner="i@em-t6c.test")
        _set(p.admin_engine, x, import_since=now - timedelta(days=30))
        mail = _letters(50, now)
        provider = _Offers(mail, admin=p.admin_engine, account_id=x)

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                res = await sched._sync_account(x, organization_id=p.org_b)
            assert "error" not in res, res
            assert res.get("limit") is True, res
            rows = _scalar(p.admin_engine, "SELECT count(*) FROM email_messages "
                           "WHERE account_id = CAST(:a AS uuid)", a=x)
            assert rows == 5 * provider.fetched
            assert 2 <= provider.fetched < 10, provider.fetched
            assert len(provider.meter_before_fetch) == provider.fetched - 1
            assert all(m is not None and m < limit
                       for m in provider.meter_before_fetch), (
                "the import fetched a batch after the meter reached the limit")
            phase, done, stored = _one(
                p.admin_engine, "SELECT import_phase, initial_sync_done, "
                "stored_bytes FROM email_accounts WHERE id = CAST(:a AS uuid)", a=x)
            assert (phase, done) == ("limit", True)
            assert stored >= limit
            with p.admin_engine.connect() as c:
                kept = set(c.execute(text(
                    "SELECT provider_message_id FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)"), {"a": x}).scalars())
            stored_at = [m.received_at for m in mail if m.provider_message_id in kept]
            dropped_at = [m.received_at for m in mail
                          if m.provider_message_id not in kept]
            assert dropped_at and min(stored_at) > max(dropped_at)
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    async def test_a_resync_at_the_limit_stops_and_says_limit(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Done-when: a Resync at the limit stops in the same way, and its
        result holds ``limit: true``. It changes no progress column."""
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 64 * 1024)
        monkeypatch.setattr(sched, "IMPORT_BATCH_SIZE", 5)
        x = _seed_account(p.admin_engine, org=p.org_b, owner="r@em-t6c.test")
        _set(p.admin_engine, x, import_since=now - timedelta(days=30),
             initial_sync_done=True, import_phase="done", import_count=7)
        provider = _Offers(_letters(50, now))

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                res = await sched._sync_account(
                    x, organization_id=p.org_b, deep=True, purge=True,
                    reset_cursor=True)
            assert res.get("limit") is True, res
            assert provider.fetched < 10
            phase, done, count = _one(
                p.admin_engine, "SELECT import_phase, initial_sync_done, "
                "import_count FROM email_accounts WHERE id = CAST(:a AS uuid)", a=x)
            assert (phase, done, count) == ("done", True, 7)
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    @pytest.mark.parametrize("full", [True, False], ids=["at_limit", "under"])
    async def test_at_the_limit_new_mail_syncs_and_e_and_f_stop(
        self, promoted, app_engine, monkeypatch, full,  # noqa: F811
    ):
        """Done-when (Q2): at the limit, the next poll still writes a new
        message. Done-when (Q3): at the limit, phases (e) and (f) make no
        provider call and no model call. Under the limit, both run."""
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        monkeypatch.setattr(storage, "storage_limit_bytes",
                            lambda: 1 if full else 100 * _MB)
        monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", True)
        x = _seed_account(p.admin_engine, org=p.org_b, owner="q@em-t6c.test")
        _set(p.admin_engine, x, import_since=now - timedelta(days=30),
             initial_sync_done=True, import_phase="limit",
             last_synced_at=now - timedelta(minutes=5))
        _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                  received_at=now - timedelta(days=2), body="kept")
        header_only = _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                                received_at=now - timedelta(days=1))
        fresh = _msg(f"new-{uuid.uuid4().hex[:8]}", now, body="")
        provider = _Offers([], recurring=[fresh])
        embedded: list[int] = []

        async def _embed(texts, model):
            embedded.append(len(texts))
            return [[0.001] * 1536 for _ in texts]

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        monkeypatch.setattr(email_embeddings, "_embed_batch", _embed)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                res = await sched._sync_account(x, organization_id=p.org_b)
            assert "error" not in res, res
            landed = _scalar(p.admin_engine,
                             "SELECT count(*) FROM email_messages WHERE "
                             "account_id = CAST(:a AS uuid) AND "
                             "provider_message_id = :p",
                             a=x, p=fresh.provider_message_id)
            assert landed == 1, "the poll did not write the new mail (Q2)"
            if full:
                assert provider.bodies == [], "phase (e) called the provider"
                assert embedded == [], "phase (f) called the model"
                assert _scalar(p.admin_engine, "SELECT body_text FROM "
                               "email_messages WHERE id = CAST(:m AS uuid)",
                               m=header_only) is None
            else:
                assert provider.bodies, "under the limit, phase (e) did not run"
                assert embedded, "under the limit, phase (f) did not run"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    @pytest.mark.parametrize("full", [True, False], ids=["at_limit", "under"])
    async def test_an_open_at_the_limit_shows_the_body_and_stores_nothing(
        self, promoted, app_engine, monkeypatch, full,  # noqa: F811
    ):
        """Done-when (Q3, Q4): at the limit, a member who opens a message with
        no stored body gets the body, and the open writes no body to the row,
        so the meter does not change. Under the limit, the open stores the
        body as it does today."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"o-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        mid = _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                        received_at=_now(), pid="pm-open")
        monkeypatch.setattr(storage, "storage_limit_bytes",
                            lambda: 1 if full else 100 * _MB)
        provider = _Offers([])

        async def _for_message(db, message_id, user_email):
            assert user_email == owner
            return provider, "pm-open", x, _Store()

        monkeypatch.setattr(messages, "_provider_for_message", _for_message)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                before = await _measure(x, p.org_b)
                shown = await messages.get_message(mid, user=me)
                after = await _measure(x, p.org_b)
            assert shown.body_text == "the body of pm-open"
            stored = _scalar(p.admin_engine, "SELECT body_text FROM email_messages "
                             "WHERE id = CAST(:m AS uuid)", m=mid)
            if full:
                assert stored is None, "the open stored a body at the limit (Q4)"
                assert after == before, "the open changed the meter at the limit"
            else:
                assert stored == "the body of pm-open"
                assert after > before
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])


@_DB_GATE
class TestTheRemovalOnARealDatabase:

    async def test_the_preview_counts_and_writes_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Done-when: the preview returns the count and the bytes, and the
        count of ``email_messages`` rows does not change."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"p-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        for days in (40, 50, 60):
            _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                      received_at=now - timedelta(days=days), body=_random_b64(2000))
        for days in (1, 2):
            _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                      received_at=now - timedelta(days=days), body="new")
        marked = datetime(2026, 1, 1, tzinfo=UTC)
        _set(p.admin_engine, x, stored_bytes=7, stored_bytes_at=marked)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                preview = await storage_routes.preview_older_mail(
                    x, before=(now - timedelta(days=30)).isoformat(), user=me)
            assert preview.messages == 3
            assert preview.bytes >= 6000
            count, stored, at = _one(
                p.admin_engine, "SELECT (SELECT count(*) FROM email_messages "
                "WHERE account_id = CAST(:a AS uuid)), stored_bytes, "
                "stored_bytes_at FROM email_accounts WHERE id = CAST(:a AS uuid)",
                a=x)
            assert count == 5, "the preview removed mail"
            assert (stored, at) == (7, marked), "the preview wrote the meter"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    async def test_a_removal_deletes_the_older_mail_of_one_mailbox(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Done-when: a removal with ``before`` 30 days back deletes each
        message of the mailbox older than that date, and no message of
        another mailbox. It deletes their ``email_executed_rules`` rows and
        moves ``import_since`` to ``before``. With every provider seam patched
        to raise, it still succeeds. Each chunk is one block, bound to the
        organization of the row."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"d-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        y = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        before = now - timedelta(days=30)
        _set(p.admin_engine, x, import_since=now - timedelta(days=90))
        old = [_seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                         received_at=now - timedelta(days=31 + k),
                         body=_random_b64(1500),
                         thread_id="t-old" if k < 3 else "t-mixed")
               for k in range(5)]
        new = [_seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                         received_at=now - timedelta(days=k + 1), body="new",
                         thread_id="t-mixed") for k in range(2)]
        undated = _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                            received_at=None, body="no date")
        other = _seed_msg(p.admin_engine, account_id=y, org=p.org_b,
                          received_at=now - timedelta(days=60), body="other box")
        with p.admin_engine.begin() as c:
            for mid in (old[0], new[0]):
                c.execute(text(
                    "INSERT INTO email_executed_rules (account_id, message_id, "
                    "organization_id) VALUES (CAST(:a AS uuid), CAST(:m AS uuid), "
                    "CAST(:o AS uuid))"), {"a": x, "m": mid, "o": p.org_b})
            c.execute(text(
                "INSERT INTO email_rule_guidance (account_id, guidance, "
                "message_id, organization_id) VALUES (CAST(:a AS uuid), "
                "'keep me', CAST(:m AS uuid), CAST(:o AS uuid))"),
                {"a": x, "m": old[1], "o": p.org_b})
            c.execute(text(
                "INSERT INTO email_embeddings (message_id, account_id, embedding, "
                "model, content_hash, organization_id) VALUES (CAST(:m AS uuid), "
                "CAST(:a AS uuid), CAST(:e AS vector), 'm', 'h', "
                "CAST(:o AS uuid))"),
                {"m": old[2], "a": x, "o": p.org_b,
                 "e": "[" + ",".join(["0.5"] * 1536) + "]"})
            c.execute(text(
                "INSERT INTO email_attachments (message_id, filename, "
                "provider_attachment_id, organization_id) VALUES "
                "(CAST(:m AS uuid), 'a.pdf', 'att-old', CAST(:o AS uuid))"),
                {"m": old[3], "o": p.org_b})
            for thread in ("t-old", "t-mixed"):
                c.execute(text(
                    "INSERT INTO email_thread_status (account_id, thread_id, "
                    "status, organization_id) VALUES (CAST(:a AS uuid), :t, "
                    "'FYI', CAST(:o AS uuid))"),
                    {"a": x, "t": thread, "o": p.org_b})

        def _raise(*_a, **_k):
            raise AssertionError("the removal reached the provider")

        from email_ingestion.providers import factory

        for target, name in ((factory, "build_provider"),
                             (sched, "build_provider"),
                             (core, "provider_session"),
                             (core, "_instantiate_provider"),
                             (core, "_provider_for_account"),
                             (core, "_provider_for_message")):
            monkeypatch.setattr(target, name, _raise)
        monkeypatch.setattr(storage, "REMOVE_CHUNK", 2)
        blocks: list[str | None] = []
        real_session = storage_routes._tenant_session

        @asynccontextmanager
        async def _counted(org=None):
            blocks.append(org)
            async with real_session(org) as db:
                yield db

        monkeypatch.setattr(storage_routes, "_tenant_session", _counted)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                meter_before = await _measure(x, p.org_b)
                preview = await storage_routes.preview_older_mail(
                    x, before=before.isoformat(), user=me)
                blocks.clear()
                done = await storage_routes.remove_older_mail(
                    x, storage_routes.RemoveOlderRequest(before=before.isoformat()),
                    user=me)
                again = await storage_routes.remove_older_mail(
                    x, storage_routes.RemoveOlderRequest(
                        before=(now - timedelta(days=60)).isoformat()), user=me)
            assert done.removed == 5 and preview.messages == 5
            assert again.removed == 0
            # One read, three chunks of 2, 2 and 1, and the last block.
            assert blocks[:5] == [None, p.org_b, p.org_b, p.org_b, p.org_b]
            left = set(_one(p.admin_engine,
                            "SELECT array_agg(id::text) FROM email_messages "
                            "WHERE account_id = CAST(:a AS uuid)", a=x)[0])
            assert left == {*new, undated}, "the removal took the wrong mail"
            assert _scalar(p.admin_engine, "SELECT count(*) FROM email_messages "
                           "WHERE id = CAST(:m AS uuid)", m=other) == 1, (
                "the removal deleted mail of another mailbox")
            rules = _one(p.admin_engine,
                         "SELECT array_agg(message_id::text) FROM "
                         "email_executed_rules WHERE account_id = CAST(:a AS uuid)",
                         a=x)[0]
            assert rules == [new[0]], "the rule history of removed mail stayed"
            guidance = _one(p.admin_engine, "SELECT count(*), "
                            "count(message_id) FROM email_rule_guidance "
                            "WHERE account_id = CAST(:a AS uuid)", a=x)
            assert tuple(guidance) == (1, 0), "the guidance went, or kept its id"
            for table, col in (("email_embeddings", "message_id"),
                               ("email_attachments", "message_id")):
                assert _scalar(p.admin_engine,
                               f"SELECT count(*) FROM {table} WHERE {col} = "
                               "ANY(CAST(:ids AS uuid[]))", ids=old) == 0, table
            threads = _one(p.admin_engine,
                           "SELECT array_agg(thread_id) FROM email_thread_status "
                           "WHERE account_id = CAST(:a AS uuid)", a=x)[0]
            assert threads == ["t-mixed"]
            since, stored = _one(p.admin_engine,
                                 "SELECT import_since, stored_bytes FROM "
                                 "email_accounts WHERE id = CAST(:a AS uuid)", a=x)
            assert since == before, "import_since did not move to before"
            assert done.stored_bytes == again.stored_bytes == stored
            assert meter_before - stored == preview.bytes, (
                "the preview did not name the bytes that the removal freed")
            assert done.storage_limit_bytes == 500 * _MB
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x, y])

    async def test_after_a_removal_a_resync_writes_no_older_mail(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Done-when: after the removal, a Resync writes no message older than
        ``before``, even from a provider that ignores ``since``."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"s-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        before = now - timedelta(days=30)
        _set(p.admin_engine, x, import_since=now - timedelta(days=90),
             initial_sync_done=True, import_phase="done")
        for days in (5, 45, 80):
            _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                      received_at=now - timedelta(days=days), body="b")
        offered = [_msg(f"r-{d}-{uuid.uuid4().hex[:6]}", now - timedelta(days=d))
                   for d in (3, 20, 40, 70, 100)]
        provider = _Offers(offered, recurring=offered, ignore_since=True)

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                await storage_routes.remove_older_mail(
                    x, storage_routes.RemoveOlderRequest(before=before.isoformat()),
                    user=me)
                res = await sched._sync_account(
                    x, organization_id=p.org_b, deep=True, purge=True,
                    reset_cursor=True)
            assert "error" not in res, res
            oldest, rows = _one(p.admin_engine,
                                "SELECT min(received_at), count(*) FROM "
                                "email_messages WHERE account_id = CAST(:a AS uuid)",
                                a=x)
            assert rows == 2 and oldest >= before, (
                "a Resync wrote mail older than the removal")
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    async def test_a_member_who_does_not_own_the_mailbox_gets_404(
        self, promoted, app_engine,  # noqa: F811
    ):
        """Done-when: R8, for two organizations, a member who does not own the
        mailbox gets 404 from both routes, and nothing is removed."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"w-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                  received_at=now - timedelta(days=40), body="old")
        when = (now - timedelta(days=30)).isoformat()
        strangers = (
            (p.org_b, UserContext(email="colleague@em-t6c.test",
                                  role=UserRole.EMPLOYEE, organization_id=p.org_b)),
            (p.org_a, UserContext(email=owner, role=UserRole.EMPLOYEE,
                                  organization_id=p.org_a)),
        )
        try:
            async with tenant_engine_scope(_dsn(p)):
                for org, who in strangers:
                    token = bind_tenant(org)
                    try:
                        with pytest.raises(HTTPException) as seen:
                            await storage_routes.preview_older_mail(
                                x, before=when, user=who)
                        assert seen.value.status_code == 404
                        with pytest.raises(HTTPException) as gone:
                            await storage_routes.remove_older_mail(
                                x, storage_routes.RemoveOlderRequest(before=when),
                                user=who)
                        assert gone.value.status_code == 404
                    finally:
                        release_tenant(token)
            assert _scalar(p.admin_engine, "SELECT count(*) FROM email_messages "
                           "WHERE account_id = CAST(:a AS uuid)", a=x) == 1
        finally:
            _wipe(p.admin_engine, [x])

    async def test_the_three_account_reads_return_the_meter_and_the_limit(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Item 14: ``EmailAccountModel`` gains ``stored_bytes`` and
        ``storage_limit_bytes``, and each account read returns them."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"a-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _set(p.admin_engine, x, stored_bytes=12345, import_phase="limit")
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)

        async def _no_restart(*_a, **_k):
            return None

        monkeypatch.setattr(sched, "refresh_account_sync", _no_restart)
        monkeypatch.setattr(sched, "remove_account_sync", _no_restart)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                [listed] = await accounts.list_accounts(user=me)
                made = await accounts.set_default_account(x, user=me)
                patched = await accounts.update_account(
                    x, accounts.AccountUpdateModel(onboarding_done=True), user=me)
            for model in (listed, made, patched):
                assert model.stored_bytes == 12345
                assert model.storage_limit_bytes == 500 * _MB
                assert model.import_phase == "limit"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])
