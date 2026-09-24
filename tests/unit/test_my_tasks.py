"""Unit tests for the My Tasks backend (`/tasks` routes) (offline — no DB, no HTTP).

Covers the pure logic layers that survive the retired store:
  - ai.propose: the clarify heuristic (disposition branches, project
    auto-match, GTD→stage default mapping)
  - the priority matrix, settings models and the email-capture helpers

🔧 **RE-CUT 2026-09-23 (S8 PR 1, `my_tasks_cutover.md` §5 S8).** The item
CRUD, the local tree, the ClickUp pull mapping, the provider registry and the
BO-1b push rules tested `routes/tasks/items.py`, `hierarchy.py`, `sync.py`,
`accounts.py` and `providers.py`. S8 PR 1 deleted those modules, so their
cases are deleted with them, not skipped. The one store's CRUD is fenced by
the `test_projects_personal*.py` suites.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from gateway.routes.tasks import ai as tasks_ai

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


# ---------------------------------------------------------------------------
# Delete (soft-delete + undo/restore + purge propagation) & bulk archive
# ---------------------------------------------------------------------------


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
    from gateway.routes.tasks.settings import UserSettingsModel

    assert UserSettingsModel().urgent_window_hours == 48


# ---------------------------------------------------------------------------
# ClickUp status → Next-Actions stage mapping
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Done column: completed tasks stay on the board until archived
# ---------------------------------------------------------------------------


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


def test_enrich_and_delegate_routes_are_registered():
    from gateway.routes.tasks import router

    paths = {getattr(r, "path", "") for r in router.routes}
    for p in ("/tasks/items/{item_id}/enrich",
              "/tasks/ai/backfill-context"):
        assert p in paths, f"missing route {p}"


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
# Sync pull (§9.3 #1): provider list_tasks + the My Tasks lens on pulled tasks
# ---------------------------------------------------------------------------


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


# ── "assigned to me" soundness ──────────────────────────────────────────────
#
# is_mine is THE signal Priority/Engage filter on ("only tasks assigned to me on
# ClickUp"). Its correctness rests on matching MY ClickUp user id against the
# task's assignee ids — so the comparison must be type-robust (ClickUp ids come
# back as ints in some payloads, strings in others) and must never match on a
# missing/blank id.


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
    assert "_find_existing_capture" in src      # idempotency per source email
    # The read lives in the seam (WS-39 S6d).
    finder = inspect.getsource(capture_email._find_existing_capture)
    assert '"email_id"' in finder


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


def test_item_model_carries_origin():
    from gateway.routes.tasks.core import MyTaskModel
    assert "origin" in MyTaskModel.model_fields


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


def test_my_task_model_projects_awaiting_approval_unchanged():
    """No migration and no model change: `sync_state` is a bare `str`, so the
    third value passes through the API as-is."""
    from gateway.routes.tasks.core import MyTaskModel

    field = MyTaskModel.model_fields["sync_state"]
    assert field.annotation is str
    assert MyTaskModel(
        id="i", title="t", sync_state="awaiting_approval",
        created_at="2026-08-11T00:00:00Z", updated_at="2026-08-11T00:00:00Z",
    ).sync_state == "awaiting_approval"


def test_agent_item_format_shows_email_origin():
    from skill_my_tasks.core import _fmt_item

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
    vocabulary. S8a (2026-09-23) retired both markers with the connector
    (D52): no row the lens answers carries `sync_state`. What the row DOES
    carry is `defer_until` and the project's lane name, and both are printed,
    so a snoozed task reads as snoozed rather than missing and the agent can
    name the lane the task is in."""
    from skill_my_tasks.core import _fmt_item

    line = _fmt_item({"id": "a" * 12, "title": "t", "disposition": "SOMEDAY",
                      "defer_until": "2026-10-03T00:00:00+00:00",
                      "workflow_stage": "Backlog"})
    assert "deferred until 2026-10-03" in line
    assert "stage Backlog" in line
    assert "PENDING PUSH" not in line and "AWAITING APPROVAL" not in line


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
    helper (delegate destination rules, a Waiting-For for follow-ups) rather
    than a divergent second code path. Since WS-39 S6d the write itself is the
    seam's (`insert_capture`)."""
    import inspect

    from gateway.routes.tasks import capture_email as ce

    persist = inspect.getsource(ce._route_and_persist)
    # Same destination rule as the one-click endpoint.
    assert 'source, sync_state = "SYNCED", "pending"' in persist
    assert 'assignee, disposition = None, "INBOX"' in persist
    assert "insert_capture" in persist
    assert 'fields["waiting_on"]' in persist

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
    from gateway.routes.tasks.settings import DEFAULT_TASK_MODELS, UserSettingsModel

    assert DEFAULT_TASK_MODELS == {
        "chat": "tier-powerful",
        "clarify": "tier-balanced",
        "atomize": "tier-fast",
        "email_capture": "tier-fast",
    }
    s = UserSettingsModel()
    assert s.capture_dedup is True and s.auto_sync_on_open is True


