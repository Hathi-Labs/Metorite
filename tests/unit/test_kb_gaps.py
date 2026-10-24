"""WS-61 KB-9a — Knowledge gaps, hermetic.

Spec: ``project-docs/specs/knowledge_base.md`` §8.4 door 3, §9 and §13.

What this file holds with no database:

* the normal form of a question, and the skip rules (short, a URL, an email
  address, a uuid, a question of numbers);
* the gate: ``KNOWLEDGE_GAPS`` off writes nothing, and a service call writes
  nothing;
* the trigger: ``GET /search/company`` schedules ONE write after its answer
  when Knowledge answered zero hits, and none when Knowledge hit, failed or
  was not asked. The answer itself does not change;
* the My Day source ``knowledge``: gated, feed-only, ``knowledge:manage``,
  ``absent`` with the flag off, and a ``kb_gap`` card with a code template;
* the route classes in the chat manifest, and the Knowledge half of the
  member purge.

``tests/unit/test_kb_gaps_r8.py`` proves the SQL on a real database.

Mutations this file catches (R7):

* the route schedules a write when Knowledge hit ->
  ``TestTheTrigger::test_a_knowledge_hit_writes_nothing``;
* the flag check leaves ``gap_for`` ->
  ``TestTheGate::test_the_flag_off_writes_nothing`` and
  ``TestTheTrigger::test_the_flag_off_schedules_nothing``;
* dismiss loses its manager check ->
  ``TestTheRoutes::test_dismiss_needs_manage_before_any_read``.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from gateway.retrieval import company
from gateway.routes.knowledge import flags as kb_flags
from gateway.routes.knowledge import gaps

ROOT = Path(__file__).resolve().parents[2]
ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"
OTHER = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002"
QUESTION = "How do we raise a credit note?"


def _user(email: str | None = "Asha@Customer.example", *perms: str,
          org: str | None = ORG, role: UserRole = UserRole.EMPLOYEE) -> UserContext:
    return UserContext(email=email, role=role,
                       access=build_access(["feature:knowledge", *perms]),
                       organization_id=org)


@pytest.fixture
def gates(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Knowledge on for ORG, and KNOWLEDGE_GAPS naming ORG."""
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "knowledge_gaps", ORG, raising=False)
    monkeypatch.setattr(settings, "knowledge_embed", "", raising=False)
    return settings


# ── The normal form ─────────────────────────────────────────────────────────

class TestTheNormalForm:
    @pytest.mark.parametrize(("raw", "norm"), [
        ("How do we raise a credit note?", "how do we raise a credit note"),
        ("  how   DO we\traise a credit-note  ", "how do we raise a credit note"),
        ("\uff26\uff55\uff4c\uff4c width words here", "full width words here"),
        ("What's the refund window?!", "what s the refund window"),
        ("snake_case words stay apart", "snake case words stay apart"),
        ("", ""),
        (None, ""),
    ])
    def test_one_rule(self, raw: str | None, norm: str) -> None:
        assert gaps.normalize(raw) == norm

    def test_a_vowel_sign_is_kept(self) -> None:
        raw = "मेरा वेतन कब मिलेगा"
        import unicodedata
        want = unicodedata.normalize("NFKC", raw).lower()
        assert gaps.normalize(raw) == want
        assert len(gaps.normalize(raw).split()) == len(raw.split()) == 4

    def test_two_words_that_differ_by_a_vowel_sign_are_two_gaps(self) -> None:
        a = gaps.normalize("वेतन कब मिलेगा")
        b = gaps.normalize("वेतन कब मुलेगा")
        assert a != b
        tamil_a = gaps.normalize("சம்பளம் எப்போது கிடைக்கும்")
        tamil_b = gaps.normalize("சம்பளம் எப்போது கிடைக்கீம்")
        assert tamil_a != tamil_b

    def test_two_wordings_are_one_gap(self) -> None:
        assert gaps.normalize("Refund window?") == gaps.normalize("refund   WINDOW")

    def test_a_long_question_is_cut_at_a_word(self) -> None:
        norm = gaps.normalize("word " * 200)
        assert len(norm) <= gaps.MAX_QUERY
        assert norm.endswith("word")

    def test_the_sample_keeps_the_first_wording_on_one_line(self) -> None:
        assert gaps.sample_of("  How do we\n raise it?  ") == "How do we raise it?"
        assert len(gaps.sample_of("x" * 900)) == gaps.MAX_QUERY


