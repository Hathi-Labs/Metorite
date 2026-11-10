"""The synthetic mailbox and the questions of the email narrowing eval (WS-48 N2).

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2.

🔴 **No real mail.** Every name, address and text here is invented, and every
domain ends in ``.test``. ``fixtures/mailbox.json`` is the output of
:func:`generate`, and ``test_email_narrowing_eval.py`` fails when the two
differ. To change the mailbox, change this file and run::

    uv run python -m evals.email_narrowing.dataset --write

The mailbox holds 300 messages of the member, from 40 senders, over 60 days.
It also holds 12 messages of ANOTHER member of the same org (the stranger).
They match every question, so a leak across members shows in the eval.

**Dates.** The fixture is written for :data:`FIXTURE_TODAY`. :func:`load`
moves every date by the days from that date to today, so a question in the
member's words ("in the last 30 days") is right on any day.

**The verdicts of the stub door.** In the scripted run, the stub of the decide
door answers each PICK question from :meth:`Dataset.verdict`. An answering
message gets a sure ``yes``, and any other message a sure ``no``, except where
a message names its own verdict: a borderline message gets ``unsure`` (kept,
so it costs a read), and a hard answering message gets a low ``no`` (kept by
the keep rule). These verdicts are STUB numbers. Only a live run
(``--compare``) asks a real ``tier-decide`` model.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MAILBOX_PATH = HERE / "fixtures" / "mailbox.json"

#: The day that the fixture is written for. :func:`load` moves it to today.
FIXTURE_TODAY = date(2026, 10, 7)
#: The span of the mailbox, in days before :data:`FIXTURE_TODAY`.
SPAN_DAYS = 60

MEMBER = "member@narrow.test"
STRANGER = "stranger@narrow.test"
ORG = "org-email-narrowing-eval"

_NS = uuid.UUID("5f0c6f0e-48a2-4e3b-9a77-0d2b6c1e4802")


def ident(*parts: Any) -> str:
    """A stable UUID for a fixture key."""
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


ACCOUNTS: dict[str, dict[str, str]] = {
    "work": {"id": ident("account", "work"), "owner": MEMBER,
             "email": MEMBER, "label": "Work"},
    "sales": {"id": ident("account", "sales"), "owner": MEMBER,
              "email": "sales@narrow.test", "label": "Sales"},
    "stranger": {"id": ident("account", "stranger"), "owner": STRANGER,
                 "email": STRANGER, "label": "Stranger"},
}

# ── The 40 senders ───────────────────────────────────────────────────────────

#: (key, name, email, kind, company). ``acme`` appears only in Acme senders.
SENDERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("priya", "Priya Rao", "priya@acme.test", "customer", "Acme Corp"),
    ("acme_accounts", "Acme Accounts", "accounts@acme.test", "customer", "Acme Corp"),
    ("acme_news", "Acme News", "news@acme.test", "newsletter", "Acme Corp"),
    ("tomas", "Tomas Vega", "tomas@vegafoods.test", "customer", "Vega Foods"),
    ("lena", "Lena Fischer", "lena@fischer-tools.test", "customer", "Fischer Tools"),
    ("arjun", "Arjun Mehta", "arjun@mehta-retail.test", "customer", "Mehta Retail"),
    ("grace", "Grace Okafor", "grace@okafor-labs.test", "customer", "Okafor Labs"),
    ("wei", "Wei Chen", "wei@chen-logistics.test", "customer", "Chen Logistics"),
    ("sara", "Sara Lind", "sara@lind-design.test", "customer", "Lind Design"),
    ("omar", "Omar Haddad", "omar@haddad-motors.test", "customer", "Haddad Motors"),
    ("julia", "Julia Rossi", "julia@rossi-clinic.test", "customer", "Rossi Clinic"),
    ("kenji", "Kenji Sato", "kenji@sato-print.test", "customer", "Sato Print"),
    ("maya", "Maya Cohen", "maya@cohen-bakery.test", "customer", "Cohen Bakery"),
    ("pedro", "Pedro Alves", "pedro@alves-farm.test", "customer", "Alves Farm"),
    ("bolt", "Bolt Supply", "orders@boltsupply.test", "supplier", "Bolt Supply"),
    ("nordic", "Nordic Steel", "dispatch@nordicsteel.test", "supplier", "Nordic Steel"),
    ("quickship", "Quickship Freight", "ops@quickship.test", "supplier", "Quickship"),
    ("polymer", "Polymer Works", "team@polymerworks.test", "supplier", "Polymer Works"),
    ("circuit", "Circuit House", "team@circuithouse.test", "supplier", "Circuit House"),
    ("papermill", "Paper Mill Co", "hello@papermill.test", "supplier", "Paper Mill Co"),
    ("brightlight", "Brightlight Optics", "info@brightlight.test", "supplier", "Brightlight"),
    ("gearco", "Gear and Co", "accounts@gearco.test", "supplier", "Gear and Co"),
    ("marketweekly", "Market Weekly", "digest@marketweekly.test", "newsletter", "Market Weekly"),
    ("factorytoday", "Factory Today", "news@factorytoday.test", "newsletter", "Factory Today"),
    ("startupbrief", "Startup Brief", "brief@startupbrief.test", "newsletter", "Startup Brief"),
    ("techradar", "Tech Radar", "radar@techradar.test", "newsletter", "Tech Radar"),
    ("eventshub", "Events Hub", "events@eventshub.test", "newsletter", "Events Hub"),
    ("metalindex", "Metal Index", "index@metalindex.test", "newsletter", "Metal Index"),
    ("dana", "Dana Iyer", "dana@narrow.test", "colleague", "Narrow Labs"),
    ("ravi", "Ravi Kumar", "ravi@narrow.test", "colleague", "Narrow Labs"),
    ("elena", "Elena Popova", "elena@narrow.test", "colleague", "Narrow Labs"),
    ("samt", "Sam Taylor", "sam@narrow.test", "colleague", "Narrow Labs"),
    ("nina", "Nina Brooks", "nina@narrow.test", "colleague", "Narrow Labs"),
    ("hr", "HR Team", "hr@narrow.test", "colleague", "Narrow Labs"),
    ("finance", "Finance Team", "finance@narrow.test", "colleague", "Narrow Labs"),
    ("cloudbox", "CloudBox", "noreply@cloudbox.test", "notice", "CloudBox"),
    ("payflow", "PayFlow", "receipts@payflow.test", "notice", "PayFlow"),
    ("calbot", "Calendar Bot", "calendar@narrow.test", "notice", "Narrow Labs"),
    ("itdesk", "IT Desk", "itdesk@narrow.test", "notice", "Narrow Labs"),
    ("courier", "Courier Track", "track@couriertrack.test", "notice", "Courier Track"),
)
_SENDER = {s[0]: s for s in SENDERS}

# ── The neutral text ─────────────────────────────────────────────────────────
#
# ⚠️ The filler holds none of the search words of the questions: pricing,
# price, quote, rate, late, delay, postpone, behind, sign, signature,
# contract, agreement, NDA, complain, unhappy, service, support, customer.
# ``test_email_narrowing_eval.py`` checks it. A search word in the filler
# would make a noise message a match, and move the numbers.

FILLER: tuple[str, ...] = (
    "Thanks for the update earlier this week. The team read it, and we will come "
    "back with notes after the review on Thursday.",
    "On our side the plan for next month has not changed. We still aim to finish "
    "the first phase before the holidays, and the second phase starts in January.",
    "I have copied Dana, who looks after the project on our side, so that she can "
    "follow the thread from here.",
    "Please let me know if a call next Tuesday or Wednesday works for you. The "
    "afternoon is easier for our team, because of the time zone.",
    "For the record, the meeting notes from last time are in the shared folder. The "
    "main points were the budget for the trial, the training plan and the next "
    "steps for the pilot.",
    "We moved to the new office last month, so please use the new address for "
    "anything you post to us. The phone numbers stay the same.",
    "Our finance team asked me to remind everyone that the year end closes on the "
    "20th of December. Anything after that date goes into the next year.",
    "The pilot at the second plant went well. The operators liked the new layout, "
    "and the training took less time than we planned.",
    "I am out of the office from the 14th to the 18th, and Ravi can help with "
    "anything urgent while I am away.",
    "We also reviewed the safety checklist with the site team, and every item on it "
    "is now green.",
    "If anything in this note is unclear, reply here and I will explain it on our "
    "next call.",
    "Best wishes to the whole team, and thanks again for the good work on the launch.",
    "The photos from the plant visit are in the album that I shared on Monday. Feel "
    "free to use them in the report.",
    "Our warehouse team counted the stock again on Friday, and the numbers now match "
    "the system.",
)

DISCLAIMER = (
    "This message and any files with it are confidential. They are for the named "
    "recipient only. If this message came to you by mistake, please tell the "
    "sender and delete it."
)

NEWS_ITEMS: tuple[str, ...] = (
    "Three new plants opened in the north this quarter, and two more are planned "
    "for next spring.",
    "A survey of 400 factory managers shows that most of them plan to train more "
    "operators next year.",
    "Robots for small workshops are now cheaper to lease than to buy, a new study says.",
    "The trade fair in Hanover had record visitors, with a strong focus on energy use.",
    "Water use per unit of output fell by a tenth across the sector in the last two years.",
    "Five tips to plan a shift change with no loss of output.",
    "Interview: how a family firm moved its whole line to electric drives in one summer.",
    "Readers ask: what is the best way to store spare parts for a long time?",
)

NOISE_TOPICS: dict[str, tuple[tuple[str, str], ...]] = {
    "customer": (
        ("Project update for {company}", "Here is the short update on the project. "
         "The first units run well, and the operators are happy with the training."),
        ("Visit next week", "We would like to visit your plant next week with two of "
         "our engineers. Would Wednesday morning work?"),
        ("Training dates", "Can we fix the dates for the operator training? Our team "
         "is free in the second half of the month."),
        ("Thanks for the visit", "Thanks for the visit yesterday. The tour of the line "
         "was very useful for our team."),
        ("New contact person at {company}", "From next month, Jo will be your contact "
         "person here. I move to a new role in our group."),
        ("Holiday closing at {company}", "Our plant is closed from the 24th to the 2nd. "
         "Mail will be read after the break."),
        ("Shipment received", "The pallets arrived this morning in good condition. "
         "Thanks to your team."),
        ("Question on the manual", "In the manual, page 14 shows two settings for the "
         "feeder. Which one do you advise for thin sheets?"),
    ),
    "supplier": (
        ("Dispatch note {n}", "Your goods left our warehouse today. The truck should "
         "reach you in two days."),
        ("Holiday closing at {company}", "Our office is closed on the 25th and the 26th. "
         "The warehouse stays open."),
        ("New account manager at {company}", "Please meet Alex, your new account "
         "manager. Alex will call you next week."),
        ("Stock report {n}", "Here is the stock report for this week. All items on "
         "your list are in stock."),
    ),
    "newsletter": (
        ("This week in manufacturing", ""),
        ("Five trends for next year", ""),
        ("Your weekly digest", ""),
        ("News from the sector", ""),
    ),
    "colleague": (
        ("Team lunch on Friday", "We book a table for the team lunch on Friday at one. "
         "Reply if you can come."),
        ("Slides for Monday", "I put the slides for the Monday review in the shared "
         "folder. Please add your page before Sunday."),
        ("Office move", "The movers come on Saturday. Please pack your desk on Friday "
         "afternoon."),
        ("Leave plan", "Please add your leave for December to the team calendar this "
         "week."),
        ("Notes from the review", "Here are my notes from the review. The main open "
         "point is the training plan for the new site."),
    ),
    "notice": (
        ("Your files were backed up", "Your weekly backup finished. 1,204 files were "
         "saved."),
        ("Receipt {n}", "We received your payment of 1,240.00 EUR. Thank you."),
        ("Invitation: weekly sync", "You are invited to the weekly sync on Tuesday at "
         "ten."),
        ("Password expiry reminder", "Your password expires in seven days. Change it "
         "in the account page."),
        ("Parcel {n} delivered", "Your parcel was delivered today at the front desk."),
    ),
}

# ── The planted messages: the answers and the distractors ───────────────────


@dataclass
class Plant:
    """One planted message. ``answers`` names the questions that it answers.
    ``verdicts`` names the stub door's answer for a question, where it is not
    the default (§7.2, the stub)."""

    key: str
    sender: str
    days_ago: int
    subject: str
    topic: str
    answers: tuple[str, ...] = ()
    verdicts: dict[str, tuple[str, float]] = field(default_factory=dict)
    attach: bool = False
    account: str = "work"


def _plants() -> list[Plant]:  # noqa: C901 — one flat list of fixtures
    p: list[Plant] = []
    add = p.append
    # Q1: customers who asked about pricing in the last 30 days. 11 answers,
    # and 4 of them say "quote" or "rates", not "pricing" (§7.2).
    add(Plant("q1_tomas", "tomas", 3, "Pricing for 200 units",
              "Could you send me your pricing for 200 units of the small mixer? We plan "
              "to order in November.", ("Q1",), account="sales"))
    add(Plant("q1_lena", "lena", 6, "Price list for next year",
              "We prepare our budget for next year. Can you send your price list for "
              "the cutting heads?", ("Q1",)))
    add(Plant("q1_arjun", "arjun", 9, "What is the price of the large unit",
              "Our board asked me what the price of the large unit is, with the stand "
              "and the cover.", ("Q1",), account="sales"))
    add(Plant("q1_grace", "grace", 12, "Pricing question",
              "Is your pricing for the lab kit the same as last year, or did it change?",
              ("Q1",), verdicts={"Q1": ("unsure", 0.55)}))
    add(Plant("q1_wei", "wei", 15, "Volume pricing",
              "If we take 1,000 units over the year, do you offer volume pricing?",
              ("Q1",), account="sales"))
    add(Plant("q1_sara", "sara", 19, "Prices for the design range",
              "Please send the prices for the design range, in euros.", ("Q1",)))
    add(Plant("q1_priya", "priya", 22, "Q4 order: pricing for 400 units",
              "For the Q4 order of 400 units, can you confirm the pricing per unit "
              "before we send the purchase order?", ("Q1", "Q2")))
    add(Plant("q1_omar", "omar", 5, "Request for a quote",
              "Please send us a quote for 50 gear boxes, delivered to our Lyon site.",
              ("Q1",), account="sales"))
    add(Plant("q1_julia", "julia", 11, "Can you quote for the clinic",
              "We open a second clinic. Could you quote for six of the small units?",
              ("Q1",), verdicts={"Q1": ("no", 0.6)}))
    add(Plant("q1_kenji", "kenji", 17, "Your rates for next year",
              "Before we renew, what are your rates for next year for the print heads?",
              ("Q1",), account="sales"))
    add(Plant("q1_maya", "maya", 25, "Day rates for the install",
              "What are your day rates for the install team, for two days on site?",
              ("Q1",)))
    # Q1 distractors: a price, but nobody asks for our pricing.
    for i, (days, line) in enumerate([
        (2, "Steel prices rose by four percent this month."),
        (8, "The price of copper fell again on weak demand."),
        (13, "Why energy prices matter for your plant budget."),
        (18, "Freight rates from Asia are stable for the third month."),
        (23, "Paper prices: what to expect in the new year."),
        (27, "Interest rates and what they mean for your next machine."),
    ]):
        sender = ("metalindex", "marketweekly", "factorytoday", "startupbrief",
                  "techradar", "marketweekly")[i]
        add(Plant(f"q1_news_{i}", sender, days, f"Market note {i + 1}", line))
    for i, (sender, days) in enumerate([("bolt", 4), ("polymer", 14), ("papermill", 21)]):
        add(Plant(f"q1_supplier_{i}", sender, days, f"Our new price list {i + 1}",
                  "Attached is our new price list. The new prices apply from the first "
                  "of next month.", verdicts={"Q1": ("no", 0.8)}, attach=True))
    add(Plant("q1_internal_0", "elena", 7, "Pricing page review",
              "Can you review the new pricing page before Friday? Marketing wants to "
              "publish it next week.", verdicts={"Q1": ("unsure", 0.5)}))
    add(Plant("q1_internal_1", "samt", 16, "Pricing for the trade fair stand",
              "The trade fair sent their pricing for the stand. We need to choose a "
              "size this week.", verdicts={"Q1": ("unsure", 0.5)}))
    add(Plant("q1_asquoted_0", "pedro", 10, "Delivery update",
              "As quoted in your last mail, the goods came on Monday. All good.",
              verdicts={"Q1": ("no", 0.75)}))
    add(Plant("q1_asquoted_1", "wei", 20, "Re: the quote from last year",
              "Thanks, the quote from last year is in our files. No action needed now.",
              verdicts={"Q1": ("no", 0.75)}))
    # Pricing asks from BEFORE the 30 days: the date filter leaves them out.
    for i, (sender, days) in enumerate(
            [("tomas", 38), ("lena", 44), ("arjun", 50), ("kenji", 56)]):
        add(Plant(f"q1_old_{i}", sender, days, f"Pricing question from the spring {i + 1}",
                  "Could you send your pricing for the standard unit?"))

    # Q2: everything from Acme about the Q4 order in the last 7 weeks. 8
    # answers (q1_priya is one of them).
    for i, (days, subject, line) in enumerate([
        (45, "Q4 order: first draft", "Here is the first draft of our Q4 order: 400 "
         "units of the mixer, in two lots."),
        (40, "Q4 order: lot sizes", "For the Q4 order, can the first lot be 250 units "
         "and the second 150?"),
        (33, "Q4 order: delivery address", "Please ship the Q4 order to our new "
         "warehouse in Pune."),
        (28, "Q4 order: purchase order", "Attached is the purchase order for the Q4 "
         "order. Please confirm."),
        (14, "Q4 order: change of colour", "For the Q4 order, we now want the grey "
         "finish, not the blue one."),
        (8, "Q4 order: dates", "When can you ship the first lot of the Q4 order?"),
        (2, "Q4 order: thanks", "Thanks for the update on the Q4 order. All clear on "
         "our side."),
    ]):
        add(Plant(f"q2_order_{i}", "priya", days, subject, line, ("Q2",),
                  attach=(i == 3)))
    for i, days in enumerate([42, 30, 18, 6]):
        add(Plant(f"q2_news_{i}", "acme_news", days, f"Acme News {i + 1}",
                  "Our news this month: a new plant manager, and the winners of the "
                  "team award."))
    for i, days in enumerate([36, 24, 10]):
        add(Plant(f"q2_invoice_{i}", "acme_accounts", days, f"Invoice for order 22{i}1",
                  f"Please find the invoice for order 22{i}1, from the spring.",
                  verdicts={"Q2": ("unsure", 0.5)}, attach=True))
    for i, days in enumerate([35, 12]):
        add(Plant(f"q2_event_{i}", "priya", days, f"Acme open day {i + 1}",
                  "You are invited to the Acme open day. There is lunch and a plant tour."))
    for i, days in enumerate([52, 55, 58]):
        add(Plant(f"q2_old_{i}", "priya", days, f"Q4 order: early idea {i + 1}",
                  "We start to think about a Q4 order. More news soon."))

    # Q3: suppliers who said that a delivery will be late. 6 answers.
    for i, (sender, days, subject, line, verdict) in enumerate([
        ("nordic", 4, "Your delivery will be late", "The steel for your order is late "
         "at the mill. It ships one week after the planned date.", None),
        ("quickship", 9, "Delay at the port", "There is a delay at the port. Your "
         "containers arrive four days after the plan.", None),
        ("circuit", 15, "Boards delayed", "The boards for your order are delayed. A "
         "part from our own supplier did not arrive.", ("no", 0.65)),
        ("brightlight", 22, "We must postpone the shipment", "We must postpone the "
         "shipment of the lenses to the end of the month.", None),
        ("gearco", 31, "Running behind on your gears", "Our line is behind by two "
         "weeks. Your gears ship on the 20th, not the 6th.", None),
        ("bolt", 47, "Late delivery of the bolts", "The bolts are late. The truck had "
         "a fault, and it leaves tomorrow.", None),
    ]):
        verdicts = {"Q3": verdict} if verdict else {}
        add(Plant(f"q3_late_{i}", sender, days, subject, line, ("Q3",), verdicts=verdicts))
    for i, (sender, days) in enumerate([("payflow", 11), ("gearco", 26), ("papermill", 40)]):
        add(Plant(f"q3_fee_{i}", sender, days, f"Reminder: late fee {i + 1}",
                  "A late fee applies to invoices that are paid after 30 days."))
    for i, (sender, days) in enumerate([("eventshub", 7), ("eventshub", 29)]):
        add(Plant(f"q3_webinar_{i}", sender, days, f"Do not be late: webinar {i + 1}",
                  "Do not be late: the webinar on lean plants starts at three sharp."))
    add(Plant("q3_staylate", "ravi", 13, "Working late on Thursday",
              "I stay late on Thursday to finish the slides. The office keys are with me."))
    add(Plant("q3_ourlate_0", "omar", 6, "Our delivery from you is late",
              "Our delivery from you is late again, and the service from your team is "
              "poor. Please call me.", ("Q5",), verdicts={"Q3": ("unsure", 0.5)}))
    add(Plant("q3_ourlate_1", "pedro", 24, "Where is our delivery",
              "Our delivery is late by a week. When does it come?",
              verdicts={"Q3": ("unsure", 0.5)}))

    # Q4: mail with an attachment that asks the member to sign. 5 answers.
    for i, (sender, days, subject, line) in enumerate([
        ("lena", 5, "Please sign the attached agreement", "Please sign the attached "
         "supply agreement and send it back by Friday."),
        ("wei", 12, "Contract for the new lane", "Attached is the contract for the new "
         "lane. Can you sign it this week?"),
        ("polymer", 20, "NDA before the sample", "Before we send the sample, please "
         "sign the attached NDA."),
        ("julia", 34, "Service agreement to sign", "Here is the service agreement for "
         "the clinic. Please sign page 4."),
        ("hr", 41, "Sign your new contract", "Your new contract is attached. Please "
         "sign it in the HR portal."),
    ]):
        add(Plant(f"q4_sign_{i}", sender, days, subject, line, ("Q4",), attach=True))
    for i, (sender, days) in enumerate([("marketweekly", 9), ("factorytoday", 27)]):
        add(Plant(f"q4_news_{i}", sender, days, f"Guide to trade agreements {i + 1}",
                  "Our free guide explains the new trade agreement between the two "
                  "regions.", attach=True))
    add(Plant("q4_signed_copy", "gearco", 17, "Your signed contract copy",
              "Here is your copy of the signed contract, for your records. Nothing "
              "to do.", verdicts={"Q4": ("unsure", 0.5)}, attach=True))
    for i, (sender, days) in enumerate([("arjun", 8), ("sara", 30)]):
        add(Plant(f"q4_noattach_{i}", sender, days, f"Contract by post {i + 1}",
                  "We post the contract to you this week. Please sign it when it "
                  "comes."))

    # Q5: customers who complained about our service or support. 5 answers.
    # Two of them share NO word with the search: the lexical limit (Q3 of the
    # spec). They are the expected misses of the eval.
    add(Plant("q5_complaint", "maya", 4, "Formal complaint",
              "This is a formal complaint. The oven stopped twice and the engineer "
              "came three days after our call.", ("Q5",)))
    add(Plant("q5_unhappy", "grace", 18, "Unhappy with your support",
              "We are unhappy with your support. Two tickets have no answer after a "
              "week.", ("Q5",)))
    add(Plant("q5_nowords_0", "kenji", 10, "Again the machine stopped",
              "The machine you sent stopped twice this week, and nobody called me back. "
              "This is not acceptable.", ("Q5",)))
    add(Plant("q5_nowords_1", "tomas", 21, "Third time I write",
              "Third time I write. The spare part never arrived, and I expect an answer "
              "today.", ("Q5",)))
    for i, (sender, days) in enumerate(
            [("cloudbox", 3), ("cloudbox", 17), ("cloudbox", 31), ("courier", 45)]):
        add(Plant(f"q5_notice_{i}", sender, days, f"Your service plan renews {i + 1}",
                  "Your service plan renews next month. No action is needed."))
    for i, days in enumerate([9, 37]):
        add(Plant(f"q5_itdesk_{i}", "itdesk", days, f"IT support hours {i + 1}",
                  "IT support is closed on Friday afternoon for the update."))
    add(Plant("q5_news", "techradar", 14, "Five trends in after-sales service",
              "Five trends in after-sales service for machine makers."))
    return p


#: The question ids, in order. Q5 is the expected miss (spec Q3).
QUESTION_IDS = ("Q1", "Q2", "Q3", "Q4", "Q5")


@dataclass(frozen=True)
class QuestionSpec:
    """A question of the eval. The filters and the before-path search are the
    words a model writes. ``window_days`` gives ``after`` for both paths."""

    id: str
    prompt: str
    words: str | None
    filters: dict[str, Any]
    inbox: dict[str, Any]
    window_days: int | None
    gated: bool = True
    note: str = ""


QUESTIONS: tuple[QuestionSpec, ...] = (
    QuestionSpec(
        "Q1", "Which customers asked about pricing in the last 30 days?",
        words="pricing OR price OR prices OR quote OR rates",
        filters={}, inbox={}, window_days=30,
    ),
    QuestionSpec(
        "Q2", "Summarise everything from Acme about the Q4 order in the last 7 weeks.",
        words="", filters={"from": "acme"}, inbox={"from_email": "acme"}, window_days=49,
    ),
    QuestionSpec(
        "Q3", "Which suppliers told us that a delivery will be late?",
        words="late OR delay OR delayed OR postpone OR behind",
        filters={}, inbox={}, window_days=None,
    ),
    QuestionSpec(
        "Q4", "Which emails with an attachment ask me to sign a contract or an agreement?",
        words="sign OR contract OR agreement OR NDA",
        filters={"has_attachments": True}, inbox={"has_attachments": True}, window_days=None,
    ),
    QuestionSpec(
        "Q5", "Which customers complained about our service or support?",
        words="complaint OR complained OR unhappy OR service OR support",
        filters={}, inbox={}, window_days=None, gated=False,
        note="Expected miss (spec Q3): two answers share no word with any search.",
    ),
)


# ── The generator ────────────────────────────────────────────────────────────


def _sign_off(sender: tuple[str, str, str, str, str], rng: random.Random) -> str:
    _key, name, _email, kind, company = sender
    if kind in {"newsletter", "notice"}:
        return f"{company}\nYou get this mail because of your account with {company}."
    phone = f"+00 {rng.randint(100, 999)} {rng.randint(1000, 9999)}"
    return f"Kind regards,\n{name}\n{company}\n{phone}"


def _body(sender: tuple[str, str, str, str, str], topic: str, rng: random.Random) -> str:
    kind = sender[3]
    parts: list[str] = []
    if kind == "newsletter":
        parts.append(f"Hello reader,\n\n{topic}".rstrip())
        items = rng.sample(NEWS_ITEMS, k=rng.randint(4, 7))
        parts.extend(f"- {item}" for item in items)
    elif kind == "notice":
        parts.append(topic)
    else:
        parts.append(f"Hello,\n\n{topic}")
        parts.extend(rng.sample(FILLER, k=rng.randint(2, 6)))
    parts.append(_sign_off(sender, rng))
    if kind in {"customer", "supplier", "colleague"} and rng.random() < 0.55:
        quoted = rng.sample(FILLER, k=rng.randint(2, 5))
        parts.append("-----Original message-----\nFrom: member@narrow.test\n\n"
                     + "\n".join(f"> {q}" for q in quoted))
    if kind in {"customer", "supplier"} and rng.random() < 0.6:
        parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def _message(
    key: str, sender_key: str, days_ago: int, subject: str, topic: str,
    rng: random.Random, *, account: str = "work", attach: bool = False,
    answers: tuple[str, ...] = (), verdicts: dict[str, tuple[str, float]] | None = None,
    folder: str = "inbox",
) -> dict[str, Any]:
    sender = _SENDER[sender_key]
    moment = datetime.combine(
        FIXTURE_TODAY - timedelta(days=days_ago), datetime.min.time(), tzinfo=UTC,
    ) + timedelta(hours=rng.randint(7, 18), minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
    body = _body(sender, topic, rng)
    acct = ACCOUNTS[account]
    return {
        "slug": key,
        "id": ident("message", key),
        "account_id": acct["id"],
        "folder": folder,
        "from_address": {"name": sender[1], "email": sender[2]},
        "to_addresses": [{"name": "", "email": acct["email"]}],
        "subject": subject,
        "snippet": " ".join(body.split())[:160],
        "body_text": body,
        "has_attachments": bool(attach),
        "is_read": rng.random() < 0.7,
        "received_at": moment.isoformat(),
        "answers": list(answers),
        "verdicts": {q: [c, p] for q, (c, p) in (verdicts or {}).items()},
    }


def generate() -> dict[str, Any]:
    """The whole fixture, deterministic. :data:`MAILBOX_PATH` holds its JSON."""
    rng = random.Random(4802)
    messages: list[dict[str, Any]] = []
    for plant in _plants():
        messages.append(_message(
            plant.key, plant.sender, plant.days_ago, plant.subject, plant.topic, rng,
            account=plant.account, attach=plant.attach, answers=plant.answers,
            verdicts=plant.verdicts,
        ))
    # Noise: every sender gets mail, then the rest at random, to 300.
    order = [s[0] for s in SENDERS]
    n = 0
    while len(messages) < 300:
        sender_key = order[n] if n < len(order) else rng.choice(order)
        kind = _SENDER[sender_key][3]
        subject, topic = rng.choice(NOISE_TOPICS[kind])
        subject = subject.format(company=_SENDER[sender_key][4], n=1000 + n)
        topic = topic.format(company=_SENDER[sender_key][4], n=1000 + n)
        messages.append(_message(
            f"noise_{n}", sender_key, rng.randint(0, SPAN_DAYS - 1), subject, topic, rng,
            account=rng.choice(("work", "work", "work", "sales")),
            attach=kind == "notice" and "Receipt" in subject,
            folder=rng.choice(("inbox",) * 9 + ("archive",)),
        ))
        n += 1
    # The stranger: another member of the same org, with mail that matches
    # every question. It must never reach the member's run.
    stranger: list[dict[str, Any]] = []
    for i, (sender_key, subject, topic, attach) in enumerate([
        ("priya", "Q4 order: pricing for the stranger", "Please send pricing for the "
         "Q4 order, and a quote.", False),
        ("nordic", "Your delivery will be late, stranger", "The steel is late, with a "
         "delay of a week.", False),
        ("lena", "Please sign this agreement, stranger", "Please sign the contract and "
         "the agreement attached.", True),
        ("maya", "A complaint about your service, stranger", "A complaint: your support "
         "and service are poor.", False),
    ] * 3):
        stranger.append(_message(
            f"stranger_{i}", sender_key, 2 + i, f"{subject} {i}", topic, rng,
            account="stranger", attach=attach,
        ))
    return {
        "fixture_today": FIXTURE_TODAY.isoformat(),
        "member": MEMBER,
        "stranger": STRANGER,
        "accounts": ACCOUNTS,
        "messages": messages,
        "stranger_messages": stranger,
    }


def write() -> Path:
    MAILBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAILBOX_PATH.write_text(
        json.dumps(generate(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return MAILBOX_PATH


# ── The loaded dataset ───────────────────────────────────────────────────────


@dataclass
class Question:
    """A question, with its dates for today and its answering ids."""

    spec: QuestionSpec
    after: date | None
    answering: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.spec.id

    def narrow_filters(self) -> dict[str, Any]:
        filters = dict(self.spec.filters)
        if self.spec.words is not None:
            filters["words"] = self.spec.words
        if self.after is not None:
            filters["after"] = self.after.isoformat()
        return filters

    def inbox_args(self) -> dict[str, Any]:
        args = dict(self.spec.inbox)
        if self.spec.words:
            args["query"] = self.spec.words
        if self.spec.window_days is not None:
            args["days"] = self.spec.window_days
        return args


@dataclass
class Dataset:
    today: date
    messages: list[dict[str, Any]]
    stranger_messages: list[dict[str, Any]]
    accounts: dict[str, dict[str, str]]
    questions: list[Question]

    def by_id(self, message_id: str) -> dict[str, Any] | None:
        for m in self.messages + self.stranger_messages:
            if m["id"] == message_id:
                return m
        return None

    def owner_of(self, message: dict[str, Any]) -> str:
        for acct in self.accounts.values():
            if acct["id"] == message["account_id"]:
                return acct["owner"]
        return ""

    def question(self, qid: str) -> Question:
        return next(q for q in self.questions if q.id == qid)

    def verdict(self, qid: str, message_id: str) -> tuple[str, float]:
        """The STUB door's answer for one message (see the module docstring)."""
        m = self.by_id(message_id)
        if m is None:
            return ("unsure", 0.5)
        if qid in m.get("verdicts", {}):
            choice, p = m["verdicts"][qid]
            return (str(choice), float(p))
        return ("yes", 0.9) if qid in m.get("answers", []) else ("no", 0.92)


def load(today: date | None = None, path: Path = MAILBOX_PATH) -> Dataset:
    """The fixture, with every date moved so that the fixture's day is *today*."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    today = today or datetime.now(UTC).date()
    shift = today - date.fromisoformat(raw["fixture_today"])

    def moved(m: dict[str, Any]) -> dict[str, Any]:
        out = dict(m)
        out["received_at"] = (datetime.fromisoformat(m["received_at"]) + shift).isoformat()
        return out

    messages = [moved(m) for m in raw["messages"]]
    questions = []
    for spec in QUESTIONS:
        after = today - timedelta(days=spec.window_days) if spec.window_days else None
        answering = tuple(m["id"] for m in messages if spec.id in m.get("answers", []))
        questions.append(Question(spec, after, answering))
    return Dataset(
        today=today, messages=messages,
        stranger_messages=[moved(m) for m in raw["stranger_messages"]],
        accounts=raw["accounts"], questions=questions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write fixtures/mailbox.json")
    args = parser.parse_args(argv)
    if args.write:
        print(write())
        return 0
    data = generate()
    print(f"{len(data['messages'])} messages, "
          f"{len({m['from_address']['email'] for m in data['messages']})} senders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
