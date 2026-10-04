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

The fences of the port (2026-10-04, the gaps G1 to G5 of §10.4.7):

* ``email-storage-g1-drafts``: the preview counts no draft, and the removal
  keeps each draft (R8, and the SQL of both statements).
* ``email-storage-g2-hydrate``: at the limit, ``hydrate_message_body``
  returns the body and writes none (R8).
* ``email-storage-g3-lock``: the removal holds the mailbox lock of the sync.
  It answers 409 while a sync holds the mailbox, and its first block under
  the lock moves ``import_since`` before any delete. It waits with no block
  open.
* ``email-storage-g4-phase``: a removal that takes the meter under the limit
  ends the ``limit`` phase, and one that does not keeps it (R8).
* ``email-storage-g5-ai-drafts``: the removal deletes the orphan rows of
  ``email_ai_drafts`` and keeps the others. A patched Mem0 client gets no
  call (R8).
* ``email-storage-multi-inbox``: a removal in mailbox A of a member leaves
  mailbox B of the same member unchanged (R8).
* ``email-storage-no-provider-reach``: a walk of the calls from each route,
  through ``email_ingestion/storage.py``, ``scheduler.py`` and ``core.py``,
  reaches no provider method and no provider builder. A companion test
  proves that the walk can fail.

The R8 tests run as the non-privileged role ``acb_app_h3rls`` (NOSUPERUSER,
NOBYPASSRLS) on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_storage_limit.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import base64
import inspect
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
    # The draft filter of the preview reads ``em.folder`` on purpose (G1). It
    # is a short WHERE test, never a size, so the check leaves it out.
    sql = _flat(stmt).replace(storage.KEPT_FOLDERS_SQL, "")
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
              pid: str | None = None, folder: str = "inbox") -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, body_text, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :p, :t, "
            ":f, '{}'::jsonb, '[]'::jsonb, 'subject', :b, :r, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": pid or f"pm-{uuid.uuid4().hex[:12]}",
             "t": thread_id, "b": body, "r": received_at, "o": org,
             "f": folder}).scalar_one())


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
                          "email_executed_rules", "email_embeddings",
                          "email_ai_drafts"):
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
            # One read, the block of the floor (G3), three chunks of 2, 2 and
            # 1, and the last block.
            assert blocks[:6] == [None] + [p.org_b] * 5
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


# ── 8. The gaps of the port, G1 and G3 (hermetic) ───────────────────────────


@pytest.mark.parametrize("stmt", [storage._PREVIEW, storage._CHUNK_IDS],
                         ids=["preview", "chunk"])
def test_the_preview_and_the_removal_skip_the_drafts(stmt) -> None:
    """R7 fence ``email-storage-g1-drafts``, SQL half. Both statements carry
    the one draft filter. The R8 half is
    ``test_the_removal_keeps_each_draft``."""
    assert storage.KEPT_FOLDERS_SQL in _flat(stmt)
    assert "NOT IN ('drafts', 'draft')" in storage.KEPT_FOLDERS_SQL


def _me(email: str = "m@em-t6c.test") -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=_ORG)


class _RouteDb:
    """A session of the route. The ownership read finds the row."""

    def __init__(self, block: int, log: list) -> None:
        self.block = block
        self.log = log

    async def execute(self, stmt, params=None, *_a, **_k):
        self.log.append((self.block, _flat(stmt)))
        return _Res(row=SimpleNamespace(organization_id=_ORG))


def _route_sessions(blocks: list, log: list):
    """A ``_tenant_session`` stand-in that numbers each block and records the
    organization that the route binds."""

    @asynccontextmanager
    async def _ts(org=None):
        blocks.append(org)
        yield _RouteDb(len(blocks) - 1, log)
    return _ts


