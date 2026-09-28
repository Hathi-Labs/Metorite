"""Is each vendor account we call on able to serve? Balance probes, refusal
counts, and the one status rule.

Owner request, 2026-09-28: *"whatever AI models we have connected, their low
balance alert should show up, or at least warn if we don't have access to know
how much is left."*

🔴 **Why this exists.** From 2026-09-26 to 2026-09-28 the DeepSeek account held
-0.05 USD. DeepSeek refused every call with HTTP 402, the Router mapped it to a
502 (``main._upstream_refusal``), and all AI on the platform failed for two days
with nobody told.

Two signals, because no single one covers every vendor:

  1. **The balance probe** (proactive). One small function per vendor that
     EXPOSES a balance, chosen by name from :data:`PROBES`. A vendor that is
     not in the registry is ``not_exposed``. We never scrape a dashboard.
  2. **The refusal count** (reactive). ``router.walk_chain`` reports every
     failed step through its ``on_refusal`` hook, and :func:`note_refusal`
     buffers it in memory. :func:`flush_refusals` writes the buffer to
     ``provider_refusal`` (migration 035). This is the ONLY signal for a vendor
     whose balance is invisible, and a 402 alerts even there.

⚠️ **The secret.** A probe receives the plaintext key, sends it in ONE header
to ONE fixed vendor host, and forgets it. No result field, no stored error and
no log line is built from anything the vendor or the HTTP library said. Every
error string comes from :data:`_ERRORS`, our own words.
``tests/unit/test_provider_balance.py`` echoes the key back from a fake vendor
and asserts it appears nowhere.

⚠️ **The fixed host.** A probe never uses the credential's ``api_base``. That
field can point at a proxy, and a proxy's key sent to the vendor would be a
leak to the vendor. So a credential with an ``api_base`` on another host is
``not_exposed``.

⚠️ **The decrypt.** This module decrypts nothing itself. It reads a key through
``router.provider_credential`` — the one Fernet seam — with ``org_id=None``,
which can only ever return the PLATFORM row.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx

_log = logging.getLogger("platform.router")

__all__ = [
    "PROBES",
    "Assessment",
    "HealthInput",
    "ProbeResult",
    "assess",
    "drain_refusals",
    "note_refusal",
    "probe",
]

# ── Configuration ───────────────────────────────────────────────────────────

#: A probe is a read of one small JSON document. Eight seconds is generous,
#: and a probe that hangs longer must not hold the loop.
PROBE_TIMEOUT_SECONDS = 8.0

#: The low line for a USD balance when a provider carries no override.
LOW_USD_VAR = "CUSTOMER_CONSOLE_PROVIDER_LOW_USD"
DEFAULT_LOW_USD = Decimal("5")

#: Fewer days of runway than this is LOW, whatever the balance.
LOW_DAYS_LEFT = Decimal("3")

#: How long a refusal keeps a provider in `refusing` or `out`.
REFUSING_MINUTES_VAR = "CUSTOMER_CONSOLE_PROVIDER_REFUSING_MINUTES"
DEFAULT_REFUSING_MINUTES = 60

#: The statuses that say OUR account is refusing, not that the request was bad.
#: 402 = payment. 401/403 = our key. 429 = our rate or our quota.
REFUSAL_STATUSES = frozenset({401, 402, 403, 429})

#: A healthy probe NEWER than one of these clears it. The probe used the same
#: key and the vendor answered it, so the key works and the account has money.
_SUPERSEDED_BY_PROBE = frozenset({401, 402})

#: 🔴 **ONE 402 or ONE 401 is decisive; ONE 403 or ONE 429 is not** (review
#: of PR #524). A 403 is often one moderated prompt, and a 429 one busy
#: minute that failover then served. Either one alone drew a red "AI is
#: failing" banner for an hour. So these two need a repeat, and a SERVED call
#: on the same vendor after the latest one clears them.
_NEEDS_REPEAT = frozenset({403, 429})
REPEAT_VAR = "CUSTOMER_CONSOLE_PROVIDER_REFUSING_MIN"
DEFAULT_REPEAT = 3

#: The statuses the rule can return, worst first.
#:
#: ⚠️ ``rate_limited`` is its own status, AMBER. A 429 says we are calling too
#: fast or past a quota. It does not say the account is empty, and a "top up"
#: banner for it sends somebody to pay for the wrong thing.
STATUSES = ("out", "refusing", "rate_limited", "low", "probe_failed", "unknown", "ok")

#: Why a status is what it is, for the console's headline. None = nothing wrong.
CAUSES = ("payment", "key", "rate_limit", "balance", "probe", "invisible")

#: Our own words for every failure. Nothing else may reach `probe_error`.
_ERRORS = {
    "timeout": "the vendor did not answer in time",
    "network": "the vendor could not be reached",
    "unreadable": "the vendor answered with a shape we do not read",
    "custom_base": "this credential points at a custom api_base, so we do not send its key to the vendor's own host",
    "not_in_registry": "this vendor exposes no balance we can read",
    "no_limit": "this key has no spending limit, and the account balance needs a management key",
    "no_credential": "no live platform credential",
    "decrypt": "the credential could not be decrypted",
}


def low_line_usd(env: Mapping[str, str] | None = None) -> Decimal:
    raw = (env if env is not None else os.environ).get(LOW_USD_VAR, "").strip()
    if not raw:
        return DEFAULT_LOW_USD
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return DEFAULT_LOW_USD
    return value if value >= 0 else DEFAULT_LOW_USD


def repeat_threshold(env: Mapping[str, str] | None = None) -> int:
    """How many 403s or 429s inside the window make an alarm. At least 1."""
    raw = (env if env is not None else os.environ).get(REPEAT_VAR, "").strip()
    try:
        n = int(raw) if raw else DEFAULT_REPEAT
    except ValueError:
        n = DEFAULT_REPEAT
    return n if n >= 1 else DEFAULT_REPEAT


def _refusing_window(env: Mapping[str, str] | None = None) -> timedelta:
    raw = (env if env is not None else os.environ).get(REFUSING_MINUTES_VAR, "").strip()
    try:
        minutes = int(raw) if raw else DEFAULT_REFUSING_MINUTES
    except ValueError:
        minutes = DEFAULT_REFUSING_MINUTES
    return timedelta(minutes=minutes if minutes > 0 else DEFAULT_REFUSING_MINUTES)


# ── The probes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeResult:
    """What one probe learned. **Never holds the key.**

    ``status`` is ``ok`` (a balance was read), ``not_exposed`` (the vendor has
    no endpoint we can read) or ``failed`` (it has one and the read failed).
    """

    status: str
    balance: Decimal | None = None
    currency: str | None = None
    available: bool | None = None
    error: str | None = None
    http_status: int | None = None
    #: Which endpoint answered, for the page. A path, never a URL with a query.
    source: str | None = None


def _failed(kind: str, http_status: int | None = None) -> ProbeResult:
    error = f"the vendor answered HTTP {http_status}" if http_status else _ERRORS[kind]
    return ProbeResult(status="failed", error=error, http_status=http_status)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def _get_json(client: httpx.Client, url: str, secret: str) -> tuple[int, Any]:
    """One GET with the key in the Authorization header. Returns (status, json).

    ⚠️ The JSON is None when the body is not JSON. The body text is never
    returned, because a vendor error body may quote the request.
    """
    res = client.get(url, headers={"Authorization": f"Bearer {secret}"})
    try:
        body = res.json()
    except ValueError:
        body = None
    return res.status_code, body


def parse_deepseek(body: Any) -> ProbeResult:
    """``GET https://api.deepseek.com/user/balance``.

    Shape (https://api-docs.deepseek.com/api/get-user-balance, read
    2026-09-28)::

        {"is_available": true,
         "balance_infos": [{"currency": "CNY"|"USD", "total_balance": "110.00",
                            "granted_balance": "10.00",
                            "topped_up_balance": "100.00"}]}

    ⚠️ An account can hold more than one currency. USD wins when present,
    because the low line and the days-left estimate are USD. Nothing is
    converted.
    """
    if not isinstance(body, dict):
        return _failed("unreadable")
    available = body.get("is_available")
    infos = body.get("balance_infos")
    if not isinstance(infos, list):
        return _failed("unreadable")
    rows = [i for i in infos if isinstance(i, dict)]
    pick = next((i for i in rows if str(i.get("currency", "")).upper() == "USD"), None)
    pick = pick or (rows[0] if rows else None)
    if pick is None:
        # The vendor answered and holds no balance line at all. With an
        # explicit `is_available: false` that is still an honest answer.
        if available is False:
            return ProbeResult(
                status="ok",
                balance=Decimal("0"),
                currency=None,
                available=False,
                source="/user/balance",
            )
        return _failed("unreadable")
    balance = _decimal(pick.get("total_balance"))
    if balance is None:
        return _failed("unreadable")
    return ProbeResult(
        status="ok",
        balance=balance,
        currency=str(pick.get("currency") or "").upper() or None,
        available=available if isinstance(available, bool) else None,
        source="/user/balance",
    )


def probe_deepseek(secret: str, client: httpx.Client) -> ProbeResult:
    status, body = _get_json(client, "https://api.deepseek.com/user/balance", secret)
    if status != 200:
        return _failed("unreadable", status)
    return parse_deepseek(body)


def parse_openrouter_credits(body: Any) -> ProbeResult:
    """``GET https://openrouter.ai/api/v1/credits`` — the ACCOUNT balance.

    Shape (https://openrouter.ai/docs/api-reference/get-credits, read
    2026-09-28)::

        {"data": {"total_credits": 100.5, "total_usage": 25.75}}

    Balance = total_credits - total_usage, in USD.
    """
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return _failed("unreadable")
    credits = _decimal(data.get("total_credits"))
    usage = _decimal(data.get("total_usage"))
    if credits is None or usage is None:
        return _failed("unreadable")
    return ProbeResult(
        status="ok", balance=credits - usage, currency="USD", source="/api/v1/credits"
    )


def parse_openrouter_key(body: Any) -> ProbeResult:
    """``GET https://openrouter.ai/api/v1/key`` — THIS KEY's remaining budget.

    Shape (https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key,
    read 2026-09-28)::

        {"data": {"limit": 100, "limit_remaining": 74.5, "usage": 25.5, ...,
                  "label": "sk-or-v1-au7...890"}}

    ⚠️ ``label`` holds a fragment of the key. It is never read.
    ⚠️ ``limit`` null means the key has no cap, so ``limit_remaining`` says
    nothing about the account. That is ``not_exposed``, not a balance of zero.
    """
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return _failed("unreadable")
    if data.get("limit") is None:
        return ProbeResult(status="not_exposed", error=_ERRORS["no_limit"])
    remaining = _decimal(data.get("limit_remaining"))
    if remaining is None:
        return _failed("unreadable")
    return ProbeResult(status="ok", balance=remaining, currency="USD", source="/api/v1/key")


def probe_openrouter(secret: str, client: httpx.Client) -> ProbeResult:
    """The account balance when the key may read it, else this key's budget.

    ⚠️ ``/api/v1/credits`` answers 403 to an ordinary inference key: it wants
    a management key. The ordinary key can still read its OWN limit.
    """
    status, body = _get_json(client, "https://openrouter.ai/api/v1/credits", secret)
    if status == 200:
        return parse_openrouter_credits(body)
    if status != 403:
        return _failed("unreadable", status)
    status, body = _get_json(client, "https://openrouter.ai/api/v1/key", secret)
    if status != 200:
        return _failed("unreadable", status)
    return parse_openrouter_key(body)


def parse_aimlapi(body: Any) -> ProbeResult:
    """``GET https://api.aimlapi.com/v2/billing``.

    Shape (https://docs.aimlapi.com/api-references/service-endpoints/account-balance,
    read 2026-09-28)::

        {"current_balance": 12.5, "currency": "USD"}

    ⚠️ The v1 endpoint (``/v1/billing/balance``) returned CREDITS and was
    retired on 2026-04-15. v2 returns USD.
    """
    if not isinstance(body, dict):
        return _failed("unreadable")
    balance = _decimal(body.get("current_balance"))
    if balance is None:
        return _failed("unreadable")
    return ProbeResult(
        status="ok",
        balance=balance,
        currency=str(body.get("currency") or "USD").upper(),
        source="/v2/billing",
    )


def probe_aimlapi(secret: str, client: httpx.Client) -> ProbeResult:
    status, body = _get_json(client, "https://api.aimlapi.com/v2/billing", secret)
    if status != 200:
        return _failed("unreadable", status)
    return parse_aimlapi(body)


ProbeFn = Callable[[str, httpx.Client], ProbeResult]


@dataclass(frozen=True)
class Probe:
    fn: ProbeFn
    #: The ONE host this probe sends a key to. See the module docstring.
    host: str


#: 🔴 **The ONE registry.** A vendor gets a probe by adding one row here. A
#: vendor that is absent is ``not_exposed`` and relies on the refusal count.
PROBES: dict[str, Probe] = {
    "deepseek": Probe(probe_deepseek, "api.deepseek.com"),
    "openrouter": Probe(probe_openrouter, "openrouter.ai"),
    "aimlapi": Probe(probe_aimlapi, "api.aimlapi.com"),
}


def _same_host(api_base: str | None, host: str) -> bool:
    if not api_base:
        return True
    netloc = (urlparse(api_base).hostname or "").lower()
    return netloc == host


def probe(
    provider: str,
    secret: str,
    *,
    api_base: str | None = None,
    client: httpx.Client | None = None,
) -> ProbeResult:
    """Probe one provider. **Never raises, and never returns the key.**"""
    entry = PROBES.get(provider)
    if entry is None:
        return ProbeResult(status="not_exposed", error=_ERRORS["not_in_registry"])
    if not _same_host(api_base, entry.host):
        return ProbeResult(status="not_exposed", error=_ERRORS["custom_base"])
    own = client is None
    http = client or httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        return entry.fn(secret, http)
    except httpx.TimeoutException:
        return _failed("timeout")
    except httpx.HTTPError:
        return _failed("network")
    except Exception:  # a parse bug must not stop the other probes
        return _failed("unreadable")
    finally:
        if own:
            http.close()


# ── The refusal buffer ──────────────────────────────────────────────────────
#
# ⚠️ **In memory, and that is deliberate.** `walk_chain` runs on the serving
# event loop for a stream. A database write there would block every other
# request on the loop. So the hook only takes a lock and bumps a counter, and
# the watch loop in `main.py` writes the buffer every few seconds.
#
# The cost: a process that dies loses at most one flush interval of counts.
# A refusing vendor refuses the next call too, so the signal comes back.

_BUFFER_LOCK = threading.Lock()
#: (provider, status) -> [count, first_at, last_at]
_BUFFER: dict[tuple[str, int], list[Any]] = {}
#: A ceiling on distinct keys, so a flood of odd model prefixes cannot grow it.
_BUFFER_MAX_KEYS = 512
_PROVIDER_MAX = 40


def _watched(status: int | None) -> bool:
    return isinstance(status, int) and (status in REFUSAL_STATUSES or 500 <= status < 600)


def note_refusal(provider: str, status: int | None, *, now: datetime | None = None) -> None:
    """Count one refusal. **Never raises**, and never touches the database."""
    try:
        if not _watched(status) or not provider:
            return
        key = (str(provider).strip().lower()[:_PROVIDER_MAX], int(status))  # type: ignore[arg-type]
        at = now or datetime.now(UTC)
        with _BUFFER_LOCK:
            slot = _BUFFER.get(key)
            if slot is None:
                if len(_BUFFER) >= _BUFFER_MAX_KEYS:
                    return
                _BUFFER[key] = [1, at, at]
            else:
                slot[0] += 1
                slot[2] = at
    except Exception:  # pragma: no cover - the hook must never fail a call
        return


#: provider -> the latest call a PLATFORM key served. Same lock as the counts.
_SUCCESS: dict[str, datetime] = {}


def note_success(provider: str, *, now: datetime | None = None) -> None:
    """Remember that a platform key just served. **Never raises.**

    One dict write under the lock, on every served call: no I/O, and no
    allocation past the first call per vendor.
    """
    try:
        if not provider:
            return
        key = str(provider).strip().lower()[:_PROVIDER_MAX]
        at = now or datetime.now(UTC)
        with _BUFFER_LOCK:
            if key in _SUCCESS or len(_SUCCESS) < _BUFFER_MAX_KEYS:
                prev = _SUCCESS.get(key)
                _SUCCESS[key] = at if prev is None or at > prev else prev
    except Exception:  # pragma: no cover - the hook must never fail a call
        return


def drain_successes() -> dict[str, datetime]:
    """Take the latest success per provider, and empty the map."""
    with _BUFFER_LOCK:
        out = dict(_SUCCESS)
        _SUCCESS.clear()
    return out


def restore_successes(rows: dict[str, datetime]) -> None:
    with _BUFFER_LOCK:
        for p, at in rows.items():
            prev = _SUCCESS.get(p)
            _SUCCESS[p] = at if prev is None or at > prev else prev


def drain_refusals() -> list[dict[str, Any]]:
    """Take everything buffered, and empty the buffer."""
    with _BUFFER_LOCK:
        items = list(_BUFFER.items())
        _BUFFER.clear()
    return [
        {"provider": p, "status": s, "refusals": n, "first_at": first, "last_at": last}
        for (p, s), (n, first, last) in items
    ]


def restore_refusals(rows: list[dict[str, Any]]) -> None:
    """Put drained rows back after a failed write, so the next flush retries."""
    with _BUFFER_LOCK:
        for r in rows:
            key = (r["provider"], r["status"])
            slot = _BUFFER.get(key)
            if slot is None:
                if len(_BUFFER) < _BUFFER_MAX_KEYS:
                    _BUFFER[key] = [r["refusals"], r["first_at"], r["last_at"]]
            else:
                slot[0] += r["refusals"]
                slot[1] = min(slot[1], r["first_at"])
                slot[2] = max(slot[2], r["last_at"])


# ── The status rule ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HealthInput:
    """Everything the rule reads for one provider. Pure data, no connection."""

    provider: str
    balance: Decimal | None = None
    currency: str | None = None
    available: bool | None = None
    balance_checked_at: datetime | None = None
    probe_status: str | None = None
    probe_http: int | None = None
    low_threshold: Decimal | None = None
    #: Latest refusal per status, inside the 24-hour window.
    last_refusal_by_status: dict[int, datetime] = field(default_factory=dict)
    #: How many of each status came inside the refusing window.
    recent_count_by_status: dict[int, int] = field(default_factory=dict)
    #: The last call a platform key served on this vendor.
    last_success_at: datetime | None = None
    #: What this vendor cost US over the last seven days, in USD.
    cost_7d_usd: Decimal | None = None


@dataclass(frozen=True)
class Assessment:
    status: str
    reason: str
    days_left: Decimal | None
    threshold: Decimal | None
    #: One of :data:`CAUSES`, or None when nothing is wrong.
    cause: str | None = None


def days_left(
    balance: Decimal | None, currency: str | None, cost_7d_usd: Decimal | None
) -> Decimal | None:
    """Days of runway at the last seven days' pace. USD only, and never a guess.

    ⚠️ None when the balance is not USD. Converting CNY at a rate we do not
    hold would be a number with a confident face and no source.
    """
    if balance is None or (currency or "").upper() != "USD":
        return None
    if cost_7d_usd is None or cost_7d_usd <= 0 or balance <= 0:
        return None
    per_day = cost_7d_usd / Decimal(7)
    return (balance / per_day).quantize(Decimal("0.1"))


def _live_refusals(
    h: HealthInput, *, now: datetime, window: timedelta, repeat: int = DEFAULT_REPEAT
) -> set[int]:
    """The refusal statuses that still count.

    Every status: inside the window. 401 and 402: not cleared by a healthy
    probe after them. 403 and 429: at least ``repeat`` of them inside the
    window, and no SERVED call on this vendor since the latest one.
    """
    healthy_probe_at = (
        h.balance_checked_at
        if h.probe_status == "ok"
        and h.available is not False
        and h.balance is not None
        and h.balance > 0
        else None
    )
    live: set[int] = set()
    for status, at in h.last_refusal_by_status.items():
        if at is None or now - at > window:
            continue
        if (
            status in _SUPERSEDED_BY_PROBE
            and healthy_probe_at is not None
            and healthy_probe_at > at
        ):
            continue
        if status in _NEEDS_REPEAT:
            if h.recent_count_by_status.get(status, 0) < repeat:
                continue
            if h.last_success_at is not None and h.last_success_at > at:
                continue
        live.add(status)
    return live


Verdict = tuple[str, str, str]


def _refusal_verdict(h: HealthInput, live: set[int]) -> Verdict | None:
    """``out``, ``refusing`` or ``rate_limited``, with the reason and the
    cause, or None."""
    if h.available is False:
        return "out", "The vendor reports that this account cannot serve calls.", "payment"
    if h.balance is not None and h.balance <= 0 and h.probe_status in ("ok", "failed"):
        return (
            "out",
            "The balance is zero or less, so the vendor refuses every call.",
            "payment",
        )
    if 402 in live:
        return "out", "The vendor refused a call with 402 (payment required).", "payment"
    rejected = sorted(live & {401, 403})
    if rejected:
        names = ", ".join(str(s) for s in rejected)
        return "refusing", f"The vendor rejected our key on recent calls ({names}).", "key"
    if h.probe_status == "failed" and h.probe_http == 401:
        return "refusing", "The vendor rejected our key on its balance endpoint (401).", "key"
    if 429 in live:
        return (
            "rate_limited",
            "The vendor is rate-limiting our calls (429), and none has been served since.",
            "rate_limit",
        )
    return None


def assess(
    h: HealthInput,
    *,
    now: datetime,
    low_usd: Decimal = DEFAULT_LOW_USD,
    refusing_window: timedelta = timedelta(minutes=DEFAULT_REFUSING_MINUTES),
    repeat: int = DEFAULT_REPEAT,
) -> Assessment:
    """🔴 **THE status rule.** One function, and every surface reads it.

    Worst first:

      * ``out`` (cause ``payment``) — the vendor says the account cannot
        serve, or the balance is zero or less, or ONE recent 402 with no
        healthy probe after it.
      * ``refusing`` (cause ``key``) — ONE recent 401, or ``repeat`` 403s in
        the window with no served call since, or the balance probe itself was
        refused with 401.
      * ``rate_limited`` (cause ``rate_limit``, amber) — ``repeat`` 429s in
        the window with no served call since.
      * ``low`` — the balance is under the threshold, or under three days of
        runway at the last seven days' pace.
      * ``probe_failed`` — the vendor has a balance endpoint and the read
        failed.
      * ``unknown`` — the vendor exposes no balance and nothing refused. Shown
        as a neutral warning. **Never green**: "we cannot see it" is not "ok".
      * ``ok``.

    ⚠️ The threshold. A per-provider override is in the provider's own
    currency. The env default is USD, so it applies to a USD balance only.
    """
    threshold = h.low_threshold
    if threshold is None and (h.currency or "").upper() == "USD":
        threshold = low_usd
    runway = days_left(h.balance, h.currency, h.cost_7d_usd)

    def result(status: str, reason: str, cause: str | None = None) -> Assessment:
        return Assessment(
            status=status, reason=reason, days_left=runway, threshold=threshold, cause=cause
        )

    live = _live_refusals(h, now=now, window=refusing_window, repeat=repeat)
    refused = _refusal_verdict(h, live)
    if refused is not None:
        return result(*refused)

    if h.balance is not None and threshold is not None and h.balance < threshold:
        line = f"{format(threshold.normalize(), 'f')} {h.currency or ''}".strip()
        return result("low", f"The balance is under the low line of {line}.", "balance")
    if runway is not None and runway < LOW_DAYS_LEFT:
        return result("low", f"About {runway} days left at the last seven days' pace.", "balance")

    if h.probe_status == "failed":
        return result(
            "probe_failed",
            "The balance read failed. Check the credential and the vendor.",
            "probe",
        )
    if h.probe_status is None:
        return result("unknown", "Not checked yet. Watch for refusals.", "invisible")
    if h.probe_status == "not_exposed":
        return result(
            "unknown",
            "This vendor does not expose its balance. Watch for refusals.",
            "invisible",
        )
    if threshold is None:
        # ⚠️ A CNY balance with no override has NO low line: the USD default
        # does not apply to it. "Above the low line" would claim a check that
        # never ran.
        currency = h.currency or "this currency"
        return result("ok", f"No low line is set for {currency}. Set one to be warned.")
    return result("ok", "The balance is above the low line.")


# ── Orchestration: the loop, the route and the report share these ──────────
#
# ⚠️ **No transaction is open while a probe waits on the network.** The keys
# are read in one short transaction, the probes run with none open, and the
# results are written in a second one. A slow vendor must not hold a
# connection from the pool for eight seconds.

#: The states that alert. `unknown` and `probe_failed` do not.
ALERTING = frozenset({"out", "refusing", "rate_limited", "low"})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _money(value: Decimal | None) -> str | None:
    """Money leaves as a string, like every other amount this API returns."""
    return None if value is None else format(value.normalize(), "f")


def flush_refusals(engine: Any) -> int:
    """Write the buffer: refusal counts to ``provider_refusal``, and the latest
    success per provider to ``provider_health.last_success_at``. **Never
    raises.** Returns how many rows it wrote.

    A failed write puts both back, so the next flush retries them.
    """
    from customer_console import store

    rows = drain_refusals()
    served = drain_successes()
    if not rows and not served:
        return 0
    try:
        with engine.begin() as conn:
            return store.provider_refusal_insert(conn, rows) + store.provider_health_note_success(
                conn, served
            )
    except Exception as exc:
        restore_refusals(rows)
        restore_successes(served)
        _log.warning("provider.refusal_flush_failed error=%s", type(exc).__name__)
        return 0


def probe_all(engine: Any, *, client: httpx.Client | None = None) -> int:
    """Probe every live platform credential and store what each said.

    Returns how many providers were probed. Every failure is one row with a
    reason in our own words, never an exception.
    """
    from customer_console import router, store

    keys: dict[str, tuple[str, str | None] | None] = {}
    with engine.begin() as conn:
        for name in store.provider_platform_names(conn):
            try:
                cred = router.provider_credential(conn, provider=name, org_id=None)
            except Exception:
                keys[name] = None
                continue
            # `org_id=None` returns the platform row or nothing. A BYOK row
            # can never come back, and this guard says so out loud.
            keys[name] = None if cred is None or cred.byok else (cred.secret, cred.api_base)

    results: dict[str, ProbeResult] = {}
    for name, key in keys.items():
        if key is None:
            results[name] = ProbeResult(status="failed", error=_ERRORS["decrypt"])
            continue
        results[name] = probe(name, key[0], api_base=key[1], client=client)
        if results[name].status == "failed":
            _log.warning(
                "provider.probe_failed provider=%s http=%s reason=%s",
                name,
                results[name].http_status,
                results[name].error,
            )
    keys.clear()

    with engine.begin() as conn:
        for name, r in results.items():
            store.provider_health_save_probe(
                conn,
                provider=name,
                status=r.status,
                balance=r.balance,
                currency=r.currency,
                available=r.available,
                error=r.error,
                http_status=r.http_status,
            )
        store.provider_refusal_prune(conn)
    return len(results)


def health_report(
    conn: Any,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One row per LIVE PLATFORM credential, judged by :func:`assess`."""
    from customer_console import store

    now = now or datetime.now(UTC)
    names = store.provider_platform_names(conn)
    health = store.provider_health_rows(conn)
    low_usd = low_line_usd(env)
    window = _refusing_window(env)
    repeat = repeat_threshold(env)
    refusals = store.provider_refusal_summary(
        conn, hours=24, recent_minutes=window // timedelta(minutes=1)
    )
    spend = {r["provider"]: r["cost_usd"] for r in store.spend_by_provider(conn, days=7)}

    out: list[dict[str, Any]] = []
    for name in names:
        h = health.get(name, {})
        seen = refusals.get(name, {})
        verdict = assess(
            HealthInput(
                provider=name,
                balance=h.get("balance"),
                currency=h.get("currency"),
                available=h.get("available"),
                balance_checked_at=h.get("balance_checked_at"),
                probe_status=h.get("probe_status"),
                probe_http=h.get("probe_http"),
                low_threshold=h.get("low_threshold"),
                last_refusal_by_status=seen.get("by_status", {}),
                recent_count_by_status=seen.get("recent_by_status", {}),
                last_success_at=h.get("last_success_at"),
                cost_7d_usd=spend.get(name),
            ),
            now=now,
            low_usd=low_usd,
            refusing_window=window,
            repeat=repeat,
        )
        out.append(
            {
                "provider": name,
                "status": verdict.status,
                "reason": verdict.reason,
                # Why, so the console's headline names the real cause:
                # payment, key, rate_limit, balance, probe or invisible.
                "cause": verdict.cause,
                "last_success_at": _iso(h.get("last_success_at")),
                "balance": _money(h.get("balance")),
                "currency": h.get("currency"),
                "available": h.get("available"),
                "balance_checked_at": _iso(h.get("balance_checked_at")),
                "probe_status": h.get("probe_status"),
                "probe_error": h.get("probe_error"),
                "probe_attempted_at": _iso(h.get("updated_at")) if h.get("probe_status") else None,
                "balance_exposed": name in PROBES,
                "threshold": _money(verdict.threshold),
                "days_left": _money(verdict.days_left),
                "cost_7d_usd": _money(spend.get(name)),
                "last_refusal_status": seen.get("last_status"),
                "last_refusal_at": _iso(seen.get("last_at")),
                "refusals_24h": seen.get("refusals", 0),
                "server_errors_24h": seen.get("server_errors", 0),
            }
        )
    return out


