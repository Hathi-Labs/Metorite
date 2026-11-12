"""Task 2968, step 1. The AD-10 wrap rule of an answer lives in ONE module.

``acb_common.door_values`` holds :func:`door_value` and the names that it
needs. ``agent_edge.tools`` imports them under its old names, and it stays the
one edge module that maps an answer (EA3-9). ``acb_skills`` cannot import the
edge, so a Studio chat tool imports the rule from ``acb_common``.

Mutations (R7). Each one makes the named test fail.

* M1. Paste a second ``def door_value`` into ``agent_edge/tools.py`` or into
  an ``acb_skills`` module. ``test_one_door_value_in_the_source`` fails.
* M2. Move ``door_value`` back into ``tools.py`` and leave the shared module
  without it. ``test_one_door_value_in_the_source`` fails, because the one
  definition is in the wrong file.
* M3. Wrap the shared rule in a local ``door_value`` of ``tools.py`` that calls
  it. ``test_the_edge_door_value_is_the_shared_one`` fails, and M1 fails too.
* M4. Give ``tools.py`` its own ``SECRET_WORDS`` tuple, with the same words.
  The words are equal, so a parity test stays green.
  ``test_the_edge_secret_words_are_the_shared_ones`` fails.

Run: ``uv run pytest tests/unit/test_door_values_single_copy.py -q``.
"""
from __future__ import annotations

import re
from pathlib import Path

from acb_common import door_values
from agent_edge import tools

_REPO = Path(__file__).resolve().parents[2]

#: The one home of the rule.
_HOME = "packages/acb_common/acb_common/door_values.py"

#: A definition of ``door_value`` at any indent, sync or async.
_DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+door_value\b", re.MULTILINE)


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in (_REPO / "apps", _REPO / "packages"):
        out.extend(
            p for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and ".venv" not in p.parts
            and "node_modules" not in p.parts
        )
    return out


def test_one_door_value_in_the_source() -> None:
    """M1 and M2. The source text of apps/ and packages/ holds exactly one
    ``def door_value``, and it is in the shared module."""
    found = [
        p.relative_to(_REPO).as_posix()
        for p in _python_files()
        for _ in _DEF_RE.finditer(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert found == [_HOME], found


def test_the_scan_sees_a_definition() -> None:
    """The scan regex finds each shape of a definition, so M1 cannot hide."""
    for source in ("def door_value(v):", "    def door_value(v):",
                   "async def door_value(v):", "\tdef  door_value (v):"):
        assert _DEF_RE.search(source), source
    for source in ("door_value(v)", "def door_values(v):", "# def door_valuex"):
        assert not _DEF_RE.search(source), source


def test_the_edge_door_value_is_the_shared_one() -> None:
    """M3. The edge maps a door answer with the shared function itself."""
    assert tools.door_value is door_values.door_value


def test_the_edge_secret_words_are_the_shared_ones() -> None:
    """M4. The edge drops a key by the shared words, not by an equal copy."""
    assert tools.SECRET_WORDS is door_values.SECRET_WORDS
