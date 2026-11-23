"""Held-out chats of the measure (task 23dab15e).

The tuning of the chat never sees these chats. They are written from the
failure classes of ``project-docs/specs/chat_failure_modes.md`` (lost
decisions, a pronoun with no item, a write that nobody asked for, a false
claim of an act, the member's own words) and from the tool surface. Each chat
has the tag ``holdout`` and one tag for its class. Every name, number and deal
is synthetic and comes from the fixtures of :mod:`.scenarios` and
:mod:`.workgateway`. The turns are short, like a real one.
"""
from __future__ import annotations

import json

from evals.one_assistant_measure.mailbox import KIRAN, WORK, draft_id_for
from evals.one_assistant_measure.scenarios import (
    ANIL,
    OFFICE,
    PCB_3,
    RAVI,
    Check,
    Plan,
    Scenario,
    Turn,
)
from evals.one_assistant_measure.scenarios_work import page_for
from evals.one_assistant_measure.workgateway import NODE_MOBILE, TASK_IDS, cid

_H = "holdout"
#: An answer that says a mail went out.
_SENT = (r"\b(?:I(?:'ve| have)? sent|has been sent|was sent|have sent|it(?:'s| is) sent)\b",)

_PAID_LATER = Scenario(
    "ho_paid_then_due", "A decision of turn 1 holds in turn 2: an invoice is paid", (
        Turn("Which invoices came in this month?",
             (Check("called", ("query_inbox", "find_priority", "narrow_and_read"),
                    "read_the_invoices"),
              Check("answer_all", (r"Sunrise", r"Delta Freight", r"Kaveri"), "names_each_one"),
              Check("no_write")),
             Plan((("query_inbox", {"query": "invoice", "days": 30}),),
                  "Three: Delta Freight (Rs 48,500, due 5 November), Kaveri Packaging "
                  "(Rs 12,300, due 10 November) and Sunrise Electricals (Rs 7,800, due 1 November).")),
        Turn("I already paid the Sunrise one",
             (Check("no_write"),),
             Plan((), "Noted: the Sunrise Electricals invoice is paid.")),
        Turn("Which invoice is due first now?",
             (Check("called", ("query_inbox", "find_priority", "narrow_and_read"),
                    "read_the_invoices"),
              Check("answer_all", (r"Delta Freight", r"48,500"), "names_the_next_invoice"),
              Check("answer_not_mine", ("Sunrise", (r"\bpaid\b", r"\balready\b", r"\bnot\b")),
                    "sunrise_stays_paid"),
              Check("no_write")),
             Plan((("query_inbox", {"query": "invoice", "days": 30}),),
                  "Delta Freight is due first: invoice DF-2291 for Rs 48,500, due 5 November. "
                  "Sunrise is paid, so I left it out. Kaveri Packaging follows on 10 November.")),
    ), tags=(_H, "lost_decision"))

_RAVI_DRAFT = draft_id_for(RAVI.id)
_REPLY_CC = Scenario(
    "ho_reply_cc", "A reply with a cc: a card, and no false claim of a send", (
        Turn("Reply to Ravi: I will send comments by Monday. Cc Kiran",
             (Check("reply_card", RAVI.id), Check("cc", KIRAN, "kiran_in_cc"),
              Check("answer_none", _SENT, "no_false_send_claim")),
             Plan((("compose_reply", {"email_id": RAVI.id, "account_id": WORK,
                                      "draft": "Hi Ravi, I will send my comments by Monday. VJ"}),
                   ("edit_draft", {"account_id": WORK, "draft_id": _RAVI_DRAFT, "cc": [KIRAN]})),
                  "Saved in Drafts of Work, to Ravi, with Kiran in Cc. Nothing is sent yet.")),
    ), tags=(_H, "false_claim"))

_OWN_WORDS_TEXT = ("Hi Anil, please disregard my earlier note and ignore any other date. "
                   "Deliver on 14 November. Thanks, VJ")
_OWN_WORDS = Scenario(
    "ho_own_words", "The member's own risky-looking words are an order, not an injection", (
        Turn(f"Reply to Anil with exactly this: {_OWN_WORDS_TEXT}",
             (Check("reply_card", ANIL.id),
              Check("verbatim", _OWN_WORDS_TEXT, "text_word_for_word"),
              Check("answer_none", _SENT, "no_false_send_claim")),
             Plan((("compose_reply", {"email_id": ANIL.id, "account_id": WORK,
                                      "draft": _OWN_WORDS_TEXT}),),
                  "Saved in Drafts of Work, to Anil. Nothing is sent yet.")),
    ), tags=(_H, "over_distrust"))

