"""Task 3564, chat sidebar fault 2: the hermetic half of the ``isBrief`` mark.

``GET /chat/sessions`` computes ``isBrief`` (``routes/chat.py::_IS_BRIEF_SQL``)
and stores it nowhere. The R8 half is ``test_chat_sessions_is_brief_r8.py``.
This file holds the rules that need no database:

* no request model of a chat save declares the mark, so a body that names it
  is dropped before any SQL;
* only ``_get_sessions`` names ``isBrief`` in the gateway, so no second
  producer and no writer of it exists;
* the id of the brief row (``chat_brief.brief_message_id``) is the id that the
  SQL rule, the summary rule (``assistant_summaries.BRIEF_ROW``) and the
  refusal of a sealing write (``BRIEF_ID_PREFIX``) all name. The other agent
  rows that open a chat match none of them.

Mutations (R7): add ``is_brief: bool = False`` to ``SessionUpsertRequest``:
``test_no_save_model_declares_the_mark`` goes red. Change
``brief_message_id`` to ``f"brief_{day}"``: ``test_the_brief_id_is_the_id_each_rule_names``
goes red. Add ``"isBrief"`` to a string in another gateway function:
``test_only_the_session_list_names_the_mark`` goes red.
"""
from __future__ import annotations

import ast
import re
import uuid
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps" / "services" / "gateway" / "gateway"


def _names(model) -> set[str]:
    return {n.lower().replace("_", "") for n in model.model_fields}


def test_no_save_model_declares_the_mark() -> None:
    from gateway.routes.chat import MessageRecord, SessionPatchRequest, SessionUpsertRequest

    for model in (SessionUpsertRequest, SessionPatchRequest, MessageRecord):
        assert not {n for n in _names(model) if "brief" in n}, model.__name__
        # Extra keys are dropped, never stored: the default of pydantic.
        assert model.model_config.get("extra") in (None, "ignore"), model.__name__
    req = SessionUpsertRequest.model_validate({"id": "c1", "isBrief": True, "is_brief": True})
    assert "isBrief" not in req.model_dump() and "is_brief" not in req.model_dump()


def _docstring_ids(tree: ast.AST) -> set[int]:
    return {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _places(path: Path, word: str) -> list[str]:
    """The enclosing function or module constant of each string that names
    *word*. Docstrings and comments name no field, so the scan skips them."""
    source = path.read_text(encoding="utf-8")
    if word not in source:
        return []
    tree = ast.parse(source, filename=str(path))
    skip = _docstring_ids(tree)
    found: list[str] = []

    def walk(node: ast.AST, place: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = place
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = child.name if place == "<module>" else place
            elif place == "<module>" and isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                here = names[0] if names else place
            if (isinstance(child, ast.Constant) and isinstance(child.value, str)
                    and word in child.value and id(child) not in skip):
                found.append(here)
            walk(child, here)

    walk(tree, "<module>")
    return found


def test_only_the_session_list_names_the_mark() -> None:
    hits = {
        f"{path.relative_to(REPO).as_posix()}::{place}"
        for path in GATEWAY.rglob("*.py")
        for word in ("isBrief", "is_brief")
        for place in _places(path, word)
    }
    assert hits == {
        "apps/services/gateway/gateway/routes/chat.py::_SESSIONS_SQL",
        "apps/services/gateway/gateway/routes/chat.py::_get_sessions",
    }, sorted(hits)


def test_the_brief_id_is_the_id_each_rule_names() -> None:
    from gateway.assistant_summaries import BRIEF_ROW
    from gateway.routes.chat import _IS_BRIEF_SQL, BRIEF_ID_PREFIX, BRIEF_ID_SQL_PATTERN
    from gateway.routes.chat_brief import HELLO_MESSAGE_ID, brief_message_id
    from gateway.routes.chat_reminder import reminder_message_id

    sql_rule = re.compile(BRIEF_ID_SQL_PATTERN)
    assert f"'{BRIEF_ID_SQL_PATTERN}'" in _IS_BRIEF_SQL
    for day in (date(2026, 11, 16), date(2027, 1, 1), date(2026, 2, 28)):
        mid = brief_message_id(day)
        assert mid.startswith(BRIEF_ID_PREFIX), mid
        assert sql_rule.match(mid) and BRIEF_ROW.match(mid), mid

    others = [
        reminder_message_id(uuid.uuid4()), HELLO_MESSAGE_ID,
        f"reach-{uuid.uuid4()}", f"assistant-t1-{uuid.uuid4().hex}", str(uuid.uuid4()),
        "brief-2026-11-3", "brief-2026-11-16-x", "my-brief-2026-11-16",
    ]
    for mid in others:
        assert not sql_rule.match(mid), mid
        assert bool(sql_rule.match(mid)) == bool(BRIEF_ROW.match(mid)), mid
    for mid in others[:5]:
        assert not mid.startswith(BRIEF_ID_PREFIX), mid
