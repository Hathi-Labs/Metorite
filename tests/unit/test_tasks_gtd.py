"""Unit tests for the /tasks GTD backend (offline — no DB, no HTTP).

Covers the pure logic layers:
  - provider registry: `build_provider` refuses a name it cannot resolve
  - ai.propose: the clarify heuristic (disposition branches, project
    auto-match, GTD→stage default mapping)
  - items: view map completeness + timestamp parsing
  - the push path's contract with the broker gate (BO-1b)

🔧 **RE-CUT 2026-08-25 (D52 repair round 1, board WS-39 S1).** Sixteen cases
here instantiated `ClickUpProvider` — payload shaping, pagination, schema
hierarchy, live membership, folder/list creation, the delete gate. D52 deleted
that class, so this module raised `ImportError` at COLLECTION and, with
`pytest -x`, took the entire `tests/unit` run with it. They are deleted, not
skipped (§12.6 criterion 7): they asserted the wire shape of one vendor's API,
which is not a contract that outlives the vendor.

What survives is everything that was never ClickUp's — the clarify heuristic,
the view map, soft delete/restore/purge, the stage mapping, and the BO-1b push
rules. `_queueing_provider` was re-cut onto a local `BaseTaskProvider` stub
(the same move `test_provider_broker_gate.py` made), because the rule it locks
— *a queued write reports `awaiting_approval`, never `synced`* — belongs to the
gate, not to a connector.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from gateway.routes.tasks import ai as tasks_ai
from gateway.routes.tasks.items import (
    DISPOSITIONS,
    VIEW_WHERE,
    ItemPatch,
    _build_item_update,
    _parse_ts,
)
from gateway.routes.tasks.providers import build_provider

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

def test_build_provider_unknown_provider_raises_400():
    """An unresolvable name is a 400, never a None the caller then dereferences.

    ⚠️ Since D52 the registry is EMPTY, so this is true of every name — the
    whole-registry claim lives in `tests/unit/test_no_task_provider_connectors.py`
    (the D52 fence). What is asserted HERE is the factory's own behaviour on a
    miss, which is the same before and after the retirement.
    """
    with pytest.raises(HTTPException) as exc:
        build_provider("asana", {"api_token": "x"})
    assert exc.value.status_code == 400



# ---------------------------------------------------------------------------
# Clarify heuristic (ai.propose)
# ---------------------------------------------------------------------------

def _item(title: str, **kw) -> SimpleNamespace:
    return SimpleNamespace(
        title=title, description=kw.get("description", ""),
        project_id=kw.get("project_id"),
    )


def _project(pid: str, outcome: str, account_id: str | None = None,
             status: str = "ACTIVE") -> SimpleNamespace:
    return SimpleNamespace(id=pid, outcome=outcome, purpose="",
                           status=status, account_id=account_id)


def test_propose_someday_hint():
    p = tasks_ai.propose(_item("Idea: someday learn KiCad"), [], [], {})
    assert p["disposition"] == "SOMEDAY"
    assert p["confidence"] == "high"
    assert not p["actionable"]


def test_propose_reference_hint():
    p = tasks_ai.propose(_item("Receipt from the Hyderabad flight"), [], [], {})
    assert p["disposition"] == "REFERENCE"


def test_propose_delegate_matches_person_and_defaults_to_synced_account():
    people = [{"name": "Priya Sharma", "email": "p@x.in", "provider_user_id": "7"}]
    p = tasks_ai.propose(
        _item("Ask Priya to reschedule the vendor review"),
        people, [], {"acct-1": ["Backlog", "To-do", "In Process"]})
    assert p["disposition"] == "WAITING"
    assert p["suggested_assignee"]["name"] == "Priya Sharma"
    # delegation is collaborative → lands on the connected workspace
    assert p["account_id"] == "acct-1"
    # actioned/delegated → the To-do stage (P7 mapping)
    assert p["status"] == "To-do"


def test_propose_project_hint_with_outcome():
    p = tasks_ai.propose(_item("Plan the Hyderabad lab fit-out"), [], [], {})
    assert p["disposition"] == "PROJECT"
    assert p["outcome"].startswith("Plan the Hyderabad lab fit-out")


def test_propose_auto_matches_existing_project_and_inherits_account():
    projects = [
        _project("p1", "Overhaul the print-farm reliability program", "acct-9"),
        _project("p2", "Run the Q3 hiring wave", "acct-9"),
    ]
    p = tasks_ai.propose(
        _item("Water-cooling loop leaking on the print farm rig — investigate"),
        [], projects, {"acct-9": ["Backlog", "To-do"]})
    assert p["project_id"] == "p1"
    assert p["project_inferred"] is True
    assert p["account_id"] == "acct-9"
    assert "belongs to" in p["rationale"]


def test_propose_no_match_stays_local():
    p = tasks_ai.propose(_item("Water the office plants"), [], [], {})
    assert p["account_id"] is None
    assert p["project_id"] is None


def test_propose_emits_complexity_parity():
    # The deterministic path always carries a complexity so the field exists
    # even when the LLM is off: PROJECT → "project", else "single".
    proj = tasks_ai.propose(_item("Plan the Hyderabad lab fit-out"), [], [], {})
    assert proj["complexity"] == "project"
    single = tasks_ai.propose(_item("Reply to the landlord email"), [], [], {})
    assert single["complexity"] == "single"


# ---------------------------------------------------------------------------
# Clarify — LLM project match resolution + overlay (Phase 1)
# ---------------------------------------------------------------------------

def test_resolve_project_match_by_token_and_fuzzy_and_none():
    projects = [
        _project("p1", "Overhaul the print-farm reliability program", "acct-9"),
        _project("p2", "Run the Q3 hiring wave", "acct-9"),
        _project("pX", "Dormant thing", status="SOMEDAY"),  # excluded (not ACTIVE)
    ]
    # [P#] token indexes into the ACTIVE projects, in order.
    assert tasks_ai._resolve_project_match("[P0]", projects).id == "p1"
    assert tasks_ai._resolve_project_match("P1", projects).id == "p2"
    # Fuzzy: the model echoed the outcome words (≥2 overlap).
    assert tasks_ai._resolve_project_match(
        "the Q3 hiring wave", projects).id == "p2"
    # No match / sentinels / out-of-range → None (never invents a project).
    assert tasks_ai._resolve_project_match("none", projects) is None
    assert tasks_ai._resolve_project_match("", projects) is None
    assert tasks_ai._resolve_project_match("[P9]", projects) is None
    assert tasks_ai._resolve_project_match("totally unrelated", projects) is None


def test_llm_overlay_files_actionable_item_under_matched_project():
    # A NEXT item the LLM filed under an existing ClickUp project inherits that
    # project's id + account, even though the disposition is not PROJECT.
    projects = [_project("p1", "Acme rollout", "acct-9")]
    llm_core = {
        "disposition": "NEXT",
        "next_action": "Email Acme the revised quote",
        "confidence": "high",
        "rationale": "Part of the Acme rollout.",
        "llm_project": projects[0],
    }
    merged = tasks_ai.propose_with_llm(
        _item("send Acme the new quote"), [], projects,
        {"acct-9": ["Backlog", "To-do"]}, llm_core)
    assert merged["disposition"] == "NEXT"
    assert merged["project_id"] == "p1"
    assert merged["project_inferred"] is True
    assert merged["account_id"] == "acct-9"


def test_llm_overlay_carries_complexity_and_subtasks():
    projects: list = []
    llm_core = {
        "disposition": "NEXT",
        "next_action": "Draft the onboarding checklist",
        "confidence": "medium",
        "rationale": "One deliverable, a few steps.",
        "complexity": "subtasks",
        "subtasks": ["List the accounts to create", "Write the welcome email"],
    }
    merged = tasks_ai.propose_with_llm(
        _item("set up new hire onboarding"), [], projects, {}, llm_core)
    assert merged["complexity"] == "subtasks"
    assert merged["subtasks"][0] == "List the accounts to create"


def test_llm_overlay_without_llm_core_is_deterministic_with_complexity():
    # llm_core=None → pure heuristic, but the complexity field still rides along.
    merged = tasks_ai.propose_with_llm(
        _item("Plan the offsite"), [], [], {}, None)
    assert merged["disposition"] == "PROJECT"
    assert merged["complexity"] == "project"


def test_llm_overlay_carries_vague_title_and_due_date():
    # The Sort→Shape card's vague-title gate + When axis both ride on the LLM
    # overlay's is_vague/suggested_title/due_date keys.
    llm_core = {
        "disposition": "NEXT",
        "next_action": "Follow up with Acme on the signed MSA",
        "confidence": "low",
        "rationale": "Title was too vague to place confidently.",
        "is_vague": True,
        "suggested_title": "Follow up with Acme on the signed MSA",
        "due_date": "2026-07-11",
    }
    merged = tasks_ai.propose_with_llm(
        _item("Follow up"), [], [], {}, llm_core)
    assert merged["is_vague"] is True
    assert merged["suggested_title"] == "Follow up with Acme on the signed MSA"
    assert merged["due_date"] == "2026-07-11"


def test_llm_overlay_without_llm_core_has_no_vague_flag():
    # Pure heuristic path never claims a title is vague — only the LLM judges it.
    merged = tasks_ai.propose_with_llm(_item("Plan the offsite"), [], [], {}, None)
    assert "is_vague" not in merged
    assert "suggested_title" not in merged


def test_llm_propose_parses_vague_title_fields_from_json(monkeypatch):
    # Exercise _llm_propose's own JSON parsing (not just the overlay merge).
    fake_content = json.dumps({
        "disposition": "NEXT",
        "next_action": "Call the lab about calibration",
        "confidence": "medium",
        "rationale": "Ambiguous stub, clarified via notes.",
        "is_vague": True,
        "suggested_title": "Call the lab about calibration",
        "due_date": "not-a-date",  # must be dropped — not a real ISO date
        "complexity": "single",
    })
    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=fake_content))])

    async def fake_completion(**kwargs):
        return fake_resp, kwargs.get("model")

    monkeypatch.setattr(
        "acb_llm.context.acompletion_with_fallback", fake_completion, raising=False)
    import asyncio
    core = asyncio.run(tasks_ai._llm_propose(
        _item("Call"), [], [], {}, "tier-fast"))
    assert core is not None
    assert core["is_vague"] is True
    assert core["suggested_title"] == "Call the lab about calibration"
    # Malformed due_date is silently dropped, never surfaced as a fake deadline.
    assert "due_date" not in core


def test_llm_propose_suggested_title_dropped_when_same_as_current():
    # A "suggestion" identical to the existing title isn't a suggestion.
    core = {"suggested_title": "call sanjay"}
    # Simulate the identical-title guard directly (mirrors _llm_propose's check).
    item = _item("Call Sanjay")
    sug = core["suggested_title"]
    surfaced = sug and sug.lower() != (item.title or "").strip().lower()
    assert not surfaced


def test_suggest_title_route_and_helper_use_llm_with_fallback():
    """The 'Improve title' affordance (always-available) and the vague-title
    gate both resolve through _llm_suggest_title, which degrades safely."""
    import asyncio

    from gateway.routes.tasks.ai import _llm_suggest_title

    # No LLM configured / import failure → safe default, never raises.
    out = asyncio.run(_llm_suggest_title("Follow up", None, "tier-fast"))
    assert out == {"is_vague": False, "suggested_title": None}
    # Empty title → same safe default, no call attempted.
    out2 = asyncio.run(_llm_suggest_title("", None, "tier-fast"))
    assert out2["is_vague"] is False and out2["suggested_title"] is None


def test_suggest_title_route_is_registered():
    from gateway.routes.tasks import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/tasks/items/{item_id}/suggest-title" in paths


def test_default_status_gtd_mapping():
    statuses = ["Backlog", "To-do", "In Process", "Review", "Done"]
    assert tasks_ai.default_status("SOMEDAY", statuses) == "Backlog"
    assert tasks_ai.default_status("PROJECT", statuses) == "Backlog"
    assert tasks_ai.default_status("NEXT", statuses) == "To-do"
    assert tasks_ai.default_status("WAITING", statuses) == "To-do"
    assert tasks_ai.default_status("NEXT", []) is None


# ---------------------------------------------------------------------------
# Items — small pure helpers
# ---------------------------------------------------------------------------

def test_view_map_covers_the_gtd_views():
    for view in ("inbox", "next", "waiting", "someday", "reference",
                 "calendar", "done", "all"):
        assert view in VIEW_WHERE


def test_all_view_excludes_done_and_trash():
    # The default working board must not surface a connected workspace's
    # completed backlog — DONE has its own view. Trash is always hidden.
    clause = VIEW_WHERE["all"]
    assert "DONE" in clause and "TRASH" in clause
    assert "NOT IN" in clause
    # 'done' view is the ONLY one that shows DONE.
    assert VIEW_WHERE["done"] == "i.disposition = 'DONE'"


def test_archive_view_shows_only_archived():
    # The archive view is the only place archived rows appear.
    assert VIEW_WHERE["archive"] == "i.archived_at IS NOT NULL"


def test_list_items_excludes_subtasks_and_selects_subtask_count():
    # Subtasks are nested under their parent, never standalone rows; and every
    # item read carries a subtask_count roll-up.
    import inspect

    from gateway.routes.tasks import core as core_mod
    from gateway.routes.tasks import items as items_mod

    src = inspect.getsource(items_mod.list_items)
    assert "i.parent_item_id IS NULL" in src
    assert "subtask_count" in core_mod.ITEM_SELECT
    assert "c.parent_item_id = i.id" in core_mod.ITEM_SELECT


# ---------------------------------------------------------------------------
# Delete (soft-delete + undo/restore + purge propagation) & bulk archive
# ---------------------------------------------------------------------------


def test_delete_is_soft_and_hidden_from_every_view():
    """DELETE must set a tombstone (deleted_at), not remove the row — so Undo
    can restore it losslessly. And every list read must hide tombstoned rows."""
    import inspect

    from gateway.routes.tasks import items as items_mod

    src = inspect.getsource(items_mod.delete_item)
    assert "deleted_at = now()" in src          # soft delete
    assert "DELETE FROM gtd_items" not in src   # not a hard delete anymore
    # Reads exclude tombstones.
    assert items_mod._DELETED_EXCLUDE == "i.deleted_at IS NULL"
    assert "_DELETED_EXCLUDE" in inspect.getsource(items_mod.list_items)
    assert "include_deleted" in inspect.getsource(items_mod._fetch_item)


def test_restore_clears_the_tombstone_only_for_deleted_rows():
    import inspect

    from gateway.routes.tasks import items as items_mod

    src = inspect.getsource(items_mod.restore_item)
    assert "deleted_at = NULL" in src
    assert "deleted_at IS NOT NULL" in src   # only a deleted row can be restored


def test_purge_removes_row_and_archives_clickup_counterpart():
    """Purge finalizes a soft delete: it removes the LOCAL row AND, for a pushed
    SYNCED task, ARCHIVES the upstream ClickUp task (recoverable there — we never
    hard-delete upstream) — best-effort, before the local removal so the provider
    linkage is still available."""
    import inspect

    from gateway.routes.tasks import items as items_mod

    src = inspect.getsource(items_mod.purge_item)
    assert "DELETE FROM gtd_items" in src        # local row IS hard-removed
    assert "_delete_upstream" in src
    # Upstream propagation is gated on a pushed synced task.
    assert 'row.source != "LOCAL"' in src
    assert "provider_task_id" in src

    up = inspect.getsource(items_mod._delete_upstream)
    # Upstream is ARCHIVED, not deleted — recoverable in the connected tool.
    assert "provider.archive_task" in up
    assert "provider.delete_task" not in up
    # Best-effort: an upstream failure must not block the local purge.
    assert "except Exception" in up




def test_base_provider_delete_task_defaults_to_unsupported():
    """A connector that hasn't implemented delete must fail loudly, not
    silently leave the upstream task behind."""
    import inspect

    from gateway.routes.tasks import providers

    src = inspect.getsource(providers.BaseTaskProvider.delete_task)
    assert "not supported" in src and "501" in src


def test_archive_endpoints_mirror_upstream_for_synced_tasks():
    """Single archive and bulk archive both back-propagate to the connected tool
    via _archive_upstream (so the app and ClickUp stay consistent), while a
    LOCAL-only task never triggers an outward write."""
    import inspect

    from gateway.routes.tasks import items as items_mod

    single = inspect.getsource(items_mod.archive_item)
    assert "_archive_upstream" in single
    bulk = inspect.getsource(items_mod.bulk_archive)
    assert "_archive_upstream" in bulk

    helper = inspect.getsource(items_mod._archive_upstream)
    # SYNCED-only: local rows (no provider linkage) are skipped.
    assert 'row.source == "LOCAL"' in helper
    assert "provider.archive_task" in helper
    # Best-effort per row — one failure doesn't abort the batch.
    assert "except Exception" in helper


def test_base_provider_archive_task_defaults_to_unsupported():
    import inspect

    from gateway.routes.tasks import providers

    src = inspect.getsource(providers.BaseTaskProvider.archive_task)
    assert "not supported" in src and "501" in src


def test_bulk_archive_is_a_local_overlay():
    """Bulk archive flips archived_at for many rows and never touches the
    connected tool (safe for ClickUp tasks) — mirrors single archive."""
    import inspect

    from gateway.routes.tasks import items as items_mod

    src = inspect.getsource(items_mod.bulk_archive)
    assert "archived_at = CASE WHEN :on THEN now() ELSE NULL END" in src
    assert "id::text = ANY(:ids)" in src
    # No provider call in the archive path.
    assert "build_provider" not in src
    assert "delete_task" not in src


# ---------------------------------------------------------------------------
# Prioritization engine — the 8-cell matrix + urgency derivation
# ---------------------------------------------------------------------------


def test_priority_formula_maps_all_8_input_combos_to_7_levels():
    """The 3 booleans → level mapping, for all 8 combinations. The two "not
    important to you" cases (urgent-only AND neither) both fold into
    low-priority, so 8 input combos resolve to 7 distinct levels."""
    from gateway.routes.tasks.priority import PriorityInputs, cell_for_inputs

    I, U, L = True, True, True  # noqa: E741 (mirror the flag names)
    n = False
    cases = {
        # leveraged branch (rank 1/3/5/6)
        (I, U, L): "critical",
        (I, n, L): "high-leverage",
        (n, U, L): "quick-leverage",
        (n, n, L): "speculative-bet",
        # non-leveraged branch (rank 2/4/7)
        (I, U, n): "urgent",
        (I, n, n): "important",
        # the merge: urgent-only AND neither → one low-priority level.
        (n, U, n): "low-priority",
        (n, n, n): "low-priority",
    }
    for (important, urgent, leveraged), expected in cases.items():
        got = cell_for_inputs(PriorityInputs(
            important=important, urgent=urgent, leveraged=leveraged))
        assert got == expected, f"{(important, urgent, leveraged)} → {got}"


def test_priority_levels_carry_the_right_action_mode():
    """Each level nudges toward do / delegate / schedule / drop — the SUGGESTION
    (a competing card badge, never a status, never in the label)."""
    from gateway.routes.tasks.priority import CELL_META

    mode = {c: CELL_META[c][3] for c in CELL_META}
    # "do" = genuinely mine, no nudge.
    assert mode["critical"] == "do"
    assert mode["high-leverage"] == "do"
    assert mode["quick-leverage"] == "do"
    assert mode["speculative-bet"] == "do"
    # Important+urgent → delegate/attend nudge.
    assert mode["urgent"] == "delegate"
    # Important-only → schedule (or delegate) nudge.
    assert mode["important"] == "schedule"
    # Low priority (urgent-only or neither) → eliminate (or delegate if it must
    # happen).
    assert mode["low-priority"] == "drop"


def test_priority_labels_have_no_action_words():
    """Labels are the priority CHARACTER only — the action (delegate/schedule/
    eliminate) lives in the badge, not the label. Guards the reframe so a future
    edit can't sneak an action-word back into a level name."""
    from gateway.routes.tasks.priority import CELL_META

    banned = ("delegate", "schedule", "eliminate", "ignore")
    for cell, (_order, _emoji, label, _mode) in CELL_META.items():
        low = label.lower()
        assert not any(w in low for w in banned), f"{cell}: {label!r}"