# ── The stored text ─────────────────────────────────────────────────────────

class TestTheStoredText:
    def test_a_phone_number_loses_its_digits(self) -> None:
        masked = gaps.mask_text("call 98765 43210 now")
        assert not any(c.isdigit() for c in masked)
        assert masked == "call [phone] now"

    @pytest.mark.parametrize("raw", [
        "salary Rs 85,000 per month", "salary $1,200 per month", "salary ₹50000 per month",
    ])
    def test_an_amount_becomes_amount(self, raw: str) -> None:
        masked = gaps.mask_text(raw)
        assert "[amount]" in masked
        assert not any(c.isdigit() for c in masked)

    def test_a_long_run_of_digits_becomes_number(self) -> None:
        assert gaps.mask_text("account 123456789012 balance") == "account [number] balance"

    def test_an_email_becomes_email(self) -> None:
        assert gaps.mask_text("who is a@b.example here") == "who is [email] here"

    def test_plain_text_is_unchanged(self) -> None:
        assert gaps.mask_text(QUESTION) == QUESTION
        assert gaps.mask_text("warranty for a 3D printer in 2024") == (
            "warranty for a 3D printer in 2024")

    def test_gap_for_returns_masked_text_in_both_items(self, gates: Any) -> None:
        found = gaps.gap_for(_user(), "what is the salary Rs 85,000 per month for staff")
        assert found is not None
        norm, sample = found
        assert not any(c.isdigit() for c in norm + sample)
        assert norm == "what is the salary amount per month for staff"
        assert sample == "what is the salary [amount] per month for staff"

    def test_two_figures_are_one_gap(self, gates: Any) -> None:
        a = gaps.gap_for(_user(), "what is the salary Rs 50000 for staff")
        b = gaps.gap_for(_user(), "what is the salary Rs 60000 for staff")
        assert a is not None and a == b

    def test_the_skip_rule_still_reads_the_raw_question(self, gates: Any) -> None:
        assert gaps.gap_for(_user(), "INV 2024 0031 77") is None


# ── The skip rules ──────────────────────────────────────────────────────────

class TestTheSkipRules:
    @pytest.mark.parametrize(("raw", "reason"), [
        ("refund window", "short"),
        ("refund", "short"),
        ("?? !!", "short"),
        ("open https://example.test/page please now", "url"),
        ("what is on www.example.test today", "url"),
        ("who is ravi@customer.example in sales", "email"),
        ("task 0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001 status", "id"),
        ("INV 2024 0031 77", "number"),
        ("PO-4411 PO-4412 PO-4413", "number"),
    ])
    def test_each_rule(self, raw: str, reason: str) -> None:
        assert gaps.skip_reason(raw) == reason

    @pytest.mark.parametrize("raw", [
        QUESTION,
        "what is our refund window",
        "how many days for a 3D printer warranty",
    ])
    def test_a_question_passes(self, raw: str) -> None:
        assert gaps.skip_reason(raw) is None


# ── The gate ────────────────────────────────────────────────────────────────

