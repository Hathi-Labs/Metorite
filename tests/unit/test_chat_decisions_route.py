"""HRD-12b (task 3481): the member's kept decisions of a chat, and Undo.

Code: ``acb_skills.chat_constraints`` (``decisions_of``, ``undo_decision``,
``item_id``, ``load_constraints(fresh=True)``), the member route
``gateway/routes/admin/chat_decisions.py`` and the one line of the executor.
Spec: ``chat_failure_modes.md`` §6 row HRD-12b. The chip is
``src/components/chat/DecisionChips.tsx``.

R7, each with the mutation that turns it red:

* ``TestTheStore`` — list a limit as a chip, or let an Undo remove a limit.
* ``TestTheMember`` — key the list by anything but the session and the hash of
  the member: another member's chip would show, and an Undo would reach it.
* ``TestTheNextTurn`` — drop ``fresh=True`` in ``_prime_chat_constraints``:
  the process cache answers and the decision holds after the Undo.
* ``TestTheRoute`` — read the member or the org from the body, or let the flag
  off answer a list.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest
from acb_common.db import bind_tenant, release_tenant
from acb_skills import chat_constraints as cc
from acb_skills.write_artifact import bind_artifact_context

ORG, OTHER_ORG = "org-a", "org-b"
SESSION = "thread-1"
ME, BEN = "vj@hathilabs.test", "ben@hathilabs.test"
OPEN_CTX = {"session_id": SESSION, "member": ME, cc.OPEN: True}
_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def flag(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_constraints", True, raising=False)


@pytest.fixture
def tenant():
    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture
def store(monkeypatch):
    from tests.unit._egress_mark_store import install_memory_mark_store

    cc.clear_cache()
    yield install_memory_mark_store(monkeypatch)
    cc.clear_cache()


@pytest.fixture
def chat(flag, tenant, store):
    bind_artifact_context(**OPEN_CTX)
    yield store
    bind_artifact_context()


def _put(items, *, org=ORG, session=SESSION, member=ME) -> None:

    ok = asyncio.run(_save(items, org, session, member))
    assert ok is True


async def _save(items, org, session, member) -> bool:
    from acb_common.tenant_redis import organization_scope

    with organization_scope(org):
        return await cc._write_redis(org, session, member, cc.clean(items))


def _decisions(*, org=ORG, session=SESSION, member=ME):
    return asyncio.run(cc.decisions_of(org, session, member))


def _undo(wanted: str, *, org=ORG, session=SESSION, member=ME):
    return asyncio.run(cc.undo_decision(org, session, member, wanted))


D1 = {"kind": cc.DECISION, "text": "TEDx is done."}
D2 = {"kind": cc.DECISION, "text": "We use the Pune vendor."}
L1 = {"kind": cc.LIMIT, "text": "Do not email Acme."}


class TestTheStore:
    def test_only_decisions_are_listed_each_with_a_stable_id(self, chat) -> None:
        _put([L1, D1, D2])
        got = _decisions()
        assert [g["text"] for g in got] == [D1["text"], D2["text"]]
        assert all(g["kind"] == cc.DECISION for g in got)
        assert [g["id"] for g in got] == [cc.item_id(D1), cc.item_id(D2)]
        assert re.fullmatch(r"[0-9a-f]{12}", got[0]["id"])
        assert D1["text"] not in got[0]["id"]

    def test_an_undo_takes_back_one_decision_and_keeps_the_rest(self, chat) -> None:
        _put([L1, D1, D2])
        state, gone = _undo(cc.item_id(D1))
        assert state == cc.UNDONE and gone == D1
        assert [g["text"] for g in _decisions()] == [D2["text"]]
        # The limit stays, in the stored list.
        assert [i["text"] for i in asyncio.run(_load())] == [L1["text"], D2["text"]]

    def test_an_undo_never_removes_a_limit(self, chat) -> None:
        _put([L1, D1])
        assert _undo(cc.item_id(L1)) == (cc.UNDO_NO_MATCH, None)
        assert [i["text"] for i in asyncio.run(_load())] == [L1["text"], D1["text"]]

    def test_an_undo_of_a_gone_or_unknown_id_is_no_match_and_writes_nothing(self, chat) -> None:
        _put([D1])
        before = len(chat.calls)
        assert _undo("0" * 12) == (cc.UNDO_NO_MATCH, None)
        assert not [c for c in chat.calls[before:] if c[0] == "setex"]
        assert _undo(cc.item_id(D1))[0] == cc.UNDONE
        assert _undo(cc.item_id(D1)) == (cc.UNDO_NO_MATCH, None)

    def test_a_fault_of_redis_says_unavailable_and_never_raises(self, chat, monkeypatch) -> None:
        _put([D1])

        async def boom(*_a, **_k):
            raise ConnectionError("redis down")

        monkeypatch.setattr(chat, "get", boom)
        assert _decisions() is None
        assert _undo(cc.item_id(D1)) == (cc.UNDO_UNAVAILABLE, None)

    def test_the_flag_off_lists_and_undoes_nothing(self, tenant, store) -> None:
        _put([D1])
        assert _decisions() == []
        assert _undo(cc.item_id(D1)) == (cc.UNDO_UNAVAILABLE, None)


async def _load():
    return await cc._read_redis(ORG, SESSION, ME)


class TestTheMember:
    def test_another_member_another_chat_and_another_org_see_and_reach_nothing(self, chat) -> None:
        _put([D1])
        assert _decisions(member=BEN) == []
        assert _decisions(session="thread-2") == []
        assert _decisions(org=OTHER_ORG) == []
        assert _undo(cc.item_id(D1), member=BEN) == (cc.UNDO_NO_MATCH, None)
        assert _undo(cc.item_id(D1), session="thread-2") == (cc.UNDO_NO_MATCH, None)
        assert _undo(cc.item_id(D1), org=OTHER_ORG) == (cc.UNDO_NO_MATCH, None)
        assert [g["text"] for g in _decisions()] == [D1["text"]], "mine stays"

    def test_the_key_holds_no_address_and_the_member_is_lowered(self, chat) -> None:
        _put([D1], member=ME)
        assert not [k for k in chat.values if ME in k]
        assert _decisions(member=ME.upper()) != []

    def test_an_empty_slot_part_reads_nothing(self, chat) -> None:
        assert _decisions(member="no-address") == []
        assert _decisions(session="") == []
        assert _undo(cc.item_id(D1), member="") == (cc.UNDO_UNAVAILABLE, None)


class TestTheNextTurn:
    def test_a_fresh_load_reads_redis_so_an_undo_of_another_process_holds(self, chat) -> None:
        """The orchestrator cached the list in this process. The gateway (another
        process) undoes through Redis. The next turn must not follow the decision."""
        async def go():
            await cc.save_constraints([D1, L1])
            assert [i["text"] for i in await cc.load_constraints()] == [D1["text"], L1["text"]]
            # The gateway: Redis only, no touch of this process cache.
            from acb_common.tenant_redis import organization_scope

            with organization_scope(ORG):
                await cc._write_redis(ORG, SESSION, ME, [L1])
            stale = await cc.load_constraints()
            fresh = await cc.load_constraints(fresh=True)
            return stale, fresh, await cc.load_constraints()

        stale, fresh, after = asyncio.run(go())
        assert [i["text"] for i in stale] == [D1["text"], L1["text"]], "the cache answers a plain load"
        assert [i["text"] for i in fresh] == [L1["text"]]
        assert [i["text"] for i in after] == [L1["text"]], "and the cache takes the fresh list"

    def test_a_fresh_load_with_an_empty_redis_empties_a_cached_list(self, chat) -> None:
        async def go():
            await cc.save_constraints([D1])
            from acb_common.tenant_redis import organization_scope

            with organization_scope(ORG):
                await cc._write_redis(ORG, SESSION, ME, [])
            return await cc.load_constraints(fresh=True)

        assert asyncio.run(go()) == []

    def test_a_fault_of_redis_keeps_the_cached_list(self, chat, monkeypatch) -> None:
        async def go():
            await cc.save_constraints([D1])

            async def boom(*_a, **_k):
                raise ConnectionError("down")

            monkeypatch.setattr(chat, "get", boom)
            return await cc.load_constraints(fresh=True)

        assert [i["text"] for i in asyncio.run(go())] == [D1["text"]]

    def test_the_executor_reads_fresh_at_the_start_of_each_turn(self) -> None:
        src = (_ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(
            encoding="utf-8")
        body = src.split("async def _prime_chat_constraints", 1)[1].split("\ndef ", 1)[0]
        assert "load_constraints(fresh=True)" in body


class TestTheRoute:
    @staticmethod
    def _client(member: str = ME, org: str = ORG):
        from acb_auth import UserContext, UserRole, get_current_user
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from gateway.routes.admin import chat_decisions  # noqa: F401
        from gateway.routes.admin.me import me_router

        app = FastAPI()
        app.include_router(me_router)
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            email=member, role=UserRole.EMPLOYEE, organization_id=org)
        return TestClient(app)

    def test_the_flag_off_reads_disabled_and_undoes_404(self, tenant, store) -> None:
        _put([D1])
        c = self._client()
        r = c.get("/auth/me/chat-decisions", params={"session_id": SESSION})
        assert r.status_code == 200 and r.json() == {"enabled": False, "items": []}
        r = c.post("/auth/me/chat-decisions/undo", json={"session_id": SESSION, "id": cc.item_id(D1)})
        assert (r.status_code, r.json()["detail"]) == (404, "off")

    def test_read_then_undo_then_read(self, chat) -> None:
        _put([L1, D1, D2])
        c = self._client()
        r = c.get("/auth/me/chat-decisions", params={"session_id": SESSION})
        assert r.status_code == 200 and r.json()["enabled"] is True
        assert [i["text"] for i in r.json()["items"]] == [D1["text"], D2["text"]]
        r = c.post("/auth/me/chat-decisions/undo",
                   json={"session_id": SESSION, "id": r.json()["items"][0]["id"]})
        assert r.status_code == 200
        assert r.json() == {"undone": {"id": cc.item_id(D1), "kind": "decision", "text": D1["text"]}}
        r = c.get("/auth/me/chat-decisions", params={"session_id": SESSION})
        assert [i["text"] for i in r.json()["items"]] == [D2["text"]]

    def test_a_second_undo_is_404_no_match(self, chat) -> None:
        _put([D1])
        c = self._client()
        body = {"session_id": SESSION, "id": cc.item_id(D1)}
        assert c.post("/auth/me/chat-decisions/undo", json=body).status_code == 200
        r = c.post("/auth/me/chat-decisions/undo", json=body)
        assert (r.status_code, r.json()["detail"]) == (404, "no_match")

    def test_the_member_and_the_org_come_from_the_session_never_the_body(self, chat) -> None:
        _put([D1])
        ben = self._client(member=BEN)
        body = {"session_id": SESSION, "id": cc.item_id(D1)}
        # Ben cannot reach my chat's list, and cannot name me in the body.
        assert ben.post("/auth/me/chat-decisions/undo", json=body).status_code == 404
        r = ben.post("/auth/me/chat-decisions/undo", json={**body, "member": ME})
        assert r.status_code == 422
        r = ben.post("/auth/me/chat-decisions/undo", json={**body, "organization_id": ORG})
        assert r.status_code == 422
        assert ben.get("/auth/me/chat-decisions", params={"session_id": SESSION}).json()["items"] == []
        assert [g["text"] for g in _decisions()] == [D1["text"]]

    @pytest.mark.parametrize("session", ["", "a b", "a/b", "x" * 129, "../x"])
    def test_a_bad_chat_id_is_422(self, chat, session) -> None:
        c = self._client()
        assert c.get("/auth/me/chat-decisions", params={"session_id": session}).status_code in (422,)
        r = c.post("/auth/me/chat-decisions/undo", json={"session_id": session, "id": "a" * 12})
        assert r.status_code == 422

    def test_a_bad_item_id_is_404_and_writes_nothing(self, chat) -> None:
        _put([D1])
        before = len(chat.calls)
        r = self._client().post("/auth/me/chat-decisions/undo",
                                json={"session_id": SESSION, "id": "ZZ"})
        assert r.status_code == 404
        assert not [c for c in chat.calls[before:] if c[0] == "setex"]

    def test_a_fault_of_redis_is_503(self, chat, monkeypatch) -> None:
        _put([D1])

        async def boom(*_a, **_k):
            raise ConnectionError("down")

        monkeypatch.setattr(chat, "get", boom)
        c = self._client()
        assert c.get("/auth/me/chat-decisions", params={"session_id": SESSION}).status_code == 503
        r = c.post("/auth/me/chat-decisions/undo", json={"session_id": SESSION, "id": cc.item_id(D1)})
        assert r.status_code == 503

    def test_no_session_no_route_work(self, chat) -> None:
        from acb_auth import get_current_user
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from gateway.routes.admin.me import me_router

        app = FastAPI()
        app.include_router(me_router)
        from acb_auth import UserContext, UserRole

        app.dependency_overrides[get_current_user] = lambda: UserContext(
            email=None, role=UserRole.EMPLOYEE, organization_id=ORG)
        r = TestClient(app).get("/auth/me/chat-decisions", params={"session_id": SESSION})
        assert r.status_code == 401

    def test_the_route_reads_no_database_and_names_no_profile_column(self) -> None:
        src = inspect.getsource(__import__("gateway.routes.admin.chat_decisions",
                                           fromlist=["x"]))
        assert "assistant_profile" not in src.split('"""', 2)[2]
        assert "_tenant_session" not in src and "text(" not in src
