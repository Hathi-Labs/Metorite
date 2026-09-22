"""WS-27n — bulk edit.

Spec: `project-docs/specs/project_management_app.md` §11.2 item 6, §11.11.

**This ticket gates WS-27g.** §11.3: the cutover imports a real workspace, and
an import that cannot be re-triaged in bulk is one somebody abandons halfway,
leaving two live systems — the state the retirement exists to end.

The claims worth pinning are the ones where a bulk edit differs from fifty
single edits, because that is where the design decisions are:

* **shape is validated once; outcomes are per task.** A field nobody can set is
  the same mistake for all fifty and earns a 422 before anything is written; a
  status name that exists in one project and not another is a fact about *that
  task*, and failing the batch for it makes a mixed selection unusable.
* **`status_id` is refused with its own message.** It is the mistake somebody
  makes by copying a single-task PATCH body, and it is *wrong* in bulk because
  a status belongs to one root and a selection can span several.
* **assignees and tags are ADD/REMOVE, never SET.** A replace across a
  selection wipes every individual assignment the fifty tasks already carried.
* **an empty request is a 422**, not a cheerful "0 changed" that lets a client
  bug ship.

Pure functions and the route registry. No Postgres, no fake.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects.automation import PATCHABLE_FIELDS
from gateway.routes.projects.bulk import (
    BULK_ACTIONS,
    MAX_BULK,
    clean_people,
    clean_tag_list,
    dedupe_ids,
    is_noop,
    moved_people,
    validate_patch,
)

# ── The patch shape ─────────────────────────────────────────────────────────

def test_a_patch_may_set_exactly_what_a_single_edit_may():
    """Derived from `apply_task_patch`'s own vocabulary rather than restated, so
    a field added there is bulk-settable without a second edit here — and a
    field removed there stops being bulk-settable at the same moment."""
    for field in PATCHABLE_FIELDS:
        assert validate_patch({field: None}) == {field: None}


def test_status_is_accepted_by_name():
    assert validate_patch({"status": "Done"}) == {"status": "Done"}


def test_status_id_is_refused_with_a_reason_that_is_specific_to_bulk():
    """The mistake somebody makes by copying a single-task PATCH body — where
    `status_id` is correct. "Unknown field" would not explain why the thing that
    works on one task is refused on fifty."""
    with pytest.raises(HTTPException) as exc:
        validate_patch({"status_id": "abc"})
    assert exc.value.status_code == 422
    detail = str(exc.value.detail)
    assert "status" in detail
    assert "project" in detail, "the message must say WHY, not just refuse"


def test_an_unknown_field_is_refused_before_any_task_is_touched():
    """The same mistake for every task in the selection, so it is a 422 up
    front rather than five hundred identical per-task failures."""
    with pytest.raises(HTTPException) as exc:
        validate_patch({"colour": "purple"})
    assert exc.value.status_code == 422
    assert "colour" in str(exc.value.detail)


def test_the_refusal_lists_what_IS_settable():
    with pytest.raises(HTTPException) as exc:
        validate_patch({"nope": 1})
    detail = str(exc.value.detail)
    for field in PATCHABLE_FIELDS:
        assert field in detail


def test_an_absent_patch_is_not_an_error():
    """A request may carry only assignees or only tags."""
    assert validate_patch(None) == {}
    assert validate_patch({}) == {}


def test_a_patch_that_is_not_an_object_is_refused():
    with pytest.raises(HTTPException):
        validate_patch(["status"])


def test_validate_patch_copies_rather_than_handing_back_the_request_body():
    """The returned dict is mutated downstream; handing back the caller's
    object would edit the parsed request under it."""
    original = {"importance": 3}
    assert validate_patch(original) is not original


# ── Assignees ───────────────────────────────────────────────────────────────

def test_assignees_are_lowercased():
    """R10 — `pm_task_assignees` stores lowercased addresses, so a caller who
    typed a capital must not create a second row for the same person."""
    assert clean_people(["Priya@Fracktal.IN"]) == ["priya@fracktal.in"]


def test_a_person_named_twice_is_added_once():
    assert clean_people(["a@x.co", "A@X.co"]) == ["a@x.co"]


def test_blanks_are_dropped_rather_than_becoming_an_empty_assignee():
    assert clean_people(["a@x.co", "", "   ", None]) == ["a@x.co"]


def test_order_is_preserved_so_the_first_named_is_the_first_notified():
    assert clean_people(["z@x.co", "a@x.co"]) == ["z@x.co", "a@x.co"]


def test_an_agent_is_a_legal_bulk_assignee():
    """D-PM-4 — one vocabulary for both species. Handing fifty tasks to an agent
    is the same action as handing them to a colleague, and a bulk endpoint that
    quietly refused agents would make it a parallel feature again."""
    assert clean_people(["agent:builder"]) == ["agent:builder"]


# ── Tags ────────────────────────────────────────────────────────────────────

def test_tags_are_normalised_the_way_the_registry_normalises_them():
    """Shared with `tags.normalise_tag`, so a tag added in bulk and one typed
    into the panel cannot become two tags."""
    assert clean_tag_list(["  needs   review "]) == ["needs review"]


def test_a_tag_named_twice_is_added_once_case_insensitively():
    assert clean_tag_list(["bug", "BUG"]) == ["bug"]


def test_a_blank_tag_is_dropped():
    assert clean_tag_list(["bug", "", "  "]) == ["bug"]


# ── The selection ───────────────────────────────────────────────────────────

def test_a_task_named_twice_is_reported_once():
    """A selection built by shift-clicking can name a task twice. Applying the
    patch twice is harmless; reporting it twice makes the counts wrong."""
    assert dedupe_ids(["a", "b", "a"]) == ["a", "b"]


def test_selection_order_is_kept():
    assert dedupe_ids(["z", "a"]) == ["z", "a"]


def test_blank_ids_are_dropped():
    assert dedupe_ids(["a", "", "  "]) == ["a"]


def test_the_batch_is_bounded():
    """A single request that can rewrite the whole table is a denial-of-service
    surface, not a feature — the same reasoning as `views.MAX_POSITIONS`."""
    assert MAX_BULK <= 1000
    assert MAX_BULK >= 100, "too small to re-triage an imported list, which is the point"


# ── Nothing to do ───────────────────────────────────────────────────────────

def test_a_request_that_asks_for_nothing_is_a_noop():
    """A selection of fifty tasks and an empty patch is a client bug. Answering
    200 with "0 changed" lets it ship."""
    assert is_noop({}, [], [], [], []) is True


@pytest.mark.parametrize("args", [
    ({"importance": 1}, [], [], [], []),
    ({}, ["a@x.co"], [], [], []),
    ({}, [], ["a@x.co"], [], []),
    ({}, [], [], ["bug"], []),
    ({}, [], [], [], ["bug"]),
])
def test_any_single_instruction_is_enough_to_act_on(args):
    assert is_noop(*args) is False


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_bulk_is_mounted_and_is_not_shadowed_by_the_single_task_route():
    """`/projects/tasks/bulk` is the first LITERAL path in this package that
    sits where a `{task_id}` template also lives.

    `__init__.py` argues import order is not load-bearing because every path is
    a literal and none can shadow another. That argument now has one place it
    could break: a bare `POST /projects/tasks/{task_id}` registered earlier
    would swallow this, and the failure would be a 422 about an invalid uuid
    rather than anything mentioning bulk.
    """
    from gateway.routes.projects import router

    posts = [
        r for r in router.routes
        if getattr(r, "path", None) == "/projects/tasks/bulk"
        and "POST" in getattr(r, "methods", set())
    ]
    assert len(posts) == 1, "bulk is not mounted, or is mounted twice"
    assert posts[0].endpoint.__name__ == "bulk_edit"

    shadowing = [
        r for r in router.routes
        if getattr(r, "path", None) == "/projects/tasks/{task_id}"
        and "POST" in getattr(r, "methods", set())
    ]
    assert not shadowing, (
        "a bare POST /projects/tasks/{task_id} would shadow /tasks/bulk "
        "depending on import order — give it a suffix"
    )


def test_bulk_does_not_reimplement_the_task_patch():
    """It reuses `automation.apply_task_patch`, which exists because WS-27f
    needed an edit indistinguishable in validation from a human PATCH. A second
    field writer would be a third opinion about what a task edit is."""
    from pathlib import Path

    import gateway.routes.projects.bulk as bulk_mod

    source = Path(bulk_mod.__file__).read_text(encoding="utf-8")
    assert "apply_task_patch" in source
    assert "UPDATE pm_tasks SET" not in source.replace(
        "UPDATE pm_tasks SET tags = :tags, updated_at = now() ", ""
    ), "bulk writes task columns directly instead of going through the service"


# ── What actually moves ─────────────────────────────────────────────────────

def test_re_asserting_an_assignee_somebody_already_has_is_not_a_change():
    """Fifty tasks that were already Priya's would otherwise each gain a
    timeline entry saying nothing — and a bulk edit reporting "50 changed"
    when it changed nothing is one whose count nobody can trust."""
    added, removed = moved_people({"priya@x.co"}, ["priya@x.co"], [])
    assert (added, removed) == ([], [])


def test_only_the_genuinely_new_are_added():
    added, _ = moved_people({"a@x.co"}, ["a@x.co", "b@x.co"], [])
    assert added == ["b@x.co"]


def test_removing_somebody_who_is_not_on_the_task_is_not_a_change():
    _, removed = moved_people({"a@x.co"}, [], ["b@x.co"])
    assert removed == []


def test_add_and_remove_in_one_request_both_apply():
    added, removed = moved_people({"a@x.co"}, ["b@x.co"], ["a@x.co"])
    assert (added, removed) == (["b@x.co"], ["a@x.co"])


def test_the_order_asked_for_is_the_order_returned():
    """The first person newly assigned is the one the batch notification points
    at, so this order is what a bell links to."""
    added, _ = moved_people(set(), ["z@x.co", "a@x.co"], [])
    assert added == ["z@x.co", "a@x.co"]


# ── The cap is enforced, not merely declared ────────────────────────────────

def test_too_large_a_batch_is_refused_before_the_database_is_opened():
    """The constant existing is not the claim; refusing is. The check runs
    before `_get_db()`, which is what lets this be hermetic — and is also the
    right order, since opening a connection to reject the request is waste."""
    import asyncio

    from gateway.routes.projects.bulk import BulkIn, bulk_edit

    payload = BulkIn(
        task_ids=[f"id-{i}" for i in range(MAX_BULK + 1)],
        patch={"importance": 1},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(bulk_edit(payload, user=None))
    assert exc.value.status_code == 422
    assert str(MAX_BULK) in str(exc.value.detail)


def test_an_empty_selection_is_refused_the_same_way():
    import asyncio

    from gateway.routes.projects.bulk import BulkIn, bulk_edit

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bulk_edit(BulkIn(task_ids=[], patch={"importance": 1}), user=None))
    assert exc.value.status_code == 422


# ── The lifecycle verbs (owner request 2026-09-20) ──────────────────────────
#
# Delete, archive and unarchive, on a selection. The single-task routes have
# existed since WS-27w; nothing could reach them from a multi-select, and
# nothing in the UI could reach them at all.

def test_the_verbs_are_the_ones_the_single_task_routes_already_have():
    """No verb invented here.

    `tasks.py` owns `DELETE /tasks/{id}`, `POST /{id}/archive` and
    `POST /{id}/unarchive`; `personal.py` owns `PATCH /tasks/{id}/personal`
    (the fourth, WS-39 S6a). Bulk is a second door onto those four, not a
    second vocabulary — a verb here with no single-task twin would be a
    behaviour only reachable in batches.
    """
    assert set(BULK_ACTIONS) == {"archive", "unarchive", "delete", "personal"}


def test_an_action_is_not_an_edit():
    """⚠️ Refused rather than ordered.

    "Archive these and also tag them" has two readings — tag then archive, or
    archive then tag — and a `delete` makes the other half meaningless
    whichever way it runs. Guessing an order here is how a caller discovers
    months later that half their request never happened.
    """
    import asyncio

    from gateway.routes.projects.bulk import BulkIn, bulk_edit

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bulk_edit(
            BulkIn(task_ids=["t1"], action="archive", tags_add=["bug"]),
            user=None,
        ))
    assert exc.value.status_code == 422
    assert "action, not an edit" in str(exc.value.detail)


def test_an_unknown_verb_names_the_ones_that_exist():
    import asyncio

    from gateway.routes.projects.bulk import BulkIn, bulk_edit

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bulk_edit(BulkIn(task_ids=["t1"], action="burn"), user=None))
    assert exc.value.status_code == 422
    assert "archive" in str(exc.value.detail)


def test_an_action_alone_is_NOT_a_request_that_asks_for_nothing():
    """The trap this guards.

    `is_noop` reads patch, assignees and tags. An action-only payload is empty
    by all four of those measures, so the noop check would refuse the one
    request shape this feature exists for — and the failure would read
    "Nothing to change" while the caller had asked to delete fifty things.
    """
    import asyncio

    from gateway.routes.projects.bulk import BulkIn, bulk_edit

    # It gets PAST validation: the failure below is the missing database, not
    # a 422. `pytest.raises(HTTPException)` would catch a 422 and hide this.
    try:
        asyncio.run(bulk_edit(BulkIn(task_ids=["t1"], action="delete"), user=None))
        raised: BaseException | None = None
    except HTTPException as exc:
        raised = exc
    except Exception as exc:  # the database is not here; that is the expected wall
        raised = exc
    assert not (
        isinstance(raised, HTTPException) and raised.status_code == 422
    ), f"action-only was refused as an empty request: {raised}"
    assert not isinstance(raised, NameError), f"the test itself is broken: {raised}"


def test_the_empty_message_now_mentions_an_action():
    """A refusal that lists three of the four ways to act teaches the wrong
    API. It is the only place a caller learns what a bulk request may carry."""
    import asyncio

    from gateway.routes.projects.bulk import BulkIn, bulk_edit

    with pytest.raises(HTTPException) as exc:
        asyncio.run(bulk_edit(BulkIn(task_ids=["t1"]), user=None))
    assert "action" in str(exc.value.detail)
