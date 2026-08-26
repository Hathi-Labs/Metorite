"""Mandatory custom fields, checked at the MOVE — migration 192.

Owner directive 2026-08-26: *"ensure all mandatory fields are completed when
creating a task that will later move to the Projects app."*

⚠️ **The boundary is the interesting decision**, and these tests encode it.
Enforcement is at ``POST /projects/tasks/{id}/move``, never at capture, for two
reasons — at capture nobody knows the destination, so nobody knows which
definitions apply; and a required-field prompt on quick capture is the friction
that stops people capturing at all, which
``personal.py::capture`` commits against in writing ("takes a title and nothing
else is required"). A task never written down is worse than one written down
incompletely.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from gateway.routes.projects import custom_fields as cf

ROOT = "00000000-0000-0000-0000-0000000000aa"


def _defs(*specs: tuple[str, str, bool]) -> list[dict[str, Any]]:
    return [
        {"id": f"f{i}", "project_id": ROOT, "field_key": key, "name": name,
         "description": None, "field_type": "text", "options": [],
         "position": i, "required": req, "created_by": "x@example.invalid"}
        for i, (key, name, req) in enumerate(specs)
    ]


@pytest.fixture
def patched(monkeypatch):
    """`load_definitions` stubbed — the union it computes is its own suite's."""
    def _install(definitions: list[dict[str, Any]]) -> None:
        async def fake(db: Any, root: str) -> list[dict[str, Any]]:
            assert root == ROOT, "must ask about the DESTINATION root"
            return definitions
        monkeypatch.setattr(cf, "load_definitions", fake)
    return _install


# ── The rule ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_missing_required_field_refuses_the_move(patched) -> None:
    patched(_defs(("client", "Client", True)))
    with pytest.raises(HTTPException) as caught:
        await cf.assert_required_fields_present(None, ROOT, {})
    assert caught.value.status_code == 422
    assert caught.value.detail["error"] == "required_fields_missing"
    assert "Client" in caught.value.detail["message"]


@pytest.mark.asyncio
async def test_a_supplied_required_field_allows_it(patched) -> None:
    patched(_defs(("client", "Client", True)))
    await cf.assert_required_fields_present(None, ROOT, {"client": "Acme"})


@pytest.mark.asyncio
async def test_an_optional_field_is_never_demanded(patched) -> None:
    patched(_defs(("notes", "Notes", False)))
    await cf.assert_required_fields_present(None, ROOT, {})


@pytest.mark.asyncio
async def test_the_refusal_carries_the_definitions_not_just_the_names(
    patched,
) -> None:
    """The move dialog renders these as inputs, in place.

    Names alone would send somebody off to the project's settings to find out
    what kind of value is wanted — which is the trip the dialog exists to save.
    """
    patched(_defs(("client", "Client", True), ("notes", "Notes", False)))
    with pytest.raises(HTTPException) as caught:
        await cf.assert_required_fields_present(None, ROOT, {})
    fields = caught.value.detail["fields"]
    assert [f["field_key"] for f in fields] == ["client"], (
        "only the MISSING required ones — listing satisfied or optional fields "
        "would make the dialog ask for things it already has"
    )
    assert fields[0]["field_type"] == "text" and "options" in fields[0]


# ── ⚠️ The falsy trap, which is the bug this check invites ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", None, [], {}])
async def test_these_count_as_UNANSWERED(patched, blank: Any) -> None:
    patched(_defs(("client", "Client", True)))
    with pytest.raises(HTTPException):
        await cf.assert_required_fields_present(None, ROOT, {"client": blank})


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [0, 0.0, False])
async def test_these_are_REAL_ANSWERS_and_must_pass(patched, answer: Any) -> None:
    """A number answered zero and a checkbox answered no ARE answers.

    The ordinary falsy bug, and on a required-field check it is worse than
    usual: it refuses the move and then tells the user to fill in a field they
    can plainly see is already filled in. "Estimated cost: 0" and "Needs legal
    review: no" are exactly the answers somebody would be annoyed to re-enter.
    """
    patched(_defs(("cost", "Estimated cost", True)))
    await cf.assert_required_fields_present(None, ROOT, {"cost": answer})


@pytest.mark.asyncio
async def test_every_missing_field_is_named_at_once(patched) -> None:
    """Not one at a time. Three round trips to learn three fields is a form
    that interrogates rather than asks."""
    patched(_defs(
        ("a", "Alpha", True), ("b", "Beta", True), ("c", "Gamma", False),
    ))
    with pytest.raises(HTTPException) as caught:
        await cf.assert_required_fields_present(None, ROOT, {})
    assert [f["name"] for f in caught.value.detail["fields"]] == ["Alpha", "Beta"]


# ── The column, and the seam it travels through ─────────────────────────────

def test_the_definition_row_exposes_required() -> None:
    """A client cannot render a required marker it is never told about."""
    class _Row:
        id, project_id, field_key, name = "f1", None, "client", "Client"
        description, field_type, options, position = None, "text", [], 0
        created_by, required = "x@example.invalid", True

    assert cf._definition_row(_Row()) ["required"] is True


def test_a_null_required_reads_as_false_not_none() -> None:
    """R6: the column is NULLABLE, so "never stated" must resolve, not leak.

    A `None` reaching a client renders as neither required nor optional, and
    `if field.required` in one language and `if field.required is not None` in
    another is how two surfaces disagree about the same field.
    """
    class _Row:
        id, project_id, field_key, name = "f1", None, "client", "Client"
        description, field_type, options, position = None, "text", [], 0
        created_by, required = "x@example.invalid", None

    assert cf._definition_row(_Row())["required"] is False
