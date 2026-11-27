"""The one parse of a mail address header (task 4016).

Every provider that reads a raw address header calls this module: the IMAP
provider, the inbound SMTP receiver and the Gmail provider. Outlook gets its
addresses from Graph as fields, so it does not parse a header.

Three rules make the parse safe against a planted sender:

1. The parse splits the RAW header. It decodes the RFC 2047 encoded words of
   a display name only AFTER the split. A decode before the split lets an
   encoded name such as ``=?utf-8?q?=3Cceo=40company.test=3E?=`` bring back
   an angle pair, a comma or a quote.
2. The address of an entry is the address that the standard library finds,
   never the text inside the first ``<...>`` pair. The display name can hold
   any text, ``"<ceo@company.test>" <mallory@evil.test>`` too.
3. A sender that is malformed or ambiguous gives NO address. Two parsers of
   the standard library must find the same one address. Each address outside
   the quoted strings and comments of the header must be that address too.
   Otherwise :func:`parse_sender` answers an empty address, never a guess. A
   mail with no ``From`` header gave an empty address before this module too,
   so each reader of the stored sender already meets that value.

The parse never raises. A header that it cannot read gives no address, so one
bad mail never stops the sync of the mails after it.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from email import policy as email_policy
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any

from .providers.base import EmailAddress

logger = logging.getLogger(__name__)

#: The ``From`` values of one mail, together, longer than this give no
#: sender. A real sender header is short, and the policy parser of the
#: standard library runs in quadratic time on some text, such as a long run
#: of backslashes. The cap is on the total, because each value is parsed.
SENDER_MAX_CHARS = 2_000
#: A mail with more ``From`` values than this has no sender. A real mail has
#: one. The cap bounds the parse work of one mail on the event loop.
SENDER_MAX_VALUES = 8
#: An address list header longer than this gives no address. The list parse
#: runs in linear time, and this cap holds some thousands of recipients.
LIST_MAX_CHARS = 200_000

#: One address that a recipient list or a sender may hold. The local part is
#: an atom text or a quoted string. The domain is a name (IDN too) or a
#: domain literal. Whitespace, a control character (NUL too), an angle
#: bracket or a second ``@`` fails it.
_ADDRESS = re.compile(
    r'(?:"[^"\\\r\n\x00-\x1f\x7f]*"|[^\s\x00-\x1f\x7f<>()\[\]\\,;:"@]+)'
    r"@"
    r"(?:\[[^\[\]\\\s\x00-\x1f\x7f]*\]|[^\s\x00-\x1f\x7f<>()\[\]\\,;:\"@]+)\Z"
)

#: One address in the text outside the quoted strings and comments.
_BARE_ADDRESS = re.compile(
    r"[^\s<>()\[\],;:@]+@(?:\[[^\[\]\s]*\]|[^\s<>()\[\],;:@]+)")

#: A fold of a header line: a line break before a space or a tab.
_FOLD = re.compile(r"\r?\n(?=[ \t])")

#: The answer for a sender that is malformed or ambiguous.
NO_SENDER = EmailAddress(name="", email="")


def header_text(value: Any) -> str:
    """One raw header value as one line of text.

    A fold goes, and each other line break becomes a space. The parser of
    ``message_from_bytes`` keeps raw 8-bit bytes as escaped surrogates, and
    this function reads those bytes as UTF-8.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if any("\udc80" <= ch <= "\udcff" for ch in text):
        try:
            text = text.encode("utf-8", "surrogateescape").decode(
                "utf-8", "replace")
        except UnicodeError:
            text = text.encode("utf-8", "replace").decode("utf-8", "replace")
    text = _FOLD.sub("", text)
    return text.replace("\r", " ").replace("\n", " ")


def raw_header_values(msg: Any, name: str) -> list[str]:
    """The RAW value of each header *name* of a parsed message, in order.

    ``str(msg.get(name))`` gives the value of the policy, with each encoded
    word decoded. That value is not raw, so the parse here never uses it.
    ``raw_items`` gives the header as it came.
    """
    want = name.lower()
    return [header_text(value) for key, value in msg.raw_items()
            if str(key).lower() == want]


def first_raw_header(msg: Any, name: str) -> str:
    """The RAW value of the first header *name*, or ``""``.

    The read stops at the first header *name*. A mail with many of them costs
    the work of one.
    """
    want = name.lower()
    for key, value in msg.raw_items():
        if str(key).lower() == want:
            return header_text(value)
    return ""


