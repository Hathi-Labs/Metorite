"""chat_message upsert must never let a LEAN writer erase a RICH row.

Regression guard for the "my inline AG-UI card disappeared" class of bug.

Three writers upsert the same ``chat_message`` row:
  1. the Next translator's 3s checkpoints (app/api/agent/chat/route.ts),
  2. the gateway's run-boundary fold (chat_fold.persist_final_assistant_message),
  3. the browser re-POSTing its whole message list on every change
     (workbench/control_plane/src/lib/sessions.ts saveMessages).

With blind ``= EXCLUDED.*`` this was last-writer-wins, and the leanest writer
won: a client whose SSE dropped mid-run re-POSTed a content-only snapshot over
the fold's complete row, wiping ``tool_events`` and the ``custom_events`` that
ARE the generative_ui cards. Observed in prod (session 708e7144): the gateway
logged ``chat_fold.persisted … tools=2`` and the row still ended up with zero
tool events and zero custom events.

Two layers here:
  • a CONTRACT test on the SQL — runs everywhere, including CI (no DB), and
    fails the moment someone reverts a column to a blind overwrite;
  • a LIVE round-trip — runs wherever Postgres is reachable (the VPS, any dev
    box with the DB), proving the merge actually behaves.
"""
from __future__ import annotations

import re

import pytest

from gateway.routes.chat import (
    MONOTONIC_MESSAGE_COLUMNS,
    _MESSAGE_UPSERT_SQL,
)

# ═══════════════════════════════════════════════════════════════════════════
# Contract — the SQL itself (no DB, always runs)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("column", MONOTONIC_MESSAGE_COLUMNS)
def test_array_columns_are_never_blindly_overwritten(column: str) -> None:
    """An empty incoming array must not be able to erase a stored one."""
    blind = re.search(
        rf"^\s*{column}\s*=\s*EXCLUDED\.{column}\s*,?\s*$",
        _MESSAGE_UPSERT_SQL,
        re.MULTILINE,
    )
    assert blind is None, (
        f"{column} is assigned straight from EXCLUDED — a client re-saving a "
        f"stale/lean snapshot will wipe it. Run artifacts are monotonic: guard "
        f"with jsonb_array_length(...) > 0 (see _MESSAGE_UPSERT_SQL)."
    )
    assert f"jsonb_array_length(COALESCE(EXCLUDED.{column}" in _MESSAGE_UPSERT_SQL, (
        f"{column} lost its non-empty guard in the ON CONFLICT clause."
    )
    assert f"ELSE chat_message.{column} END" in _MESSAGE_UPSERT_SQL, (
        f"{column} must fall back to the STORED value when the incoming array "
        f"is empty."
    )


@pytest.mark.parametrize("column", ["reasoning", "agent_state"])
def test_nullable_columns_coalesce_to_stored(column: str) -> None:
    """A NULL from a lean writer must not erase stored reasoning/state."""
    assert (
        f"COALESCE(EXCLUDED.{column}, chat_message.{column})"
        in _MESSAGE_UPSERT_SQL
    ), f"{column} must COALESCE onto the stored value, not overwrite with NULL."


def test_content_is_still_overwritten() -> None:
    """``content`` is deliberately NOT monotonic — it's rewritten in place.

    The answer is edited as it streams (tool-boundary folds, the
    RUN_FINISHED un-fold), so "keep the longer one" would strand stale text.
    This pins the asymmetry so it stays a decision, not an accident.
    """
    assert re.search(
        r"^\s*content\s*=\s*EXCLUDED\.content\s*,\s*$",
        _MESSAGE_UPSERT_SQL,
        re.MULTILINE,
    ), "content should remain a plain overwrite"
    assert "content" not in MONOTONIC_MESSAGE_COLUMNS


# ═══════════════════════════════════════════════════════════════════════════
# Live round-trip — requires a reachable Postgres with chat_message
# ═══════════════════════════════════════════════════════════════════════════


def _db_reachable() -> bool:
    """True if we can open a session AND chat_message exists."""
    try:
        from acb_graph import get_session
        from sqlalchemy import text
    except Exception:
        return False
    try:
        with get_session() as s:
            s.execute(text("SELECT 1 FROM chat_message LIMIT 1"))
        return True
    except Exception:
        return False


_LIVE = _db_reachable()
_needs_db = pytest.mark.skipif(
    not _LIVE,
    reason="no reachable Postgres — live upsert round-trip skipped "
    "(runs on the VPS / any box with the DB)",
)

