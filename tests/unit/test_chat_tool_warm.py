"""TOF-1e (task 3793): the cold build of the chat tool rows leaves the request path.

Spec: ``project-docs/specs/ai_studio_rows/TOF-1a.md``, "Before the flip" item 1.
Code: ``gateway/studio/chat_tool_actions.py`` (``warm``, ``start_warm`` and
``chat_tool_rows(defer_cold=True)``) and the lock of ``_tool_injection``.

Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_chat_tool_warm.py -q``.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from acb_skills import skill_packs as sp
from gateway.studio import chat_tool_actions as cta


@pytest.fixture(autouse=True)
def _cold(monkeypatch):
    """Each case starts cold, and no thread outlives it."""
    monkeypatch.setattr(cta, "_WARM", threading.Event())
    monkeypatch.setattr(cta, "_warm_thread", None)
    yield
    thread = cta._warm_thread
    if thread is not None:
        thread.join(timeout=10)


def test_the_request_path_never_builds_a_cold_owner(monkeypatch) -> None:
    """The per-request read gives class C rows, and the build runs on another
    thread. Mutation: let ``defer_cold`` call ``_owner_tools`` on the caller
    thread → the recorded thread is the main thread → red."""
    caller = threading.get_ident()
    seen: list[int] = []
    gate = threading.Event()

    def slow(_packs: Any) -> dict[str, Any]:
        seen.append(threading.get_ident())
        gate.wait(timeout=10)  # the build is slow, as the real one (64 s)
        return {}

    monkeypatch.setattr(cta, "_owner_tools", slow)
    t0 = time.perf_counter()
    rows = cta.chat_tool_rows(defer_cold=True)
    took = time.perf_counter() - t0
    assert took < 0.2, "the read does not wait for the build"
    assert rows and {r["class"] for r in rows} == {"C"}, "fail closed until warm"
    assert [r["id"] for r in rows] == [f"tool:{n}" for n in sp.chat_tools()], "same rows"
    assert not cta.is_warm()
    assert cta._warm_thread is not None
    gate.set()
    cta._warm_thread.join(timeout=10)
    assert seen and caller not in seen, "the build ran off the caller thread"


def test_once_warm_the_request_path_reads_the_classes_with_no_build(monkeypatch) -> None:
    """Mutation: drop the ``is_set`` check → ``_owner_tools`` runs on each
    read, or the class stays C → red."""
    calls: list[int] = []

    def built(_packs: Any) -> dict[str, Any]:
        calls.append(1)
        return {}

    monkeypatch.setattr(cta, "_owner_tools", built)
    cta._WARM.set()
    cta.chat_tool_rows(defer_cold=True)
    assert len(calls) == 1 and cta._warm_thread is None, "no thread when warm"


def test_start_warm_runs_one_thread_at_a_time(monkeypatch) -> None:
    """Mutation: drop the live-thread check → two threads build → red."""
    gate = threading.Event()
    monkeypatch.setattr(cta, "warm", lambda: gate.wait(timeout=10) or True)
    try:
        assert cta.start_warm() is True
        assert cta.start_warm() is False
    finally:
        gate.set()


def test_warm_sets_the_flag_only_when_every_owner_loaded(monkeypatch) -> None:
    """Mutation: set the flag when one owner failed → the catalog stays on
    class C for good, or a failed owner builds on the loop → red."""
    import orchestrator._tool_injection as ti

    monkeypatch.setattr(cta, "_owner_tools", lambda _p: {"x": object()})
    monkeypatch.setattr(ti, "_pack_owner_module", lambda owner: None)
    assert cta.warm() is False and not cta.is_warm()
    monkeypatch.setattr(ti, "_pack_owner_module", lambda owner: object())
    assert cta.warm() is True and cta.is_warm()
    monkeypatch.setattr(cta, "_owner_tools", lambda _p: (_ for _ in ()).throw(RuntimeError("x")))
    cta._WARM.clear()
    assert cta.warm() is False, "a fault never raises"


def test_the_loader_takes_one_lock_for_its_cold_paths() -> None:
    """The warm thread and a run share the process dicts. Mutation: drop the
    lock from ``_pack_owner_module`` → two threads import one owner → red."""
    import orchestrator._tool_injection as ti

    imports: list[str] = []
    gate = threading.Event()

    def slow_import(owner: str) -> Any:
        imports.append(owner)
        gate.wait(timeout=5)
        return object()

    saved = (dict(ti._PACK_OWNER_MODULES), dict(ti._PACK_OWNER_FAILED))
    ti._PACK_OWNER_MODULES.pop("crm-assistant", None)
    orig = ti._import_pack_owner
    ti._import_pack_owner = slow_import  # type: ignore[assignment]
    try:
        threads = [threading.Thread(target=ti._pack_owner_module, args=("crm-assistant",))
                   for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        gate.set()
        for t in threads:
            t.join(timeout=10)
    finally:
        ti._import_pack_owner = orig  # type: ignore[assignment]
        ti._PACK_OWNER_MODULES.clear()
        ti._PACK_OWNER_MODULES.update(saved[0])
        ti._PACK_OWNER_FAILED.clear()
        ti._PACK_OWNER_FAILED.update(saved[1])
    assert imports == ["crm-assistant"], "one import for two callers"


def test_a_name_that_two_packs_hold_gets_no_row() -> None:
    """TOF-1a "Before the flip" item 5: what the loader does with a name in two
    packs is pinned. The scope drops it from both. Mutation: keep the last
    pack of a repeated name → ``dup`` appears → red."""
    packs = (
        ("pack-a", "crm-assistant", ("only_a", "dup")),
        ("pack-b", "email-assistant", ("dup", "only_b")),
        ("pack-c", "crm-assistant", ("studio_x", "door_op")),
        ("pack-d", "other-owner", ("outside",)),
        ("email-setup", "email-assistant", ("left_out",)),
    )
    got = sp._scope.__wrapped__(
        packs, frozenset({"door_op"}), sp.CHAT_TOOL_OWNERS, sp.CHAT_TOOL_LEFT_OUT)
    assert dict(got) == {"only_a": "pack-a", "only_b": "pack-b"}
