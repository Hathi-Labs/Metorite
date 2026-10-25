"""The one web push sender (WS-66 PN-1).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §10.2 item 4, and
``chat_run_continuity.md`` §S7 items 4 and 5.

* **One network door.** The endpoint is member input, so a send takes two
  steps, as ``gateway/kb_sources/http.py`` does (KC-1a). First the push
  service allowlist (``endpoints.endpoint_refusal``). Then
  ``outbound_guard.request_https``, the gateway's one guard: it resolves the
  host once, refuses a private address, pins the socket, refuses a redirect,
  and caps the time and the answer.
* **pywebpush makes the request, and never sends it.** ``WebPusher.encode``
  encrypts the payload (RFC 8291, ``aes128gcm``), and ``Vapid02.sign`` makes
  the ``Authorization`` header (RFC 8292). That work is CPU only, so it runs
  in ``asyncio.to_thread``.
* **All devices at once, inside one bound.** The sends to one member's
  devices run together, and :data:`SEND_BUDGET_S` bounds them all. A send
  that has not answered by then counts as failed. The BFF waits 30 seconds,
  so the bound stays well under that.
* **The outcome.** A 2xx answer stamps ``last_success_at``. A 404 or a 410
  says the subscription is gone, and the sender deletes that row in a
  session bound to the same tenant (``store.record``). Any other answer, a
  refusal of the guard and a transport fault count one failure and keep the
  row.
* **No connection waits on the network.** The sender reads the devices in one
  short transaction and writes the outcomes in a second one.
* **No content.** :data:`TEST_MESSAGE` has a fixed title and no body (S7
  item 5). A log line names the host of an endpoint, never its path.

RE-9 will pass its own :class:`PushMessage` (``title``, ``body``, ``url``,
``tag``, ``urgency``, ``ttl``). Today only ``POST /push/test`` sends.

Fence: ``tests/unit/test_push_subscriptions.py`` fakes the network at the
``outbound_guard.request_https`` boundary, decrypts what goes out, and runs
the SQL on a real Postgres. ``tests/unit/test_push_platform.py`` covers the
rest with no database.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx
from acb_common import get_logger

from gateway import outbound_guard
from gateway.push import endpoints, flags, store

_log = get_logger(__name__)

#: Seconds for the sends to ALL the devices of one call. The guard caps each
#: request at ``outbound_guard.TOTAL_TIMEOUT_S`` (10) as well.
SEND_BUDGET_S = 20.0

#: The answers that say the subscription is gone (RFC 8030 §7.3).
GONE_STATUSES = frozenset({404, 410})

#: How long the VAPID JWT lives. RFC 8292 §2 allows at most 24 hours.
VAPID_LIFETIME_S = 12 * 3600

Urgency = Literal["very-low", "low", "normal", "high"]

#: The fixed title of the test push (S7 item 5). The payload carries no body.
TEST_TITLE = "Metorite test notification"


@dataclass(frozen=True)
class PushMessage:
    """One notification. ``url`` must be a path on this app.

    The payload holds ``title``, ``url`` and, when set, ``body`` and ``tag``.
    The service worker (``workbench/control_plane/public/sw.js``) reads these
    four keys and no other."""

    title: str
    body: str | None = None
    url: str = "/"
    tag: str | None = None
    urgency: Urgency = "normal"
    ttl: int = 60

    def payload(self) -> str:
        """The JSON that the push service carries, encrypted, to the device."""
        if not self.url.startswith("/") or self.url.startswith("//"):
            raise ValueError("a push url is a path on this app")
        data: dict[str, str] = {"title": self.title, "url": self.url}
        if self.body is not None:
            data["body"] = self.body
        if self.tag is not None:
            data["tag"] = self.tag
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


#: The test push of ``POST /push/test``: a fixed title, no body, and a tap
#: opens My Profile, where the member turned push on.
TEST_MESSAGE = PushMessage(title=TEST_TITLE, url="/people/me", tag="metorite-test")


@dataclass(frozen=True)
class SendReport:
    """What one send to one member's devices did."""

    devices: int
    sent: int
    removed: int
    failed: int