class TestTheGate:
    def test_the_setting_is_off_by_default(self) -> None:
        from acb_common.settings import Settings

        assert Settings.model_fields["knowledge_gaps"].default == ""

    def test_the_flag_off_writes_nothing(self, gates: Any, monkeypatch) -> None:
        monkeypatch.setattr(gates, "knowledge_gaps", "", raising=False)
        assert gaps.gap_for(_user(), QUESTION) is None
        monkeypatch.setattr(gates, "knowledge_gaps", OTHER, raising=False)
        assert gaps.gap_for(_user(), QUESTION) is None

    def test_the_flag_needs_knowledge_on(self, gates: Any, monkeypatch) -> None:
        monkeypatch.setattr(gates, "knowledge_gaps", "*", raising=False)
        monkeypatch.setattr(gates, "knowledge_enabled", False, raising=False)
        assert not kb_flags.knowledge_gaps_allowed(ORG)
        monkeypatch.setattr(gates, "knowledge_enabled", True, raising=False)
        assert kb_flags.knowledge_gaps_allowed(ORG)
        assert not kb_flags.knowledge_gaps_allowed(None)

    def test_a_member_writes_the_normal_form_and_the_wording(self, gates: Any) -> None:
        assert gaps.gap_for(_user(), QUESTION) == (
            "how do we raise a credit note", "How do we raise a credit note?")

    @pytest.mark.parametrize("user", [
        _user("system:internal"),
        _user(None),
        _user("not-an-address"),
        _user(org=None),
        _user(role=UserRole.AGENT),
    ])
    def test_a_service_or_a_principal_with_no_member_writes_nothing(
        self, gates: Any, user: UserContext,
    ) -> None:
        assert gaps.gap_for(user, QUESTION) is None

    def test_a_skipped_question_writes_nothing(self, gates: Any) -> None:
        assert gaps.gap_for(_user(), "refund window") is None


# ── The trigger: GET /search/company ────────────────────────────────────────

class _Writes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def record(self, org: str, member: str, norm: str, sample: str) -> int:
        self.calls.append((org, member, norm, sample))
        return 1


