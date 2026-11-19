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

import functools
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Underflow,
)
from typing import Any
from urllib.parse import quote_plus

from gateway.connections.api.template import Template, Total
from gateway.crm_sources.base import UntrustedHost

#: The text that takes the place of a credential value.
REMOVED = "[removed]"

#: The daily budget of a connection whose template names no daily cap
#: (§2.3a, decision c): half of a careful guess of 1000 calls a day.
DEFAULT_DAILY_BUDGET = 500

#: The shortest credential value that the remover takes. The gateway refuses
#: a row with a shorter one, so a value of one letter never turns each such
#: letter of an answer into :data:`REMOVED` (§2.3a).
MIN_SECRET = 8

#: The length of the start and of the end of a credential that the remover
#: also removes, so a partial echo of a credential goes too.
SECRET_PART = 16

#: The largest exponent of a number that the total adds, either way. A
#: larger one counts as ``not_numbers`` (§2.3a, decision d).
MAX_EXPONENT = 40

#: The longest figure of a total. A longer one is refused as ``not_exact``.
MAX_FIGURE = 100

#: The arithmetic of the total. Each fault raises, so a sum never rounds or
#: overflows in silence.
SUM_CONTEXT = Context(prec=80, Emax=200, Emin=-200,
                      traps=[Inexact, Overflow, Underflow, InvalidOperation, DivisionByZero])

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
    """True for a token of printable ASCII with no space, of at least
    :data:`MIN_SECRET` characters. A token with a CR, an LF or another control
    character never goes into a header."""
    return (isinstance(token, str) and len(token) >= MIN_SECRET
            and all(0x21 <= ord(ch) <= 0x7E for ch in token))


# ── Step 9: the credential remover ──────────────────────────────────────────


def _hidden_format(ch: str) -> bool:
    """True for a format character: category Cf (ZWJ, ZWNJ, the soft hyphen
    and the other zero-width marks) or a variation selector."""
    return (unicodedata.category(ch) == "Cf" or 0xFE00 <= ord(ch) <= 0xFE0F
            or 0xE0100 <= ord(ch) <= 0xE01EF)


def secret_forms(values: Iterable[str | None], scheme: str = "") -> tuple[str, ...]:
    """Each form of each credential value that a text may hold.

    The literal value, its ``quote_plus`` form (``+`` for a space), its
    JSON-escaped form, the value after the sign-in scheme, and its first and
    last :data:`SECRET_PART` characters, for a partial echo. :func:`scrub`
    matches each character of a form as itself or percent-encoded, in upper
    or lower case hex. So a value with every byte encoded, or only some,
    goes too. A value shorter than :data:`MIN_SECRET` is left out. The
    longest form comes first.
    """
    forms: set[str] = set()
    for value in values:
        if not value or len(value) < MIN_SECRET:
            continue
        forms.update((value, quote_plus(value), json.dumps(value)[1:-1]))
        if len(value) > SECRET_PART:
            forms.update((value[:SECRET_PART], value[-SECRET_PART:]))
        if scheme:
            forms.add(f"{scheme} {value}")
    return tuple(sorted((f for f in forms if f), key=lambda f: (-len(f), f)))


@functools.lru_cache(maxsize=256)
def _form_pattern(form: str) -> re.Pattern[str]:
    """*form* with each character as itself or as its percent-encoded bytes.
    A space also matches ``+``."""
    parts: list[str] = []
    for ch in form:
        encoded = "".join(f"%{b:02x}" for b in ch.encode("utf-8"))
        options = [re.escape(ch), re.escape(encoded)]
        if ch == " ":
            options.append(r"\+")
        parts.append("(?:" + "|".join(options) + ")")
    return re.compile("".join(parts), re.IGNORECASE)


def scrub(text: str, forms: Iterable[str]) -> str:
    """*text* with each form of :func:`secret_forms` replaced by
    :data:`REMOVED`.

    The match runs on a copy with each format character removed, so a
    zero-width joiner inside a token does not hide it. Each character of a
    form matches as itself or percent-encoded. The matched span goes from the
    ORIGINAL text, with the format characters inside it. Spans that overlap
    or touch become one :data:`REMOVED`.
    """
    shapes = tuple(forms)
    if not shapes or not text:
        return text
    keep = [i for i, ch in enumerate(text) if not _hidden_format(ch)]
    folded = "".join(text[i] for i in keep)
    spans: list[tuple[int, int]] = []
    for form in shapes:
        for found in _form_pattern(form).finditer(folded):
            if found.end() > found.start():
                spans.append((keep[found.start()], keep[found.end() - 1] + 1))
    if not spans:
        return text
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out: list[str] = []
    last = 0
    for start, end in merged:
        out.extend((text[last:start], REMOVED))
        last = end
    out.append(text[last:])
    return "".join(out)


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

    def _dropped(key: Any) -> bool:
        # The key as the API sent it, and the key with no hidden character.
        return key in drop or strip(str(key)) in drop

    def _walk(node: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise AnswerRefused("too_deep")
        if isinstance(node, str):
            return scrub(strip(node), shapes)
        if isinstance(node, list):
            return [_walk(item, depth + 1) for item in node]
        if isinstance(node, dict):
            return {scrub(strip(str(k)), shapes): _walk(v, depth + 1)
                    for k, v in node.items() if not _dropped(k)}
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
    is :data:`PLAIN_NUMBER`. A bool, a list, an object, ``None``, each other
    text, a value that is not finite, and a value whose exponent is past
    :data:`MAX_EXPONENT` are not numbers. It never raises.
    """
    number = _decimal(value)
    if number is None or not number.is_finite():
        return None
    if number and abs(number.adjusted()) > MAX_EXPONENT:
        return None
    return number


def _decimal(value: Any) -> Decimal | None:
    """*value* as a :class:`Decimal` by type, before the checks of
    :func:`plain_number`."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))  # NaN and inf: plain_number refuses them
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
        list under the mark's key, and ``not_exact`` when the sum cannot
        stay exact in :data:`SUM_CONTEXT`.
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
                try:
                    self.total = SUM_CONTEXT.add(self.total, number)
                except ArithmeticError:
                    raise AnswerRefused("not_exact") from None
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
        ``total``: its sum so far is ``partial_total``. Raises
        :class:`AnswerRefused` (``not_exact``) for a figure past
        :data:`MAX_FIGURE` characters."""
        figure = format(self.total, "f")
        if len(figure) > MAX_FIGURE:
            raise AnswerRefused("not_exact")
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
    "MAX_EXPONENT",
    "MAX_FIGURE",
    "MIN_SECRET",
    "PLAIN_NUMBER",
    "REMOVED",
    "SECRET_PART",
    "SUM_CONTEXT",
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
