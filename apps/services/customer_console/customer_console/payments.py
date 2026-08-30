"""The payment-provider seam, and the ONE path from money to entitlement.

Spec: ``customer_console.md`` §6 **CP-9** (§9.2 the order and its state
machine · §9.4 the seam · §9.5 the webhook · §9.6 fulfilment) ·
``subscription_console.md`` **SC-4g** (iii)/(iv) (the pre-GST discount base and
the Rs 0 path) · D19.5 (Razorpay first, Stripe when the first international
customer appears).

Three things here are load-bearing, and none of them is the provider call.

**1. Money is INTEGER PAISE, converted exactly once.** ``plan_catalog`` stays
NUMERIC rupees — prices are data, and the D23/D24 ladder is denominated in
rupees — and :func:`paise` is the single named conversion, applied at order
creation from the catalog row. Razorpay's API is denominated in paise, and a
rupee amount that round-trips through JSON as a float is how Rs 1,800.00
becomes Rs 1,799.99 in a place nobody looks. :func:`paise` refuses a ``float``
argument outright rather than rounding one.

**2. The order's state machine has three TERMINAL states, and terminality is
the money guard.** ``captured``, ``failed`` and ``abandoned`` have no outgoing
edge, so a second capture event for an order that is already ``captured`` is a
no-op rather than a second grant. That matters because
:data:`payment_event.provider_event_id` does **not** catch it: Razorpay sends
*different* event ids for one capture (``payment.captured`` and ``order.paid``
are two events, two ids, one payment). The primary key is transport dedup; this
is the money guard; they answer different questions and both are kept.

**3. :func:`fulfil` is the only writer of entitlements on the checkout path**,
called from exactly two places — the webhook capture and SC-4g's Rs 0
redemption. Two implementations of "grant what was bought" is how a customer
ends up paying for something the free path gave away (D42), so the free path
does not get its own writer; it gets this one, with a different ``reference``.

⚠️ **What ships without a Razorpay account, which is every environment today.**
Credentials come from env with **no defaults**, and absence means the seam
REFUSES (503) rather than inventing an endpoint or a key: creating the account
is OWNER-GATE **even in test mode** (§8 gate 3, §9.7). The agent-reachable half
is everything else — :class:`FakeProvider` computes the **real** HMAC-SHA256
signature over the raw body with a test secret, so the algorithm under test is
the shipped one and only the network is fake. The test-mode capture rehearsal
(SC-4g clause 2) is handed to the owner, scripted; a PR claiming it is a PR
claiming something it did not do.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy.engine import Connection

from customer_console import store
from customer_console.credits import (
    LEDGER_REASON_DISCOUNT_REDEMPTION,
    LEDGER_REASON_PURCHASE,
)
from customer_console.lifecycle import TransitionRefused

__all__ = [
    "GST_RATE_BP",
    "ORDER_STATES",
    "ORDER_TERMINAL_STATES",
    "PACK_KINDS",
    "PROVIDER_ENV_VARS",
    "SUBSCRIPTION_TERM_MONTHS",
    "FakeProvider",
    "PaymentProvider",
    "ProviderOrder",
    "ProviderUnconfigured",
    "RazorpayProvider",
    "WebhookEvent",
    "abandon_if_expired",
    "assert_order_transition",
    "can_order_transition",
    "compute_signature",
    "discount_for",
    "fulfil",
    "gst_for",
    "gst_split_for",
    "home_state",
    "paise",
    "provider",
    "reason_for",
    "set_provider",
    "transition_order",
]

_log = logging.getLogger("platform.payments")


# ── Money: one conversion, integer paise everywhere after it ────────────────

#: GST on a software service supply, in basis points — 18 percent
#: (``saas_operations_doctrine.md`` §3.1). Basis points rather than a float for
#: the same reason ``discount_code.percent_bp`` is: a tax that rounds
#: differently on two reads is a dispute, and this number ends up on a document
#: a tax authority reads.
GST_RATE_BP = 1800

_BP_DENOMINATOR = 10_000


def paise(price_inr: Decimal | int | str) -> int:
    """Convert a catalog rupee price to integer paise. **The one conversion.**

    Refuses ``float`` rather than rounding it. A float that reaches here has
    already lost the property the whole file exists to preserve, and accepting
    it would make this function the place the loss is laundered into an integer
    that looks exact. The catalog column is ``NUMERIC(12,2)`` and SQLAlchemy
    hands it over as ``Decimal``; anything else names its own bug.
    """
    if isinstance(price_inr, float):
        raise TypeError(
            "paise() refuses a float — money is Decimal or int here "
            "(customer_console.md §6 CP-9 §9.2). A float price has already "
            "lost precision; convert at the source, not here."
        )
    rupees = price_inr if isinstance(price_inr, Decimal) else Decimal(price_inr)
    return int((rupees * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def gst_for(taxable_paise: int) -> int:
    """GST on the **discounted** base, rounded half-up to the paisa.

    Computed on ``taxable_paise`` — i.e. AFTER any discount (SC-4g (iii)) —
    which is standard GST invoice practice and the only reading under which
    100 percent off yields taxable 0 -> GST 0 -> total 0. Discount-after-tax
    would leave GST payable on a zero-rupee sale, i.e. a bill for customer zero.
    """
    if taxable_paise < 0:
        raise ValueError("taxable_paise cannot be negative")
    return (taxable_paise * GST_RATE_BP + _BP_DENOMINATOR // 2) // _BP_DENOMINATOR


#: Our own registered state, deciding the CGST+SGST vs IGST split. An
#: agent-proposed default the owner may overrule (D16/D17): the supplier of
#: record is Fracktal Works, registered in Karnataka. It is env-overridable
#: because a supplier's registration is a business fact that must not need a
#: deploy to correct — and it is not a secret, so unlike the Razorpay
#: credentials it carries a default rather than refusing.
_HOME_STATE_ENV = "CUSTOMER_CONSOLE_HOME_STATE"
_HOME_STATE_DEFAULT = "KA"

SPLIT_INTRA_STATE = "cgst_sgst"
SPLIT_INTER_STATE = "igst"


def home_state() -> str:
    return (os.environ.get(_HOME_STATE_ENV, "").strip()
            or _HOME_STATE_DEFAULT).upper()


def gst_split_for(customer_state: str | None) -> str:
    """``cgst_sgst`` when the customer's state equals ours, ``igst`` otherwise.

    A customer with **no** recorded state therefore takes ``igst``, which is the
    spec's literal rule and the conservative one: the totals are identical
    either way (18 percent), so the only thing at stake is the split printed on
    a future invoice — and SC-5e's "changing the state changes the tax
    treatment of the NEXT invoice and never a past one" is exactly why the split
    is SNAPSHOTTED onto the order rather than joined at render time.
    """
    if customer_state and customer_state.strip().upper() == home_state():
        return SPLIT_INTRA_STATE
    return SPLIT_INTER_STATE


def discount_for(
    *, gross_paise: int, kind: str,
    percent_bp: int | None, amount_paise: int | None,
) -> int:
    """The discount this code applies to this gross, clamped to the gross.

    **A fixed-amount code larger than the gross is clamped, never negative** —
    a negative order total is a refund path and this is not one (SC-4g (iii)).

    Percent is floored rather than rounded, so a rounding artefact can never
    make the discount exceed the gross; the residual is at most one paisa and
    it lands on our side of the line, which is the correct direction for an
    artefact nobody chose.
    """
    if gross_paise < 0:
        raise ValueError("gross_paise cannot be negative")
    if kind == "percent":
        if percent_bp is None:
            raise ValueError("a percent code needs percent_bp")
        return min(gross_paise * percent_bp // _BP_DENOMINATOR, gross_paise)
    if kind == "fixed":
        if amount_paise is None:
            raise ValueError("a fixed code needs amount_paise")
        return min(amount_paise, gross_paise)
    raise ValueError(f"unknown discount kind {kind!r}")


# ── The order state machine (§9.2) ──────────────────────────────────────────
#
# EXTENDS `lifecycle.can_transition`'s idiom rather than inventing a second one:
# same shape (a graph as data, a predicate, an assert that raises), same
# exception type, different subject. Two state-machine idioms in one service
# would be two things to learn and one to get wrong.

#: Terminal, in the strong sense: **no edge leaves them**. This is the money
#: guard — a fulfilment attempt against an already-`captured` order is refused
#: here, which is what makes the SECOND event of one capture harmless.
ORDER_TERMINAL_STATES: frozenset[str] = frozenset({
    "captured", "failed", "abandoned",
})

_ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    # `created -> captured` exists for the Rs 0 path, which never reaches a
    # provider and therefore never "attempts" anything.
    #
    # ⚠️ **The two `-> failed` edges have NO writer** (2026-08-19, review P0).
    # They are kept because an explicit customer *cancel* is a real order-level
    # failure the surface half may add — but a failed payment ATTEMPT is not
    # one: one provider order accepts many attempts until one captures, and
    # closing the order on the first UPI timeout made the retry that succeeded
    # unfulfillable. Order-level failure today is `abandoned`, written by the
    # clock. Fenced by `test_no_code_path_drives_an_order_to_failed`.
    "created": frozenset({"attempted", "captured", "failed", "abandoned"}),
    # ⚠️ Nothing in THIS slice writes `attempted`. It is written by the surface
    # half (SC-4a) when the customer opens the provider's checkout, and the
    # edges exist now so that slice needs no graph change — a state machine
    # amended by the surface that trips over it is how the graph stops being
    # the authority.
    "attempted": frozenset({"captured", "failed", "abandoned"}),
    "captured": frozenset(),
    "failed": frozenset(),
    "abandoned": frozenset(),
}

#: Every state an order may hold. Derived from the graph, never a second list —
#: the CHECK constraint in ``007_payments.sql`` is the only other copy and the
#: suite compares the two against a real database.
ORDER_STATES: frozenset[str] = frozenset(_ORDER_TRANSITIONS)


def can_order_transition(current: str, target: str) -> bool:
    return target in _ORDER_TRANSITIONS.get(current, frozenset())


def transition_order(
    conn: Connection, *, order_id: str, current: str, target: str
) -> None:
    """Move an order, refusing every edge not on the graph. **The one writer.**

    Every status write in this service goes through here — fulfilment and
    expiry, the only two callers — so "is this move legal" is asked once rather
    than at each site, and ``terminal_at`` is stamped by the same rule that
    decides terminality. A free-form status write is what
    ``POST /orgs/lifecycle`` refuses one plane up, for the same reason.
    """
    assert_order_transition(current, target)
    store.set_order_status(
        conn, order_id=order_id, status=target,
        terminal=target in ORDER_TERMINAL_STATES,
    )


def abandon_if_expired(conn: Connection, *, order: dict[str, Any]) -> bool:
    """Expire an open order whose clock has run out. Returns True if it did.

    **``abandoned`` is written by expiry, never by the customer** (§9.2): a
    customer who walks away tells you nothing, and an order that stays
    `created` forever makes "how many orders are open" unanswerable. The
    expiry is observed at the next write that touches the order rather than by
    a sweeper this slice does not have — the operator-facing sweep belongs with
    CP-8's console, and a scheduler minted here would have no surface to report
    to.

    Deliberately **not** applied on the capture path: an order that expired
    thirty seconds before the money arrived should be fulfilled, not refused.
    """
    if order["status"] in ORDER_TERMINAL_STATES or not order["expired"]:
        return False
    transition_order(
        conn, order_id=order["id"], current=order["status"],
        target="abandoned",
    )
    return True


def assert_order_transition(current: str, target: str) -> None:
    """Raise :class:`TransitionRefused` unless the move is on the graph."""
    if not can_order_transition(current, target):
        terminal = (
            f"; {current!r} is terminal and nothing leaves it"
            if current in ORDER_TERMINAL_STATES else ""
        )
        raise TransitionRefused(
            f"order {current!r} -> {target!r} is not a permitted "
            f"transition{terminal}"
        )


# ── The provider seam (§9.4) ────────────────────────────────────────────────

#: The three variables, named once. They have **no defaults**: absence is a
#: refusal, not a localhost guess (the `route.ts:34-36` posture). This tuple is
#: what the 503 body lists, so the operator reads what to set rather than
#: "misconfigured".
PROVIDER_ENV_VARS: tuple[str, ...] = (
    "CUSTOMER_CONSOLE_RAZORPAY_KEY_ID",
    "CUSTOMER_CONSOLE_RAZORPAY_KEY_SECRET",
    "CUSTOMER_CONSOLE_RAZORPAY_WEBHOOK_SECRET",
)

_SIGNATURE_HEADER = "x-razorpay-signature"
#: Razorpay carries the event id in a HEADER, not in the body — which is why
#: :meth:`PaymentProvider.verify_webhook` takes the headers at all.
_EVENT_ID_HEADER = "x-razorpay-event-id"


class ProviderUnconfigured(RuntimeError):
    """No provider credentials. The seam refuses; it does not improvise."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            "the payment provider is not configured; set "
            + ", ".join(missing)
        )


