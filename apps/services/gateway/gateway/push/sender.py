"""The one web push sender (WS-66 PN-1).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §10.2 item 4, and
``chat_run_continuity.md`` §S7 items 4 and 5.

* **The send blocks.** ``pywebpush.webpush`` posts with ``requests``, so each
  send runs in ``asyncio.to_thread``. The sends to one member's devices go one
  after the other, so one member holds one worker thread at a time.
* **The outcome.** A 2xx answer stamps ``last_success_at``. A 404 or a 410
  says the subscription is gone, and the sender deletes that row in a session
  bound to the same tenant (``store.record``). Any other answer, and a
  transport fault, counts one failure and keeps the row.
* **No connection waits on the network.** The sender reads the devices in one
  short transaction and writes the outcomes in a second one. No transaction
  stays open across a send.
* **The network rules.** The endpoint must pass ``endpoints.endpoint_refusal``
  at send time too. The ``requests`` session refuses each redirect and reads
  no proxy from the environment (``_session``).
* **No content.** :data:`TEST_MESSAGE` has a fixed title and no body (S7
  item 5). A log line names the host of an endpoint, never its path.

RE-9 will pass its own :class:`PushMessage` (``title``, ``body``, ``url``,
``tag``, ``urgency``, ``ttl``). Today only ``POST /push/test`` sends.

Fence: ``tests/unit/test_push_subscriptions.py``. It fakes the push service at
the ``pywebpush.webpush`` boundary, and runs the SQL on a real Postgres.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

import requests
from acb_common import get_logger

from gateway.push import endpoints, flags, store

_log = get_logger(__name__)

#: Seconds for one send: the connect and the read of the answer.
SEND_TIMEOUT_S = 10.0

#: The answers that say the subscription is gone (RFC 8030 §7.3).
GONE_STATUSES = frozenset({404, 410})

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


class _NoRedirectSession(requests.Session):
    """A ``requests`` session that follows no redirect and reads no proxy.

    A redirect target never passed ``endpoints.endpoint_refusal``, so a 3xx
    comes back as an answer, and ``webpush`` raises on it as a failure."""

    def __init__(self) -> None:
        super().__init__()
        self.trust_env = False
        self.max_redirects = 0

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        kwargs["allow_redirects"] = False
        return super().request(method, url, *args, **kwargs)


def _session() -> requests.Session:
    """A fresh session for one send. A test replaces it."""
    return _NoRedirectSession()


def _post(device: store.Device, message: PushMessage, key: object, subject: str) -> str:
    """Send one push. BLOCKS: the caller runs it in a worker thread.

    Answers ``sent``, ``gone`` or ``failed``. It never raises."""
    from pywebpush import WebPushException, webpush

    host = endpoints.endpoint_host(device.endpoint)
    session = _session()
    try:
        webpush(
            subscription_info={"endpoint": device.endpoint,
                               "keys": {"p256dh": device.p256dh, "auth": device.auth}},
            data=message.payload(),
            vapid_private_key=key,  # type: ignore[arg-type]
            # A NEW dict for each send: webpush writes `aud` into it, and a
            # shared dict would keep the first endpoint's audience.
            vapid_claims={"sub": subject},
            ttl=max(0, int(message.ttl)),
            timeout=SEND_TIMEOUT_S,
            headers={"Urgency": message.urgency},
            requests_session=session,
        )
        return "sent"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in GONE_STATUSES:
            _log.info("push.gone", host=host, status=status)
            return "gone"
        _log.warning("push.send_failed", host=host, status=status)
        return "failed"
    except Exception as exc:  # a transport fault or a bad key: count it, keep the row
        _log.warning("push.send_failed", host=host, error=type(exc).__name__)
        return "failed"
    finally:
        session.close()


async def send_to_member(org: str, member: str, message: PushMessage) -> SendReport:
    """Send *message* to each device of *member* in *org*.

    The caller checked ``flags.enabled()``. Raises ``RuntimeError`` when the
    keys are not configured, so a caller cannot send unsigned."""
    key, subject = flags.vapid(), flags.subject()
    if key is None or subject is None:
        raise RuntimeError("web push keys are not configured")
    message.payload()  # refuse a bad url before any send
    found = await store.devices(org, member)
    outcomes: list[tuple[store.Device, str]] = []
    for device in found:
        if endpoints.endpoint_refusal(device.endpoint) is not None:
            outcomes.append((device, "skipped"))
            continue
        outcome = await asyncio.to_thread(_post, device, message, key, subject)
        outcomes.append((device, outcome))
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
