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

import httpx
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
    canonical_folder,
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
def _no_catch_up_misses():
    """Each test starts with no short catch-up counted (fix round 3)."""
    sched._catch_up_misses.clear()
    yield
    sched._catch_up_misses.clear()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", False,
                        raising=False)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_FMT)


# ── a Graph mail API in memory ──────────────────────────────────────────────


def _parse_window(flt: str | None) -> tuple[datetime | None, datetime | None, bool]:
    """``(since, upper, upper_is_open)``: ``ge``, then ``le`` or ``lt``."""
    since = upper = None
    open_upper = False
    for part in (flt or "").split(" and "):
        words = part.split()
        if len(words) != 3:
            continue
        at = datetime.strptime(words[2], _FMT).replace(tzinfo=UTC)
        if words[1] == "ge":
            since = at
        elif words[1] in ("le", "lt"):
            upper, open_upper = at, words[1] == "lt"
    return since, upper, open_upper


class _Resp:
    def __init__(self, status: int, body: dict, headers: dict | None = None) -> None:
        self.status_code = status
        self.body = body
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        """Raise as httpx does, so the provider can read the status."""
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://graph.test/page")
            raise httpx.HTTPStatusError(
                f"Graph answered {self.status_code}", request=req,
                response=httpx.Response(self.status_code, request=req,
                                        headers=self.headers))

    def json(self) -> dict:
        return self.body


def _graph_msg(mid: str, at: datetime) -> dict:
    return {"id": mid, "receivedDateTime": _iso(at), "subject": f"s {mid}",
            "conversationId": f"c-{mid}", "internetMessageId": f"<{mid}@graph.test>"}


#: The system folders that each mailbox holds. The fake adds them empty, so a
#: test names only the folders that it fills (fix round 2: their 404 raises).
_ALWAYS = ("inbox", "sentitems", "drafts", "junkemail", "deleteditems")


class _FakeGraph:
    """Each folder is a list of receive times. It honours ``$filter``
    (``ge``, ``le`` and ``lt``), ``$top``, ``$count``, and
    ``@odata.nextLink`` as a ``$skip`` offset over the LIVE list, as Graph
    does. A folder that it does not hold answers 404.

    A time may hold a fraction of a second, as in Exchange. The fake filters
    and sorts on the full time, and shows only the whole second, as Graph
    does (``_iso``).

    ``pages`` holds ``(folder, n, filter)`` for each page request, where
    ``n`` counts the requests of that folder, and ``tops`` holds its
    ``$top``. ``fail`` maps ``(folder, n)`` to the status that request
    answers. ``delete`` removes a message while a sync runs. ``missing``
    names system folders that the mailbox does not have."""

    LINK = "https://graph.test/next/"

    def __init__(self, folders: dict[str, list[datetime]], *,
                 page_size: int | None = None, no_count: tuple = (),
                 count_status: dict | None = None, fail: dict | None = None,
                 retry_after: str | None = None, ignore_until: bool = False,
                 missing: tuple = (), ignore_until_in: tuple = (),
                 fail_offset: dict | None = None, lookup_fail: tuple = ()) -> None:
        folders = {**{p: [] for p in _ALWAYS if p not in missing}, **folders}
        self.mail = {p: [(f"{p}-{i:04d}", t)
                         for i, t in enumerate(sorted(ts, reverse=True))]
                     for p, ts in folders.items()}
        self._all = {p: [mid for mid, _ in v] for p, v in self.mail.items()}
        self.page_size = page_size
        self.no_count = set(no_count)
        self.count_status = count_status or {}
        self.fail = fail or {}
        self.retry_after = retry_after
        self.ignore_until = ignore_until
        self.ignore_until_in = set(ignore_until_in)
        # ``fail_offset`` maps a folder to ``(offset, status)``: each page at
        # or past that offset fails, in every cycle.
        self.fail_offset = fail_offset or {}
        self.lookup_fail = set(lookup_fail)
        self.lookups: list[str] = []
        self.pages: list[tuple[str, int, str | None]] = []
        self.tops: list[tuple[str, int]] = []
        self.followed: list[tuple[str, int]] = []
        self.answers: dict[str, list[list[str]]] = {}
        self.counts: list[tuple[str, dict, dict]] = []
        self.on_page = None
        self._n: dict[str, int] = {}
        self._links: dict[str, tuple] = {}

    def ids(self, path: str) -> list[str]:
        """Every id of *path*, newest first, deleted ones too."""
        return list(self._all[path])

    def times(self, path: str) -> dict[str, datetime]:
        return dict(self.mail[path])

    def delete(self, path: str, mid: str) -> None:
        self.mail[path] = [(m, t) for m, t in self.mail[path] if m != mid]

    def _window(self, path: str, flt: str | None) -> list[tuple[str, datetime]]:
        since, upper, open_upper = _parse_window(flt)
        if self.ignore_until or path in self.ignore_until_in:
            upper = None
        return [(m, t) for m, t in self.mail[path]
                if (since is None or t >= since)
                and (upper is None or t < upper or (t == upper and not open_upper))]

    async def request(self, method, url, params=None, **_kw):
        """What ``_graph_send`` calls."""
        assert method == "GET", method
        return await self.get(url, params=params)

    def _lookup(self, params: dict) -> _Resp:
        """``/me/messages`` with ``internetMessageId eq '<id>'``: every folder."""
        wanted = params["$filter"].split(" eq ", 1)[1].strip("'").replace("''", "'")
        self.lookups.append(wanted)
        if wanted in self.lookup_fail:
            return _Resp(503, {})
        hits = [{"id": m} for msgs in self.mail.values() for m, _ in msgs
                if f"<{m}@graph.test>" == wanted]
        return _Resp(200, {"value": hits[:1]})

    async def get(self, url, params=None, headers=None):
        if url == "/me/messages":
            return self._lookup(params or {})
        if url.startswith(self.LINK):
            path = self._links[url][0]
            self.followed.append((path, self._n.get(path, 0) + 1))
            return self._page(*self._links[url])
        path = url.split("/")[3]
        if path not in self.mail:
            return _Resp(404, {})
        params = params or {}
        flt = params.get("$filter")
        if params.get("$count") == "true":
            self.counts.append((path, dict(params), dict(headers or {})))
            if path in self.count_status:
                return _Resp(self.count_status[path], {})
            if path in self.no_count:
                return _Resp(200, {"value": []})
            return _Resp(200, {"@odata.count": len(self._window(path, flt)),
                               "value": []})
        self.tops.append((path, int(params["$top"])))
        return self._page(path, flt, 0, int(params["$top"]))

    def _page(self, path, flt, offset, top):
        n = self._n[path] = self._n.get(path, 0) + 1
        self.pages.append((path, n, flt))
        if self.on_page is not None:
            self.on_page(path, n)
        if (path, n) in self.fail:
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return _Resp(self.fail[(path, n)], {}, headers)
        if path in self.fail_offset and offset >= self.fail_offset[path][0]:
            return _Resp(self.fail_offset[path][1], {})
        # ``page_size`` shrinks a normal page. A ``$top`` above 100 is the
        # query of one whole second, and Graph honours it.
        size = top if top > 100 else (self.page_size or top)
        window = self._window(path, flt)
        chunk = window[offset:offset + size]
        self.answers.setdefault(path, []).append([m for m, _ in chunk])
        body: dict = {"value": [_graph_msg(m, t) for m, t in chunk]}
        if offset + size < len(window):
            link = f"{self.LINK}{path}/{len(self._links)}"
            self._links[link] = (path, flt, offset + size, top)
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
    # Archive answered 404 and was skipped. The empty system folders read
    # one page each.
    assert {p for p, _, _ in graph.pages} == {*_THREE, *_ALWAYS}


