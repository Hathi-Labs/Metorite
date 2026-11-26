"""The synthetic mailbox and the project tree of the email-to-Projects eval.

Every name, domain and address is made up. No real customer data is here.
Each address ends in ``.example`` or ``.si`` (the made-up domain of Aroh).

The mailbox holds the shapes of the failed chat of 2026-11-11:

* Four existing projects, each with mail: ``Vozzo Creditvision``, ``TATV``,
  ``Kestrel Rollout`` and ``Lumen Billing``.
* ``Aroh`` is found only as a domain (``aroh.si``). No subject and no body
  holds the word.
* ``Attero`` is in one mail and ``Aroh`` is in two others. No single mail holds
  both names, so a search for both words together finds nothing.
* ``Zephyr`` has no mail at all.
"""
from __future__ import annotations

from evals.one_assistant_measure.mailbox import Mail, uid

#: The names that the cases use.
EXISTING = ("Vozzo Creditvision", "TATV", "Kestrel Rollout", "Lumen Billing")
NEW_PRODUCT = "Aroh"
NEW_CHILD = "Attero"
NO_MAIL = "Zephyr"

VOZZO = Mail(
    uid(9001), "Asha Verma", "asha@vozzo-cv.example", "Vozzo Creditvision: UAT sign-off moves to 20 Nov",
    "Hi VJ,\n\nThe bank asked for a second UAT round on the scoring screens. Sign-off moves "
    "from 12 Nov to 20 Nov. We need two more testers from your side.\n\nAsha",
    days_ago=2, thread_id="t-vozzo",
)
TATV = Mail(
    uid(9002), "Dev Nair", "dev@tatv-ops.example", "TATV pilot: two of three sites are live",
    "Hi VJ,\n\nTATV pilot status: the Pune and Indore sites are live. The Nagpur site waits "
    "for the network link, due 15 Nov. No blocker for us.\n\nDev",
    days_ago=3, thread_id="t-tatv",
)
KESTREL = Mail(
    uid(9003), "Mala Rao", "mala@kestrel-rail.example", "Kestrel Rollout: vendor contract signed",
    "Hi VJ,\n\nThe Kestrel Rollout vendor contract is signed. Site survey starts on 18 Nov. "
    "The plan holds.\n\nMala",
    days_ago=5, thread_id="t-kestrel",
)
LUMEN = Mail(
    uid(9004), "Iqbal Shah", "iqbal@lumen-pay.example", "Lumen Billing: go-live on hold",
    "Hi VJ,\n\nOur finance team froze the budget. The Lumen Billing go-live is on hold until "
    "December. We cannot pay the October invoice yet.\n\nIqbal",
    days_ago=1, thread_id="t-lumen",
)
#: Aroh: the name is only in the address (``aroh.si``). The body never says it.
AROH_1 = Mail(
    uid(9005), "Ravi Menon", "ravi@aroh.si", "Pilot scope for the first release",
    "Hi VJ,\n\nWe want the dashboard module in the pilot by 30 Nov. The budget is approved: "
    "INR 4,00,000. Please send the plan by Friday.\n\nRavi Menon\nHead of Product",
    days_ago=4, thread_id="t-aroh-1",
)
AROH_2 = Mail(
    uid(9006), "Nisha Pillai", "nisha@aroh.si", "Who owns the data export?",
    "Hi VJ,\n\nOur data team needs one owner on your side for the export format. Can you "
    "name a person this week?\n\nNisha",
    days_ago=2, thread_id="t-aroh-2",
)
#: Attero: one mail, and it never says Aroh.
ATTERO = Mail(
    uid(9007), "Sunil Das", "sunil@attero-labs.example", "Attero integration: API keys sent",
    "Hi VJ,\n\nThe Attero sandbox API keys are in the shared vault. Kickoff call on Monday "
    "at 10:00. We need the webhook list before then.\n\nSunil",
    days_ago=1, thread_id="t-attero",
)

MAILS: tuple[Mail, ...] = (VOZZO, TATV, KESTREL, LUMEN, AROH_1, AROH_2, ATTERO)

#: The mail that backs each existing project, and the health it shows.
STATUS_MAIL = {
    "Vozzo Creditvision": (VOZZO, "at_risk"),
    "TATV": (TATV, "on_track"),
    "Kestrel Rollout": (KESTREL, "on_track"),
    "Lumen Billing": (LUMEN, "off_track"),
}


def node_id(n: int) -> str:
    """A fixed Projects id of the fixture tree."""
    return f"7a5e0000-0000-4000-9000-{n:012d}"


SPACE = node_id(10)
PROJECT_IDS = {name: node_id(11 + i) for i, name in enumerate(EXISTING)}
