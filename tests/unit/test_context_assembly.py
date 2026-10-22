"""Unit tests for the server-side run-context assembler (C2).

One assembler for every chat / agent-run path: token-budgeted windowing +
current-turn dedup + DB-rebuild-when-empty, replacing the six divergent
client-history slicers in the executor. See specs/context_assembly_c2.md.
"""
from __future__ import annotations

from acb_llm import (
    assemble_run_context,
    context_window_for,
    count_message_tokens,
)


def _roles(msgs):
    return [m["role"] for m in msgs]


# ── Basic assembly ──────────────────────────────────────────────────────────


def test_leads_with_system_then_history_then_current():
    out = assemble_run_context(
        system_context="You are X.",
        history=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        current_message="how are you",
        model="",
    )
    assert _roles(out) == ["system", "user", "assistant", "user"]
    assert out[0]["content"] == "You are X."
    assert out[-1]["content"] == "how are you"


def test_no_system_context_omits_system_message():
    out = assemble_run_context(
        history=[{"role": "user", "content": "a"}],
        current_message="b",
        model="",
    )
    assert _roles(out) == ["user", "user"]


def test_empty_everything_still_returns_current_turn():
    out = assemble_run_context(current_message="just this", model="")
    assert out == [{"role": "user", "content": "just this"}]


def test_blank_and_bad_role_history_entries_dropped():
    out = assemble_run_context(
        history=[
            {"role": "user", "content": "keep me"},
            {"role": "assistant", "content": "   "},   # blank → dropped
            {"role": "tool", "content": "tool junk"},   # bad role → dropped
            {"role": "user", "content": ""},            # empty → dropped
        ],
        current_message="q",
        model="",
    )
    assert [m["content"] for m in out] == ["keep me", "q"]


# ── Current-turn dedup (server-side withoutCurrentTurn) ─────────────────────


def test_dedupes_trailing_current_turn():
    out = assemble_run_context(
        history=[
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "the question"},  # == current
        ],
        current_message="the question",
        model="",
    )
    # "the question" appears exactly once (as the appended current turn).
    assert sum(1 for m in out if m["content"] == "the question") == 1
    assert out[-1] == {"role": "user", "content": "the question"}


def test_does_not_dedupe_non_trailing_or_assistant():
    out = assemble_run_context(
        history=[
            {"role": "user", "content": "the question"},  # NOT trailing
            {"role": "assistant", "content": "an answer"},
        ],
        current_message="the question",
        model="",
    )
    # The earlier identical user turn is kept; current appended → appears twice.
    assert sum(1 for m in out if m["content"] == "the question") == 2


# ── DB-rebuild-when-empty (non-chat caller parity) ──────────────────────────


def test_rebuilds_from_loader_when_history_empty():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [
            {"role": "user", "content": "from db"},
            {"role": "assistant", "content": "db answer"},
        ]

    out = assemble_run_context(
        history=[],
        current_message="new q",
        model="",
        history_loader=loader,
    )
    assert calls["n"] == 1
    assert [m["content"] for m in out] == ["from db", "db answer", "new q"]


def test_loader_not_called_when_client_sent_history():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [{"role": "user", "content": "db"}]

    out = assemble_run_context(
        history=[{"role": "user", "content": "client"}],
        current_message="q",
        model="",
        history_loader=loader,
    )
    assert calls["n"] == 0  # client history wins; loader untouched
    assert [m["content"] for m in out] == ["client", "q"]


def test_loader_failure_degrades_gracefully():
    def loader():
        raise RuntimeError("db down")

    out = assemble_run_context(
        history=[], current_message="q", model="", history_loader=loader,
    )
    # Never raises; just returns the current turn.
    assert out == [{"role": "user", "content": "q"}]


# ── Token-budget windowing (the key upgrade over blind count caps) ──────────


def test_token_budget_shrinks_overlong_transcript():
    # A transcript far larger than tier-fast's 32k window must be fitted.
    big = [
        {"role": "user", "content": "X" * 60_000},
        {"role": "assistant", "content": "Y" * 60_000},
    ]
    out = assemble_run_context(
        history=big, current_message="now", model="tier-fast",
        max_output_tokens=1024,
    )
    assert count_message_tokens(out, "tier-fast") <= context_window_for("tier-fast")
    # The current turn is always preserved verbatim.
    assert out[-1]["content"] == "now"


