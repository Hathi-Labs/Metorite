"""The synthetic chats and the questions of the WhatsApp narrowing eval (WS-48 N4).

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §7.2 and §9 N4. The
eval follows ``evals/email_narrowing/``.

🔴 **No real chat.** Every name and text here is invented. No message holds a
phone number, and every WhatsApp id is a ``test-`` id. ``fixtures/chats.json``
is the output of :func:`generate`, and ``test_whatsapp_narrowing_eval.py``
fails when the two differ. To change the chats, change this file and run::

    uv run python -m evals.whatsapp_narrowing.dataset --write

The member holds 21 chats with one person and 6 groups, over 60 days. ANOTHER
member of the same org (the stranger) holds three chats with the same names,
and their messages match every question. So a leak across members shows in
the eval, also through the ``contact`` filter.

**Dates.** The fixture is written for :data:`FIXTURE_TODAY`. :func:`load`
moves every date by the days from that date to today.

**The verdicts of the stub door.** An answering message gets a sure ``yes``,
and any other message a sure ``no``, except where a message names its own
verdict: a borderline message gets ``unsure`` (kept, so it costs a read), and a
hard answering message gets a low ``no`` (kept by the keep rule). These
verdicts are STUB numbers. Only a live run (``--compare``) asks a real
``tier-decide`` model.
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
CHATS_PATH = HERE / "fixtures" / "chats.json"

#: The day that the fixture is written for. :func:`load` moves it to today.
FIXTURE_TODAY = date(2026, 10, 8)
#: The span of the chats, in days before :data:`FIXTURE_TODAY`.
SPAN_DAYS = 60

MEMBER = "member@narrow.test"
STRANGER = "stranger@narrow.test"
ORG = "org-whatsapp-narrowing-eval"

_NS = uuid.UUID("9b8e1c2a-48f4-4e3b-9a77-0d2b6c1e4804")


def ident(*parts: Any) -> str:
    """A stable UUID for a fixture key."""
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


ACCOUNTS: dict[str, dict[str, str]] = {
    "main": {"id": ident("account", "main"), "owner": MEMBER, "label": "Main number"},
    "stranger": {"id": ident("account", "stranger"), "owner": STRANGER, "label": "Stranger"},
}

# ── The people and the chats ─────────────────────────────────────────────────

#: (key, name, company, role). Every name and company is invented.
CONTACTS: tuple[tuple[str, str, str, str], ...] = (
    ("asha", "Asha Menon", "Northfield Pumps", "dealer"),
    ("bilal", "Bilal Shah", "Bilal Agencies", "dealer"),
    ("chitra", "Chitra Iyer", "Chitra Hydro", "dealer"),
    ("dev", "Dev Malhotra", "Dev Traders", "dealer"),
    ("esha", "Esha Kapoor", "Kapoor Irrigation", "dealer"),
    ("farhan", "Farhan Ali", "Ali Pump House", "dealer"),
    ("gopal", "Gopal Rao", "Rao Motors", "dealer"),
    ("hema", "Hema Nair", "Nair Farm Supply", "dealer"),
    ("imran", "Imran Qureshi", "Qureshi Farms", "customer"),
    ("jaya", "Jaya Pillai", "Pillai Dairy", "customer"),
    ("kabir", "Kabir Sethi", "Sethi Builders", "customer"),
    ("lata", "Lata Joshi", "Joshi Nursery", "customer"),
    ("manoj", "Manoj Verma", "Verma Hotels", "customer"),
    ("nisha", "Nisha Bose", "Bose Clinic", "customer"),
    ("om", "Om Prakash", "Prakash Mills", "customer"),
    ("pooja", "Pooja Reddy", "Reddy Gardens", "customer"),
    ("qasim", "Qasim Khan", "Khan Castings", "supplier"),
    ("rekha", "Rekha Das", "Das Motor Parts", "supplier"),
    ("sanjay", "Sanjay Gupta", "Gupta Freight", "supplier"),
    ("tara", "Tara Singh", "our team", "colleague"),
    ("uday", "Uday Patil", "our team", "colleague"),
)
_CONTACT = {c[0]: c for c in CONTACTS}

#: (key, name, the keys of the people who write in it).
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("dealers_north", "Dealers North", ("asha", "bilal", "dev", "gopal")),
    ("dealers_south", "Dealers South", ("chitra", "esha", "farhan", "hema")),
    ("service_team", "Service Team", ("tara", "uday")),
    ("factory_floor", "Factory Floor", ("tara", "uday")),
    ("family", "Family", ("tara",)),
    ("news_forward", "Trade News Forward", ("qasim", "rekha", "sanjay")),
)
_GROUP = {g[0]: g for g in GROUPS}


def chat_name(key: str) -> str:
    if key in _GROUP:
        return _GROUP[key][1]
    _k, name, company, _role = _CONTACT[key]
    return f"{name} ({company})"


def _chats() -> list[dict[str, Any]]:
    out = []
    for key, *_rest in CONTACTS:
        out.append({"slug": key, "id": ident("chat", key), "account_id": ACCOUNTS["main"]["id"],
                    "wa_chat_id": f"test-dm-{key}", "kind": "dm", "name": chat_name(key)})
    for key, name, _members in GROUPS:
        out.append({"slug": key, "id": ident("chat", key), "account_id": ACCOUNTS["main"]["id"],
                    "wa_chat_id": f"test-group-{key}", "kind": "group", "name": name})
    # The stranger: the same names, another member's account.
    for key, kind in (("asha", "dm"), ("dealers_north", "group"), ("imran", "dm")):
        out.append({"slug": f"stranger_{key}", "id": ident("chat", "stranger", key),
                    "account_id": ACCOUNTS["stranger"]["id"],
                    "wa_chat_id": f"test-stranger-{key}", "kind": kind, "name": chat_name(key)})
    return out


# ── The neutral text ─────────────────────────────────────────────────────────
#
# ⚠️ The noise holds none of the search words of the questions (see
# :data:`QUESTIONS`). ``test_whatsapp_narrowing_eval.py`` checks it. A search
# word in the noise would make a noise message a match, and move the numbers.

NOISE_IN: tuple[str, ...] = (
    "Good morning sir 🙏", "Ok noted 👍", "Thank you ji", "Call me when you are free",
    "Reached the site, waiting at the gate", "Payment done, please check the account",
    "Can we meet on Monday?", "Happy Diwali to you and your family 🪔", "Ji sir",
    "Theek hai", "Kal milte hain", "Where is the invoice for last month?",
    "Order 4471 received, thanks", "Please call back after lunch", "👍", "Ok",
    "The truck reached our godown this morning", "Our office is closed on Sunday",
    "Can you check the warranty card number for me?", "Will call you in the evening",
    "Is the new catalogue ready?", "Our accountant needs the GST number again",
    "Meeting at three is fine", "Noted, thanks for the update",
)
NOISE_OUT: tuple[str, ...] = (
    "Ok", "Noted 👍", "Sure, calling you in ten minutes", "Thanks ji 🙏", "Done",
    "Received with thanks", "Good morning", "Yes, Monday works", "Let me check and revert",
    "Please call me after six", "Happy Diwali 🪔", "Ji, theek hai",
)
NOISE_GROUP: tuple[str, ...] = (
    "Good morning all 🌞", "Happy Diwali everyone 🪔", "👍👍", "Noted",
    "Who is coming to the meet on Friday?", "Payment cycle is on the 10th as usual",
    "Please update your GST details in the portal", "Lunch is at one today",
    "Rain in our area since morning", "Congratulations on the new showroom 🎉",
    "The road near the highway is closed today", "Thanks for the visit yesterday",
)
#: Long forwards, each one past the snippet clip, so PICK has text to save.
FORWARDS: tuple[str, ...] = (
    "Forwarded: Ten tips for pump care in the monsoon. One, keep the motor dry and "
    "raise it above the floor. Two, clean the foot valve every month. Three, check the "
    "earthing before the first rain. Four, keep a spare capacitor at the site. Five, "
    "never run the pump dry for more than a minute. Six, use the right cable size for "
    "the load. Seven, keep the panel door closed. Eight, check the bearings for noise. "
    "Nine, log the hours of use. Ten, call a trained engineer for any repair.",
    "Forwarded: The state farm fair opens next month at the exhibition ground. More "
    "than two hundred stalls will show tractors, drip lines, solar panels and seeds. "
    "Entry is free for registered farmers, and the organisers ask every dealer to "
    "bring a banner and a stand. The fair runs for four days, from nine in the "
    "morning to seven in the evening, and there is a seminar on water use each day.",
    "Forwarded: A new study of small farms shows that drip lines cut water use by a "
    "third in two seasons. The study looked at four hundred farms in three districts. "
    "Farms with a timer on the pump saved the most power. The authors advise a yearly "
    "check of the filters, and a soil test before each season, to plan the water.",
    "Forwarded: Notice to all members of the trade body. The annual meeting is on the "
    "first Saturday of next month, at the hall near the bus stand. The agenda is the "
    "accounts of the year, the new committee, and the plan for the trade fair. Lunch "
    "follows the meeting. Members who cannot come may vote by proxy with a signed form.",
)


# ── The planted messages: the answers and the distractors ───────────────────


@dataclass
class Plant:
    """One planted message. ``answers`` names the questions that it answers.
    ``verdicts`` names the stub door's answer for a question, where it is not
    the default."""

    key: str
    chat: str
    days_ago: int
    text: str
    direction: str = "in"
    sender: str | None = None  # a contact key, for a group; None = the dm contact
    kind: str = "text"
    voice: bool = False        # the text is the transcript of a voice note
    answers: tuple[str, ...] = ()
    verdicts: dict[str, tuple[str, float]] = field(default_factory=dict)


def _plants() -> list[Plant]:  # noqa: PLR0915 — one flat list of fixtures
    p: list[Plant] = []
    add = p.append
    # Q1: dealers who asked for the price of the X200 in the last 30 days. 8
    # answers. Three say "rate", "quote" or "kitna", and one is a voice note.
    add(Plant("q1_asha", "asha", 3, "What is the price of the X200 pump with the stand?",
              answers=("Q1",)))
    add(Plant("q1_bilal", "bilal", 6, "Sir X200 ka price kya hai? Need 10 units.",
              answers=("Q1",)))
    add(Plant("q1_chitra", "chitra", 9, "Please quote for 25 pieces of X200, to our godown.",
              answers=("Q1",), verdicts={"Q1": ("unsure", 0.55)}))
    add(Plant("q1_dev", "dev", 12, "X200 kitna hai for dealers? Same as last year?",
              answers=("Q1",)))
    add(Plant("q1_esha", "esha", 15, "Namaste ji, X200 pump ka rate batao, hum bees piece lenge",
              voice=True, kind="voice", answers=("Q1",)))
    add(Plant("q1_farhan", "farhan", 18, "Your prices for the X200 and the X300 range, please",
              answers=("Q1",)))
    add(Plant("q1_gopal", "dealers_north", 23, "What is the new rate for X200 for us dealers?",
              sender="gopal", answers=("Q1",), verdicts={"Q1": ("no", 0.6)}))
    add(Plant("q1_hema", "hema", 25, "What is your best price for X200 this season?",
              answers=("Q1",)))
    # Q1 distractors.
    add(Plant("q1_mine", "asha", 3, "The X200 price is 18,500 for each unit, with the stand.",
              direction="out"))  # the member's own word: from_me=false leaves it out
    for i, (chat, days) in enumerate([("asha", 40), ("dev", 47), ("hema", 55)]):
        add(Plant(f"q1_old_{i}", chat, days, "X200 price for the next season?"))
    for i, (sender, days, line) in enumerate([
        ("qasim", 4, "Steel prices rose by four percent this month."),
        ("rekha", 11, "Copper rate fell again on weak demand."),
        ("sanjay", 20, "Diesel price is up by two rupees from today."),
    ]):
        add(Plant(f"q1_news_{i}", "news_forward", days, line, sender=sender))
    add(Plant("q1_supplier", "qasim", 7, "Our new rate list for castings is in this file.",
              kind="document", verdicts={"Q1": ("no", 0.8)}))
    add(Plant("q1_service", "imran", 10, "What is the price of one service visit?",
              verdicts={"Q1": ("unsure", 0.5)}))
    add(Plant("q1_bus", "dev", 14, "The bus rate went up, so I come by train tomorrow.",
              verdicts={"Q1": ("no", 0.75)}))

    # Q2: what Dealers North said about the October stock in the last 3 weeks.
    # 7 answers. The search is on the filters only.
    for i, (sender, days, line, voice) in enumerate([
        ("asha", 2, "October stock of X200 is down to 4 pieces at our end.", False),
        ("bilal", 5, "We need 12 more X300 for October.", False),
        ("dev", 8, "Our October count: 30 motors and 8 pumps in the godown.", False),
        ("gopal", 11, "Can the October lot reach us before Diwali?", False),
        ("asha", 14, "October ka maal kab aayega? Shelves are empty.", True),
        ("bilal", 17, "For October we are fine on motors, short on pumps.", False),
        ("dev", 19, "Please plan the October lot for the highway shops too.", False),
    ]):
        add(Plant(f"q2_stock_{i}", "dealers_north", days, line, sender=sender,
                  voice=voice, kind="voice" if voice else "text", answers=("Q2",)))
    add(Plant("q2_market", "dealers_north", 9, "Stock market is down today 😅", sender="gopal",
              verdicts={"Q2": ("unsure", 0.5)}))
    for i, days in enumerate([25, 31, 38]):
        add(Plant(f"q2_old_{i}", "dealers_north", days,
                  f"September count {i + 1}: all fine at our end.", sender="dev"))

    # Q3: customers who sent a photo, a video, a voice note or a file of a
    # damaged delivery. 5 answers.
    add(Plant("q3_imran", "imran", 4, "Photo: the pump body is cracked after delivery",
              kind="image", answers=("Q3",)))
    add(Plant("q3_jaya", "jaya", 8, "The box came damaged, see the photo", kind="image",
              answers=("Q3",)))
    add(Plant("q3_kabir", "kabir", 13, "Damage report for order 5512, in this file",
              kind="document", answers=("Q3",)))
    add(Plant("q3_lata", "lata", 19, "the motor is leaking since the delivery came",
              kind="voice", voice=True, answers=("Q3",)))
    add(Plant("q3_manoj", "manoj", 27, "Video of the dented cover, it came like this",
              kind="video", answers=("Q3",)))
    add(Plant("q3_fine", "nisha", 6, "No damage at all, all good, thanks", kind="image",
              verdicts={"Q3": ("no", 0.85)}))
    add(Plant("q3_van", "om", 10, "The van broke down on the way, it comes tomorrow"))
    add(Plant("q3_mine", "imran", 4, "Here is the cracked part that we took back",
              direction="out", kind="image"))
    add(Plant("q3_colleague", "service_team", 12, "Broken bracket at the plant, photo",
              sender="tara", kind="image", verdicts={"Q3": ("unsure", 0.5)}))

    # Q4: what the member promised to send to customers this week. 5 answers.
    add(Plant("q4_imran", "imran", 1, "I will send the invoice by Friday", direction="out",
              answers=("Q4",)))
    add(Plant("q4_jaya", "jaya", 2, "Kal tak quotation bhejunga", direction="out",
              answers=("Q4",)))
    add(Plant("q4_kabir", "kabir", 3, "I will share the manual tomorrow morning",
              direction="out", answers=("Q4",)))
    add(Plant("q4_lata", "lata", 4, "Main kal subah spare part dispatch kar dunga",
              direction="out", kind="voice", voice=True, answers=("Q4",)))
    add(Plant("q4_pooja", "pooja", 6, "Sending the warranty card today", direction="out",
              answers=("Q4",)))
    add(Plant("q4_ask", "manoj", 2, "Please send me your address", direction="out",
              verdicts={"Q4": ("no", 0.85)}))
    add(Plant("q4_done", "nisha", 5, "I sent it yesterday, please check", direction="out"))
    add(Plant("q4_duty", "service_team", 3, "Please share the duty list", direction="out",
              verdicts={"Q4": ("no", 0.8)}))
    add(Plant("q4_old", "om", 9, "I will send the catalogue next week", direction="out"))

    # Q5: customers angry about late service. 5 answers. Two share NO word
    # with the search: the lexical limit (Q3 of the spec), the expected miss.
    add(Plant("q5_om", "om", 5, "I am very upset, the engineer came three days late",
              answers=("Q5",)))
    add(Plant("q5_pooja", "pooja", 11, "This is a complaint, the service is always late",
              answers=("Q5",)))
    add(Plant("q5_nisha", "nisha", 16, "Unhappy with the delay in the repair",
              answers=("Q5",)))
    add(Plant("q5_nowords_0", "manoj", 9, "Third time I am calling, nobody came to fix the motor",
              answers=("Q5",)))
    add(Plant("q5_nowords_1", "kabir", 20, "Very bad experience, your engineer never showed up",
              answers=("Q5",)))
    add(Plant("q5_meeting", "uday", 7, "Running late for the meeting, start without me"))
    add(Plant("q5_family", "family", 13, "Train is late, start dinner 🙂", sender="tara"))
    return p


# ── The questions ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QuestionSpec:
    """A question of the eval. ``filters`` and ``words`` are what a model writes
    for ``narrow_and_read``. ``searches`` and ``read_chat`` are today's path:
    one ``search_whatsapp`` for each word (the route ANDs the words of one
    search), or one chat read in full."""

    id: str
    prompt: str
    words: str | None
    filters: dict[str, Any]
    window_days: int | None
    searches: tuple[str, ...] = ()
    read_chat: str | None = None
    gated: bool = True
    note: str = ""


QUESTIONS: tuple[QuestionSpec, ...] = (
    QuestionSpec(
        "Q1", "Which dealers asked for the price of the X200 pump in the last 30 days?",
        words="price OR prices OR rate OR rates OR quote OR kitna",
        filters={"from_me": False}, window_days=30,
        searches=("price", "prices", "rate", "quote", "kitna"),
    ),
    QuestionSpec(
        "Q2", "Summarise what the Dealers North group said about the October stock "
        "in the last 3 weeks.",
        words="", filters={"contact": "Dealers North", "group": True}, window_days=21,
        read_chat="dealers_north",
    ),
    QuestionSpec(
        "Q3", "Which customers sent a photo, a video or a file of a damaged delivery?",
        words="damaged OR damage OR broken OR cracked OR leaking OR dented",
        filters={"has_media": True, "from_me": False}, window_days=None,
        searches=("damaged", "damage", "broken", "cracked", "leaking", "dented"),
    ),
    QuestionSpec(
        "Q4", "What did I promise to send to customers this week?",
        words="send OR sending OR share OR bhejunga OR dispatch",
        filters={"from_me": True}, window_days=7,
        searches=("send", "sending", "share", "bhejunga", "dispatch"),
    ),
    QuestionSpec(
        "Q5", "Which customers were angry about late service?",
        words="angry OR upset OR unhappy OR late OR delay OR complaint",
        filters={"from_me": False}, window_days=None, gated=False,
        searches=("angry", "upset", "unhappy", "late", "delay", "complaint"),
        note="Expected miss (spec Q3): two answers share no word with any search.",
    ),
)

#: Every word of every search. The noise holds none of them.
SEARCH_WORDS = frozenset(
    w.lower() for q in QUESTIONS for w in [*(q.words or "").split(), *q.searches]
    if w != "OR"
)


# ── The generator ────────────────────────────────────────────────────────────


class _Clock:
    """Unique times: the stub door finds a message by its sender and time."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: set[str] = set()

    def at(self, days_ago: int) -> str:
        base = datetime.combine(FIXTURE_TODAY - timedelta(days=days_ago), datetime.min.time(),
                                tzinfo=UTC)
        while True:
            moment = base + timedelta(hours=self.rng.randint(7, 21),
                                      minutes=self.rng.randint(0, 59),
                                      seconds=self.rng.randint(0, 59))
            text = moment.isoformat()
            if text not in self.used:
                self.used.add(text)
                return text


