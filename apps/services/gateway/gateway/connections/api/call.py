"""The pure steps of one API call (API-3, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.3 and §2.3a. The routes
are in ``gateway/routes/api_connections.py``. They do the I/O, and they call
these helpers in the order of §2.3.

* :func:`check_host` is step 7: the API host of the row must be a host of the
  template, and of the client's own list when the template names a client.
* :func:`secret_forms` and :func:`scrub` are step 9: each literal credential
  value goes, also in its URL-encoded, JSON-escaped and scheme forms.
* :func:`clean` is step 8 and step 9 on a parsed answer: hidden characters
  go, each secret field goes, and each text passes :func:`scrub`. The caller
  passes the strip of hidden characters (``acb_skills.hidden_text
  .strip_hidden``), because this package imports no ``acb_skills``.
* :func:`plain_number` is the strict number rule of the gateway total
  (§2.3a, decision d). :class:`Adder` adds the pages.
* :func:`daily_budget` is the budget of §2.3a, decision c.

This module does no I/O. It reads no setting, no environment and no
credential store, and it imports no ``acb_skills`` (the purity fence of
``tests/unit/test_api_templates.py``). Fences:
``tests/unit/test_api_call_egress.py``, ``tests/unit/test_api_call_limits.py``
and ``tests/unit/test_api_no_env_credentials.py``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, quote_plus

from gateway.connections.api.template import Template, Total
from gateway.crm_sources.base import UntrustedHost

#: The text that takes the place of a credential value.
REMOVED = "[removed]"

#: The daily budget of a connection whose template names no daily cap
#: (§2.3a, decision c).
DEFAULT_DAILY_BUDGET = 1000

#: The deepest nesting of an answer that :func:`clean` walks. A deeper answer
#: is refused, so a hostile answer cannot exhaust the stack.
MAX_DEPTH = 32

#: The longest error message of the API that a refusal passes on.
MAX_MESSAGE = 200

#: The strict shape of a number in text: ASCII digits, an optional minus and
#: an optional decimal part. No leading zero, no comma, no exponent, no space.
PLAIN_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,30})?")

#: The shape of a currency code that a total names.
CURRENCY_CODE = re.compile(r"[A-Z]{3}")

#: The strip of hidden characters that the caller gives.
Strip = Callable[[str], str]

#: The shape of a context value that the gateway sets on a call.
CONTEXT_VALUE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class AnswerRefused(ValueError):
    """An answer that the gateway cannot pass on. ``code`` names the reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── Step 7: the host ────────────────────────────────────────────────────────


def check_host(template: Template, api_domain: object,
               client_hosts: Mapping[str, frozenset[str]]) -> str:
    """*api_domain* when it is a host of *template* (and of its client).

    Raises :class:`UntrustedHost`. A template that names a client takes its
    hosts from that client's list, so a token of a Metorite client goes only
    to that client's hosts (§2.1, §2.7).
    """
    if not isinstance(api_domain, str) or not api_domain:
        raise UntrustedHost("The connection names no API host")
    if api_domain not in {origin for _, origin in template.hosts}:
        raise UntrustedHost("The API host is not a host of the template")
    client = template.sign_in.client
    if client and api_domain not in client_hosts.get(client, frozenset()):
        raise UntrustedHost("The API host is not a host of the client")
    return api_domain


def context_values(template: Template, provider_meta: Mapping[str, Any]) -> dict[str, str]:
    """The context value of each context key of *template*, from the row.

    Raises :class:`AnswerRefused` (``connection_incomplete``) for a key with
    no value, or a value that is not :data:`CONTEXT_VALUE`.
    """
    out: dict[str, str] = {}
    for key in template.context:
        value = provider_meta.get(key)
        if not isinstance(value, str) or not CONTEXT_VALUE.fullmatch(value):
            raise AnswerRefused("connection_incomplete")
        out[key] = value
    return out


def token_is_plain(token: object) -> bool:
    """True for a token of printable ASCII with no space. A token with a CR,
    an LF or another control character never goes into a header."""
    return (isinstance(token, str) and bool(token)
            and all(0x21 <= ord(ch) <= 0x7E for ch in token))


# ── Step 9: the credential remover ──────────────────────────────────────────


def secret_forms(values: Iterable[str | None], scheme: str = "") -> tuple[str, ...]:
    """Each form of each credential value that a text may hold.

    The literal value, its URL-encoded forms (upper and lower case hex, and
    ``+`` for a space), its JSON-escaped form, and the value after the
    sign-in scheme. The longest form comes first, so a short form never
    breaks a long one before the long one goes.
    """
    forms: set[str] = set()
    for value in values:
        if not value:
            continue
        encoded = quote(value, safe="")
        for form in (value, encoded, encoded.lower(), quote_plus(value),
                     json.dumps(value)[1:-1]):
            forms.add(form)
        if scheme:
            forms.add(f"{scheme} {value}")
    return tuple(sorted((f for f in forms if f), key=lambda f: (-len(f), f)))


def scrub(text: str, forms: Iterable[str]) -> str:
    """*text* with each form of :func:`secret_forms` replaced by
    :data:`REMOVED`."""
    for form in forms:
        if form in text:
            text = text.replace(form, REMOVED)
    return text


def clean_text(text: object, forms: Iterable[str], strip: Strip,
               cap: int = MAX_MESSAGE) -> str:
    """Third-party text for an error: no hidden character, no credential, at
    most *cap* characters."""
    plain = strip(str(text or ""))
    return scrub(plain, tuple(forms))[:cap]


