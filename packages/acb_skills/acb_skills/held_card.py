"""Held chat: you confirm new people (``email_app_master_plan.md`` §21).

A held chat is a chat that read mail from outside senders (H-236). Such a
chat holds no tool that the model can point at a new address. §21 lets it
send anyway, through ONE card that the gateway builds and records:

* the gateway classifies each person on the card, and the model never does
  (:data:`CLASS_ORG`, :data:`CLASS_SENT`, :data:`CLASS_THREAD`,
  :data:`CLASS_NEW`);
* a known person starts ticked, and a new address starts unticked with a
  mark (:data:`NEW_MARK`);
* the gateway sends only to a ticked row that it recorded for that card.

This module holds what the gateway, the email assistant and the client must
agree on. The gateway half is ``gateway/routes/email/automation/held_card.py``.
The client half is ``workbench/control_plane/src/lib/sendCard.ts``.

**Dark.** :func:`held_card_send_enabled` is the ONE reader of
``EMAIL_HELD_CARD_SEND``, and it is off by default. With it off, a held chat
holds exactly the tools of H-374, and each route of the gateway half answers
404. The flag moves mail to real people, so the owner flips it.
Fence: ``tests/unit/test_egress_card_bound_pin.py``.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.held_card")

__all__ = [
    "ADDED_MARK",
    "ADDRESS_MAX",
    "ARTIFACT_REF",
    "ASSISTANT_ADDED_MARK",
    "ATTACHMENT_REF",
    "CLASSES",
    "CLASS_NEW",
    "CLASS_ORG",
    "CLASS_SENT",
    "CLASS_THREAD",
    "DEFAULT_TICKED",
    "HELD_SETTING_DECISIONS",
    "HELD_TEXT_MAX",
    "HELD_WORKSPACE_AGENT",
    "HELD_WORKSPACE_REFUSED",
    "KIND_BCC",
    "KIND_CC",
    "KIND_FILE",
    "KIND_TO",
    "KNOWLEDGE_REF",
    "METORITE_FILE_REF",
    "METORITE_FILE_REFUSED",
    "NEW_MARK",
    "RECIPIENT_KINDS",
    "ROW_KINDS",
    "RULE_COPY_MARK",
    "SETTING_CLOSED",
    "SETTING_MAILBOX",
    "SETTING_REFUSED",
    "TASK_FILE_REF",
    "THREAD_MARK",
    "USUAL_COPY_MARK",
    "WORKSPACE_FILE_REFUSED",
    "address_problem",
    "class_mark",
    "closed_setting_value",
    "confusable_of",
    "domain_of",
    "file_label",
    "held_card_send_enabled",
    "is_metorite_file_ref",
    "lookalike_of",
    "lookalike_warning",
    "parse_file_ref",
    "recipient_label",
    "rule_copy_mark",
    "strongest_class",
    "thread_mark",
]


def held_card_send_enabled() -> bool:
    """True when ``EMAIL_HELD_CARD_SEND`` is on. THE one reader.

    It fails closed: a fault in the settings read is off. Each new held-run
    path of §21 asks it on the server: the egress pin, the email tools and
    the gateway routes."""
    try:
        from acb_common import get_settings

        return bool(getattr(get_settings(), "email_held_card_send", False))
    except Exception:  # never arm a send on a fault
        _log.warning("held_card.flag_read_failed")
        return False


# ── The classes of a person (§21.4 item 2) ───────────────────────────────────

#: A member of the organization, or a People row of it.
CLASS_ORG = "org"
#: A person that the member wrote to before (a To or a Cc of a sent mail).
CLASS_SENT = "sent"
#: A person of this thread: the sender of a received mail of the thread, or a
#: To or a Cc of such a mail. The owner answered open question 1 on
#: 2026-10-24: "Everyone on the original email" starts ticked. One carve-out
#: stays: a person whose domain is a LOOK-ALIKE of the domain of a sender of
#: the thread, or of an organization domain (:func:`lookalike_of`), is new,
#: unticked and flagged. A Reply-To that is not the sender is never thread.
CLASS_THREAD = "thread"
#: Anything else. A sender of outside mail (``seen``) is new too, because the
#: attacker is a sender (§21.2 rule 3).
CLASS_NEW = "new"

#: The classes, strongest first.
CLASSES: tuple[str, ...] = (CLASS_ORG, CLASS_SENT, CLASS_THREAD, CLASS_NEW)
#: The classes whose row starts ticked. A new address starts unticked.
DEFAULT_TICKED: frozenset[str] = frozenset({CLASS_ORG, CLASS_SENT, CLASS_THREAD})

#: The mark on a new address (§21.1 item 2). The client draws the same words
#: (``sendCard.ts``), and a test holds the two in step.
NEW_MARK = "new address — not in your contacts"
#: The mark on a person of the thread.
THREAD_MARK = "from this thread"
#: The mark on a colleague that the server put on a reply card, because the
#: member copies them on mail of this thread or to this domain (item 7 of
#: the owner's request of 2026-10-24).
USUAL_COPY_MARK = "you usually copy them"
#: The head of the mark on a person that the member's own "always copy" rule
#: adds (§21.15): "your rule: always copy on jainuniversity.ac.in".
RULE_COPY_MARK = "your rule: always copy on"
#: The mark on a person that the chat added to a reply, outside a held run.
#: The server cannot know that the member named the person (review P1-b).
ADDED_MARK = "added to this reply"
#: The mark on a person that the chat added in a held run: the model chose
#: the address, and the member checks it (review P1-b).
ASSISTANT_ADDED_MARK = "the assistant added them"


def thread_mark(sender: str) -> str:
    """The mark on a person of the original email, with the sender named:
    "on Dr Sagar's original email". *sender* is the name of the sender, else
    the address."""
    who = " ".join(str(sender or "").split())[:80]
    return f"on {who}'s original email" if who else "on the original email"


def rule_copy_mark(match: str) -> str:
    """The mark of a colleague that the member's always-copy rule for
    *match* (a domain or an address) adds to a card (§21.15)."""
    m = " ".join(str(match or "").split())[:120]
    return f"{RULE_COPY_MARK} {m}" if m else RULE_COPY_MARK


def strongest_class(classes: Iterable[str]) -> str:
    """The strongest class of *classes*, or :data:`CLASS_NEW` for none."""
    have = set(classes)
    for cls in CLASSES:
        if cls in have:
            return cls
    return CLASS_NEW


def class_mark(cls: str) -> str:
    """The mark that the card shows for a row of *cls*, or ""."""
    if cls == CLASS_NEW:
        return NEW_MARK
    if cls == CLASS_THREAD:
        return THREAD_MARK
    return ""


# ── The rows of a card ───────────────────────────────────────────────────────

KIND_TO = "to"
KIND_CC = "cc"
KIND_BCC = "bcc"
#: A file row (§21.1 item 3). The member can untick each one.
KIND_FILE = "file"
#: The kinds of a recipient row, in the order the card lists them.
RECIPIENT_KINDS: tuple[str, ...] = (KIND_TO, KIND_CC, KIND_BCC)
#: Every kind a row of a held card can have. A later slice adds a kind here.
ROW_KINDS: frozenset[str] = frozenset({*RECIPIENT_KINDS, KIND_FILE})


# ── The files rule (§21.1 item 3, §21.3 the Attach row) ──────────────────────
#
# A file of a held send comes from data, never from the model (§21.2 rule 4).
# The model names an id. The server resolves it, as the member, through the
# store and the visibility check that the app uses. Four sources:
#
# * ``attachment:<id>`` — a file of a mail of the member's own mailbox;
# * ``artifact:<agent>:<path>`` — a file of the member's own workspace of
#   that agent, as the Artifacts app shows it (H-392, owner 2026-10-24);
# * ``knowledge:<page id>`` — a Knowledge page that the member can read, as
#   its Markdown (H-392);
# * ``task-file:<attachment id>`` — a file on a task that the member can see
#   (H-392).
#
# A bare workspace path is refused. So is a file of the ``email-assistant``
# workspace in a held chat, because the held run itself can write there,
# from data that it read (:data:`HELD_WORKSPACE_AGENT`).

#: The head of a reference to a file of a mail of the member's own mailbox.
ATTACHMENT_REF = "attachment:"
#: H-392: the heads of the three Metorite file references.
ARTIFACT_REF = "artifact:"
KNOWLEDGE_REF = "knowledge:"
TASK_FILE_REF = "task-file:"
#: The head of an old, unscoped Metorite file reference. It names no store.
METORITE_FILE_REF = "file:"
#: The agent whose workspace a held run can write. Its files are refused in a
#: held chat, because an injection could have the run write member data there.
HELD_WORKSPACE_AGENT = "email-assistant"
#: The forms that the refusals name.
_FILE_FORMS = (
    "attachment:<id> from read_email, artifact:<agent>:<path> from the "
    "Artifacts app, knowledge:<page id>, or task-file:<attachment id>")
#: The refusal of a workspace path, or of any reference of no known form.
WORKSPACE_FILE_REFUSED = (
    "A bare workspace path, or a reference that names no store, cannot be "
    "attached, so nothing was sent. Name a file as " + _FILE_FORMS + ".")
#: The refusal of an unscoped ``file:<id>``.
METORITE_FILE_REFUSED = (
    "A file:<id> reference names no store, so nothing was sent. Name a file as "
    + _FILE_FORMS + ".")
#: The refusal of a file of the held run's own workspace in a held chat.
HELD_WORKSPACE_REFUSED = (
    "A chat that read outside mail cannot attach a file of the email "
    "assistant's own workspace, because this chat can write there. Nothing "
    "was sent. Attach a file of the member's mailbox, of another workspace, "
    "of Knowledge or of a task.")


def parse_file_ref(ref: Any) -> tuple[str, str]:
    """``(source, id)`` of one file reference of a chat send.

    ``("attachment", <uuid>)``, ``("knowledge", <uuid>)`` and
    ``("task-file", <uuid>)`` carry a canonical id. ``("artifact",
    "<agent>:<path>")`` carries the agent and the path. Anything else raises
    ``ValueError`` with the refusal that the member reads: a ``file:<id>``
    with :data:`METORITE_FILE_REFUSED`, and a workspace path or any other
    text with :data:`WORKSPACE_FILE_REFUSED`. The server checks that the
    member can see each file."""
    import uuid

    text = str(ref or "").strip()
    low = text.lower()
    for head, source in ((ATTACHMENT_REF, "attachment"), (KNOWLEDGE_REF, "knowledge"),
                         (TASK_FILE_REF, "task-file")):
        if low.startswith(head):
            try:
                return source, str(uuid.UUID(text[len(head):].strip()))
            except ValueError:
                raise ValueError(WORKSPACE_FILE_REFUSED) from None
    if low.startswith(ARTIFACT_REF):
        agent, _, path = text[len(ARTIFACT_REF):].partition(":")
        agent, path = agent.strip(), path.strip()
        if not agent or not path or any(ch.isspace() for ch in agent):
            raise ValueError(WORKSPACE_FILE_REFUSED)
        return "artifact", f"{agent}:{path}"
    if low.startswith(METORITE_FILE_REF):
        raise ValueError(METORITE_FILE_REFUSED)
    raise ValueError(WORKSPACE_FILE_REFUSED)


def is_metorite_file_ref(ref: Any) -> bool:
    """True for a reference of one of the three Metorite stores (H-392)."""
    low = str(ref or "").strip().lower()
    return low.startswith((ARTIFACT_REF, KNOWLEDGE_REF, TASK_FILE_REF))


def file_label(name: str, size: str) -> str:
    """The label of a file row: the name in quotes, and the size. A sender
    chose the name, so the quotes keep it from reading as a recipient."""
    quoted = str(name or "file").replace("\\", "\\\\").replace('"', '\\"')
    return f'File: "{quoted}" ({size})' if size else f'File: "{quoted}"'

#: The longest text that a held card shows. A longer text sends nothing,
#: because the card shows the whole text or no card shows (open question 8).
HELD_TEXT_MAX = 3000

#: The longest address a card shows (the RFC 5321 path limit).
ADDRESS_MAX = 254


def address_problem(addr: str) -> str:
    """Why *addr* cannot be a row, or "".

    A row shows the address that the send uses, character for character. So
    an address with a hidden character, a space or a control is refused, and
    never cleaned in silence."""
    from acb_skills.reply_card import is_invisible_char

    a = str(addr or "")
    if not a or "@" not in a:
        return "is not an address"
    if len(a) > ADDRESS_MAX:
        return f"is longer than {ADDRESS_MAX} characters"
    if any(ch.isspace() or is_invisible_char(ch) for ch in a):
        return "holds a hidden character or a space"
    return ""


# ── Lookalike domains (open question 4, the safer choice) ────────────────────
#
# The card flags a NEW address whose domain looks like a domain that the
# member knows: one edit away ("fracktai.in" for "fracktal.in"), or the same
# after the common look-alike letters are folded ("fr4cktal.in", or a Cyrillic
# letter a, U+0430). The flag is a warning on the row. It never ticks or unticks a row.

#: Letters that a reader takes for another one, folded to that one.
_CONFUSABLE = str.maketrans({
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "!": "i", "|": "l",
    # Cyrillic, written as escapes so that no reader of this file is fooled
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s",
    "\u043a": "k", "\u043c": "m", "\u0442": "t", "\u0432": "b", "\u043d": "h",
    # Greek
    "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03bd": "v", "\u03b9": "i",
    "\u03ba": "k", "\u03c4": "t", "\u03c5": "u",
})
#: The shortest domain that the one-edit rule reads. A short domain has too
#: many neighbours, so only the folded rule reads it.
_EDIT_MIN = 6


def confusable_of(ch: str) -> str:
    """The Latin letter that *ch* imitates, by the ONE look-alike table
    (:data:`_CONFUSABLE`), or "" when the table holds no entry for a
    letter that is not ASCII. The mixed-script check of a chat send names
    this letter (H-393, ``reply_card.lookalike_words``)."""
    if not ch or ch.isascii():
        return ""
    folded = ch.translate(_CONFUSABLE)
    return folded if folded != ch else ""


def _unicode_domain(domain: str) -> str:
    d = domain.strip().lower().rstrip(".")
    if "xn--" in d:
        try:
            d = d.encode("ascii").decode("idna")
        except (UnicodeError, ValueError):
            return d
    return d


def _skeleton(domain: str) -> str:
    d = unicodedata.normalize("NFKD", _unicode_domain(domain))
    d = "".join(ch for ch in d if not unicodedata.combining(ch))
    d = d.translate(_CONFUSABLE)
    return d.replace("rn", "m").replace("vv", "w").replace("cl", "d")


def _one_edit(a: str, b: str) -> bool:
    """True when *a* and *b* differ by one insert, one delete, one change or
    one swap of two neighbours."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def lookalike_of(domain: str, known: Iterable[str]) -> str | None:
    """The known domain that *domain* looks like, or None.

    None for a domain that IS known, and for a domain that looks like none.
    The compare is in lower case."""
    d = _unicode_domain(domain)
    if not d:
        return None
    known_set = {_unicode_domain(k) for k in known if k and str(k).strip()}
    if d in known_set:
        return None
    sk = _skeleton(d)
    for k in sorted(known_set):
        if not k:
            continue
        if _skeleton(k) == sk:
            return k
        if min(len(d), len(k)) >= _EDIT_MIN and _one_edit(d, k):
            return k
    return None