def _sender_name(chat: str, sender: str | None, direction: str) -> str:
    if direction == "out":
        return "Member"
    key = sender or chat
    return _CONTACT[key][1]


def _message(
    key: str, chat: dict[str, Any], sent_at: str, text: str, *, direction: str = "in",
    sender: str | None = None, kind: str = "text", voice: bool = False,
    answers: tuple[str, ...] = (), verdicts: dict[str, tuple[str, float]] | None = None,
) -> dict[str, Any]:
    owner_chat = chat["slug"].removeprefix("stranger_")
    return {
        "slug": key,
        "id": ident("message", key),
        "account_id": chat["account_id"],
        "chat_id": chat["id"],
        "wa_message_id": f"test-{key}",
        "direction": direction,
        "kind": kind,
        "sender_name": _sender_name(owner_chat, sender, direction),
        "body_text": "" if voice else text,
        "transcript_text": text if voice else None,
        "sent_at": sent_at,
        "answers": list(answers),
        "verdicts": {q: [c, pr] for q, (c, pr) in (verdicts or {}).items()},
    }


def generate() -> dict[str, Any]:
    """The whole fixture, deterministic. :data:`CHATS_PATH` holds its JSON."""
    rng = random.Random(4804)
    clock = _Clock(rng)
    chats = _chats()
    by_slug = {c["slug"]: c for c in chats}
    messages: list[dict[str, Any]] = []
    for plant in _plants():
        messages.append(_message(
            plant.key, by_slug[plant.chat], clock.at(plant.days_ago), plant.text,
            direction=plant.direction, sender=plant.sender, kind=plant.kind,
            voice=plant.voice, answers=plant.answers, verdicts=plant.verdicts,
        ))
    # Noise: each chat with one person, then each group.
    n = 0
    for key, *_rest in CONTACTS:
        for _ in range(rng.randint(8, 14)):
            out = rng.random() < 0.4
            text = rng.choice(NOISE_OUT if out else NOISE_IN)
            messages.append(_message(f"noise_{n}", by_slug[key], clock.at(rng.randint(0, SPAN_DAYS - 1)),
                                     text, direction="out" if out else "in"))
            n += 1
    for key, _name, members in GROUPS:
        count = 45 if key == "dealers_north" else 18
        for _ in range(count):
            if key == "news_forward" or rng.random() < 0.12:
                text = rng.choice(FORWARDS)
            else:
                text = rng.choice(NOISE_GROUP)
            messages.append(_message(f"noise_{n}", by_slug[key],
                                     clock.at(rng.randint(0, SPAN_DAYS - 1)), text,
                                     sender=rng.choice(members)))
            n += 1
    # The stranger: another member of the same org, with messages that match
    # every question. They must never reach the member's run.
    stranger: list[dict[str, Any]] = []
    for i, (chat, text, direction, kind, sender) in enumerate([
        ("stranger_asha", "What is the price of the X200 pump, stranger?", "in", "text", None),
        ("stranger_dealers_north", "October stock is low here, stranger", "in", "text", "asha"),
        ("stranger_imran", "Photo: the pump came cracked and damaged, stranger", "in", "image", None),
        ("stranger_imran", "I will send the invoice today, stranger", "out", "text", None),
        ("stranger_imran", "I am upset, the service is late, a complaint, stranger", "in", "text", None),
        ("stranger_dealers_north", "X200 rate and quote for the stranger group", "in", "text", "dev"),
    ] * 2):
        stranger.append(_message(
            f"stranger_{i}", by_slug[chat], clock.at(1 + i), f"{text} {i}",
            direction=direction, kind=kind, sender=sender,
        ))
    return {
        "fixture_today": FIXTURE_TODAY.isoformat(),
        "member": MEMBER,
        "stranger": STRANGER,
        "accounts": ACCOUNTS,
        "chats": chats,
        "messages": messages,
        "stranger_messages": stranger,
    }


