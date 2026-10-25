"""The scenarios of the measure: 13 email chats, 18 member turns.

Every name, address, order and number here is synthetic. The Navy scenarios
copy only the SHAPE of the chat of 2026-10-25 (``specs/one_assistant_context.md``
§1): an order whose body lists the parts, and two scanned files whose names
look like part names. No text of that chat is here.

Each turn has:

* ``words``: what the member types.
* ``checks``: the machine checks of the turn (:mod:`.checks` runs them).
* ``plan``: a known-good path, the email tool calls and the answer. The
  scripted run plays it on both arms, and every check must pass. A scripted
  run proves the path and the checks, never the model's choice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.one_assistant_measure.mailbox import (
    DRAFT,
    HOME,
    KIRAN,
    MAIL,
    SAGAR,
    WORK,
    Attachment,
    Mail,
    uid,
)

# ── the mail ─────────────────────────────────────────────────────────────────

SAGAR_MAIL = Mail(
    MAIL, "Dr Sagar", SAGAR, "Quote for the clinic",
    "Dear VJ,\n\nPlease send your best quote for the clinic fit-out by Thursday.\n\nDr Sagar",
    days_ago=1, needs_reply=True,
)

NAVY = Mail(
    uid(1), "Stores Officer, Ordnance Depot", "stores@depot.example",
    "Purchase order OD/2026/114: hydraulic spares",
    "Dear Sir,\n\nPlease supply the following against our order OD/2026/114:\n"
    "1. P-12 hydraulic seal kit, quantity 40\n"
    "2. P-31 bearing housing, quantity 12\n"
    "Deliver to the depot stores by 30 November 2026.\n\n"
    "The two attached files are sample drawings, for reference only.\n\n"
    "Regards,\nStores Officer",
    days_ago=2, is_read=True,
    attachments=(Attachment(uid(101), "CB6_sample.pdf"), Attachment(uid(102), "CB7_sample.pdf")),
)
INVOICE_DF = Mail(
    uid(2), "Delta Freight Accounts", "accounts@deltafreight.example",
    "Invoice DF-2291 for October shipments",
    "Hello,\n\nPlease find our invoice DF-2291 for the October shipments. The amount is "
    "Rs 48,500. Payment is due by 5 November 2026.\n\nThanks,\nDelta Freight",
    days_ago=3,
)
INVOICE_KP = Mail(
    uid(3), "Kaveri Packaging", "billing@kaveripack.example",
    "Invoice KP-77",
    "Hi,\n\nOur invoice KP-77 for 600 cartons is Rs 12,300, due by 10 November 2026.\n\nKaveri Packaging",
    days_ago=6, is_read=True,
)
INVOICE_SE = Mail(
    uid(4), "Sunrise Electricals", "office@sunrise-elec.example",
    "Invoice SE-1450: wiring work",
    "Dear customer,\n\nInvoice SE-1450 for the wiring work is Rs 7,800, due by 1 November 2026.\n\n"
    "Sunrise Electricals",
    days_ago=9, is_read=True,
)
PRIYA = Mail(
    uid(5), "Priya Nair", "priya@boardworks.example", "Board order: revised quantities",
    "Hi VJ,\n\nWe need 250 boards instead of 200 for the November batch. "
    "Can you confirm by Friday?\n\nPriya",
    days_ago=1.5, needs_reply=True,
)
ANIL = Mail(
    uid(6), "Anil Rao", "anil@railfreight.example", "Delivery date for the extruder parts",
    "Hi VJ,\n\nWe plan to deliver the extruder parts on 12 November. Please confirm the date."
    "\n\nAnil",
    days_ago=2.5, needs_reply=True,
)
MEERA = Mail(
    uid(7), "Meera Iyer", "meera@clinicsupply.example", "Quote request: mounting brackets",
    "Hello,\n\nPlease quote for 40 units of the X-200 mounting bracket, delivered to Pune. "
    "The attached spec sheet of the X-100 is for reference only, to show the finish we want."
    "\n\nMeera",
    days_ago=0.5, needs_reply=True,
    attachments=(Attachment(
        uid(103), "X-100_spec_sheet.pdf",
        "X-100 mounting bracket. Material: 2 mm stainless steel. Finish: brushed. "
        "Hole pattern: 4 x M6. Weight: 180 g."),),
)
RAVI = Mail(
    uid(8), "Ravi Kumar", "ravi@designhouse.example", "Drawings for review",
    "Hi VJ,\n\nThe gearbox drawings are attached. Please review them and send your "
    "comments by Monday.\n\nRavi",
    days_ago=0.8,
    attachments=(Attachment(uid(104), "Gearbox_GB-40_rev3.pdf"),),
)
NEWS_1 = Mail(uid(9), "MakerMag", "news@makermag.example", "Weekly digest: 10 tools for makers",
              "This week: ten tools for makers, a reader survey and our events.", days_ago=1.2)
NEWS_2 = Mail(uid(10), "SensorShop", "offers@sensorshop.example", "Offer: 20% off sensors",
              "Save 20% on every sensor this week only.", days_ago=2.2)
PCB_1 = Mail(
    uid(11), "Kumar S", "kumar@pcbfab.example", "PCB rework for the control boards",
    "Hi VJ,\n\nThe first batch of 120 control boards failed the burn-in test. "
    "We propose a rework of the power stage.\n\nKumar",
    days_ago=6, is_read=True, thread_id="t-pcb",
)
PCB_2 = Mail(
    uid(12), "VJ", "vj@hathilabs.test", "Re: PCB rework for the control boards",
    "Agreed. What is the cost, and when do you ship?\n\nVJ",
    days_ago=5, is_read=True, folder="sent", thread_id="t-pcb", to=("kumar@pcbfab.example",),
)
PCB_3 = Mail(
    uid(13), "Kumar S", "kumar@pcbfab.example", "Re: PCB rework for the control boards",
    "Hi VJ,\n\nThe rework costs Rs 36,000 for all 120 boards. We ship the reworked "
    "boards on 3 November.\n\nKumar",
    days_ago=4, is_read=True, thread_id="t-pcb",
)

#: The mailbox of every scenario except the five turns.
OFFICE: tuple[Mail, ...] = (NAVY, INVOICE_DF, INVOICE_KP, INVOICE_SE, PRIYA, ANIL, MEERA,
                            RAVI, NEWS_1, NEWS_2, PCB_1, PCB_2, PCB_3)

# ── the shapes ───────────────────────────────────────────────────────────────

PROJECTS = "## Where the member is now\nThe member is in the Projects app (/projects)."
EMAIL_PAGE = "## Where the member is now\nThe member is in the Email app (/email)."


@dataclass(frozen=True)
class Check:
    """One machine check: its kind (:data:`checks.KINDS`) and its argument."""

    kind: str
    arg: Any = None
    #: The name in the report. The kind, when empty.
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or self.kind


@dataclass(frozen=True)
class Plan:
    """A known-good path of one turn: the email tool calls, then the answer."""

    calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    answer: str = ""


@dataclass(frozen=True)
class Turn:
    words: str
    checks: tuple[Check, ...]
    plan: Plan


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    turns: tuple[Turn, ...]
    mails: tuple[Mail, ...] = OFFICE
    page: str = EMAIL_PAGE
    #: Earlier turns of the chat, as the browser sends them, before turn 1.
    history: tuple[dict[str, str], ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)


def _link(m: Mail) -> str:
    return f"[{m.subject}](/email?email={m.id})"


# ── the Navy shape ───────────────────────────────────────────────────────────

NAVY_ASK = "What parts does the purchase order from the ordnance depot ask for?"
NAVY_GUESS = "The order names the parts CB6 and CB7."
NAVY_CORRECTION = ("The attachments are only references. Read the parts from the body "
                   "of the email, not from the file names.")
NAVY_NAMES = ("CB6", "CB7")
NAVY_ANSWER = ("The order asks for two parts: P-12 hydraulic seal kit (40) and P-31 bearing "
               f"housing (12), by 30 November. CB6 and CB7 are only the file names of the "
               f"sample drawings. {_link(NAVY)}")
_NAVY_CHECKS = (
    Check("answer_all", (r"P-12", r"P-31"), "parts_from_body"),
    Check("no_name_claim", NAVY_NAMES, "no_file_name_as_part"),
)

# ── the scenarios ────────────────────────────────────────────────────────────

FIVE_TURNS = Scenario(
    "five_turns", "The five turns of test_one_assistant_email", mails=(SAGAR_MAIL,), page=PROJECTS,
    tags=("trajectory",),
    turns=(
        Turn("what needs a reply",
             (Check("called", ("query_inbox", "find_priority"), "read_the_inbox"),
              Check("all_inboxes"), Check("answer_all", (r"Sagar",), "names_the_sender")),
             Plan((("query_inbox", {"account_id": "", "unread_only": True}),),
                  "Dr Sagar needs a reply about the quote for the clinic.")),
        Turn("reply to Dr Sagar and cc Kiran",
             (Check("reply_card", MAIL), Check("cc", KIRAN, "kiran_in_cc")),
             Plan((("compose_reply", {"email_id": MAIL, "account_id": WORK}),
                   ("edit_draft", {"account_id": WORK, "draft_id": DRAFT, "cc": [KIRAN]})),
                  "Saved in Drafts with Kiran in Cc.")),
        Turn("send it",
             (Check("sent_draft", DRAFT),),
             Plan((("send_draft", {"account_id": WORK, "draft_id": DRAFT}),), "Sent.")),
        # The test says only "set a rule on my personal box". A live model
        # needs to know WHICH rule, so the words name it.
        Turn("set a rule on my personal box: label mail with receipt in the subject as Receipts",
             (Check("rule_in", HOME),),
             Plan((("create_rule", {"account_id": "my personal box", "name": "Receipts",
                                    "subject_pattern": "receipt", "label": "Receipts"}),),
                  "The rule is in Personal.")),
        Turn("which mailbox needs reconnecting",
             (Check("answer_all", (r"Personal",), "names_the_mailbox"),),
             Plan((), "Personal needs reconnecting. Sign in again from the Email app's mailbox menu.")),
    ),
)

NAVY_FRESH = Scenario(
    "navy_fresh", "The Navy shape, first ask", tags=("navy",),
    turns=(Turn(NAVY_ASK, (*_NAVY_CHECKS, Check("read_mail", NAVY.id)),
                Plan((("query_inbox", {"query": "ordnance depot"}),
                      ("read_email", {"email_id": NAVY.id})), NAVY_ANSWER)),),
)

NAVY_REPLAY = Scenario(
    "navy_replay", "The Navy replay: a wrong guess, then the member's correction",
    tags=("navy",),
    history=({"role": "user", "content": NAVY_ASK}, {"role": "assistant", "content": NAVY_GUESS}),
    turns=(Turn(NAVY_CORRECTION, (*_NAVY_CHECKS, Check("task_no_guess", NAVY_NAMES)),
                Plan((("query_inbox", {"query": "ordnance depot"}),
                      ("read_email", {"email_id": NAVY.id})), NAVY_ANSWER)),),
)

READ_INVOICE = Scenario(
    "read_invoice", "Read one email",
    turns=(Turn("What does the Delta Freight invoice say?",
                (Check("answer_all", (r"48,500",), "the_amount"),
                 Check("answer_all", (r"5(?:th)? Nov|Nov\w* 5\b",), "the_due_date")),
                Plan((("query_inbox", {"query": "Delta Freight invoice"}),
                      ("read_email", {"email_id": INVOICE_DF.id})),
                     f"Delta Freight sent invoice DF-2291 for Rs 48,500, due by 5 November. "
                     f"{_link(INVOICE_DF)}")),),
)

FIND_PRIYA = Scenario(
    "find_priya", "Find a mail by its sender",
    turns=(Turn("Did Priya write to me about the board order? What does she want?",
                (Check("answer_all", (r"\b250\b",), "the_quantity"),
                 Check("answer_all", (r"Friday",), "the_deadline")),
                Plan((("query_inbox", {"from_email": "priya"}),
                      ("read_email", {"email_id": PRIYA.id})),
                     f"Yes. Priya wants 250 boards instead of 200 for the November batch, and "
                     f"asks you to confirm by Friday. {_link(PRIYA)}")),),
)

SUMMARISE_UNREAD = Scenario(
    "summarise_unread", "Summarise many emails",
    turns=(Turn("Summarise my unread mail.",
                (Check("called", ("query_inbox", "find_priority", "narrow_and_read"), "read_the_inbox"),
                 Check("answer_some", ((r"Delta Freight", r"Priya", r"Anil", r"Meera", r"Ravi"), 4),
                       "names_the_senders")),
                Plan((("query_inbox", {"unread_only": True}),),
                     "Seven unread. Priya wants 250 boards by Friday, Anil asks you to confirm "
                     "12 November, Meera asks for a quote for 40 X-200 brackets, Ravi sent gearbox "
                     "drawings for review by Monday, and Delta Freight sent invoice DF-2291 for "
                     "Rs 48,500. Two are newsletters.")),),
)

NEEDS_REPLY = Scenario(
    "needs_reply", "What needs a reply",
    turns=(Turn("What needs a reply from me?",
                (Check("answer_all", (r"Priya", r"Anil", r"Meera"), "names_each_one"),),
                Plan((("find_priority", {"kind": "needs_reply"}),),
                     "Three need a reply: Priya (board quantities), Anil (the delivery date) "
                     "and Meera (a quote for brackets).")),),
)

COUNT_INVOICES = Scenario(
    "count_invoices", "Count and name across emails",
    turns=(Turn("How many invoices came in during the last two weeks, and from whom?",
                (Check("answer_all", (r"\b(?:3|three)\b",), "the_count"),
                 Check("answer_all", (r"Delta Freight", r"Kaveri", r"Sunrise"), "names_each_one")),
                Plan((("query_inbox", {"query": "invoice", "days": 14}),),
                     "Three invoices: Delta Freight (Rs 48,500), Kaveri Packaging (Rs 12,300) "
                     "and Sunrise Electricals (Rs 7,800).")),),
)

THREAD_SUMMARY = Scenario(
    "thread_summary", "Summarise a thread",
    turns=(Turn("Summarise my thread with Kumar about the PCB rework.",
                (Check("answer_some", ((r"\b120\b", r"36,000", r"3(?:rd)? Nov|Nov\w* 3\b"), 2),
                       "the_facts"),),
                Plan((("query_inbox", {"query": "PCB rework"}),
                      ("read_thread", {"email_id": PCB_3.id})),
                     "120 control boards failed burn-in. Kumar proposed a rework of the power "
                     "stage, and you agreed. The rework costs Rs 36,000, and the boards ship on "
                     "3 November.")),),
)

REPLY_CORRECTION = Scenario(
    "reply_correction", "A reply after a correction in an earlier turn",
    turns=(
        Turn("Anil wrote that the extruder parts arrive on 12 November. That date is wrong: "
             "they arrive on 14 November.",
             (Check("no_write"),),
             Plan((), "Noted: the parts arrive on 14 November, not 12 November.")),
        Turn("Reply to Anil and confirm the date.",
             (Check("replied", ANIL.id), Check("reply_text", (r"\b14\b",), "the_corrected_date")),
             Plan((("compose_reply", {"email_id": ANIL.id, "account_id": WORK,
                                      "instruction": "Confirm delivery on 14 November."}),),
                  "Saved in Drafts of Work: the reply confirms 14 November.")),
    ),
)

REFERENCE_FILE = Scenario(
    "reference_file", "A file that is only a reference",
    turns=(Turn("What does Meera want me to quote?",
                (Check("answer_all", (r"X-200", r"\b40\b"), "the_item_and_quantity"),
                 Check("no_reference_subject", ("X-100",), "reference_is_not_the_item")),
                Plan((("query_inbox", {"from_email": "meera"}),
                      ("read_email", {"email_id": MEERA.id})),
                     "Meera wants a quote for 40 units of the X-200 mounting bracket, delivered "
                     "to Pune. The X-100 spec sheet is only a reference for the finish.")),),
)

SCANNED_FILE = Scenario(
    "scanned_file", "A scanned file: the name is all there is",
    turns=(Turn("What does the drawing that Ravi sent show?",
                (Check("admits_unreadable"), Check("no_content_claim", ("GB-40", "gearbox")),
                 Check("called", ("read_email_attachment",), "tried_the_file")),
                Plan((("query_inbox", {"from_email": "ravi"}),
                      ("read_email_attachment", {"email_id": RAVI.id,
                                                 "attachment": "Gearbox_GB-40_rev3.pdf"})),
                     "I could not read the drawing: it is a scanned image with no text. Its "
                     "file name, Gearbox_GB-40_rev3.pdf, is only a label. Ravi asks for your "
                     "comments by Monday.")),),
)

EXACT_TEXT = "Hi Priya, yes, 250 boards for the November batch is fine. Thanks, VJ"
EXACT_REPLY = Scenario(
    "exact_reply", "A reply with the member's exact text",
    turns=(Turn(f"Reply to Priya with exactly this text: {EXACT_TEXT}",
                (Check("reply_card", PRIYA.id), Check("verbatim", EXACT_TEXT, "text_word_for_word")),
                Plan((("compose_reply", {"email_id": PRIYA.id, "account_id": WORK,
                                         "draft": EXACT_TEXT}),),
                     "Saved in Drafts of Work, to Priya.")),),
)

SCENARIOS: tuple[Scenario, ...] = (
    FIVE_TURNS, NAVY_FRESH, NAVY_REPLAY, READ_INVOICE, FIND_PRIYA, SUMMARISE_UNREAD,
    NEEDS_REPLY, COUNT_INVOICES, THREAD_SUMMARY, REPLY_CORRECTION, REFERENCE_FILE,
    SCANNED_FILE, EXACT_REPLY,
)


def scenario(sid: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == sid:
            return s
    raise KeyError(sid)