def lookalike_warning(known: str) -> str:
    """The warning on a row whose domain looks like *known*."""
    return f"the domain looks like {known}, which you know — check each letter"


def domain_of(addr: str) -> str:
    """The domain of *addr*, in lower case, or ""."""
    return str(addr or "").rpartition("@")[2].strip().lower()


def recipient_label(kind: str, addr: str) -> str:
    """The label of a recipient row: the head and the whole address. A
    domain that is not ASCII also shows its punycode form, so a look-alike
    letter shows."""
    head = {KIND_TO: "To", KIND_CC: "Cc", KIND_BCC: "Bcc"}.get(kind, kind)
    domain = domain_of(addr)
    if domain.isascii():
        return f"{head}: {addr}"
    try:
        puny = domain.encode("idna").decode("ascii")
    except UnicodeError:
        puny = "no punycode form"
    return f"{head}: {addr} (non-ASCII domain: {puny})"



# ── Settings through closed keys (§21.6) ─────────────────────────────────────
#
# A held chat may change the member's own settings of one mailbox, behind a
# card that names each key, its old value and its new value. Only a key with
# a fixed set of values, or a number, may change. Free text that a later run
# reads and acts on stays held, because an injection could persist there.

#: A parameter that names the mailbox. The tool checks that the member owns
#: it, and the gateway checks the owner again.
SETTING_MAILBOX = "mailbox"
#: A key with a fixed set of values, or a number in a fixed range.
SETTING_CLOSED = "closed"
#: A key that a held chat may not change.
SETTING_REFUSED = "refused"