def _record_steps(monkeypatch, calls: list, *, gone=(0,), stored: int = 10,
                  fail_chunk: bool = False) -> None:
    """Replace each step of ``email_ingestion/storage.py`` with a recorder.
    Each entry is ``(block, step, the mailbox lock is held)``."""
    left = list(gone)

    def _note(db, step: str, aid: str) -> None:
        calls.append((db.block, step, sched.sync_busy(aid)))

    async def advance(db, aid, before):
        _note(db, "advance_import_since", aid)

    async def chunk(db, aid, before, *, chunk):
        _note(db, "remove_older_chunk", aid)
        if fail_chunk:
            raise RuntimeError("the chunk failed")
        return left.pop(0) if left else 0

    async def thread_status(db, aid):
        _note(db, "delete_empty_thread_status", aid)
        return 0

    async def ai_drafts(db, aid):
        _note(db, "delete_orphan_ai_drafts", aid)
        return 0

    async def measure(db, aid):
        _note(db, "measure_stored_bytes", aid)
        return stored

    async def end_phase(db, aid, value):
        _note(db, "end_limit_phase", aid)
        return False

    for name, fn in (("advance_import_since", advance),
                     ("remove_older_chunk", chunk),
                     ("delete_empty_thread_status", thread_status),
                     ("delete_orphan_ai_drafts", ai_drafts),
                     ("measure_stored_bytes", measure),
                     ("end_limit_phase", end_phase)):
        monkeypatch.setattr(storage, name, fn)


def _no_lock_left() -> bool:
    return sched._sync_locks == {} and sched._sync_lock_users == {}


async def test_the_first_block_under_the_lock_moves_the_floor(monkeypatch) -> None:
    """R7 fence ``email-storage-g3-lock`` (G3). The ownership read runs with
    no lock. Then every step runs under the mailbox lock, and the first block
    moves ``import_since`` and does nothing else, before the first delete."""
    calls: list = []
    blocks: list = []
    log: list = []
    monkeypatch.setattr(storage_routes, "_tenant_session",
                        _route_sessions(blocks, log))
    monkeypatch.setattr(storage, "REMOVE_CHUNK", 2)
    _record_steps(monkeypatch, calls, gone=[2, 2, 1])
    done = await storage_routes.remove_older_mail(
        "ACC-1", storage_routes.RemoveOlderRequest(before="2026-01-01"),
        user=_me())
    assert done.removed == 5
    assert [(b, s) for b, s, _ in calls] == [
        (1, "advance_import_since"),
        (2, "remove_older_chunk"), (3, "remove_older_chunk"),
        (4, "remove_older_chunk"),
        (5, "delete_empty_thread_status"), (5, "delete_orphan_ai_drafts"),
        (5, "measure_stored_bytes"), (5, "end_limit_phase"),
    ], "the floor did not move in the first block, before any delete"
    assert all(held for *_, held in calls), "a step ran without the lock"
    assert blocks == [None] + [_ORG] * 5
    assert [b for b, _ in log] == [0], "the ownership read is not block 0"
    assert "user_id = :uid" in log[0][1]
    assert _no_lock_left(), "the removal kept the mailbox lock"


async def test_a_removal_answers_409_while_a_sync_holds_the_mailbox(
    monkeypatch,
) -> None:
    """R7 fence ``email-storage-g3-lock`` (G3). A sync holds the mailbox for
    longer than the wait, so the removal answers 409 and writes nothing. It
    opens no block after the ownership read."""
    started, release = asyncio.Event(), asyncio.Event()

    async def _cycle(account_id, **_kw):
        started.set()
        await release.wait()
        return {"synced": 0, "history_id": None}

    calls: list = []
    blocks: list = []
    monkeypatch.setattr(sched, "_sync_cycle", _cycle)
    monkeypatch.setattr(storage_routes, "REMOVAL_LOCK_WAIT_S", 0.05)
    monkeypatch.setattr(storage_routes, "_tenant_session",
                        _route_sessions(blocks, []))
    _record_steps(monkeypatch, calls)
    sync = asyncio.create_task(sched._sync_account("acc-1", organization_id=_ORG))
    await started.wait()
    try:
        with pytest.raises(HTTPException) as err:
            await storage_routes.remove_older_mail(
                "ACC-1", storage_routes.RemoveOlderRequest(before="2026-01-01"),
                user=_me())
        assert sched.sync_busy("acc-1"), "the sync lost its lock"
    finally:
        release.set()
        await sync
    assert err.value.status_code == 409
    assert err.value.detail == storage_routes.REMOVAL_BUSY_DETAIL
    assert calls == [], "the removal wrote while a sync held the mailbox"
    assert blocks == [None], "the removal opened a block after the read"
    assert _no_lock_left()