async def test_a_folder_gets_its_next_page_only_after_it_held_the_newest_head() -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3)
    p = _outlook(graph, ("F-user",))
    taken: list[str] = []
    late: list[tuple[str, int, list[str], list[str]]] = []

    def _on_page(path, n):
        if n > 1:
            late.append((path, n, list(taken), list(graph.answers[path][-1])))

    graph.on_page = _on_page
    async for batch in p.import_batches(since=FLOOR, size=1):
        taken.extend(m.provider_message_id for m in batch)

    assert len(late) == 6, "each folder of 7 messages reads 2 more pages"
    for path, n, before, last_answer in late:
        assert before[-1] in last_answer and set(last_answer) <= set(before), (
            f"{path} got page {n} while it did not hold the newest head")
    assert sorted(taken) == sorted(i for f in _THREE for i in graph.ids(f))


async def test_each_stream_takes_the_window_and_a_resume_point() -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3)
    p = _outlook(graph, ("F-user",))
    until = T0 - timedelta(minutes=7 * 9)

    batches = await _collect(p.import_batches(since=FLOOR, until=until, size=5))

    top = _iso(until + timedelta(seconds=1))
    want = f"receivedDateTime ge {_iso(FLOOR)} and receivedDateTime lt {top}"
    firsts = [flt for _, n, flt in graph.pages if n == 1]
    assert len(firsts) == 3 + 3 and all(flt == want for flt in firsts)
    # Each later page is a new query: the floor and a lower ``lt``.
    later = [flt for _, n, flt in graph.pages if n > 1]
    assert later and all(flt.startswith(f"receivedDateTime ge {_iso(FLOOR)} and "
                                         "receivedDateTime lt ") for flt in later)
    got = [m.received_at for b in batches for m in b]
    assert max(got) == until, "the resume point itself is read again"
    assert len(got) == 21 - 9


async def test_a_move_out_of_a_folder_during_the_import_loses_no_message() -> None:
    """The case of the reviewer probe. A ``$skip`` link shifts when the member
    moves a message out of the inbox, and the message at the page edge was
    never read. Each page is now a new query by time."""
    inbox = [T0 - timedelta(minutes=10 * k) for k in range(500)]
    sent = [T0 - timedelta(minutes=10 * k + 5) for k in range(500)]
    graph = _FakeGraph({"inbox": inbox, "sentitems": sent})
    p = _outlook(graph)
    moved: list[str] = []

    def _member_archives_one(path, n):
        if path == "inbox" and n == 4 and not moved:
            moved.append(graph.ids("inbox")[5])
            graph.delete("inbox", moved[0])

    graph.on_page = _member_archives_one
    seen: list[str] = []
    async for batch in p.import_batches(since=T0 - timedelta(days=60), size=100):
        seen.extend(m.provider_message_id for m in batch)

    assert moved, "the probe did not move a message"
    lost = sorted(set(graph.times("inbox")) - set(seen))
    assert lost == [], f"the import never read {lost}"
    assert len(seen) == len(set(seen)), "the import read a message twice"