# Throwaway ids so nothing collides with real chat data; purged after.
_TEST_SESSION = "pytest-chat-message-upsert"
_TEST_MESSAGE = "pytest-msg-1"
# The member who writes both turns (S13, projects_ai_chat.md §19). An agent
# row takes a client update only from the member who started its run, so the
# second write must come from the same member as the first.
_ACTOR = "pytest@local"


def _purge() -> None:
    """Remove the throwaway session + its messages (idempotent)."""
    try:
        from acb_graph import get_session
        from sqlalchemy import text

        with get_session() as s:
            s.execute(
                text("DELETE FROM chat_message WHERE session_id = :sid"),
                {"sid": _TEST_SESSION},
            )
            s.execute(
                text("DELETE FROM chat_session WHERE id = :sid"),
                {"sid": _TEST_SESSION},
            )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


@pytest.fixture()
def _clean_row():
    _purge()
    yield
    _purge()


@_needs_db
@pytest.mark.usefixtures("_clean_row")
def test_lean_resave_keeps_the_generative_ui_card() -> None:
    """The exact prod failure: a content-only re-save must keep the card."""
    from gateway.routes.chat import (
        MessageRecord,
        _ensure_session,
        _get_messages,
        _upsert_messages,
    )

    _ensure_session(_TEST_SESSION, "pytest@local", "pytest-agent")

    card = {"name": "generative_ui", "value": {"type": "table", "props": {}}}
    rich = MessageRecord(
        id=_TEST_MESSAGE,
        role="assistant",
        content="here is the pipeline",
        timestamp=1_700_000_000_000,
        tool_events=[{"name": "emit_generative_ui", "status": "done"}],
        progress_lines=["emit_generative_ui"],
        reasoning='["thinking"]',
        agent_state={"segments": [{"id": "m1", "text": "hi"}]},
        custom_events=[card],
    )
    _upsert_messages(_TEST_SESSION, [rich], actor_email=_ACTOR)

    # The browser re-POSTs its whole list; its copy of this turn lost the
    # stream artifacts (dropped SSE), so everything but content is empty.
    lean = MessageRecord(
        id=_TEST_MESSAGE,
        role="assistant",
        content="here is the pipeline",
        timestamp=1_700_000_000_000,
    )
    _upsert_messages(_TEST_SESSION, [lean], actor_email=_ACTOR)

    stored = [
        m for m in _get_messages(_TEST_SESSION, "pytest@local")
        if m["id"] == _TEST_MESSAGE
    ]
    assert len(stored) == 1
    row = stored[0]
    assert row["customEvents"] == [card], (
        "the generative_ui card was erased by a lean re-save — inline AG-UI "
        "will vanish from earlier turns again"
    )
    assert len(row["toolEvents"]) == 1, "tool timeline erased by a lean re-save"
    assert row["progressLines"] == ["emit_generative_ui"]
    assert row["reasoning"] == '["thinking"]'
    assert row["agentState"] is not None


@_needs_db
@pytest.mark.usefixtures("_clean_row")
def test_richer_write_still_wins() -> None:
    """Monotonic must not mean frozen — a non-empty write still replaces."""
    from gateway.routes.chat import (
        MessageRecord,
        _ensure_session,
        _get_messages,
        _upsert_messages,
    )

    _ensure_session(_TEST_SESSION, "pytest@local", "pytest-agent")

    base = MessageRecord(
        id=_TEST_MESSAGE,
        role="assistant",
        content="partial",
        timestamp=1_700_000_000_000,
        tool_events=[{"name": "zoho_crm"}],
        custom_events=[{"name": "generative_ui", "value": {"v": 1}}],
    )
    _upsert_messages(_TEST_SESSION, [base], actor_email=_ACTOR)

    grown = MessageRecord(
        id=_TEST_MESSAGE,
        role="assistant",
        content="partial then some",
        timestamp=1_700_000_000_000,
        tool_events=[{"name": "zoho_crm"}, {"name": "emit_generative_ui"}],
        custom_events=[
            {"name": "generative_ui", "value": {"v": 1}},
            {"name": "generative_ui", "value": {"v": 2}},
        ],
    )
    _upsert_messages(_TEST_SESSION, [grown], actor_email=_ACTOR)

    row = next(
        m for m in _get_messages(_TEST_SESSION, "pytest@local")
        if m["id"] == _TEST_MESSAGE
    )
    assert row["content"] == "partial then some"
    assert len(row["toolEvents"]) == 2
    assert len(row["customEvents"]) == 2