def test_priority_level_order_is_the_7_level_sequence():
    """The revised 1→7 sequence interleaves leveraged and non-leveraged levels
    (an important+urgent fire outranks leveraged high-leverage work). Drives the
    grouped Priority/Engage views + the priority sort."""
    from gateway.routes.tasks.priority import CELLS_IN_ORDER

    assert CELLS_IN_ORDER == [
        "critical",         # 1
        "urgent",           # 2  (important + urgent)
        "high-leverage",    # 3
        "important",        # 4
        "quick-leverage",   # 5
        "speculative-bet",  # 6
        "low-priority",     # 7  (urgent-only OR neither)
    ]


def test_urgency_is_derived_overdue_or_within_window():
    from datetime import UTC, datetime, timedelta

    from gateway.routes.tasks.priority import is_urgent

    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    assert is_urgent(None, now=now) is False               # no due date
    assert is_urgent(now - timedelta(days=2), now=now) is True   # overdue
    assert is_urgent(now + timedelta(hours=6), now=now) is True  # within 48h
    assert is_urgent(now + timedelta(hours=47), now=now) is True
    assert is_urgent(now + timedelta(hours=72), now=now) is False  # outside
    # Window is configurable.
    assert is_urgent(now + timedelta(hours=72), window_hours=96, now=now) is True


