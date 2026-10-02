"""WS-17 EM-T6b — newest first, in batches, with progress and resume.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7, the part
"EM-T6b". Owner decisions D-EM-10 and D-EM-13 (§10.2).

R7 fences named here:

* ``email-import-newest-first``: ``OutlookProvider.import_batches`` merges
  the folders. The batches in sequence are newest first across every folder,
  and a folder gets its next page only after it held the newest head.
* ``email-import-estimate``: the estimate is the sum of the folder counts,
  and ``None`` when a folder gives no count. The import goes on.
* ``email-import-no-session-across-fetch``: the core fetches each batch with
  no session open, and one block writes the batch and its progress.
* ``email-import-progress``: after each batch, ``import_reached_at`` is the
  oldest mail written and ``import_count`` is the rows written so far (R8).
* ``email-import-resume``: an import that fails part way keeps its rows. The
  next sync asks for ``until = import_reached_at`` and ends ``done`` (R8).
* ``email-deep-sync-no-progress``: the deep sync of a member act writes in
  batches, and it changes no progress column and no ``initial_sync_done`` (R8).
* ``email-catch-up``: after a pause, the recurring sweep reads back to
  ``last_synced_at - 1 hour``. The floor still binds each page.
* ``email-import-no-reconcile``: no import batch reaches the reconcile or the
  label learner.

The R8 tests run the REAL core as the non-privileged role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS) on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_import_batches.py -v -rs
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from email_ingestion.providers.base import (
    EmailAddress,
    EmailFolder,
    EmailMessage,
    SyncResult,
)
from email_ingestion.providers.gmail import GmailProvider
from email_ingestion.providers.outlook import OutlookProvider
from gateway.routes.email.transport import accounts
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _count_as,
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

_FMT = "%Y-%m-%dT%H:%M:%SZ"
_ORG = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", False,
                        raising=False)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_FMT)


# ── a Graph mail API in memory ──────────────────────────────────────────────


def _parse_window(flt: str | None) -> tuple[datetime | None, datetime | None]:
    since = until = None
    for part in (flt or "").split(" and "):
        words = part.split()
        if len(words) != 3:
            continue
        at = datetime.strptime(words[2], _FMT).replace(tzinfo=UTC)
        if words[1] == "ge":
            since = at
        elif words[1] == "le":
            until = at
    return since, until


class _Resp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self.body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"Graph answered {self.status_code}")

    def json(self) -> dict:
        return self.body


def _graph_msg(path: str, i: int, at: datetime) -> dict:
    return {"id": f"{path}-{i:04d}", "receivedDateTime": _iso(at),
            "subject": f"{path} {i}", "conversationId": f"c-{path}-{i}",
            "internetMessageId": f"<{path}-{i}@graph.test>"}


class _FakeGraph:
    """Each folder is a list of receive times. It honours ``$filter``
    (``ge`` and ``le``), ``$top``, ``@odata.nextLink`` and ``$count``. A
    folder that it does not hold answers 404."""

    LINK = "https://graph.test/next/"

    def __init__(self, folders: dict[str, list[datetime]], *,
                 page_size: int | None = None, no_count: tuple = (),
                 fail_page: tuple | None = None) -> None:
        self.mail = {p: sorted(ts, reverse=True) for p, ts in folders.items()}
        self.page_size = page_size
        self.no_count = set(no_count)
        self.fail_page = fail_page
        self.pages: list[tuple[str, int, str | None]] = []
        self.counts: list[tuple[str, dict, dict]] = []
        self.on_page = None
        self._links: dict[str, tuple] = {}

    def ids(self, path: str) -> list[str]:
        return [f"{path}-{i:04d}" for i in range(len(self.mail[path]))]

    def _window(self, path: str, flt: str | None) -> list[tuple[int, datetime]]:
        since, until = _parse_window(flt)
        return [(i, t) for i, t in enumerate(self.mail[path])
                if (since is None or t >= since) and (until is None or t <= until)]

    async def get(self, url, params=None, headers=None):
        if url.startswith(self.LINK):
            return self._page(*self._links[url])
        path = url.split("/")[3]
        if path not in self.mail:
            return _Resp(404, {})
        params = params or {}
        flt = params.get("$filter")
        if params.get("$count") == "true":
            self.counts.append((path, dict(params), dict(headers or {})))
            if path in self.no_count:
                return _Resp(200, {"value": []})
            return _Resp(200, {"@odata.count": len(self._window(path, flt)),
                               "value": []})
        return self._page(path, flt, 0, int(params["$top"]), 1)

    def _page(self, path, flt, offset, top, page_no):
        self.pages.append((path, page_no, flt))
        if self.on_page is not None:
            self.on_page(path, page_no)
        if self.fail_page == (path, page_no):
            return _Resp(503, {})
        size = self.page_size or top
        window = self._window(path, flt)
        body: dict = {"value": [_graph_msg(path, i, t)
                                for i, t in window[offset:offset + size]]}
        if offset + size < len(window):
            link = f"{self.LINK}{path}/{page_no + 1}/{len(self._links)}"
            self._links[link] = (path, flt, offset + size, top, page_no + 1)
            body["@odata.nextLink"] = link
        return _Resp(200, body)

    def page_count(self, path: str) -> int:
        return sum(1 for p, _, _ in self.pages if p == path)


def _outlook(graph: _FakeGraph, user_folders: tuple[str, ...] = ()) -> OutlookProvider:
    p = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    p._get_client = AsyncMock(return_value=graph)  # type: ignore[method-assign]
    p.list_folders = AsyncMock(return_value=[  # type: ignore[method-assign]
        EmailFolder(provider_folder_id=f, name=f"Folder {f}", type="user")
        for f in user_folders])
    p.authenticate = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return p


def _interleaved(start: datetime, n: int, paths: tuple[str, ...],
                 step: timedelta = timedelta(minutes=7)) -> dict[str, list[datetime]]:
    """*n* receive times for each folder, dealt in turn, so the dates of the
    folders interleave."""
    out: dict[str, list[datetime]] = {p: [] for p in paths}
    for k in range(n * len(paths)):
        out[paths[k % len(paths)]].append(start - step * k)
    return out


async def _collect(it) -> list[list[EmailMessage]]:
    return [batch async for batch in it]


# ── 1. Outlook merges the folders (hermetic) ───────────────────────────────


T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
FLOOR = T0 - timedelta(days=30)
_THREE = ("inbox", "sentitems", "F-user")


async def test_the_batches_are_newest_first_across_three_folders() -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3)
    p = _outlook(graph, ("F-user",))

    batches = await _collect(p.import_batches(since=FLOOR, size=4))

    assert [len(b) for b in batches] == [4, 4, 4, 4, 4, 1]
    times = [m.received_at for b in batches for m in b]
    assert times == sorted(times, reverse=True), "a batch broke the order"
    assert len(set(times)) == 21
    folders = {m.folder for b in batches[:2] for m in b}
    assert len(folders) == 3, "the first batches did not mix the folders"
    # The four missing system folders answered 404 and were skipped.
    assert {p for p, _, _ in graph.pages} == set(_THREE)


async def test_a_folder_gets_its_next_page_only_after_it_held_the_newest_head() -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3)
    p = _outlook(graph, ("F-user",))
    taken: list[str] = []
    late: list[tuple[str, int, str | None]] = []
    graph.on_page = lambda path, page_no: late.append(
        (path, page_no, taken[-1] if taken else None)) if page_no > 1 else None

    async for batch in p.import_batches(since=FLOOR, size=1):
        taken.extend(m.provider_message_id for m in batch)

    assert len(late) == 6, "each folder of 7 messages reads 2 more pages"
    for path, page_no, last in late:
        assert last == f"{path}-{(page_no - 1) * 3 - 1:04d}", (
            f"{path} got page {page_no} while it did not hold the newest head")
    assert len(taken) == 21


async def test_each_stream_takes_the_window_and_a_resume_point() -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3)
    p = _outlook(graph, ("F-user",))
    until = T0 - timedelta(minutes=7 * 9)

    batches = await _collect(p.import_batches(since=FLOOR, until=until, size=5))

    want = f"receivedDateTime ge {_iso(FLOOR)} and receivedDateTime le {_iso(until)}"
    firsts = [flt for _, page_no, flt in graph.pages if page_no == 1]
    assert len(firsts) == 3 and all(flt == want for flt in firsts)
    got = [m.received_at for b in batches for m in b]
    assert max(got) == until, "the resume point itself is read again"
    assert len(got) == 21 - 9


async def test_the_estimate_is_the_sum_of_the_folder_counts() -> None:
    graph = _FakeGraph({"inbox": [T0 - timedelta(hours=h) for h in range(5)],
                        "sentitems": [T0 - timedelta(hours=h) for h in range(3)],
                        "F-user": [T0 - timedelta(days=d) for d in (1, 40)]})
    p = _outlook(graph, ("F-user",))
    seen: list[int | None] = []

    async def _estimate(n):
        seen.append(n)

    batches = await _collect(p.import_batches(since=FLOOR, on_estimate=_estimate))

    assert seen == [5 + 3 + 1], "the folder below the floor counted"
    assert sorted(path for path, _, _ in graph.counts) == sorted(_THREE)
    for _, params, headers in graph.counts:
        assert params["$count"] == "true" and params["$top"] == 1
        assert params["$filter"] == f"receivedDateTime ge {_iso(FLOOR)}"
        assert headers.get("ConsistencyLevel") == "eventual"
    assert sum(len(b) for b in batches) == 9


async def test_a_folder_with_no_count_gives_no_estimate_and_the_import_goes_on() -> None:
    graph = _FakeGraph(_interleaved(T0, 4, _THREE), no_count=("sentitems",))
    p = _outlook(graph, ("F-user",))
    seen: list[int | None] = []

    async def _estimate(n):
        seen.append(n)

    batches = await _collect(p.import_batches(since=FLOOR, on_estimate=_estimate))
    assert seen == [None]
    assert sum(len(b) for b in batches) == 12


async def test_a_later_page_that_fails_raises_and_a_missing_folder_does_not() -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3,
                       fail_page=("inbox", 2))
    p = _outlook(graph, ("F-user",))
    taken: list[EmailMessage] = []
    with pytest.raises(RuntimeError, match="503"):
        async for batch in p.import_batches(since=FLOOR, size=2):
            taken.extend(batch)
    assert taken, "the import wrote nothing before the failed page"


async def test_a_folder_reads_no_more_than_the_deep_page_cap(monkeypatch) -> None:
    graph = _FakeGraph({"inbox": [T0 - timedelta(minutes=m) for m in range(9)]},
                       page_size=2)
    p = _outlook(graph)
    monkeypatch.setattr(OutlookProvider, "DEEP_SYNC_MAX_PAGES", 3)
    batches = await _collect(p.import_batches(since=FLOOR, size=100))
    assert graph.page_count("inbox") == 3
    assert sum(len(b) for b in batches) == 6


async def test_the_default_import_sorts_cuts_and_drops_mail_newer_than_until() -> None:
    """Gmail and IMAP use the default of ``BaseEmailProvider`` (item 1)."""
    def _m(pid, at):
        return EmailMessage(provider_message_id=pid, thread_id=None,
                            folder="inbox", received_at=at)

    until = T0 - timedelta(hours=1)
    p = GmailProvider({"access_token": "x", "refresh_token": "y"})
    p.sync_messages = AsyncMock(return_value=SyncResult(messages=[  # type: ignore[method-assign]
        _m("b", T0 - timedelta(hours=3)), _m("new", T0), _m("none", None),
        _m("a", until), _m("c", (T0 - timedelta(hours=2)).replace(tzinfo=None)),
    ]))
    batches = await _collect(p.import_batches(since=FLOOR, until=until, size=2))
    assert [[m.provider_message_id for m in b] for b in batches] == [
        ["a", "c"], ["b", "none"]]
    assert p.sync_messages.await_args.kwargs == {"deep": True, "since": FLOOR}


# ── 2. The core (hermetic) ─────────────────────────────────────────────────


def _flat(stmt) -> str:
    return " ".join(str(stmt).split())


class _Res:
    def __init__(self, row) -> None:
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def scalar(self):
        return None


def _row(**over) -> SimpleNamespace:
    now = _now()
    base = dict(id="log-1", provider="microsoft", credentials_encrypted="x",
                last_history_id=None, sync_interval_secs=300,
                initial_sync_done=False, import_since=now - timedelta(days=30),
                import_reached_at=None, import_count=None,
                last_synced_at=None, created_at=now, categories=[])
    base.update(over)
    return SimpleNamespace(**base)


def _sessions(row, log: list, state: dict):
    """A ``tenant_session`` stand-in that counts the open blocks and logs each
    statement with the number of its block."""
    @asynccontextmanager
    async def _ts(org=None):
        state["open"] += 1
        state["opens"] += 1
        block = state["opens"]

        class _Db:
            async def execute(self, stmt, params=None, *_a, **_k):
                log.append((block, _flat(stmt), dict(params or {})))
                return _Res(row)

        try:
            yield _Db()
        finally:
            state["open"] -= 1
    return _ts


def _msg(pid: str, at: datetime | None) -> EmailMessage:
    return EmailMessage(provider_message_id=pid, thread_id=f"t-{pid}",
                        folder="inbox", subject=f"s {pid}", body_text="body",
                        from_address=EmailAddress(name="S", email="s@x.test"),
                        received_at=at)


class _Batches:
    """A provider whose import yields fixed batches and whose recurring sweep
    returns ``recurring``. It records each call into ``events``."""

    def __init__(self, batches, recurring=(), *, estimate=None, state=None,
                 events=None) -> None:
        self.batches = batches
        self.recurring = list(recurring)
        self.estimate = estimate
        self.state = state
        self.events = events if events is not None else []
        self.imports: list[dict] = []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {}

    async def import_batches(self, *, since, until=None, size=100,
                             on_estimate=None):
        self.imports.append({"since": since, "until": until, "size": size})
        if on_estimate is not None:
            self.events.append(("estimate", self._open()))
            await on_estimate(self.estimate)
        for batch in self.batches:
            self.events.append(("batch", self._open()))
            yield batch

    async def sync_messages(self, **kw) -> SyncResult:
        self.events.append(("sweep", self._open()))
        return SyncResult(messages=list(self.recurring), new_history_id=None,
                          full_snapshot=True)

    async def get_message(self, provider_message_id):
        raise RuntimeError("no body backfill here")

    def _open(self) -> int | None:
        return None if self.state is None else self.state["open"]


@pytest.fixture()
def core(monkeypatch):
    """Run ``_sync_account`` on watched fake sessions."""
    from acb_llm import key_store

    upserts: list[tuple[int, str]] = []
    state = {"open": 0, "opens": 0}
    log: list = []

    async def _upsert(db, account_id, msg):
        upserts.append((state["opens"], msg.provider_message_id))

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "upsert_message", _upsert)
    monkeypatch.setattr(sched, "run_label_learn_hook", _noop)

    async def _run(provider, row, **kw) -> dict:
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        monkeypatch.setattr(sched, "tenant_session", _sessions(row, log, state))
        return await sched._sync_account("acc-1", organization_id=_ORG, **kw)

    return SimpleNamespace(run=_run, upserts=upserts, state=state, log=log)


def _stmts(log: list, fragment: str) -> list[tuple[int, dict]]:
    return [(block, params) for block, sql, params in log if fragment in sql]


async def test_each_batch_is_fetched_with_no_session_open(core) -> None:
    now = _now()
    batches = [[_msg(f"m{i}", now - timedelta(minutes=i)) for i in (1, 2)],
               [_msg(f"m{i}", now - timedelta(minutes=i)) for i in (3, 4)],
               [_msg("m5", now - timedelta(minutes=5)), _msg("m6", None)]]
    provider = _Batches(batches, estimate=6, state=core.state)

    res = await core.run(provider, _row())

    assert "error" not in res, res
    assert provider.events == [("estimate", 0), ("batch", 0), ("batch", 0),
                               ("batch", 0), ("sweep", 0)]
    assert core.state["open"] == 0
    # The order of the progress: counting, the estimate, one write for each
    # batch in the block of its messages, then done.
    [(counting, c_params)] = _stmts(core.log, "import_phase = 'counting'")
    [(est, e_params)] = _stmts(core.log, "SET import_estimate = :estimate")
    progress = _stmts(core.log, "import_phase = 'importing'")
    [(done, _)] = _stmts(core.log, "import_phase = 'done'")
    assert c_params["count"] == 0 and e_params["estimate"] == 6
    assert counting < est < progress[0][0] and progress[-1][0] < done
    assert [p["count"] for _, p in progress] == [2, 4, 6]
    assert [p["reached"] for _, p in progress] == [
        now - timedelta(minutes=2), now - timedelta(minutes=4),
        now - timedelta(minutes=5)]
    blocks = [b for b, _ in progress]
    assert len(set(blocks)) == 3, "two batches shared one block"
    for b, pid in core.upserts[:6]:
        assert b in blocks, f"{pid} was not written in the block of its batch"
    assert res["synced"] == 6


async def test_a_resume_starts_at_the_point_it_reached_and_keeps_the_count(core) -> None:
    reached = _now() - timedelta(days=3)
    provider = _Batches([[_msg("m9", reached - timedelta(hours=1))]], estimate=4)

    await core.run(provider, _row(import_reached_at=reached, import_count=200))

    assert provider.imports[0]["until"] == reached
    [(_, c_params)] = _stmts(core.log, "import_phase = 'counting'")
    [(_, e_params)] = _stmts(core.log, "SET import_estimate = :estimate")
    [(_, b_params)] = _stmts(core.log, "import_phase = 'importing'")
    assert c_params["count"] == 200, "a resume started its count again"
    assert e_params["estimate"] == 204, "the estimate left out the rows so far"
    assert b_params["count"] == 201


async def test_no_estimate_writes_null(core) -> None:
    provider = _Batches([], estimate=None)
    await core.run(provider, _row())
    [(_, e_params)] = _stmts(core.log, "SET import_estimate = :estimate")
    assert e_params["estimate"] is None


async def test_a_deep_sync_of_a_member_act_writes_no_progress(core) -> None:
    now = _now()
    provider = _Batches([[_msg("m1", now)], [_msg("m2", now - timedelta(hours=1))]])

    res = await core.run(provider, _row(initial_sync_done=False,
                                        import_reached_at=now - timedelta(days=2)),
                         deep=True)

    assert res["synced"] == 2
    assert provider.imports == [{"since": provider.imports[0]["since"],
                                 "until": None, "size": sched.IMPORT_BATCH_SIZE}]
    progress_writes = ("import_phase", "SET import_estimate",
                       "import_count = :count", "import_reached_at = COALESCE",
                       "initial_sync_done =")
    for sql in (s for _, s, _ in core.log):
        assert not any(w in sql for w in progress_writes), sql
    # Each batch still has its own block.
    assert core.upserts[0][0] != core.upserts[1][0]


async def test_a_done_mailbox_runs_no_import(core) -> None:
    provider = _Batches([[_msg("m1", _now())]])
    await core.run(provider, _row(initial_sync_done=True))
    assert provider.imports == []
    assert provider.events == [("sweep", None)]


async def test_no_import_batch_reaches_the_reconcile_or_the_label_learner(
    core, monkeypatch,
) -> None:
    now = _now()
    reconciled: list[list[str]] = []
    learned: list[list[str]] = []

    async def _reconcile(db, account_id, sync_result):
        reconciled.append([m.provider_message_id for m in sync_result.messages])
        return 0

    async def _learn(hook, account_id, changes):
        learned.append([m.provider_message_id for m, _ in changes])

    async def _hook(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "reconcile_full_snapshot", _reconcile)
    monkeypatch.setattr(sched, "run_label_learn_hook", _learn)
    monkeypatch.setattr(sched.hooks, "learn_label_changes", _hook)
    provider = _Batches([[_msg("i1", now - timedelta(hours=2))],
                         [_msg("i2", now - timedelta(hours=3))]],
                        recurring=[_msg("r1", now)])

    await core.run(provider, _row())

    assert reconciled == [["r1"]]
    assert learned == [["r1"]]


# ── 3. The catch-up after a pause (hermetic, real Outlook) ─────────────────


def _catch_up_graph(now: datetime) -> _FakeGraph:
    """The inbox holds 600 messages of the last 21 days, then 300 older ones.
    Each other folder holds 250 messages over 60 days."""
    inbox = [now - timedelta(minutes=50 * k + 1) for k in range(600)]
    inbox += [now - timedelta(days=21.5) - timedelta(hours=2 * k) for k in range(300)]
    folders = {"inbox": inbox}
    for path in ("sentitems", "drafts", "archive", "junkemail", "deleteditems",
                 "F-user"):
        folders[path] = [now - timedelta(hours=5.76 * k + 0.5) for k in range(250)]
    return _FakeGraph(folders)


async def test_after_21_days_a_sync_writes_all_600_new_messages(core) -> None:
    now = _now()
    graph = _catch_up_graph(now)
    p = _outlook(graph, ("F-user",))

    res = await core.run(p, _row(initial_sync_done=True, import_since=None,
                                 last_synced_at=now - timedelta(days=21)))

    assert "error" not in res, res
    written = {pid for _, pid in core.upserts}
    assert set(graph.ids("inbox")[:600]) <= written, "the catch-up lost mail"
    assert graph.page_count("inbox") == 7
    for path in ("sentitems", "drafts", "archive", "junkemail", "deleteditems",
                 "F-user"):
        assert graph.page_count(path) == 2, path


async def test_five_minutes_after_a_sync_each_folder_reads_two_pages(core) -> None:
    now = _now()
    graph = _catch_up_graph(now)
    p = _outlook(graph, ("F-user",))

    await core.run(p, _row(initial_sync_done=True,
                           last_synced_at=now - timedelta(minutes=5)))

    for path in graph.mail:
        assert graph.page_count(path) == 2, path


async def test_after_21_days_off_the_sweep_requests_no_page_below_the_floor(core) -> None:
    now = _now()
    floor = now - timedelta(days=30)
    graph = _FakeGraph({"inbox": [now - timedelta(hours=2 * k + 1)
                                  for k in range(720)]})
    p = _outlook(graph)

    await core.run(p, _row(initial_sync_done=True, import_since=floor,
                           last_synced_at=now - timedelta(days=21)))

    firsts = [flt for _, page_no, flt in graph.pages if page_no == 1]
    assert firsts == [f"receivedDateTime ge {_iso(floor)}"]
    written = {pid for _, pid in core.upserts}
    paused = [pid for pid, t in zip(graph.ids("inbox"), graph.mail["inbox"],
                                    strict=True)
              if t >= now - timedelta(days=21)]
    assert paused and set(paused) <= written, "the mail of the pause is missing"
    old = [pid for pid, t in zip(graph.ids("inbox"), graph.mail["inbox"],
                                 strict=True) if t < floor]
    assert not written & set(old), "a message below the floor was written"
    assert graph.page_count("inbox") == 3, "the sweep paged on to the floor"


def test_the_watermark_is_the_last_sync_less_an_hour_or_the_connect() -> None:
    synced = T0 - timedelta(days=21)
    made = T0 - timedelta(days=40)
    assert sched._catch_up_watermark(SimpleNamespace(
        last_synced_at=synced, created_at=made)) == synced - timedelta(hours=1)
    assert sched._catch_up_watermark(SimpleNamespace(
        last_synced_at=None, created_at=made)) == made
    assert sched._catch_up_watermark(SimpleNamespace()) is None


# ── 4. R8 ───────────────────────────────────────────────────────────────────


def _progress(admin_engine, account_id: str) -> dict:
    with admin_engine.connect() as c:
        acct = dict(c.execute(text(
            "SELECT import_phase, import_count, import_estimate, "
            "import_reached_at, initial_sync_done FROM email_accounts "
            "WHERE id = CAST(:a AS uuid)"), {"a": account_id}).mappings().one())
        rows, oldest = c.execute(text(
            "SELECT count(*), min(received_at) FROM email_messages "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).one()
    return {**acct, "rows": rows, "oldest": oldest}


def _set(admin_engine, account_id: str, **cols) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with admin_engine.begin() as c:
        c.execute(text(f"UPDATE email_accounts SET {sets} "
                       "WHERE id = CAST(:a AS uuid)"), {"a": account_id, **cols})


def _drop(admin_engine, account_id: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_messages WHERE account_id = "
                       "CAST(:a AS uuid)"), {"a": account_id})
    _purge(admin_engine, [account_id])


def _watch(provider, admin_engine, account_id: str, snaps: list) -> None:
    """Record the progress in the database before each batch fetch and after
    the last. When the core asks for batch k + 1, the block of batch k has
    committed."""
    real = provider.import_batches

    async def _watched(**kw):
        async for batch in real(**kw):
            snaps.append(_progress(admin_engine, account_id))
            yield batch
        snaps.append(_progress(admin_engine, account_id))

    provider.import_batches = _watched


class _Scripted:
    """A provider that honours ``since`` and ``until``. Its import raises on
    batch ``fail_at`` once. Its recurring sweep finds nothing new."""

    def __init__(self, messages: list[EmailMessage], fail_at: int | None = None) -> None:
        self.messages = messages
        self.fail_at = fail_at
        self.asked: list[tuple] = []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {}

    async def import_batches(self, *, since, until=None, size=100, on_estimate=None):
        self.asked.append((since, until))
        msgs = sorted((m for m in self.messages
                       if m.received_at >= since
                       and (until is None or m.received_at <= until)),
                      key=lambda m: m.received_at, reverse=True)
        if on_estimate is not None:
            await on_estimate(len(msgs))
        for n, start in enumerate(range(0, len(msgs), size), 1):
            if n == self.fail_at:
                self.fail_at = None
                raise RuntimeError("Graph answered 503")
            yield msgs[start:start + size]

    async def sync_messages(self, **_kw) -> SyncResult:
        return SyncResult(messages=[], new_history_id=None)

    async def get_message(self, provider_message_id):
        raise RuntimeError("no body backfill here")


def _mail(n: int, now: datetime) -> list[EmailMessage]:
    tag = uuid.uuid4().hex[:8]
    return [_msg(f"pm-{tag}-{k:04d}", now - timedelta(minutes=10 * k + 1))
            for k in range(n)]


@_DB_GATE
class TestTheImportOnARealDatabase:

    async def test_an_import_in_org_b_writes_its_rows_and_progress_there(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        _set(p.admin_engine, account_id, import_since=now - timedelta(days=30))
        folders = _interleaved(now - timedelta(minutes=1), 84, _THREE,
                               step=timedelta(hours=2))
        folders["inbox"] = [*folders["inbox"][:-2],
                            now - timedelta(days=40), now - timedelta(days=41)]
        graph = _FakeGraph(folders)
        provider = _outlook(graph, ("F-user",))
        in_window = sum(1 for ts in folders.values() for t in ts
                        if t >= now - timedelta(days=30))
        snaps: list[dict] = []
        _watch(provider, p.admin_engine, account_id, snaps)

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id, organization_id=p.org_b)
            assert "error" not in res, res

            assert in_window == 250
            assert snaps[0]["import_phase"] == "counting"
            assert snaps[0]["import_estimate"] == 250
            assert snaps[0]["rows"] == 0
            after = snaps[1:]
            assert [s["rows"] for s in after] == [100, 200, 250]
            for s in after:
                assert s["import_phase"] == "importing"
                assert s["import_count"] == s["rows"]
                assert s["import_reached_at"] == s["oldest"]
            final = _progress(p.admin_engine, account_id)
            assert final["import_phase"] == "done"
            assert final["initial_sync_done"] is True
            assert final["import_count"] == 250 == final["rows"]
            assert final["import_estimate"] == 250

            msgs = ("SELECT count(*) FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)")
            prog = ("SELECT count(*) FROM email_accounts "
                    "WHERE id = CAST(:a AS uuid) AND import_count IS NOT NULL")
            params = {"a": account_id}
            assert _count_as(p.app_url, p.org_b, msgs, params) == 250
            assert _count_as(p.app_url, p.org_b, prog, params) == 1
            assert _count_as(p.app_url, p.org_a, msgs, params) == 0, (
                "org A read the imported mail of org B")
            assert _count_as(p.app_url, p.org_a, prog, params) == 0, (
                "org A read the import progress of org B")
            with p.admin_engine.connect() as c:
                orgs = c.execute(text(
                    "SELECT DISTINCT organization_id::text FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)"), params).scalars().all()
            assert orgs == [p.org_b]
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)

    async def test_a_failed_import_resumes_from_the_point_it_reached(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        _set(p.admin_engine, account_id, import_since=now - timedelta(days=30))
        mail = _mail(250, now)
        provider = _Scripted(mail, fail_at=3)

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                first = await sched._sync_account(account_id,
                                                  organization_id=p.org_b)
                assert "error" in first
                broken = _progress(p.admin_engine, account_id)
                assert broken["rows"] == 200
                assert broken["initial_sync_done"] is False
                assert broken["import_phase"] == "importing"
                assert broken["import_count"] == 200
                assert broken["import_reached_at"] == mail[199].received_at

                second = await sched._sync_account(account_id,
                                                   organization_id=p.org_b)
            assert "error" not in second, second
            assert provider.asked[1][1] == mail[199].received_at, (
                "the next sync did not resume at import_reached_at")
            done = _progress(p.admin_engine, account_id)
            assert done["rows"] == 250
            assert done["initial_sync_done"] is True
            assert done["import_phase"] == "done"
            # The message at the resume point is written twice, and the
            # upsert keeps one row. The count and the estimate both hold it.
            assert done["import_count"] == 251 == done["import_estimate"]
            assert done["import_reached_at"] == mail[-1].received_at
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)

    @pytest.mark.parametrize("was_done", [False, True])
    async def test_a_resync_writes_in_batches_and_keeps_the_progress(
        self, promoted, app_engine, monkeypatch, was_done,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        cols: dict = {"import_since": now - timedelta(days=30),
                      "initial_sync_done": was_done}
        if was_done:
            cols.update(import_phase="done", import_count=42,
                        import_estimate=50,
                        import_reached_at=now - timedelta(days=29))
        _set(p.admin_engine, account_id, **cols)
        before = _progress(p.admin_engine, account_id)
        provider = _Scripted(_mail(250, now))
        snaps: list[dict] = []
        _watch(provider, p.admin_engine, account_id, snaps)

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b,
                                                deep=True)
            assert res.get("synced") == 250, res
            assert [s["rows"] for s in snaps] == [0, 100, 200, 250], (
                "the resync did not write batch by batch")
            assert provider.asked[0][1] is None
            after = _progress(p.admin_engine, account_id)
            for col in ("import_phase", "import_count", "import_estimate",
                        "import_reached_at", "initial_sync_done"):
                assert after[col] == before[col], col
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)

    async def test_the_three_account_reads_return_the_progress(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t6b.test"
        account_id = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        reached = _now() - timedelta(days=12)
        _set(p.admin_engine, account_id, import_phase="importing",
             import_count=300, import_estimate=1200, import_reached_at=reached)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE,
                         organization_id=p.org_b)

        async def _no_restart(*_a, **_k):
            return None

        monkeypatch.setattr(sched, "refresh_account_sync", _no_restart)
        monkeypatch.setattr(sched, "remove_account_sync", _no_restart)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                [listed] = await accounts.list_accounts(user=me)
                made = await accounts.set_default_account(account_id, user=me)
                patched = await accounts.update_account(
                    account_id, accounts.AccountUpdateModel(onboarding_done=True),
                    user=me)
            for model in (listed, made, patched):
                assert model.import_phase == "importing"
                assert model.import_count == 300
                assert model.import_estimate == 1200
                assert datetime.fromisoformat(model.import_reached_at) == reached
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)