def _request(device: store.Device, message: PushMessage, key: object,
             subject: str) -> tuple[bytes, dict[str, str]]:
    """The encrypted body and the headers of one push. CPU only, no network.

    ``WebPusher.encode`` and ``Vapid02.sign`` are pywebpush's own parts, so the
    bytes are the ones ``pywebpush.webpush`` would send."""
    from pywebpush import WebPusher

    pusher = WebPusher({"endpoint": device.endpoint,
                        "keys": {"p256dh": device.p256dh, "auth": device.auth}})
    body = pusher.encode(message.payload().encode("utf-8"), "aes128gcm")["body"]
    parts = urlsplit(device.endpoint)
    claims = {"sub": subject, "aud": f"{parts.scheme}://{parts.netloc}",
              "exp": int(time.time()) + VAPID_LIFETIME_S}
    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(max(0, int(message.ttl))),
        "Urgency": message.urgency,
    }
    headers.update(key.sign(claims))  # type: ignore[attr-defined]
    return body, headers


async def _post(device: store.Device, message: PushMessage, key: object, subject: str) -> str:
    """Send one push through the one guard. Answers ``sent``, ``gone`` or
    ``failed``, and never raises."""
    host = endpoints.endpoint_host(device.endpoint)
    try:
        body, headers = await asyncio.to_thread(_request, device, message, key, subject)
    except Exception as exc:  # a bad device key: count it, keep the row
        _log.warning("push.encode_failed", host=host, error=type(exc).__name__)
        return "failed"
    try:
        answer = await outbound_guard.request_https(
            "POST", device.endpoint, headers=headers, content=body)
    except outbound_guard.OutboundRefused as exc:
        # The guard names the host and a reason, never the path.
        _log.warning("push.send_refused", host=host, reason=exc.reason)
        return "failed"
    except (TimeoutError, httpx.HTTPError) as exc:
        _log.warning("push.send_failed", host=host, error=type(exc).__name__)
        return "failed"
    status = answer.status_code
    if 200 <= status < 300:
        return "sent"
    if status in GONE_STATUSES:
        _log.info("push.gone", host=host, status=status)
        return "gone"
    _log.warning("push.send_failed", host=host, status=status)
    return "failed"


async def _send_all(jobs: list[tuple[store.Device, PushMessage, object, str]],
                    budget_s: float) -> list[str]:
    """Run every send at once. A send still open after *budget_s* is cancelled
    and counts as ``failed``."""
    if not jobs:
        return []
    tasks = [asyncio.create_task(_post(*job)) for job in jobs]
    done, pending = await asyncio.wait(tasks, timeout=budget_s)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        _log.warning("push.send_budget_spent", open_sends=len(pending))
    results: list[str] = []
    for task in tasks:
        if task in done and not task.cancelled() and task.exception() is None:
            results.append(task.result())
        else:
            results.append("failed")
    return results


async def send_to_member(org: str, member: str, message: PushMessage,
                         *, budget_s: float = SEND_BUDGET_S) -> SendReport:
    """Send *message* to each device of *member* in *org*.

    The caller checked ``flags.enabled()``. Raises ``RuntimeError`` when the
    keys are not configured, so a caller cannot send unsigned."""
    key, subject = flags.vapid(), flags.subject()
    if key is None or subject is None:
        raise RuntimeError("web push keys are not configured")
    message.payload()  # refuse a bad url before any send
    found = await store.devices(org, member)
    sendable = [d for d in found if endpoints.endpoint_refusal(d.endpoint) is None]
    sent = await _send_all([(d, message, key, subject) for d in sendable], budget_s)
    results = dict(zip((d.id for d in sendable), sent, strict=True))
    outcomes = [(d, results.get(d.id, "skipped")) for d in found]
    await store.record(org, member, outcomes)
    report = SendReport(
        devices=len(found),
        sent=sum(1 for _, o in outcomes if o == "sent"),
        removed=sum(1 for _, o in outcomes if o == "gone"),
        failed=sum(1 for _, o in outcomes if o in ("failed", "skipped")),
    )
    _log.info("push.sent", devices=report.devices, sent=report.sent,
              removed=report.removed, failed=report.failed)
    return report
