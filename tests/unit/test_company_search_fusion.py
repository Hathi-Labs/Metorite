"""WS-KG KG-3b — one fused list and a vector half, behind ``CONTEXT_RECENCY``.

Spec: ``project-docs/specs/knowledge_graph.md`` §9 (KG-3b) and §7.3.
No database and no embedding call: the embed door is a fake. The real read of
``semantic_messages`` on Postgres is ``test_company_search_fusion_r8.py``.

The claims, each written so a wrong build fails it:

* **RRF order.** ``by_fusion`` sorts every app's hits in ONE list by the
  score of CX-1 (``1 / (60 + rank)`` times the decay). A tie goes to the app
  named first. ``rrf_scores`` is the one rank sum, and Knowledge's ``fuse``
  still uses it.
* **Flag off, byte for byte.** The answer is the ``_interleave`` one, the apps
  get no ``vectors`` option, and no turn store opens.
* **A paraphrase finds the mail** through the vector half, when the lexical
  half finds nothing.
* **No vectors, the lexical answer.** No error and no embed call.
* **One embed call per turn** for one query, however many sources ask.
* **Nothing widens.** The vector half takes the member from the session and
  its reads go through the account scope of Email (checked on SQL here, on a
  database in the R8 file).

Mutations this file catches (R7): the sort reverts to ``_interleave`` ->
``TestTheFusedOrder``; the route passes ``vectors`` with the flag off ->
``TestFlagOffIsByteIdentical``; the cache is dropped -> ``TestOneEmbedPerTurn``.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from gateway.kb_index import query as kb_query
from gateway.retrieval import company, sources, turn_vector
from gateway.retrieval.sources import Hit

from tests.unit.test_company_search_recency import recency, world  # noqa: F401
from tests.unit.test_company_search_sources import ALL, apps, member  # noqa: F401

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _hit(app: str, ident: str, stamp: str | None = None) -> Hit:
    return Hit(app=app, kind=app, id=ident, title=ident, hint=app, href=f"/{app}/{ident}",
               snippet="", when=None, stamp=stamp)


def run(user: Any, q: str = "acme", k: int = 5, apps_: str | None = None) -> dict:
    return asyncio.run(company.company_search(q=q, apps=apps_, k=k, user=user))


# ── The one rank sum ────────────────────────────────────────────────────────

class TestTheRankSum:
    def test_k_is_sixty_and_the_ranks_start_at_one(self):
        scores, items = kb_query.rrf_scores(
            (["a", "b"], ["b", "c"]), key=lambda x: x)
        assert kb_query.RRF_K == 60
        assert scores["a"] == pytest.approx(1 / 61)
        assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
        assert scores["c"] == pytest.approx(1 / 62)
        assert list(items) == ["a", "b", "c"]

    def test_knowledge_fuse_still_uses_it(self):
        src = inspect.getsource(kb_query.fuse)
        assert "rrf_scores(" in src and "1.0 / (rrf_k" not in src


# ── The fused order ─────────────────────────────────────────────────────────

class TestTheFusedOrder:
    def test_one_list_sorted_by_the_score_across_apps(self):
        found = {
            "tasks": [_hit("tasks", "t1"), _hit("tasks", "t2")],
            "email": [_hit("email", "e1"), _hit("email", "e2")],
        }
        got = company.by_fusion(["tasks", "email"], found, NOW)
        # Equal scores at each rank: the app named first goes first.
        assert [h.id for h in got] == ["t1", "e1", "t2", "e2"]
        flipped = company.by_fusion(["email", "tasks"], found, NOW)
        assert [h.id for h in flipped] == ["e1", "t1", "e2", "t2"]

    def test_a_lone_deep_app_does_not_wait_for_a_turn(self):
        # `_interleave` gives the email its 3rd place only after tasks' 3rd.
        found = {"tasks": [_hit("tasks", "t1")],
                 "email": [_hit("email", f"e{n}") for n in range(1, 4)]}
        got = company.by_fusion(["tasks", "email"], found, NOW)
        assert [h.id for h in got] == ["t1", "e1", "e2", "e3"]

    def test_the_decay_lowers_an_old_hit_below_a_lower_ranked_new_one(self):
        old = _hit("email", "old", "2025-01-01T00:00:00+00:00")
        new = _hit("email", "new", "2026-10-17T00:00:00+00:00")
        got = company.by_fusion(["email"], {"email": [old, new]}, NOW)
        assert [h.id for h in got] == ["new", "old"]
        # The sort is not by date alone: a near-equal date keeps the rank.
        near = _hit("email", "near", "2026-10-16T00:00:00+00:00")
        got = company.by_fusion(["email"], {"email": [near, new]}, NOW)
        assert [h.id for h in got] == ["near", "new"]

    def test_the_input_is_not_changed(self):
        mine = [_hit("tasks", "t1"), _hit("tasks", "t2")]
        company.by_fusion(["tasks"], {"tasks": mine}, NOW)
        assert [h.id for h in mine] == ["t1", "t2"]


# ── Flag off ────────────────────────────────────────────────────────────────

class TestFlagOffIsByteIdentical:
    def test_the_answer_is_the_interleave_of_today(self, world, recency):  # noqa: F811
        recency(False)
        user = member(*ALL)
        answer = run(user)
        assert set(answer) == {"query", "hits", "skipped"}
        # The golden: the ids and apps of the answer, frozen before KG-3b.
        assert [(h["app"], h["id"]) for h in answer["hits"]] == [
            ("tasks", "t-1"), ("email", "m-1"), ("people", "p-1"), ("crm", "d-1"),
            ("artifacts", "a-1"), ("whatsapp", "w-1"), ("knowledge", "k-1"),
            ("tasks", "t-2"), ("email", "m-2"), ("crm", "c-1"), ("artifacts", "a-2"),
            ("whatsapp", "w-2"), ("knowledge", "k-2"),
        ]
        assert all(set(h) == {"app", "kind", "id", "title", "hint", "href", "snippet", "when"}
                   for h in answer["hits"])

    def test_the_apps_get_no_vectors_option(self, world, recency):  # noqa: F811
        recency(False)
        run(member(*ALL))
        email_call = next(kw for name, _u, kw in world.calls if name == "email")
        assert "vectors" not in email_call

    def test_on_it_changes_only_the_order_and_adds_the_stamp(self, world, recency):  # noqa: F811
        recency(True)
        answer = run(member(*ALL))
        assert "searched" in answer
        assert all("stamp" in h for h in answer["hits"])
        recency(False)
        off = run(member(*ALL))
        assert sorted(h["id"] for h in answer["hits"]) == sorted(h["id"] for h in off["hits"])


# ── The vector half of Email ────────────────────────────────────────────────

class _Mail:
    """A fake Email search: lexical finds ``words`` only, vectors find by meaning."""

    def __init__(self, monkeypatch, *, has_vectors: bool = True, lexical=(), lex_delay: float = 0.0):
        import email_ingestion.email_embeddings as ee
        import gateway.routes.email.transport.search as email_search

        self.embeds: list[str] = []
        self.semantic_calls: list[dict] = []
        self.has_vectors = has_vectors
        self.lexical = list(lexical)

        async def lexical_search(**kw):
            await asyncio.sleep(lex_delay)
            return {"emails": list(self.lexical), "total": None}

        async def embed_query(words: str, org=None):
            self.embeds.append(words)
            return [1.0, 0.0]

        async def semantic(*, user, words, k, received_after, embed):
            self.semantic_calls.append({"user": user, "k": k, "after": received_after})
            if not self.has_vectors:
                return []          # as the real read: no vector, no embed call
            vector = await embed(words)
            return [] if vector is None else [{
                "id": "m-paraphrase", "subject": "Invoice March",
                "from_address": {"name": "Priya"}, "snippet": "pay by Friday",
                "received_at": "2026-10-01T08:00:00+00:00", "highlight": ""}]

        monkeypatch.setattr(email_search, "search_messages", lexical_search)
        monkeypatch.setattr(email_search, "semantic_messages", semantic)
        monkeypatch.setattr(ee, "embed_query", embed_query)


def _email(user, vectors: bool, q: str = "bill for the third month") -> list[Hit]:
    return asyncio.run(sources.REGISTRY["email"].find(user, q, 5, vectors=vectors))


class TestTheVectorHalf:
    def test_a_paraphrase_finds_the_mail(self, monkeypatch):
        mail = _Mail(monkeypatch)
        turn_vector.begin_turn()
        got = _email(member("email"), True)
        assert [h.id for h in got] == ["m-paraphrase"]
        assert got[0].title == "Invoice March"
        assert mail.embeds == ["bill for the third month"]

    def test_without_the_flag_the_paraphrase_finds_nothing(self, monkeypatch):
        mail = _Mail(monkeypatch)
        assert _email(member("email"), False) == []
        assert mail.semantic_calls == [] and mail.embeds == []

    def test_no_vectors_means_the_lexical_answer_and_no_embed(self, monkeypatch):
        lexical = [{"id": "m-lex", "subject": "Bill", "from_address": {}, "snippet": "x",
                    "received_at": None, "highlight": ""}]
        mail = _Mail(monkeypatch, has_vectors=False, lexical=lexical)
        turn_vector.begin_turn()
        got = _email(member("email"), True)
        assert [h.id for h in got] == ["m-lex"]
        assert mail.embeds == []

    def test_a_failed_vector_half_leaves_the_lexical_answer(self, monkeypatch):
        import gateway.routes.email.transport.search as email_search

        lexical = [{"id": "m-lex", "subject": "Bill", "from_address": {}, "snippet": "x",
                    "received_at": None, "highlight": ""}]
        _Mail(monkeypatch, lexical=lexical)

        async def boom(**_kw):
            raise RuntimeError("database says no")

        monkeypatch.setattr(email_search, "semantic_messages", boom)
        assert [h.id for h in _email(member("email"), True)] == ["m-lex"]

    def test_both_halves_fuse_by_rank_and_a_mail_in_both_wins(self, monkeypatch):
        both = {"id": "m-paraphrase", "subject": "Invoice March", "from_address": {},
                "snippet": "", "received_at": None, "highlight": ""}
        only = {"id": "m-only", "subject": "Other", "from_address": {}, "snippet": "",
                "received_at": None, "highlight": ""}
        _Mail(monkeypatch, lexical=[only, both])
        turn_vector.begin_turn()
        got = _email(member("email"), True)
        assert [h.id for h in got] == ["m-paraphrase", "m-only"]

    def test_the_member_is_the_session_member(self, monkeypatch):
        mail = _Mail(monkeypatch)
        user = member("email")
        turn_vector.begin_turn()
        _email(user, True)
        assert mail.semantic_calls[0]["user"] is user
        params = inspect.signature(
            __import__("gateway.routes.email.transport.search",
                       fromlist=["semantic_messages"]).semantic_messages).parameters
        assert not {"org", "organization_id", "tenant", "email"} & set(params)


# ── The deadline of the vector half ─────────────────────────────────────────

class TestTheVectorDeadline:
    LEX: ClassVar[list] = [{"id": "m-lex", "subject": "Bill", "from_address": {}, "snippet": "x",
            "received_at": None, "highlight": ""}]

    def test_a_slow_vector_half_leaves_the_lexical_hits(self, monkeypatch):
        import gateway.routes.email.transport.search as email_search

        _Mail(monkeypatch, lexical=self.LEX)

        async def slow(**_kw):
            await asyncio.sleep(5)
            return []

        monkeypatch.setattr(email_search, "semantic_messages", slow)
        monkeypatch.setattr(kb_query, "QUERY_EMBED_TIMEOUT_S", 0.05)
        assert [h.id for h in _email(member("email"), True)] == ["m-lex"]

    def test_the_source_is_not_skipped_when_the_vector_half_is_slow(self, world, recency, monkeypatch):  # noqa: F811
        import gateway.routes.email.transport.search as email_search

        recency(True)

        async def slow(**_kw):
            await asyncio.sleep(5)
            return []

        monkeypatch.setattr(email_search, "semantic_messages", slow)
        monkeypatch.setattr(kb_query, "QUERY_EMBED_TIMEOUT_S", 0.05)
        answer = run(member(*ALL))
        assert "email" not in answer["skipped"]
        assert {"m-1", "m-2"} <= {h["id"] for h in answer["hits"]}

    def test_a_raising_vector_half_leaves_the_lexical_hits(self, monkeypatch):
        import gateway.routes.email.transport.search as email_search

        _Mail(monkeypatch, lexical=self.LEX)

        async def boom(**_kw):
            raise RuntimeError("no")

        monkeypatch.setattr(email_search, "semantic_messages", boom)
        assert [h.id for h in _email(member("email"), True)] == ["m-lex"]

    def test_the_budget_is_below_the_source_timeout_and_the_call_is_under_wait_for(self):
        assert kb_query.QUERY_EMBED_TIMEOUT_S < company.SOURCE_TIMEOUT_S
        src = inspect.getsource(sources._email)
        import re

        assert re.search(r"asyncio\.wait_for\(\s*semantic_messages\(", src)
        assert src.count("semantic_messages(") == 1


class TestTheBudgetRunsFromTheStart:
    LEX = TestTheVectorDeadline.LEX

    def _slow_vectors(self, monkeypatch, mail, seconds=5.0):
        import gateway.routes.email.transport.search as email_search

        async def slow(*, user, words, k, received_after, embed):
            await embed(words)
            await asyncio.sleep(seconds)
            return []

        monkeypatch.setattr(email_search, "semantic_messages", slow)

    def test_a_one_second_lexical_half_still_answers(self, apps, recency, monkeypatch):  # noqa: F811
        recency(True)
        mail = _Mail(monkeypatch, lexical=self.LEX, lex_delay=1.0)
        self._slow_vectors(monkeypatch, mail)
        answer = run(member("email"))
        assert "email" not in answer["skipped"]
        assert [h["id"] for h in answer["hits"]] == ["m-lex"]

    def test_a_1_4_second_lexical_half_skips_the_vector_half(self, apps, recency, monkeypatch):  # noqa: F811
        recency(True)
        mail = _Mail(monkeypatch, lexical=self.LEX, lex_delay=1.4)
        self._slow_vectors(monkeypatch, mail)
        answer = run(member("email"))
        assert "email" not in answer["skipped"]
        assert [h["id"] for h in answer["hits"]] == ["m-lex"]
        assert mail.embeds == []

    def test_a_fast_lexical_half_gets_the_full_budget(self, apps, recency, monkeypatch):  # noqa: F811
        recency(True)
        seen: list[float] = []
        orig = asyncio.wait_for

        async def spy(aw, timeout):
            if timeout is not None and timeout < 1.0 and "semantic" in repr(aw):
                seen.append(timeout)
            return await orig(aw, timeout)

        _Mail(monkeypatch, lexical=self.LEX)
        monkeypatch.setattr(sources.asyncio, "wait_for", spy)
        run(member("email"))
        assert seen and seen[0] == pytest.approx(kb_query.QUERY_EMBED_TIMEOUT_S)

    def test_the_budget_is_the_route_time_left_when_it_is_lower(self, monkeypatch):
        mail = _Mail(monkeypatch, lexical=self.LEX)
        self._slow_vectors(monkeypatch, mail)
        got = asyncio.run(sources.REGISTRY["email"].find(
            member("email"), "q", 5, vectors=True, budget_s=0.3))
        assert [h.id for h in got] == ["m-lex"]      # 0.3 - 0.2 < the floor: skipped
        assert mail.embeds == []

    def test_flag_off_passes_no_budget(self, world, recency):  # noqa: F811
        recency(False)
        run(member(*ALL))
        email_call = next(kw for name, _u, kw in world.calls if name == "email")
        assert "budget_s" not in email_call and "vectors" not in email_call


# ── One embed call per turn ─────────────────────────────────────────────────

class TestOneEmbedPerTurn:
    def test_two_asks_in_one_turn_embed_once(self, monkeypatch):
        mail = _Mail(monkeypatch)
        turn_vector.begin_turn()
        _email(member("email"), True)
        _email(member("email"), True)
        assert mail.embeds == ["bill for the third month"]

    def test_knowledge_reuses_the_vector_of_email(self, monkeypatch):
        calls: list[str] = []

        async def door(texts, **_kw):
            calls.append(texts[0])
            return [[0.5, 0.5]]

        monkeypatch.setattr(kb_query.embed_door, "embed", door)
        monkeypatch.setattr(kb_query, "knowledge_embed_allowed", lambda _o: True)
        turn_vector.begin_turn()
        turn_vector.remember("org-1", "bill  for the third month", [9.0, 9.0])
        got = asyncio.run(kb_query.embed_query("org-1", "bill for the third month"))
        assert got == [9.0, 9.0] and calls == []

    def test_the_door_runs_once_and_the_next_ask_reuses_it(self, monkeypatch):
        calls: list[str] = []

        async def door(texts, **_kw):
            calls.append(texts[0])
            return [[0.5, 0.5]]

        monkeypatch.setattr(kb_query.embed_door, "embed", door)
        monkeypatch.setattr(kb_query, "knowledge_embed_allowed", lambda _o: True)
        turn_vector.begin_turn()
        for _ in range(3):
            assert asyncio.run(kb_query.embed_query("org-1", "same words")) == [0.5, 0.5]
        assert calls == ["same words"]

    def test_no_turn_no_cache(self, monkeypatch):
        calls: list[str] = []

        async def door(texts, **_kw):
            calls.append(texts[0])
            return [[0.5, 0.5]]

        monkeypatch.setattr(kb_query.embed_door, "embed", door)
        monkeypatch.setattr(kb_query, "knowledge_embed_allowed", lambda _o: True)
        turn_vector._TURN.set(None)
        asyncio.run(kb_query.embed_query("org-1", "same words"))
        asyncio.run(kb_query.embed_query("org-1", "same words"))
        assert calls == ["same words", "same words"]

    def test_a_vector_is_never_shared_across_orgs_or_words(self):
        turn_vector.begin_turn()
        turn_vector.remember("org-1", "x y", [1.0])
        assert turn_vector.recall("org-1", "x  y") == [1.0]
        assert turn_vector.recall("org-2", "x y") is None
        assert turn_vector.recall("org-1", "x z") is None

    def test_a_failure_is_not_kept(self):
        turn_vector.begin_turn()
        turn_vector.remember("org-1", "x", None)
        assert turn_vector.recall("org-1", "x") is None

    def test_the_route_opens_a_turn_only_with_the_flag(self, world, recency, monkeypatch):  # noqa: F811
        opened: list[int] = []
        monkeypatch.setattr(turn_vector, "begin_turn", lambda: opened.append(1))
        recency(False)
        run(member(*ALL))
        assert opened == []
        recency(True)
        run(member(*ALL))
        assert opened == [1]