async def test_a_removal_runs_when_the_sync_ends_inside_the_wait(
    monkeypatch,
) -> None:
    """G3. The removal waits for the lock, and it runs when the sync ends
    inside the wait. A sync that tries to start during the removal skips."""
    started, release = asyncio.Event(), asyncio.Event()

    async def _cycle(account_id, **_kw):
        started.set()
        await release.wait()
        return {"synced": 0, "history_id": None}

    calls: list = []
    skipped: list = []
    monkeypatch.setattr(sched, "_sync_cycle", _cycle)
    monkeypatch.setattr(storage_routes, "REMOVAL_LOCK_WAIT_S", 5.0)
    monkeypatch.setattr(storage_routes, "_tenant_session",
                        _route_sessions([], []))
    _record_steps(monkeypatch, calls)

    async def advance(db, aid, before):
        calls.append((db.block, "advance_import_since", sched.sync_busy(aid)))
        skipped.append(await sched._sync_account(
            "acc-1", organization_id=_ORG, if_busy="skip"))

    monkeypatch.setattr(storage, "advance_import_since", advance)
    sync = asyncio.create_task(sched._sync_account("acc-1", organization_id=_ORG))
    await started.wait()
    removal = asyncio.create_task(storage_routes.remove_older_mail(
        "acc-1", storage_routes.RemoveOlderRequest(before="2026-01-01"),
        user=_me()))
    await asyncio.sleep(0.05)
    assert calls == [], "the removal ran while the sync held the mailbox"
    release.set()
    await sync
    done = await removal
    assert done.removed == 0
    assert calls[0][1] == "advance_import_since"
    assert skipped == [sched.SYNC_SKIPPED_BUSY], (
        "a sync ran during the removal")
    assert _no_lock_left()


async def test_a_failed_removal_releases_the_lock(monkeypatch) -> None:
    """G3. The lock is released on every exit, a failure included."""
    calls: list = []
    monkeypatch.setattr(storage_routes, "_tenant_session",
                        _route_sessions([], []))
    _record_steps(monkeypatch, calls, fail_chunk=True)
    with pytest.raises(RuntimeError):
        await storage_routes.remove_older_mail(
            "acc-1", storage_routes.RemoveOlderRequest(before="2026-01-01"),
            user=_me())
    assert [s for _, s, _ in calls] == ["advance_import_since",
                                        "remove_older_chunk"]
    assert _no_lock_left()


def _call_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    return getattr(node.func, "id", getattr(node.func, "attr", ""))


def _lock_waits_inside_a_block(source: str) -> list[int]:
    """The line of each ``hold_mailbox`` call that runs while a session block
    is open: in the body of the block, or as a later item of the same
    ``async with``."""
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncWith):
            continue
        items = [item.context_expr for item in node.items]
        if not any(_call_name(e).endswith("tenant_session") for e in items):
            continue
        hits += [n.lineno for part in [*items, *node.body]
                 for n in ast.walk(part) if _call_name(n) == "hold_mailbox"]
    return sorted(set(hits))


def test_the_removal_waits_for_the_lock_with_no_block_open() -> None:
    """G3. The wait must not hold a pool slot: the route calls
    ``hold_mailbox`` outside each session block, and the helper opens no
    session itself."""
    source = _ROUTE_STORAGE.read_text(encoding="utf-8")
    assert "hold_mailbox(" in source
    assert _lock_waits_inside_a_block(source) == []
    helper = inspect.getsource(sched.hold_mailbox)
    for opener in ("tenant_session", "get_db", "get_session_factory"):
        assert opener not in helper, f"hold_mailbox opens a session ({opener})"


def test_the_lock_block_check_can_fail() -> None:
    planted = (
        "async def f(a):\n"
        "    async with _tenant_session() as db:\n"
        "        async with hold_mailbox(a, wait_secs=5):\n"
        "            pass\n"
        "    async with _tenant_session(o) as db, hold_mailbox(a, wait_secs=5):\n"
        "        pass\n"
    )
    assert _lock_waits_inside_a_block(planted) == [3, 5]


# ── 9. No provider method is reachable from a route (hermetic) ─────────────

_SCHEDULER = _REPO / "apps/services/email_ingestion/email_ingestion/scheduler.py"
_CORE = _REPO / "apps/services/gateway/gateway/routes/email/core.py"
_BASE_PROVIDER = (
    _REPO / "apps/services/email_ingestion/email_ingestion/providers/base.py")

#: The modules that the walk reads. A name that one of them defines is
#: followed into its body. Any other name is a leaf.
_WALKED = (_ROUTE_STORAGE, _INGEST_STORAGE, _SCHEDULER, _CORE)


