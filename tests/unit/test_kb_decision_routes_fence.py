"""No tool or agent path names the accept or reject route of a proposal (K-8).

``POST /knowledge/proposals/{id}/accept`` and ``/reject`` are a person's act, in
the Knowledge app. ``_require_web`` reads the internal token, a member email
and no ``X-Actor-Via`` as a browser. A generic gateway or HTTP tool that named
either route would pass that check. So the source of every tool and agent path
must not name them. Only the chat manifest may, and only as class ``X`` rows
(`test_kb_proposals.py` holds those). The Knowledge app (web client) is the one
caller and is outside this scan.

The scan reads the source of ``acb_skills`` (the chat tools), ``agent_edge``
(the MCP edge), ``acb_auth`` (the ``MCP_ROUTES`` lists) and the gateway outside
``routes/knowledge``. It also checks the route sets of the MCP edge as data.

Mutation (R7): add ``"/knowledge/proposals/{proposal_id}/accept"`` to a tool
file or to ``MCP_ROUTES`` and a test below fails.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_DECISION = re.compile(r"proposals[^\n]{0,60}/(accept|reject)\b")
_ALLOWED = {ROOT / "packages/acb_skills/acb_skills/knowledge_manifest.py"}
_SCANNED = (
    ROOT / "packages/acb_skills/acb_skills",
    ROOT / "apps/services/agent_edge",
    ROOT / "packages/acb_auth/acb_auth",
    ROOT / "apps/services/gateway/gateway",
)
_OWNER = ROOT / "apps/services/gateway/gateway/routes/knowledge"


def _sources() -> list[Path]:
    out: list[Path] = []
    for base in _SCANNED:
        for path in sorted(base.rglob("*.py")):
            if path in _ALLOWED or _OWNER in path.parents or "__pycache__" in path.parts:
                continue
            out.append(path)
    return out


def _hits(text: str) -> list[str]:
    return [m.group(0) for m in _DECISION.finditer(text)]


def test_the_scan_is_not_vacuous() -> None:
    assert len(_sources()) > 100
    assert _hits('"/knowledge/proposals/{proposal_id}/accept"')
    assert _hits("f'/knowledge/proposals/{pid}/reject'")
    assert not _hits('"/knowledge/proposals"')
    # The one allowed file does name both, so the pattern matches real source.
    manifest = (ROOT / "packages/acb_skills/acb_skills/knowledge_manifest.py").read_text(encoding="utf-8")
    assert len(_hits(manifest)) >= 2


def test_no_tool_or_agent_source_names_accept_or_reject() -> None:
    found = {
        str(p.relative_to(ROOT)): hits
        for p in _sources() if (hits := _hits(p.read_text(encoding="utf-8")))
    }
    assert not found, (
        "These files name the accept or reject route of a proposal. A person "
        f"decides in the Knowledge app and no tool does: {found}"
    )


def test_the_mcp_route_sets_hold_no_proposal_decision() -> None:
    from acb_auth import agent_oauth

    bad = sorted(
        (m, p) for m, p in (agent_oauth.MCP_ROUTES | agent_oauth.MCP_WRITE_ROUTES)
        if _DECISION.search(p)
    )
    assert not bad, bad


def test_the_chat_manifest_keeps_both_routes_as_class_x() -> None:
    from acb_skills import knowledge_manifest as m

    rows = [r for r in m.MANIFEST if _DECISION.search(r.path)]
    assert len(rows) == 2
    assert all(r.cls == "X" for r in rows), [(r.path, r.cls) for r in rows]
