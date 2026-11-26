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
   the standard library must find the same one address, and the header must
   hold exactly one ``@`` outside its quoted strings and comments. Otherwise
   :func:`parse_sender` answers an empty address, never a guess. A mail with
   no ``From`` header gave an empty address before this module too, so each
   reader of the stored sender already meets that value.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from email import policy as email_policy
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any

from .providers.base import EmailAddress

#: A ``From`` header longer than this gives no sender. A real sender header is
#: short, and the policy parser of the standard library runs in quadratic time
#: on some text, such as a long run of backslashes.
SENDER_MAX_CHARS = 2_000
#: An address list header longer than this gives no address. The list parse
#: runs in linear time, and this cap holds some thousands of recipients.
LIST_MAX_CHARS = 200_000

#: One address that a recipient list or a sender may hold. The local part is
#: an atom text or a quoted string. The domain is a name (IDN too) or a
#: domain literal. Whitespace, an angle bracket or a second ``@`` fails it.
_ADDRESS = re.compile(
    r'(?:"[^"\\\r\n]*"|[^\s<>()\[\]\\,;:"@]+)'
    r"@"
    r"(?:\[[^\[\]\\\s]*\]|[^\s<>()\[\]\\,;:\"@]+)\Z"
)

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
    """The RAW value of the first header *name*, or ``""``."""
    values = raw_header_values(msg, name)
    return values[0] if values else ""


def decode_display_name(name: str) -> str:
    """Decode the RFC 2047 encoded words of one display name.

    A name that does not decode stays as it came.
    """
    name = name.strip()
    if "=?" not in name:
        return name
    try:
        return str(make_header(decode_header(name))).strip()
    except (LookupError, ValueError, HeaderParseError):
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


def parse_address_list(raw: Any) -> list[EmailAddress]:
    """Each address of one RAW address header (``To``, ``Cc``, ``Bcc``).

    A quoted comma stays inside its name. A bare comma, as in
    ``Doe, John <j@x.com>``, splits off the fragment ``Doe``. A fragment has
    no address, so it never becomes a recipient. The address of an entry is
    the address of the parse, never text of the display name.
    """
    text = header_text(raw)
    if not text.strip() or len(text) > LIST_MAX_CHARS:
        return []
    out: list[EmailAddress] = []
    for name, addr in _pairs(text) or []:
        addr = addr.strip()
        if is_address(addr):
            out.append(EmailAddress(name=decode_display_name(name), email=addr))
    return out


def _top_level_ats(text: str) -> int | None:
    """The count of ``@`` outside quoted strings and comments, or None.

    None means that a quoted string or a comment does not end. A backslash
    inside a quoted string or a comment escapes the next character.
    """
    ats = 0
    in_quote = False
    depth = 0
    escaped = False
    for ch in text:
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
        elif ch == "@":
            ats += 1
    if in_quote or depth:
        return None
    return ats


def _policy_addresses(text: str) -> list[str] | None:
    """The addresses that the policy parser of the standard library finds."""
    try:
        header = email_policy.default.header_factory("From", text)
        return [a.addr_spec for a in header.addresses if "@" in a.addr_spec]
    except Exception:  # a header that does not parse finds none
        return None


def _sender_of_one(text: str) -> EmailAddress:
    """The sender of ONE raw ``From`` value, or :data:`NO_SENDER`."""
    if len(text) > SENDER_MAX_CHARS or _top_level_ats(text) != 1:
        return NO_SENDER
    pairs = _pairs(text)
    if pairs is None:
        return NO_SENDER
    real = [i for i, (_n, a) in enumerate(pairs) if "@" in a]
    if len(real) != 1:
        return NO_SENDER
    index = real[0]
    name, addr = pairs[index]
    addr = addr.strip()
    if not is_address(addr):
        return NO_SENDER
    policy = _policy_addresses(text)
    if policy is None or len(policy) != 1 or policy[0].lower() != addr.lower():
        return NO_SENDER
    # ``Doe, John <j@x.com>`` gives the fragment ``Doe`` before the address.
    # The fragments are part of the name, as a mail client shows it.
    parts = [decode_display_name(" ".join(x for x in pair if x.strip()))
             for pair in pairs[:index]]
    parts.append(decode_display_name(name))
    return EmailAddress(name=", ".join(p for p in parts if p), email=addr)


def parse_sender(raw: Any) -> EmailAddress | None:
    """The sender of a message: the one address of its ``From``.

    *raw* is one RAW ``From`` value, or the list of each ``From`` value of the
    message. No value, or only empty ones, gives None.

    The answer is :data:`NO_SENDER` (an empty address) when the header is
    malformed or ambiguous:

    - more than one address, a group of more than one, or no address;
    - a quoted string or a comment that does not end;
    - a second ``@`` outside quotes and comments, such as
      ``ceo@company.test <mallory@evil.test>``;
    - two ``From`` headers with different addresses;
    - a header that the two parsers of the standard library read differently.
    """
    if raw is None:
        return None
    values: Iterable[Any] = [raw] if isinstance(raw, str) else raw
    texts = [t for t in (header_text(v) for v in values) if t.strip()]
    if not texts:
        return None
    senders = [_sender_of_one(t) for t in texts]
    first = senders[0]
    if not first.email or any(s.email.lower() != first.email.lower()
                              for s in senders[1:]):
        return NO_SENDER
    return first
