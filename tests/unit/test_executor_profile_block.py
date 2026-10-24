"""The member's profile block in the per-turn context (WS-63 PZ-2).

Spec: ``project-docs/specs/personalization.md`` §5.1. R7 fences named here:

* ``profile-block-four-conditions``: the block reaches a run only with the
  flag on, the agent ``orchestrator``, a verified member, a solo room from
  the run boundary, and an org. A run that fails one reads nothing.
* ``profile-block-order``: the block sits after the name line and before
  the number-system line, and no other block moves.
* ``profile-block-solo-plumbing``: the web chat route passes
  ``solo_room=room_is_solo(room)``, so only a private room of one is solo.
  The WhatsApp run passes it, and a run that passes ``None`` keeps its
  parent's value. A detached run (a workflow agent node, a background
  sub-task, a task dispatch) passes ``False``.
* ``profile-block-render``: the §5.1 shape, the 6,000-character cap, and the
  order in which the cap sheds.

The R8 half (the read under FORCE RLS) is
``tests/unit/test_executor_profile_block_r8.py``.
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from acb_common import get_settings
from acb_common import number_system as ns
from orchestrator import executor as ex

# The WhatsApp world of the bot_run suite: the real route and run, a fake
# executor that records its keywords.
from tests.unit.test_wac_bot_run import world  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
ORG = "11111111-1111-4111-8111-111111111111"
MEMBER = "ana@alpha.example"

#: The example of §4.2, which §5.1 renders.
EXAMPLE: dict[str, Any] = {
    "v": 1,
    "learning": True,
    "address": {"name": "VJ", "formality": "casual", "greeting": "brief"},
    "style": {"length": "short", "language": "en-IN",
              "notes": ["Hinglish is fine on WhatsApp"]},
    "aliases": [{"id": "a1", "alias": "Raj", "email": "raj@fracktal.in"}],
    "facts": [{"id": "f1", "text": "Travels every Friday", "kind": "routine",
               "source": "chat", "at": "2026-10-23T11:00:00Z"}],
}

#: The block of §5.1, byte for byte.
GOLDEN = (
    "## About the member (their own profile — they can see and change it)\n"
    'Address them as "VJ". Tone: casual. Greeting: brief. In the first reply of a chat '
    "only, open with one short greeting for the time of day. Never greet again in that "
    "chat.\n"
    "Answer length: short. Use about half the usual length. Language: en-IN. Answer in "
    "it, unless they write in another language. Notes: Hinglish is fine on WhatsApp.\n"
    'People they name: "Raj" = raj@fracktal.in.\n'
    "What they asked you to remember:\n"
    "- [f1] Travels every Friday (routine, 2026-10-23)\n"
    "Where this conflicts with the house voice, follow the member.\n"
    "If they ask what you know about them, answer from this block only."
)


@pytest.fixture()
def run(monkeypatch: pytest.MonkeyPatch):
    """A solo orchestrator run with an org and a verified member, the flag
    on, and a recorded read of the profile."""
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    state: dict[str, Any] = {"org": ORG, "member": MEMBER, "raw": EXAMPLE,
                             "preferred": "", "zone": "", "display": "",
                             "reads": [], "fail": False}

    def read(org: str, member: str) -> Any:
        state["reads"].append((org, member))
        if state["fail"]:
            raise RuntimeError("the database is down")
        return (state["raw"], state["preferred"], state["zone"], state["display"])

    monkeypatch.setattr(ex, "_current_run_org", lambda: state["org"])
    monkeypatch.setattr(ex, "_verified_run_member", lambda: state["member"])
    monkeypatch.setattr(ex, "_read_member_profile", read)
    monkeypatch.setattr(ex, "_read_member_display_name", lambda org, m: "Ana Rao")
    monkeypatch.setattr(ex, "_read_number_system", lambda org, m: "indian")
    monkeypatch.setattr(ex, "_run_org_display_name", lambda: "Acme Tools")
    ex._PROFILE_CACHE.clear()
    ex._MEMBER_NAME_CACHE.clear()
    ns.forget()
    token = ex._RUN_SOLO_ROOM.set(True)
    try:
        yield state
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
        ex._PROFILE_CACHE.clear()
        ex._MEMBER_NAME_CACHE.clear()
        ns.forget()


def _parts(agent: str = "orchestrator", payload: dict[str, Any] | None = None) -> list[str]:
    return ex._context_preamble_parts(payload or {}, {}, agent)


def _blocks(parts: list[str]) -> list[str]:
    return [p for p in parts if p.startswith(ap.BLOCK_HEADER)]


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", on, raising=False)


# ── profile-block-four-conditions ──────────────────────────────────────────


def test_a_solo_orchestrator_run_gets_the_block_after_the_name_line(run) -> None:
    parts = _parts()
    assert _blocks(parts) == [GOLDEN]
    at = parts.index(GOLDEN)
    assert parts[at - 1] == ex.MEMBER_NAME_LINE.format(name="Ana Rao")
    assert parts[at + 1] == ns.PROMPT_LINES["indian"]
    assert run["reads"] == [(ORG, MEMBER)]


def test_the_flag_off_gives_no_block_and_no_read(run, monkeypatch) -> None:
    _flag(monkeypatch, False)
    assert _blocks(_parts()) == []
    assert run["reads"] == []


def test_a_flag_that_is_not_true_is_off(run, monkeypatch) -> None:
    _flag(monkeypatch, "yes")
    assert _blocks(_parts()) == []
    assert run["reads"] == []


@pytest.mark.parametrize("agent", ["email-assistant", "projects-assistant", "", "Orchestrator"])
def test_another_agent_gets_no_block_and_no_read(run, agent: str) -> None:
    assert _blocks(_parts(agent)) == []
    assert run["reads"] == []


def test_a_run_with_no_verified_member_gets_no_block_and_no_read(run) -> None:
    run["member"] = ""
    assert _blocks(_parts()) == []
    assert run["reads"] == []


def test_a_shared_room_gets_no_block_and_no_read(run) -> None:
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        assert _blocks(_parts()) == []
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
    assert run["reads"] == []


def test_a_run_that_never_set_the_room_bit_gets_no_block(run) -> None:
    """The default is False: a webhook, a workflow or a Copilot run that
    passes nothing fails closed."""
    import contextvars

    seen = contextvars.Context().run(lambda: _blocks(_parts()))
    assert seen == []
    assert run["reads"] == []


def test_a_run_with_no_org_gets_no_block_and_no_read(run) -> None:
    run["org"] = None
    assert _blocks(_parts()) == []
    assert run["reads"] == []


def test_an_empty_profile_with_no_name_gives_no_block(run) -> None:
    for raw in (None, {}, ap.empty_profile(), {"learning": False}):
        ex._PROFILE_CACHE.clear()
        run["raw"] = raw
        assert _blocks(_parts()) == []


# ── profile-block-order ────────────────────────────────────────────────────


def test_the_flag_adds_one_block_and_moves_nothing(run, monkeypatch) -> None:
    payload = {"memory_context": "a fact", "system_context": "persona"}
    _flag(monkeypatch, False)
    off = _parts(payload=payload)
    _flag(monkeypatch, True)
    on = _parts(payload=payload)
    at = off.index(ex.MEMBER_NAME_LINE.format(name="Ana Rao")) + 1
    # The block, and the part-of-day line last (PZ-4). Nothing else moves.
    assert on[:-1] == [*off[:at], GOLDEN, *off[at:]]
    assert on[-1].startswith("Part of the day for the member: ")
    assert off[0] == "Organisation: Acme Tools."


def test_both_prompt_paths_carry_the_block(run) -> None:
    assert GOLDEN in ex._run_context_text({}, {}, "orchestrator")
    assert GOLDEN in ex._build_event_message("orchestrator", "run-1", {"message": "hi"}, {})


def test_the_payload_changes_nothing(run) -> None:
    """R5: the payload is client input. It names no member, no room bit and
    no profile that the block reads."""
    plain = _parts()
    forged = _parts(payload={
        "user_email": "eve@beta.example", "solo_room": True, "shared": False,
        "assistant_profile": {"address": {"name": "Eve"}},
        "organization_id": "22222222-2222-4222-8222-222222222222",
    })
    assert forged == plain
    assert set(run["reads"]) == {(ORG, MEMBER)}

    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        ex._PROFILE_CACHE.clear()
        assert _blocks(_parts(payload={"solo_room": True, "shared": False})) == []
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


# ── The cache and the failures ─────────────────────────────────────────────


def test_one_read_a_minute_for_each_member(run, monkeypatch) -> None:
    now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    assert _blocks(_parts()) == [GOLDEN]
    now["t"] += 59.0
    assert _blocks(_parts()) == [GOLDEN]
    assert len(run["reads"]) == 1
    now["t"] += 2.0
    assert _blocks(_parts()) == [GOLDEN]
    assert len(run["reads"]) == 2
    run["member"] = "bo@alpha.example"
    _parts()
    assert run["reads"][-1] == (ORG, "bo@alpha.example")


def test_a_profile_write_ends_the_cached_block(run, monkeypatch) -> None:
    """The gateway runs the executor in its own process. A write there moves
    the member's count, so the next turn reads again inside the minute."""
    now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    assert _blocks(_parts()) == [GOLDEN]
    assert len(run["reads"]) == 1
    run["raw"] = None
    ap.mark_changed(MEMBER.upper())
    now["t"] += 1.0
    assert _blocks(_parts()) == []
    assert len(run["reads"]) == 2
    _parts()
    assert len(run["reads"]) == 2