async def test_a_second_that_fills_a_page_is_read_in_one_query() -> None:
    """Seven messages share one second, and a page holds three. The bound
    cannot move, so one query with ``$top=1000`` reads that second (fix
    round 2, item 5). No ``$skip`` link is followed, so a move inside that
    second cannot shift it."""
    second = T0 - timedelta(hours=1)
    same = [second + timedelta(milliseconds=100 * k) for k in range(7)]
    graph = _FakeGraph({"inbox": [T0, *same, T0 - timedelta(hours=2)]},
                       page_size=3)
    p = _outlook(graph)

    batches = await _collect(p.import_batches(since=FLOOR, size=100))

    got = [m.provider_message_id for b in batches for m in b]
    assert sorted(got) == sorted(graph.ids("inbox"))
    assert len(got) == len(set(got)) == 9
    assert graph.followed == []
    whole = (f"receivedDateTime ge {_iso(second)} and receivedDateTime lt "
             f"{_iso(second + timedelta(seconds=1))}")
    assert ("inbox", 1000) in graph.tops
    assert any(flt == whole for p_, _, flt in graph.pages if p_ == "inbox")


async def test_a_page_edge_inside_a_second_loses_no_message() -> None:
    """Fix round 2, item 1. Exchange keeps a fraction of a second, and Graph
    shows whole seconds. Page 1 ends at 10:00:05.9 and 10:00:05.4. A next
    page of ``le 10:00:05`` is ``le 10:00:05.000``, and it dropped
    10:00:05.1."""
    s = datetime(2026, 9, 30, 10, 0, 5, tzinfo=UTC)
    inbox = [s + timedelta(seconds=7, milliseconds=500),
             s + timedelta(milliseconds=900), s + timedelta(milliseconds=400),
             s + timedelta(milliseconds=100), s - timedelta(seconds=9)]
    graph = _FakeGraph({"inbox": inbox}, page_size=3)
    p = _outlook(graph)

    batches = await _collect(p.import_batches(since=FLOOR, size=100))

    got = [m.provider_message_id for b in batches for m in b]
    assert sorted(got) == sorted(graph.ids("inbox")), "a split second lost mail"
    assert len(got) == len(set(got))


@pytest.mark.parametrize("seed", [0, 1, 2])
async def test_bursts_with_hidden_fractions_lose_and_repeat_nothing(seed) -> None:
    """The reviewer simulation, ported: bursts of 1 to 250 messages inside
    one second, each with a hidden fraction, read in pages of 100."""
    import random

    rnd = random.Random(seed)
    times: list[datetime] = []
    at = T0
    while len(times) < 2000:
        for _ in range(rnd.choice([1] * 6 + [2, 3, 5, 8, 40, 130, 250])):
            times.append(at + timedelta(microseconds=rnd.randrange(1_000_000)))
        at -= timedelta(seconds=rnd.randint(1, 600))
    graph = _FakeGraph({"inbox": times[:2000]})
    p = _outlook(graph)

    got = [m.provider_message_id
           for b in await _collect(p.import_batches(since=T0 - timedelta(days=170)))
           for m in b]

    assert sorted(got) == sorted(graph.ids("inbox"))
    assert len(got) == len(set(got))


async def test_a_server_that_ignores_the_bound_ends_the_folder(caplog) -> None:
    """A page that adds no new message would repeat for ever. It ends the
    folder and logs ``sync.import_folder_capped``."""
    graph = _FakeGraph({"inbox": [T0 - timedelta(minutes=m) for m in range(9)]},
                       page_size=3, ignore_until=True)
    p = _outlook(graph)
    with caplog.at_level("WARNING"):
        batches = await _collect(p.import_batches(since=FLOOR, size=100))
    assert sum(len(b) for b in batches) == 3
    assert graph.page_count("inbox") == 2
    assert any("sync.import_folder_capped" in r.getMessage() for r in caplog.records)


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
    assert sorted(path for path, _, _ in graph.counts) == sorted({*_THREE, *_ALWAYS})
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


async def test_a_count_that_answers_503_gives_no_estimate_and_every_message() -> None:
    """Verifier F1: a count request that fails is no failure of the import."""
    graph = _FakeGraph(_interleaved(T0, 4, _THREE), count_status={"inbox": 503})
    p = _outlook(graph, ("F-user",))
    seen: list[int | None] = []

    async def _estimate(n):
        seen.append(n)

    batches = await _collect(p.import_batches(since=FLOOR, on_estimate=_estimate))
    assert seen == [None]
    got = sorted(m.provider_message_id for b in batches for m in b)
    assert got == sorted(i for f in _THREE for i in graph.ids(f))


@pytest.fixture()
def slept(monkeypatch) -> list[float]:
    """Record the waits of a retry, and do not wait."""
    from email_ingestion.providers import outlook

    waits: list[float] = []

    async def _sleep(delay, *_a, **_k):
        waits.append(delay)

    monkeypatch.setattr(outlook.asyncio, "sleep", _sleep)
    return waits


async def test_a_later_page_that_fails_raises(slept) -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3,
                       fail={("inbox", 2): 500})
    p = _outlook(graph, ("F-user",))
    taken: list[EmailMessage] = []
    with pytest.raises(httpx.HTTPStatusError, match="500"):
        async for batch in p.import_batches(since=FLOOR, size=2):
            taken.extend(batch)
    assert taken, "the import wrote nothing before the failed page"
    assert slept == [], "a 500 was tried again"


@pytest.mark.parametrize("status", [429, 503, 504])
async def test_a_page_tries_once_more_after_retry_after(slept, status) -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3,
                       fail={("inbox", 2): status}, retry_after="7")
    p = _outlook(graph, ("F-user",))
    batches = await _collect(p.import_batches(since=FLOOR, size=4))
    assert sum(len(b) for b in batches) == 21
    assert slept == [7.0]


async def test_a_retry_waits_no_longer_than_the_bound(slept) -> None:
    graph = _FakeGraph({"inbox": [T0]}, fail={("inbox", 1): 429},
                       retry_after="900")
    p = _outlook(graph)
    await _collect(p.import_batches(since=FLOOR))
    assert slept == [30.0]