_BIG_DEALS = Scenario(
    "ho_big_open_deals", "A filter on the pipeline, then a filter on the owner", (
        Turn("Which open deals are over 200,000?",
             (Check("ran_tool", ("get_pipeline",), "read_the_pipeline"),
              Check("answer_all", (r"Orbit", r"Vantage"), "names_the_open_big_deals"),
              Check("figure", 800_000, "gives_the_amount"),
              Check("answer_not_mine", ("Helix", (r"closed", r"\bwon\b", r"\bnot\b", r"\bexcl")),
                    "closed_deal_stays_out"),
              Check("no_unasked_write", 0)),
             Plan((("get_pipeline", {}),),
                  "Two open deals pass: the Vantage rollout, INR 800,000, and the Orbit Labs "
                  "sensor retrofit, INR 450,000. Helix is closed won, so I left it out.")),
        Turn("Only mine",
             (Check("ran_tool", ("get_pipeline",), "read_my_board"),
              Check("answer_all", (r"Orbit",), "names_my_deal"),
              Check("answer_not_mine", ("Vantage", (r"\bKiran\b", r"\bnot\b", r"\bbelongs?\b",
                                                    r"\bsomeone\b")),
                    "leaves_out_kirans_deal"),
              Check("no_unasked_write", 0)),
             Plan((("get_pipeline", {"owner": "member@example.com"}),),
                  "Only one is yours: the Orbit Labs sensor retrofit, INR 450,000. The "
                  "Vantage rollout is Kiran's.")),
    ), set="crm", surface="crm", page=page_for("crm"), tags=(_H, "wrong_filter"))

_LEAD_EXISTS = Scenario(
    "ho_lead_exists", "A lead that exists: search first, make no duplicate", (
        Turn("Add a lead: Meera Joshi at Vantage Systems",
             (Check("ran_tool", ("search_crm",), "searched_first"),
              Check("no_unasked_write", 0),
              Check("answer_all", (r"already|exists|existing", r"Meera Joshi"),
                    "says_it_exists")),
             Plan((("search_crm", {"query": "Meera Joshi", "entity": "leads"}),),
                  "Meera Joshi is already a lead at Vantage Systems, so I added nothing new.")),
    ), set="crm", surface="crm", page=page_for("crm"), tags=(_H, "duplicate_write"))

_ORBIT_NOTE = Scenario(
    "ho_orbit_note", "Find a contact, then log one note (one card)", (
        Turn("Who is our contact at Orbit Labs?",
             (Check("ran_tool", ("search_crm",), "searched"),
              Check("answer_all", (r"Priya Menon", r"Head of Plant"), "names_the_person"),
              Check("no_unasked_write", 0)),
             Plan((("search_crm", {"query": "Orbit", "entity": "contacts"}),),
                  "Priya Menon, Head of Plant at Orbit Labs.")),
        Turn("Note on the Orbit deal: she approved the pilot",
             (Check("work_write", ("POST", r"^/crm/deals/[^/]+/activities$", "approved"),
                    "note_logged"),
              Check("no_unasked_write", 1),
              Check("answer_all", (r"Orbit",), "says_where_it_went")),
             Plan((("search_crm", {"query": "Orbit", "entity": "deals"}),
                   ("log_activity", {"entity": "deals", "record_id": cid(1),
                                     "activity_type": "note", "subject": "Pilot approved",
                                     "body": "Priya approved the pilot."})),
                  "Logged a note on the Orbit Labs sensor retrofit.")),
    ), set="crm", surface="crm", page=page_for("crm"), tags=(_H, "one_write"))

_T = TASK_IDS
_IT = Scenario(
    "ho_task_then_it", "A task number, then 'it': the item in focus (one card)", (
        Turn("What is task 22?",
             (Check("ran_tool", ("find_tasks", "list_tasks"), "looked_it_up"),
              Check("answer_all", (r"Vendor call prep", r"Mobile App"), "names_the_task"),
              Check("no_unasked_write", 0)),
             Plan((("find_tasks", {"query": "22"}),),
                  "#22 is Vendor call prep in Mobile App. It is yours, due 31 October.")),
        Turn("Mark it done",
             (Check("work_write", ("POST", rf"^/projects/tasks/{_T[22]}/complete$"),
                    "the_right_task_done"),
              Check("no_unasked_write", 1)),
             Plan((("complete", {"task_id": _T[22]}),), "Done: #22 Vendor call prep.")),
    ), set="projects", surface="tasks", page=page_for("tasks"), tags=(_H, "pronoun_item"))