@dataclass(frozen=True)
class ProviderOrder:
    """What the provider gave back when we created an order there."""

    provider_order_id: str
    amount_paise: int
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebhookEvent:
    """One signature-verified delivery, already parsed but not yet trusted.

    "Verified" means the bytes came from the provider. It does **not** mean the
    amount agrees with our order — that check is the caller's and it is a
    refusal, never a silent resolution in the customer's favour (§9.5).
    """

    event_id: str
    kind: str
    provider_order_id: str | None
    provider_payment_id: str | None
    provider_customer_id: str | None
    amount_paise: int | None
    body: dict[str, Any]


def compute_signature(secret: str, raw_body: bytes) -> str:
    """Razorpay's webhook signature: HMAC-SHA256 hex over the RAW body.

    One implementation, used by the real provider to verify and by the fake to
    sign. A fake that signed differently would test our parser against our own
    imagination of the algorithm — which is precisely the class of test that
    passes while production rejects every delivery.
    """
    return hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()


def _parse_event(
    raw_body: bytes, headers: Mapping[str, str]
) -> WebhookEvent | None:
    """Shape a verified delivery. ``None`` when it is unusable.

    Called only AFTER the signature verifies, so a failure here is a provider
    contract change rather than an attack — but it is still a refusal, because
    an event with no id has no transport dedup key and an event we cannot parse
    is one we must not act on.
    """
    event_id = (headers.get(_EVENT_ID_HEADER) or "").strip()
    if not event_id:
        return None
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None

    def _entity(kind: str) -> dict[str, Any]:
        # Guarded at EVERY level: a signed body carrying `"payment": "x"`
        # used to raise at `.get` and escape the function that promises
        # "None when it is unusable" — a 500 the provider retries forever
        # instead of the designed 400.
        payload = body.get("payload")
        if not isinstance(payload, dict):
            return {}
        wrapper = payload.get(kind)
        if not isinstance(wrapper, dict):
            return {}
        entity = wrapper.get("entity")
        return entity if isinstance(entity, dict) else {}

    payment = _entity("payment")
    order = _entity("order")
    amount = payment.get("amount", order.get("amount"))
    return WebhookEvent(
        event_id=event_id,
        kind=str(body.get("event") or ""),
        provider_order_id=payment.get("order_id") or order.get("id"),
        provider_payment_id=payment.get("id"),
        # Present only when a Customer object exists at the provider. NULL is
        # the honest record when it does not — see `fulfil`'s note.
        provider_customer_id=payment.get("customer_id"),
        amount_paise=int(amount) if isinstance(amount, int) else None,
        body=body,
    )