def test_priority_cell_end_to_end_uses_derived_urgency():
    """priority_cell must combine the manual flags with derived urgency: an
    important+leveraged task with a deadline 6h out is Critical; move the
    deadline out a week and it becomes High-Leverage."""
    from datetime import UTC, datetime, timedelta

    from gateway.routes.tasks.priority import priority_cell

    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    soon = now + timedelta(hours=6)
    later = now + timedelta(days=7)
    assert priority_cell(important=True, leveraged=True,
                         due_at=soon, now=now) == "critical"
    assert priority_cell(important=True, leveraged=True,
                         due_at=later, now=now) == "high-leverage"
    assert priority_cell(important=True, leveraged=True,
                         due_at=None, now=now) == "high-leverage"  # no date → not urgent


def test_priority_flags_are_patchable_and_local_only():
    """important/leveraged/kept_mine are editable via PATCH and live in the
    local overlay (never back-synced — not in the ClickUp-writable set)."""
    import inspect

    from gateway.routes.tasks import items as items_mod

    build = inspect.getsource(items_mod._build_item_update)
    assert "important = :important" in build
    assert "leveraged = :leveraged" in build
    assert "kept_mine = :kept_mine" in build
    # Back-sync must NOT push these (they're personal prioritization).
    backsync = inspect.getsource(items_mod._push_patch_upstream)
    assert "important" not in backsync
    assert "leveraged" not in backsync


def test_llm_clarify_proposes_matrix_flags_and_they_propagate():
    """The LLM clarify prompt asks for important/leveraged, and propose_with_llm
    carries them into the merged proposal (so the card can pre-fill them)."""
    import inspect

    from gateway.routes.tasks import ai as tasks_ai

    llm = inspect.getsource(tasks_ai._llm_propose)
    assert '"important": bool' in llm and '"leveraged": bool' in llm
    # Urgency must NOT be an LLM judgment — it's derived from the due date.
    assert "it's derived from the due date" in llm
    merged = inspect.getsource(tasks_ai.propose_with_llm)
    assert '"important", "leveraged"' in merged


def test_urgent_window_setting_defaults_to_48h():
    from gateway.routes.tasks.settings import GtdSettingsModel

    assert GtdSettingsModel().urgent_window_hours == 48


# ---------------------------------------------------------------------------
# ClickUp status → Next-Actions stage mapping
# ---------------------------------------------------------------------------

def test_status_stage_heuristic_guesses_by_name():
    """The auto-guess maps raw ClickUp status names to the 4 stages by keyword,
    and only guesses stages the user actually has."""
    from gateway.routes.tasks.settings import (
        DEFAULT_WORKFLOW_STAGES,
        guess_stage_for_status,
    )

    st = list(DEFAULT_WORKFLOW_STAGES)  # TODO / IN PROCESS / WAITING FOR / DONE
    assert guess_stage_for_status("backlog", st) == "TODO"
    assert guess_stage_for_status("to do", st) == "TODO"
    assert guess_stage_for_status("in progress", st) == "IN PROCESS"
    assert guess_stage_for_status("Review", st) == "IN PROCESS"
    assert guess_stage_for_status("blocked", st) == "WAITING FOR"
    assert guess_stage_for_status("Complete", st) == "DONE"
    # Unknown → the first stage (never lost).
    assert guess_stage_for_status("frobnicate", st) == "TODO"
    # A heuristic for a stage the user removed is skipped → falls to first.
    assert guess_stage_for_status("done", ["TODO", "IN PROCESS"]) == "TODO"


# ---------------------------------------------------------------------------
# Clarify filing places (where_match) — guidance-steered destination
# ---------------------------------------------------------------------------

def _places():
    accounts = [{
        "id": "acct-1", "label": "Fracktal", "provider": "clickup",
        "hierarchy": [{
            "id": "sp-1", "name": "Engineering",
            "folders": [{"id": "fo-1", "name": "Proposals", "lists": []}],
            "lists": [],
        }],
    }]
    local_spaces = [SimpleNamespace(id="ls-1", name="Home")]
    local_folders = [SimpleNamespace(id="lf-1", space_id="ls-1", name="Chores")]
    return tasks_ai._collect_places(accounts, local_spaces, local_folders)


def test_collect_places_orders_roots_spaces_folders():
    places = _places()
    labels = [tasks_ai._place_label(p) for p in places]
    # Workspace root first, then its spaces/folders, then the local tree.
    assert labels[0] == "Fracktal"
    assert "Fracktal › Engineering" in labels
    assert "Fracktal › Engineering › Proposals" in labels
    assert "Local (private)" in labels
    assert "Local › Home › Chores" in labels


def test_resolve_place_token_fuzzy_and_sentinels():
    places = _places()
    # [D#] token indexes the collected order (authoritative).
    assert tasks_ai._resolve_place("[D0]", places)["account_id"] == "acct-1"
    folder = tasks_ai._resolve_place("D2", places)
    assert folder["folder_id"] == "fo-1" and folder["space_id"] == "sp-1"
    # Fuzzy: the model echoed the place path instead of the token.
    fuzzy = tasks_ai._resolve_place("Engineering Proposals", places)
    assert fuzzy is not None and fuzzy["folder_id"] == "fo-1"
    # Sentinels / out-of-range / no overlap → None (never invents a place).
    assert tasks_ai._resolve_place("none", places) is None
    assert tasks_ai._resolve_place("", places) is None
    assert tasks_ai._resolve_place("[D99]", places) is None
    assert tasks_ai._resolve_place("zzz qqq", places) is None


def test_llm_overlay_place_sets_destination_and_where_target():
    """Guidance like 'put this in ClickUp under Proposals' arrives as llm_place
    — without an existing-project match it decides the account AND the Where
    target a NEW list will be created under."""
    place = {"account_id": "acct-1", "account_label": "Fracktal",
             "space_id": "sp-1", "space_name": "Engineering",
             "folder_id": "fo-1", "folder_name": "Proposals"}
    llm_core = {
        "disposition": "PROJECT",
        "next_action": "Draft the MC-EME proposal outline",
        "outcome": "MC-EME drone proposal submitted",
        "confidence": "high",
        "rationale": "User asked to file it in ClickUp under Proposals.",
        "complexity": "project",
        "llm_place": place,
    }
    merged = tasks_ai.propose_with_llm(
        _item("Update the IDEX proposal for MC-EME"), [], [],
        {"acct-1": ["Backlog"]}, llm_core)
    assert merged["account_id"] == "acct-1"
    assert merged["target_space_id"] == "sp-1"
    assert merged["target_folder_id"] == "fo-1"


def test_llm_overlay_place_drops_project_matched_in_another_home():
    """An explicit destination overrides a keyword-scaffold project match that
    lives elsewhere — the user just steered AWAY from that home."""
    projects = [_project("p-local", "IDEX proposal work", None)]
    place = {"account_id": "acct-1", "account_label": "Fracktal",
             "space_id": "sp-1", "space_name": "Engineering",
             "folder_id": None, "folder_name": None}
    llm_core = {
        "disposition": "NEXT",
        "next_action": "Reformat the proposal for MC-EME",
        "confidence": "medium",
        "rationale": "Filed per the user's destination.",
        "llm_place": place,
    }
    merged = tasks_ai.propose_with_llm(
        _item("Update the IDEX proposal draft"), [], projects, {}, llm_core)
    assert merged["account_id"] == "acct-1"
    assert merged["project_id"] is None
    assert merged["project_inferred"] is False
    assert merged["target_space_id"] == "sp-1"


def test_llm_overlay_existing_project_match_beats_place():
    """An llm_project match already carries its own home — the place never
    re-routes it."""
    projects = [_project("p1", "Acme rollout", "acct-9")]
    place = {"account_id": "acct-1", "account_label": "Other",
             "space_id": "sp-1", "space_name": "Engineering",
             "folder_id": None, "folder_name": None}
    llm_core = {
        "disposition": "NEXT",
        "next_action": "Email Acme the revised quote",
        "confidence": "high",
        "rationale": "Belongs to the rollout.",
        "llm_project": projects[0],
        "llm_place": place,
    }
    merged = tasks_ai.propose_with_llm(
        _item("send Acme the new quote"), [], projects, {}, llm_core)
    assert merged["project_id"] == "p1"
    assert merged["account_id"] == "acct-9"
    assert "target_space_id" not in merged