def test_no_model_skips_token_fit():
    # Without a model there's no window to fit to — return assembled as-is.
    big = [{"role": "user", "content": "Z" * 100_000}]
    out = assemble_run_context(history=big, current_message="q", model="")
    assert len(out[0]["content"]) == 100_000  # untouched


def test_max_turns_upper_bound_applied_before_fit():
    history = [
        {"role": "user", "content": f"turn {i}"} for i in range(200)
    ]
    out = assemble_run_context(
        history=history, current_message="last", model="", max_turns=10,
    )
    # 10 history turns + 1 current.
    assert len(out) == 11
    assert out[0]["content"] == "turn 190"  # only the last 10 kept
    assert out[-1]["content"] == "last"


# ── Room for the agent's instructions and tool schemas (2026-10-22) ─────────


def _fixed_window(monkeypatch, window: int = 20_000) -> None:
    import acb_llm.context as ctx

    monkeypatch.setattr(ctx, "context_window_for", lambda _m: window)
    monkeypatch.setattr(
        ctx, "count_message_tokens",
        lambda msgs, _m="": sum(len(str(m.get("content") or "")) // 4 + 8 for m in msgs),
    )


def test_reserve_tokens_leave_room_for_instructions_and_tools(monkeypatch):
    """The fit takes ``reserve_tokens`` off the history budget.

    Mutation: drop ``- reserve_tokens`` from the budget in
    ``assemble_run_context``. The history then fills the window that the
    agent's instructions and tools need, and this test fails.
    """
    import acb_llm.context as ctx

    _fixed_window(monkeypatch)
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i} " + "x" * 4000}
        for i in range(16)
    ]
    plain = assemble_run_context(
        history=history, current_message="now", model="m", max_output_tokens=2000,
    )
    reserved = assemble_run_context(
        history=history, current_message="now", model="m", max_output_tokens=2000,
        reserve_tokens=8000,
    )
    assert len(reserved) < len(plain)
    assert ctx.count_message_tokens(reserved) + 8000 + 2000 + 512 <= 20_000
    assert reserved[-1]["content"] == "now"


def test_agent_reserve_reads_maf_instructions_and_tools():
    """The executor measures a MAF agent's instructions and tool schemas.

    Mutation: return 0 from ``_agent_prompt_reserve_tokens``. Then the
    reserve stays 0 and this test fails.
    """
    from agent_framework import tool
    from orchestrator.executor import _agent_prompt_reserve_tokens

    @tool
    def lookup(order_id: str) -> str:
        """Look up one order by its id and return its status line."""
        return order_id

    from types import SimpleNamespace

    agent = SimpleNamespace(default_options={"instructions": "i" * 4000, "tools": [lookup]})
    tokens = _agent_prompt_reserve_tokens(agent)
    assert tokens >= 1000 + 20  # 4000 chars of instructions + one schema
    assert _agent_prompt_reserve_tokens(None) == 0


def test_agent_reserve_reads_copilot_system_message():
    """A Copilot SDK agent holds its persona in ``system_message``.

    Mutation: read only ``instructions``. This agent then measures 0.
    """
    from types import SimpleNamespace

    from orchestrator.executor import _agent_prompt_reserve_tokens

    copilot = SimpleNamespace(
        _default_options={"system_message": {"mode": "append", "content": "p" * 8000}})
    assert _agent_prompt_reserve_tokens(copilot) == 2000


def test_compose_maf_run_passes_the_agent_reserve():
    """Each executor call of the assembler passes the agent's reserve.

    Mutation: remove ``reserve_tokens=_agent_prompt_reserve_tokens(agent)``
    from any call site. The count below then drops under 3.
    """
    import inspect

    from orchestrator import executor

    src = inspect.getsource(executor)
    assert src.count("reserve_tokens=_agent_prompt_reserve_tokens(agent)") >= 3