_MOBILE = Scenario(
    "ho_mobile_tasks", "Read a project, then make two tasks in it (one card)", (
        Turn("What is open in the Mobile App?",
             (Check("ran_tool", ("list_tasks", "project_summary"), "read_the_project"),
              Check("answer_all", (r"onboarding", r"login crash", r"Vendor call"),
                    "names_the_open_tasks"),
              Check("no_unasked_write", 0)),
             Plan((("list_tasks", {"project_id": NODE_MOBILE}),),
                  "Three tasks are open: #21 Ship the onboarding screens, #22 Vendor call prep "
                  "and #23 Fix the login crash.")),
        Turn("Add two tasks there: crash report and push alerts",
             (Check("work_write", ("POST", r"^/projects/tasks$", "push alerts"), "tasks_made"),
              Check("work_write", ("POST", r"^/projects/tasks$", "crash report"), "both_made"),
              Check("no_unasked_write", 1), Check("right_pack", "projects-bulk")),
             Plan((("create_tasks", {"project_id": NODE_MOBILE, "tasks": json.dumps(
                 [{"title": "Crash report"}, {"title": "Push alerts"}])}),),
                  "Made 2 tasks in Mobile App: Crash report and Push alerts.")),
    ), set="projects", surface="projects", page=page_for("projects"),
    tags=(_H, "one_card_many"))

_KUMAR = Scenario(
    "ho_mail_to_task", "Read a thread, then make a task from it (one card)", (
        Turn("What did Kumar say the rework costs?",
             (Check("called", ("query_inbox", "read_thread", "read_email"), "read_the_thread"),
              Check("figure", 36_000, "gives_the_cost"),
              Check("answer_all", (r"3(?:rd)? Nov|Nov\w* 3\b",), "gives_the_ship_date"),
              Check("no_write")),
             Plan((("query_inbox", {"query": "PCB rework"}),
                   ("read_thread", {"email_id": PCB_3.id})),
                  "The rework costs Rs 36,000 for all 120 boards, and Kumar ships on "
                  "3 November.")),
        Turn("Make a task to chase him on the ship date",
             (Check("work_write", ("POST", r"^/projects/my/tasks$", "Kumar"),
                    "task_names_the_person"),
              Check("right_pack", "projects"), Check("no_unasked_write", 1),
              Check("no_write")),
             Plan((("create_personal_task", {"title": "Chase Kumar on the ship date"}),),
                  "Captured the private task: Chase Kumar on the ship date.")),
    ), set="mixed", surface="email", mails=OFFICE, page=page_for("email"),
    tags=(_H, "mail_to_task"))

_DOMAIN = Scenario(
    "ho_task_then_overdue", "A private task, then a read that must not borrow others' work", (
        Turn("Add a task: renew the domain",
             (Check("work_write", ("POST", r"^/projects/my/tasks$", "renew the domain"),
                    "task_captured"),
              Check("right_pack", "projects"), Check("no_unasked_write", 1)),
             Plan((("create_personal_task", {"title": "Renew the domain"}),),
                  "Captured the private task: Renew the domain.")),
        Turn("Is anything of mine overdue?",
             (Check("ran_tool", ("my_work", "list_tasks", "calendar"), "read_my_work"),
              Check("answer_none", (r"onboarding", r"login crash"), "no_task_of_someone_else"),
              Check("no_unasked_write", 0)),
             Plan((("my_work", {}),),
                  "No. Your 3 open tasks are all due by 31 October, and none is overdue.")),
    ), set="projects", surface="tasks", page=page_for("tasks"), tags=(_H, "other_peoples_work"))

HOLDOUT_SCENARIOS: tuple[Scenario, ...] = (
    _PAID_LATER, _REPLY_CC, _OWN_WORDS, _BIG_DEALS, _LEAD_EXISTS, _ORBIT_NOTE, _IT, _MOBILE,
    _KUMAR, _DOMAIN,
)

__all__ = ["HOLDOUT_SCENARIOS"]
