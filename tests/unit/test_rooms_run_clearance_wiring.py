"""Source fence: the run path consults the run clearance (no database needed).

Moved out of ``tests/unit/test_rooms.py``, whose module skips with no
DATABASE_URL, so this source-only check never ran in CI. It reads source text
and needs no database, model or Redis.
"""

from __future__ import annotations

import re
from pathlib import Path

_GATEWAY = Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway" / "gateway"


def _endpoint_source() -> str:
    text = (_GATEWAY / "routes" / "agent.py").read_text(encoding="utf-8")
    start = text.index("async def run_agent_stream_endpoint(")
    nxt = re.search(r"^(?:@|async def |def )", text[start + 10:], re.MULTILINE)
    return text[start : start + 10 + nxt.start()] if nxt else text[start:]


def test_the_run_path_is_wired_to_the_run_clearance() -> None:
    """The rule with the quietest failure mode, pinned where it is applied.

    *What* a shared room may read and write is decided by
    ``acb_memory.resolve_clearance`` and tested against that function directly
    (``tests/unit/test_memory_compartments.py``). This test checks that the
    run path consults it at every seam. The endpoint sets the clearance and
    the memory tools target. ``memory_preread.py`` builds the prompt block
    (it left ``agent.py`` in WS-47 WAC-P2).
    """
    src = _endpoint_source()
    preread = (_GATEWAY / "memory_preread.py").read_text(encoding="utf-8")

    # 1. The clearance is resolved from the room's sharedness, once.
    assert "resolve_clearance(" in src
    assert "shared=_room_is_shared," in src
    # 2. The memory TOOLS read and write the run's compartment.
    assert "_set_memory_user_id(_clearance.write)" in src
    # 3. The endpoint hands the memory builder the sharedness and the clearance.
    assert "await memory_preread(" in src
    assert "clearance=_clearance," in src
    # 4. The builder reads the room and prefs compartments, and the personal
    #    one only when the run is not shared.
    assert 'has_user = user_id != "anonymous" and not shared' in preread
    assert "if clearance.room else _none()" in preread
    assert "if clearance.prefs else _none()" in preread
    # 5. The cached block is keyed by clearance, or a thread cached while solo
    #    would serve the owner's private block to the room for the whole TTL.
    assert "clearance=clearance.fingerprint," in preread
    # 6. Run-boundary extraction targets the compartment, not the typer...
    assert "_extract_user = _clearance.write if _room_is_shared else _mem_user" in src
    # ...and the session owner / authorship actor is still a real email.
    assert '_mem_user = (user.email or "").strip()' in src
