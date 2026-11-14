"""WS-55 MCP-G19a: the count equals the list, for each caller and filter (R8).

CIV-33e (task 3075) moved this test out of ``test_board_stats_r8.py``, which
was one file of about 550 s. The test body is unchanged. The seed and the
helpers live in ``_board_stats_r8.py``. Run::

    uv run pytest tests/unit/test_board_stats_filters_r8.py -v -rs
"""

from __future__ import annotations

import pytest

from tests.unit import _board_stats_r8 as shared
from tests.unit._board_stats_r8 import (
    _DB_GATE,
    CALLERS,
    FILTERS,
    _list_all,
    _stats,
)

world = shared.world
armed = shared.armed
board = shared.board


@_DB_GATE
class TestTheCountEqualsTheList:
    """The pin of the slice: ``total`` of the stats is ``total`` of the list
    and the number of rows that the list returns, for each caller and filter."""

    @pytest.mark.parametrize("who", sorted(CALLERS))
    async def test_every_filter_for_every_caller(self, armed, board, who):
        for query in FILTERS:
            rows, total = await _list_all(armed, who, query)
            stats = await _stats(armed, who, query)
            assert stats["total"] == total == len(rows), (who, query)
