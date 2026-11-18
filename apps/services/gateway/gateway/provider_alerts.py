"""Tell the admins of the operator org when an AI vendor runs out (AO-2).

Spec: ``project-docs/specs/customer_console.md`` §6A.17b. Metorite task 3683.

🔴 **Why this exists.** On 2026-11-17 the Anthropic account ran out of
credit, and every chat turn failed for more than 2 hours before anyone knew.
AO-1 made the Console read that as ``out``, but the only signal was a panel of
the Operator Console that nobody watched. The owner chose the channel on
2026-11-18: a Metorite notification, the bell and a phone push.

**Who judges and who tells.** The Console judges each vendor
(``provider_balance.alert_signal``). This box reads one word per vendor
through the ONE Console client (``console_resolve.provider_health_on_console``),
remembers the last alert it sent, and tells the admins. The box asks, because
every arrow between the planes runs from the box to the Console.

**When an alert fires** (:func:`decide`).

* A change INTO ``out`` or ``low`` sends that alert.
* ``serving`` after ``out`` or ``low`` sends one "serves again".
* ``clear`` and ``other`` keep the remembered state, and send nothing. A
  vendor with no balance probe reads ``clear`` when its refusal aged out with
  no call. That proves nothing, so the bell keeps the alert until a call
  serves.

**At most one alert an hour for each vendor and state.** :func:`run_pass`
claims ``key("provider_alerts", "slot", <vendor>, <state>)`` with ``SET NX EX
3600`` before it sends. A claim that fails sends nothing and does NOT move the
remembered state, so the alert goes out when the hour ends, if the state still
holds.

**A restart sends nothing for an unchanged state.** The remembered state is in
the tenant Redis of the org (``key("provider_alerts", "sent")``), with no TTL,
so a new process reads the same state and :func:`decide` finds no change.

**Who hears it.** The active members of an org that ``PROVIDER_ALERT_ORGS``
lists, who pass the shell's admin test (``routes/shell/intent.ADMIN_PERMISSION``).
The org comes from the flag and the job worker, never from request input (R5).
The members come from ``kb_access.resolve.load_members`` inside
``tenant_session(org)``. Each grant comes from ``resolve_access_in_org``.

**How it reaches them.**

* The bell: ``routes/shell/needs.py`` reads :func:`bell_rows` as the needs
  source ``providers``. No table is written.
* The phone: ``push.sender.send_to_member``, the one sender, when web push is
  on and its keys are set.

🔴 **No vendor text.** The Console door sends a closed set of keys, the client
checks each one, and every word of a bell row, a push and a log line comes from
:func:`words` or is an id.

**Cadence.** The job queue's seed hook, once for each org and 15-minute bucket
(``docs/job_queue.md`` §5c). It is the one recurring seam, so this module adds
no loop.

Fence: ``tests/unit/test_provider_alerts.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger, get_settings, tenant_redis
from acb_common.db import tenant_session
from acb_common.tenant_redis import key, organization_scope

from gateway.jobs import queue, worker

_log = get_logger("provider_alerts")

#: The job kind of one pass. Detached: it calls the Console and a push service.
KIND = "provider_alerts.check"
#: One alert of each vendor and state in this many seconds, at most.
RATE_LIMIT_S = 3600
#: How long the bell keeps a "serves again" row.
RECOVERED_SHOWN = timedelta(hours=24)
#: The provider page of the Operator Console. Each bell row links here.
PROVIDER_PAGE = "https://operator.metorite.com/providers"
#: A push opens this path of the app. The sender takes an app path only, so a
#: push cannot open the Operator Console. The bell row there links to it.
PUSH_URL = "/"
#: A push to a phone that sleeps still arrives within this time.
PUSH_TTL_S = 12 * 3600
#: The states that this module remembers and sends.
STATES = ("out", "low", "ok")
#: The bell shows these states while they hold.
_OPEN = frozenset({"out", "low"})

#: The job's own policy line (BR-7a): it reaches people.
POLICY = (
    "AO-2: only the admins of an org that PROVIDER_ALERT_ORGS lists; "
    "one alert an hour for each vendor and state"
)


def _now() -> datetime:
    """The one clock of this module. A test freezes THIS."""
    return datetime.now(UTC)


# ── The flag ────────────────────────────────────────────────────────────────


def alert_orgs() -> frozenset[str]:
    """The org ids of ``PROVIDER_ALERT_ORGS``, trimmed and in lower case. The
    ONE reader of the flag.

    ``*`` is dropped: a customer's admins must never get our vendor alerts,
    so the flag names the operator org by its id. A SIGHUP reload applies a
    change, because each call reads the settings again.
    """
    raw = str(getattr(get_settings(), "provider_alert_orgs", "") or "")
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip() and p.strip() != "*")


def alert_allowed(organization_id: Any) -> bool:
    """True when the flag lists *organization_id*. Empty sends nothing."""
    org = str(organization_id or "").strip().lower()
    return bool(org) and org in alert_orgs()


# ── The words ───────────────────────────────────────────────────────────────


def _days(days_left: str | None) -> str | None:
    if not days_left:
        return None
    return f"about {days_left} {'day' if days_left == '1' else 'days'} left"


def words(state: str, label: str, days_left: str | None = None) -> tuple[str, str | None]:
    """The title and the detail of one alert. Operator-facing, so it names the
    vendor by its display name. Every word is ours."""
    if state == "out":
        return (f"{label} is out of credit.",
                f"The chat now uses the next model in the chain. Top up the {label} account.")
    if state == "low":
        days = _days(days_left)
        return (f"{label} credit is low: {days}." if days else f"{label} credit is low.", None)
    return f"{label} serves again.", None


# ── The state ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alert:
    """One alert that was sent, as the remembered state keeps it."""

    provider: str
    label: str
    state: str
    days_left: str | None
    at: str

    def entry(self) -> dict[str, Any]:
        return {"label": self.label, "state": self.state,
                "days_left": self.days_left, "at": self.at}


def decide(last: dict[str, Any] | None, signal: str) -> str | None:
    """The alert that *signal* sends after the remembered entry *last*, or
    None. Pure.

    * ``out`` or ``low``: that alert, when it is not the remembered state.
    * ``serving``: ``ok`` ("serves again") after ``out`` or ``low``. Before
      any alert there is nothing to recover from.
    * ``clear`` and ``other``: nothing, and the state holds.
    """
    prev = last.get("state") if isinstance(last, dict) else None
    if signal in ("out", "low"):
        return signal if prev != signal else None
    if signal == "serving" and prev in _OPEN:
        return "ok"
    return None


def _sent_key() -> Any:
    return key("provider_alerts", "sent")


def _slot_key(provider: str, state: str) -> Any:
    return key("provider_alerts", "slot", provider, state)


def _parse_sent(raw: Any) -> dict[str, dict[str, Any]]:
    """The remembered map, or an empty one. A bad value reads as empty."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()
            if isinstance(v, dict) and v.get("state") in STATES}


