"""The setup text of an outside agent is one block in three places (MCP-Gc).

R7: this file is the fence. The block in docs/external_agents_connect.md must
equal the fenced block of the playbook file kit/paste-in.md, byte for byte.
The Copy control in the AI agents view has its own pin:
workbench/control_plane/src/app/integrations/lib/agentSetupText.test.ts.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_KIT = _ROOT / "packages/acb_playbook/acb_playbook/content/kit/paste-in.md"
_DOC = _ROOT / "docs/external_agents_connect.md"
_FENCE = re.compile(r"```text\n(.*?)\n```", re.S)
_MAX_CHARS = 3000


def _kit_block() -> str:
    text = _KIT.read_text(encoding="utf-8").replace("\r\n", "\n")
    blocks = _FENCE.findall(text)
    assert len(blocks) == 1, "kit/paste-in.md must hold exactly one fenced text block"
    return blocks[0]


def _doc_block() -> str:
    text = _DOC.read_text(encoding="utf-8").replace("\r\n", "\n")
    start, end = "<!-- BEGIN:agent-setup-text -->", "<!-- END:agent-setup-text -->"
    assert text.count(start) == 1 and text.count(end) == 1, "the doc needs one pair of markers"
    between = text.split(start, 1)[1].split(end, 1)[0]
    blocks = _FENCE.findall(between)
    assert len(blocks) == 1, "the markers must hold exactly one fenced text block"
    return blocks[0]


def test_the_doc_block_equals_the_kit_block():
    assert _doc_block() == _kit_block()


def test_the_block_fits_the_limit():
    assert len(_kit_block()) <= _MAX_CHARS


def test_the_block_names_the_rules_the_connection_cannot_give():
    block = _kit_block()
    for needle in ("worktree", "short_id", "add_task_reference", "record_outside_decision", "status_id", "120"):
        assert needle in block, f"the block lacks {needle}"
    assert "PR #N" in block  # it names the trap to avoid