async def test_a_page_that_fails_twice_raises(slept) -> None:
    graph = _FakeGraph(_interleaved(T0, 7, _THREE), page_size=3,
                       fail={("inbox", 2): 503, ("inbox", 3): 503})
    p = _outlook(graph, ("F-user",))
    with pytest.raises(httpx.HTTPStatusError, match="503"):
        await _collect(p.import_batches(since=FLOOR, size=4))
    assert len(slept) == 1


async def test_a_first_page_that_fails_with_another_status_raises(slept) -> None:
    """Fix round 1, item 2: only a 404 skips a folder."""
    graph = _FakeGraph(_interleaved(T0, 3, _THREE), fail={("sentitems", 1): 500})
    p = _outlook(graph, ("F-user",))
    with pytest.raises(httpx.HTTPStatusError, match="500"):
        await _collect(p.import_batches(since=FLOOR))


async def test_a_missing_archive_and_user_folder_are_skipped_and_logged(caplog) -> None:
    """Fix round 2, item 6: a 404 skips only Archive or a user folder."""
    graph = _FakeGraph(_interleaved(T0, 3, ("inbox",)))
    p = _outlook(graph, ("F-gone",))
    with caplog.at_level("INFO"):
        batches = await _collect(p.import_batches(since=FLOOR))
    assert sum(len(b) for b in batches) == 3
    skipped = sorted(r.getMessage() for r in caplog.records
                     if "sync.import_folder_skipped" in r.getMessage())
    assert skipped == ["sync.import_folder_skipped folder=F-gone status=404",
                       "sync.import_folder_skipped folder=archive status=404"]


@pytest.mark.parametrize("folder", ["inbox", "sentitems", "drafts"])
async def test_a_404_on_a_folder_that_each_mailbox_has_raises(folder) -> None:
    """Fix round 2, item 6: the import never writes ``done`` without one."""
    graph = _FakeGraph({} if folder == "inbox" else _interleaved(T0, 3, ("inbox",)),
                       missing=(folder,))
    p = _outlook(graph)
    with pytest.raises(httpx.HTTPStatusError, match="404"):
        await _collect(p.import_batches(since=FLOOR))


async def test_a_folder_reads_no_more_than_the_import_page_cap(monkeypatch, caplog) -> None:
    graph = _FakeGraph({"inbox": [T0 - timedelta(minutes=m) for m in range(9)]},
                       page_size=2)
    p = _outlook(graph)
    monkeypatch.setattr(OutlookProvider, "IMPORT_MAX_PAGES", 3)
    with caplog.at_level("WARNING"):
        batches = await _collect(p.import_batches(since=FLOOR, size=100))
    assert graph.page_count("inbox") == 3
    # Each page after the first reads its boundary message again.
    assert sum(len(b) for b in batches) == 4
    assert any("sync.import_folder_capped folder=inbox pages=3" in r.getMessage()
               for r in caplog.records)


def test_the_import_cap_is_far_above_the_deep_sweep() -> None:
    """Fix round 1, item 7: 200 pages cut mail out of a large folder."""
    assert OutlookProvider.IMPORT_MAX_PAGES >= 25 * OutlookProvider.DEEP_SYNC_MAX_PAGES


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
                last_synced_at=None, created_at=now, db_now=now, categories=[])
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


async def test_a_resync_reconciles_once_against_its_whole_import(core, monkeypatch) -> None:
    """Fix round 1, item 6. After the deep sync of a member act, one block
    reconciles against every message that the import wrote, before the
    reconcile of the recurring sweep. The first import does not."""
    now = _now()
    imports: list[tuple[list[str], datetime]] = []
    sweeps: list[int] = []

    async def _import(db, account_id, snapshot, *, started_at):
        # Fix round 2, item 7: tuples of (id, folder, received_at).
        assert all(isinstance(e, tuple) and len(e) == 3 for e in snapshot)
        imports.append((sorted(pid for pid, _, _ in snapshot), started_at))
        return []

    async def _sweep(db, account_id, sync_result):
        sweeps.append(len(sync_result.messages))
        return 0

    monkeypatch.setattr(sched, "import_reconcile_candidates", _import)
    monkeypatch.setattr(sched, "reconcile_full_snapshot", _sweep)
    graph = _FakeGraph(_interleaved(now - timedelta(minutes=1), 30, _THREE))
    p = _outlook(graph, ("F-user",))
    start = now - timedelta(seconds=3)

    await core.run(p, _row(initial_sync_done=True, db_now=start), deep=True)

    every = sorted(i for f in _THREE for i in graph.ids(f))
    assert imports == [(every, start)], "the resync did not reconcile its import"
    assert len(sweeps) == 1

    imports.clear()
    await core.run(p, _row(initial_sync_done=False))
    assert imports == [], "the first import reconciled its batches"


