"""The Console's native provider handlers — the seam litellm cannot fill.

Spec: ``project-docs/specs/customer_console.md`` §6A.10b (the handler seam,
H-47) · §6A.14 (the ``decide`` task, CP-13a, D75).

🔴 **Why this module exists.** ``model_capability.invocation`` names how the
Router calls a (model, task) pair. Until CP-13a every value was a litellm
verb. TypeSafe's Jev has no litellm SDK verb — litellm reaches it only
through the Proxy, and D58 rejects the Proxy. So Jev is the first NATIVE
invocation, ``native_typesafe``, and this file is its home (§6A.10b
clause 1).

⚠️ **The plane boundary is the argument for the home.** This module imports
NO tenant package — nothing under ``packages/acb_*``. The Console is
cross-tenant by design, and ``acb_stt`` stays the tenant's own package.
``test_customer_console_decide.py`` fences the import list.

⚠️ **It calls ``httpx`` directly, and never a vendor SDK** (§6A.14 clause 5).
The call is one endpoint and one bearer token. A package in a CROSS-TENANT
service is a supply-chain decision, and ``payments.py`` states the same rule
for Razorpay.

**The shape of a handler** (§6A.10b clause 2). One ``call(task, payload)``
returns a :class:`ProviderResult`. ``payload`` is a typed request object and
never a raw dict from the wire.

🔴 **The vendor's words stay HERE** (§6A.14 clause 4). Our wire says
``boolean``, and the vendor says ``noul``. The vendor's ``legend`` on a score
answer never leaves this file either. So a second vendor costs one handler
and no caller change.

🔴 **ONE System One wire class, two vendors** (CP-13h, 2026-09-24).
:class:`SystemOneHandler` speaks the System One body. Each instance names
its credential prefix, its host, its path, how it sends score levels and
whether it reads a vendor-stated cost. ``native_typesafe`` calls TypeSafe
direct. ``native_aimlapi`` calls the same Jev through the AI/ML API reseller,
because TypeSafe paused signups. Do not add a second wire class for a third
reseller. Add an instance.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx

from customer_console.router import ExtractedUsage, reported_cost_usd

__all__ = [
    "DECIDE_TASK",
    "NATIVE_HANDLERS",
    "QUESTION_TYPES",
    "AimlApiHandler",
    "DecidePayload",
    "NativeHandler",
    "NativeProviderError",
    "ProviderResult",
    "Question",
    "SystemOneHandler",
    "TypeSafeHandler",
    "call_native",
    "native_vendors",
]

_log = logging.getLogger(__name__)

#: The ONE task a native handler serves today (D75).
DECIDE_TASK = "decide"

#: Our three question types, in OUR words. The door refuses anything else
#: before a vendor sees it (§6A.14 clause 13).
QUESTION_TYPES = frozenset({"boolean", "choice", "score"})

#: Our word → the vendor's word. The ONE place ``noul`` is spelled.
_TO_TYPESAFE_TYPE: dict[str, str] = {
    "boolean": "noul",
    "choice": "choice",
    "score": "score",
}

#: The vendor's host, when the credential row names no ``api_base``.
TYPESAFE_API_BASE = "https://api.typesafe.ai"

#: The one vendor endpoint. A System One call is a POST here.
TYPESAFE_PATH = "/v1/systemone"

#: The prefix a bound model id carries (§6A.14 clause 6). The Router reads it
#: to find the credential, and the vendor must not see it.
TYPESAFE_PROVIDER = "typesafe"

#: CP-13h. The AI/ML API reseller, in front of the same Jev. The operator
#: binds ``aimlapi/typesafe/jev``. The Router splits on the FIRST slash, so
#: ``aimlapi`` names the credential, and the vendor sees ``typesafe/jev``.
AIMLAPI_PROVIDER = "aimlapi"
AIMLAPI_API_BASE = "https://api.aimlapi.com"
AIMLAPI_PATH = "/v1/decisions"

#: How a vendor wants the levels of a ``score`` question. ``map`` sends our
#: key → description map as it is. ``list`` sends the descriptions as an
#: ordered array, lowest first, and the vendor answers with level INDEXES.
SCORE_LEVELS_MAP = "map"
SCORE_LEVELS_LIST = "list"

#: How long one decision may take. The vendor states 70 to 500 ms end to
#: end, and we have not measured it. The ceiling is generous on purpose, so
#: a slow answer fails over rather than hangs a worker thread for ever.
DEFAULT_TIMEOUT_SECONDS = 30.0


class NativeProviderError(Exception):
    """A native vendor call failed. It CARRIES ``status_code``.

    🔴 **The attribute is the contract** (§6A.14 clause 12). ``walk_chain``
    reads ``exc.status_code`` to decide failover and the final status. A raw
    ``httpx.HTTPStatusError`` has no such attribute, so a vendor 429 would
    read as None and reach the caller as a 502.

    ⚠️ **``status_code`` is None when the vendor gave no status** — a
    timeout or a dropped connection. ``walk_chain`` treats None as
    retryable, and ``_upstream_refusal`` answers 502.

    🔴 **``terminal`` stops the chain.** A 200 whose body we cannot read has
    already been PAID for at the vendor. ``walk_chain`` reads ``terminal``
    and raises at once, so a second step never pays a second vendor for the
    same request. The caller still reads a 502.

    The message never quotes the vendor's body. That body can quote the
    request, and the request carries customer content.
    """

    def __init__(self, status_code: int | None, reason: str, *, terminal: bool = False) -> None:
        super().__init__(f"native provider failed with {status_code}: {reason}")
        self.status_code = status_code
        self.terminal = terminal


@dataclass(frozen=True)
class Question:
    """One question, in OUR vocabulary. ``type`` is boolean, choice or score."""

    type: str
    instructions: str
    criteria: Mapping[str, str]


@dataclass(frozen=True)
class DecidePayload:
    """The typed request a ``decide`` handler takes (§6A.10b clause 2).

    ``model`` is the BOUND id, prefix and all (``typesafe/jev-1.13.0``). The
    handler strips the prefix before the vendor sees it. ``api_key`` and
    ``api_base`` come from the ``provider_credential`` row, and never from
    the caller.
    """

    model: str
    state: Any
    questions: Mapping[str, Question]
    #: ⚠️ ``repr=False``: a payload in a log line or a traceback must never
    #: print our vendor key.
    api_key: str = field(repr=False)
    api_base: str | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    #: Log context only. They let the unreadable-body alarm join to the
    #: organization and the usage row. The vendor never sees either.
    organization_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    """What a native handler hands back to the door.

    🔴 **A token task carries ``usage`` and NO quantity** (§6A.14 clause 8).
    A quantity sends ``_record_completion`` down the per-unit branch. The
    unit ``tokens`` has no per-unit column, so the cost would be NULL and the
    ``usage_unreadable`` guard would never run. So the vendor's input count
    lands in ``usage.prompt_tokens``, and ``quantity`` stays None.

    ``body`` is already in OUR shape. The door adds the tier and the request
    id, and passes the rest to the caller as it is.
    """

    body: dict[str, Any]
    usage: ExtractedUsage
    quantity: Decimal | None = None
    unit: str | None = None


class NativeHandler(Protocol):
    """Every native handler answers this one call."""

    async def call(self, task: str, payload: DecidePayload) -> ProviderResult: ...


def _vendor_model(model: str, provider: str = TYPESAFE_PROVIDER) -> str:
    """Strip OUR prefix, and only ours. The vendor sees the rest.

    ``typesafe/jev-1.13.0`` → ``jev-1.13.0`` for TypeSafe.
    ``aimlapi/typesafe/jev`` → ``typesafe/jev`` for the reseller, because the
    split is on the FIRST slash and the reseller's own id has a slash too.
    """
    prefix, sep, rest = model.partition("/")
    if sep and prefix == provider and rest:
        return rest
    return model


def _to_typesafe_questions(
    questions: Mapping[str, Question], *, score_levels: str = SCORE_LEVELS_MAP
) -> dict[str, Any]:
    """Our questions, in the vendor's words. ``boolean`` becomes ``noul``.

    With ``score_levels == "list"``, a ``score`` goes out as the ordered
    array of its level descriptions. A blank description sends its key.
    """
    out: dict[str, Any] = {}
    for qid, question in questions.items():
        vendor_type = _TO_TYPESAFE_TYPE.get(question.type)
        if vendor_type is None:
            # The door refuses an unknown type with a 400 before it gets
            # here. Reaching this line is our bug, and the vendor must not
            # pay for it.
            raise NativeProviderError(None, f"unknown question type {question.type!r}")
        criteria: Any = dict(question.criteria)
        if question.type == "score" and score_levels == SCORE_LEVELS_LIST:
            criteria = [desc if desc.strip() else key for key, desc in criteria.items()]
        out[qid] = {
            "type": vendor_type,
            "instructions": question.instructions,
            "criteria": criteria,
        }
    return out


def _probability(value: Any) -> float | None:
    """A number from 0 to 1, or None. A bool is not a probability."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if 0.0 <= number <= 1.0:
        return number
    return None


