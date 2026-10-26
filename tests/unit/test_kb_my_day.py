"""WS-61 KB-9b slice 1 — the Knowledge cards of My Day, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §9 and §17 (the KB-9b row),
``my_day_feed.md`` §5.8 and MD-3, ``proactive_reach.md`` §4.1 and §4.2.

What this file holds with no database:

* the gates: Knowledge off for the org raises ``SourceAbsent`` before any
  read, a plain member makes no gap read, ``KNOWLEDGE_GAPS`` off makes no gap
  read and the page cards still come (A3);
* ONE session, and 15 signals at most (A4);
* the shape of the SQL: the one visibility clause, the owner ``COALESCE``,
  the author test and the cap of 5 (the R8 twin proves the rows);
* the two cards: key, act, why line, rank, group, class and label;
* the cache bump after the commit of a publish and of an accept (A6);
* the feed with ``knowledge`` unlisted (A7) and the cache key (A8);
* KB-9b slice 2, ``source_sync_failed``: the SQL of the source read, its gate
  on ``KNOWLEDGE_SOURCES``, the card text, the class, and the bump of a failed
  run and of a failed discovery AFTER the session closes.

``tests/unit/test_kb_my_day_r8.py`` proves the reads on a real database.

Mutations this file catches (R7):

* the read opens a second session -> ``TestOneSession``;
* the new-page read loses the clause -> ``TestTheSql::test_each_page_read_puts_in_the_clause``;
* the owner ``COALESCE`` swaps its order -> ``TestTheSql::test_the_owner_is_the_owner_then_the_author``;
* the author test goes -> ``TestTheSql::test_the_new_page_read_skips_the_viewers_own_page``;
* the cap of 5 goes -> ``TestTheSql::test_the_new_page_read_is_capped``;
* the bump moves before the commit -> ``TestTheBump``;
* the hash loses the manage grant -> ``TestTheCacheKey``;
* ``review_at`` leaves ``state.STATE_FACTS`` -> ``TestTheStateOfAPageCard``;
* the review cap goes -> ``TestOneSession::test_twelve_expired_pages_leave_room``;
* the source read loses the org filter -> ``TestTheSourceCard::test_the_source_read_names_the_org_the_owner_and_the_state``;
* the source read loses the owner filter -> the same test, and the R8 twin;
* a stopped run counts as failed -> ``TestTheSourceCard::test_a_stopped_run_never_counts`` and the R8 twin;
* the bump moves inside the session -> ``TestTheSyncBump``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from gateway.routes.knowledge import core, maintenance, my_day, pages, proposals
from gateway.routes.shell import cache, feed, feed_rank, needs, state

ROOT = Path(__file__).resolve().parents[2]
ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def _user(email: str | None = "asha@customer.example", *perms: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:knowledge", *perms]),
                       organization_id=ORG)


@pytest.fixture
def gates(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Knowledge on for ORG, and KNOWLEDGE_GAPS naming ORG."""
    s = get_settings()
    monkeypatch.setattr(s, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(s, "knowledge_orgs", "*", raising=False)
    monkeypatch.setattr(s, "knowledge_gaps", ORG, raising=False)
    return s


def _page(n: int, *, kind: str = "review") -> SimpleNamespace:
    return SimpleNamespace(
        id=f"00000000-0000-4000-8000-{n:012d}", title=f"Page {n}", space_name="Finance",
        review_at=NOW - timedelta(days=n), published_at=NOW - timedelta(hours=n))


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _Db:
    """Records each statement. It answers by the table the statement reads."""

    def __init__(self, review: list[Any], shared: list[Any], gap_rows: list[Any],
                 sources: list[Any] | None = None) -> None:
        self.review, self.shared, self.gap_rows = review, shared, gap_rows
        self.sources = sources or []
        self.ran: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self.ran.append((sql, dict(params or {})))
        if "FROM kb_gaps" in sql:
            return _Result(self.gap_rows)
        if "FROM kb_sources" in sql:
            return _Result(self.sources)
        return _Result(self.review if "p.review_at <= now()" in sql else self.shared)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A fake session seam. ``opened`` counts the sessions."""
    w = SimpleNamespace(opened=0, db=_Db([], [], []))

    @contextlib.asynccontextmanager
    async def session(*_a: Any, **_k: Any):
        w.opened += 1
        yield w.db

    monkeypatch.setattr(core, "_tenant_session", session)
    return w


def _gap_row(n: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=f"g-{n}", sample_query="how do we raise a credit note",
                           ask_count=3, last_asked_at=NOW)


# ── The gates (A3) ──────────────────────────────────────────────────────────

class TestTheGates:
    def test_knowledge_off_raises_absent_before_any_read(self, gates, world) -> None:
        gates.knowledge_enabled = False
        with pytest.raises(needs.SourceAbsent):
            asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        assert world.opened == 0

    def test_the_org_missing_from_the_list_raises_absent(self, gates, world) -> None:
        gates.knowledge_orgs = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0099"
        with pytest.raises(needs.SourceAbsent):
            asyncio.run(feed._knowledge(_user("a@x.test")))
        assert world.opened == 0

    def test_a_plain_member_gets_page_cards_and_no_gap_read(self, gates, world) -> None:
        world.db = _Db([_page(1)], [_page(2)], [_gap_row()])
        found = asyncio.run(feed._knowledge(_user("a@x.test")))
        kinds = [s.kind for s in found.signals()]
        assert kinds == ["doc_needs_review", "doc_shared_with_me"]
        assert not any("kb_gaps" in sql for sql, _ in world.db.ran)

    def test_a_manager_gets_the_gap_card_too(self, gates, world) -> None:
        world.db = _Db([_page(1)], [_page(2)], [_gap_row()])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        assert [s.kind for s in found.signals()] == [
            "doc_needs_review", "kb_gap", "doc_shared_with_me"]

    def test_the_gaps_flag_off_makes_no_gap_read_and_the_page_cards_come(
        self, gates, world,
    ) -> None:
        gates.knowledge_gaps = ""
        world.db = _Db([_page(1)], [_page(2)], [_gap_row()])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        assert [s.kind for s in found.signals()] == [
            "doc_needs_review", "doc_shared_with_me"]
        assert not any("kb_gaps" in sql for sql, _ in world.db.ran)

    def test_a_viewer_with_no_email_gets_no_page_row(self, gates, world) -> None:
        world.db = _Db([_page(1)], [_page(2)], [_gap_row()])
        found = asyncio.run(feed._knowledge(_user("", "knowledge:manage")))
        assert [s.kind for s in found.signals()] == ["kb_gap"]
        assert not any("kb_pages" in sql for sql, _ in world.db.ran)

    def test_a_plain_member_with_no_email_opens_no_session(self, gates, world) -> None:
        assert asyncio.run(feed._knowledge(_user(""))).signals() == []
        assert world.opened == 0

    def test_the_provider_names_no_permission_and_hashes_manage(self) -> None:
        p = needs.REGISTRY["knowledge"]
        assert p.permission is None and p.hash_permission == "knowledge:manage"
        assert p.gated and not p.needs


# ── One session and 15 signals (A4) ─────────────────────────────────────────

class TestOneSession:
    def test_the_three_reads_share_one_session(self, gates, world) -> None:
        world.db = _Db([_page(1)], [_page(2)], [_gap_row()])
        asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        assert world.opened == 1
        assert len(world.db.ran) == 3

    def test_fifteen_signals_at_most_and_capped(self, gates, world) -> None:
        world.db = _Db([_page(n) for n in range(1, 17)], [_page(n) for n in range(20, 25)],
                       [_gap_row(n) for n in range(3)])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        signals = found.signals()
        assert len(signals) == needs.PER_APP == 15
        assert found.capped is True
        # The order before the cut: review first, then gaps, then shared.
        assert {s.kind for s in signals} == {"doc_needs_review"}

    def test_the_order_is_review_gaps_shared(self, gates, world) -> None:
        world.db = _Db([_page(1), _page(2)], [_page(3)], [_gap_row()])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        assert [s.kind for s in found.signals()] == [
            "doc_needs_review", "doc_needs_review", "kb_gap", "doc_shared_with_me"]
        assert found.capped is False


# ── The SQL (the R8 twin proves its rows) ───────────────────────────────────

class TestTheSql:
    def _ran(self, gates, world, *perms: str) -> list[tuple[str, dict[str, Any]]]:
        asyncio.run(my_day.read(_user("A@x.test", *perms), gaps_on=True, limit=16))
        return world.db.ran

    def test_each_page_read_puts_in_the_clause(self, gates, world) -> None:
        vis, _ = core.visible_pages_clause(_user("a@x.test"), "p")
        reads = [(sql, p) for sql, p in self._ran(gates, world) if "kb_pages" in sql]
        assert len(reads) == 2
        for sql, params in reads:
            assert vis in sql
            assert "s.kind <> 'me'" in sql and "s.archived_at IS NULL" in sql
            assert params["kb_viewer"] == "a@x.test"
            assert params["kb_org"] == ORG

    def test_the_owner_is_the_owner_then_the_author(self, gates, world) -> None:
        [review] = [sql for sql, _ in self._ran(gates, world) if "p.review_at <= now()" in sql]
        assert "COALESCE(p.owner_email, p.author_email)" in review
        assert maintenance.EXPIRED in review
        assert "p.state = 'published'" in review

    def test_the_expired_test_is_the_maintenance_list_s(self) -> None:
        assert my_day.EXPIRED is maintenance.EXPIRED
        assert not hasattr(maintenance, "_EXPIRED")

    def test_the_new_page_read_skips_the_viewers_own_page(self, gates, world) -> None:
        [shared] = [sql for sql, _ in self._ran(gates, world)
                    if "published_at >=" in sql]
        assert "lower(p.author_email) IS DISTINCT FROM :kb_viewer" in shared
        assert "interval '24 hours'" in shared
        assert "ORDER BY p.published_at DESC" in shared

    def test_the_new_page_read_is_capped(self, gates, world) -> None:
        reads = [(sql, p) for sql, p in self._ran(gates, world) if "published_at >=" in sql]
        [(sql, params)] = reads
        assert "LIMIT :kb_limit" in sql
        assert params["kb_limit"] == my_day.SHARED_MAX == 5

    def test_no_tenant_comes_from_input(self) -> None:
        text = (ROOT / "apps/services/gateway/gateway/routes/knowledge/my_day.py").read_text(
            encoding="utf-8")
        assert "Request" not in text and "Query(" not in text
        assert "user.organization_id" not in text  # the clause reads it from the user


# ── The two cards ───────────────────────────────────────────────────────────

class TestTheCards:
    def _signal(self, kind: str) -> Any:
        row = {"id": "p-1", "title": "Refund policy", "space": "Finance",
               "review_at": NOW - timedelta(days=3), "published_at": NOW}
        return feed._page_signal(row, kind)

    def test_both_kinds_key_on_the_page(self) -> None:
        from gateway.routes.shell.state import SIGNAL_ID_PATTERN

        for kind in ("doc_needs_review", "doc_shared_with_me"):
            sig = self._signal(kind)
            assert sig.signal_id == "knowledge:page:p-1"
            assert re.fullmatch(SIGNAL_ID_PATTERN, sig.signal_id)
            assert sig.app == "knowledge" and sig.kind == kind
            assert sig.entity == {"type": "page", "id": "p-1", "label": "Refund policy"}
            [act] = sig.acts
            assert act["href"] == "/knowledge/pages/p-1" and act["way"] == "open"

    def test_the_acts_are_review_and_read(self) -> None:
        assert [(a["label"]) for a in self._signal("doc_needs_review").acts] == ["Review"]
        assert [(a["label"]) for a in self._signal("doc_shared_with_me").acts] == ["Read"]

    def test_the_shared_card_expires_a_day_after_the_publish(self) -> None:
        sig = self._signal("doc_shared_with_me")
        assert sig.expires_at == (NOW + timedelta(hours=24)).isoformat()
        assert self._signal("doc_needs_review").expires_at is None

    def test_the_why_lines_name_the_date_or_the_space_only(self) -> None:
        review = self._signal("doc_needs_review")
        key, line = feed._md3_why("doc_needs_review", review.facts, UTC, NOW.date())
        assert key == "review_due"
        assert line == "The review date was on Thursday. Check that the page is still right."
        shared = self._signal("doc_shared_with_me")
        key, line = feed._md3_why("doc_shared_with_me", shared.facts, UTC, NOW.date())
        assert key == "shared"
        assert line == "A new page went live in Finance in the last day."
        for sig in (review, shared):
            assert set(sig.facts) <= {"space", "review_at", "published_at"}

    def test_one_page_in_both_kinds_folds_to_the_review(self) -> None:
        cards = feed.build_cards(
            [self._signal("doc_shared_with_me"), self._signal("doc_needs_review")],
            NOW, UTC, NOW.date())
        [card] = cards
        assert card["kind"] == "doc_needs_review" and card["folded"] == ["doc_shared_with_me"]

    def test_rank_group_class_and_label(self) -> None:
        assert feed_rank.BASE["doc_needs_review"] == 21
        assert feed_rank.BASE["doc_shared_with_me"] == 19
        assert feed_rank.BASE["doc_needs_review"] > feed_rank.BASE["kb_gap"] \
            > feed_rank.BASE["doc_shared_with_me"]
        for kind in ("doc_needs_review", "doc_shared_with_me"):
            assert feed_rank.group(kind, {}, NOW, NOW.date()) == "fyi"
        text = (ROOT / "workbench/control_plane/src/lib/shell/feed.ts").read_text(
            encoding="utf-8")
        assert 'doc_needs_review: "Page to review"' in text
        assert 'doc_shared_with_me: "New page"' in text

    def test_the_limits_hold_every_kind(self) -> None:
        from gateway.routes.shell import prefs

        assert prefs.MAX_KINDS == 26 == len(prefs.feed_kinds())


# ── The bump after the commit (A6) ──────────────────────────────────────────

class TestTheBump:
    @pytest.fixture
    def order(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        log: list[str] = []

        @contextlib.asynccontextmanager
        async def session(*_a: Any, **_k: Any):
            log.append("open")
            try:
                yield object()
            finally:
                log.append("close")

        async def bump(org: Any) -> None:
            log.append(f"bump:{org}")

        page = SimpleNamespace(id="p-1", state="draft")

        async def load_page(*_a: Any, **_k: Any) -> Any:
            return page

        async def space_of(*_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(id="s-1")

        async def level(*_a: Any, **_k: Any) -> str:
            return "write"

        async def update(*_a: Any, **_k: Any) -> Any:
            log.append("update")
            return SimpleNamespace(id="p-1", title="T", body_md="b")

        async def written(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {"id": "p-1"}

        async def gate_page(*_a: Any, **_k: Any) -> Any:
            return page

        async def no_edit(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(core, "_tenant_session", session)
        monkeypatch.setattr(cache, "bump_org_safe", bump)
        monkeypatch.setattr(core, "load_page", load_page)
        monkeypatch.setattr(core, "space_level", level)
        monkeypatch.setattr(pages, "_space_of", space_of)
        monkeypatch.setattr(pages, "_update", update)
        monkeypatch.setattr(pages, "_written", written)
        monkeypatch.setattr(proposals, "_gate_page", gate_page)
        monkeypatch.setattr(proposals, "_edit_proposal", no_edit)
        monkeypatch.setattr(proposals, "_require_web", lambda _u: None)
        monkeypatch.setattr(proposals, "page_proposal_dict", lambda _r: {})
        return log

    def test_publish_bumps_after_the_session_closes(self, order: list[str]) -> None:
        from fastapi import BackgroundTasks

        asyncio.run(pages.publish_page(
            "00000000-0000-4000-8000-000000000001", BackgroundTasks(), _user("a@x.test")))
        assert order == ["open", "update", "close", f"bump:{ORG}"]

    def test_accept_bumps_after_the_session_closes(self, order: list[str]) -> None:
        from fastapi import BackgroundTasks

        asyncio.run(proposals.accept_proposal(
            "00000000-0000-4000-8000-000000000001", BackgroundTasks(), _user("a@x.test")))
        assert order == ["open", "update", "close", f"bump:{ORG}"]


# ── KB-9b slice 2: the card of a source that stopped syncing ────────────────

def _src(n: int = 1, state: str = "ready") -> SimpleNamespace:
    return SimpleNamespace(id=f"00000000-0000-4000-8000-{n:012d}", provider="google_drive",
                           state=state)


@pytest.fixture
def sources_on(gates: Any) -> Any:
    """KNOWLEDGE_SOURCES naming ORG."""
    gates.knowledge_sources = ORG
    return gates


class TestTheSourceCard:
    def test_sources_off_runs_no_source_statement_and_the_page_cards_come(
        self, gates, world,
    ) -> None:
        gates.knowledge_sources = ""
        world.db = _Db([_page(1)], [_page(2)], [], sources=[_src()])
        found = asyncio.run(feed._knowledge(_user("a@x.test")))
        assert [s.kind for s in found.signals()] == ["doc_needs_review", "doc_shared_with_me"]
        assert not any("kb_sources" in sql for sql, _ in world.db.ran)

    def test_sources_on_reads_in_the_same_one_session(self, sources_on, world) -> None:
        world.db = _Db([_page(1)], [_page(2)], [], sources=[_src()])
        found = asyncio.run(feed._knowledge(_user("a@x.test")))
        assert world.opened == 1
        assert len(world.db.ran) == 3
        assert [s.kind for s in found.signals()] == [
            "source_sync_failed", "doc_needs_review", "doc_shared_with_me"]

    def test_the_failures_come_first_then_review_gaps_shared(self, sources_on, world) -> None:
        world.db = _Db([_page(1)], [_page(2)], [_gap_row()], sources=[_src(1), _src(2)])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        assert [s.kind for s in found.signals()] == [
            "source_sync_failed", "source_sync_failed", "doc_needs_review", "kb_gap",
            "doc_shared_with_me"]

    def test_the_failures_survive_the_cut_of_fifteen(self, sources_on, world) -> None:
        world.db = _Db([_page(n) for n in range(1, 11)], [_page(n) for n in range(20, 25)],
                       [_gap_row(n) for n in range(3)], sources=[_src(1)])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        signals = found.signals()
        assert len(signals) == 15 and signals[0].kind == "source_sync_failed"

    def test_a_viewer_with_no_email_reads_no_source(self, sources_on, world) -> None:
        world.db = _Db([], [], [_gap_row()], sources=[_src()])
        found = asyncio.run(feed._knowledge(_user("", "knowledge:manage")))
        assert [s.kind for s in found.signals()] == ["kb_gap"]
        assert not any("kb_sources" in sql for sql, _ in world.db.ran)

    def test_the_source_read_names_the_org_the_owner_and_the_state(
        self, sources_on, world,
    ) -> None:
        asyncio.run(my_day.read(_user("A@x.test"), gaps_on=False, limit=16, sources_on=True))
        [(sql, params)] = [(q, p) for q, p in world.db.ran if "FROM kb_sources" in q]
        assert "s.organization_id = CAST(:kb_org AS uuid)" in sql
        assert "lower(s.owner_email) = :kb_viewer" in sql
        assert "s.state <> 'disconnected'" in sql
        assert params["kb_org"] == ORG and params["kb_viewer"] == "a@x.test"
        assert params["kb_limit"] == my_day.SOURCES_MAX == 10
        assert params["kb_runs"] == my_day.FAILED_RUNS == 3
        # The run read is for the same org and the same source, and it is the
        # newest three through the index (source_id, started_at DESC).
        assert "r.organization_id = s.organization_id" in sql
        assert "ORDER BY r.started_at DESC LIMIT :kb_runs" in sql

    def test_a_stopped_run_never_counts(self) -> None:
        sql = my_day._SOURCES_SQL
        assert "r.status <> 'running'" in sql  # a stopped run is finished
        assert "bool_and(x.status = 'failed'" in sql  # and it is not failed
        assert "r.kind IN ('changes', 'reconcile')" in sql
        assert "NOT IN ('stale', 'superseded')" in sql
        assert "= :kb_runs AND" in sql  # fewer than three finished runs never fail

    def test_the_page_reads_stay_two(self, sources_on, world) -> None:
        asyncio.run(my_day.read(_user("a@x.test"), gaps_on=False, limit=16, sources_on=True))
        assert len([1 for q, _ in world.db.ran if "FROM kb_pages" in q]) == 2


class TestTheSourceSignal:
    def _signal(self, state: str) -> Any:
        return feed._source_signal(
            {"id": "src-1", "provider": "google_drive", "state": state})

    def _line(self, state: str) -> str:
        sig = self._signal(state)
        key, line = feed._md3_why("source_sync_failed", sig.facts, UTC, NOW.date())
        assert key == "source_failed"
        return line

    def test_the_key_and_the_one_act(self) -> None:
        from gateway.routes.shell.state import SIGNAL_ID_PATTERN

        for st in ("needs_reconnect", "ready"):
            sig = self._signal(st)
            assert sig.signal_id == "knowledge:source:src-1"
            assert re.fullmatch(SIGNAL_ID_PATTERN, sig.signal_id)
            assert sig.app == "knowledge" and sig.kind == "source_sync_failed"
            [act] = sig.acts
            assert act["href"] == "/knowledge/sources" and act["way"] == "open"

    def test_the_why_lines(self) -> None:
        assert self._line("needs_reconnect") == (
            "Your Google Drive stopped syncing. Reconnect it in Knowledge.")
        assert self._line("ready") == "The last three syncs failed."

    def test_the_card_holds_no_code_no_address_and_no_token(self) -> None:
        # A6: the signal copies the state and the provider name from the row,
        # and nothing else, so an extra column cannot reach the card.
        row = {"id": "src-1", "provider": "google_drive", "state": "needs_reconnect",
               "last_error": "invalid_grant", "account_email": "ana@gmail.example",
               "token": "ya29.secret"}
        sig = feed._source_signal(row)
        [card] = feed.build_cards([sig], NOW, UTC, NOW.date())
        text = repr(card) + repr(sig)
        for leak in ("invalid_grant", "ana@gmail.example", "ya29.secret", "@"):
            assert leak not in text, leak
        assert set(sig.facts) == {"reason", "provider"}

    def test_the_select_names_no_error_and_no_address(self) -> None:
        sql = my_day._SOURCES_SQL
        assert "last_error" not in sql and "account_email" not in sql
        assert "account_id" not in sql
        assert sql.split(" FROM ")[0] == "SELECT s.id, s.provider, s.state"

    def test_rank_group_class_and_label(self) -> None:
        from gateway.reach import classes

        assert feed_rank.BASE["source_sync_failed"] == 31
        assert feed_rank.BASE["source_sync_failed"] > feed_rank.BASE["doc_needs_review"]
        assert feed_rank.group("source_sync_failed", {}, NOW, NOW.date()) == "needs_you"
        assert classes.REACH_CLASS["source_sync_failed"] == "interrupt"
        text = (ROOT / "workbench/control_plane/src/lib/shell/feed.ts").read_text(
            encoding="utf-8")
        assert 'source_sync_failed: "Source stopped syncing"' in text

    def test_the_provider_holds_the_new_kind(self) -> None:
        assert "source_sync_failed" in needs.REGISTRY["knowledge"].kinds


# ── KB-9b slice 2, A5: the bump runs AFTER the session closes ───────────────

class TestTheSyncBump:
    """The order of a failed run: the session closes, and then the bump."""

    @pytest.fixture
    def seen(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from tests.unit import test_kb_sync_engine as eng

        w = eng._bind(monkeypatch, eng.World())
        bumps: list[tuple[str, str, int]] = []

        async def bump(org: Any, email: Any) -> None:
            bumps.append((str(org), str(email), w.open))
            w.events.append(("bump", email))

        monkeypatch.setattr(cache, "bump_member_safe", bump)
        return SimpleNamespace(w=w, bumps=bumps, eng=eng)

    def _fail(self, seen: Any, exc: BaseException) -> Any:
        adapter = seen.eng.FakeAdapter(seen.w, {"t0": exc})
        return seen.eng._run("changes", adapter)

    def _after_close(self, seen: Any) -> None:
        [(org, email, open_sessions)] = seen.bumps
        assert (org, email) == (seen.eng.ORG, "ana@kc3.test")
        assert open_sessions == 0, "the bump ran inside a session"
        events = seen.w.events
        at = events.index(("bump", "ana@kc3.test"))
        assert events[at - 1] == ("commit", None), "a session was open at the bump"

    def _discovery(self, seen: Any, monkeypatch: pytest.MonkeyPatch, exc: BaseException,
                   *, row: bool = True) -> str:
        eng = seen.eng
        if row:
            monkeypatch.setattr(eng._Db, "_disc_fail", lambda self, p: eng._Result(
                [SimpleNamespace(owner_email="ana@kc3.test")]), raising=False)
        adapter = eng.FakeAdapter(seen.w, {})

        async def fail(cred: Any) -> str:
            raise exc

        adapter.changes_start_token = fail  # type: ignore[method-assign]
        return asyncio.run(eng.discovery.discover_source(eng.ORG, eng.SOURCE, adapter))

    def test_a_failed_run_bumps_the_connector_after_the_commit(self, seen) -> None:
        from gateway.kb_sources.base import SourceError

        exc = SourceError("boom")
        exc.code = "http_500"
        assert self._fail(seen, exc).status == "failed"
        self._after_close(seen)

    def test_an_unexpected_error_bumps_after_the_commit(self, seen) -> None:
        assert self._fail(seen, RuntimeError("x")).status == "failed"
        self._after_close(seen)

    def test_needs_reconnect_bumps_after_the_commit(self, seen) -> None:
        from gateway.kb_sources.base import NeedsReconnect

        exc = NeedsReconnect("dead")
        exc.code = "invalid_grant"
        assert self._fail(seen, exc).status == "stopped"
        self._after_close(seen)

    def test_a_good_run_and_a_rate_limit_bump_nothing(self, seen) -> None:
        from gateway.kb_sources.base import RateLimited

        eng = seen.eng
        adapter = eng.FakeAdapter(seen.w, {"t0": eng._page(nxt="t1"),
                                           "t1": RateLimited("slow", tries=4)})
        eng._run("changes", adapter)
        adapter = eng.FakeAdapter(seen.w, {"t0": eng._page(start="t9")})
        assert eng._run("changes", adapter).status == "ok"
        assert seen.bumps == []

    def test_a_bump_that_raises_never_fails_the_run(self, seen, monkeypatch) -> None:
        async def boom(org: Any, email: Any) -> None:
            raise RuntimeError("redis down")

        monkeypatch.setattr(cache, "bump_member_safe", boom)
        out = self._fail(seen, RuntimeError("x"))
        assert (out.status, out.error) == ("failed", "RuntimeError")

    def test_a_discovery_that_failed_bumps_after_its_session(self, seen, monkeypatch) -> None:
        from gateway.kb_sources.base import SourceError

        assert self._discovery(seen, monkeypatch, SourceError("no token")) == "failed"
        self._after_close(seen)

    def test_a_discovery_that_needs_a_reconnect_bumps_too(self, seen, monkeypatch) -> None:
        from gateway.kb_sources.base import NeedsReconnect

        assert self._discovery(
            seen, monkeypatch, NeedsReconnect("dead")) == "needs_reconnect"
        self._after_close(seen)

    def test_a_disconnected_source_in_discovery_bumps_nothing(self, seen, monkeypatch) -> None:
        from gateway.kb_sources.base import SourceError

        # The stock fake answers the mark with no row: the source is gone.
        self._discovery(seen, monkeypatch, SourceError("x"), row=False)
        assert seen.bumps == []

    def test_a_bump_that_raises_never_fails_a_discovery(self, seen, monkeypatch) -> None:
        from gateway.kb_sources.base import SourceError

        async def boom(org: Any, email: Any) -> None:
            raise RuntimeError("redis down")

        monkeypatch.setattr(cache, "bump_member_safe", boom)
        assert self._discovery(seen, monkeypatch, SourceError("x")) == "failed"

    def test_the_engine_and_the_discovery_bump_through_one_helper(self) -> None:
        for name in ("engine.py", "discovery.py"):
            text = (ROOT / "apps/services/gateway/gateway/kb_sync" / name).read_text(
                encoding="utf-8")
            assert "owner_bump.bump_owner(" in text
            assert "bump_member_safe" not in text


# ── The cache key (A8) and the dark list (A7) ───────────────────────────────

class TestTheCacheKey:
    def test_the_hash_differs_with_and_without_manage(self) -> None:
        assert cache.features_hash(_user("a@x.test", "knowledge:manage")) \
            != cache.features_hash(_user("a@x.test"))

    def test_the_hash_is_stable_for_one_member(self) -> None:
        assert cache.features_hash(_user("a@x.test", "knowledge:manage")) \
            == cache.features_hash(_user("b@x.test", "knowledge:manage"))

    def test_there_is_one_hash_function(self) -> None:
        text = (ROOT / "apps/services/gateway/gateway/routes/shell/cache.py").read_text(
            encoding="utf-8")
        assert len(re.findall(r"def \w*hash\w*\(", text)) == 1


class TestTheDarkList:
    def test_with_knowledge_unlisted_it_does_not_run(self, monkeypatch) -> None:
        from tests.unit.test_shell_feed import FULL, _list, fakes, member, run_feed  # noqa: F401

        _list(monkeypatch, "")
        ran: list[str] = []

        async def read(*_a: Any, **_k: Any) -> Any:
            ran.append("read")
            return my_day.Rows()

        monkeypatch.setattr(my_day, "read", read)
        answer = run_feed(member(*FULL, "knowledge"))
        assert "knowledge" not in answer["sources"]
        assert ran == []
        assert not any(c["app"] == "knowledge" for c in answer["cards"])


# ── The state of a page card (KB-9b fix round 1, F1) ────────────────────────

class TestTheStateOfAPageCard:
    CTX = state.Context(now=NOW, day=state.Day(tz=UTC))

    def _card(self, kind: str, review_at: datetime) -> dict[str, Any]:
        row = {"id": "p-1", "title": "Refund policy", "space": "Finance",
               "review_at": review_at, "published_at": NOW - timedelta(hours=1)}
        [card] = feed.build_cards([feed._page_signal(row, kind)], NOW, UTC, NOW.date())
        return {**card, "state_fp": state.state_fp(card["signal_id"], card["facts"])}

    def test_a_dismissed_review_card_returns_with_a_new_review_date(self) -> None:
        first = self._card("doc_needs_review", NOW - timedelta(days=2))
        dismissed = {"state": "dismissed", "kind": "doc_needs_review",
                     "fingerprint": first["state_fp"]}
        # The same date stays hidden.
        assert state.decide(first, dismissed, self.CTX).visible is False
        # The next review cycle, 180 days later, is a new card.
        later = self._card("doc_needs_review", NOW + timedelta(days=178))
        assert later["state_fp"] != first["state_fp"]
        assert state.decide(later, dismissed, self.CTX).visible is True

    def test_a_dismissed_new_page_card_does_not_hide_the_review_card(self) -> None:
        shared = self._card("doc_shared_with_me", NOW - timedelta(days=2))
        dismissed = {"state": "dismissed", "kind": "doc_shared_with_me",
                     "fingerprint": shared["state_fp"]}
        review = self._card("doc_needs_review", NOW - timedelta(days=2))
        assert review["state_fp"] != shared["state_fp"]
        assert state.decide(review, dismissed, self.CTX).visible is True

    def test_only_the_page_kinds_carry_review_at(self) -> None:
        assert "review_at" in state.STATE_FACTS
        assert set(state.STATE_FACTS) == {
            "due_at", "note_ids", "waiting_since", "message_id", "review_at"}


# ── The review cap (F2), the cache key (F3) and the reads (F4) ──────────────

class TestTheCapAndTheKey:
    def test_twelve_expired_pages_leave_room(self, gates, world) -> None:
        world.db = _Db([_page(n) for n in range(1, 13)], [_page(30)], [_gap_row()])
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        kinds = [s.kind for s in found.signals()]
        assert world.db.ran[0][1]["kb_limit"] == my_day.REVIEW_MAX == 10
        assert "kb_gap" in kinds and "doc_shared_with_me" in kinds

    def test_the_review_read_asks_for_ten_at_most(self, gates, world) -> None:
        asyncio.run(my_day.read(_user("a@x.test"), gaps_on=False, limit=16))
        [(sql, params)] = [(q, p) for q, p in world.db.ran if "review_at <= now()" in q]
        assert params["kb_limit"] == 10 and "LIMIT :kb_limit" in sql

    def test_the_hash_follows_the_org_switch_while_knowledge_is_listed(
        self, gates, monkeypatch,
    ) -> None:
        monkeypatch.setattr(get_settings(), "my_day_providers", "knowledge")
        member = _user("a@x.test")
        on = cache.features_hash(member)
        gates.knowledge_enabled = False
        assert cache.features_hash(member) != on

    def test_the_hash_ignores_the_switch_while_knowledge_is_unlisted(
        self, gates, monkeypatch,
    ) -> None:
        monkeypatch.setattr(get_settings(), "my_day_providers", "")
        member = _user("a@x.test")
        on = cache.features_hash(member)
        gates.knowledge_enabled = False
        assert cache.features_hash(member) == on

    def test_both_reads_name_the_live_published_page_rules(self, gates, world) -> None:
        asyncio.run(my_day.read(_user("a@x.test"), gaps_on=False, limit=16))
        reads = [sql for sql, _ in world.db.ran if "kb_pages" in sql]
        assert len(reads) == 2
        for sql in reads:
            assert "p.state = 'published'" in sql
            assert "s.kind <> 'me'" in sql
            assert "s.archived_at IS NULL" in sql