async def test_a_failed_import_still_writes_the_new_mail(core) -> None:
    """Fix round 2, item 3 (owner answer Q2). The import fails on its second
    batch. The recurring sweep still runs and writes the new mail, and phase
    (d) records the error. The import is not ``done``."""
    now = _now()

    class _Breaks(_Batches):
        async def import_batches(self, **kw):
            n = 0
            async for batch in super().import_batches(**kw):
                n += 1
                if n == 2:
                    raise RuntimeError("Graph answered 500")
                yield batch

    provider = _Breaks([[_msg("old-1", now - timedelta(days=2))],
                        [_msg("old-2", now - timedelta(days=3))]],
                       recurring=[_msg("new-1", now)])

    res = await core.run(provider, _row())

    assert res["error"] == "Graph answered 500"
    assert res["synced"] == 2
    assert [pid for _, pid in core.upserts] == ["old-1", "new-1"]
    # Fix round 3, item 1(b): the mailbox never finished a sync, so
    # ``last_synced_at`` stays NULL and the next sweep reads back to the
    # connect.
    [(_, d_params)] = _stmts(core.log, "last_synced_at = CASE")
    assert d_params["keep_watermark"] is True
    [(_, e_params)] = _stmts(core.log, "SET sync_status = 'error', sync_error")
    assert e_params["import_error"] == "Graph answered 500"
    assert _stmts(core.log, "import_phase = 'done'") == []

    # A mailbox with a sync point moves it on: the sweep was complete.
    core.log.clear()
    again = _Breaks([[_msg("old-3", now - timedelta(days=4))],
                     [_msg("old-4", now - timedelta(days=5))]],
                    recurring=[_msg("new-2", now)])
    await core.run(again, _row(last_synced_at=now - timedelta(hours=1)))
    [(_, d_params)] = _stmts(core.log, "last_synced_at = CASE")
    assert d_params["keep_watermark"] is False


async def test_a_count_that_answers_503_still_writes_every_message(core) -> None:
    """Verifier F1, through the core: the estimate is NULL and the import
    writes each message."""
    now = _now()
    graph = _FakeGraph(_interleaved(now - timedelta(minutes=1), 40, _THREE),
                       count_status={"sentitems": 503})
    p = _outlook(graph, ("F-user",))

    res = await core.run(p, _row())

    assert "error" not in res, res
    [(_, e_params)] = _stmts(core.log, "SET import_estimate = :estimate")
    assert e_params["estimate"] is None
    every = {i for f in _THREE for i in graph.ids(f)}
    assert every <= {pid for _, pid in core.upserts}
    [(_, done)] = _stmts(core.log, "import_phase = 'done'")
    assert done == {"id": "acc-1"}


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
    assert firsts and set(firsts) == {f"receivedDateTime ge {_iso(floor)}"}
    written = {pid for _, pid in core.upserts}
    times = graph.times("inbox")
    paused = [pid for pid, t in times.items() if t >= now - timedelta(days=21)]
    assert paused and set(paused) <= written, "the mail of the pause is missing"
    old = [pid for pid, t in times.items() if t < floor]
    assert not written & set(old), "a message below the floor was written"
    assert graph.page_count("inbox") == 3, "the sweep paged on to the floor"


_PHASE_D = "last_synced_at = CASE"


async def test_a_catch_up_page_that_fails_keeps_the_watermark(core, slept) -> None:
    """Verifier F3, fix round 2. Page 4 of the inbox fails during the
    catch-up of 21 days. The cycle writes the pages that it read, keeps
    ``last_synced_at``, and succeeds, so new mail lands. The next cycle reads
    the whole pause."""
    now = _now()
    graph = _catch_up_graph(now)
    graph.fail = {("inbox", 4): 500}
    p = _outlook(graph, ("F-user",))
    row = _row(initial_sync_done=True, import_since=None,
               last_synced_at=now - timedelta(days=21))

    res = await core.run(p, row)

    assert "error" not in res, res
    written = {pid for _, pid in core.upserts}
    assert set(graph.ids("inbox")[:300]) <= written, "the pages read were lost"
    assert set(graph.ids("sentitems")[:200]) <= written
    [(_, d_params)] = _stmts(core.log, _PHASE_D)
    assert d_params["keep_watermark"] is True, "the failed catch-up moved last_synced_at"

    core.upserts.clear()
    again = await core.run(p, row)
    assert "error" not in again, again
    assert set(graph.ids("inbox")[:600]) <= {pid for _, pid in core.upserts}


async def test_a_normal_poll_keeps_the_watermark_when_a_folder_fails(core, slept) -> None:
    """Fix round 3. A folder whose page fails with a status other than 403
    or 404 is unread this cycle. The sweep writes the other folders, keeps
    the watermark, and the cycle is a soft failure with a short note."""
    now = _now()
    graph = _catch_up_graph(now)
    graph.fail = {("inbox", 2): 500}
    p = _outlook(graph, ("F-user",))

    res = await core.run(p, _row(initial_sync_done=True, import_since=None,
                                 last_synced_at=now - timedelta(minutes=5)))

    assert "error" not in res, res
    written = {pid for _, pid in core.upserts}
    assert not any(pid.startswith("inbox-") for pid in written)
    assert set(graph.ids("sentitems")[:200]) <= written
    [(_, d_params)] = _stmts(core.log, _PHASE_D)
    assert d_params["keep_watermark"] is True
    assert d_params["sync_note"] == (
        "The catch-up of inbox is incomplete. The next sync tries again.")
    assert res["catch_up_incomplete"] is True


async def test_a_sweep_page_that_answers_503_once_is_read_again(core, slept) -> None:
    now = _now()
    graph = _catch_up_graph(now)
    graph.fail = {("inbox", 2): 503}
    p = _outlook(graph, ("F-user",))

    res = await core.run(p, _row(initial_sync_done=True, import_since=None,
                                 last_synced_at=now - timedelta(minutes=5)))

    assert "error" not in res, res
    assert set(graph.ids("inbox")[:200]) <= {pid for _, pid in core.upserts}
    assert slept == [1.0]


@pytest.mark.parametrize(("status", "short"),
                         [(500, True), (429, True), (403, False), (404, False)])
