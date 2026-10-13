"""The routing test set of the one assistant (NS-6 slice 6d, D99).

Spec: ``project-docs/specs/navigation_shell.md`` §7.1 and slice 6d of NS-6.

The member always talks to ONE agent, ``orchestrator``. Each case is one
member question, the app the member asks it from, and the specialist that
should answer it. ``expected`` is ``None`` when the orchestrator should answer
itself. ``asks`` is True when the right act is to ask the member one short
question first (``ASSISTANT_PERSONA``: "When you cannot tell which record ...
ask one short question with ask_questions before you act").

Every agent id here is a name of the REAL registry
(``gateway.routes.agent._AGENT_REGISTRY``). The runner checks each one against
the registry and against the tools of the real orchestrator, so a typo or a
renamed agent fails the run. An app is a pane ``href`` of
``workbench/control_plane/src/lib/nav.ts``, and the runner reads the pane's
label from that file, so the page context is the one the dock sends.

The words, names and addresses are made up. Every address ends in
``.example``. The ids in the facts are fixed UUIDs of no real record.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The member who asks, and the organization of the run. Neither is real.
MEMBER = "member@example.com"
ORG = "aaaaaaaa-0000-0000-0000-0000000000d6"

#: Fixed ids that the facts name, in the shape the apps publish them.
PROJECT_ID = "6d000000-0000-4000-8000-000000000001"
TASK_ID = "6d000000-0000-4000-8000-000000000002"
EMAIL_ID = "6d000000-0000-4000-8000-000000000003"
ACCOUNT_ID = "6d000000-0000-4000-8000-000000000004"
ITEM_ID = "6d000000-0000-4000-8000-000000000005"


@dataclass(frozen=True)
class Case:
    """One member question, and who should answer it."""

    id: str
    #: The member's words, as typed in the chat bar.
    words: str
    #: The ``href`` of the pane the member is in, or None for a page that
    #: belongs to no app (My Day).
    app: str | None
    #: The specialist that should answer: an agent id of the registry, or
    #: None when ``orchestrator`` answers itself.
    expected: str | None
    #: True when the right act is to ask the member first. Then ``expected``
    #: is None: no specialist runs before the member answers.
    asks: bool = False
    #: The facts the app publishes (``useAssistantFacts``), as data lines.
    facts: tuple[str, ...] = ()
    #: Why this case is here, for a reader of the table.
    note: str = ""


# ── The facts, in the shape of each app's own ``assistantFacts.ts`` ─────────

_PROJECT = (
    f"They have the project «Website relaunch» selected (project_id: {PROJECT_ID}). "
    '"This project" or "here" means it.',
    "They are looking at the board view.",
)
_PROJECT_TASK = (
    *_PROJECT,
    f"They have task #12 «Draft the launch email» open (task_id: {TASK_ID}). "
    '"This task" means it.',
)
_MAILBOX = (
    f"They are looking at the mailbox «member@example.com» (account_id: {ACCOUNT_ID}).",
)
_OPEN_EMAIL = (
    *_MAILBOX,
    f"They have the email «Quote for 40 printers» from «buyer@acme.example» open "
    f'(email_id: {EMAIL_ID}, account_id: {ACCOUNT_ID}). "This email" means it.',
)
_MY_TASKS = (
    'They are looking at the "Next" view of My Tasks.',
    "Their inbox holds 3, next actions 7, waiting-for 2.",
)


CASES: tuple[Case, ...] = (
    # ── Projects → projects-assistant ──────────────────────────────────────
    Case("P1", "Add a task here: send the press kit to the printer, due Friday.",
         "/projects", "projects-assistant", facts=_PROJECT,
         note="a write in the app the member is in"),
    Case("P2", "What is overdue in this project?",
         "/projects", "projects-assistant", facts=_PROJECT,
         note="a read of the open project"),
    Case("P3", "Move this task to Done and tell Priya.",
         "/projects", "projects-assistant", facts=_PROJECT_TASK,
         note="the open task, named only by the page"),
    Case("P4", "How is the Website relaunch project going?",
         None, "projects-assistant",
         note="from My Day: no app, a project named in the words"),

    # ── My Email → email-assistant ─────────────────────────────────────────
    Case("E1", "Summarise the unread mail from this week.",
         "/email", "email-assistant", facts=_MAILBOX),
    Case("E2", "Draft a reply to this email: we can ship on Monday.",
         "/email", "email-assistant", facts=_OPEN_EMAIL,
         note="the open email, named only by the page"),
    Case("E3", "Did Priya answer my email about the Welmont quote?",
         "/projects", "email-assistant", facts=_PROJECT,
         note="cross-app: asked in Projects, about an email"),
    Case("E4", "Find the last email from Acme about their renewal.",
         "/crm", "email-assistant",
         note="cross-app: asked in CRM, about an email"),

    # ── CRM → crm-assistant ────────────────────────────────────────────────
    Case("C1", "How many deals are in the Negotiation stage, and what are they worth?",
         "/crm", "crm-assistant"),
    Case("C2", "Create a lead for Ravi Kumar at Zenith Labs, ravi@zenith.example.",
         "/crm", "crm-assistant"),
    Case("C3", "Log a call on the Acme deal: they want a demo next week.",
         "/email", "crm-assistant", facts=_OPEN_EMAIL,
         note="cross-app: asked in Email, a CRM write"),
    Case("C4", "Which deals did we win last month?",
         "/projects", "crm-assistant", facts=_PROJECT,
         note="cross-app: asked in Projects, about the pipeline"),

    # ── My Tasks and Calendar → task-manager ───────────────────────────────
    Case("T1", "Capture a to-do: renew the domain before the 30th.",
         "/tasks", "task-manager", facts=_MY_TASKS),
    Case("T2", "What should I work on next?",
         "/tasks", "task-manager", facts=_MY_TASKS),
    Case("T3", "Block two hours tomorrow morning to write the board report.",
         "/calendar", "task-manager",
         note="Calendar has no agent of its own (ALSO_SERVES in assistant.ts)"),
    Case("T4", "Remind me to call Acme back on Thursday.",
         "/crm", "task-manager",
         note="cross-app: a personal to-do asked in CRM"),

    # ── My WhatsApp → whatsapp-assistant ───────────────────────────────────
    Case("W1", "Summarise today's messages in the Suppliers group.",
         "/whatsapp", "whatsapp-assistant"),
    Case("W2", "Draft a reply to Arjun's last WhatsApp message.",
         "/whatsapp", "whatsapp-assistant"),
    Case("W3", "What did Meera say on WhatsApp about the delivery?",
         "/email", "whatsapp-assistant", facts=_MAILBOX,
         note="cross-app: asked in Email, about a WhatsApp chat"),

    # ── Integrations and App Workshop ──────────────────────────────────────
    Case("I1", "Help me set up the API key for our SendGrid account.",
         "/integrations", "apis-config"),
    Case("B1", "Build me a small app that tracks visitor sign-ins at the front desk.",
         "/chat", "app-builder",
         note="App Workshop is a preview pane, so the page does not move"),

    # ── orchestrator answers itself ────────────────────────────────────────
    Case("N1", "What can you do?", "/chat", None),
    Case("N2", "Good morning!", None, None, note="from My Day"),
    Case("N3", "Thanks, that is all for now.", "/people", None),

    # ── Ambiguous: ask the member first ────────────────────────────────────
    Case("A1", "Move it to next week.", None, None, asks=True,
         note="no page facts: which item?"),
    Case("A2", "Send Ravi a message that I am running late.", "/chat", None, asks=True,
         note="email or WhatsApp, and which Ravi?"),
    Case("A3", "Delete the old one.", "/artifacts", None, asks=True,
         note="which file? A delete needs the record first"),
)


def case(case_id: str) -> Case:
    """The case with *case_id*. A KeyError names a case that does not exist."""
    for c in CASES:
        if c.id == case_id:
            return c
    raise KeyError(case_id)