def _provider_methods() -> frozenset[str]:
    """Each method of ``BaseEmailProvider``, read from its source, so a new
    provider method joins the fence with no edit here."""
    tree = ast.parse(_BASE_PROVIDER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BaseEmailProvider":
            return frozenset(
                n.name for n in node.body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                and not n.name.startswith("__"))
    raise AssertionError("BaseEmailProvider is gone from providers/base.py")


def _defs(sources: list[str]) -> dict[str, list[ast.AST]]:
    found: dict[str, list[ast.AST]] = {}
    for source in sources:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.setdefault(node.name, []).append(node)
    return found


def _names_in(fn: ast.AST) -> set[str]:
    """Each name that a function body reads or calls. Its docstring does not
    count, so a function can say what it must not do."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
    return names


def reachable(sources: list[str], roots: tuple[str, ...]) -> set[str]:
    """Each name reachable from ``roots`` through the functions that
    ``sources`` define. The walk goes by name, so it is wide on purpose: a
    name that two modules define is followed into both."""
    defs = _defs(sources)
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for fn in defs.get(name, []):
            todo.extend(_names_in(fn) - seen)
    return seen


_FORBIDDEN = _PROVIDER_NAMES | _provider_methods()


def test_no_provider_method_is_reachable_from_a_route() -> None:
    """R7 fence ``email-storage-no-provider-reach`` (item 13, D-EM-14). The
    walk starts at both routes, and it follows the steps of
    ``email_ingestion/storage.py``, the lock of ``scheduler.py`` and the
    owner check of ``core.py``. No provider method and no provider builder
    is on the way."""
    sources = [p.read_text(encoding="utf-8") for p in _WALKED]
    reached = reachable(sources, ("remove_older_mail", "preview_older_mail"))
    # The walk is not vacuous: it reaches the lock, the steps and the check.
    assert {"hold_mailbox", "_leave_sync_lock", "remove_older_chunk",
            "end_limit_phase", "delete_orphan_ai_drafts",
            "_assert_account_owner"} <= reached
    assert reached & _FORBIDDEN == set(), sorted(reached & _FORBIDDEN)


def test_the_reach_fence_can_fail() -> None:
    """The companion: a provider call two calls deep is found."""
    planted = (
        "async def remove_older_mail(a):\n"
        "    await step(a)\n"
        "async def step(a):\n"
        "    p = make(a)\n"
        "    await p.trash_message(a)\n"
    )
    assert "trash_message" in reachable([planted], ("remove_older_mail",)) & _FORBIDDEN
    assert {"send_message", "modify_message", "move_to_folder",
            "build_provider", "provider_session"} <= _FORBIDDEN


# ── 10. The gaps of the port, on a real database ────────────────────────────


def _ai_draft(admin, *, account_id: str, org: str, thread_id: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_ai_drafts (account_id, thread_id, draft_text, "
            "organization_id) VALUES (CAST(:a AS uuid), :t, 'a draft', "
            "CAST(:o AS uuid))"), {"a": account_id, "t": thread_id, "o": org})


def _thread_status(admin, *, account_id: str, org: str, thread_id: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "organization_id) VALUES (CAST(:a AS uuid), :t, 'FYI', "
            "CAST(:o AS uuid))"), {"a": account_id, "t": thread_id, "o": org})


def _column(admin, sql: str, **params) -> list:
    with admin.connect() as c:
        return sorted(c.execute(text(sql), params).scalars())


def _mailbox_state(admin, account_id: str) -> dict:
    """Everything of one mailbox that a removal could change."""
    a = {"a": account_id}
    where = "WHERE account_id = CAST(:a AS uuid)"
    return {
        "messages": _column(admin, f"SELECT id::text FROM email_messages {where}", **a),
        "executed": _column(admin, "SELECT message_id::text FROM "
                            f"email_executed_rules {where}", **a),
        "threads": _column(admin, "SELECT thread_id FROM "
                           f"email_thread_status {where}", **a),
        "ai_drafts": _column(admin, "SELECT thread_id FROM "
                             f"email_ai_drafts {where}", **a),
        "account": tuple(_one(admin, "SELECT import_since, import_phase, "
                              "stored_bytes, stored_bytes_at, updated_at "
                              "FROM email_accounts WHERE id = CAST(:a AS uuid)",
                              **a)),
    }


@_DB_GATE
class TestTheGapsOnARealDatabase:

    async def test_the_removal_keeps_each_draft(
        self, promoted, app_engine,  # noqa: F811
    ):
        """R7 fence ``email-storage-g1-drafts`` (G1). An old message in the
        folder ``drafts`` stays, the preview does not count it, and its
        thread keeps its rows."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"g1-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        before = now - timedelta(days=30)
        old_inbox = _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                              received_at=now - timedelta(days=40), body="old",
                              thread_id="t-gone")
        drafts = [_seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                            received_at=now - timedelta(days=50), body="unsent",
                            thread_id="t-draft", folder=folder)
                  for folder in ("drafts", "Drafts", "draft")]
        _thread_status(p.admin_engine, account_id=x, org=p.org_b,
                       thread_id="t-draft")
        _ai_draft(p.admin_engine, account_id=x, org=p.org_b, thread_id="t-draft")
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                preview = await storage_routes.preview_older_mail(
                    x, before=before.isoformat(), user=me)
                done = await storage_routes.remove_older_mail(
                    x, storage_routes.RemoveOlderRequest(before=before.isoformat()),
                    user=me)
            assert preview.messages == 1, "the preview counted a draft"
            assert done.removed == 1
            left = _column(p.admin_engine, "SELECT id::text FROM email_messages "
                           "WHERE account_id = CAST(:a AS uuid)", a=x)
            assert left == sorted(drafts), "the removal took a draft"
            assert old_inbox not in left
            state = _mailbox_state(p.admin_engine, x)
            assert state["threads"] == ["t-draft"]
            assert state["ai_drafts"] == ["t-draft"]
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    @pytest.mark.parametrize("full", [True, False], ids=["at_limit", "under"])
    async def test_hydrate_at_the_limit_returns_the_body_and_writes_none(
        self, promoted, app_engine, monkeypatch, full,  # noqa: F811
    ):
        """R7 fence ``email-storage-g2-hydrate`` (G2, Q4). The reply drafter
        and the follow-up path call ``hydrate_message_body``. At the limit
        it returns the body and writes none. Under the limit it stores the
        body as before. The meter of the account row decides."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"g2-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        _set(p.admin_engine, x, stored_bytes=5000)
        mid = _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                        received_at=_now(), pid="pm-hydrate")
        monkeypatch.setattr(storage, "storage_limit_bytes",
                            lambda: 5000 if full else 5001)
        provider = _Offers([])

        async def _for_message(db, message_id, user_email):
            assert user_email == owner
            return provider, "pm-hydrate", x, _Store()

        monkeypatch.setattr(core, "_provider_for_message", _for_message)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)), tenant_session(p.org_b) as db:
                body = await core.hydrate_message_body(db, mid, owner)
            assert body == "the body of pm-hydrate"
            assert provider.bodies == ["pm-hydrate"]
            stored = _scalar(p.admin_engine, "SELECT body_text FROM "
                             "email_messages WHERE id = CAST(:m AS uuid)", m=mid)
            if full:
                assert stored is None, "hydrate stored a body at the limit (G2)"
            else:
                assert stored == "the body of pm-hydrate"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    @pytest.mark.parametrize("under", [True, False], ids=["under", "still_full"])
    async def test_a_removal_under_the_limit_ends_the_limit_phase(
        self, promoted, app_engine, monkeypatch, under,  # noqa: F811
    ):
        """R7 fence ``email-storage-g4-phase`` (G4). The last block writes
        ``import_phase = 'done'`` when the phase was ``limit`` and the new
        meter is under the limit. A meter still at the limit keeps
        ``limit``, so the notice of EM-T6e stays."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"g4-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        _set(p.admin_engine, x, import_phase="limit", initial_sync_done=True,
             import_since=now - timedelta(days=90))
        for days in (40, 50, 60):
            _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                      received_at=now - timedelta(days=days),
                      body=_random_b64(3000))
        _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                  received_at=now - timedelta(days=1), body="new")
        # Under: about 9,000 bytes go, and a few hundred stay.
        monkeypatch.setattr(storage, "storage_limit_bytes",
                            lambda: 4000 if under else 1)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                before_meter = await _measure(x, p.org_b)
                done = await storage_routes.remove_older_mail(
                    x, storage_routes.RemoveOlderRequest(
                        before=(now - timedelta(days=30)).isoformat()), user=me)
            assert before_meter >= 9000 and done.removed == 3
            phase, done_flag = _one(p.admin_engine, "SELECT import_phase, "
                                    "initial_sync_done FROM email_accounts "
                                    "WHERE id = CAST(:a AS uuid)", a=x)
            if under:
                assert done.stored_bytes < 4000
                assert phase == "done", "the limit phase stayed under the limit"
            else:
                assert phase == "limit", "the phase ended at the limit"
            assert done_flag is True
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    async def test_the_removal_deletes_the_orphan_ai_drafts_and_no_memory(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """R7 fence ``email-storage-g5-ai-drafts`` (G5, item 11). A draft of
        the AI whose thread has no message left goes. A draft of a thread
        with a message left stays. The Mem0 memories stay, and a patched
        Mem0 client gets no call."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"g5-{uuid.uuid4().hex[:6]}@em-t6c.test"
        x = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                  received_at=now - timedelta(days=40), body="old",
                  thread_id="t-old")
        _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                  received_at=now - timedelta(days=45), body="old",
                  thread_id="t-mixed")
        _seed_msg(p.admin_engine, account_id=x, org=p.org_b,
                  received_at=now - timedelta(days=2), body="new",
                  thread_id="t-mixed")
        for thread in ("t-old", "t-mixed"):
            _ai_draft(p.admin_engine, account_id=x, org=p.org_b, thread_id=thread)
        mem0_calls: list = []

        class _Mem0:
            def __getattr__(self, name):
                def _call(*a, **k):
                    mem0_calls.append(name)
                    raise AssertionError(f"the removal called Mem0 ({name})")
                return _call

        import acb_memory.mem0_client as mem0_client
        from gateway.routes.email import memory_purge

        monkeypatch.setattr(mem0_client, "get_memory_client",
                            lambda *a, **k: mem0_calls.append("get") or _Mem0())
        monkeypatch.setattr(memory_purge, "schedule_mailbox_memory_purge",
                            lambda *a, **k: mem0_calls.append("purge"))
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                done = await storage_routes.remove_older_mail(
                    x, storage_routes.RemoveOlderRequest(
                        before=(now - timedelta(days=30)).isoformat()), user=me)
            assert done.removed == 2
            assert _mailbox_state(p.admin_engine, x)["ai_drafts"] == ["t-mixed"]
            assert mem0_calls == [], "the removal reached Mem0"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [x])

    async def test_a_removal_in_one_mailbox_leaves_the_other_unchanged(
        self, promoted, app_engine,  # noqa: F811
    ):
        """R7 fence ``email-storage-multi-inbox``. Member M has mailboxes A
        and B with the same kind of mail. A removal in A leaves each row of
        B as it was: messages, rule history, thread statuses, drafts of the
        AI, the floor, the phase and the meter."""
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"mi-{uuid.uuid4().hex[:6]}@em-t6c.test"
        a = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        b = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        now = _now()
        for box in (a, b):
            _set(p.admin_engine, box, import_phase="limit",
                 import_since=now - timedelta(days=90), initial_sync_done=True)
            old = _seed_msg(p.admin_engine, account_id=box, org=p.org_b,
                            received_at=now - timedelta(days=40),
                            body=_random_b64(1000), thread_id="t-shared")
            _seed_msg(p.admin_engine, account_id=box, org=p.org_b,
                      received_at=now - timedelta(days=1), body="new",
                      thread_id="t-new")
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO email_executed_rules (account_id, message_id, "
                    "organization_id) VALUES (CAST(:a AS uuid), "
                    "CAST(:m AS uuid), CAST(:o AS uuid))"),
                    {"a": box, "m": old, "o": p.org_b})
            _thread_status(p.admin_engine, account_id=box, org=p.org_b,
                           thread_id="t-shared")
            _ai_draft(p.admin_engine, account_id=box, org=p.org_b,
                      thread_id="t-shared")
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(_dsn(p)):
                await _measure(a, p.org_b)
                await _measure(b, p.org_b)
                b_before = _mailbox_state(p.admin_engine, b)
                done = await storage_routes.remove_older_mail(
                    a, storage_routes.RemoveOlderRequest(
                        before=(now - timedelta(days=30)).isoformat()), user=me)
            assert done.removed == 1
            a_after = _mailbox_state(p.admin_engine, a)
            assert a_after["threads"] == [] and a_after["ai_drafts"] == []
            assert a_after["account"][1] == "done"
            assert _mailbox_state(p.admin_engine, b) == b_before, (
                "a removal in mailbox A changed mailbox B")
            assert len(b_before["messages"]) == 2 and b_before["account"][1] == "limit"
        finally:
            release_tenant(token)
            _wipe(p.admin_engine, [a, b])