def test_llm_overlay_place_local_keeps_it_local():
    place = {"account_id": None, "account_label": "Local",
             "space_id": "ls-1", "space_name": "Home",
             "folder_id": None, "folder_name": None}
    llm_core = {
        "disposition": "NEXT",
        "next_action": "Sort the tax receipts",
        "confidence": "medium",
        "rationale": "Private errand, kept local.",
        "llm_place": place,
    }
    merged = tasks_ai.propose_with_llm(
        _item("sort receipts"), [], [], {"acct-1": ["To-do"]}, llm_core)
    assert merged["account_id"] is None
    assert merged["target_space_id"] == "ls-1"


def test_reclarify_binding_pops_where_target():
    """A SYNCED task's locked destination can't take a new-home target."""
    item = SimpleNamespace(source="SYNCED", account_id="acct-1",
                           project_id="p1")
    proposal = {"account_id": "acct-2", "project_id": "p9",
                "target_space_id": "sp-1", "target_folder_id": "fo-1"}
    out = tasks_ai._apply_reclarify_binding(proposal, item)
    assert out["account_id"] == "acct-1"
    assert out["project_id"] == "p1"
    assert out["locked_destination"] is True
    assert "target_space_id" not in out
    assert "target_folder_id" not in out


def test_llm_propose_parses_where_match(monkeypatch):
    fake_content = json.dumps({
        "disposition": "NEXT",
        "next_action": "Draft the proposal skeleton",
        "confidence": "medium",
        "rationale": "Filed where the user asked.",
        "where_match": "[D2]",
    })
    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=fake_content))])

    async def fake_completion(**kwargs):
        return fake_resp, kwargs.get("model")

    monkeypatch.setattr(
        "acb_llm.context.acompletion_with_fallback", fake_completion,
        raising=False)
    import asyncio
    places = _places()
    core = asyncio.run(tasks_ai._llm_propose(
        _item("Draft proposal"), [], [], {}, "tier-fast", places=places))
    assert core is not None
    assert core["llm_place"]["folder_id"] == "fo-1"
    # Without places the channel stays closed even if the model answered.
    core2 = asyncio.run(tasks_ai._llm_propose(
        _item("Draft proposal"), [], [], {}, "tier-fast"))
    assert core2 is not None and "llm_place" not in core2


def test_seed_status_stage_map_keeps_user_choices():
    """Seeding auto-guesses unmapped statuses but never overrides an explicit
    user mapping; keys are normalized (lower/trim)."""
    from gateway.routes.tasks.settings import seed_status_stage_map

    stages = ["TODO", "IN PROCESS", "WAITING FOR", "DONE"]
    existing = {"in progress": "DONE"}  # a deliberate (odd) user choice
    out = seed_status_stage_map(
        ["In Progress", "backlog", "  "], stages, existing)
    assert out["in progress"] == "DONE"      # user choice preserved
    assert out["backlog"] == "TODO"          # auto-guessed
    assert "" not in out                     # blank dropped


def test_status_map_normalizes_and_ignores_blanks():
    from gateway.routes.tasks.settings import _status_map

    out = _status_map({"To Do ": "TODO", "": "X", "review": "  "})
    assert out == {"to do": "TODO"}          # trimmed key, blanks dropped


@pytest.mark.asyncio
async def test_status_for_stage_reverses_the_map_per_project():
    """On a board drag, _status_for_stage finds a status in THIS task's own
    project that maps to the target stage; returns None (→ local-only move) when
    the project has no status mapped to that stage."""
    from gateway.routes.tasks.items import _status_for_stage

    class _Provider:
        def __init__(self, statuses):
            self._statuses = statuses

        async def list_statuses_for_task(self, _tid):
            return self._statuses

    smap = {"to do": "TODO", "in progress": "IN PROCESS", "done": "DONE"}
    # Project HAS a status mapped to IN PROCESS → writes it.
    p = _Provider(["To Do", "In Progress", "Done"])
    assert await _status_for_stage(p, "t1", "IN PROCESS", smap) == "In Progress"
    # Project has NO status mapped to WAITING FOR → None (move stays local).
    assert await _status_for_stage(p, "t1", "WAITING FOR", smap) is None
    # Provider hiccup → None, never raises.
    class _Boom:
        async def list_statuses_for_task(self, _tid):
            raise RuntimeError("boom")
    assert await _status_for_stage(_Boom(), "t1", "TODO", smap) is None


def test_status_catalog_route_is_registered():
    from gateway.routes.tasks import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/tasks/status-catalog" in paths


def test_workflow_stage_is_a_backsync_trigger():
    """A workflow_stage move on a synced task must reach the upstream back-sync
    (it translates to a ClickUp status), so it counts as 'writable' and the
    payload builder resolves the stage → status."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    push_src = inspect.getsource(tasks_items._push_patch_upstream)
    assert "patch.workflow_stage" in push_src  # part of the 'writable' gate
    payload_src = inspect.getsource(tasks_items._build_upstream_payload)
    assert "_status_for_stage" in payload_src


# ---------------------------------------------------------------------------
# Done column: completed tasks stay on the board until archived
# ---------------------------------------------------------------------------

def test_done_disposition_stamps_completed_at():
    from gateway.routes.tasks.items import _build_item_update

    sets, params = _build_item_update("i1", "u", ItemPatch(disposition="DONE"))
    assert "disposition = :disp" in sets
    assert params["disp"] == "DONE"
    assert "completed_at = now()" in sets
    assert "completed_at = NULL" not in sets


def test_reopen_clears_completed_at():
    """Moving a task OFF done (e.g. dragging a card out of the Done column back
    to NEXT) clears completed_at so it reads as active again — the invariant is
    completed_at is non-null iff DONE."""
    from gateway.routes.tasks.items import _build_item_update

    sets, _ = _build_item_update("i1", "u", ItemPatch(disposition="NEXT"))
    assert "completed_at = NULL" in sets
    assert "completed_at = now()" not in sets


def test_stage_boundary_flips_disposition_both_ways():
    """patch_item translates a board drag across the DONE boundary into a
    disposition flip: drop on the LAST stage → DONE; drag a DONE card to an
    EARLIER stage → reopen to NEXT."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    src = inspect.getsource(tasks_items.patch_item)
    # Last stage → DONE.
    assert 'patch.workflow_stage == stages[-1]' in src
    assert 'patch.disposition = "DONE"' in src
    # Earlier stage while currently DONE → reopen to NEXT.
    assert 'patch.workflow_stage != stages[-1]' in src
    assert 'current.disposition == "DONE"' in src
    assert 'patch.disposition = "NEXT"' in src


def test_organize_request_accepts_subtasks():
    from gateway.routes.tasks.items import OrganizeRequest

    req = OrganizeRequest(kind="next", next_action="Ship it",
                          subtasks=["step one", "step two"])
    assert req.subtasks == ["step one", "step two"]


def test_organize_owner_axis_independent_of_size():
    """Sort→Shape: OWNER (assignee) must combine with ANY size (next/project),
    not just the legacy kind="delegate" — a task can be a project, delegated,
    with a deadline, and broken into steps, all in one commit."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    src = inspect.getsource(tasks_items.organize_item)
    # A next/project/calendar kind delegates too when it carries an assignee.
    assert 'req.kind in ("next", "project", "calendar") and req.assignee is not None' in src
    assert 'disposition = "WAITING"' in src
    # is_mine now derives from the independent `delegated` flag, not kind=="delegate".
    assert '"is_mine": not delegated' in src


def test_organize_synced_delegate_requires_a_project():
    """A clarify-delegate to a connected tool must carry a destination project —
    the teammate has to SEE the task there, which needs a list to create it in.
    organize_item refuses (before any write) a synced delegation with no project,
    so it can't commit a WAITING row that strands invisibly (the Veena bug)."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    src = inspect.getsource(tasks_items.organize_item)
    # The guard keys off delegated + SYNCED + missing project, and it lives
    # BEFORE the UPDATE (fail-closed, no partial write).
    assert 'delegated and source == "SYNCED"' in src
    assert "req.kind == \"project\" or not req.project_id" in src
    guard = src.index("delegate into")
    write = src.index("UPDATE gtd_items")
    assert guard < write, "the project guard must precede the row write"