def _clean(text: str) -> str:
    """*text* with no NUL. A JSONB value cannot hold U+0000."""
    return text.replace("\x00", "")


def decode_display_name(name: str) -> str:
    """Decode the RFC 2047 encoded words of one display name.

    A name that does not decode stays as it came. The answer holds no NUL.
    This function never raises. A charset name that is not ASCII, as in
    ``=?\u00e9?q?x?=``, raises ``CharsetError`` in the standard library.
    """
    name = _clean(name).strip()
    if "=?" not in name:
        return name
    try:
        return _clean(str(make_header(decode_header(name)))).strip()
    except Exception:  # a name that does not decode stays as it came
        return name


def _pairs(text: str) -> list[tuple[str, str]] | None:
    """The lenient ``getaddresses`` pairs of one raw header, or None.

    ``strict=False`` keeps each real address of a loose list, such as
    ``a@x.org, b@x.org,``. Since the fix of CVE-2023-27043 the strict parser
    refuses the whole header for one defect. A Python from before that fix
    has no ``strict`` keyword and parses leniently already. The checks of
    :func:`parse_sender` do not depend on the strict mode, so the result is
    the same on each patch version. A deep nest of comments raises
    ``RecursionError``, and then the header gives no pair.
    """
    try:
        try:
            return getaddresses([text], strict=False)
        except TypeError:  # a Python from before the CVE-2023-27043 fix
            return getaddresses([text])
    except Exception:  # a header that does not parse gives no pair
        return None


def is_address(value: str) -> bool:
    """True when *value* is one plain address, with no name and no list."""
    return bool(_ADDRESS.match(value or ""))


def _refused(what: str, exc: Exception) -> None:
    """Log a header that did not parse. The log never holds the header."""
    logger.warning("email_ingestion: a %s header did not parse (%s), so it "
                   "gives no address", what, type(exc).__name__)


def parse_address_list(raw: Any) -> list[EmailAddress]:
    """Each address of one RAW address header (``To``, ``Cc``, ``Bcc``).

    A quoted comma stays inside its name. A bare comma, as in
    ``Doe, John <j@x.com>``, splits off the fragment ``Doe``. A fragment has
    no address, so it never becomes a recipient. The address of an entry is
    the address of the parse, never text of the display name. This function
    never raises: a header that it cannot read gives no address.
    """
    try:
        text = header_text(raw)
        if not text.strip() or len(text) > LIST_MAX_CHARS:
            return []
        out: list[EmailAddress] = []
        for name, addr in _pairs(text) or []:
            addr = addr.strip()
            if is_address(addr):
                out.append(EmailAddress(name=decode_display_name(name),
                                        email=addr))
        return out
    except Exception as exc:  # one bad header never stops the sync
        _refused("address list", exc)
        return []


def _outside_quotes(text: str) -> str | None:
    """*text* with each quoted string and comment made spaces, or None.

    The answer has the length of *text*, so an index into it is an index into
    *text*. None means that a quoted string or a comment does not end. A
    backslash inside a quoted string or a comment escapes the next character.
    """
    out: list[str] = []
    in_quote = False
    depth = 0
    escaped = False
    for ch in text:
        keep = False
        if escaped:
            escaped = False
        elif (in_quote or depth) and ch == "\\":
            escaped = True
        elif in_quote:
            if ch == '"':
                in_quote = False
        elif ch == "(":
            depth += 1
        elif depth:
            if ch == ")":
                depth -= 1
        elif ch == '"':
            in_quote = True
        else:
            keep = True
        out.append(ch if keep else " ")
    if in_quote or depth:
        return None
    return "".join(out)


def _phrase_text(text: str) -> str:
    """The text of a display name phrase, as a mail client shows it.

    A quoted string loses its quotes and its escapes. A comment and each other
    word stay as they came, so ``Amazon Web Services, Inc.`` keeps each word.
    """
    out: list[str] = []
    in_quote = False
    depth = 0
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            out.append(ch)
        elif in_quote and ch == "\\":
            escaped = True
        elif in_quote:
            if ch == '"':
                in_quote = False
            else:
                out.append(ch)
        elif ch == "(":
            depth += 1
            out.append(ch)
        elif depth:
            if ch == ")":
                depth -= 1
            elif ch == "\\":
                escaped = True
                continue
            out.append(ch)
        elif ch == '"':
            in_quote = True
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def _policy_addresses(text: str) -> list[str] | None:
    """The addresses that the policy parser of the standard library finds."""
    try:
        header = email_policy.default.header_factory("From", text)
        return [a.addr_spec for a in header.addresses if "@" in a.addr_spec]
    except Exception:  # a header that does not parse finds none
        return None