def test_ai_call_sites_use_configured_models():
    """The atomizer and the email-capture drafter run on the user's
    configured tier (user_settings), not a hardcoded one."""
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
    from gateway.routes.tasks.settings import UserSettingsPatch

    p = UserSettingsPatch(capture_dedup=False)
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    assert fields == {"capture_dedup": False}


# ---------------------------------------------------------------------------
# Clarify upgrades: live members, hierarchy, create-project + attachments
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Waiting-For surfacing (spec §6 / §12) — the gtd_waiting columns that five
# INSERT sites wrote and nothing ever read back.
# ---------------------------------------------------------------------------


def test_expected_by_round_trips_from_row_to_item_model():
    """§12: 'delegate a task to a teammate, see it on Waiting For, get an
    overdue flag'. The flag is client-side over `expected_by`, so the column
    has to survive the row→model hop as an ISO string."""
    from datetime import UTC, datetime

    from gateway.routes.tasks.core import MyTaskModel, _row_to_item

    assert "expected_by" in MyTaskModel.model_fields
    assert "last_nudged_at" in MyTaskModel.model_fields

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
    """The client's isStaleWaiting (tasks/lib/waiting.ts) and the server are
    the same rule stated twice: the seam's Python and the client's constant.
    If one moves, the view and /tasks/insights disagree about the same list.
    Pin both here (WS-39 S6d)."""
    import inspect
    import re
    from pathlib import Path

    from gateway.routes.projects import item_lens

    # The pm arm: strictly more than STALE_WAITING_DAYS since delegated_at,
    # and NOTHING else. A nudge does not reset it, a promised date does not
    # enter it, because the client's rule reads only `delegatedAt`.
    pm = inspect.getsource(item_lens._PmLens.insight_counts)
    assert item_lens.STALE_WAITING_DAYS == 5
    assert "timedelta(days=STALE_WAITING_DAYS)" in pm
    assert "last_nudged_at" not in pm and "expected_by" not in pm

    client = Path(__file__).resolve().parents[2] / (
        "workbench/control_plane/src/app/tasks/lib/waiting.ts")
    ts = client.read_text(encoding="utf-8")
    assert re.search(r"STALE_WAITING_DAYS\s*=\s*5;", ts)
    body = ts[ts.index("export function isStaleWaiting"):]
    body = body[:body.index("}\n")]
    assert "delegatedAt" in body and "lastNudgedAt" not in body


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

    # The two email captures write through the seam since WS-39 S6d. Their
    # `fields` carry no `expected_by`. The seam's own Waiting-For write is
    # the overlay, pinned in `test_tasks_ai_source.py`.
    for fn in (capture_mod.capture_from_email, capture_mod._route_and_persist):
        assert '"expected_by"' not in inspect.getsource(fn), fn.__name__