#: The decision for each parameter of ``update_assistant_settings``. A test
#: holds this map to the tool's parameters, so a new setting needs a
#: decision here (``tests/unit/test_held_settings.py``).
HELD_SETTING_DECISIONS: dict[str, str] = {
    "account_id": SETTING_MAILBOX,
    "about": SETTING_REFUSED,
    "signature": SETTING_REFUSED,
    "personal_instructions": SETTING_REFUSED,
    "writing_style": SETTING_REFUSED,
    "auto_run": SETTING_CLOSED,
    "cold_email_blocker": SETTING_CLOSED,
    "draft_replies": SETTING_CLOSED,
    "draft_confidence": SETTING_CLOSED,
    "follow_up_awaiting_days": SETTING_CLOSED,
    "follow_up_needs_reply_days": SETTING_CLOSED,
    "follow_up_auto_draft": SETTING_CLOSED,
    "digest_frequency": SETTING_CLOSED,
    # A list of free names that later runs read: held (the safer choice).
    "digest_categories": SETTING_REFUSED,
    "digest_day_of_week": SETTING_CLOSED,
    "digest_time_of_day": SETTING_CLOSED,
    # §21.6 item 2: digest_* without digest_send_to_email.
    "digest_send_to_email": SETTING_REFUSED,
    "multi_rule_execution": SETTING_CLOSED,
    "sensitive_data_protection": SETTING_CLOSED,
    # H-404 (owner, 2026-10-24). The brief is one mail a day to the member's
    # OWN account address, which the server picks. The model names no
    # address, so the act is an on or off switch, and the held card shows it.
    # With the switch off a held run does not hold this tool at all.
    "morning_brief_enabled": SETTING_CLOSED,
    # H-404: the extra domains decide who counts as the organization, in
    # the reading of each later mail. An injection that adds its own domain
    # makes its mail read as internal. So a held chat never changes it.
    "org_domains": SETTING_REFUSED,
    # §21.15 (owner, 2026-10-24): the always-copy rules decide who goes on
    # each later reply and new-mail card. An injection that changes them
    # changes future recipients, so a held chat never changes them.
    "always_copy": SETTING_REFUSED,
}

