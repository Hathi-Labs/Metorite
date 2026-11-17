"""Seven chats from the owner chat of 2026-11-11 (task 2874). Every name is synthetic.

The real chat held seven defects. Each chat below replays the SHAPE of one defect with
synthetic people, mail and addresses. Each has the tags ``t2874`` and ``case:<k>``. The
``t2874`` tag marks the chats that a fix aims at (the T set of ``compare.py``), so the E set
of the email chats leaves them out.

| Case | The defect of 2026-11-11 | The chat | Replay |
|---|---|---|---|
| 1 | The model changed the act: a forward became a new mail | find a mail, then forward it with a note | yes |
| 2 | A promise with no act: "I'll check the thread" | a question that a read of a thread answers | yes |
| 3 | A false doubt after "Sent to" | a reply that the member sends, then "did it go out?" | yes |
| 4 | Wrong card state after a Discard | a reply that the member discards, then "is it waiting?" | yes |
| 5 | "Mark it done" on an email went to Projects | find a mail, then "mark it done" | yes, see below |
| 6 | A first name typed with another spelling | "send Vineeta a note", the contact is "Vinita Rao" | yes, see below |
| 7 | ``send_email`` for a reply | find a mail, then reply to it | yes |

What each replay does NOT prove:

* Case 1. The real redraft came after the member wrote "don't draft a new email". A chat
  message that closes a card is a STEER, and the harness cannot post one in the middle of a run
  (``run.Member`` says why). So the chat replays the FIRST request, the choice of the act.
* Case 5. The 11-11 failure used the Projects tools. A chat of the ``email`` set turns on the
  email pack only, so the Projects tools are in the request only under ``--mirror-prod``. The
  scripted run passes either way, because the known-good plan never searches. Run the live chat
  with ``--mirror-prod``.
* Case 6. ``resolve_person`` is a tool of the orchestrator behind the profile flag
  (``ASSISTANT_PROFILE_ENABLED``). The chat sets the flag in the eval process only (:data:`FLAGS`).
  ``ASSISTANT_PROFILE_ENABLED`` is a key of ``prod_flags.json`` (part 4), so a run with
  ``--mirror-prod`` has it. ``ASSISTANT_PROFILE_ORGS`` is not in the production list, and
  only :data:`FLAGS` sets it, for this chat.

Limit of cases 3 and 4. The harness sends a later turn only the text that the browser sends:
the earlier questions and answers. Production loads the stored session, so turn 2 also sees the
tool results of turn 1. So a live pass or fail of turn 2 of case 3 or case 4 does not prove
what production does. The tool result of the same turn (turn 1) is real.

Every chat holds at most 11 words in a turn, so no turn-kind request goes out.
"""
from __future__ import annotations

from typing import Any

from evals.one_assistant_measure.mailbox import KIRAN, WORK, Mail, uid
from evals.one_assistant_measure.scenarios import (
    ANIL,
    EMAIL_PAGE,
    EXACT_TEXT,
    MEERA,
    OFFICE,
    PCB_3,
    PRIYA,
    Check,
    Plan,
    Scenario,
    Turn,
    _link,
)
from evals.routing import dataset as ds

LAKSHMI = Mail(
    uid(210), "Lakshmi Pillai", "lakshmi@toolroom.example", "Re: Tooling invoice TR-88",
    "Hi VJ,\n\nThe tooling invoice TR-88 is paid. Thanks for the quick turn.\n\nLakshmi",
    days_ago=0.4, thread_id="t-tooling",
)
VINITA = Mail(
    uid(211), "Vinita Rao", "vinita@gardenlab.example", "Sample sizes for the garden kit",
    "Hello,\n\nThe sample sizes are 40 mm and 60 mm.\n\nVinita",
    days_ago=3, is_read=True,
)
#: The mailbox of the seven chats: the office mail and two mails of case 5 and case 6.
MAILS_2874: tuple[Mail, ...] = (*OFFICE, LAKSHMI, VINITA)

#: The settings that one chat adds to its run, by chat id (``run.flag_for``). Case 6 needs
#: ``resolve_person``, a tool that exists only with the assistant profile on for the org.
FLAGS: dict[str, dict[str, Any]] = {
    "t2874_first_name_miss": {"assistant_profile_enabled": True, "assistant_profile_orgs": ds.ORG},
}


def _read(words: str, mail: Mail, sender: str, answer: str) -> Turn:
    """A turn that finds one mail and reads it."""
    return Turn(
        words,
        (Check("read_mail", mail.id), Check("answer_all", (sender,), "names_the_sender")),
        Plan((("query_inbox", {"from_email": mail.address.split("@")[0]}),
              ("read_email", {"email_id": mail.id})), answer))


def _tags(case: int) -> tuple[str, ...]:
    return ("t2874", f"case:{case}")


# 1. The model changed the act: a forward stays a forward.
NOTE = "Kiran, please price this by Friday."
FORWARD = Scenario(
    "t2874_forward_keeps_act", "Case 1: forward a mail with a note, and it stays a forward",
    mails=MAILS_2874, tags=_tags(1), surface="email",
    turns=(
        _read("Find Meera's mail about the mounting brackets.", MEERA, "Meera",
              f"Meera asks for a quote for 40 X-200 brackets, to Pune. {_link(MEERA)}"),
        Turn("Forward that mail to Kiran with a note: price by Friday.",
             (Check("act_kept", ("forward_email", MEERA.id), "stays_a_forward"),
              Check("result_has", r"Forwarded", "forward_went"),
              Check("answer_all", (r"[Ff]orward",), "says_forwarded")),
             Plan((("find_contact", {"name": "Kiran"}),
                   ("forward_email", {"email_id": MEERA.id, "to": [KIRAN], "note": NOTE})),
                  f"Forwarded Meera's mail to {KIRAN} with your note.")),
    ),
)

