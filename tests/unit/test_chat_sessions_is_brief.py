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
  rows that open a chat match none of them;
* the SQL rule is the client rule (``isBriefRow`` in ``briefTurn.ts``), read
  from the TypeScript file;
* the probe carries the ``BETWEEN`` bound on ``id`` that lets
  ``chat_message_pkey`` bound the scan (review of a26ff7dda).

Mutations (R7): add ``is_brief: bool = False`` to ``SessionUpsertRequest``:
``test_no_save_model_declares_the_mark`` goes red. Change
``brief_message_id`` to ``f"brief_{day}"``: ``test_the_brief_id_is_the_id_each_rule_names``
goes red. Add ``"isBrief"`` to a string in another gateway function:
``test_only_the_session_list_names_the_mark`` goes red. Drop the
``BETWEEN`` from ``_IS_BRIEF_SQL``: ``test_the_probe_is_bounded_on_the_primary_key``
goes red. Add the flag ``i`` to the regex of ``isBriefRow``:
``test_the_sql_rule_is_the_client_rule`` goes red.
"""
from __future__ import annotations

import ast
import re
import uuid
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps" / "services" / "gateway" / "gateway"

#: ASCII digits as two other scripts write them. Built from code points, so
#: no look-alike character sits in the source.
_ARABIC_INDIC = str.maketrans("0123456789", "".join(chr(0x0660 + i) for i in range(10)))
_FULLWIDTH = str.maketrans("0123456789", "".join(chr(0xFF10 + i) for i in range(10)))


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


#: The client rule, ``isBriefRow`` in ``briefTurn.ts``: the regex literal
#: that its body tests ``id`` with, and the flags after its closing slash.
_TS_RULE = re.compile(r"/(\^brief-[^/\n]*\$)/([a-z]*)\.test\(id\)")


def _client_rule() -> tuple[str, str]:
    """The source and the flags of the regex of ``isBriefRow``, read from
    the TypeScript file itself."""
    source = (REPO / "workbench" / "control_plane" / "src" / "lib" / "shell"
              / "briefTurn.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("export function isBriefRow(")
    body = source[start:source.index("\n}\n", start)]
    found = _TS_RULE.findall(body)
    assert len(found) == 1, body
    return found[0]


def test_the_sql_rule_is_the_client_rule() -> None:
    """The SQL rule is ``isBriefRow`` of the client, character for character.
    A JavaScript ``\\d`` is ``[0-9]`` (ASCII only), so the SQL spells it so.
    The Postgres reading of the rule is the R8 suite's
    (``test_an_id_that_only_looks_like_a_brief_is_not_one``), never Python's:
    a Python ``$`` also matches before a final newline."""
    from gateway.routes.chat import _IS_BRIEF_SQL, BRIEF_ID_SQL_PATTERN

    rule, flags = _client_rule()
    assert rule == r"^brief-\d{4}-\d{2}-\d{2}$"
    assert flags == "", "a flag such as i or m changes the rule"
    assert rule.replace(r"\d", "[0-9]") == BRIEF_ID_SQL_PATTERN
    assert f"b.id ~ '{BRIEF_ID_SQL_PATTERN}'" in _IS_BRIEF_SQL


def test_the_probe_is_bounded_on_the_primary_key() -> None:
    """The regex cannot bound an index scan under a non-C collation, so the
    probe would read each row of each chat. The ``BETWEEN`` gives the scan of
    ``chat_message_pkey`` a range on ``id``, and the regex stays the exact
    rule. ``>= 'brief-' AND < 'brief.'`` is wrong under en_US.utf8: it drops
    the real brief, because that collation skips the hyphen at first."""
    from gateway.routes.chat import _IS_BRIEF_SQL, BRIEF_ID_RANGE

    assert BRIEF_ID_RANGE == ("brief-0000-00-00", "brief-9999-99-99")
    assert "b.id BETWEEN 'brief-0000-00-00' AND 'brief-9999-99-99'" in _IS_BRIEF_SQL
    assert "'brief.'" not in _IS_BRIEF_SQL


def test_the_brief_id_is_the_id_each_rule_names() -> None:
    from gateway.assistant_summaries import BRIEF_ROW
    from gateway.routes.chat import BRIEF_ID_PREFIX, BRIEF_ID_RANGE, BRIEF_ID_SQL_PATTERN
    from gateway.routes.chat_brief import HELLO_MESSAGE_ID, brief_message_id
    from gateway.routes.chat_reminder import reminder_message_id

    # ASCII and the whole string: the reading of Postgres and of JavaScript.
    exact = re.compile(BRIEF_ID_SQL_PATTERN.removeprefix("^").removesuffix("$"), re.ASCII)
    low, high = BRIEF_ID_RANGE
    for day in (date(2026, 11, 16), date(2027, 1, 1), date(1, 1, 1), date(9999, 12, 31)):
        mid = brief_message_id(day)
        assert mid.startswith(BRIEF_ID_PREFIX), mid
        assert exact.fullmatch(mid) and BRIEF_ROW.match(mid), mid
        assert low <= mid <= high, mid  # code-point order; R8 holds the DB's order

    others = [
        reminder_message_id(uuid.uuid4()), HELLO_MESSAGE_ID,
        f"reach-{uuid.uuid4()}", f"assistant-t1-{uuid.uuid4().hex}", str(uuid.uuid4()),
        "brief-2026-11-3", "brief-2026-11-16-x", "my-brief-2026-11-16",
        "brief-2026-11-16\n", "Brief-2026-11-16", "brief-2026-11-16".translate(_ARABIC_INDIC),
        "brief-2026-11-16".translate(_FULLWIDTH),
    ]
    for mid in others:
        assert not exact.fullmatch(mid), repr(mid)
    for mid in others[:5]:
        assert not mid.startswith(BRIEF_ID_PREFIX), mid
