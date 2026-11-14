"""WS-55 MCP-G19a: the compact list reads the same rows as the plain list (R8).

CIV-33e (task 3075) moved this test out of ``test_board_stats_r8.py``, which
was one file of about 550 s. The test body is unchanged. The seed and the
helpers live in ``_board_stats_r8.py``. Run::

    uv run pytest tests/unit/test_board_stats_compact_r8.py -v -rs
"""

from __future__ import annotations

import pytest

from tests.unit import _board_stats_r8 as shared
from tests.unit._board_stats_r8 import (
    _DB_GATE,
    FILTERS,
    _list_all,
)

world = shared.world
armed = shared.armed
board = shared.board


@_DB_GATE
class TestTheCountEqualsTheList:
    """The pin of the slice: ``total`` of the stats is ``total`` of the list
    and the number of rows that the list returns, for each caller and filter."""

    @pytest.mark.parametrize("who", ["human", "agent", "pinned", "org_b"])
    async def test_the_compact_list_reads_the_same_rows(self, armed, board, who):
        for query in FILTERS:
            plain, total = await _list_all(armed, who, query)
            compact, ctotal = await _list_all(armed, who, query, compact=True)
            assert ctotal == total
            assert {r["id"] for r in compact} == {r["id"] for r in plain}, (who, query)
