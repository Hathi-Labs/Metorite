"""ask_questions takes the shapes a model really sends (task 1533).

Production, 2026-11-03: the model passed the question list as a JSON string
of an ARRAY. The tool wanted an object and answered an error three times, so
no card showed. Mutation each test catches: the array form or the doubly
encoded form goes back to an error.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from acb_skills import ask_tools
from acb_skills.ask_tools import ask_questions

Q = {"header": "Sharat's email", "question": "Which address?", "options": [{"label": "Work"}, {"label": "Home"}]}


def _run(value: Any) -> str:
    return asyncio.run(ask_questions(value))


@pytest.fixture(autouse=True)
def _cards_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_tools, "cards_refused", lambda: False)


def _ok(result: str) -> bool:
    return not result.startswith("Error")


def test_json_string_of_an_object() -> None:
    assert _ok(_run(json.dumps({"questions": [Q]})))


def test_json_string_of_a_list() -> None:
    """The production shape."""
    assert _ok(_run(json.dumps([Q])))


def test_list_already_parsed() -> None:
    assert _ok(_run([Q]))


def test_object_with_a_string_questions_value() -> None:
    assert _ok(_run(json.dumps({"questions": json.dumps([Q])})))


@pytest.mark.parametrize("bad", ["not json", "42", '"text"', "[]", '{"questions": []}'])
def test_malformed_input_names_the_expected_shape(bad: str) -> None:
    result = _run(bad)
    assert result.startswith("Error")
    assert '{"questions":[' in result


def test_question_validation_is_kept() -> None:
    assert "has no question text" in _run(json.dumps([{"header": "x"}]))
    assert "is not an object" in _run(json.dumps(["text"]))