# 2. A promise with no act: the answer reads the thread now.
PROMISE = Scenario(
    "t2874_no_empty_promise", "Case 2: a question that a read answers, with no promise to check",
    mails=MAILS_2874, tags=_tags(2), surface="email",
    turns=(Turn(
        "Check the Kumar thread on the PCB rework. Still Rs 36,000?",
        (Check("called", ("read_thread", "read_email"), "read_the_thread"),
         Check("no_promise", None, "no_promise_of_a_later_check"),
         Check("answer_all", (r"36,000",), "the_price")),
        Plan((("query_inbox", {"query": "PCB rework"}), ("read_thread", {"email_id": PCB_3.id})),
             "Yes. The rework still costs Rs 36,000 for all 120 boards, and the boards ship on "
             f"3 November. {_link(PCB_3)}")),),
)

# 3. A false doubt: a send that the tool confirmed is sent.
SENT_TO = "priya@boardworks.example"
CONFIRMED = Scenario(
    "t2874_sent_is_confirmed", "Case 3: after 'Sent to', the model does not doubt the send",
    mails=MAILS_2874, tags=_tags(3), surface="email",
    turns=(
        Turn("Reply to Priya: yes, 250 boards is fine. Send it.",
             (Check("reply_card", PRIYA.id),
              Check("result_has", rf"Sent to {SENT_TO}", "tool_said_sent"),
              Check("no_doubt", None, "no_doubt_of_the_send")),
             Plan((("query_inbox", {"from_email": "priya"}),
                   ("compose_reply", {"email_id": PRIYA.id, "account_id": WORK,
                                      "draft": EXACT_TEXT})),
                  f"Sent to {SENT_TO}."), card="send"),
        Turn("Did the reply to Priya go out?",
             (Check("no_doubt", None, "no_doubt_of_the_send"),
              Check("answer_all", (r"\b(?:yes|sent)\b",), "says_it_went")),
             Plan((), f"Yes. The reply went to {SENT_TO}, from your Work mailbox.")),
    ),
)

# 4. Wrong card state: a Discard is final.
DISCARDED = Scenario(
    "t2874_discard_is_final", "Case 4: after a Discard, the answer says 'You discarded it'",
    mails=MAILS_2874, tags=_tags(4), surface="email",
    turns=(
        Turn("Reply to Anil and confirm 14 November.",
             (Check("called", ("compose_reply",), "showed_the_card"),
              Check("states_discard", None, "says_discarded")),
             Plan((("query_inbox", {"from_email": "anil"}),
                   ("compose_reply", {"email_id": ANIL.id, "account_id": WORK,
                                      "instruction": "Confirm delivery on 14 November."})),
                  "You discarded the reply, so I will not send it. Tell me if you want a new one."),
             card="discard"),
        Turn("Is that reply still waiting for me?",
             (Check("states_discard", None, "says_discarded"),),
             Plan((), "No. You discarded it. Tell me if you want a new reply.")),
    ),
)

# 5. "Mark it done" on an email is mark_thread_done.
DONE = Scenario(
    "t2874_mark_email_done", "Case 5: 'mark it done' on an email is mark_thread_done",
    mails=MAILS_2874, tags=_tags(5), surface="email",
    turns=(
        _read("Show me the latest mail from Lakshmi.", LAKSHMI, "Lakshmi",
              f"Lakshmi says that invoice TR-88 is paid. {_link(LAKSHMI)}"),
        Turn("Mark it done.",
             (Check("done_direct", LAKSHMI.id, "mark_thread_done_first"),
              Check("answer_all", (r"\bdone\b",), "says_done")),
             Plan((("mark_thread_done", {"account_id": WORK, "email_id": LAKSHMI.id}),),
                  "The thread is marked done.")),
    ),
)

# 6. A first-name miss: the closest spelling of find_contact, before a question.
MISS = Scenario(
    "t2874_first_name_miss", "Case 6: 'nobody matches' tries find_contact with the same name",
    mails=MAILS_2874, tags=_tags(6), surface="email",
    turns=(Turn(
        "Send Vineeta a note: the samples are ready.",
        (Check("tried_closest", r"vineeta", "find_contact_with_the_name"),
         Check("no_write_before_question", None, "asked_before_a_write")),
        Plan((("resolve_person", {"name": "Vineeta"}), ("find_contact", {"name": "Vineeta"})),
             "I found no exact match for Vineeta. The closest is Vinita Rao, "
             "vinita@gardenlab.example. Is she the person?")),),
)

# 7. A reply goes through compose_reply, never send_email.
REPLY = Scenario(
    "t2874_reply_by_compose", "Case 7: a reply is compose_reply, with no wasted send_email round",
    mails=MAILS_2874, tags=_tags(7), surface="email",
    turns=(
        _read("Find Priya's mail about the board order.", PRIYA, "Priya",
              f"Priya asks for 250 boards instead of 200. {_link(PRIYA)}"),
        Turn("Reply to Priya: yes, 250 boards is fine.",
             (Check("reply_card", PRIYA.id),
              Check("reply_by_compose", PRIYA.id, "compose_not_send")),
             Plan((("compose_reply", {"email_id": PRIYA.id, "account_id": WORK,
                                      "draft": EXACT_TEXT}),),
                  "Saved in Drafts of Work, to Priya.")),
    ),
)

SCENARIOS_2874: tuple[Scenario, ...] = (FORWARD, PROMISE, CONFIRMED, DISCARDED, DONE, MISS, REPLY)
assert all(s.page == EMAIL_PAGE and "t2874" in s.tags for s in SCENARIOS_2874)
