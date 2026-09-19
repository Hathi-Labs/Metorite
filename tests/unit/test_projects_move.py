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

    def test_select_widens_into_multi_select_only(self):
        # One chosen option is a legal list of one, and nothing about the
        # stored value changes.
        assert compatible("select", "multi_select")
        assert not compatible("multi_select", "select")

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
