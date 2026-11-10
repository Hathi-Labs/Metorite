"""The stub email agent of the Projects operations eval (WS-46 P9).

Spec: ``project-docs/specs/projects_agent_parity.md`` §11.1 item 3 and §9.4.

``call_agent("email-assistant", …)`` answers with ONE fixed, synthetic mail
from Priya Menon. No real mailbox and no real data are involved. Any other
agent name fails the way an unknown agent does, so a run that asks the CRM
agent, or a misspelt name, shows up in the evidence.

The stub takes the place of ``orchestrator.executor._run_sub_agent_streaming``,
the one function that ``call_agent`` awaits for a callee. So the real
``call_agent`` runs: its cycle check, its depth check and its answer framing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EMAIL_AGENT = "email-assistant"

#: The subject, the date and the message id of the synthetic mail.
SUBJECT = "Launch kits"
SENT = "2026-10-20"
MESSAGE_ID = "msg-eval-0001"

#: What email-assistant returns, in the shape that §9.4 asks for.
ANSWER = (
    f"Mail from Priya Menon <priya.menon@eval.example>, subject «{SUBJECT}», "
    f"sent {SENT}, message id {MESSAGE_ID}. Requests:\n"
    "1. Pack 40 kits. Owner: Asha Iyer. Due Friday.\n"
    "2. Print the kit labels. Owner: Asha Iyer. Due Friday.\n"
    "3. Book the courier. Owner: Priya Menon. Due next Monday."
)


@dataclass
class EmailStub:
    """Records each delegation, and answers the email agent."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    async def run_sub_agent(
        self, agent_name: str, message: str, run_id: str, event_queue=None, model=None,
    ) -> str:
        self.calls.append((agent_name, message))
        if agent_name != EMAIL_AGENT:
            raise LookupError(f"no agent named {agent_name!r} in this eval")
        return ANSWER
