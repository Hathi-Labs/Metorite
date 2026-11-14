"""The Connect card of a Studio save in the chat (WS-STU I8d, ``ai_studio.md`` §14 row I8d).

Three facts, held with no network and no database:

1. No parameter of any Studio tool is a secret name (``is_secret_key``), so Mira has no field for a key.
2. The save result holds only the server names of the draft, and none of them looks like a secret.
3. The card source holds no Input and no textarea (the twin of this fact is in
   ``src/components/studio/StudioConnectCard.test.ts``).

⚠️ What a test cannot do: it cannot stop a model from putting a typed key into a tool argument or into
the transcript. The manifest validator refuses a secret-shaped string in a draft (``SECRET_SHAPES``), and
a connection name must match ``CONNECTION_NAME_RE``, so a key never gets STORED. A full guarantee needs a
transcript scrub, which is out of scope. The done-when is "never asks, never carries".
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from acb_common.secret_shapes import is_secret_key, looks_like_secret
from acb_common.studio_manifest import validate_manifest
from acb_skills import studio_tools as st

ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "workbench/control_plane/src/components/studio/StudioConnectCard.tsx"
PKG = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"

ALL_TOOLS = (
    *st.STUDIO_TOOLS, *st.STUDIO_PACKAGE_TOOLS, *st.STUDIO_RECORD_TOOLS, *st.STUDIO_SKILL_TOOLS,
    *st.STUDIO_WORKFLOW_TOOLS,
)


def test_the_tool_lists_are_not_empty() -> None:
    assert len(ALL_TOOLS) >= 23


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.__name__)
def test_no_studio_tool_parameter_is_a_secret_name(tool) -> None:
    names = list(inspect.signature(tool).parameters)
    assert [n for n in names if is_secret_key(n)] == []


def test_the_save_result_holds_only_server_names() -> None:
    names = ["mcp:hubspot", "mcp:slack", "gmail"]
    manifest = {"studio": 1, "id": "crm-app", "name": "CRM app", "connections": names}
    reply = st._Reply(200, {"package": {"id": PKG, "live_version": None, "manifest": manifest}})
    result = st._saved_package(reply)
    assert result["ok"] is True
    text = repr(result)
    for name in names:
        assert name in text
        assert not looks_like_secret(name)
    assert not looks_like_secret(text)
    # The manifest text is never in the result, only its outline.
    assert "manifest" not in result


@pytest.mark.parametrize(
    "name", ["sk-live-0123456789abcdef0123456789abcdef", "ghp_0123456789abcdefghijklmnopqrstuvwxyz0123"],
)
def test_a_key_in_a_connection_name_never_reaches_a_stored_draft(name: str) -> None:
    """The validator refuses a secret-shaped name, so a typed key is not STORED."""
    manifest = {"studio": 1, "id": "crm-app", "name": "CRM app", "connections": [name]}
    errors = validate_manifest(manifest)
    assert any(e.path.startswith("$.connections") for e in errors), errors


def test_the_card_source_holds_no_input_and_no_textarea() -> None:
    src = re.sub(r"/\*.*?\*/", "", CARD.read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"Input|textarea|password|api_?key", src, re.I)