async def read_sent(org: str) -> dict[str, dict[str, Any]]:
    """The last alert sent for each vendor of *org*."""
    with organization_scope(org):
        raw = await tenant_redis.get_tenant_redis().get(_sent_key())
    return _parse_sent(raw)


async def run_pass(org: str, rows: list[dict[str, Any]], now: datetime) -> list[Alert]:
    """Judge one Console answer for *org*, and send what changed.

    Returns the alerts sent. Each alert claims its hourly slot first. Then
    the remembered map is written, and only then is anything delivered. So a
    crash during a send never sends that alert twice.
    """
    at = now.astimezone(UTC).isoformat()
    fired: list[Alert] = []
    with organization_scope(org):
        redis = tenant_redis.get_tenant_redis()
        sent = _parse_sent(await redis.get(_sent_key()))
        for row in rows:
            provider = str(row["provider"])
            state = decide(sent.get(provider), str(row["signal"]))
            if state is None:
                continue
            claimed = await redis.set(_slot_key(provider, state), at, nx=True, ex=RATE_LIMIT_S)
            if not claimed:
                _log.info("provider_alerts.held", provider=provider, state=state)
                continue
            alert = Alert(provider=provider, label=str(row["label"]), state=state,
                          days_left=row.get("days_left") if state == "low" else None, at=at)
            sent[provider] = alert.entry()
            fired.append(alert)
        if fired:
            await redis.set(_sent_key(), json.dumps(sent, sort_keys=True))
    if fired:
        await _deliver(org, fired)
    return fired


# ── The recipients and the delivery ─────────────────────────────────────────


async def recipients(org: str) -> list[str]:
    """The active members of *org* who pass the shell's admin test, in
    address order.

    The member list is one read in ``tenant_session(org)``, the R8-tested
    ``kb_access.resolve.load_members``. Each grant comes from
    ``resolve_access_in_org``, which opens its own session. So the list
    session closes first, and no session is held twice. A member whose
    access read fails is left out (it fails closed).
    """
    from acb_auth.access import resolve_access_in_org

    from gateway.kb_access.resolve import load_members
    from gateway.routes.shell.intent import ADMIN_PERMISSION

    async with tenant_session(org) as db:
        members = sorted(await load_members(db, org))
    admins: list[str] = []
    for email in members:
        access = await resolve_access_in_org(email, org)
        if access.is_active and access.has(ADMIN_PERMISSION):
            admins.append(email)
    return admins


