"""Task 4017: ONE test of "the member of this run, or a shared room".

A shared room binds the memory id ``room:<thread id>`` (the gateway's stream
route, ``acb_memory.resolve_clearance``). The thread id is the client's chosen
chat id (``SessionUpsertRequest.id`` has no validator), so ``room:team@x`` is a
real id. A bare ``"@" in`` test read it as a member, and a member's own data
went into a room that other people read.

Three parts:

1. The rules of ``memory_tools.run_member_address``.
2. Each converted tool, in a run bound to ``room:team@x``, gives its own
   refusal sentence and sends no request.
3. The fence: no module of ``acb_skills`` tests ``"@" in`` / ``"@" not in``
   outside the allow-list, so the helper stays the one place.

Hermetic. The R8 case is in ``test_people_tools_r8.py``.
"""
from __future__ import annotations

import ast
import collections
import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from acb_common import bind_run_context, run_context_scope
from acb_skills import memory_tools as mt
from acb_skills.memory_tools import (
    SHARED_ID_PREFIXES,
    _bind_memory_user_id,
    _unbind_memory_user_id,
    run_member_address,
)
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

ROOM = "room:team@x"
MEMBER = "mel@example.com"
SKILLS = Path(mt.__file__).resolve().parent


# ── 1. The rules of the helper ──────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    ROOM, "room:x", "ROOM:team@x", " room:team@x ", "", "   ", None, 7, b"a@b.com",
    "a@b@c", "x y@z", "x\ty@z", "a@b\x00", "group:a@b", "agent:sales@x",
    "prefs:a@b.com", "org:a@b", "a:b@c.com", "a@b:c", "@b.com", "a@", "@",
    "a" * 320 + "@b.com", "no-at-sign",
])
def test_the_helper_refuses_each_value_that_names_no_member(value: Any) -> None:
    assert run_member_address(value) is None


@pytest.mark.parametrize("prefix", SHARED_ID_PREFIXES)
def test_the_helper_refuses_each_shared_prefix(prefix: str) -> None:
    assert run_member_address(f"{prefix}team@x.com") is None
    assert run_member_address(f"{prefix.upper()}team@x.com") is None


@pytest.mark.parametrize("value", [MEMBER, "Mel.O+tag@Example.co.in", "a@b"])
def test_the_helper_accepts_a_plain_member_address(value: str) -> None:
    assert run_member_address(value) == value


def test_the_helper_strips_and_keeps_the_case() -> None:
    assert run_member_address("  Mel@Example.com ") == "Mel@Example.com"


def test_with_no_argument_the_helper_reads_the_memory_id() -> None:
    binding = _bind_memory_user_id(ROOM)
    try:
        assert run_member_address() is None
    finally:
        _unbind_memory_user_id(binding)
    binding = _bind_memory_user_id(MEMBER)
    try:
        assert run_member_address() == MEMBER
    finally:
        _unbind_memory_user_id(binding)


def test_a_run_with_no_binding_names_no_member() -> None:
    assert run_member_address() is None


# ── 2. Each converted tool, in a shared room ────────────────────────────────


class _NoRequest:
    """An ``httpx.AsyncClient`` that records and refuses each request.

    Opening the client is no request (``crm_tools._get`` reads its headers
    inside the ``async with``), so only a verb call counts."""

    calls: ClassVar[list[tuple[Any, ...]]] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def __aenter__(self) -> _NoRequest:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def request(self, *a: Any, **_k: Any) -> Any:
        _NoRequest.calls.append(a)
        raise AssertionError("a shared room sent a request")

    get = post = patch = put = delete = request


@pytest.fixture
def no_request(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    _NoRequest.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _NoRequest)
    return _NoRequest.calls


@contextlib.contextmanager
def _room_run(user: str = ROOM) -> Iterator[None]:
    """One run bound to *user*: the memory id, and a VERIFIED run-context user
    with the same artifact member. A real room binds only the memory id. The
    run-context readers get the same value here, so each reader is tried."""
    with run_context_scope(), artifact_context_scope():
        bind_run_context(run_id="r-4017", user=user, member_verified=True)
        bind_artifact_context(session_id="s-4017", member=user,
                              agent_name="orchestrator", run_id="r-4017")
        binding = _bind_memory_user_id(user)
        try:
            yield
        finally:
            _unbind_memory_user_id(binding)