class PaymentProvider(Protocol):
    """The seam D19.5 chose. Stripe is its second implementation, later."""

    def create_order(
        self, *, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> ProviderOrder:
        ...

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent | None:
        ...


class RazorpayProvider:
    """Razorpay over its HTTP API with ``httpx``. **No SDK dependency.**

    The integration is two endpoints and one HMAC, and adding a package to a
    *cross-tenant* service is a supply-chain decision rather than a
    convenience (§9.4). If a future call turns out to need the SDK, add it then
    and say why in the PR.
    """

    #: Not configurable. A caller-settable API base is how CP-4 shipped a
    #: credential-exfiltration hole: one request field, our platform key posted
    #: to the caller's host.
    API_BASE = "https://api.razorpay.com/v1"

    def __init__(self, *, key_id: str, key_secret: str,
                 webhook_secret: str, timeout: float = 20.0) -> None:
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self._timeout = timeout

    def create_order(
        self, *, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> ProviderOrder:
        import httpx

        response = httpx.post(
            f"{self.API_BASE}/orders",
            auth=(self._key_id, self._key_secret),
            json={
                "amount": amount_paise,
                "currency": "INR",
                # OUR order id. It is what lets a reconciliation start from
                # either side (CP-8) without a mapping table.
                "receipt": receipt,
                "notes": notes,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return ProviderOrder(
            provider_order_id=str(payload["id"]),
            amount_paise=int(payload["amount"]),
            raw=payload,
        )

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent | None:
        """Verify, THEN parse. ``None`` means refuse — 400, never 200.

        The order of these two statements is the acceptance criterion (§9.5,
        done-when 6): nothing about the body is read until the signature over
        its bytes verifies. A 200-and-ignore would be worse than a refusal —
        a provider that receives 200 stops retrying, so accepting and dropping
        silently loses captured payments.

        ⚠️ **The comparison is over BYTES, and that is not a style choice**
        (repaired 2026-08-19, review P2-2). ``hmac.compare_digest`` on two
        ``str`` arguments is **ASCII-only** and raises ``TypeError`` otherwise;
        Starlette decodes header bytes as latin-1, so a single byte above 0x7F
        in the signature header turned this refusal into an unhandled **500**
        on a route that carries no bearer token by design. Encoding both sides
        keeps the constant-time comparison and makes a hostile header what it
        always was — a value that cannot equal a hex digest.
        """
        presented = _header(headers, _SIGNATURE_HEADER)
        if not presented:
            return None
        expected = compute_signature(self._webhook_secret, raw_body)
        if not hmac.compare_digest(
            presented.encode("utf-8"), expected.encode("utf-8")
        ):
            return None
        return _parse_event(raw_body, headers)


class FakeProvider:
    """The agent-safe provider: fake network, **real signature algorithm**.

    Same shape as ``router.set_provider_call`` (CP-4) and for the same reason —
    a test that needs a real account is a test nobody runs. Here it is worse:
    nobody may *create* the account (§9.7), so without this the entire seam
    would be untestable rather than merely expensive to test.

    What is fake: the HTTP call. What is real: :func:`compute_signature`, the
    header names, the body shape, and every branch of
    :meth:`RazorpayProvider.verify_webhook`, which this class reuses verbatim
    rather than reimplementing.
    """

    def __init__(self, *, key_id: str = "rzp_test_fake",
                 key_secret: str = "fake-secret",
                 webhook_secret: str = "fake-webhook-secret") -> None:
        self.key_id = key_id
        self.webhook_secret = webhook_secret
        self.created: list[dict[str, Any]] = []
        self._real = RazorpayProvider(
            key_id=key_id, key_secret=key_secret,
            webhook_secret=webhook_secret,
        )
        self._counter = 0
        #: Unique per instance, because ``payment_order.provider_order_id`` is
        #: UNIQUE and an R8 suite runs against a database that PERSISTS between
        #: runs. A fake that minted ``order_FAKE000001`` every time would fail
        #: on the second run of the day with an integrity error that says
        #: nothing about the code under test.
        self._run = secrets.token_hex(4)

    def create_order(
        self, *, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> ProviderOrder:
        self._counter += 1
        call = {"amount": amount_paise, "receipt": receipt, "notes": notes}
        self.created.append(call)
        provider_order_id = f"order_FAKE{self._run}{self._counter:04d}"
        return ProviderOrder(
            provider_order_id=provider_order_id,
            amount_paise=amount_paise,
            raw={"id": provider_order_id, "amount": amount_paise,
                 "currency": "INR", "status": "created", "receipt": receipt},
        )

    def verify_webhook(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> WebhookEvent | None:
        return self._real.verify_webhook(raw_body, headers)

    # ── Test-side helpers: they SIGN, they never verify ──────────────────
    def sign(self, raw_body: bytes) -> str:
        """The real HMAC, so the verifier under test is the shipped one."""
        return compute_signature(self.webhook_secret, raw_body)

    def capture_event(
        self, *, provider_order_id: str, amount_paise: int,
        event_id: str, kind: str = "payment.captured",
        payment_id: str = "pay_FAKE0001",
        customer_id: str | None = "cust_FAKE0001",
    ) -> tuple[bytes, dict[str, str]]:
        """A recorded-shape delivery and its headers, signed for real."""
        body = {
            "entity": "event",
            "event": kind,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": provider_order_id,
                        "amount": amount_paise,
                        "currency": "INR",
                        "status": "captured",
                        "customer_id": customer_id,
                    }
                },
                "order": {
                    "entity": {
                        "id": provider_order_id,
                        "amount": amount_paise,
                        "status": "paid",
                    }
                },
            },
        }
        raw = json.dumps(body).encode("utf-8")
        return raw, {
            _SIGNATURE_HEADER: self.sign(raw),
            _EVENT_ID_HEADER: event_id,
        }


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header read — Starlette lower-cases, dicts do not."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


#: The installed provider, or ``None`` to build one from env. A one-element
#: list rather than a module global rebound by tests: the same idiom
#: ``router.set_provider_call`` uses, and for the same reason — a rebound name
#: is invisible to a module that imported it earlier.
_PROVIDER: list[PaymentProvider | None] = [None]


def set_provider(instance: PaymentProvider | None) -> None:
    """Install a provider. **Tests use this; production never does.**"""
    _PROVIDER[0] = instance


def provider() -> PaymentProvider:
    """The configured provider, or raise :class:`ProviderUnconfigured`.

    Refusing is the shipped behaviour, not a degraded one: with no account the
    checkout is 503 and the webhook is 503, which — together with
    ``purchaseEnabled`` staying False — is how CP-9 ships dark. Both are
    *observable*, neither is a flag nobody reads.
    """
    installed = _PROVIDER[0]
    if installed is not None:
        return installed
    values = [os.environ.get(name, "").strip() for name in PROVIDER_ENV_VARS]
    missing = tuple(
        name for name, value in zip(PROVIDER_ENV_VARS, values, strict=True)
        if not value
    )
    if missing:
        raise ProviderUnconfigured(missing)
    return RazorpayProvider(
        key_id=values[0], key_secret=values[1], webhook_secret=values[2],
    )


# ── Fulfilment (§9.6): one function, one transaction, two call sites ────────

#: Catalog kinds that buy CREDITS rather than seats. **Empty of rows today** —
#: `plan_catalog.kind` is CHECK (core|center|addon|bundle) and no pack is
#: priced anywhere (§9.1; pricing the ladder is an owner act). The branch below
#: exists so packs are a catalog row later and not a second code path.
PACK_KINDS: frozenset[str] = frozenset({"pack"})

#: `<reference kind>` -> the LEDGER_REASONS member that names the event.
#: `seat_grant.reason` and (when packs land) `credit_ledger.reason` are written
#: from the same map, so one purchase says one word in both tables.
_REASON_FOR_REFERENCE_KIND: dict[str, str] = {
    "order": LEDGER_REASON_PURCHASE,
    "redemption": LEDGER_REASON_DISCOUNT_REDEMPTION,
}

#: The purchased term. Applied by the DATABASE (``make_interval``) so both
#: paths read one clock — a Python-computed period would differ from the
#: database's by the measured container skew and turn the equivalence fence
#: into a flake that looks like a real difference.
SUBSCRIPTION_TERM_MONTHS = 1


def reason_for(reference: str) -> str:
    """``order:<uuid>`` -> ``purchase``; ``redemption:<uuid>`` -> its own word.

    The composed ``<reason>:<ref>`` is what ``seat_grant.reason`` carries, and
    ``reference.split(":", 1)`` recovers the pair — symmetric and mechanical,
    which is what makes "tell a discounted grant from a paid one a year later"
    a query rather than an archaeology exercise (SC-4g done-when 6).
    """
    kind = reference.split(":", 1)[0]
    try:
        return _REASON_FOR_REFERENCE_KIND[kind]
    except KeyError:
        raise ValueError(
            f"unknown fulfilment reference kind {kind!r}; expected one of "
            f"{sorted(_REASON_FOR_REFERENCE_KIND)}"
        ) from None


def fulfil(conn: Connection, *, order_id: str, reference: str) -> None:
    """Grant what the order's lines imply. **The one fulfilment function.**

    Called from exactly two places — the webhook capture path and SC-4g's
    redemption path — and fenced structurally so a third cannot quietly appear.
    The Rs 0 path is not a second implementation of "grant what was bought"; it
    is this one, with a different ``reference``.

    Writes, in one transaction with the caller's:

    * ``org_subscription`` — status and period for the purchased term. On the
      paid path the three provider columns carry what the provider told us; on
      the Rs 0 path all three stay **NULL**, because no provider was involved
      and NULL is the honest record of that. ⚠️ Never ``'none'`` here: that
      value belongs to ``payment_order.provider``, and
      ``org_subscription.provider`` is CHECK (razorpay|manual).
    * one ``seat_grant`` per package line, carrying ``<reason>:<ref>``.
    * one ``credit_ledger`` row per credit-pack line — **zero at launch**,
      since no pack is sellable (:data:`PACK_KINDS`).

    It does **not** touch ``organization.status``. A suspended organization
    that pays is still suspended the microsecond after this returns, and
    reinstatement stays an operator act through ``POST /orgs/lifecycle``: an
    unattended callback must not drive a state machine whose whole value is
    that a human is on every edge (done-when 16). The named residual — a
    customer who pays at 2am waits for an operator — is accepted at
    one-customer volume and answered by CP-8's queue, never by wiring this
    function into ``lifecycle``.

    Raises :class:`TransitionRefused` when the order is already terminal. That
    is the money guard, and it lives HERE rather than in the callers so that
    neither caller can forget it.
    """
    order = store.order_for_update(conn, order_id=order_id)
    if order is None:
        raise ValueError(f"no such order {order_id!r}")

    # The money guard. `captured` is terminal, so the SECOND event of one
    # capture lands here and stops — which is what the event-id primary key
    # cannot do, because the two events have different ids (§9.5, B8).
    assert_order_transition(order["status"], "captured")

    reason = reason_for(reference)
    grant_reason = f"{reason}:{reference}"

    paid = order["provider"] == "razorpay"
    refs = store.capture_provider_refs(conn, order_id=order_id) if paid else {}
    store.activate_subscription(
        conn,
        org_id=order["organization_id"],
        term_months=SUBSCRIPTION_TERM_MONTHS,
        provider="razorpay" if paid else None,
        # ⚠️ Named residual: Razorpay sends `customer_id` only when a Customer
        # object exists at the provider, so a real capture may carry NULL here
        # and the paid path then differs from the Rs 0 path in two columns
        # rather than three. Recorded rather than papered over with an
        # invented identifier — a payment id in a customer column is worse
        # than a NULL.
        provider_customer_id=refs.get("customer_id") if paid else None,
        # The provider-side reference for the term we sold. There is no
        # Razorpay *Subscription* object in this slice — we sell a term through
        # a one-time order — and the order id is the identifier reconciliation
        # actually joins on. SC-5f's mandates replace it with a real `sub_…`.
        provider_subscription_id=order["provider_order_id"] if paid else None,
    )

    for line in store.order_lines(conn, order_id=order_id):
        if line["kind"] in PACK_KINDS:
            store.add_credit(
                conn, org_id=order["organization_id"],
                delta=Decimal(line["quantity"]), reason=reason,
                ref=reference,
            )
            continue
        store.grant_seats(
            conn, org_id=order["organization_id"], plan_slug=line["plan_slug"],
            quantity=line["quantity"], reason=grant_reason,
        )

    transition_order(
        conn, order_id=order_id, current=order["status"], target="captured",
    )
    _log.info(
        "payments.fulfilled",
        extra={"order": order_id, "fulfil_reason": reason},
    )
