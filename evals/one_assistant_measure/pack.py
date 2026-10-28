"""The names that the measure shares about the email assistant (WS-67 OA-5, OA-7h).

OA-5 built the email tool pack here as an EVAL fixture: it changed the
orchestrator that the real factory built. OA-7 shipped the real pack
(``acb_skills.tool_packs``), and OA-7h removed the fixture. The ``pack`` arm
now sets the real flag ``TOOL_PACKS`` and ``TOOL_PACKS_ORGS`` in the eval
process (``run.pack_flag``), and the real run loads the pack.

What stays is the set of names that ``run.py`` and ``checks.py`` share:

* :data:`EA`, the agent that the delegation arm calls and that a hand-off
  for setup work still reaches on the pack arm.
* :data:`DELEGATE_TOOL`, the orchestrator's specialist tool for that agent.
* :func:`email_tool_names`, the tools in the agent's ``own_tool_scope``. The
  checks use it to find the email tool calls of a run.
* :data:`REFUSED`, the answer of the OA-5 fixture to a hand-off. The real
  pack uses the same text (``tool_packs._EMAIL_DELEGATION_REFUSED``), and
  ``tests/unit/test_tool_packs_choice.py`` pins the two equal. So the file
  stays for that fence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EA = "email-assistant"
ORCHESTRATOR = "orchestrator"
#: The orchestrator's specialist tool for the email assistant.
DELEGATE_TOOL = "email_assistant"
#: The answer to a hand-off to the email assistant while the run holds both
#: email packs. The measure runs with ``email`` alone, so it sees no such text.
REFUSED = (
    "Not run. In this chat the email tools are yours: call them yourself. "
    "No email agent exists to hand the task to."
)
EA_DIR = Path(__file__).resolve().parents[2] / "apps" / "agents" / "agent-email-assistant"


def email_config() -> dict[str, Any]:
    return json.loads((EA_DIR / "config.json").read_text(encoding="utf-8"))


def email_tool_names() -> frozenset[str]:
    """The email assistant's own tools, as ``own_tool_scope`` names them."""
    return frozenset(email_config().get("own_tool_scope") or ())