async def test_a_first_page_that_fails_leaves_its_folder_short(slept, status, short) -> None:
    """Fix round 3, item 1(a). A folder whose first page fails is unread. Only
    a missing (404) or forbidden (403) folder is skipped with no harm."""
    now = _now()
    graph = _FakeGraph({"inbox": [now - timedelta(hours=k + 1) for k in range(5)],
                        "sentitems": [now - timedelta(hours=k + 2) for k in range(5)]},
                       fail={("inbox", 1): status, ("inbox", 2): status})
    p = _outlook(graph)

    res = await p.sync_messages(deep=False, since=now - timedelta(days=30),
                                catch_up=now - timedelta(hours=2))

    assert res.catch_up_incomplete is short
    assert res.catch_up_folders == (["inbox"] if short else [])
    assert set(graph.ids("sentitems")) <= {m.provider_message_id for m in res.messages}


async def test_a_catch_up_that_always_fails_moves_on_after_six_cycles(
    core, slept, caplog,
) -> None:
    """Fix round 3, item 2. Page 4 of the inbox fails in every cycle. Each
    short cycle keeps the watermark, writes a note with the folder only, and
    is a soft failure. The sixth one moves the watermark on and logs
    ``sync.catch_up_abandoned``, and the count starts again."""
    now = _now()
    graph = _catch_up_graph(now)
    graph.fail_offset = {"inbox": (300, 500)}
    p = _outlook(graph, ("F-user",))
    row = _row(initial_sync_done=True, import_since=None,
               last_synced_at=now - timedelta(days=21))
    keeps, notes, results = [], [], []

    with caplog.at_level("WARNING"):
        for _ in range(7):
            core.log.clear()
            results.append(await core.run(p, row))
            [(_, d_params)] = _stmts(core.log, _PHASE_D)
            keeps.append(d_params["keep_watermark"])
            notes.append(d_params["sync_note"])

    assert keeps == [True] * 5 + [False, True]
    assert notes[0] == "The catch-up of inbox is incomplete. The next sync tries again."
    assert notes[5] == ("The catch-up of inbox stopped after 6 tries. "
                        "Older mail of the pause can be missing.")
    assert all(r.get("catch_up_incomplete") is True and "error" not in r
               for r in results)
    [abandoned] = [r.getMessage() for r in caplog.records
                   if "sync.catch_up_abandoned" in r.getMessage()]
    assert "folder=inbox" in abandoned and "misses=6" in abandoned
    gap = int(abandoned.split("gap_secs=")[1].split()[0])
    assert abs(gap - (21 * 86400 + 3600)) < 120


async def test_a_complete_cycle_starts_the_count_again(core, slept) -> None:
    now = _now()
    graph = _catch_up_graph(now)
    p = _outlook(graph, ("F-user",))
    row = _row(initial_sync_done=True, import_since=None,
               last_synced_at=now - timedelta(days=21))
    keeps = []
    for fail in [True] * 5 + [False] + [True] * 5:
        graph.fail_offset = {"inbox": (300, 500)} if fail else {}
        core.log.clear()
        await core.run(p, row)
        [(_, d_params)] = _stmts(core.log, _PHASE_D)
        keeps.append(d_params["keep_watermark"])
    assert keeps == [True] * 5 + [False] + [True] * 5