def _tools() -> list[Any]:
    from acb_skills import company_search_tools as cs
    from acb_skills import crm_tools as ct
    from acb_skills import integrations_admin_tools as ia
    from acb_skills import knowledge_tools as kt
    from acb_skills import org_admin_tools as oa
    from acb_skills import people_tools as pt
    from acb_skills import profile_tools as pf
    from acb_skills import studio_tools as st

    studio_refused = {"ok": False, "errors": [{"path": "$", "message": st._SHARED_ROOM}],
                      "diff": []}
    return [
        pytest.param(oa.list_members, (), {"ok": False, "error": oa._SHARED_ROOM},
                     id="org_admin.list_members"),
        pytest.param(ia.read_integrations, (), {"ok": False, "error": ia._SHARED_ROOM},
                     id="integrations_admin.read_integrations"),
        pytest.param(st.studio_template_list, (), studio_refused,
                     id="studio.studio_template_list"),
        pytest.param(st.studio_list_actions, (), studio_refused,
                     id="studio.studio_list_actions"),
        pytest.param(cs.search_company, ("acme",), cs.SHARED_ROOM,
                     id="company_search.search_company"),
        pytest.param(cs.resolve_name, ("Asha",), cs.NAMES_SHARED_ROOM,
                     id="company_search.resolve_name"),
        pytest.param(kt.list_spaces, (), kt.SHARED_ROOM, id="knowledge.list_spaces"),
        pytest.param(ct.crm_list_records, ("leads",), ct.CRM_SHARED_ROOM,
                     id="crm.crm_list_records"),
        pytest.param(pt.people_directory, (), {"ok": False, "error": pt._SHARED_ROOM},
                     id="people.people_directory"),
        pytest.param(pt.people_me, (), {"ok": False, "error": pt._SHARED_ROOM},
                     id="people.people_me"),
        pytest.param(pf.resolve_person, ("Asha",), pf.SHARED_ROOM,
                     id="profile.resolve_person"),
    ]


@pytest.mark.parametrize(("tool", "args", "refusal"), _tools())
async def test_each_converted_tool_refuses_a_room_and_sends_nothing(
    no_request: list[Any], tool: Any, args: tuple[Any, ...], refusal: Any,
) -> None:
    """Mutation: put back ``"@" not in user`` in the module's gate -> the
    ``room:team@x`` id passes -> a request goes out -> red."""
    with _room_run():
        out = await tool(*args)
    assert out == refusal
    assert no_request == []


async def test_a_profile_write_refuses_a_room_and_sends_nothing(no_request: list[Any]) -> None:
    from acb_skills import profile_tools as pf

    with _room_run():
        out = await pf.profile_forget("fact-1")
    assert out.endswith(pf.SHARED_ROOM_WRITE)
    assert '"reason":"not_your_turn"' in out
    assert no_request == []


