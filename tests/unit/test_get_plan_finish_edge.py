"""Task 2473: the MCP ``get_plan`` answer says the finish is unknown, and how many
slices it guessed. Hermetic: the mapper and its output schema, no database."""
from __future__ import annotations

import pytest

pytest.importorskip("agent_edge", reason="agent-edge not installed")

from agent_edge import tools as T


def _valid(finish) -> bool:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover
        pytest.skip("jsonschema not installed")
    schema = T._GET_PLAN_OUTPUT["properties"]["finish"]
    jsonschema.validate(finish, schema)
    return True


def test_a_finish_with_every_estimate_is_unchanged() -> None:
    out = T._plan_finish({"mins": 450, "working_days": 2, "date": "2026-11-10"})
    assert out == {"mins": 450, "working_days": 2, "date": "2026-11-10"}
    assert _valid(out)


def test_an_unknown_finish_has_no_length_and_no_date() -> None:
    out = T._plan_finish({"mins": None, "working_days": None, "date": None,
                          "unknown": True, "guessed": 8,
                          "words": "The finish is unknown: 8 tasks have no estimate."})
    assert out["unknown"] is True and out["guessed"] == 8
    assert out["mins"] is None and out["working_days"] is None and out["date"] is None
    assert "unknown" in out["words"]
    assert _valid(out)


def test_a_finish_with_guesses_keeps_its_date_and_counts_them() -> None:
    out = T._plan_finish({"mins": 300, "working_days": 1, "date": "2026-11-03",
                          "guessed": 2, "words": "2 tasks have no estimate."})
    assert out["date"] == "2026-11-03" and out["guessed"] == 2
    assert "unknown" not in out
    assert _valid(out)