def _sender_name(text: str, top: str, pairs: list[tuple[str, str]],
                 index: int) -> str:
    """The display name of the one sender of *text*, decoded.

    With an angle address, the name is the raw text before the last ``<``
    outside quotes and comments, after a group label. The name decodes only
    after this split. With a bare address, as in ``a@x.org (Asha)``, the name
    is the comment that the parse found.
    """
    angle = top.rfind("<")
    if angle >= 0:
        start = top.rfind(":", 0, angle) + 1
        return decode_display_name(_phrase_text(text[start:angle]))
    # ``Doe, john@x.com`` gives the fragment ``Doe`` before the address.
    # The fragments are part of the name, as a mail client shows it.
    parts = [decode_display_name(" ".join(x for x in pair if x.strip()))
             for pair in pairs[:index]]
    parts.append(decode_display_name(pairs[index][0]))
    return ", ".join(p for p in parts if p)


def _sender_of_one(text: str) -> EmailAddress:
    """The sender of ONE raw ``From`` value, or :data:`NO_SENDER`."""
    top = _outside_quotes(text)
    if top is None:
        return NO_SENDER
    ats = top.count("@")
    if ats == 0:
        return NO_SENDER
    pairs = _pairs(text)
    if pairs is None:
        return NO_SENDER
    real = [(i, a.strip()) for i, (_n, a) in enumerate(pairs) if "@" in a]
    if not real:
        return NO_SENDER
    # Each entry with an ``@`` must be the same address. The last one is the
    # angle address of ``john@x.com <john@x.com>``, so its case is kept.
    index, addr = real[-1]
    want = addr.lower()
    if not is_address(addr) or any(a.lower() != want for _i, a in real):
        return NO_SENDER
    if ats > 1:
        # Each address outside the quotes and comments must be the address
        # of the parse. ``john@x.com <john@x.com>`` passes, and
        # ``ceo@company.test <mallory@evil.test>`` does not.
        bare = _BARE_ADDRESS.findall(top)
        if len(bare) != ats or any(b.lower() != want for b in bare):
            return NO_SENDER
    policy = _policy_addresses(text)
    if not policy or any(p.lower() != want for p in policy):
        return NO_SENDER
    return EmailAddress(name=_sender_name(text, top, pairs, index), email=addr)


def _sender(raw: Any) -> EmailAddress | None:
    """The body of :func:`parse_sender`, which may raise."""
    values: Iterable[Any] = [raw] if isinstance(raw, str) else raw
    texts: list[str] = []
    total = 0
    for value in values:
        text = header_text(value)
        if not text.strip():
            continue
        texts.append(text)
        total += len(text)
        if len(texts) > SENDER_MAX_VALUES or total > SENDER_MAX_CHARS:
            return NO_SENDER
    if not texts:
        return None
    senders = []
    for text in texts:
        sender = _sender_of_one(text)
        if not sender.email:
            return NO_SENDER
        senders.append(sender)
    first = senders[0]
    if any(s.email.lower() != first.email.lower() for s in senders[1:]):
        return NO_SENDER
    return first


def parse_sender(raw: Any) -> EmailAddress | None:
    """The sender of a message: the one address of its ``From``.

    *raw* is one RAW ``From`` value, or the list of each ``From`` value of the
    message. No value, or only empty ones, gives None.

    The answer is :data:`NO_SENDER` (an empty address) when the header is
    malformed or ambiguous:

    - two different addresses, or no address;
    - a quoted string or a comment that does not end;
    - an address outside quotes and comments that is not the sender, such as
      ``ceo@company.test`` of ``ceo@company.test <mallory@evil.test>``;
    - two ``From`` headers with different addresses;
    - more than :data:`SENDER_MAX_VALUES` values, or more than
      :data:`SENDER_MAX_CHARS` characters in all;
    - a header that the two parsers of the standard library read differently.

    This function never raises. A header that it cannot read gives
    :data:`NO_SENDER`, and a log line names the kind of error.
    """
    if raw is None:
        return None
    try:
        return _sender(raw)
    except Exception as exc:  # one bad header never stops the sync
        _refused("From", exc)
        return NO_SENDER
