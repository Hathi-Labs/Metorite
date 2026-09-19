"""WS-27bl — the rules behind moving a task between projects.

Spec: ``project-docs/specs/project_management_app.md`` §9.13 ·
D-PM-29 / D-PM-30 / D-PM-31.

These are the decisions, not the plumbing. Every case here is a way the move
could be wrong while still returning 200: a value written into a field that
cannot hold it, a selection that silently takes its mapping from the wrong
source, a drop nobody was told about.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import move
from gateway.routes.projects.move import (
    COMPATIBLE_TYPES,
    apply_field_map,
    compatible,
    resolve_field_map,
    shared_source,
)


def field(key: str, name: str, field_type: str = "text") -> dict:
    return {"field_key": key, "name": name, "field_type": field_type}


def task(task_id: str, project_id: str, number: int) -> SimpleNamespace:
    return SimpleNamespace(id=task_id, project_id=project_id, task_number=number)


# ── D-PM-30: one source project per selection ───────────────────────────────

class TestSharedSource:
    def test_one_source_is_returned(self):
        got = shared_source([task("t1", "p1", 1), task("t2", "p1", 2)])
        assert got == "p1"

    def test_two_sources_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            shared_source([task("t1", "p1", 1), task("t2", "p2", 2)])
        assert exc.value.status_code == 422

    def test_the_refusal_NAMES_the_strays(self):
        """⚠️ The point of the message. "2 sources" tells nobody what to
        deselect; the task numbers do."""
        with pytest.raises(HTTPException) as exc:
            shared_source([
                task("t1", "p1", 1), task("t2", "p1", 2), task("t3", "p2", 77),
            ])
        assert "#77" in exc.value.detail
        # The majority project is the presumed source, so its rows are NOT
        # named as the problem.
        assert "#1" not in exc.value.detail

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(HTTPException):
            shared_source([])


# ── Type compatibility ──────────────────────────────────────────────────────

class TestCompatible:
    def test_same_type_always_carries(self):
        for kind in COMPATIBLE_TYPES:
            assert compatible(kind, kind)

    def test_select_does_NOT_widen_into_multi_select(self):
        """⚠️ This test asserted the opposite until review, 2026-09-19.

        The claim was "one chosen option is a legal list of one, and nothing
        about the value changes" — and those two clauses contradict each
        other. A list of one is `["High"]`, and `_coerce_multi_select`
        refuses a bare string. The widening would have written a value the
        destination's own coercer rejects, which is precisely what this
        table exists to prevent.
        """
        assert not compatible("select", "multi_select")
        assert not compatible("multi_select", "select")

    def test_a_choice_field_carries_only_if_the_OPTIONS_fit(self):
        """A type match is not a mapping for a choice field.

        Two spaces each hold a `select` named "Severity", one with
        [Low, High] and one with [S1, S2, S3]. Copying "High" across leaves
        a value `_coerce_select` refuses on every later write: unfilterable,
        and uneditable except by hand.
        """
        assert compatible("select", "select", ["Low", "High"], ["Low", "High", "Mid"])
        assert not compatible("select", "select", ["Low", "High"], ["S1", "S2"])
        # A subset is fine; the destination may offer more.
        assert compatible("select", "select", ["Low"], ["Low", "High"])

    def test_a_non_choice_field_ignores_options(self):
        assert compatible("text", "text", ["ignored"], [])

    def test_NOTHING_widens_into_text(self):
        """⚠️ The rule that stops every map succeeding.

        Letting anything become text would turn a date into a string no filter
        can compare, and call it a mapping. A map that cannot round-trip is a
        drop wearing a mapping's clothes, and D-PM-29 says a drop is NAMED.
        """
        for kind in ("number", "date", "boolean", "url", "select"):
            assert not compatible(kind, "text")

    def test_an_unknown_type_carries_nowhere(self):
        assert not compatible("telepathy", "text")


# ── The field map ───────────────────────────────────────────────────────────

class TestResolveFieldMap:
    def test_matches_on_field_key_first(self):
        mapping, orphans = resolve_field_map(
            [field("severity", "How bad")],
            [field("severity", "Severity")],
        )
        assert mapping == {"severity": "severity"}
        assert orphans == []

    def test_falls_back_to_a_case_folded_name(self):
        # Two spaces that each grew a "Severity" independently.
        mapping, orphans = resolve_field_map(
            [field("sev", "Severity")],
            [field("severity_level", "  severity  ")],
        )
        assert mapping == {"sev": "severity_level"}
        assert orphans == []

    def test_a_name_match_with_an_INCOMPATIBLE_type_is_an_orphan(self):
        """A match is not a mapping. Writing a select into a number field is
        corruption with an audit trail."""
        mapping, orphans = resolve_field_map(
            [field("sev", "Severity", "select")],
            [field("sev2", "Severity", "number")],
        )
        assert mapping == {}
        assert [o["field_key"] for o in orphans] == ["sev"]

    def test_no_destination_field_is_an_orphan(self):
        mapping, orphans = resolve_field_map([field("sev", "Severity")], [])
        assert mapping == {}
        assert [o["field_key"] for o in orphans] == ["sev"]

    def test_field_key_beats_a_name_match_elsewhere(self):
        # `field_key` is the stable identity (migration 155 says so); a display
        # name is free to change, so it must not outrank one.
        mapping, _ = resolve_field_map(
            [field("sev", "Severity")],
            [field("sev", "Something else"), field("other", "Severity")],
        )
        assert mapping == {"sev": "sev"}


class TestApplyFieldMap:
    def test_a_mapped_value_lands_under_the_new_key(self):
        landed, dropped = apply_field_map(
            {"sev": "high"}, {"sev": "severity"}, frozenset({"severity"}),
        )
        assert landed == {"severity": "high"}
        assert dropped == {}

    def test_a_key_already_legal_in_the_destination_carries_unmapped(self):
        """Two spaces sharing a `field_key` need no map entry, and requiring
        one would make the common case the noisy one."""
        landed, dropped = apply_field_map({"sev": "high"}, {}, frozenset({"sev"}))
        assert landed == {"sev": "high"}
        assert dropped == {}

    def test_an_unmappable_value_is_DROPPED_and_reported(self):
        landed, dropped = apply_field_map({"sev": "high"}, {}, frozenset({"other"}))
        assert landed == {}
        assert dropped == {"sev": "high"}

    def test_a_map_pointing_at_a_key_the_destination_lost_is_a_DROP(self):
        """⚠️ The stale-card case. A map can be built, a field deleted, and the
        map then applied. Writing under a key nothing defines would recreate
        the very orphan this feature exists to remove."""
        landed, dropped = apply_field_map(
            {"sev": "high"}, {"sev": "gone"}, frozenset({"severity"}),
        )
        assert landed == {}
        assert dropped == {"sev": "high"}

    def test_nothing_in_nothing_out(self):
        assert apply_field_map({}, {}, frozenset()) == ({}, {})


# ── The review's findings, each pinned (2026-09-19) ─────────────────────────

class TestTwoSourceFieldsOneTarget:
    """🔴 P0. Two source fields resolving to ONE destination field.

    `pm_custom_fields` is UNIQUE on (project_id, field_key) and nothing else,
    so two definitions may share a NAME — and WS-27bj's org-wide ∪ root-local
    union makes that the ordinary case, not a curiosity: an org-wide
    `priority` beside a root-local `prio`, both called "Priority".

    Before the fix, one matched by key and the other by name, both aimed at
    the same target, and the second silently overwrote the first in `landed`.
    The loser never entered `drops`, so no warning, no `accept_drops` gate,
    no timeline row — and which value survived followed JSONB key order, so
    it differed task by task inside ONE bulk move.
    """

    SOURCE = [
        field("priority", "Priority"),
        field("prio", "Priority"),
    ]
    DEST = [field("priority", "Priority")]

    def test_only_one_source_claims_the_target(self):
        mapping, orphans = resolve_field_map(self.SOURCE, self.DEST)
        assert mapping == {"priority": "priority"}
        assert [o["field_key"] for o in orphans] == ["prio"]

    def test_the_EXACT_key_match_wins_whatever_the_order(self):
        # Otherwise the winner depends on whatever order `load_definitions`
        # happened to return, which is not a rule anybody can reason about.
        for source in (self.SOURCE, list(reversed(self.SOURCE))):
            mapping, _ = resolve_field_map(source, self.DEST)
            assert mapping == {"priority": "priority"}

    def test_the_loser_is_REPORTED_as_a_drop_not_silently_lost(self):
        mapping, _ = resolve_field_map(self.SOURCE, self.DEST)
        landed, dropped = apply_field_map(
            {"priority": "P1-critical", "prio": "P4-someday"},
            mapping,
            frozenset({"priority"}),
        )
        assert landed == {"priority": "P1-critical"}
        assert dropped == {"prio": "P4-someday"}

    def test_a_HAND_SUPPLIED_map_cannot_collide_either(self):
        """The caller posts `field_map`, so the rule cannot live only where
        the server builds it."""
        landed, dropped = apply_field_map(
            {"a": "first", "b": "second"},
            {"a": "target", "b": "target"},
            frozenset({"target"}),
        )
        assert landed == {"target": "first"}
        assert dropped == {"b": "second"}

    def test_nothing_is_ever_lost_without_appearing_somewhere(self):
        """The invariant the whole feature rests on: every input value is
        either landed or dropped, never neither."""
        values = {"priority": "P1", "prio": "P4", "orphan": "x"}
        mapping, _ = resolve_field_map(self.SOURCE, self.DEST)
        landed, dropped = apply_field_map(values, mapping, frozenset({"priority"}))
        assert len(landed) + len(dropped) == len(values)


class TestABlankLaneIsNotAUuid:
    """F9 — the placeholder option in the dialog reached the driver.

    `MoveTasksDialog` renders `<option value="">Pick a lane…</option>` for a
    row with no automatic landing. Pick a lane, then pick that option again,
    and the payload carries `{"<old>": ""}`. That empty string reached
    `require_status_in_project`, which builds `CAST('' AS uuid)`, and asyncpg
    raised — a 500 where "you did not pick a lane" was the honest answer.
    """

    def test_a_blank_VALUE_is_dropped_rather_than_sent_on(self):
        body = move.MoveIn(
            task_ids=["t1"],
            destination_project_id="p2",
            status_map={"old-lane": "", "other": "new-lane"},
        )
        assert body.status_map == {"other": "new-lane"}

    def test_a_whitespace_value_counts_as_blank(self):
        body = move.MoveIn(
            task_ids=["t1"], destination_project_id="p2",
            status_map={"old-lane": "   "},
        )
        assert body.status_map == {}

    def test_field_map_gets_the_same_treatment(self):
        body = move.MoveIn(
            task_ids=["t1"], destination_project_id="p2",
            field_map={"a": "", "b": "dest"},
        )
        assert body.field_map == {"b": "dest"}

    def test_a_blank_KEY_is_refused_instead(self):
        """Nothing maps FROM nothing, so this is a caller error and not an
        unanswered row."""
        with pytest.raises(ValueError):
            move.MoveIn(
                task_ids=["t1"], destination_project_id="p2",
                status_map={"": "new-lane"},
            )

    def test_None_still_means_no_mapping_at_all(self):
        body = move.MoveIn(task_ids=["t1"], destination_project_id="p2")
        assert body.status_map is None
        assert body.field_map is None
