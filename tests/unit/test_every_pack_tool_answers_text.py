"""HRD-3 (task 2982): each pack tool answers a failure as text
(``chat_failure_modes.md`` §4.2 B4, §7 rule 3).

``refusals_as_text`` covered a ``GatewayRefusal`` of Projects, CRM and email.
Any other exception reached MAF, and the model read "Error: Function failed."
The ONE guard is ``acb_skills.pack_tool_guard.guard``, built into
``_pack_call``. Run:
``uv run pytest tests/unit/test_every_pack_tool_answers_text.py -q``.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.tool_refusals import GatewayRefusal, RefusalVoice, is_refusal, refusals_as_text
from acb_skills import pack_tool_guard as ptg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from orchestrator import _tool_injection as ti

from tests.unit.test_pack_guards import _build, _run, _stub

_READ = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}
_WRITE = {"read_only": False, "destructive": False, "idempotent": False, "open_world": False}
SECRET = "jane.secret@acme.test"


@pytest.fixture(autouse=True)
def _agent_permission_off(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")


def _owner_tools() -> list[tuple[str, str, Any]]:
    out = []
    for slug in sf.pack_slugs():
        owner = tp.pack(slug).owner
        module = ti._pack_owner_module(owner)
        assert module is not None, f"the owner {owner} of {slug} did not load"
        out += [(slug, owner, fn) for fn in ti._owner_pack_tools(slug, owner, module)
                if inspect.iscoroutinefunction(ti._pack_func(fn))]
    return out


def test_each_pack_tool_is_wrapped_by_the_guard() -> None:
    """All real tools of all packs, built by the one seam ``_pack_call``.
    Mutation: drop the ``guard`` call in ``_pack_call`` → every tool red."""
    tools = _owner_tools()
    assert len(tools) > 200, "the sweep found too few pack tools"
    unguarded = [tool_name(fn) for slug, owner, fn in tools
                 if not getattr(ti._pack_func(ti._pack_call(fn, owner, False, "k", slug)),
                                ptg.GUARDED_ATTR, False)]
    assert unguarded == []


def test_every_name_of_every_pack_family_is_built_and_so_wrapped() -> None:
    """A tool that the family names and that no owner builds would escape the
    sweep above. Mutation: name a tool in ``SKILL_FAMILIES`` that no owner
    builds → red."""
    built = {tool_name(fn) for _s, _o, fn in _owner_tools()}
    named = {n for slug in sf.pack_slugs() for n in sf.SKILL_FAMILIES[slug]["tools"]}
    # `narrow_and_read` is built only while NARROWING_AGENTS names the owner.
    assert named - built <= {"narrow_and_read"}, sorted(named - built)


@pytest.mark.parametrize(("risk", "careful"), [
    (_READ, False), (_WRITE, True), (None, True),
], ids=["read-only", "write", "no-annotation"])
async def test_a_raising_stub_answers_text(risk, careful, caplog) -> None:
    """Through ``_pack_tools``, a stub of the ``email`` pack that raises. The
    text holds the class and not the message (the message holds an address).
    A read tool says "Nothing changed". Any other tool says it may have changed
    part of it. Mutation: say "Nothing changed" for a write → red."""
    async def tool(email_id: str = "") -> str:
        raise ValueError(f"boom for {SECRET}")

    tool.__name__ = "read_email"
    if risk is not None:
        tool.__tool_risk__ = dict(risk)  # type: ignore[attr-defined]
    with _run(), caplog.at_level(logging.DEBUG):
        build = await _build("email", _stub(tool))
        text = await build.tools[0](email_id="m-1")
    assert text.startswith("The tool failed (ValueError).")
    assert "Next:" in text
    assert is_refusal(text), "the card must not draw a failed call as done"
    assert SECRET not in text
    assert ("Nothing changed." in text) is (not careful)
    assert ("It may have changed part of it. Check before you retry." in text) is careful
    assert SECRET not in caplog.text and "boom" not in caplog.text


async def test_the_log_line_holds_the_tool_name_and_the_class_only(monkeypatch) -> None:
    """Mutation: log ``str(exc)`` → the address shows → red."""
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(ptg._log, "warning", lambda event, **kw: seen.append({"e": event, **kw}))

    async def tool(email_id: str = "") -> str:
        raise KeyError(SECRET)

    tool.__name__ = "read_email"
    with _run():
        build = await _build("email", _stub(tool))
        await build.tools[0](email_id="m-1")
    assert seen == [{"e": "pack_tool_guard.failed", "tool": "read_email", "error_type": "KeyError"}]


async def test_a_tool_that_refuses_as_text_keeps_its_refusal_and_its_chain() -> None:
    """The guard is OUTSIDE ``refusals_as_text``. A ``GatewayRefusal`` still
    reads as the refusal of the family, ``__wrapped__`` still reaches the
    owner's function, and ``__tool_risk__`` and the signature stay (H-236).
    Mutation: let the guard catch before ``refusals_as_text`` (wrap inside),
    or build the wrapper without ``functools.wraps`` → red."""
    voice = RefusalVoice(app="CRM", log_event="x.refused", not_found_next="Search first.")

    async def source(email_id: str = "") -> str:
        raise GatewayRefusal("bad", status=422, detail="email_id: required", fields=("email_id",))

    source.__name__ = "read_email"
    source.__tool_risk__ = dict(_READ)  # type: ignore[attr-defined]
    wrapped = refusals_as_text(source, voice)
    with _run():
        build = await _build("email", _stub(wrapped))
        tool = build.tools[0]
        text = await tool(email_id="")
    assert text.startswith("Refused: The CRM refused an argument (422).")
    assert inspect.unwrap(tool) is source
    assert tool.__wrapped__ is not source and tool.__tool_risk__ == _READ
    assert inspect.signature(tool) == inspect.signature(source)
    assert tool.__cc_gated__ is True


async def test_a_stopped_run_still_stops() -> None:
    """``CancelledError`` is not an ``Exception``, so it passes the guard.
    Mutation: ``except BaseException`` → the cancel becomes text → red."""
    import asyncio

    async def tool(email_id: str = "") -> str:
        raise asyncio.CancelledError

    tool.__name__ = "read_email"
    with _run():
        build = await _build("email", _stub(tool))
        with pytest.raises(asyncio.CancelledError):
            await build.tools[0](email_id="m-1")


def test_a_failed_call_text_is_a_failure_for_the_card() -> None:
    """``is_refusal`` counts the text, so the stream marks ``success: false``.
    Mutation: drop ``FAILED`` from ``is_refusal`` → red."""
    assert is_refusal(ptg.failure_text(RuntimeError("x"), read_only=True))
    assert is_refusal(ptg.failure_text(RuntimeError("x"), read_only=False))
    assert not is_refusal("The tool failed to start")