def write() -> Path:
    CHATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHATS_PATH.write_text(
        json.dumps(generate(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return CHATS_PATH


# ── The loaded dataset ───────────────────────────────────────────────────────


def message_text(m: dict[str, Any]) -> str:
    """The text that a search and a reader see: the body, or the transcript."""
    return str(m.get("body_text") or m.get("transcript_text") or "")


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


@dataclass
class Dataset:
    today: date
    chats: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    stranger_messages: list[dict[str, Any]]
    accounts: dict[str, dict[str, str]]
    questions: list[Question]

    def by_id(self, message_id: str) -> dict[str, Any] | None:
        for m in self.messages + self.stranger_messages:
            if m["id"] == message_id:
                return m
        return None

    def chat(self, chat_id: str) -> dict[str, Any] | None:
        return next((c for c in self.chats if c["id"] == chat_id), None)

    def chat_by_slug(self, slug: str) -> dict[str, Any]:
        return next(c for c in self.chats if c["slug"] == slug)

    def owner_of_account(self, account_id: str) -> str:
        for acct in self.accounts.values():
            if acct["id"] == account_id:
                return acct["owner"]
        return ""

    def owner_of(self, message: dict[str, Any]) -> str:
        return self.owner_of_account(message["account_id"])

    def question(self, qid: str) -> Question:
        return next(q for q in self.questions if q.id == qid)

    def verdict(self, qid: str, message_id: str) -> tuple[str, float]:
        """The STUB door's answer for one message (see the module docstring)."""
        m = self.by_id(message_id)
        if m is None:
            return ("unsure", 0.5)
        if qid in m.get("verdicts", {}):
            choice, pr = m["verdicts"][qid]
            return (str(choice), float(pr))
        return ("yes", 0.9) if qid in m.get("answers", []) else ("no", 0.92)


def load(today: date | None = None, path: Path = CHATS_PATH) -> Dataset:
    """The fixture, with every date moved so that the fixture's day is *today*."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    today = today or datetime.now(UTC).date()
    shift = today - date.fromisoformat(raw["fixture_today"])

    def moved(m: dict[str, Any]) -> dict[str, Any]:
        out = dict(m)
        out["sent_at"] = (datetime.fromisoformat(m["sent_at"]) + shift).isoformat()
        return out

    messages = [moved(m) for m in raw["messages"]]
    questions = []
    for spec in QUESTIONS:
        after = today - timedelta(days=spec.window_days) if spec.window_days else None
        answering = tuple(m["id"] for m in messages if spec.id in m.get("answers", []))
        questions.append(Question(spec, after, answering))
    return Dataset(
        today=today, chats=raw["chats"], messages=messages,
        stranger_messages=[moved(m) for m in raw["stranger_messages"]],
        accounts=raw["accounts"], questions=questions,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write fixtures/chats.json")
    args = parser.parse_args(argv)
    if args.write:
        print(write())
        return 0
    data = generate()
    print(f"{len(data['messages'])} messages in {len(data['chats'])} chats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