# ── Step 8: parse, strip, cap ───────────────────────────────────────────────


def parse(body: bytes, *, exact: bool = False) -> Any:
    """The JSON of *body*, or :class:`AnswerRefused` (``not_json``).

    ``NaN`` and ``Infinity`` are refused. With *exact*, each number with a
    decimal point is a :class:`Decimal`, so the total adds it with no float
    error.
    """
    def _no_constant(name: str) -> Any:
        raise ValueError(name)

    try:
        return json.loads(body, parse_constant=_no_constant,
                          parse_float=Decimal if exact else float)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise AnswerRefused("not_json") from None


def clean(value: Any, *, secret_fields: Iterable[str], forms: Iterable[str],
          strip: Strip) -> Any:
    """*value* with each hidden character and each credential form removed
    from each text and key, and each key of *secret_fields* removed.

    Raises :class:`AnswerRefused` (``too_deep``) past :data:`MAX_DEPTH`.
    """
    drop = frozenset(secret_fields)
    shapes = tuple(forms)

    def _walk(node: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise AnswerRefused("too_deep")
        if isinstance(node, str):
            return scrub(strip(node), shapes)
        if isinstance(node, list):
            return [_walk(item, depth + 1) for item in node]
        if isinstance(node, dict):
            return {scrub(strip(str(k)), shapes): _walk(v, depth + 1)
                    for k, v in node.items() if k not in drop}
        return node

    return _walk(value, 0)


def size_of(value: Any) -> int:
    """The bytes of *value* as compact JSON, as the chat gets it."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode("utf-8"))


def error_message(body: bytes, forms: Iterable[str], strip: Strip) -> str:
    """The ``message`` of an error answer of the API, cleaned, or empty."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("message"), str):
        return ""
    return clean_text(data["message"], forms, strip)


# ── The gateway total ───────────────────────────────────────────────────────


def plain_number(value: Any) -> Decimal | None:
    """*value* as a :class:`Decimal` when it is a plain number, else None.

    A JSON number is a number. A text is a number only when its whole shape
    is :data:`PLAIN_NUMBER`. A bool, a list, an object, ``None`` and each other
    text are not numbers.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, float):
        return Decimal(repr(value)) if value == value and abs(value) != float("inf") else None
    if isinstance(value, str) and PLAIN_NUMBER.fullmatch(value):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


@dataclass
class Adder:
    """The sum of the pages of one total (§2.3a, decision d)."""

    mark: Total
    total: Decimal = Decimal(0)
    records: int = 0
    numbers: int = 0
    not_numbers: int = 0
    pages_read: int = 0
    currencies: set[str] = field(default_factory=set)

    def add_page(self, data: Any) -> int:
        """Add the items of one page. Returns the count of its items.

        Raises :class:`AnswerRefused` (``not_a_list``) when the page holds no
        list under the mark's key.
        """
        items = data.get(self.mark.items) if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise AnswerRefused("not_a_list")
        self.pages_read += 1
        for item in items:
            self.records += 1
            row = item if isinstance(item, dict) else {}
            number = plain_number(row.get(self.mark.field))
            if number is None:
                self.not_numbers += 1
            else:
                self.numbers += 1
                self.total += number
            if self.mark.currency:
                code = row.get(self.mark.currency)
                self.currencies.add(code if isinstance(code, str)
                                    and CURRENCY_CODE.fullmatch(code) else "unknown")
        return len(items)

    def mixed(self) -> list[str] | None:
        """The sorted currencies when the pages hold more than one, else None."""
        return sorted(self.currencies) if len(self.currencies) > 1 else None

    def answer(self, *, incomplete: bool, stopped_by: str | None) -> dict[str, Any]:
        """The answer of the total. A total that a limit stopped has no
        ``total``: its sum so far is ``partial_total``."""
        figure = format(self.total, "f")
        currency = next(iter(self.currencies)) if len(self.currencies) == 1 else None
        return {
            "field": self.mark.field,
            "total": None if incomplete else figure,
            "partial_total": figure if incomplete else None,
            "currency": currency if currency != "unknown" else None,
            "pages_read": self.pages_read,
            "records": self.records,
            "numbers": self.numbers,
            "not_numbers": self.not_numbers,
            "incomplete": incomplete,
            "stopped_by": stopped_by,
        }


# ── The daily budget ────────────────────────────────────────────────────────


def daily_budget(template: Template) -> int:
    """Half of the API's daily cap, or :data:`DEFAULT_DAILY_BUDGET`."""
    per_day = template.limits.per_day
    if per_day is None:
        return DEFAULT_DAILY_BUDGET
    return max(1, per_day // 2)


def utc_day(now: datetime) -> tuple[str, int, int]:
    """The UTC day of *now*, the Unix time when its key ends, and the seconds
    to the next day.

    The key ends one hour after the day, so a clock that is a little behind
    never meets a key of the day before.
    """
    moment = now.astimezone(UTC)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    left = max(1, int((end - moment).total_seconds()))
    return start.strftime("%Y-%m-%d"), int(end.timestamp()) + 3600, left


__all__ = [
    "CONTEXT_VALUE",
    "DEFAULT_DAILY_BUDGET",
    "MAX_DEPTH",
    "PLAIN_NUMBER",
    "REMOVED",
    "Adder",
    "AnswerRefused",
    "Strip",
    "check_host",
    "clean",
    "clean_text",
    "context_values",
    "daily_budget",
    "error_message",
    "parse",
    "plain_number",
    "scrub",
    "secret_forms",
    "size_of",
    "token_is_plain",
    "utc_day",
]