@pytest.fixture
def search(gates: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The company route over a stub Knowledge search, and a stub gap write."""
    import gateway.routes.knowledge.search as kb_search

    state: dict[str, Any] = {"hits": [], "fail": None}

    async def knowledge(**kw: Any) -> dict[str, Any]:
        if state["fail"] is not None:
            raise state["fail"]
        return {"query": kw["q"], "degraded": True, "hits": state["hits"]}

    writes = _Writes()
    monkeypatch.setattr(kb_search, "search_knowledge", knowledge)
    monkeypatch.setattr(gaps, "record_gap", writes.record)
    state["writes"] = writes
    return state


def _get(user: UserContext, q: str = QUESTION) -> dict[str, Any]:
    app = FastAPI()
    app.include_router(company.router)
    app.dependency_overrides[get_current_user] = lambda: user
    res = TestClient(app).get("/search/company", params={"q": q})
    assert res.status_code == 200, res.text
    return res.json()


_HIT = {"page_id": "k-1", "space_id": "s-1", "space_name": "Company",
        "title": "Credit notes", "heading_path": None, "text": "How to raise one.",
        "score": 0.1, "updated_at": None, "href": "/knowledge/pages/k-1"}


class TestTheTrigger:
    def test_a_zero_hit_search_schedules_one_write(self, search: Any) -> None:
        answer = _get(_user())
        assert answer == {"query": QUESTION, "hits": [], "skipped": []}
        assert search["writes"].calls == [
            (ORG, "asha@customer.example", "how do we raise a credit note", QUESTION)]

    def test_a_knowledge_hit_writes_nothing(self, search: Any) -> None:
        search["hits"] = [_HIT]
        answer = _get(_user())
        assert [h["id"] for h in answer["hits"]] == ["k-1"]
        assert search["writes"].calls == []

    def test_other_apps_hitting_do_not_matter(self, search: Any, monkeypatch) -> None:
        """A gap is about Knowledge: a task hit beside a Knowledge miss still counts."""
        import gateway.routes.projects.search as projects_search

        async def tasks(**kw: Any) -> dict[str, Any]:
            return {"rows": [{"id": "t-1", "title": "Credit note task", "snippet": ""}]}

        monkeypatch.setattr(projects_search, "search_tasks", tasks)
        answer = _get(_user("asha@customer.example", "feature:projects"))
        assert [h["app"] for h in answer["hits"]] == ["tasks"]
        assert len(search["writes"].calls) == 1

    def test_a_failed_knowledge_source_writes_nothing(self, search: Any) -> None:
        search["fail"] = RuntimeError("db down")
        answer = _get(_user())
        assert answer["skipped"] == ["knowledge"]
        assert search["writes"].calls == []

    def test_the_flag_off_schedules_nothing(self, search: Any, gates: Any,
                                            monkeypatch) -> None:
        monkeypatch.setattr(gates, "knowledge_gaps", "", raising=False)
        _get(_user())
        assert search["writes"].calls == []

    def test_a_short_question_schedules_nothing(self, search: Any) -> None:
        _get(_user(), q="credit note")
        assert search["writes"].calls == []

    def test_a_direct_call_has_no_tasks_and_writes_nothing(self, search: Any) -> None:
        asyncio.run(company.company_search(q=QUESTION, apps=None, k=5, user=_user()))
        assert search["writes"].calls == []

    def test_a_fault_in_the_gap_code_never_fails_the_search(
        self, search: Any, monkeypatch,
    ) -> None:
        def broken(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(gaps, "gap_for", broken)
        assert _get(_user())["hits"] == []

    def test_schedule_passes_the_masked_sample(self, gates: Any) -> None:
        tasks = BackgroundTasks()
        assert gaps.schedule_gap(tasks, _user(), "what is the salary Rs 85,000 for staff", 0)
        assert tasks.tasks[0].args[3] == "what is the salary [amount] for staff"
        assert tasks.tasks[0].args[2] == "what is the salary amount for staff"

    def test_schedule_reads_only_zero(self, gates: Any) -> None:
        tasks = BackgroundTasks()
        assert not gaps.schedule_gap(tasks, _user(), QUESTION, None)
        assert not gaps.schedule_gap(tasks, _user(), QUESTION, 2)
        assert not gaps.schedule_gap(None, _user(), QUESTION, 0)
        assert tasks.tasks == []
        assert gaps.schedule_gap(tasks, _user(), QUESTION, 0)
        assert [t.func for t in tasks.tasks] == [gaps.record_gap]

    def test_the_route_takes_no_tenant_for_the_write(self) -> None:
        params = inspect.signature(company.company_search).parameters
        assert params["background"].annotation in (BackgroundTasks, "BackgroundTasks")


class TestTheWriteNeverRaises:
    def test_a_database_fault_reads_as_zero(self, monkeypatch) -> None:
        from gateway.routes.knowledge import core

        def broken(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("no pool")

        monkeypatch.setattr(core, "_tenant_session", broken)
        got = asyncio.run(gaps.record_gap(ORG, "a@x.test", "a b c", "A b c"))
        assert got == 0

    def test_the_upsert_counts_a_new_asker_once_and_resets_an_old_window(self) -> None:
        sql = gaps.UPSERT_GAP_SQL
        assert "ON CONFLICT (organization_id, norm_query) DO UPDATE" in sql
        assert sql.count(f"interval '{gaps.WINDOW_DAYS} days'") == 3
        assert "g.first_asked_at < now()" in sql, "the window runs from the first ask"
        assert "g.last_asked_at < now()" not in sql
        assert "g.status = 'answered' AND g.answered_page_id IS NULL" in sql
        assert f">= {gaps.MAX_ASKERS} THEN g.askers" in sql


# ── The routes ──────────────────────────────────────────────────────────────

class TestTheRoutes:
    def _client(self, user: UserContext) -> TestClient:
        from gateway.routes import knowledge as kb_package

        app = FastAPI()
        app.include_router(kb_package.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def test_dismiss_needs_manage_before_any_read(self, gates: Any, monkeypatch) -> None:
        from gateway.routes.knowledge import core

        opened: list[str] = []

        def session(*_a: Any, **_k: Any) -> Any:
            opened.append("session")
            raise AssertionError("a member's dismiss must not reach the database")

        monkeypatch.setattr(core, "_tenant_session", session)
        res = self._client(_user()).post(f"/knowledge/gaps/{OTHER}/dismiss")
        assert res.status_code == 403
        assert "knowledge:manage" in res.json()["detail"]
        assert opened == []

    def test_reopen_needs_manage_before_any_read(self, gates: Any, monkeypatch) -> None:
        from gateway.routes.knowledge import core

        def session(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("a member's reopen must not reach the database")

        monkeypatch.setattr(core, "_tenant_session", session)
        res = self._client(_user()).post(f"/knowledge/gaps/{OTHER}/reopen")
        assert res.status_code == 403
        assert "knowledge:manage" in res.json()["detail"]

    @pytest.mark.parametrize(("status", "code"), [
        (None, 404), ("open", 409), ("answered", 409), ("dismissed", 200),
    ])
    def test_reopen_by_status(self, gates: Any, monkeypatch, status: str | None,
                              code: int) -> None:
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        from gateway.routes.knowledge import core

        state = {"status": status, "ran": []}

        class _Db:
            async def execute(self, stmt: Any, params: Any = None) -> None:
                state["ran"].append(str(stmt))
                state["status"] = "open"

        @asynccontextmanager
        async def session(*_a: Any, **_k: Any):
            yield _Db()

        async def one(db: Any, user: Any, gap_id: Any) -> Any:
            if state["status"] is None:
                raise core.not_found()
            return SimpleNamespace(
                id=OTHER, sample_query="q", ask_count=1, first_asked_at=None,
                last_asked_at=None, status=state["status"], page_id=None,
                page_title=None, asked_by_me=False, dismissed_at=None,
                dismissed_by=None)

        async def bump(_org: str) -> None:
            return None

        monkeypatch.setattr(core, "_tenant_session", session)
        monkeypatch.setattr(gaps, "_one_gap", one)
        monkeypatch.setattr(gaps, "_bump", bump)
        res = self._client(_user("a@x.test", "knowledge:manage")).post(
            f"/knowledge/gaps/{OTHER}/reopen")
        assert res.status_code == code, res.text
        if code == 200:
            assert res.json()["status"] == "open" and res.json()["dismissed_at"] is None
            assert "dismissed_at = NULL" in state["ran"][0]
        else:
            assert state["ran"] == []

    @pytest.mark.parametrize(("method", "path", "body"), [
        ("GET", "/knowledge/gaps", None),
        ("POST", f"/knowledge/gaps/{OTHER}/dismiss", None),
        ("POST", f"/knowledge/gaps/{OTHER}/reopen", None),
        ("POST", f"/knowledge/gaps/{OTHER}/answered", {"page_id": OTHER}),
    ])
    def test_each_route_is_404_with_the_flag_off(
        self, gates: Any, monkeypatch, method: str, path: str, body: Any,
    ) -> None:
        monkeypatch.setattr(gates, "knowledge_gaps", "", raising=False)
        res = self._client(_user("a@x.test", "knowledge:manage")).request(
            method, path, json=body)
        assert res.status_code == 404

    def test_an_unknown_status_is_a_422(self, gates: Any) -> None:
        res = self._client(_user("a@x.test", "knowledge:manage")).get(
            "/knowledge/gaps?status=closed")
        assert res.status_code == 422


# ── My Day: the source `knowledge` and the card `kb_gap` ────────────────────

class TestTheMyDayCard:
    def test_the_source_is_gated_feed_only_and_for_managers(self) -> None:
        from gateway.routes.shell import feed, needs

        p = needs.REGISTRY["knowledge"]
        assert p.gated and not p.needs and p.handled is None
        assert p.features == ("knowledge",)
        assert p.permission == "knowledge:manage"
        assert p.kinds == feed.KNOWLEDGE_KINDS == frozenset({"kb_gap"})
        assert not p.held_by(_user())
        assert p.held_by(_user("a@x.test", "knowledge:manage"))

    def test_the_flag_off_reads_absent_before_any_read(self, gates: Any, monkeypatch) -> None:
        from gateway.routes.shell import feed, needs

        monkeypatch.setattr(gates, "knowledge_gaps", "", raising=False)

        async def read(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("no read with the flag off")

        monkeypatch.setattr(gaps, "my_day_rows", read)
        with pytest.raises(needs.SourceAbsent):
            asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))

    def test_a_row_becomes_one_card(self, gates: Any, monkeypatch) -> None:
        from gateway.routes.shell import feed, feed_rank
        from gateway.routes.shell.state import SIGNAL_ID_PATTERN

        at = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

        async def rows(user: UserContext, limit: int) -> list[dict[str, Any]]:
            return [{"id": "g-1", "question": QUESTION, "ask_count": 3, "last_asked_at": at}]

        monkeypatch.setattr(gaps, "my_day_rows", rows)
        found = asyncio.run(feed._knowledge(_user("a@x.test", "knowledge:manage")))
        [signal] = found.signals()
        assert signal.signal_id == "knowledge:gap:g-1"
        import re

        assert re.fullmatch(SIGNAL_ID_PATTERN, signal.signal_id)
        assert signal.kind == "kb_gap" and signal.app == "knowledge"
        assert signal.title == QUESTION
        assert signal.facts == {"askers": 3, "last_asked_at": at.isoformat()}
        assert [(a["id"], a["href"]) for a in signal.acts] == [
            ("write-page", "/knowledge/gaps?write=g-1"),
            ("open-gaps", "/knowledge/gaps")]
        assert feed_rank.group("kb_gap", signal.facts, at, at.date()) == "fyi"
        key, line = feed._md3_why("kb_gap", signal.facts, UTC, at.date())
        assert key == "gap"
        assert line == ("3 people asked this in the last 30 days. Nobody has written "
                        "it down in Knowledge.")

    def test_the_reach_class_is_brief(self) -> None:
        from gateway.reach import classes

        assert classes.REACH_CLASS["kb_gap"] == "brief"

    def test_the_client_names_the_kind_and_the_app(self) -> None:
        text = (ROOT / "workbench/control_plane/src/lib/shell/feed.ts").read_text(
            encoding="utf-8")
        assert 'kb_gap: "Unanswered question"' in text
        assert 'knowledge: "/knowledge"' in text
        assert 'knowledge: "Knowledge"' in text


# ── The chat manifest ───────────────────────────────────────────────────────

class TestTheManifest:
    def test_the_list_is_planned_and_the_decisions_are_off_the_chat(self) -> None:
        from acb_skills import knowledge_manifest as m

        rows = {(r.method, r.path): r for r in m.MANIFEST}
        listed = rows[("GET", "/knowledge/gaps")]
        assert (listed.cls, listed.tool) == ("A", "") and listed.planned
        for verb in ("dismiss", "answered"):
            row = rows[("POST", f"/knowledge/gaps/{{gap_id}}/{verb}")]
            assert row.cls == "X" and not row.tool and "person" in row.reason


# ── The member purge ────────────────────────────────────────────────────────

class TestThePurge:
    def test_the_purge_body_reaches_the_gaps(self) -> None:
        from gateway.routes.admin import members

        src = Path(members.__file__).read_text(encoding="utf-8")
        body = src[src.index("async def purge_member_rows("):src.index("# ── Role assignment")]
        assert "await purge_knowledge_member(db, org_id, addr)" in body

    async def test_the_admin_route_runs_the_scrub(self, monkeypatch) -> None:
        from gateway.routes.admin import members
        from gateway.routes.admin.members import purge_member

        from tests.unit._admin_fakes import _FakeDB, bind_admin_db
        from tests.unit.test_admin_member_purge import OWNER, PRIYA

        fake = _FakeDB()
        bind_admin_db(monkeypatch, fake, (members,))
        fake.seed_user("u-owner", "owner@fracktal.in", joined_at="then")
        fake.user_roles["u-owner"] = ["owner"]
        fake.seed_user("u-priya", PRIYA, joined_at="then")
        fake.user_roles["u-priya"] = ["member"]

        async def no_plans(*_a: Any, **_k: Any) -> list:
            return []

        monkeypatch.setattr(members, "read_teardown_plans", no_plans)
        await purge_member(PRIYA, admin=OWNER)
        assert len(fake.kb_purge) == 2
        assert all("UPDATE kb_gaps" in s for s in fake.kb_purge)
        assert any("array_remove(askers" in s for s in fake.kb_purge)