def refresh_alerts(conn: Any, report: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Log each provider's status once per TRANSITION. Returns what changed.

    ``out`` and ``refusing`` log ``provider.refusing``, ``rate_limited``
    logs ``provider.rate_limited``, ``low`` logs
    ``provider.balance_low``, and leaving a bad state logs
    ``provider.recovered``. A provider that stays bad logs nothing more.
    """
    from customer_console import store

    changed: list[tuple[str, str]] = []
    for row in report:
        moved, prev = store.provider_health_set_alert(
            conn, provider=row["provider"], state=row["status"]
        )
        if not moved:
            continue
        changed.append((row["provider"], row["status"]))
        if row["status"] in ("out", "refusing"):
            _log.warning(
                "provider.refusing provider=%s state=%s last_status=%s reason=%s",
                row["provider"],
                row["status"],
                row["last_refusal_status"],
                row["reason"],
            )
        elif row["status"] == "rate_limited":
            _log.warning(
                "provider.rate_limited provider=%s refusals_24h=%s reason=%s",
                row["provider"],
                row["refusals_24h"],
                row["reason"],
            )
        elif row["status"] == "low":
            _log.warning(
                "provider.balance_low provider=%s balance=%s currency=%s days_left=%s",
                row["provider"],
                row["balance"],
                row["currency"],
                row["days_left"],
            )
        elif prev in ALERTING:
            _log.info(
                "provider.recovered provider=%s state=%s was=%s",
                row["provider"],
                row["status"],
                prev,
            )
    return changed
