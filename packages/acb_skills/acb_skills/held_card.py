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
    "ADDRESS_MAX",
    "ATTACHMENT_REF",
    "CLASSES",
    "CLASS_NEW",
    "CLASS_ORG",
    "CLASS_SENT",
    "CLASS_THREAD",
    "DEFAULT_TICKED",
    "HELD_TEXT_MAX",
    "KIND_BCC",
    "KIND_CC",
    "KIND_FILE",
    "KIND_TO",
    "METORITE_FILE_REF",
    "METORITE_FILE_REFUSED",
    "NEW_MARK",
    "RECIPIENT_KINDS",
    "ROW_KINDS",
    "THREAD_MARK",
    "WORKSPACE_FILE_REFUSED",
    "address_problem",
    "class_mark",
    "domain_of",
    "file_label",
    "held_card_send_enabled",
    "lookalike_of",
    "lookalike_warning",
    "parse_file_ref",
    "recipient_label",
    "strongest_class",
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
#: A person that the reply derivation gave for this thread: the sender of a
#: mail of the thread. Open question 1 took the safer choice, so only that
#: sender counts. Each other person on the thread is new, unless known.
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
# The model names an id. The server resolves it. In this build one source
# can be a row: a file of a mail of the member's own mailbox, named as
# ``attachment:<id>``. A workspace path is refused, because the run itself
# wrote the file, from data that it read. A Metorite file (``file:<id>``) is
# refused too, until the spec names the store that owns such a file.

#: The head of a reference to a file of a mail of the member's own mailbox.
ATTACHMENT_REF = "attachment:"
#: The head of a reference to a Metorite file.
METORITE_FILE_REF = "file:"
#: The refusal of a workspace path in a held chat.
WORKSPACE_FILE_REFUSED = (
    "A chat that read outside mail cannot attach a file of the workspace, so "
    "nothing was sent. Attach a file of the member's own mailbox, named as "
    "attachment:<id> from read_email.")
#: The refusal of a Metorite file in a held chat (a decision that §21 does
#: not record yet: which store owns such a file).
METORITE_FILE_REFUSED = (
    "A chat that read outside mail cannot attach a Metorite file yet, so "
    "nothing was sent. Attach a file of the member's own mailbox, named as "
    "attachment:<id> from read_email.")


def parse_file_ref(ref: Any) -> tuple[str, str]:
    """``(source, id)`` of one file reference of a held send.

    ``("attachment", <uuid>)`` for ``attachment:<uuid>``. Anything else
    raises ``ValueError`` with the refusal that the member reads: a
    ``file:<id>`` with :data:`METORITE_FILE_REFUSED`, and a workspace path or
    any other text with :data:`WORKSPACE_FILE_REFUSED`."""
    import uuid

    text = str(ref or "").strip()
    if text.lower().startswith(ATTACHMENT_REF):
        raw = text[len(ATTACHMENT_REF):].strip()
        try:
            return "attachment", str(uuid.UUID(raw))
        except ValueError:
            raise ValueError(WORKSPACE_FILE_REFUSED) from None
    if text.lower().startswith(METORITE_FILE_REF):
        raise ValueError(METORITE_FILE_REFUSED)
    raise ValueError(WORKSPACE_FILE_REFUSED)


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