async def test_the_profile_offer_claims_nothing_in_a_room(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import profile_tools as pf

    monkeypatch.setattr(pf, "_run_org", lambda: "org-1")
    monkeypatch.setattr(pf, "get_tenant_redis", _no_redis, raising=False)
    with _room_run():
        assert await pf._claim_offer() is False


def _no_redis() -> Any:
    raise AssertionError("a shared room reached Redis")


def test_the_studio_key_part_is_empty_in_a_room() -> None:
    from acb_skills import studio_tools as st

    with _room_run():
        assert st._member_part() == ""
    with _room_run(MEMBER):
        assert st._member_part() != ""


async def test_the_crm_late_answer_never_matches_in_a_room(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import crm_tools as ct

    monkeypatch.setattr(ct, "_run_org", lambda: "org-1")
    with _room_run():
        assert await ct._late_answer_matches("q-1", "yes") is False


def test_the_run_context_readers_name_no_member_for_a_room() -> None:
    from acb_skills import chat_pin_tools, decide_tools, home_tools

    with _room_run():
        assert chat_pin_tools._member() == ""
        assert home_tools._member() == ""
        assert decide_tools._proven_member() is None
    with _room_run(MEMBER):
        assert chat_pin_tools._member() == MEMBER
        assert home_tools._member() == MEMBER
        assert decide_tools._proven_member() == MEMBER


async def test_the_chat_constraints_read_and_undo_nothing_for_a_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_skills import chat_constraints as cc

    monkeypatch.setattr(cc, "chat_constraints_enabled", lambda: True)

    async def _no_read(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a shared room read the kept decisions")

    monkeypatch.setattr(cc, "_read_redis", _no_read)
    monkeypatch.setattr(cc, "_lock", _no_read)
    assert await cc.decisions_of("org-1", "s-1", ROOM) == []
    assert await cc.undo_decision("org-1", "s-1", ROOM, "d-1") == (cc.UNDO_UNAVAILABLE, None)


async def test_the_mailboxes_are_not_read_for_a_room(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import email_chat_context as ecc

    reads: list[str] = []

    async def _fetch(member: str) -> list[Any]:
        reads.append(member)
        return []

    monkeypatch.setattr(ecc, "fetch_accounts", _fetch)
    monkeypatch.setattr(ecc, "_MAILBOXES", collections.OrderedDict())
    await ecc.prime_mailboxes("org-1", ROOM)
    assert reads == []
    # Non-vacuity: a member's own turn reads the mailboxes.
    await ecc.prime_mailboxes("org-4017", MEMBER)
    assert reads == [MEMBER]


async def test_the_late_approve_gates_refuse_a_room(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import late_approve as la

    monkeypatch.setattr(la, "late_approve_on", lambda _org: True)
    monkeypatch.setattr(la, "_run_org", lambda: "org-1")
    monkeypatch.setattr(la, "_run_member", lambda: ROOM)
    monkeypatch.setattr(la, "_run_thread", lambda: "t-1")
    monkeypatch.setattr(la, "_redis", _no_redis)
    row = {"kind": "confirmation", "state": "parked", "actor_email": ROOM,
           "thread_id": "t-1", "request_id": "r-1"}
    assert await la.record_offer("org-1", "t-1", "r-1", row, 60) is False
    assert await la.drop_offer("org-1", ROOM, "t-1", "r-1") is None
    assert await la.grant_from_offer("org-1", ROOM, row, la.APPROVE) is False
    assert la._run_scope() is None


def test_the_task_edit_card_refuses_a_room_responder() -> None:
    from acb_skills.task_card import parse_task_edit_answer

    assert parse_task_edit_answer('{"title": "x"}', ROOM) is None


async def test_image_analysis_refuses_a_room_member(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_common.assistant_profile as ap
    import acb_llm.routed as routed
    from acb_skills import attachment_tools as at

    monkeypatch.setattr(routed, "routing_is_on", lambda: True)
    monkeypatch.setattr(ap, "image_analysis_for", lambda _org: True)
    monkeypatch.setattr(at, "_run_tenant", lambda: "org-1")
    monkeypatch.setattr(at, "_run_member", lambda: ROOM)

    async def _no_read(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a shared room read an attachment")

    monkeypatch.setattr(at, "read_attachment_bytes", _no_read)
    assert await at.analyze_image("a.png", "what is it?") == at.IMAGE_NO_MEMBER


def test_a_skill_marker_with_a_room_id_is_foreign(tmp_path: Path) -> None:
    from acb_skills import agent_paths as ap

    (tmp_path / "s").mkdir()
    (tmp_path / "s" / ap.SKILL_AUTHOR_MARKER).write_text(ROOM, encoding="utf-8")
    assert ap.skill_owner(tmp_path, "s", ROOM) == ap.SKILL_FOREIGN


# ── 3. The fence: the helper is the one place ───────────────────────────────

#: ``(module, function)`` -> why a bare ``"@" in`` test is right there. Each
#: one reads a value that is NOT the run's member.
ALLOWED: dict[tuple[str, str], str] = {
    ("ask_tools.py", "_reply_classes"): "a reply form's address keys, not the run identity",
    ("ask_tools.py", "_title_words"): "splits the words of a card title",
    ("held_card.py", "address_problem"): "a row's address of a held send, not the run identity",
}


def _at_tests() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SKILLS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SKILLS).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Compare)
                        and isinstance(node.left, ast.Constant) and node.left.value == "@"
                        and any(isinstance(op, ast.In | ast.NotIn) for op in node.ops)):
                    found.add((rel, fn.name))
        for node in tree.body:
            for sub in ast.walk(node):
                if (not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                        and isinstance(sub, ast.Compare)
                        and isinstance(sub.left, ast.Constant) and sub.left.value == "@"):
                    found.add((rel, "<module>"))
    return found


def test_no_module_tests_the_run_member_with_a_bare_at_sign() -> None:
    """A new ``"@" in member`` test reads ``room:team@x`` as a member. Use
    ``memory_tools.run_member_address``. A test of a value that is not the
    run's member goes in :data:`ALLOWED` with its reason."""
    extra = _at_tests() - set(ALLOWED)
    assert not extra, (
        f"a bare '\"@\" in' test outside memory_tools.run_member_address: {sorted(extra)}")


def test_the_allow_list_has_no_stale_entry() -> None:
    stale = set(ALLOWED) - _at_tests()
    assert not stale, f"remove the stale ALLOWED entries: {sorted(stale)}"