async def test_a_short_catch_up_backs_the_loop_off(monkeypatch) -> None:
    """Fix round 3, item 2: the loop sleeps the backoff, not the interval."""
    import asyncio

    waits: list[float] = []

    async def _sync(account_id, **_kw):
        return {"synced": 0, "history_id": None, "catch_up_incomplete": True}

    async def _hook(*_a, **_k):
        return None

    async def _interval(*_a, **_k):
        return 300

    async def _sleep(secs):
        waits.append(secs)
        raise asyncio.CancelledError

    monkeypatch.setattr(sched, "_sync_account", _sync)
    monkeypatch.setattr(sched, "run_hook", _hook)
    monkeypatch.setattr(sched, "_get_account_sync_interval", _interval)
    monkeypatch.setattr(sched.asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        await sched._account_sync_loop("acc-1", 300, organization_id=_ORG)
    assert waits == [600]


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
                with p.admin_engine.connect() as c:
                    status = c.execute(text(
                        "SELECT a.sync_status, a.sync_error, l.status "
                        "FROM email_accounts a JOIN email_sync_log l "
                        "ON l.account_id = a.id WHERE a.id = CAST(:a AS uuid)"),
                        {"a": account_id}).one()
                assert tuple(status) == ("error", "Graph answered 503", "error"), (
                    "phase (d) did not record the failed import")
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

    async def test_a_resync_trashes_mail_deleted_in_outlook_in_org_b(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 1, item 6. A message that the member deleted in
        Outlook lies inside the window of the import, beyond the newest
        pages. The Resync moves it to trash in org B, and keeps the rest."""
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        _set(p.admin_engine, account_id, import_since=now - timedelta(days=30),
             initial_sync_done=True)
        mail = _mail(250, now)
        gone = f"pm-gone-{uuid.uuid4().hex[:8]}"
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "internet_message_id, folder, from_address, to_addresses, "
                "subject, received_at, organization_id) VALUES "
                "(CAST(:a AS uuid), :pid, '<gone@seed.test>', 'inbox', "
                "'{}'::jsonb, '[]'::jsonb, 'deleted in Outlook', :r, "
                "CAST(:o AS uuid))"),
                {"a": account_id, "pid": gone, "r": mail[150].received_at
                 + timedelta(minutes=3), "o": p.org_b})
        provider = _Scripted(mail)
        provider.import_full_snapshot = True
        asked: list[str] = []

        async def _exists(internet_message_id):
            # Fix round 3: the reconcile asks the provider before a trash.
            asked.append(internet_message_id)
            return False

        provider.message_exists = _exists

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b, deep=True)
            assert "error" not in res, res
            with p.admin_engine.connect() as c:
                folders = dict(c.execute(text(
                    "SELECT provider_message_id, folder FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).all())
            assert folders.pop(gone) == "trash", "the resync kept a deleted message"
            assert asked == ["<gone@seed.test>"]
            assert set(folders.values()) == {"inbox"} and len(folders) == 250
            gone_sql = ("SELECT count(*) FROM email_messages WHERE account_id = "
                        "CAST(:a AS uuid) AND folder = 'trash'")
            assert _count_as(p.app_url, p.org_b, gone_sql, {"a": account_id}) == 1
            assert _count_as(p.app_url, p.org_a, gone_sql, {"a": account_id}) == 0
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)

    async def _resync_with_rows(self, p, monkeypatch, graph, user_folders, rows,
                                hook=None):
        """Seed *rows* in org B, run a Resync over *graph*, and return the
        folder of each seeded row after it. Each row is ``(key, folder,
        received_at)``, and Graph does not hold it."""
        now = _now()
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        _set(p.admin_engine, account_id, import_since=now - timedelta(days=30),
             initial_sync_done=True)
        ids = {}
        with p.admin_engine.begin() as c:
            for key, folder, received, *imid in rows:
                ids[key] = f"pm-{key}-{uuid.uuid4().hex[:8]}"
                c.execute(text(
                    "INSERT INTO email_messages (account_id, provider_message_id, "
                    "internet_message_id, folder, from_address, to_addresses, "
                    "subject, received_at, updated_at, organization_id) VALUES "
                    "(CAST(:a AS uuid), :pid, :imid, :f, '{}'::jsonb, '[]'::jsonb, "
                    ":key, :r, now() - interval '1 day', CAST(:o AS uuid))"),
                    {"a": account_id, "pid": ids[key], "f": folder, "key": key,
                     "imid": imid[0] if imid else f"<{key}@seed.test>",
                     "r": received, "o": p.org_b})
        if hook is not None:
            graph.on_page = lambda path, n: hook(path, n, account_id, ids)
        provider = _outlook(graph, user_folders)

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(account_id,
                                                organization_id=p.org_b, deep=True)
            assert "error" not in res, res
            with p.admin_engine.connect() as c:
                folders = dict(c.execute(text(
                    "SELECT subject, folder FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid) AND subject = ANY(:k)"),
                    {"a": account_id, "k": list(ids)}).all())
            trashed = ("SELECT count(*) FROM email_messages WHERE account_id = "
                       "CAST(:a AS uuid) AND folder = 'trash'")
            assert _count_as(p.app_url, p.org_a, trashed, {"a": account_id}) == 0
            return folders
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)

    async def test_the_import_reconcile_keeps_what_it_did_not_read(
        self, promoted, app_engine, monkeypatch, caplog,  # noqa: F811
    ):
        """Fix round 2, item 4, on real SQL. Graph holds none of the seeded
        rows. Only ``deleted`` is a real delete: it lies in the window of the
        import, outside the newest pages of the sweep, and nothing touched it.
        Every other row stays."""
        _assert_non_priv(app_engine)
        now = _now()
        inbox = [now - timedelta(hours=k + 1) for k in range(10)]
        capped = [now - timedelta(hours=k + 1, minutes=30) for k in range(10)]
        graph = _FakeGraph({"inbox": inbox, "F-cap": capped}, page_size=3,
                           missing=(), ignore_until_in=("F-cap",))
        graph.mail.pop("archive", None)
        cap_folder = canonical_folder("Folder F-cap")

        def _member_moves_one(path, n, account_id, ids):
            # A move, a rule action or a draft during the import writes the row.
            if path == "inbox" and n == 2:
                with promoted.admin_engine.begin() as c:
                    c.execute(text(
                        "UPDATE email_messages SET updated_at = clock_timestamp() "
                        "WHERE provider_message_id = :pid"), {"pid": ids["moved"]})

        rows = [("deleted", "inbox", now - timedelta(hours=8, minutes=30)),
                ("moved", "inbox", now - timedelta(hours=7, minutes=30)),
                ("below-floor", "inbox", now - timedelta(days=40)),
                ("skipped-404", "archive", now - timedelta(hours=5)),
                ("below-cap", cap_folder, now - timedelta(hours=9))]
        with caplog.at_level("WARNING"):
            folders = await self._resync_with_rows(
                promoted, monkeypatch, graph, ("F-cap",), rows,
                hook=_member_moves_one)

        assert folders == {"deleted": "trash", "moved": "inbox",
                           "below-floor": "inbox", "skipped-404": "archive",
                           "below-cap": cap_folder}, folders
        assert any("sync.import_folder_capped folder=F-cap" in r.getMessage()
                   for r in caplog.records), "the user folder was not capped"

    async def test_a_mass_trash_skips_the_folder(
        self, promoted, app_engine, monkeypatch, caplog,  # noqa: F811
    ):
        """Fix round 2, item 2(b). Sixty rows of the inbox window are absent
        from the import. That is a gap in the read, not real deletes, so the
        folder is skipped and logged, and every row stays."""
        _assert_non_priv(app_engine)
        now = _now()
        graph = _FakeGraph({"inbox": [now - timedelta(hours=k + 1)
                                      for k in range(10)]}, page_size=3)
        rows = [(f"gap-{k:02d}", "inbox",
                 now - timedelta(hours=7, minutes=k + 1)) for k in range(60)]
        with caplog.at_level("WARNING"):
            folders = await self._resync_with_rows(
                promoted, monkeypatch, graph, (), rows)
        assert set(folders.values()) == {"inbox"} and len(folders) == 60
        skipped = [r.getMessage() for r in caplog.records
                   if "sync.import_reconcile_skipped" in r.getMessage()]
        assert len(skipped) == 1 and "folder=inbox candidates=60" in skipped[0]

    async def test_the_reconcile_keeps_a_row_whose_message_graph_still_has(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 3, item 3. A move in the Outlook client gives the message
        a new id, and the old row keeps its ``updated_at``. Before the trash,
        the reconcile asks Graph by ``internetMessageId``. A row whose message
        Graph still has stays, and so does a row whose lookup fails or that
        has no internet message id. Only the row that Graph lost goes."""
        _assert_non_priv(app_engine)
        now = _now()
        graph = _FakeGraph({"inbox": [now - timedelta(hours=k + 1) for k in range(10)],
                            "outbox": [now - timedelta(hours=8)]},
                           page_size=3, lookup_fail=("<flaky@seed.test>",))
        moved_imid = f"<{graph.ids('outbox')[0]}@graph.test>"
        rows = [("client-moved", "inbox", now - timedelta(hours=8), moved_imid),
                ("flaky", "inbox", now - timedelta(hours=8, minutes=10)),
                ("no-imid", "inbox", now - timedelta(hours=8, minutes=20), None),
                ("gone", "inbox", now - timedelta(hours=8, minutes=30))]

        folders = await self._resync_with_rows(promoted, monkeypatch, graph, (), rows)

        assert folders == {"client-moved": "inbox", "flaky": "inbox",
                           "no-imid": "inbox", "gone": "trash"}, folders
        assert sorted(graph.lookups) == sorted(
            [moved_imid, "<flaky@seed.test>", "<gone@seed.test>"])

    async def test_a_failed_import_and_a_failed_inbox_keep_last_synced_at_null(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 3, item 1. The first import fails, and inbox page 1 of
        the sweep fails in the same cycle. ``last_synced_at`` stays NULL, so
        the next cycle reads back to ``created_at`` and the mail since the
        connect lands."""
        _assert_non_priv(app_engine)
        p = promoted
        now = _now()
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        _set(p.admin_engine, account_id, import_since=now - timedelta(days=30))
        with p.admin_engine.connect() as c:
            created = c.execute(text(
                "SELECT created_at FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                {"a": account_id}).scalar_one()
        graph = _FakeGraph({"inbox": [now - timedelta(hours=k + 1) for k in range(5)]},
                           fail={("inbox", 1): 500, ("inbox", 2): 500})
        provider = _outlook(graph)
        real_sync = provider.sync_messages
        catch_ups: list = []

        async def _recording(**kw):
            catch_ups.append(kw.get("catch_up"))
            return await real_sync(**kw)

        provider.sync_messages = _recording

        from acb_llm import key_store

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()

        def _state():
            with p.admin_engine.connect() as c:
                return c.execute(text(
                    "SELECT last_synced_at, sync_status, initial_sync_done "
                    "FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                    {"a": account_id}).one()

        try:
            async with tenant_engine_scope(app_dsn):
                first = await sched._sync_account(account_id, organization_id=p.org_b)
                assert "error" in first and first.get("catch_up_incomplete"), first
                assert tuple(_state()) == (None, "error", False), (
                    "a failed import moved last_synced_at")
                second = await sched._sync_account(account_id, organization_id=p.org_b)
            assert "error" not in second, second
            assert catch_ups == [created, created], (
                "the next cycle did not read back to the connect")
            synced, status, done = _state()
            assert synced is not None and status == "idle" and done is True
        finally:
            release_tenant(token)
            _drop(p.admin_engine, account_id)

    @pytest.mark.parametrize(("absent", "skipped"), [(55, False), (61, True)])
    async def test_the_cap_takes_two_percent_of_a_large_folder(
        self, promoted, app_engine, caplog, absent, skipped,  # noqa: F811
    ):
        """Fix round 3, item 4. With 3000 rows in the window, 2% is 60, above
        the floor of 50. So 55 candidates pass, and the folder gives at most
        50 lookups. 61 candidates skip the folder."""
        from email_ingestion import reconcile

        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner="b@em-t6b.test")
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "internet_message_id, folder, from_address, to_addresses, subject, "
                "received_at, updated_at, organization_id) "
                "SELECT CAST(:a AS uuid), 'big-' || g, '<big-' || g || '@seed.test>', "
                "'inbox', '{}'::jsonb, '[]'::jsonb, 'big', "
                "now() - make_interval(mins => g), now() - interval '1 day', "
                "CAST(:o AS uuid) FROM generate_series(1, 3000) g"),
                {"a": account_id, "o": p.org_b})
            rows = c.execute(text(
                "SELECT provider_message_id, received_at FROM email_messages "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).all()
        gone = {f"big-{g}" for g in range(100, 100 + absent)}
        snapshot = [(pid, "inbox", at) for pid, at in rows if pid not in gone]
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            with caplog.at_level("WARNING"):
                async with (tenant_engine_scope(app_dsn),
                            sched.tenant_session(p.org_b) as db):
                    found = await reconcile.import_reconcile_candidates(
                        db, account_id, snapshot, started_at=datetime.now(UTC))
            logged = [r.getMessage() for r in caplog.records
                      if "sync.import_reconcile_skipped" in r.getMessage()]
            if skipped:
                assert found == [] and len(logged) == 1
                assert f"candidates={absent} rows=3000 cap=60" in logged[0]
            else:
                assert logged == [], "the 2% branch did not lift the cap above 50"
                assert len(found) == reconcile.IMPORT_RECONCILE_MAX_LOOKUPS
        finally:
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