def _probabilities(value: Any) -> dict[str, float]:
    """The per-option map, rebuilt key by key so nothing else rides along."""
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        number = _probability(raw)
        if number is not None:
            out[str(key)] = number
    return out


def _level_keys(question: Question, probabilities: dict[str, float]) -> dict[str, float]:
    """Index-keyed level probabilities → OUR level keys.

    A ``list`` vendor answers ``{"0": 0.0, "1": 0.7, "2": 0.3}``. The caller
    named the levels, so the caller reads its own keys back. An index out of
    range, or a key that is not an index, is dropped and never guessed.
    """
    keys = list(question.criteria)
    out: dict[str, float] = {}
    for raw_key, number in probabilities.items():
        if not (raw_key.isascii() and raw_key.isdigit()):
            continue
        index = int(raw_key)
        if index < len(keys):
            out[keys[index]] = number
    return out


def _answer(
    question: Question, raw: Any, *, score_levels: str = SCORE_LEVELS_MAP
) -> dict[str, Any]:
    """One vendor answer, in OUR shape.

    🔴 **The TYPE comes from OUR question, and never from the vendor's
    answer.** So ``noul`` cannot reach the caller, whatever the vendor
    sends. Each output dict is BUILT from named fields, so the vendor's
    ``legend`` and any field it adds later stay here too.

    Raises:
        NativeProviderError: the answer is missing, or it is not a shape we
            can read. The caller gets a 502, never a guess.
    """
    if question.type == "boolean":
        # The vendor answers a `noul` with one probability. Accept it bare,
        # or inside an object. CP-13h: both vendors document the field as
        # `noul` (`{"type": "noul", "noul": 0.96}`), so read that first.
        probability = _probability(raw)
        if probability is None and isinstance(raw, Mapping):
            probability = _probability(raw.get("noul"))
        if probability is None and isinstance(raw, Mapping):
            probability = _probability(raw.get("probability"))
        if probability is None:
            raise NativeProviderError(None, "unreadable boolean answer")
        return {"type": "boolean", "probability": probability}

    if not isinstance(raw, Mapping):
        raise NativeProviderError(None, f"unreadable {question.type} answer")
    confidence = _probability(raw.get("confidence"))

    if question.type == "choice":
        choice = raw.get("choice")
        if not isinstance(choice, str):
            raise NativeProviderError(None, "unreadable choice answer")
        return {
            "type": "choice",
            "choice": choice,
            "probabilities": _probabilities(raw.get("probabilities")),
            "confidence": confidence,
        }

    # score. The vendor's `legend` is NOT copied.
    # 🔴 CP-13h: a score may be FRACTIONAL. Both vendors say it is a
    # probability-weighted position that can land between levels (1.3). A
    # float is read, never refused: refusing it was a TERMINAL 502 after the
    # vendor had already charged us. A bool is not a score, and neither is
    # NaN or infinity.
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, str | int | float):
        raise NativeProviderError(None, "unreadable score answer")
    if isinstance(score, float) and not math.isfinite(score):
        raise NativeProviderError(None, "unreadable score answer")
    probabilities = _probabilities(raw.get("probabilities"))
    if score_levels == SCORE_LEVELS_LIST:
        probabilities = _level_keys(question, probabilities)
    return {
        "type": "score",
        "score": score,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def _token_count(value: Any) -> int:
    """A non-negative integer count, or zero. Zero is the unreadable signal."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _meta_cost(data: Mapping[str, Any]) -> Decimal | None:
    """The reseller's own charge, ``meta.usage.usd_spent``, or None.

    ⚠️ **Never raises, and never fails the call.** A missing or odd figure
    is None, and the meter then computes the cost from the profile.
    ``router.reported_cost_usd`` is the one parse.
    """
    meta = data.get("meta")
    if not isinstance(meta, Mapping):
        return None
    usage = meta.get("usage")
    if not isinstance(usage, Mapping):
        return None
    return reported_cost_usd(usage.get("usd_spent"))


def _from_typesafe(
    data: Any,
    questions: Mapping[str, Question],
    *,
    score_levels: str = SCORE_LEVELS_MAP,
    reads_cost: bool = False,
) -> ProviderResult:
    """The vendor's body → our body plus the usage the meter reads.

    With ``reads_cost``, the vendor's own figure lands in
    ``vendor_reported_cost_usd``, and the meter prefers it
    (``cost_source = 'vendor'``). The ``meta`` block never reaches the body.
    """
    if not isinstance(data, Mapping):
        raise NativeProviderError(None, "unreadable body")
    raw_answers = data.get("answers")
    if not isinstance(raw_answers, Mapping):
        raise NativeProviderError(None, "no answers in the body")

    answers: dict[str, Any] = {}
    for qid, question in questions.items():
        if qid not in raw_answers:
            raise NativeProviderError(None, "an answer is missing")
        answers[qid] = _answer(question, raw_answers[qid], score_levels=score_levels)

    raw_usage = data.get("usage")
    raw_usage = raw_usage if isinstance(raw_usage, Mapping) else {}
    input_tokens = _token_count(raw_usage.get("input_tokens"))
    output_tokens = _token_count(raw_usage.get("output_tokens"))

    return ProviderResult(
        body={
            "answers": answers,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
        # 🔴 Input → prompt, output → completion (clause 8). A zero prompt
        # makes `_record_completion` flag `usage_unreadable` and bill zero,
        # which is the loud answer for a body that reported no input.
        usage=ExtractedUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            vendor_reported_cost_usd=_meta_cost(data) if reads_cost else None,
        ),
    )


class SystemOneHandler:
    """One System One call, for any vendor that speaks the body (CP-13h).

    Each instance names its vendor. ``provider`` is the credential prefix it
    strips. ``api_base`` is the host when the credential names none, and
    ``path`` is the endpoint. ``score_levels`` says how score levels go out.
    ``reads_cost`` reads the vendor's own charge from ``meta.usage``.

    ``transport`` exists so a test can answer with a recorded vendor body and
    no network. Production passes none.
    """

    def __init__(
        self,
        *,
        provider: str,
        api_base: str,
        path: str,
        score_levels: str = SCORE_LEVELS_MAP,
        reads_cost: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if score_levels not in (SCORE_LEVELS_MAP, SCORE_LEVELS_LIST):
            raise ValueError(f"unknown score_levels {score_levels!r}")
        self.provider = provider
        self.api_base = api_base
        self.path = path
        self.score_levels = score_levels
        self.reads_cost = reads_cost
        self._transport = transport

    async def call(self, task: str, payload: DecidePayload) -> ProviderResult:
        if task != DECIDE_TASK:
            raise NativeProviderError(
                None, f"{self.provider} serves {DECIDE_TASK!r}, not {task!r}"
            )

        body = {
            "state": payload.state,
            "model": _vendor_model(payload.model, self.provider),
            "questions": _to_typesafe_questions(
                payload.questions, score_levels=self.score_levels
            ),
        }
        url = (payload.api_base or self.api_base).rstrip("/") + self.path

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=payload.timeout
            ) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {payload.api_key}"},
                )
        except httpx.HTTPError as exc:
            # No status: a timeout or a dropped connection. Retryable.
            raise NativeProviderError(None, type(exc).__name__) from exc

        if response.status_code >= 400:
            # 401 key, 422 validation, 429 rate limit, 529 overloaded. The
            # status travels, and the body does not.
            _log.warning(
                "handlers.vendor_refused",
                extra={"vendor": self.provider, "vendor_status": response.status_code},
            )
            raise NativeProviderError(response.status_code, "vendor refused")

        # 🔴 **A 200 we cannot read is TERMINAL.** The vendor has answered,
        # so it has charged us. Another step would pay a second vendor for
        # the same request. The caller reads a 502 and no usage row is
        # written, so this line is the only record of the call. It names
        # the status, the organization and the request, and never the body
        # or the key.
        try:
            return _from_typesafe(
                response.json(),
                payload.questions,
                score_levels=self.score_levels,
                reads_cost=self.reads_cost,
            )
        except (ValueError, NativeProviderError) as exc:
            reason = str(exc) if isinstance(exc, NativeProviderError) else "body is not JSON"
            _log.error(
                "handlers.vendor_unreadable",
                extra={
                    "vendor": self.provider,
                    "upstream_status": response.status_code,
                    "router_org": payload.organization_id,
                    "router_request": payload.request_id,
                },
            )
            raise NativeProviderError(None, reason, terminal=True) from exc


class TypeSafeHandler(SystemOneHandler):
    """``native_typesafe``: one System One call to TypeSafe direct (D75).

    ⚠️ **Byte for byte as CP-13a built it.** Score levels still go out as a
    map. The vendor's API reference, read 2026-09-24, documents an ordered
    ARRAY. §6A.14 CP-13h records that finding, and this slice does not act
    on it.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(
            provider=TYPESAFE_PROVIDER,
            api_base=TYPESAFE_API_BASE,
            path=TYPESAFE_PATH,
            transport=transport,
        )


class AimlApiHandler(SystemOneHandler):
    """``native_aimlapi``: the same Jev, through the AI/ML API reseller (CP-13h).

    Score levels go out as the ordered array the reseller documents, and the
    reseller's ``meta.usage.usd_spent`` is the vendor-reported cost.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(
            provider=AIMLAPI_PROVIDER,
            api_base=AIMLAPI_API_BASE,
            path=AIMLAPI_PATH,
            score_levels=SCORE_LEVELS_LIST,
            reads_cost=True,
            transport=transport,
        )


#: The handler table (§6A.10b clause 1). The key is the value an operator
#: writes into ``model_capability.invocation``, and ``KNOWN_INVOCATIONS`` and
#: ``SERVING_INVOCATIONS`` must both hold it.
NATIVE_HANDLERS: dict[str, NativeHandler] = {
    "native_typesafe": TypeSafeHandler(),
    "native_aimlapi": AimlApiHandler(),
}


def native_vendors() -> frozenset[str]:
    """The credential prefixes the native handlers answer to.

    Read from the table, so a fence never types a second list of vendors.
    """
    return frozenset(
        vendor
        for handler in NATIVE_HANDLERS.values()
        if isinstance(vendor := getattr(handler, "provider", None), str)
    )


async def call_native(
    *,
    invocation: str,
    task: str,
    payload: DecidePayload,
    **_route_fields: Any,
) -> ProviderResult:
    """Dispatch one native call to the handler its capability row names.

    ``router._default_provider_call`` is the one caller. The door's other
    kwargs (``model``, for the failover log) are not the handler's business.
    """
    handler = NATIVE_HANDLERS.get(invocation)
    if handler is None:
        raise NativeProviderError(None, f"no native handler named {invocation!r}")
    return await handler.call(task, payload)