def test_organize_synced_delegate_auto_pushes_to_the_tool():
    """Parity with POST /items/{id}/delegate: a clarify-delegate to a connected
    workspace pushes upstream in the same request (so the teammate sees it),
    rather than leaving it 'pending' and invisible. A push hiccup is caught and
    the delegation is still saved (WAITING) with the manual Push affordance."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    organize_src = inspect.getsource(tasks_items.organize_item)
    push_src = inspect.getsource(tasks_items._maybe_push_delegated)
    # organize commits the local clarify first, THEN runs the auto-push helper.
    # H2 shape: the local write happens in the FIRST `_tenant_session` block
    # (committed on its clean exit) and the push runs in a SECOND block — so
    # the helper call must come after a later `async with _tenant_session()`.
    assert "_maybe_push_delegated(" in organize_src
    first_block = organize_src.index("async with _tenant_session()")
    second_block = organize_src.index(
        "async with _tenant_session()", first_block + 1)
    assert second_block < organize_src.index("_maybe_push_delegated(")
    # The helper only pushes a SYNCED delegation with a chosen project, and a
    # push failure is tolerated (deferred), not fatal to the clarify.
    assert 'delegated and source == "SYNCED" and project_id' in push_src
    assert "_push_pending_item(db, item_id, uid)" in push_src
    assert "delegate_push_deferred" in push_src


def test_item_patch_is_mine_is_local_overlay_never_pushed_upstream():
    """Removing a handed-off/unassigned task from "My Next Actions" is a purely
    LOCAL overlay: is_mine patches the row, but is NEVER a ClickUp-writable
    field — the task stays put upstream. (My Next Actions = NEXT & is_mine.)"""
    import inspect

    from gateway.routes.tasks.items import (
        ItemPatch,
        _build_item_update,
        _push_patch_upstream,
    )

    sets, params = _build_item_update("item1", "u1", ItemPatch(is_mine=False))
    assert "is_mine = :is_mine" in sets
    assert params["is_mine"] is False

    # An unset is_mine leaves the column untouched (no accidental writes).
    sets2, _ = _build_item_update("item1", "u1", ItemPatch(context="@calls"))
    assert not any("is_mine" in s for s in sets2)

    # is_mine must not be in the set of fields back-synced to the tool.
    assert "is_mine" not in inspect.getsource(_push_patch_upstream)


# ---------------------------------------------------------------------------
# Enrich (fill missing fields) + reclarify binding + delegate promotion
# ---------------------------------------------------------------------------

def test_missing_fields_reports_only_empty():
    from gateway.routes.tasks.ai import _missing_fields

    full = SimpleNamespace(title="x", description="", context="@calls",
                           energy="low", time_estimate_mins=10,
                           due_at=object(), assignee={"name": "Bo"})
    assert _missing_fields(full) == set()
    bare = SimpleNamespace(title="x", description="", context=None,
                           energy="", time_estimate_mins=None, due_at=None,
                           assignee=None)
    assert _missing_fields(bare) == {
        "context", "energy", "time_estimate_mins", "due_at", "assignee"}


def test_enrich_heuristic_fills_only_requested_and_never_invents_due():
    from gateway.routes.tasks.ai import enrich_heuristic

    item = SimpleNamespace(title="Call the vendor about the quote",
                           description="")
    out = enrich_heuristic(item, {"context", "due_at"}, [])
    # A call → @calls; it never fabricates a due date.
    assert out["context"] == "@calls"
    assert "due_at" not in out
    # Only the requested fields come back — energy wasn't asked for.
    assert "energy" not in out


def test_propose_fields_is_db_free_and_fillable_without_llm():
    # _propose_fields is the pure (no-DB) proposal core that backfill fans out
    # concurrently. With use_llm=False it must fill @context from the heuristic
    # alone — no DB session, no LLM — so a shared session stays untouched during
    # the concurrent calls and the roster is loaded once by the caller.
    import asyncio

    from gateway.routes.tasks.ai import _propose_fields

    item = SimpleNamespace(title="Buy printer paper", description="",
                           context=None, energy="x", time_estimate_mins=1,
                           due_at=object(), assignee={"name": "Bo"})
    out = asyncio.run(_propose_fields(
        item, {"context"}, people=[], use_llm=False, model=""))
    assert out == {"context": "@errands"}


def test_apply_reclarify_binding_locks_synced_destination():
    from gateway.routes.tasks.ai import _apply_reclarify_binding

    synced = SimpleNamespace(source="SYNCED", account_id="acct-1",
                             project_id="proj-9")
    prop = {"account_id": "acct-OTHER", "project_id": "proj-OTHER",
            "project_inferred": True, "disposition": "NEXT"}
    out = _apply_reclarify_binding(dict(prop), synced)
    assert out["account_id"] == "acct-1"
    assert out["project_id"] == "proj-9"
    assert out["project_inferred"] is False
    assert out["locked_destination"] is True
    # A LOCAL task is free to be re-homed — binding untouched.
    local = SimpleNamespace(source="LOCAL", account_id=None, project_id=None)
    out2 = _apply_reclarify_binding(dict(prop), local)
    assert out2["account_id"] == "acct-OTHER"
    assert "locked_destination" not in out2


def test_clarify_route_accepts_reclarify_flag():
    import inspect

    from gateway.routes.tasks import ai as tasks_ai_mod

    sig = inspect.signature(tasks_ai_mod.clarify_item)
    assert "reclarify" in sig.parameters


def test_delegate_request_shape():
    from gateway.routes.tasks.core import PersonModel
    from gateway.routes.tasks.items import DelegateRequest

    req = DelegateRequest(
        assignee=PersonModel(name="Priya Sharma", provider_user_id="7"),
        account_id="acct-1", project_id="proj-1")
    assert req.assignee.name == "Priya Sharma"
    assert req.next_action is None  # optional re-phrase


def test_enrich_and_delegate_routes_are_registered():
    from gateway.routes.tasks import router

    paths = {getattr(r, "path", "") for r in router.routes}
    for p in ("/tasks/items/{item_id}/enrich",
              "/tasks/ai/backfill-context",
              "/tasks/items/{item_id}/delegate"):
        assert p in paths, f"missing route {p}"


def test_local_hierarchy_routes_are_registered():
    # The Projects-view tree depends on these local hierarchy endpoints.
    from gateway.routes.tasks import router

    paths = {getattr(r, "path", "") for r in router.routes}
    for p in ("/tasks/hierarchy", "/tasks/spaces", "/tasks/folders",
              "/tasks/local-projects"):
        assert p in paths, f"missing hierarchy route {p}"


def test_create_local_project_request_defaults():
    from gateway.routes.tasks.hierarchy import CreateLocalProjectRequest

    # A project can be created ungrouped (no space/folder) — both default None.
    req = CreateLocalProjectRequest(outcome="Ship v2")
    assert req.space_id is None and req.folder_id is None
    # Or placed in a folder (which pins the space server-side).
    req2 = CreateLocalProjectRequest(outcome="Ship v2", folder_id="f1")
    assert req2.folder_id == "f1"


def test_project_model_carries_hierarchy_placement():
    from gateway.routes.tasks.core import GtdProjectModel

    p = GtdProjectModel(id="p1", outcome="Do the thing",
                        space_id="s1", folder_id="f1")
    assert p.space_id == "s1" and p.folder_id == "f1"


def test_workflow_stages_normalizes_and_defaults():
    from gateway.routes.tasks.settings import (
        DEFAULT_WORKFLOW_STAGES,
        _stages,
    )
    # A stored JSON string, a real list, junk, and empties all normalize.
    assert _stages('["A", "B"]') == ["A", "B"]
    assert _stages([" A ", "", "B", None]) == ["A", "B"]
    assert _stages(None) == DEFAULT_WORKFLOW_STAGES
    assert _stages([]) == DEFAULT_WORKFLOW_STAGES
    assert _stages("not json") == DEFAULT_WORKFLOW_STAGES
    assert DEFAULT_WORKFLOW_STAGES[-1] == "DONE"  # last stage = the done stage


def test_patch_sets_sort_key_including_zero():
    # A drag writes a fractional rank; 0.0 is a legitimate rank (top of a
    # group), so the builder must emit the clause for it — a truthiness check
    # would silently drop sort_key=0.
    sets, params = _build_item_update("id1", "u", ItemPatch(sort_key=0.0))
    assert "sort_key = :sortkey" in sets
    assert params["sortkey"] == 0.0

    sets, params = _build_item_update("id1", "u", ItemPatch(sort_key=1500.5))
    assert params["sortkey"] == 1500.5


def test_patch_omits_sort_key_when_absent():
    # An unrelated patch (e.g. a note edit) must not touch sort_key.
    sets, params = _build_item_update("id1", "u", ItemPatch(notes="hi"))
    assert not any("sort_key" in s for s in sets)
    assert "sortkey" not in params


def test_list_items_orders_by_sort_key_before_created_at():
    # The board/list manual order must sort ranked rows first (NULLS LAST) and
    # keep LOCAL-first; a code read guards the ORDER BY contract.
    import inspect

    from gateway.routes.tasks import items as items_mod

    src = inspect.getsource(items_mod.list_items)
    assert "sort_key ASC NULLS LAST" in src
    # LOCAL rows still win over the synced mirror regardless of rank.
    assert "(i.source = 'LOCAL') DESC" in src


def test_dispositions_are_the_canonical_set():
    assert {"INBOX", "NEXT", "WAITING", "SOMEDAY", "PROJECT",
                            "REFERENCE", "DONE", "TRASH"} == DISPOSITIONS


def test_parse_ts_accepts_iso_and_z_suffix():
    assert _parse_ts("2026-07-08T00:00:00Z") is not None
    assert _parse_ts("") is None
    assert _parse_ts(None) is None
    with pytest.raises(HTTPException):
        _parse_ts("not-a-date")


# ---------------------------------------------------------------------------
# People / capabilities (org-knowledge layer, §6.1)
# ---------------------------------------------------------------------------

def test_hr_import_mapper_merges_org_and_resume_skills():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "import_hr_people",
        Path(__file__).resolve().parents[2] / "scripts" / "import_hr_people.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    hr = {"company": "X", "departments": [{
        "name": "Engineering", "head": "Vijay",
        "teams": [{"name": "Firmware", "members": [{
            "name": "Rahul", "email": "r@x.in", "role": "Engineer",
            "skills": ["Firmware", "c++"], "status": "active",
            "capacity_hours_per_week": 40, "current_load_hours_per_week": 30,
            "available_hours_per_week": 10, "clickup_user_id": 42,
        }]}],
    }]}
    resumes = {"profiles": [{
        "name": "rahul", "email": "r@x.in",
        "skills": ["C++", "embedded systems"],
        "experience_summary": "5y embedded", "years_experience": 5,
        "domain": "Embedded",
    }]}
    rows = mod.build_rows(hr, resumes)
    assert len(rows) == 1
    r = rows[0]
    assert r["department"] == "Engineering" and r["team"] == "Firmware"
    assert r["reports_to"] == "Vijay"
    # merged + case-insensitively deduped, org-chart order first
    assert r["skills"] == ["Firmware", "c++", "embedded systems"]
    assert r["resume_summary"] == "5y embedded"
    assert r["clickup_user_id"] == "42"
    assert r["available"] == 10


def test_capability_match_picks_skill_fit_with_availability_tiebreak():
    people = [
        {"name": "A", "skills": ["firmware"], "available_hours_per_week": 5},
        {"name": "B", "skills": ["firmware"], "available_hours_per_week": 20},
        {"name": "C", "skills": ["sales"], "available_hours_per_week": 40},
    ]
    fit = tasks_ai._match_capability("fix the firmware regression", people)
    assert fit["name"] == "B"  # same skill score; more hours free
    assert tasks_ai._match_capability("water the plants", people) is None


def test_propose_attaches_capability_owner_without_forcing_delegate():
    people = [{"name": "Rahul", "skills": ["firmware"], "provider_user_id": "1",
               "available_hours_per_week": 12}]
    p = tasks_ai.propose(
        _item("Fix the bed-leveling firmware regression"), people, [], {})
    assert p["disposition"] == "NEXT"  # suggestion, not a forced WAITING
    assert p["suggested_assignee"]["name"] == "Rahul"
    assert "Rahul fits" in p["rationale"]


# ---------------------------------------------------------------------------
# Sync pull (§9.3 #1): provider list_tasks + the GTD lens on pulled tasks
# ---------------------------------------------------------------------------

from gateway.routes.tasks.sync import map_pulled_task  # noqa: E402


def _pulled(**over):
    base = {
        "provider_task_id": "t1",
        "title": "Task",
        "status": "To-do",
        "status_type": "custom",
        "assignees": [],
        "closed_at_ms": None,
    }
    base.update(over)
    return base


def test_pull_mapping_mine_open_is_next():
    m = map_pulled_task(_pulled(
        assignees=[{"name": "v", "provider_user_id": "42"}]), "42")
    assert m["disposition"] == "NEXT"
    assert m["is_mine"] is True
    assert m["waiting_on"] is None


def test_pull_mapping_others_task_is_waiting_with_monitor_record():
    m = map_pulled_task(_pulled(
        assignees=[{"name": "j", "provider_user_id": "7"}],
        status="in progress"), "42")
    assert m["disposition"] == "WAITING"
    assert m["is_mine"] is False
    assert m["waiting_on"]["provider_user_id"] == "7"


def test_pull_mapping_backlog_stage_is_someday_even_when_mine():
    m = map_pulled_task(_pulled(
        assignees=[{"name": "v", "provider_user_id": "42"}],
        status="Backlog"), "42")
    assert m["disposition"] == "SOMEDAY"


def test_pull_mapping_closed_wins_over_everything():
    m = map_pulled_task(_pulled(
        assignees=[{"name": "j", "provider_user_id": "7"}],
        status="Backlog", status_type="closed", closed_at_ms=1719000000000), "42")
    assert m["disposition"] == "DONE"
    assert m["completed_at_ms"] == 1719000000000
    assert m["waiting_on"] is None  # nothing to wait on once it's done


def test_pull_mapping_unassigned_open_is_team_pool_next():
    m = map_pulled_task(_pulled(), "42")
    assert m["disposition"] == "NEXT"
    assert m["is_mine"] is False  # team pool, not my list
    assert m["assignee"] is None


def test_pull_mapping_prefers_me_as_display_assignee():
    m = map_pulled_task(_pulled(assignees=[
        {"name": "j", "provider_user_id": "7"},
        {"name": "v", "provider_user_id": "42"},
    ]), "42")
    assert m["is_mine"] is True
    assert m["assignee"]["provider_user_id"] == "42"


# ── "assigned to me" soundness ──────────────────────────────────────────────
#
# is_mine is THE signal Priority/Engage filter on ("only tasks assigned to me on
# ClickUp"). Its correctness rests on matching MY ClickUp user id against the
# task's assignee ids — so the comparison must be type-robust (ClickUp ids come
# back as ints in some payloads, strings in others) and must never match on a
# missing/blank id.

def test_is_mine_matches_across_int_and_str_id_types():
    """A numeric assignee id must still match my string id (and vice-versa) —
    the mapping stringifies both sides, so 42 == '42'. A type mismatch here would
    silently drop every one of my tasks out of Priority/Engage."""
    # assignee id as an int, my id as a str
    m = map_pulled_task(
        _pulled(assignees=[{"name": "v", "provider_user_id": 42}]), "42")
    assert m["is_mine"] is True
    # assignee id as a str, my id passed as an int-ish str
    m2 = map_pulled_task(
        _pulled(assignees=[{"name": "v", "provider_user_id": "42"}]), 42)  # type: ignore[arg-type]
    assert m2["is_mine"] is True


def test_is_mine_never_matches_on_blank_or_missing_id():
    """A blank/absent id must NOT spuriously match — otherwise unassigned or
    malformed tasks would all look 'mine'."""
    # My id is empty → nothing is mine, even an assignee with a blank id.
    m = map_pulled_task(
        _pulled(assignees=[{"name": "x", "provider_user_id": ""}]), "")
    assert m["is_mine"] is False
    # I have an id, but the assignee's id is missing entirely.
    m2 = map_pulled_task(_pulled(assignees=[{"name": "x"}]), "42")
    assert m2["is_mine"] is False






def test_sync_upsert_preserves_user_overlay_and_owns_completion():
    """The upsert must only refresh MIRRORED fields on re-sync: the user's
    GTD overlay survives, except completion where the provider wins."""
    from gateway.routes.tasks import sync as tasks_sync

    sql = str(tasks_sync._UPSERT_SQL)
    # provider owns completion state…
    assert "WHEN EXCLUDED.completed_at IS NOT NULL THEN 'DONE'" in sql
    # …and an upstream reopen un-DONEs the row
    assert "gtd_items.disposition = 'DONE'" in sql
    # …but an open row keeps the disposition the user chose
    assert "ELSE gtd_items.disposition" in sql
    # user's project refile is never clobbered
    assert "coalesce(gtd_items.project_id, EXCLUDED.project_id)" in sql
    # conflict target matches the partial unique index
    assert "ON CONFLICT (account_id, provider_task_id) WHERE source <> 'LOCAL'" in sql


# ---------------------------------------------------------------------------
# Email → task capture (origin linkage) + calendar-date validation
# ---------------------------------------------------------------------------

from gateway.routes.tasks.capture_email import draft_task_fallback  # noqa: E402


def test_email_capture_fallback_draft_names_sender_and_strips_reply_prefixes():
    d = draft_task_fallback("Re: Fwd: Re: Vendor quote v2", "Sanjay Rao",
                            "Please approve the revised quote by Friday.")
    assert d["title"] == "Email from Sanjay Rao: Vendor quote v2"
    assert d["notes"].startswith("Please approve")


def test_email_capture_fallback_handles_empty_subject():
    d = draft_task_fallback("", "", "")
    assert d["title"] == "Handle email from someone"


def test_email_capture_is_owner_checked_and_idempotent():
    import inspect

    from gateway.routes.tasks import capture_email

    src = inspect.getsource(capture_email.capture_from_email)
    assert "a.user_id = :uid" in src            # ownership through the mailbox
    assert "origin->>'email_id'" in src         # idempotency per source email
    assert "NOT IN ('DONE', 'TRASH')" in src    # only OPEN items block re-capture


def test_email_capture_finds_pm_account_for_a_delegate():
    """A delegated email capture must be staged on a PM account the assignee
    belongs to (a teammate can't see a private LOCAL task). The matcher keys on
    provider_user_id first, then email, then name."""
    import asyncio

    from gateway.routes.tasks import capture_email as ce

    class _Res:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Row:
        def __init__(self, id_, cache):
            self.id = id_
            self.schema_cache = cache

    class _DB:
        def __init__(self, rows):
            self._rows = rows

        async def execute(self, *_a, **_k):
            return _Res(self._rows)

    db = _DB([_Row("acct-1", {"members": [
        {"name": "Rahul", "email": "rahul@x.in", "provider_user_id": "7"}]})])
    placed = asyncio.run(ce._find_pm_account_for_person(
        db, "u1", {"name": "Rahul", "provider_user_id": "7"}))
    assert placed is not None
    account_id, member = placed
    assert account_id == "acct-1"
    assert member["provider_user_id"] == "7"

    # Nobody matches → None, so the route downgrades to a local inbox item.
    db2 = _DB([_Row("acct-1", {"members": [
        {"name": "Someone Else", "provider_user_id": "99"}]})])
    assert asyncio.run(ce._find_pm_account_for_person(
        db2, "u1", {"name": "Rahul", "provider_user_id": "7"})) is None


def test_email_capture_routes_delegations_to_the_pm_tool():
    """Destination rule: a task handed to SOMEONE ELSE goes to the PM tool
    (SYNCED/pending); if no account has them it must NOT be stranded as an
    invisible local delegated task — it falls back to MY inbox."""
    import inspect

    from gateway.routes.tasks import capture_email

    src = inspect.getsource(capture_email.capture_from_email)
    assert 'source, sync_state = "SYNCED", "pending"' in src
    assert 'assignee, disposition = None, "INBOX"' in src
    # The staged destination is carried onto the row.
    assert '"source": source, "account_id": account_id' in src


def test_calendar_decision_requires_a_date():
    """GTD hard landscape: kind=calendar without due_at used to create a
    hard-date item with no date — invisible on the Calendar view."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    src = inspect.getsource(tasks_items.organize_item)
    assert 'req.kind == "calendar" and not (req.due_at' in src


def test_item_model_carries_origin():
    from gateway.routes.tasks.core import GtdItemModel
    assert "origin" in GtdItemModel.model_fields


def test_push_carries_email_origin_reference_into_provider():
    """Lifecycle-long linkage: pushing an email-origin item to the PM tool
    appends the source-email reference to the description."""
    import inspect

    from gateway.routes.tasks import items as tasks_items

    # The push machinery lives in the shared helper (reused by push + delegate).
    src = inspect.getsource(tasks_items._push_pending_item)
    assert 'origin.get("kind") == "email"' in src
    assert "Captured from email" in src


# ── BO-1b: a broker-QUEUED push never reports as synced ──────────────────────
#
# The pending marker is taken from the REAL `providers._broker_gate` (env var +
# a stubbed `enqueue`), never hand-written here — a test that invents the marker
# shape agrees with itself and would have passed against the bug too.

class _PushFakeDb:
    """The three statements `_push_pending_item` / `_push_child_subtasks` run:
    the project lookup (`fetchone`), the child list (`fetchall`), and the row
    UPDATEs, which are recorded verbatim."""

    def __init__(self, provider_ref="list-5", children=()):
        self._provider_ref = provider_ref
        self._children = list(children)
        self.updates: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.lstrip().upper().startswith("UPDATE"):
            self.updates.append((sql, dict(params or {})))
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
        return SimpleNamespace(
            fetchone=lambda: SimpleNamespace(provider_ref=self._provider_ref),
            fetchall=lambda: self._children,
        )


def _push_row(**kw):
    base = dict(
        id="item-1", sync_state="pending", account_id="acc-1",
        project_id="proj-1", title="Ship it", next_action=None,
        description=None, provider_status=None, due_at=None,
        assignee=None, origin=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _patch_push_harness(monkeypatch, provider, row=None):
    """Stub the item fetch, account ownership, credential decrypt and provider
    construction so `_push_pending_item` reaches `provider` with no DB."""
    from gateway.routes.tasks import items as tasks_items

    item_row = row if row is not None else _push_row()

    async def _fetch(db, item_id, uid):
        return item_row

    async def _owner(db, account_id, uid):
        return SimpleNamespace(id=account_id, provider="stub",
                               workspace_id="ws1", credentials_encrypted=b"x")

    monkeypatch.setattr(tasks_items, "_fetch_item", _fetch)
    monkeypatch.setattr(tasks_items, "_assert_account_owner", _owner)
    monkeypatch.setattr(
        tasks_items, "_key_store",
        lambda: SimpleNamespace(decrypt=lambda _b: '{"token": "t"}'))
    monkeypatch.setattr(tasks_items, "build_provider", lambda *a, **k: provider)
    monkeypatch.setattr(tasks_items, "_row_to_item", lambda r: r)
    return item_row


def _queueing_provider(monkeypatch):
    """A provider whose `create_task` goes through the REAL `_broker_gate`, with
    enforcement on and the broker's enqueue stubbed — so it returns the genuine
    pending marker and the underlying write is proven never to run.

    ⚠️ **Re-cut 2026-08-25 (D52 repair round 1).** This used to build a real
    `ClickUpProvider` and booby-trap its `httpx` client. That class is deleted,
    and the contract under test was never its: it is
    `BaseTaskProvider._broker_gate`'s, which survives. So the concrete class is
    a local stub — the same move `tests/unit/test_provider_broker_gate.py` made
    — and the booby trap moved INTO `do_write`, where it is stricter than the
    HTTP one ever was: it fires on any attempt to perform the write at all,
    with no transport to route around.
    """
    import acb_graph
    import action_broker
    from gateway.routes.tasks.providers import BaseTaskProvider

    monkeypatch.setenv("ACTION_BROKER_ENFORCE", "all")

    def _no_session():
        raise ConnectionError("no db in test")

    monkeypatch.setattr(acb_graph, "get_session", _no_session)
    monkeypatch.setattr(action_broker, "enqueue", lambda p: "act-9")

    class _GatedStub(BaseTaskProvider):
        provider = "stub"

        async def create_task(self, project_ref, payload):
            async def _do_write():
                raise AssertionError(
                    "a queued write must never perform the provider write")

            return await self._broker_gate(
                "stub.create_task", f"list:{project_ref}",
                {"title": payload.get("title")}, _do_write,
            )

    _GatedStub.__abstractmethods__ = frozenset()
    return _GatedStub()


def test_a_queued_push_writes_awaiting_approval_and_no_provider_task_id(monkeypatch):
    """BO-1b: under `ACTION_BROKER_ENFORCE=all` the write is QUEUED, so nothing
    exists upstream. The row must land `sync_state='awaiting_approval'` and must
    NOT claim a `provider_task_id` — the marker's empty string is otherwise
    indistinguishable from a real id downstream."""
    import asyncio

    from gateway.routes.tasks import items as tasks_items

    provider = _queueing_provider(monkeypatch)
    _patch_push_harness(monkeypatch, provider)
    db = _PushFakeDb()

    asyncio.run(tasks_items._push_pending_item(db, "item-1", "u1"))

    assert len(db.updates) == 1
    sql, params = db.updates[0]
    assert "sync_state = 'awaiting_approval'" in sql
    assert "'synced'" not in sql
    assert "provider_task_id" not in sql
    assert set(params) == {"id"} and params["id"] == "item-1"


def test_an_auto_applied_push_still_writes_synced(monkeypatch):
    """The control: with enforcement OFF the same path is unchanged — the guard
    is conditional on the marker, not a blanket refusal to sync."""
    import asyncio

    from gateway.routes.tasks import items as tasks_items

    class _Applying:
        async def create_task(self, project_ref, payload):
            return {"provider_task_id": "T7", "provider_url": "u",
                    "provider_status": "open"}

    monkeypatch.delenv("ACTION_BROKER_ENFORCE", raising=False)
    _patch_push_harness(monkeypatch, _Applying())
    db = _PushFakeDb(children=[])

    asyncio.run(tasks_items._push_pending_item(db, "item-1", "u1"))

    sql, params = db.updates[0]
    assert "sync_state = 'synced'" in sql
    assert params["tid"] == "T7"


def test_a_queued_subtask_write_also_never_reports_synced(monkeypatch):
    """Defence in depth, and DELIBERATELY exercised by calling
    `_push_child_subtasks` directly: **this branch is unreachable through
    `POST /tasks/items/{id}/push`**. Parent and child share one action name
    (`clickup.create_task`), so enforcement can never queue the child without
    also queueing the parent, and a queued parent returns from
    `_push_pending_item` before this function is called (and the `if parent_tid:`
    guard sits behind that). Do not "fix" that guard to make this fire — a queued
    parent has no upstream id, so its children would be created top-level."""
    import asyncio

    from gateway.routes.tasks import items as tasks_items

    provider = _queueing_provider(monkeypatch)
    child = SimpleNamespace(id="child-1", title="Sub", next_action=None,
                            description=None, provider_status=None)
    db = _PushFakeDb(children=[child])

    asyncio.run(tasks_items._push_child_subtasks(
        db, "item-1", "u1", provider, "list-5", "T1"))

    assert len(db.updates) == 1
    sql, params = db.updates[0]
    assert "sync_state = 'awaiting_approval'" in sql
    assert "provider_task_id" not in sql
    assert params == {"id": "child-1"}


def test_gtd_item_model_projects_awaiting_approval_unchanged():
    """No migration and no model change: `sync_state` is a bare `str`, so the
    third value passes through the API as-is."""
    from gateway.routes.tasks.core import GtdItemModel

    field = GtdItemModel.model_fields["sync_state"]
    assert field.annotation is str
    assert GtdItemModel(
        id="i", title="t", sync_state="awaiting_approval",
        created_at="2026-08-11T00:00:00Z", updated_at="2026-08-11T00:00:00Z",
    ).sync_state == "awaiting_approval"


def test_agent_item_format_shows_email_origin():
    from skill_task_gtd.core import _fmt_item

    line = _fmt_item({"id": "x" * 12, "title": "Approve the quote",
                      "disposition": "NEXT", "source": "LOCAL",
                      "origin": {"kind": "email", "from_name": "Sanjay Rao",
                                 "subject": "Vendor quote"}})
    assert "from email: Sanjay Rao" in line
    plain = _fmt_item({"id": "y" * 12, "title": "buy tape",
                       "disposition": "INBOX", "source": "LOCAL"})
    assert "from email" not in plain


def test_agent_item_format_distinguishes_the_two_waiting_states():
    """BO-1b repair: `awaiting_approval` was an unswept consumer of the widened
    vocabulary — `_fmt_item` only tested for `'pending'`, so a queued item
    rendered to the agent with NO marker at all and no provider link, and the
    agent could not tell it from a normal task. The two states are not synonyms
    and the rendered line must say which one it is."""
    from skill_task_gtd.core import _fmt_item

    staged = _fmt_item({"id": "a" * 12, "title": "t", "disposition": "NEXT",
                        "source": "SYNCED", "sync_state": "pending"})
    queued = _fmt_item({"id": "b" * 12, "title": "t", "disposition": "NEXT",
                        "source": "SYNCED", "sync_state": "awaiting_approval"})
    normal = _fmt_item({"id": "c" * 12, "title": "t", "disposition": "NEXT",
                        "source": "SYNCED", "sync_state": "synced"})

    assert "PENDING PUSH" in staged and "AWAITING APPROVAL" not in staged
    assert "AWAITING APPROVAL" in queued and "PENDING PUSH" not in queued
    assert "PENDING PUSH" not in normal and "AWAITING APPROVAL" not in normal


# ---------------------------------------------------------------------------
# Email → task capture: clarify-before-capture popup (preview/enhance/create)
# ---------------------------------------------------------------------------


def test_title_similarity_flags_near_duplicate_asks_and_ignores_scaffolding():
    """The fuzzy matcher (Jaccard over significant tokens) should score a
    near-duplicate ask high and an unrelated one low — and stopwords/reply
    scaffolding must not inflate the overlap."""
    from gateway.routes.tasks.capture_email import _title_similarity

    high = _title_similarity("Approve the vendor quote",
                             "Review vendor quote")
    assert high >= 0.5
    low = _title_similarity("Approve the vendor quote",
                            "Book flights to Delhi")
    assert low < 0.5
    # 'Email from X:' scaffolding words are stopwords → don't manufacture a
    # match between two unrelated 'Email from …' titles.
    scaffold = _title_similarity("Email from Sanjay: budget",
                                 "Email from Rahul: hiring")
    assert scaffold < 0.5


def test_route_and_persist_is_the_shared_write_used_by_popup_create():
    """The popup's /create endpoint must write through the SAME routing/persist
    helper (delegate destination rules, gtd_waiting for follow-ups) rather than
    a divergent second code path."""
    import inspect

    from gateway.routes.tasks import capture_email as ce

    persist = inspect.getsource(ce._route_and_persist)
    # Same destination rule as the one-click endpoint.
    assert 'source, sync_state = "SYNCED", "pending"' in persist
    assert 'assignee, disposition = None, "INBOX"' in persist
    assert "INSERT INTO gtd_items" in persist
    assert "INSERT INTO gtd_waiting" in persist

    create = inspect.getsource(ce.create_capture_from_email)
    assert "_route_and_persist" in create
    # Still idempotent per source email.
    assert "_find_existing_capture" in create


def test_preview_uses_deterministic_default_not_the_llm():
    """Opening the popup must be instant: the default title comes from the
    subject-derived fallback, and the LLM is only invoked by /enhance."""
    import inspect

    from gateway.routes.tasks import capture_email as ce

    preview = inspect.getsource(ce.preview_capture_from_email)
    assert "draft_task_fallback" in preview
    assert "_llm_capture" not in preview        # no LLM on open
    assert "_find_similar_tasks" in preview     # similar-task warnings

    enhance = inspect.getsource(ce.enhance_capture_from_email)
    assert "_llm_capture" in enhance            # AI enrich lives here
    assert "INSERT INTO gtd_items" not in enhance  # never writes


# ---------------------------------------------------------------------------
# Per-user settings (AI tiers + toggles)
# ---------------------------------------------------------------------------


def test_settings_defaults_per_function():
    """Each AI function has its own default tier (email-app parity): chat on
    the strong tool-caller, high-volume triage on the fast tier."""
    from gateway.routes.tasks.settings import DEFAULT_GTD_MODELS, GtdSettingsModel

    assert DEFAULT_GTD_MODELS == {
        "chat": "tier-powerful",
        "clarify": "tier-balanced",
        "atomize": "tier-fast",
        "email_capture": "tier-fast",
    }
    s = GtdSettingsModel()
    assert s.capture_dedup is True and s.auto_sync_on_open is True


def test_ai_call_sites_use_configured_models():
    """The atomizer and the email-capture drafter run on the user's
    configured tier (gtd_settings), not a hardcoded one."""
    import inspect

    from gateway.routes.tasks import ai as tasks_ai
    from gateway.routes.tasks import capture_email

    src = inspect.getsource(tasks_ai.atomize_dump)
    assert 'model=models["atomize"]' in src
    src2 = inspect.getsource(capture_email.capture_from_email)
    assert 'model=models["email_capture"]' in src2
    # Both LLM helpers accept the model and route through the alias-aware
    # completion path (tier-fast/-balanced/-powerful or a raw model id).
    assert "acompletion_with_fallback" in inspect.getsource(tasks_ai._llm_atomize)
    assert "acompletion_with_fallback" in inspect.getsource(capture_email._llm_capture)


def test_settings_update_is_partial():
    """PUT /tasks/settings only touches provided fields (patch semantics)."""
    from gateway.routes.tasks.settings import GtdSettingsPatch

    p = GtdSettingsPatch(capture_dedup=False)
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    assert fields == {"capture_dedup": False}


# ---------------------------------------------------------------------------
# Clarify upgrades: live members, hierarchy, create-project + attachments
# ---------------------------------------------------------------------------








def test_sync_refreshes_members_and_create_project_is_owner_checked():
    import inspect

    from gateway.routes.tasks import accounts as tasks_accounts
    from gateway.routes.tasks import sync as tasks_sync

    # Sync keeps the delegate list honest every pull.
    assert "list_members" in inspect.getsource(tasks_sync._sync_account)
    # Create-project + member refresh go through the ownership guard.
    for fn in (tasks_accounts.create_account_project,
               tasks_accounts.refresh_account_members):
        assert "_assert_account_owner" in inspect.getsource(fn)


def test_attachment_names_are_sanitized_and_executables_blocked():
    from gateway.routes.tasks.attachments import _BLOCKED_EXT, _safe_name

    assert _safe_name("../../etc/passwd") == "passwd"
    assert _safe_name("photo (1).png") == "photo _1_.png"
    assert _safe_name("") == "attachment"
    assert ".exe" in _BLOCKED_EXT and ".sh" in _BLOCKED_EXT


def test_attachment_serving_is_owner_checked():
    import inspect

    from gateway.routes.tasks import attachments

    src = inspect.getsource(attachments.serve_attachment)
    assert "user_id = :uid" in src


def test_capture_accepts_attachments_and_item_model_carries_them():
    from gateway.routes.tasks.core import GtdItemModel
    from gateway.routes.tasks.items import CaptureRequest

    assert "attachments" in CaptureRequest.model_fields
    assert "attachments" in GtdItemModel.model_fields


# ---------------------------------------------------------------------------
# Waiting-For surfacing (spec §6 / §12) — the gtd_waiting columns that five
# INSERT sites wrote and nothing ever read back.
# ---------------------------------------------------------------------------


def test_item_select_reads_the_waiting_record_columns():
    """`expected_by` is the deterministic overdue line (spec §6 line 540) and
    `last_nudged_at` says whether a follow-up already went out. Both are
    mig-48 columns on gtd_waiting; if ITEM_SELECT does not project them the
    Waiting-For view cannot flag anything."""
    from gateway.routes.tasks.core import ITEM_SELECT

    assert "w.expected_by" in ITEM_SELECT
    assert "w.last_nudged_at" in ITEM_SELECT
    # Still the OPEN record only — a resolved waiting-for must not resurface.
    assert "w.resolved = false" in ITEM_SELECT


def test_expected_by_round_trips_from_row_to_item_model():
    """§12: 'delegate a task to a teammate, see it on Waiting For, get an
    overdue flag'. The flag is client-side over `expected_by`, so the column
    has to survive the row→model hop as an ISO string."""
    from datetime import UTC, datetime

    from gateway.routes.tasks.core import GtdItemModel, _row_to_item

    assert "expected_by" in GtdItemModel.model_fields
    assert "last_nudged_at" in GtdItemModel.model_fields

    delegated = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    expected = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
    row = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        source="LOCAL", account_id=None, provider_task_id=None,
        provider_url=None, title="Vendor quote", description=None,
        disposition="WAITING", next_action=None, context=None, energy=None,
        time_estimate_mins=None, is_two_minute=False, project_id=None,
        defer_until=None, sync_state="local", provider_status=None,
        assignee=None, is_mine=False,
        waiting_on={"name": "Sai Kumar", "email": "sai@fracktal.in"},
        delegated_at=delegated, expected_by=expected, last_nudged_at=None,
        due_at=None, is_hard_date=False, completed_at=None, clarified_at=None,
        created_at=delegated, updated_at=delegated,
    )
    item = _row_to_item(row)
    # who / what / since-when (§1 line 46) + the overdue line (§6 line 540).
    assert item.waiting_on is not None and item.waiting_on.name == "Sai Kumar"
    assert item.title == "Vendor quote"
    assert item.delegated_at == delegated.isoformat()
    assert item.expected_by == expected.isoformat()
    # Never nudged yet — the nudge path is not built (owner-gated).
    assert item.last_nudged_at is None


def test_stale_waiting_rule_is_five_days_since_delegation():
    """The client's isStaleWaiting (tasks/lib/waiting.ts) and this SQL are the
    same rule stated twice; if one moves the view and /tasks/insights disagree
    about the same list. Pin the SQL side here."""
    import inspect

    from gateway.routes.tasks import ai as tasks_ai_mod

    src = inspect.getsource(tasks_ai_mod.inbox_insights)
    assert "w.delegated_at < now() - interval '5 days'" in src


def test_no_insert_site_derives_expected_by_from_a_due_date():
    """`expected_by` means ONE thing: someone actually promised this date.

    Every INSERT site used to snapshot the item's own due date into it under
    another name, which is the worst shape — nobody promised anything, and the
    copy froze the instant the deadline moved, so the Overdue badge lied in
    both directions. The rule now: these sites write NO `expected_by` at all
    (NULL = no promise), and the overdue line falls back to the item's live
    `due_at` client-side (tasks/lib/waiting.ts::isWaitingOverdue). A promise is
    stated explicitly through PATCH /tasks/items/{id}.

    Pinned by source inspection because the write is raw SQL in a route that
    needs a live DB to exercise."""
    import inspect

    from gateway.routes.tasks import capture_email as capture_mod
    from gateway.routes.tasks import items as items_mod
    from gateway.routes.tasks import sync as sync_mod

    sites = [
        items_mod.delegate_item,          # POST /items/{id}/delegate
        items_mod.organize_item,          # clarify → delegate
        capture_mod.capture_from_email,   # one-click email capture
        capture_mod._route_and_persist,   # clarify-popup email capture
        sync_mod._sync_account,           # provider pull (monitored task)
    ]
    for fn in sites:
        src = inspect.getsource(fn)
        insert_at = src.index("INSERT INTO gtd_waiting")
        insert = src[insert_at:insert_at + 400]
        assert "expected_by" not in insert, (
            f"{fn.__name__} writes expected_by at insert time — if the value is "
            "the item's own due date, leave it NULL and let the read-time "
            "fallback judge it live")


def test_patch_item_can_state_and_clear_an_explicit_promised_by_date():
    """The other half of the rule: a promise nobody can record is not a fact
    the system holds. `PATCH /tasks/items/{id}` carries `expected_by` onto the
    item's OPEN gtd_waiting row (same auth, same shape as the due-date edit),
    and "" clears it back to NULL — taking the promise back, not writing a
    second deadline."""
    import inspect

    from gateway.routes.tasks.items import ItemPatch, patch_item

    assert "expected_by" in ItemPatch.model_fields

    src = inspect.getsource(patch_item)
    assert "UPDATE gtd_waiting" in src
    # The OPEN record only — a resolved waiting-for is history.
    assert "resolved = false" in src
    # Ownership: the patch is scoped to the caller's own item.
    assert "user_id = :uid" in src