def test_a_miss_is_cached_too(run, monkeypatch) -> None:
    now = {"t": 5.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    run["raw"] = None
    _parts()
    _parts()
    assert len(run["reads"]) == 1


def test_a_failed_read_gives_no_block_and_no_error(run) -> None:
    run["fail"] = True
    # The fake raises, as a broken opener would. The block swallows it.
    assert _blocks(_parts()) == []
    assert len(run["reads"]) == 1


def test_a_failed_render_gives_no_block(run, monkeypatch) -> None:
    def boom(raw: Any) -> str:
        raise ValueError("bad shape")

    monkeypatch.setattr(ap, "render_block", boom)
    assert _blocks(_parts()) == []


def test_the_real_read_never_raises(monkeypatch) -> None:
    class _Broken:
        def __enter__(self):
            raise RuntimeError("no connection")

        def __exit__(self, *a: Any) -> bool:
            return False

    monkeypatch.setattr(ex, "_opener_for_org", lambda org: _Broken)
    assert ex._read_member_profile(ORG, MEMBER) is None
    monkeypatch.setattr(ex, "_opener_for_org", lambda org: None)
    assert ex._read_member_profile(ORG, MEMBER) is None


def test_the_read_binds_the_run_org_and_member(monkeypatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a: Any) -> bool:
            return False

        def execute(self, stmt: Any, params: dict[str, Any]):
            seen.append((str(stmt), params))
            row = ({"v": 1},) if len(seen) == 1 else ("VJ R", None, "Vijay Varada")
            return type("R", (), {"first": lambda self: row})()

    monkeypatch.setattr(ex, "_opener_for_org", lambda org: _Session)
    assert ex._read_member_profile(ORG, MEMBER) == ({"v": 1}, "VJ R", "", "Vijay Varada")
    ((sql, params), (address_sql, address_params)) = seen
    assert sql == ap.READ_SQL
    assert params == {"org": ORG, "m": MEMBER}
    assert "CAST(:org AS uuid)" in sql and "lower(user_id) = lower(:m)" in sql
    assert "::" not in sql
    assert address_sql == ap.ADDRESS_SQL
    assert address_params == {"org": ORG, "m": MEMBER}
    assert "{org}" not in address_sql and "::" not in address_sql


# ── profile-block-solo-plumbing ────────────────────────────────────────────


def _calls(path: Path, func: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name == func:
                out.append(node)
    return out


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_the_web_chat_route_passes_the_room_bit() -> None:
    path = REPO / "apps/services/gateway/gateway/routes/agent.py"
    (call,) = _calls(path, "run_agent_stream")
    value = _kw(call, "solo_room")
    assert value is not None, "the web chat run gives no solo_room"
    assert ast.unparse(value) == "room_is_solo(room)"
    src = path.read_text(encoding="utf-8")
    assert "from gateway.rooms import room_is_solo" in src


def _room(members: list[str], visibility: Any) -> Any:
    from gateway.rooms import RoomAccess

    return RoomAccess(
        session_id="s1", email=MEMBER, role="owner", can_read=True,
        can_send=True, can_cancel=True, can_invite=True, can_manage=True,
        is_shared=len(members) > 1, members=members, visibility=visibility,
    )


@pytest.mark.parametrize(("members", "visibility", "solo"), [
    ([MEMBER], "private", True),
    ([MEMBER, "bo@alpha.example"], "private", False),
    ([MEMBER], "org", False),
    ([MEMBER], "people", False),
    ([MEMBER, "bo@alpha.example"], "org", False),
    ([MEMBER], None, True),
    ([MEMBER], "", True),
    # Repair round 2 (P1): the one member must be the actor, and a group or
    # the org is never one person.
    (["group:eng"], "private", False),
    (["org"], "private", False),
    (["bo@alpha.example"], "private", False),
    ([], "private", False),
    ([MEMBER.upper()], "private", True),
])
def test_only_a_private_room_of_one_is_solo(members, visibility, solo) -> None:
    """§5.1, §7 (D-PZ-3): a room of one that other members can read is not
    solo. An empty visibility counts as private."""
    from gateway.rooms import room_is_solo

    assert room_is_solo(_room(members, visibility)) is solo


def test_no_room_is_solo() -> None:
    from gateway.rooms import room_is_solo

    assert room_is_solo(None) is True


def test_the_whatsapp_run_passes_the_room_bit() -> None:
    path = REPO / "apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py"
    calls = [c for c in _calls(path, "wait_for")
             if c.args and isinstance(c.args[0], ast.Call)
             and isinstance(c.args[0].func, ast.Call)
             and ast.unparse(c.args[0].func) == "_executor()"]
    (wait,) = calls
    run_call = wait.args[0]
    value = _kw(run_call, "solo_room")
    assert value is not None, "the WhatsApp run gives no solo_room"
    assert ast.unparse(value) == "not shared"


def test_none_keeps_the_parent_value_and_a_value_is_released() -> None:
    import contextvars

    def body() -> list[bool]:
        seen = [ex._RUN_SOLO_ROOM.get()]
        outer = ex._bind_solo_room(True)
        inner = ex._bind_solo_room(None)
        seen.append(ex._RUN_SOLO_ROOM.get())
        ex._release_solo_room(inner)
        narrow = ex._bind_solo_room(False)
        seen.append(ex._RUN_SOLO_ROOM.get())
        ex._release_solo_room(narrow)
        seen.append(ex._RUN_SOLO_ROOM.get())
        ex._release_solo_room(outer)
        seen.append(ex._RUN_SOLO_ROOM.get())
        return seen

    assert contextvars.Context().run(body) == [False, True, False, True, False]


def test_a_reset_in_a_foreign_context_fails_closed() -> None:
    import contextvars

    token = contextvars.Context().run(lambda: ex._bind_solo_room(True))

    def body() -> bool:
        ex._RUN_SOLO_ROOM.set(True)
        ex._release_solo_room(token)
        return ex._RUN_SOLO_ROOM.get()

    assert contextvars.Context().run(body) is False


def test_the_batch_run_binds_and_a_nested_run_inherits(monkeypatch) -> None:
    import contextvars

    seen: list[tuple[str, bool]] = []

    async def inner(agent_name: str, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
        seen.append((agent_name, ex._RUN_SOLO_ROOM.get()))
        if agent_name == "parent":
            await ex.run_agent("child", {})
            await ex.run_agent("narrow", {}, solo_room=False)
            seen.append(("parent-after", ex._RUN_SOLO_ROOM.get()))
        return {}

    monkeypatch.setattr(ex, "_run_agent_inner", inner)

    async def go() -> bool:
        await ex.run_agent("parent", {}, solo_room=True)
        return ex._RUN_SOLO_ROOM.get()

    after = contextvars.Context().run(asyncio.run, go())
    assert seen == [("parent", True), ("child", True), ("narrow", False),
                    ("parent-after", True)]
    assert after is False


def test_the_stream_run_binds_and_a_nested_run_inherits(monkeypatch) -> None:
    import contextvars

    seen: list[bool] = []

    def load(*a: Any, **k: Any):
        seen.append(ex._RUN_SOLO_ROOM.get())
        raise RuntimeError("stop after the bind")

    monkeypatch.setattr(ex, "load_agent", load)
    monkeypatch.setattr(ex, "record", lambda *a, **k: None)

    async def drain(**kw: Any) -> None:
        async for _ in ex.run_agent_stream("profile-probe", {}, **kw):
            pass

    async def go() -> list[bool]:
        after: list[bool] = []
        await drain()
        await drain(solo_room=True)
        after.append(ex._RUN_SOLO_ROOM.get())
        token = ex._bind_solo_room(True)
        await drain(solo_room=None)
        await drain(solo_room=False)
        after.append(ex._RUN_SOLO_ROOM.get())
        ex._release_solo_room(token)
        return after

    after = contextvars.Context().run(asyncio.run, go())
    assert seen == [False, True, True, False]
    assert after == [False, True]


def _inner_records_solo(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Stub the batch run's body. The REAL ``run_agent`` binds the bit, and
    the stub records what the run sees."""
    seen: list[tuple[str, bool]] = []

    async def inner(agent_name: str, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
        seen.append((agent_name, ex._RUN_SOLO_ROOM.get()))
        return {"result": "ok"}

    monkeypatch.setattr(ex, "_run_agent_inner", inner)
    return seen


def _in_a_solo_chat(coro_fn: Any) -> Any:
    """Run *coro_fn* in a fresh context whose parent run is solo."""
    import contextvars

    async def go() -> Any:
        ex._RUN_SOLO_ROOM.set(True)
        return await coro_fn()

    return contextvars.Context().run(asyncio.run, go())


def test_a_workflow_agent_node_started_from_a_solo_chat_is_not_solo(monkeypatch) -> None:
    """A workflow copies the context that started it. Its output is not the
    member's solo chat, so its agent node passes ``solo_room=False``."""
    from gateway.routes.workflows import service

    seen = _inner_records_solo(monkeypatch)

    async def member(workflow_id: str) -> str:
        return MEMBER

    monkeypatch.setattr(service, "_workflow_billing_member", member)
    out = _in_a_solo_chat(lambda: service._agent_runner("wf")("orchestrator", "m", None))
    assert out == "ok"
    assert seen == [("orchestrator", False)]


def test_a_background_sub_task_of_a_solo_chat_is_not_solo(monkeypatch) -> None:
    """``asyncio.create_task`` copies the solo bit. The background sub-task
    answers into the inbox, so it passes ``solo_room=False``."""
    from acb_skills import agent_tools

    seen = _inner_records_solo(monkeypatch)

    async def unmarked() -> bool:
        return False

    monkeypatch.setattr(agent_tools, "_session_marked", unmarked)

    async def go() -> str:
        out = await agent_tools.call_agent_background("orchestrator", "do it")
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*others)
        assert ex._RUN_SOLO_ROOM.get() is True
        return out

    out = _in_a_solo_chat(go)
    assert "Dispatched sub-task" in out
    assert seen == [("orchestrator", False)]


def test_a_task_dispatch_from_a_solo_context_is_not_solo(monkeypatch) -> None:
    """An assigned agent replies on the task timeline, which other members
    read. The dispatch passes ``solo_room=False``."""
    from gateway.routes.projects import agent_dispatch

    seen = _inner_records_solo(monkeypatch)

    async def record(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(agent_dispatch, "_record_outcome", record)
    _in_a_solo_chat(lambda: agent_dispatch._run_and_record("orchestrator", "do it", "t1", ORG))
    assert seen == [("orchestrator", False)]


# ── profile-block-render ───────────────────────────────────────────────────


def test_the_render_of_the_spec_example_is_golden() -> None:
    assert ap.render_block(EXAMPLE) == GOLDEN
    assert ap.render_block(EXAMPLE) == ap.render_block(dict(EXAMPLE))
    import json

    assert ap.render_block(json.dumps(EXAMPLE)) == GOLDEN


def test_learning_off_still_renders() -> None:
    assert ap.render_block({**EXAMPLE, "learning": False}) == GOLDEN


def test_white_space_collapses_in_every_value() -> None:
    raw = {
        "address": {"name": "V\u2028J"},
        "style": {"notes": ["two   spaces\u2029here"]},
        "aliases": [{"id": "a1", "alias": "Big\u00a0Raj", "email": "raj@fracktal.in"}],
        "facts": [{"id": "f 1", "text": "one\u2028two   three", "kind": "about"}],
    }
    block = ap.render_block(raw)
    assert 'Address them as "V J".' in block
    assert "Notes: two spaces here." in block
    assert '"Big Raj" = raj@fracktal.in' in block
    assert "- [f 1] one two three (about)" in block
    for line in block.split("\n"):
        assert "  " not in line
    # Seven breaks: a note is a style key, so the house-voice line shows (PZ-3).
    assert block.count("\n") == 7


def _fact(n: int, at: str) -> dict[str, Any]:
    return {"id": f"f{n}", "text": f"{n:02d} " + "x" * 290, "kind": "work",
            "source": "chat", "at": at}


def test_over_the_cap_the_oldest_facts_go_first() -> None:
    # Position and age disagree: the newest fact is first.
    facts = [_fact(n, f"2026-{12 - n // 4:02d}-{28 - n % 4:02d}T00:00:00Z")
             for n in range(ap.FACTS_MAX)]
    raw = {**EXAMPLE, "facts": facts}
    block = ap.render_block(raw)
    assert len(block) <= ap.RENDER_MAX
    lines = block.split("\n")
    assert lines[:4] == GOLDEN.split("\n")[:4]
    assert lines[-1] == ap.BLOCK_FOOTER
    kept = [ln.split("]")[0][4:] for ln in lines if ln.startswith("- [")]
    assert 0 < len(kept) < ap.FACTS_MAX
    # The newest is first in the list, so the kept facts are the first ones,
    # in their stored order, and the oldest went.
    assert kept == [str(n) for n in range(len(kept))]
    assert ap.render_block(raw) == block


def test_an_undated_fact_goes_before_a_dated_one_and_ties_go_by_position() -> None:
    facts = [_fact(n, "2026-10-01T00:00:00Z") for n in range(ap.FACTS_MAX)]
    facts[30] = {k: v for k, v in facts[30].items() if k != "at"}
    block = ap.render_block({"facts": facts})
    ids = [ln.split("]")[0][4:] for ln in block.split("\n") if ln.startswith("- [")]
    assert "30" not in ids and "0" not in ids
    assert ids == [str(n) for n in range(ap.FACTS_MAX) if n != 30][-len(ids):]


def test_aliases_go_from_the_end_only_after_every_fact() -> None:
    aliases = [{"id": f"a{n}", "alias": f"Name{n:02d}" + "y" * 50,
                "email": f"person{n:02d}." + "z" * 200 + "@fracktal.in"}
               for n in range(ap.ALIASES_MAX)]
    raw = {**EXAMPLE, "aliases": aliases}
    block = ap.render_block(raw)
    assert len(block) <= ap.RENDER_MAX
    assert ap.FACTS_HEADER not in block, "a fact stayed while an alias went"
    line = next(ln for ln in block.split("\n") if ln.startswith("People they name"))
    kept = line.count(" = ")
    assert 0 < kept < ap.ALIASES_MAX
    assert f'"Name00{"y" * 50}"' in line
    assert f'"Name{kept - 1:02d}' in line and f'"Name{kept:02d}' not in line
    assert block.split("\n")[1:3] == GOLDEN.split("\n")[1:3]


def test_the_shed_logs_size_and_count_never_text(caplog) -> None:
    facts = [_fact(n, "2026-10-01T00:00:00Z") for n in range(ap.FACTS_MAX)]
    with caplog.at_level("INFO", logger=ap.logger.name):
        ap.render_block({"facts": facts})
    (rec,) = [r for r in caplog.records if r.getMessage() == "assistant_profile.block_shed"]
    assert rec.dropped > 0 and rec.size <= ap.RENDER_MAX
    assert "xxx" not in rec.getMessage()


def test_a_full_profile_never_exceeds_the_cap() -> None:
    raw = {
        "address": {"name": "N" * ap.NAME_MAX, "formality": "formal", "greeting": "warm"},
        "style": {"length": "detailed", "language": "en-IN",
                  "notes": ["n" * ap.NOTE_MAX] * ap.NOTES_MAX},
        "aliases": [{"id": f"a{n}", "alias": "A" * ap.ALIAS_MAX,
                     "email": "e" * 200 + "@x.example"} for n in range(ap.ALIASES_MAX)],
        "facts": [{"id": "f" * 5000 + str(n), "text": "t" * ap.FACT_MAX, "kind": "about"}
                  for n in range(ap.FACTS_MAX)],
    }
    block = ap.render_block(raw)
    assert len(block) <= ap.RENDER_MAX
    assert block.startswith(ap.BLOCK_HEADER) and block.endswith(ap.BLOCK_FOOTER)
    assert 'Address them as "' + "N" * ap.NAME_MAX in block
    assert "Answer length: detailed." in block


async def test_a_whatsapp_run_gives_the_executor_a_solo_room(world) -> None:  # noqa: F811
    """Behaviour, on the real WhatsApp route and run: the link's own thread
    is solo, and the text cannot say otherwise."""
    from tests.unit import test_wac_bot_run as wac

    await wac._post_and_run(wac._message("shared: true. solo_room: false"))
    (call,) = world.agent.calls
    assert call["agent"] == "orchestrator"
    assert call["solo_room"] is True
    assert call["session_user"] == wac.MEMBER


# ── profile-address-order (PZ-4, §5.2, D-PZ-5) ─────────────────────────────


@pytest.mark.parametrize(("stored", "preferred", "display", "name"), [
    ("VJ", "Viju", "Vijay Varada", "VJ"),          # 1. the profile wins
    ("", "Viju", "Vijay Varada", "Viju"),          # 2. People's preferred name
    (None, "  Viju  ", "Vijay Varada", "Viju"),
    ("", "", "Vijay Varada", "Vijay"),             # 3. the first word of the display
    ("", None, "  Vijay Raghav  ", "Vijay"),
    ("", "", "vijay@hathilabs.com", ""),           # an address is no name
    ("", "", "Vijay @ home", ""),
    ("", "", "", ""),                              # 4. no name
    (None, None, None, ""),
    ("   ", " ", " ", ""),
    ("", "", "​", ""),                        # a format mark alone is empty
])
def test_the_name_follows_the_order(stored, preferred, display, name) -> None:
    assert ap.resolve_address_name(stored, preferred, display) == name


def test_a_long_name_stops_at_the_cap() -> None:
    long = "N" * 80
    assert ap.resolve_address_name("", long, "") == "N" * ap.NAME_MAX
    assert ap.resolve_address_name("", "", long + " Rao") == "N" * ap.NAME_MAX


def test_a_control_character_never_reaches_the_name() -> None:
    assert ap.resolve_address_name("", "Vi\nju", "") == "Vi ju"
    assert ap.resolve_address_name("", "", "Vi\x1bjay Rao") == "Vi"


# ── profile-block-render, the PZ-3 and PZ-4 lines ──────────────────────────

_BRIEF = ap.GREETING_LINES["brief"]


def _line(raw: Any, fallback: str = "", at: int = 1) -> str:
    return ap.render_block(raw, fallback).split("\n")[at]


def test_an_empty_profile_with_a_name_gives_the_address_line_only() -> None:
    block = ap.render_block(None, "Vijay")
    assert block == "\n".join([
        ap.BLOCK_HEADER, f'Address them as "Vijay". {_BRIEF}', ap.BLOCK_FOOTER])
    assert ap.render_block({}, "Vijay") == block
    assert ap.render_block(None, "") == ""
    assert ap.render_block(ap.empty_profile()) == ""


def test_the_stored_name_wins_over_the_fallback() -> None:
    assert _line({"address": {"name": "VJ"}}, "Vijay") == f'Address them as "VJ". {_BRIEF}'


def test_no_name_forbids_a_placeholder() -> None:
    line = _line({"style": {"length": "short"}})
    assert line == f"{ap.NO_NAME_LINE} {_BRIEF}"
    assert line.startswith("Do not address them by name. Never write a placeholder.")
    assert "[" not in line


@pytest.mark.parametrize(("greeting", "text"), [
    ("none", "Greeting: none. Open with the answer."),
    ("brief", "Greeting: brief. In the first reply of a chat only, open with one short "
              "greeting for the time of day. Never greet again in that chat."),
    ("warm", "Greeting: warm. In the first reply of a chat only, open with a greeting and "
             "one personal line from a fact in this block. Do this at most once a day."),
    (None, "Greeting: brief. In the first reply of a chat only, open with one short "
           "greeting for the time of day. Never greet again in that chat."),
])
def test_each_greeting_line_is_golden(greeting, text) -> None:
    address = {"name": "VJ"} if greeting is None else {"name": "VJ", "greeting": greeting}
    assert _line({"address": address}) == f'Address them as "VJ". {text}'
    assert ap.effective_greeting({"address": address}) == (greeting or "brief")


@pytest.mark.parametrize(("length", "text"), [
    ("short", "Answer length: short. Use about half the usual length."),
    ("default", "Answer length: default."),
    ("detailed", "Answer length: detailed. Give fuller answers than usual. On WhatsApp "
                 "keep the channel limits."),
])
def test_each_length_line_is_golden(length, text) -> None:
    block = ap.render_block({"address": {"name": "VJ"}, "style": {"length": length}})
    assert block.split("\n")[2] == text
    assert block.split("\n")[-2] == ap.HOUSE_VOICE_LINE


def test_the_language_line_lets_the_member_switch() -> None:
    assert _line({"style": {"language": "hi"}}, "VJ", at=2) == (
        "Language: hi. Answer in it, unless they write in another language.")


def test_the_house_voice_line_needs_a_style_key_or_a_formality() -> None:
    assert ap.HOUSE_VOICE_LINE not in ap.render_block({"address": {"name": "VJ"}})
    assert ap.HOUSE_VOICE_LINE not in ap.render_block(
        {"address": {"name": "VJ", "greeting": "none"},
         "facts": [{"id": "f1", "text": "x", "kind": "about"}]})
    for raw in ({"address": {"formality": "formal"}},
                {"style": {"notes": ["No emoji"]}},
                {"style": {"language": "en-IN"}}):
        lines = ap.render_block(raw).split("\n")
        assert lines[-2:] == [ap.HOUSE_VOICE_LINE, ap.BLOCK_FOOTER]


def test_the_block_is_byte_stable_with_a_fallback() -> None:
    assert ap.render_block(EXAMPLE, "Vijay") == ap.render_block(dict(EXAMPLE), "Vijay")
    assert ap.render_block(EXAMPLE, "Vijay") == GOLDEN


def test_the_cap_keeps_the_new_lines() -> None:
    facts = [_fact(n, "2026-10-01T00:00:00Z") for n in range(ap.FACTS_MAX)]
    block = ap.render_block({"style": {"length": "detailed"}, "facts": facts}, "Vijay")
    lines = block.split("\n")
    assert len(block) <= ap.RENDER_MAX
    assert lines[1] == f'Address them as "Vijay". {_BRIEF}'
    assert lines[2] == ap.LENGTH_LINES["detailed"]
    assert lines[-2:] == [ap.HOUSE_VOICE_LINE, ap.BLOCK_FOOTER]


def test_each_new_sentence_keeps_to_25_words() -> None:
    import re

    texts = [*ap.GREETING_LINES.values(), *ap.LENGTH_LINES.values(),
             ap.HOUSE_VOICE_LINE, ap.NO_NAME_LINE]
    for text in texts:
        for sentence in re.split(r"(?<=\.)\s+", text):
            assert len(sentence.split()) <= 25, sentence


# ── The part of the day (PZ-4) ─────────────────────────────────────────────


@pytest.mark.parametrize(("hour", "part"), [
    (4, "night"), (5, "morning"), (11, "morning"), (12, "afternoon"), (16, "afternoon"),
    (17, "evening"), (20, "evening"), (21, "night"), (0, "night"),
])
def test_the_part_of_day_bands(hour, part) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from acb_common.today import part_of_day_line

    now = datetime(2026, 10, 24, hour, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert part_of_day_line("Asia/Kolkata", now) == f"Part of the day for the member: {part}."


def test_the_part_of_day_reads_the_member_zone_and_a_bad_zone_falls_back() -> None:
    from datetime import UTC, datetime

    from acb_common.today import part_of_day

    now = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)       # 08:30 in Kolkata
    assert part_of_day("America/New_York", now) == "night"   # 23:00
    assert part_of_day("Asia/Kolkata", now) == "morning"
    assert part_of_day("Not/AZone", now) == "morning"
    assert part_of_day(None, now) == "morning"
    assert part_of_day("", datetime(2026, 10, 24, 3, 0)) == "morning"  # naive is UTC


# ── The executor and the order (PZ-4) ──────────────────────────────────────


def _address(block: str) -> str:
    return block.split("\n")[1]


@pytest.mark.parametrize(("stored", "preferred", "display", "first"), [
    ({"address": {"name": "VJ"}}, "Viju", "Vijay Varada", 'Address them as "VJ".'),
    ({"style": {"length": "short"}}, "Viju", "Vijay Varada", 'Address them as "Viju".'),
    ({"style": {"length": "short"}}, "", "Vijay Varada", 'Address them as "Vijay".'),
    ({"style": {"length": "short"}}, "", "vijay@x.example", ap.NO_NAME_LINE),
    ({"style": {"length": "short"}}, "", "", ap.NO_NAME_LINE),
    (None, "", "Vijay Varada", 'Address them as "Vijay".'),
])
def test_the_block_follows_each_step_of_the_order(run, stored, preferred, display,
                                                   first) -> None:
    run.update(raw=stored, preferred=preferred, display=display)
    (block,) = _blocks(_parts())
    assert _address(block).startswith(first)


def test_an_empty_profile_and_no_name_reads_once_and_adds_nothing(run, monkeypatch) -> None:
    run.update(raw=None, preferred="", display="")
    _flag(monkeypatch, False)
    off = _parts()
    _flag(monkeypatch, True)
    assert _parts() == off
    assert len(run["reads"]) == 1


def test_the_signing_line_keeps_the_full_display_name(run) -> None:
    run.update(raw=None, display="Ana Rao")
    parts = _parts()
    (block,) = _blocks(parts)
    assert _address(block).startswith('Address them as "Ana".')
    assert ex.MEMBER_NAME_LINE.format(name="Ana Rao") in parts
    at = parts.index(block)
    assert parts[at - 1] == ex.MEMBER_NAME_LINE.format(name="Ana Rao")
    assert parts[at + 1] == ns.PROMPT_LINES["indian"]


@pytest.mark.parametrize("case", ["shared", "no-member", "flag-off"])
def test_no_block_no_read_and_no_part_of_day(run, monkeypatch, case) -> None:
    run.update(raw=None, display="Ana Rao")
    token = ex._RUN_SOLO_ROOM.set(case != "shared")
    try:
        if case == "no-member":
            run["member"] = ""
        if case == "flag-off":
            _flag(monkeypatch, False)
        parts = _parts()
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
    assert _blocks(parts) == []
    assert not any(p.startswith("Part of the day") for p in parts)
    assert run["reads"] == []


def test_the_part_of_day_line_follows_today_only_when_the_block_greets(
    run, monkeypatch,
) -> None:
    from acb_common import today

    seen: list[Any] = []
    monkeypatch.setattr(today, "part_of_day_line",
                        lambda zone, now=None: seen.append(zone) or "Part of the day: X.")
    run.update(zone="Europe/Berlin")
    parts = _parts()
    assert parts[-1] == "Part of the day: X."
    assert parts[-2].startswith("Today is ")
    assert seen == ["Europe/Berlin"]

    for raw in ({"address": {"name": "VJ", "greeting": "none"}},):
        ex._PROFILE_CACHE.clear()
        run["raw"] = raw
        parts = _parts()
        assert _blocks(parts) and parts[-1].startswith("Today is ")
    ex._PROFILE_CACHE.clear()
    run.update(raw={"address": {"name": "VJ", "greeting": "warm"}}, zone="")
    assert _parts()[-1] == "Part of the day: X."
    assert seen[-1] is None


def test_the_cached_entry_keeps_the_zone_and_the_greeting(run, monkeypatch) -> None:
    now = {"t": 10.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    run.update(zone="Europe/Berlin", raw={"address": {"greeting": "none"}}, display="Ana")
    first = ex._member_profile_entry("orchestrator")
    assert first[1:] == ("Europe/Berlin", "none")
    run.update(raw=EXAMPLE, zone="UTC")
    now["t"] += 30.0
    assert ex._member_profile_entry("orchestrator") == first
    assert len(run["reads"]) == 1


def test_a_preferred_name_edit_shows_after_the_ttl(run, monkeypatch) -> None:
    now = {"t": 10.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    run.update(raw=None, preferred="Viju", display="Vijay Varada")
    assert _address(_blocks(_parts())[0]).startswith('Address them as "Viju".')
    run["preferred"] = "VJ"
    now["t"] += 59.0
    assert _address(_blocks(_parts())[0]).startswith('Address them as "Viju".')
    now["t"] += 2.0
    assert _address(_blocks(_parts())[0]).startswith('Address them as "VJ".')


def test_a_failed_read_gives_no_part_of_day(run) -> None:
    run["fail"] = True
    parts = _parts()
    assert _blocks(parts) == []
    assert parts[-1].startswith("Today is ")


def test_the_address_sql_names_the_org_on_both_sides() -> None:
    sql = ap.ADDRESS_SQL
    assert "p.organization_id = u.organization_id" in sql
    assert "u.organization_id = CAST(:org AS uuid)" in sql
    assert "lower(u.email) = lower(:m)" in sql
    assert "::" not in sql and "{org}" not in sql
    assert ap.ADDRESS_SQL_TEMPLATE.count("{org}") == 1