def push_message(alert: Alert) -> Any:
    """The push of one alert. A long TTL, so a phone that sleeps still gets
    it, and one tag for each vendor, so a newer alert replaces an older one."""
    from gateway.push.sender import PushMessage

    title, body = words(alert.state, alert.label, alert.days_left)
    return PushMessage(title=title, body=body, url=PUSH_URL,
                       tag=f"provider-{alert.provider}",
                       urgency="high" if alert.state == "out" else "normal",
                       ttl=PUSH_TTL_S, renotify=True)


async def _push(org: str, alerts: list[Alert], admins: list[str]) -> int:
    """Send each alert to each admin's devices. Answers the sends that a
    device took. Push off or keys unset: no send, and the bell still shows."""
    from gateway.push import flags as push_flags
    from gateway.push import sender

    if not (push_flags.enabled() and push_flags.configured()):
        _log.info("provider_alerts.push_off")
        return 0
    taken = 0
    for alert in alerts:
        message = push_message(alert)
        for admin in admins:
            try:
                report = await sender.send_to_member(org, admin, message)
            except Exception as exc:  # one member's fault never stops the rest
                _log.warning("provider_alerts.push_failed", error=type(exc).__name__)
                continue
            taken += report.sent
    return taken


async def _deliver(org: str, alerts: list[Alert]) -> None:
    """Tell the admins. The bell reads the remembered map, so this moves the
    feed's org version and sends the push."""
    from gateway.routes.shell import cache

    try:
        admins = await recipients(org)
    except Exception as exc:
        _log.warning("provider_alerts.recipients_failed", error=type(exc).__name__)
        admins = []
    await cache.bump_org_safe(org)
    pushed = await _push(org, alerts, admins)
    for alert in alerts:
        _log.warning("provider_alerts.sent", provider=alert.provider, state=alert.state,
                     admins=len(admins), pushed=pushed)


# ── The bell ────────────────────────────────────────────────────────────────


async def bell_rows(org: str, now: datetime) -> list[Alert]:
    """The alerts that the bell shows for *org*: each vendor that is ``out``
    or ``low``, and each "serves again" of the last :data:`RECOVERED_SHOWN`.
    The newest first."""
    rows: list[Alert] = []
    for provider, entry in (await read_sent(org)).items():
        try:
            at = datetime.fromisoformat(str(entry.get("at")))
        except ValueError:
            continue
        state = entry["state"]
        if state == "ok" and now - at > RECOVERED_SHOWN:
            continue
        rows.append(Alert(provider=provider, label=str(entry.get("label") or provider),
                          state=state, days_left=entry.get("days_left"), at=at.isoformat()))
    return sorted(rows, key=lambda a: a.at, reverse=True)


# ── The job ─────────────────────────────────────────────────────────────────


async def seed(org: str, now: datetime) -> None:
    """The ``worker.seed_hooks`` hook: one :data:`KIND` job for each listed
    org and 15-minute bucket. The dedupe key holds the bucket."""
    if not alert_allowed(org):
        return
    async with tenant_session(org) as db:
        await queue.enqueue(db, KIND, {},
                            dedupe_key=f"provider_alerts:{worker.seed_bucket(now)}", now=now)


async def _settle(org: str, claim: queue.Claim, now: datetime) -> None:
    async with tenant_session(org) as db:
        if await queue.hold(db, claim):
            await queue.complete(db, claim, now=now)


async def check(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`KIND`: read the Console, then judge.

    No session is open while it waits on the Console or a push service. A
    Console that gives no answer settles the job ``done``. The next bucket
    reads again, so nothing is lost.
    """
    from acb_auth.console_resolve import (
        ConsoleProviderHealthUnavailable,
        provider_health_on_console,
    )

    now = _now()
    if alert_allowed(org):
        try:
            rows = await provider_health_on_console()
        except ConsoleProviderHealthUnavailable as exc:
            # Our own words: an HTTP status, "unwired", or a transport class.
            _log.warning("provider_alerts.console_unavailable", reason=str(exc)[:60])
        else:
            await run_pass(org, rows, now)
    await _settle(org, claim, _now())


def register_jobs(*, replace: bool = False) -> None:
    """Register the kind and the seed hook. Safe to call twice."""
    worker.register(KIND, check, mode="detached", replace=replace,
                    effect="send", policy=POLICY)
    if seed not in worker.seed_hooks:
        worker.seed_hooks.append(seed)


register_jobs()
