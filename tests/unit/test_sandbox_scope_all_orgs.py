"""SBX-F24 — the ``tools`` target in the scope parser (``agent_sandbox.md`` §0c, D227).

What breaks this fence:

- ``tools`` is absent from ``MAF_CODING_TARGETS``, so ``tools:*`` or
  ``tools:<org>`` parses to an error. The parser then fails closed and turns
  OFF every target of the box: ``code_task``, ``app_builder``, ``projects``
  and ``compute`` (§0c item 4). SBX-4a adds the target first, so that an
  early ``tools:`` entry costs nothing.
- An unknown target stops failing closed.

The clause "an org in ``user_tool_disabled_orgs`` still runs" waits for
SBX-6, which adds that setting. The guard that refuses ``tools:*`` while
self-serve signup is on waits for SBX-9 (§0c). Both join this file then.
"""
from __future__ import annotations

from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

ORG = "aaaaaaaa-0000-0000-0000-00000000004a"
OTHER = "bbbbbbbb-0000-0000-0000-00000000004a"
TARGETS = ("code_task", "app_builder", "projects", "compute", "tools")


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch) -> Any:
    from acb_common import get_settings

    settings = get_settings()

    def set_scope(raw: str) -> None:
        monkeypatch.setattr(settings, "maf_coding_scope", raw)

    return set_scope


def test_tools_is_a_scope_target() -> None:
    assert sb.TOOLS_TARGET == "tools"
    assert sb.TOOLS_TARGET in sb.MAF_CODING_TARGETS
    assert sb.parse_maf_coding_scope("tools:*") == frozenset({("tools", "*")})
    assert sb.parse_maf_coding_scope(f"tools:{ORG}") == frozenset({("tools", ORG)})


def test_a_tools_entry_keeps_every_other_target_on(scope: Any) -> None:
    scope(f"code_task:{ORG},app_builder:{ORG},projects:{ORG},compute:{ORG},tools:{ORG}")
    for target in TARGETS:
        assert sb.maf_coding_scope_allows(target, ORG), target
        assert not sb.maf_coding_scope_allows(target, OTHER), target
    scope("code_task:*,tools:*")
    assert sb.maf_coding_scope_allows("code_task", ORG)
    assert sb.maf_coding_scope_allows("tools", ORG)
    assert not sb.maf_coding_scope_allows("projects", ORG)


def test_an_unknown_target_still_fails_closed(scope: Any) -> None:
    with pytest.raises(ValueError, match="unknown target"):
        sb.parse_maf_coding_scope("toolz:*")
    scope(f"code_task:{ORG},toolz:{ORG}")
    for target in TARGETS:
        assert not sb.maf_coding_scope_allows(target, ORG), target