#: The values that each closed key with a fixed set takes.
_CLOSED_CHOICES: dict[str, frozenset[str]] = {
    "cold_email_blocker": frozenset({"OFF", "LABEL", "ARCHIVE"}),
    "draft_confidence": frozenset({"ALL_EMAILS", "STANDARD", "HIGH_CONFIDENCE"}),
    "digest_frequency": frozenset({"OFF", "DAILY", "WEEKLY"}),
}
#: The range of each closed key that is a number.
_CLOSED_RANGES: dict[str, tuple[int, int]] = {
    "follow_up_awaiting_days": (0, 365),
    "follow_up_needs_reply_days": (0, 365),
    "digest_day_of_week": (0, 6),
}
_CLOSED_BOOLS = frozenset({"auto_run", "draft_replies", "follow_up_auto_draft",
                           "multi_rule_execution", "sensitive_data_protection",
                           "morning_brief_enabled"})


def closed_setting_value(key: str, value: Any) -> Any:
    """The value of one closed key in its stored form. Raises ``ValueError``
    with the words that the member reads for a value outside the closed set."""
    if key in _CLOSED_BOOLS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} is on or off")
        return value
    if key in _CLOSED_CHOICES:
        v = str(value or "").strip().upper()
        if v not in _CLOSED_CHOICES[key]:
            raise ValueError(f"{key} is one of {', '.join(sorted(_CLOSED_CHOICES[key]))}")
        return v
    if key in _CLOSED_RANGES:
        lo, hi = _CLOSED_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"{key} is a whole number from {lo} to {hi}")
        return value
    if key == "digest_time_of_day":
        import re

        v = str(value or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError(f"{key} is a time as HH:MM, from 00:00 to 23:59")
        return v
    raise ValueError(f"{key} is not a setting that this chat can change")
