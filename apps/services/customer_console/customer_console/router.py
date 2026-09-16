"""The AI Router — tier resolution and provider pass-through (CP-4).

Spec: ``project-docs/specs/customer_console.md`` §4 · D32.1 / D32.7.

**This slice deliberately does not price anything.** It forwards, it counts, and
it writes a ``usage_event`` with ``billed_credits = 0``. The rate card is set in
CP-6, *after* a month of real per-organization burn exists — a rate card built on
estimates is one you change on customers who have already seen a number. The
sequencing is the point, not an omission.

Two things it establishes that everything downstream needs:

  * **The tier is the only vocabulary.** A caller names ``tier-balanced``; this
    module resolves it to a concrete model from the ``tier_binding`` table. A
    provider swap becomes one row here rather than a deploy to every customer
    deployment — which is the whole argument for a central Router (D32.7).
  * **The org comes from the key.** Usage is attributed to whoever the API key
    resolved to, never to anything in the request.

The provider call sits behind :data:`_PROVIDER_CALL` so the pass-through can be
tested without a provider account. That is not a testing convenience bolted on:
without it the only way to test the Router is to spend money at DeepSeek, which
means in practice nobody tests it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from customer_console.credits import RateCard, TierRate, UnpricedModel

__all__ = [
    "CHAT_TASK",
    "DEFAULT_SPEECH_MEDIA_TYPE",
    "SERVING_INVOCATIONS",
    "SSE_DONE",
    "TRANSCRIPTION_RESPONSE_FORMAT",
    "VISION_TASK",
    "VISION_TIER",
    "Credential",
    "ResolvedTier",
    "TierUnknown",
    "UnservableInvocation",
    "VisionUnbound",
    "call_provider",
    "decrypt_secret",
    "duration_seconds",
    "encrypt_secret",
    "frame_of",
    "image_count",
    "provider_credential",
    "reads_images",
    "relay_stream",
    "resolve_invocation",
    "resolve_rate_card",
    "resolve_tier",
    "resolve_tier_rate",
    "set_provider_call",
    "speech_audio",
    "usage_from_frame",
    "usage_from_response",
    "vendor_cost_per_unit_usd",
    "vendor_cost_usd",
]


#: The task a caller declares when it sends an image (D-AI-2). Priced in
#: tokens, exactly like ``chat`` (`010_tasks_units_capabilities.sql`:46).
VISION_TASK = "vision"

#: The task a chat model serves. D-AI-2 reads this binding FIRST, because a
#: chat model that reads images costs one call and a second model costs two.
CHAT_TASK = "chat"

#: The tier that holds a dedicated image-reading model. §3.3 keeps it out of
#: the customer picker: no caller ever names it, and :func:`resolve_vision_chain`
#: is the only thing that resolves it.
VISION_TIER = "tier-vision"


class TierUnknown(Exception):
    """The requested tier has no binding.

    Raised rather than defaulted. ``_byok_default_model`` in the tenant
    orchestrator silently coerces an unknown model to ``tier-balanced`` and logs
    it; that was right for a personal application where the operator was the
    user, and it is wrong for a platform, where it hides a misconfigured agent
    behind a bill (D32.7).
    """


@dataclass(frozen=True)
class ResolvedTier:
    tier: str
    model: str
    #: Which task this binding serves. Resolution is two steps as of
    #: D60: (task, tier) -> model, then (model, task) -> invocation.
    task: str = "chat"
    #: Position in the fallback chain. 1 is the primary. Carried so the step
    #: that ANSWERS can be recorded as evidence (migration 013) — deriving it
    #: later by joining `tier_binding` history breaks the day a chain is
    #: re-bound, which is exactly when somebody reads the history.
    rank: int = 1


def resolve_tier(conn: Connection, tier: str, task: str = "chat") -> ResolvedTier:
    """Resolve a tier alias to the model currently bound to it.

    Picks the newest binding whose ``effective_from`` has passed, so a future
    dated row can be staged without taking effect — the same shape as
    ``seat_grant`` and ``model_rate_card``. Repricing and re-pointing are both
    "insert a row with a later date", never "edit the live one", so history
    stays reconstructable.

    ⚠️ **``rank ASC`` is the tiebreak, and it is load-bearing.** A tier holds
    an ordered CHAIN as of migration 011, and every step of one chain shares an
    ``effective_from``. Without the rank tiebreak this returns an arbitrary
    step — most often the wrong one, and only under the multi-step chains that
    are the whole point of the feature. This function returns the PRIMARY;
    :func:`resolve_chain` returns the whole thing.
    """
    row = conn.execute(
        text(
            """
            SELECT model FROM tier_binding
            WHERE tier = :tier AND task = :task AND effective_from <= now()
            ORDER BY effective_from DESC, rank ASC
            LIMIT 1
            """
        ),
        {"tier": tier, "task": task},
    ).first()
    if row is None:
        # ⚠️ An UNBOUND TASK IS A 400, never a coercion to the chat binding
        # (§6A.9 rule 2). Serving an image request from a text model is D32.7's
        # "silent coercion hides a misconfigured agent behind a bill" wearing
        # different clothes, and the customer gets a paragraph where they asked
        # for a picture.
        raise TierUnknown(f"no binding for tier {tier!r} on task {task!r}")
    return ResolvedTier(tier=tier, model=row[0], task=task)


def resolve_chain(conn: Connection, tier: str, task: str = "chat") -> list[ResolvedTier]:
    """Every model bound to this ``(task, tier)``, in the order to try them.

    🔴 **This is what a fallback IS.** The Router walks this list and stops at
    the first step that answers, so a provider being overloaded costs a retry
    instead of costing the customer their request.

    ⚠️ **One chain, one ``effective_from``.** The whole chain is written at a
    single timestamp (migration 011), so this takes the newest timestamp that
    has passed and then every row at it. Reading "the newest row per rank"
    instead would silently splice two different chains together — half of
    yesterday's and half of today's — which is a configuration nobody chose and
    nobody could reproduce from the audit trail.

    Raises:
        TierUnknown: nothing is bound. Same refusal as :func:`resolve_tier`,
            and for the same reason (§6A.9 rule 2): coercing an unbound task to
            the chat binding hides a misconfigured agent behind a bill.
    """
    rows = conn.execute(
        text(
            """
            SELECT model, rank FROM tier_binding
            WHERE tier = :tier AND task = :task
              AND effective_from = (
                  SELECT max(effective_from) FROM tier_binding
                  WHERE tier = :tier AND task = :task
                    AND effective_from <= now()
              )
            ORDER BY rank ASC
            """
        ),
        {"tier": tier, "task": task},
    ).all()
    if not rows:
        raise TierUnknown(f"no binding for tier {tier!r} on task {task!r}")
    return [ResolvedTier(tier=tier, model=r[0], task=task, rank=int(r[1])) for r in rows]


# ── D-AI-2: an image follows the chat model when it can (§3.2) ──────────────


class VisionUnbound(Exception):
    """Both halves of the image wall are down at once (§3.2 step 4).

    The chosen tier has a chat model that does not read images, AND nothing
    binds :data:`VISION_TIER`. So no model in the system can see the image.

    ⚠️ **A separate class from :class:`TierUnknown` because the SENTENCE
    differs.** The route answers 400 either way, and the customer must be told
    WHICH half to fix — a tier that binds nothing at all is a different repair
    from a tier whose chat model is text-only. The refusal SLUG stays
    ``tier_unknown`` (`020_usage_refusal.sql`'s CHECK closes the vocabulary at
    three), because the wording of an HTTP detail and the slug the meter
    records are two different things.
    """


def reads_images(conn: Connection, model: str) -> bool:
    """Does this model read an image itself? ``model_profile.reads_images``.

    🔴 **ONE source for the flag** (§3.2). ``model_capability`` holds no
    ``vision`` row for any model, so a capability read answers nothing, and two
    sources for one fact is how the two start to disagree.

    A model with NO profile row answers FALSE. Nobody has told us the model
    reads images, and D-AI-2 then routes the image to a model that certainly
    does rather than to one that may drop it in silence.
    """
    row = conn.execute(
        text("SELECT reads_images FROM model_profile WHERE model = :m"),
        {"m": model},
    ).first()
    return bool(row and row[0])


def _models_that_read_images(conn: Connection, models: Sequence[str]) -> set[str]:
    """The subset of *models* that reads an image, in ONE query.

    🔴 **The chain resolves on the serving path, and inside the serving
    transaction.** :func:`reads_images` answers for one model, so a filter
    built on it runs one single-row SELECT per step. Measured on a five-step
    chain: the resolve cost 7 queries before this helper and costs 3 after it.
    The schema puts no ceiling on chain length, and ``MAX_CHAIN_ATTEMPTS`` caps
    the WALK rather than the resolve, so that cost grows with the chain an
    operator binds.

    ⚠️ **Three states read the same, and they must.** A model with no
    ``model_profile`` row is absent from the result. A SQL NULL flag fails
    ``AND reads_images``. A FALSE flag fails it too. All three mean *does not
    see*, exactly as :func:`reads_images` answers for one model.

    ⚠️ **This function is the ONE reader on the serving path, and
    :func:`reads_images` has NO caller today** (measured 2026-08-31, whole
    tree, source and tests). The single-model form stays in ``__all__`` for a
    caller that holds exactly one model. Such a caller reads the flag through
    this module, and it writes no second SELECT against ``model_profile``.
    This is the set form of the same question, and never a second source for
    the flag.
    """
    wanted = list(models)
    if not wanted:
        return set()
    rows = conn.execute(
        text("SELECT model FROM model_profile WHERE model = ANY(:models) AND reads_images"),
        {"models": wanted},
    ).all()
    return {r[0] for r in rows}


def resolve_vision_chain(conn: Connection, tier: str) -> list[ResolvedTier]:
    """Which chain serves a ``task: vision`` call on *tier* (D-AI-2, §3.2).

    🔴 **Nothing here reads the payload.** The CALLER declares the task (G-3,
    D61). This function takes a tier slug and a connection, and it never sees
    ``messages`` — inferring the task from an ``image_url`` part is exactly the
    inference D32.7 is hostile to.

    🔴 **A TIER THAT BINDS THE DECLARED TASK SERVES IT DIRECTLY** (§3.2 step
    0.5). The image rule runs only for a tier that does not. This is the first
    read below, and it is not an optimisation: a caller that names
    :data:`VISION_TIER` itself, or any second vision tier an operator adds,
    binds ``vision`` and binds no ``chat`` model at all. Reading the chat
    binding first told that caller *"no binding for tier 'tier-vision' on task
    'vision'"*, which is FALSE — the binding it names is right there. Measured
    on 2026-08-31, and it was a 200 before this slice.

    Three answers, and the money is the reason for the second one:

    1. The chosen tier's own ``vision`` chain, when the tier binds one. The
       call bills (chosen tier, ``vision``), exactly as it did before D-AI-2.
    2. The chosen tier's CHAT chain, FILTERED to the steps that set
       ``reads_images``. One model answers, and the call bills the (chosen
       tier, ``chat``) pair. A second call to a vision model would cost a
       second call.
    3. The :data:`VISION_TIER` chain otherwise, billing (``tier-vision``,
       ``vision``). This is a capability LIFT and not a degradation, so §6A.9
       rule 1 does not forbid it: the tier the customer picked does not drop,
       and every chat turn beside it stays where it was.

    🔴 **A BLIND STEP NEVER ENTERS THE LIFT CHAIN** (§3.2 step 3b, D16). The
    flag is read on EVERY step of the chat chain, and a step that clears it is
    dropped. An empty result falls to :data:`VISION_TIER`, exactly as a chain
    whose every step clears the flag does. ONE query answers for the whole
    chain (:func:`_models_that_read_images`), because this resolves on the
    serving path and a per-step read cost one query per rank.

    ⚠️ **The rank-1 read was a shipped wrong answer, and this is the repair.**
    Rank 1 reads images, rank 1 fails, and the walk moves to a blind rank 2.
    That model then answers about a picture it never saw, with a confident 200.
    The route also drops every step it holds no key for, so an UNKEYED rank 1
    made the blind rank 2 the FIRST step, with nothing having failed. A wrong
    answer is worse than a refusal, which is the whole reason §3.2 step 4
    refuses rather than serving.

    ⚠️ **The fall is a RESOLUTION act, and never a failover act.** This
    function picks the chain before the walk starts. A step that fails at
    runtime therefore falls to the next SEEING step and to nothing else. A
    chain that spliced two tiers together would bill two pairs out of one walk,
    and §3.2 records no decision on that.

    Raises:
        TierUnknown: the chosen tier binds neither ``vision`` nor ``chat``
            (§3.2 step 0). The caller answers this with the wall it already
            had, unchanged.
        VisionUnbound: no step of the chat chain reads an image, and nothing
            binds :data:`VISION_TIER`. Both halves are down, and the sentence
            names both. The wording stays singular *"the chat model for tier
            <slug>"*, because §3.2 step 4 fixes it word for word.
    """
    try:
        return resolve_chain(conn, tier, VISION_TASK)
    except TierUnknown:
        # The tier serves no vision task of its own, so D-AI-2 decides. Handled
        # here rather than re-raised, because the chat binding below may still
        # answer and a `raise` would end the resolution on a wall that is not
        # yet a wall.
        pass
    chat_chain = resolve_chain(conn, tier, CHAT_TASK)
    # ONE query for the whole chain, and the comprehension keeps the RANK
    # order. The result is a SUBSEQUENCE of the chain, which is what makes
    # `served_rank` true — `ResolvedTier.rank` comes off the column and never
    # off a list index.
    sees = _models_that_read_images(conn, [s.model for s in chat_chain])
    seeing = [s for s in chat_chain if s.model in sees]
    if seeing:
        return seeing
    try:
        return resolve_chain(conn, VISION_TIER, VISION_TASK)
    except TierUnknown as unbound:
        raise VisionUnbound(
            f"no vision model is bound; the chat model for tier {tier} does not read images"
        ) from unbound


# ── The rate card (CP-6) ────────────────────────────────────────────────────


def resolve_rate_card(conn: Connection, model: str, task: str = "chat") -> RateCard:
    """The rate card in force for one model, as of now.

    Deliberately the same shape as :func:`resolve_tier`: newest row whose
    ``effective_from`` has passed. Repricing is *"insert a row with a later
    date"*, never *"edit the live one"*, so a past invoice is never recomputed
    against today's card — rating happens once, at write time.

    Raises:
        UnpricedModel: no row is in effect for this model. Refusing is the
            point (see :class:`customer_console.credits.UnpricedModel`): a model
            the card does not price is an operational mistake, and billing it
            confidently as free looks like revenue working while the margin
            leaks. The *caller* on the metering path downgrades this to "bill
            zero, loudly" — because a metering failure must never fail a
            completion — but the decision is made there, visibly, not hidden
            here behind a default of zero.
    """
    row = conn.execute(
        text(
            """
            SELECT input_credits_per_1k, output_credits_per_1k,
                   cached_input_credits_per_1k,
                   unit, credits_per_unit, pricing_mode
            FROM model_rate_card
            WHERE model = :model AND task = :task AND effective_from <= now()
            ORDER BY effective_from DESC
            LIMIT 1
            """
        ),
        {"model": model, "task": task},
    ).first()
    if row is None:
        raise UnpricedModel(
            f"{model!r} has no rate-card row in effect for task {task!r}; "
            "refusing to bill it as free"
        )
    return RateCard(
        model=model,
        # ⚠️ `model_rate_card` gains NO per-million columns and never will.
        # D67.2 retired it as a billing input and the table stays only so a
        # past invoice reads back (R6). So this converts on the way out, and
        # the domain object carries ONE scale whichever card it came from —
        # which is the property that lets `rate_call` price either without
        # knowing which it holds.
        input_per_1m=_per_1m(None, row[0]),
        output_per_1m=_per_1m(None, row[1]),
        cached_input_per_1m=_per_1m(None, row[2]),
        task=task,
        unit=row[3],
        credits_per_unit=row[4],
        pricing_mode=row[5],
    )


#: The scale the per-thousand columns are multiplied by to reach per million.
#: Named once, because a bare 1000 appearing twice is how the two copies
#: eventually disagree.
_PER_1K_TO_PER_1M = Decimal(1000)


def _per_1m(per_1m: Decimal | None, per_1k: Decimal | None) -> Decimal:
    """The per-million rate, from either column. Migration 025, release one.

    🔴 **The per-million column wins whenever it holds a number**, and the
    per-thousand column is the fallback for a row written before 024 applied,
    or by a service that has not restarted yet.

    ⚠️ **Zero is a NUMBER and must not fall through.** ``or`` would treat a
    legitimate zero rate — an absorbed task, a free tier — as missing and
    silently reach for the other column. The explicit ``is None`` is the whole
    difference, and `test_customer_console_tier_pricing.py` pins it.

    Both absent answers zero rather than raising: `pricing_mode` already
    decides whether a card may be billed at all, and a second refusal here
    would fire on the `absorbed` rows D19.2 puts there on purpose.
    """
    if per_1m is not None:
        return per_1m
    if per_1k is not None:
        return per_1k * _PER_1K_TO_PER_1M
    return Decimal(0)


def resolve_tier_rate(conn: Connection, tier: str, task: str) -> TierRate:
    """The TIER rate card in force, as of now — what the customer pays (D67).

    Same idiom as :func:`resolve_rate_card`, re-keyed: newest row whose
    ``effective_from`` has passed. The customer's price is keyed on the tier
    they PICKED, never on the model that served them, so a failover moves our
    cost and not their bill.

    Raises:
        UnpricedModel: no row is in effect for this (tier, task). The
            metering caller downgrades this to "bill zero, loudly" — a
            completion the customer already has must never fail on rating.
    """
    row = conn.execute(
        text(
            """
            SELECT unit, credits_per_unit, pricing_mode,
                   input_credits_per_1m, output_credits_per_1m,
                   cached_input_credits_per_1m
            FROM tier_rate_card
            WHERE tier = :tier AND task = :task AND effective_from <= now()
            ORDER BY effective_from DESC
            LIMIT 1
            """
        ),
        {"tier": tier, "task": task},
    ).first()
    if row is None:
        raise UnpricedModel(
            f"tier {tier!r} has no rate-card row in effect for task "
            f"{task!r}; refusing to bill it as free"
        )
    return TierRate(
        tier=tier,
        # 🔴 Per MILLION, and there is no per-thousand column left to fall back
        # to — migration 030 dropped them. The fallback existed for the window
        # in which new code could meet the old schema, and release one closed
        # it.
        #
        # ⚠️ `_per_1m` still normalises NULL to zero. A row that predates the
        # backfill would otherwise rate as NULL and bill zero in silence, which
        # is the shape slice 1 exists to stop.
        input_per_1m=_per_1m(row[3], None),
        output_per_1m=_per_1m(row[4], None),
        cached_input_per_1m=_per_1m(row[5], None),
        task=task,
        unit=row[0],
        credits_per_unit=row[1],
        pricing_mode=row[2],
    )


def resolve_invocation(conn: Connection, model: str, task: str) -> str:
    """Which provider verb serves this ``(model, task)``. D60 step two.

    Replaces ``_STT_TIER_IDS: frozenset({"stt"})`` in ``acb_llm/client.py``,
    which meant ``tier-image`` would be handed to ``acompletion`` and rejected
    by the provider (D60.2). A frozenset cannot grow a row.

    ⚠️ **Capability is not availability** (§6A.9 rule 3). This answers what the
    model CAN do. ``tier_binding`` decides what we USE it for, and the gap
    between the two is the operator's most valuable view.

    Raises:
        TierUnknown: the pair has no capability row. Refusing is the point —
            guessing ``acompletion`` is how audio reaches a chat endpoint.
    """
    row = conn.execute(
        text(
            """
            SELECT invocation FROM model_capability
            WHERE model = :model AND task = :task
            """
        ),
        {"model": model, "task": task},
    ).first()
    if row is None:
        raise TierUnknown(f"{model!r} declares no capability for task {task!r}")
    return row[0]


# ── Provider credentials ────────────────────────────────────────────────────


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.environ.get("CUSTOMER_CONSOLE_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError(
            "CUSTOMER_CONSOLE_ENCRYPTION_KEY is not set. Provider secrets are "
            "encrypted at rest and there is deliberately no default — a default "
            "key is the same as no encryption, while looking like encryption."
        )
    # Derive a valid Fernet key from an arbitrary-length secret, so operators
    # are not required to generate base64 of exactly 32 bytes by hand.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


class Credential(NamedTuple):
    """One resolved provider credential, and WHOSE account it is.

    ⚠️ A NamedTuple rather than a dataclass so the callers that index
    ``cred[0]``/``cred[1]`` (several tests, and the previous tuple shape)
    keep working. New code reads the names.
    """

    secret: str
    api_base: str | None
    #: TRUE when the row is the ORGANIZATION'S own vendor account. §3.4: such
    #: a call is metered but billed zero, and our provider cost for it is
    #: zero — we paid the vendor nothing. The rater cannot honour either rule
    #: without this bit, which is why it travels WITH the secret rather than
    #: being re-derived at metering time from a table that may have rotated.
    byok: bool


def provider_credential(
    conn: Connection, *, provider: str, org_id: str | None = None
) -> Credential | None:
    """The live credential for a provider, with whose account it is.

    Prefers the organization's OWN credential when it has one — that is BYOK
    (§3.4): a customer insisting on their own provider account is metered but
    not charged for tokens, which also caps our exposure on the largest
    accounts. Falls back to the platform's account (``organization_id IS NULL``).
    """
    row = conn.execute(
        text(
            """
            SELECT secret_enc, api_base, organization_id IS NOT NULL
            FROM provider_credential
            WHERE provider = :provider AND revoked_at IS NULL
              AND (organization_id = CAST(:org AS uuid) OR organization_id IS NULL)
            ORDER BY organization_id NULLS LAST
            LIMIT 1
            """
        ),
        {"provider": provider, "org": org_id},
    ).first()
    if row is None:
        return None
    return Credential(decrypt_secret(row[0]), row[1], bool(row[2]))


# ── The provider call, behind a seam ────────────────────────────────────────

ProviderCall = Callable[..., Awaitable[Any]]

#: The litellm verbs a SERVING route may dispatch to today.
#:
#: ⚠️ **Narrower than ``catalog.KNOWN_INVOCATIONS`` on purpose.** That set is
#: what an operator may WRITE into ``model_capability``. This one is what the
#: Router can CALL. A capability row may legally name a verb no route serves
#: yet — §6A.9 rule 3 says capability is not availability — and the gap must
#: raise here rather than reach litellm as an attribute nobody checked.
#:
#: 🔴 **``aimage_generation`` and ``aspeech`` joined on 2026-08-31** (§6A.10c
#: clause 9). Each one has a door now, so the Router may call it. The set
#: stays a STRICT subset of ``KNOWN_INVOCATIONS``: ``aembedding`` has no
#: serving route, and an operator may still declare it.
SERVING_INVOCATIONS = frozenset(
    {
        "acompletion",
        "atranscription",
        "aimage_generation",
        "aspeech",
    }
)

#: The verb a caller that names none gets. Every chat call made this exact
#: request before ``invocation`` existed, so the default keeps that path byte
#: for byte the same.
DEFAULT_INVOCATION = "acompletion"


class UnservableInvocation(Exception):
    """The capability row names a verb no route serves yet.

    Raised rather than defaulted to ``acompletion``. Guessing is how audio
    reaches a chat endpoint (D60.2), and the provider answers that with a
    charge and a paragraph.
    """


async def _litellm_call(**kwargs: Any) -> Any:
    """Send one call to litellm through the verb the capability row names.

    🔴 **``invocation`` is the Router's OWN instruction and never a provider
    parameter.** It is removed here, so litellm and the vendor behind it see
    exactly the keys the route built. ``resolve_invocation`` reads the name
    off ``model_capability`` (D60 step two), and the route passes it in.

    ⚠️ **The verb is looked up through an allowlist, not by free attribute
    access.** A capability row is operator-written data, and
    ``getattr(litellm, <anything>)`` on operator data is a way to call a
    function nobody reviewed.
    """
    import litellm

    invocation = kwargs.pop("invocation", DEFAULT_INVOCATION)
    if invocation not in SERVING_INVOCATIONS:
        raise UnservableInvocation(
            f"no serving route calls {invocation!r}; this Router serves "
            f"{', '.join(sorted(SERVING_INVOCATIONS))}"
        )
    return await getattr(litellm, invocation)(**kwargs)


_PROVIDER_CALL: list[ProviderCall] = [_litellm_call]


def set_provider_call(fn: ProviderCall) -> None:
    """Swap the provider call. Tests use this; production never does."""
    _PROVIDER_CALL[0] = fn


async def call_provider(**kwargs: Any) -> Any:
    return await _PROVIDER_CALL[0](**kwargs)


# ── Usage extraction ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractedUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    #: WHICH vendor convention reported ``cached_tokens``, or None when no
    #: cached count arrived. ``subset`` means the count sits INSIDE
    #: ``prompt_tokens`` (the OpenAI-compatible shape, read from
    #: ``prompt_tokens_details.cached_tokens``). ``sibling`` means it sits
    #: BESIDE them (the Anthropic shape, read from ``cache_read_input_tokens``).
    #:
    #: 🔴 **Recorded rather than acted on, on purpose.** The billing code
    #: subtracts, which is right for ``subset`` and wrong for ``sibling``.
    #: Re-normalising on a guess would bill wrong in the other direction, so
    #: `credit_pricing.md` §3 refuses the impossible case and stores this
    #: instead — the fleet gets MEASURED before anybody changes the arithmetic.
    cache_convention: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def vendor_cost_usd(
    usage: ExtractedUsage,
    *,
    input_per_1m: Decimal | None,
    output_per_1m: Decimal | None,
    cached_per_1m: Decimal | None,
) -> Decimal | None:
    """What THIS call cost us at the vendor, or ``None`` for "we cannot say".

    🔴 **The missing half of margin (A1).** ``usage_event.provider_cost_usd``
    existed for twelve migrations with no writer, so every margin read "not
    measured". The prices come from ``model_profile`` — the operator's own
    record of what each vendor charges — read at metering time, so a later
    profile edit never rewrites what a past call cost.

    ⚠️ **``None`` means UNKNOWN and is the answer whenever any NEEDED price is
    missing.** A call with cached tokens and no cached price is not costed at
    the input rate — that overstates the cost, understates the margin, and a
    wrong number in the safe direction is still a wrong number that someone
    will renegotiate a contract on. Prices for token kinds this call did not
    consume are not needed and their absence costs nothing.

    ⚠️ **A call with zero tokens everywhere is also ``None``.** Extraction is
    best-effort, and all-zero counts usually mean the provider's shape was not
    recognised — recording $0 for a call we could not read would be a
    measurement nobody made.
    """
    prompt = max(usage.prompt_tokens, 0)
    completion = max(usage.completion_tokens, 0)
    # The extractor records cached reads as a subset of prompt_tokens. Clamp,
    # so a provider that double-reports cannot produce a negative uncached
    # count and with it a negative cost.
    cached = min(max(usage.cached_tokens, 0), prompt)

    if prompt == 0 and completion == 0:
        return None

    uncached = prompt - cached
    total = Decimal(0)
    if uncached > 0:
        if input_per_1m is None:
            return None
        total += Decimal(uncached) * input_per_1m
    if cached > 0:
        if cached_per_1m is None:
            return None
        total += Decimal(cached) * cached_per_1m
    if completion > 0:
        if output_per_1m is None:
            return None
        total += Decimal(completion) * output_per_1m

    return (total / Decimal(1_000_000)).quantize(Decimal("0.00000001"))


def vendor_cost_per_unit_usd(
    quantity: Decimal | None, per_unit_usd: Decimal | None
) -> Decimal | None:
    """What one per-unit call cost us at the vendor, or ``None``.

    The sibling of :func:`vendor_cost_usd` for a task the vendor sells by
    a natural unit rather than by token. ``transcribe`` was the first one
    (D19.2), and ``image`` and ``speak`` joined it on 2026-08-31.

    🔴 **ONE multiply for every per-unit task, and the CALLER picks the
    price** (§6A.10c clause 8). This function never learns which unit it
    holds. ``_record_completion`` reads the task's own unit and hands over
    the matching ``model_profile`` column, so a picture is never costed at a
    per-minute price. *(This was ``vendor_cost_per_minute_usd`` until
    2026-08-31, and the name said what only the caller can know.)*

    ⚠️ **``None`` means UNKNOWN and never zero** — D-AI-7 rule 3. Two things
    produce it. Nobody has priced the model, so ``per_unit_usd`` is
    ``None``. Or nothing measured the call, so ``quantity`` is ``None`` or
    zero. Recording $0.00 for a call we could not read would be a
    measurement nobody made, and it would report a margin of 100 percent.
    """
    if per_unit_usd is None or quantity is None:
        return None
    if quantity <= 0:
        return None
    return (Decimal(quantity) * Decimal(per_unit_usd)).quantize(Decimal("0.00000001"))


#: What the meter asks the provider to answer with on a transcription.
#:
#: 🔴 **The METER owns this field, not the caller** (§6A.10a clause 3).
#: litellm's ``TranscriptionResponse`` declares ``text`` and ``usage`` alone,
#: and it copies a ``duration`` on only when the provider sent one. An
#: OpenAI-family provider sends one only under this format. Without the rule
#: every transcribe call falls to the zero-bill arm, so the meter records
#: zero for all of them. Our own ``acb_stt`` sets the same field before it
#: reads the duration (``packages/acb_stt/acb_stt/litellm_provider.py``).
TRANSCRIPTION_RESPONSE_FORMAT = "verbose_json"


def duration_seconds(response: Any) -> Decimal | None:
    """How many seconds of audio the provider says it heard. Never raises.

    ``None`` means the response carried no duration we recognise. The caller
    bills zero and says so out loud — a completion the customer already holds
    must never fail because the meter could not read it.

    Two shapes, read in this order. litellm 1.86.0 gives
    ``usage.type == "duration"`` with ``usage.seconds`` on the providers that
    report a usage object. The older shape is a bare ``duration`` attribute
    that litellm copies across from the provider body. Reading the usage
    object FIRST matters, because it is the reported one rather than the
    inferred one.
    """

    def _get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if hasattr(obj, "get"):
            try:
                return obj.get(key)
            except Exception:
                return None
        return getattr(obj, key, None)

    def _number(value: Any) -> Decimal | None:
        # A bool is an int in Python, and `True` seconds is not a duration.
        if value is None or isinstance(value, bool):
            return None
        try:
            seconds = Decimal(str(value))
        except Exception:
            return None
        # A negative duration is a broken report, not a credit.
        return seconds if seconds >= 0 else None

    try:
        usage = _get(response, "usage")
        if _get(usage, "type") == "duration":
            reported = _number(_get(usage, "seconds"))
            if reported is not None:
                return reported
        return _number(_get(response, "duration"))
    except Exception:
        return None


def image_count(response: Any) -> Decimal | None:
    """How many pictures the provider RETURNED. Never raises.

    🔴 **The count comes off the RESPONSE, and never off the request's
    ``n``** (§6A.10c clause 5). A provider that answers with fewer pictures
    than the caller asked for must bill fewer. Only the response holds that
    fact.

    ``None`` means the body carried no readable list of images. The caller
    bills zero and says so out loud, because the customer already holds
    whatever came back.

    Two shapes, and both reach us. litellm answers with an ``ImageResponse``
    whose ``data`` is a list of objects. The stub seam in the tests answers
    with a plain dict.

    ⚠️ **THE ``None`` ARM IS STUB-ONLY TODAY, and nobody may read it as a
    live alarm.** ``ImageResponse.__init__``
    (``litellm/types/utils.py:2336``, measured in litellm 1.86.0) turns a
    falsy ``data`` into ``[]``, so a real litellm answer reaches this
    function with a list every time and takes the ``Decimal(0)`` arm. The arm
    is KEPT rather than deleted for ONE reason. H-47's native handler seam
    will hand this function a shape litellm never built. Its fence drives a
    dict stub, and it says so.

    📌 **The SECOND reason was false, and review round 2 measured it away.**
    This docstring said a deleted arm would cost the picture call against
    three TOKEN rates. It would not. :func:`vendor_cost_usd` returns ``None``
    when prompt and completion are both zero, as its own docstring states,
    and that is every image call. So the token branch would record
    ``provider_cost_usd`` NULL — the same value the per-unit branch records
    for a quantity of zero. The route coerces a ``None`` count to zero before
    the meter runs, so nothing hands one down either.
    """
    try:
        if isinstance(response, dict):
            data = response.get("data")
        else:
            data = getattr(response, "data", None)
        # A string is a sequence too, and its length is not a picture count.
        if isinstance(data, (str, bytes)) or not isinstance(data, Sequence):
            return None
        return Decimal(len(data))
    except Exception:
        return None


#: What we answer a speech caller with when the provider named no type.
#:
#: *Agent default*, anchored on the vendor: the OpenAI speech endpoint sends
#: MP3 unless the caller asks for another format. A body with no type at all
#: makes a browser guess, and a wrong guess plays nothing.
DEFAULT_SPEECH_MEDIA_TYPE = "audio/mpeg"


def speech_audio(response: Any) -> tuple[bytes, str]:
    """The audio bytes a speech call answered with, and their media type.

    🔴 **The caller reads the provider's own bytes** (§6A.10c clause 2). This
    endpoint answers audio and never JSON, so nothing here re-encodes the
    body or wraps it in a field.

    Three shapes reach us. litellm answers with an
    ``HttpxBinaryResponseContent``, which holds the httpx response and its
    headers. A provider client may answer with bare ``bytes``. The stub seam
    in the tests answers with a plain dict.

    Empty bytes are a legal answer, and this function never raises: a body we
    cannot read reaches the customer as an empty one, which their own player
    reports far better than a 500 does.
    """

    def _media_type(source: Any) -> str | None:
        headers = getattr(getattr(source, "response", None), "headers", None)
        if headers is None:
            return None
        try:
            value = headers.get("content-type")
        except Exception:
            return None
        return value.split(";", 1)[0].strip() if isinstance(value, str) else None

    try:
        if isinstance(response, dict):
            body = response.get("content")
            declared = response.get("media_type")
        elif isinstance(response, (bytes, bytearray)):
            body, declared = response, None
        else:
            body = getattr(response, "content", None)
            declared = _media_type(response)
        if not isinstance(body, (bytes, bytearray)):
            body = b""
        if not isinstance(declared, str) or not declared.strip():
            declared = DEFAULT_SPEECH_MEDIA_TYPE
        return bytes(body), declared
    except Exception:
        return b"", DEFAULT_SPEECH_MEDIA_TYPE


def usage_from_response(response: Any) -> ExtractedUsage:
    """Pull token counters out of a provider response, best-effort.

    Never raises: a provider that reports usage in a shape we do not recognise
    must still return its completion to the customer. An unmetered call is a
    revenue problem; a failed call is a product problem, and the product problem
    is worse. CP-6's gate is where an unmeterable call becomes visible.

    ``cached_tokens`` is normalised across the two reporting styles — Anthropic's
    top-level ``cache_read_input_tokens`` and OpenAI's nested
    ``prompt_tokens_details.cached_tokens`` — because the billing code treats it
    as a subset of ``prompt_tokens`` and must not have to know which provider it
    came from.
    """

    def _get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if hasattr(obj, "get"):
            try:
                return obj.get(key)
            except Exception:
                return None
        return getattr(obj, key, None)

    try:
        usage = _get(response, "usage")
        if usage is None:
            return ExtractedUsage()

        prompt = _get(usage, "prompt_tokens") or 0
        completion = _get(usage, "completion_tokens") or 0

        # ⚠️ WHICH field answered is the convention signal, so record it here
        # and nowhere else — this is the only place that knows.
        convention: str | None = None
        cached = _get(usage, "cache_read_input_tokens")
        if isinstance(cached, int):
            convention = "sibling"
        else:
            details = _get(usage, "prompt_tokens_details")
            cached = _get(details, "cached_tokens")
            if isinstance(cached, int):
                convention = "subset"
        if not isinstance(cached, int):
            cached = 0
            convention = None

        return ExtractedUsage(
            prompt_tokens=int(prompt) if isinstance(prompt, int) else 0,
            completion_tokens=int(completion) if isinstance(completion, int) else 0,
            cached_tokens=cached,
            cache_convention=convention,
        )
    except Exception:
        return ExtractedUsage()


# ── Streaming relay (CP-4b) ─────────────────────────────────────────────────

#: The sentinel every OpenAI-compatible client waits for. A client that never
#: sees it holds the connection open until its own timeout.
SSE_DONE = b"data: [DONE]\n\n"

_DATA_PREFIX = b"data:"


def frame_of(chunk: Any) -> bytes:
    """Serialise ONE provider chunk into ONE SSE frame.

    ⚠️ **This is the only place the Router serialises anything on the streaming
    path**, and it runs only for a source that yields OBJECTS. A source that
    already yields frames reaches the client untouched — see ``relay_stream``.

    ⚠️ **Byte-identity is guaranteed from here outward, not from the provider's
    socket.** litellm parses the provider's SSE and re-emits objects, so the
    original bytes are gone before the Router sees them. CP-4b's done-when 1 is
    fenced through the ``set_provider_call`` seam for exactly this reason: the
    seam is the boundary we control, and the Router must not alter what crosses
    it.
    """
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk)
    dump = getattr(chunk, "model_dump_json", None)
    body = dump(exclude_none=True) if callable(dump) else json.dumps(chunk, default=str)
    return b"data: " + body.encode("utf-8") + b"\n\n"


def usage_from_frame(frame: bytes) -> ExtractedUsage:
    """Pull usage out of an already-encoded SSE frame. Never raises.

    A raw-frame source still has to be meterable, or the seam that makes CP-4b
    testable without a provider account could not exercise the metering clauses.
    """
    try:
        line = frame.strip()
        if not line.startswith(_DATA_PREFIX):
            return ExtractedUsage()
        body = line[len(_DATA_PREFIX) :].strip()
        if body == b"[DONE]" or not body:
            return ExtractedUsage()
        return usage_from_response(json.loads(body))
    except Exception:
        return ExtractedUsage()


def _is_done(frame: bytes) -> bool:
    return frame.strip() == b"data: [DONE]"


async def relay_stream(
    source: Any,
    *,
    on_finish: Callable[[ExtractedUsage, bool], None],
) -> AsyncIterator[bytes]:
    """Relay provider frames to the client unaltered, then meter exactly once.

    ``on_finish(usage, started)`` runs **exactly once**, in a ``finally``, so it
    runs on a clean end, on a provider error mid-stream, and on a client that
    disconnects. ``started`` is False when no frame was ever produced.

    The four CP-4b hazards, each answered here:

    * **Byte-identity** — a frame that arrives as ``bytes`` is yielded as it
      arrived. Nothing re-encodes it.
    * **Exactly one usage row** — one ``finally``, one call, whatever the exit.
    * **The abandoned stream** — the client's disconnect closes this generator,
      which raises ``GeneratorExit`` at the ``yield``. ``finally`` still runs, so
      a stream we paid for is still metered.
    * **The phantom row** — a call that fails before its first frame leaves
      ``started`` False, and the caller writes nothing. That defect is what
      produced the 501 this replaces.

    ⚠️ ``on_finish`` is SYNCHRONOUS on purpose. Awaiting inside a generator that
    is being closed is not reliable, and the write is a single insert. It blocks
    the loop for that insert, which is the cheaper of the two failures.
    """
    started = False
    seen_done = False
    usage = ExtractedUsage()
    try:
        async for chunk in source:
            raw = isinstance(chunk, (bytes, bytearray))
            frame = bytes(chunk) if raw else frame_of(chunk)
            found = usage_from_frame(frame) if raw else usage_from_response(chunk)
            # LAST report wins. A provider that sends `stream_options.
            # include_usage` puts the real counts in the final frame, and the
            # frames before it carry none.
            if found.prompt_tokens or found.completion_tokens:
                usage = found
            if _is_done(frame):
                seen_done = True
            started = True
            yield frame
        if not seen_done:
            # The source ended without the sentinel. Send it, or every
            # OpenAI-compatible client waits for a frame that never comes.
            yield SSE_DONE
    finally:
        on_finish(usage, started)


# ── Failover: which upstream errors are worth trying the next step for ──────


#: An unbounded chain is an unbounded bill and an unbounded wait. litellm is
#: already told `num_retries: 1`, so three steps is up to six provider calls
#: on one request — which is the ceiling, not the target.
MAX_CHAIN_ATTEMPTS = 3

#: Statuses where the REQUEST is wrong. Every step fails these identically, so
#: walking the chain spends money to learn nothing.
#:
#: ⚠️ **413 is deliberately here.** A model with a bigger window might accept an
#: over-long payload, so retrying is tempting — and each attempt re-uploads the
#: whole thing. Paying three times to maybe fit is the wrong default.
TERMINAL_STATUSES = frozenset({400, 404, 413, 422})

#: Statuses that say OUR credential is bad. A second model from the SAME vendor
#: uses the same key, so it fails the same way — skip that vendor entirely.
CREDENTIAL_STATUSES = frozenset({401, 403})


def is_retryable(status: int | None) -> bool:
    """Is another step worth trying?

    ⚠️ **`None` is retryable, and that is the common case.** A timeout, a DNS
    failure and a dropped connection all arrive with no status. Those are
    exactly the provider-down shapes a chain exists for, so treating "no
    status" as terminal would make failover fire only for the errors that
    least need it.
    """
    if status is None:
        return True
    if status in TERMINAL_STATUSES:
        return False
    if status in CREDENTIAL_STATUSES:
        return True
    return status == 408 or status == 429 or status >= 500


class UpstreamFailed(Exception):
    """Every step of the chain refused, or one refused terminally.

    ⚠️ **Carries a STATUS, not a message.** The upstream message can quote the
    request, and the request can carry customer content — so the caller maps
    the status to its own wording and the provider's text never leaves here.
    """

    def __init__(self, status: int | None) -> None:
        super().__init__(f"upstream failed with {status}")
        self.status = status


async def walk_chain(
    attempts: Sequence[ResolvedTier],
    attempt: Callable[[ResolvedTier], Awaitable[Any]],
    on_failover: Callable[[ResolvedTier, ResolvedTier, int | None], None] | None = None,
) -> tuple[Any, ResolvedTier]:
    """Try each step in order and return the first answer, and who gave it.

    🔴 **The ONE failover policy in the product.** Both serving shapes walk
    through here — the buffered call (:func:`call_chain`) and the stream
    (:func:`open_stream_chain`). `TERMINAL_STATUSES`, `CREDENTIAL_STATUSES` and
    `is_retryable` are read in this function and in no other, so a stream can
    never grow a second opinion about what a vendor 500 means.

    ⚠️ **Returns the step that ANSWERED, and the caller must bill from it.** An
    Opus request that fell over to Haiku costs Haiku. Pricing the intended step
    would overcharge a customer for a model they did not get.

    ⚠️ **A vendor that answers 401 is struck off for the rest of the walk.**
    Every model from that vendor presents the same key of ours, so trying a
    second one spends a round trip to learn what we already know.

    `attempt` is a callable because the two shapes differ in what "an answer"
    is: a response body for one, an open stream plus its first chunk for the
    other. The POLICY does not differ, so it is written once.
    """
    dead_vendors: set[str] = set()
    for position, step in enumerate(attempts):
        vendor = step.model.split("/", 1)[0]
        if vendor in dead_vendors:
            continue
        try:
            return await attempt(step), step
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in CREDENTIAL_STATUSES:
                dead_vendors.add(vendor)
            remaining = [
                s for s in attempts[position + 1 :] if s.model.split("/", 1)[0] not in dead_vendors
            ]
            if not is_retryable(status) or not remaining:
                raise UpstreamFailed(status) from exc
            if on_failover is not None:
                on_failover(step, remaining[0], status)
    # Unreachable while `attempts` is non-empty: the loop either returns or
    # raises. Kept so a future edit that empties it fails loudly.
    raise UpstreamFailed(None)


async def call_chain(
    attempts: Sequence[ResolvedTier],
    kwargs_for: Callable[[ResolvedTier], dict[str, Any]],
    on_failover: Callable[[ResolvedTier, ResolvedTier, int | None], None] | None = None,
) -> tuple[Any, ResolvedTier]:
    """Walk the chain for a BUFFERED completion.

    🔴 **This is D-AI-5 actually doing something.** Until it existed the chain
    was configuration the Router stored and never read.

    Lives here, not in the route, because a route needs FastAPI and a test of
    failover ORDER should not. `kwargs_for` is a callable for the same reason:
    building a provider call needs the request, the credential and the clamp,
    and none of that is this function's business.
    """

    async def _attempt(step: ResolvedTier) -> Any:
        return await call_provider(**kwargs_for(step))

    return await walk_chain(attempts, _attempt, on_failover)


async def aclose_quietly(source: Any) -> None:
    """Close one provider stream, and never raise while doing it.

    🔴 **The ONE close in the streaming path.** The walk closes a LOSER here,
    and the route closes the WINNER through the same function. Two spellings
    of "let go of a socket" is how one of them ends up forgotten.

    ⚠️ **Degrades when ``aclose`` is absent.** A source is whatever the
    provider SDK handed back. litellm's ``CustomStreamWrapper`` has one, and a
    plain async generator has one, but a future SDK shape may not — and a
    missing cleanup hook must not become an AttributeError on the serving
    path.

    ⚠️ **Safe on a stream that is already finished.** ``CustomStreamWrapper.
    aclose`` clears its inner stream and no-ops the second time, and an
    exhausted async generator returns at once. So the caller may close on
    every exit rather than reasoning about which exit it took.
    """
    aclose = getattr(source, "aclose", None)
    if aclose is None:
        return
    with contextlib.suppress(Exception):
        await aclose()


async def open_stream_chain(
    attempts: Sequence[ResolvedTier],
    kwargs_for: Callable[[ResolvedTier], dict[str, Any]],
    on_failover: Callable[[ResolvedTier, ResolvedTier, int | None], None] | None = None,
) -> tuple[list[Any], Any, ResolvedTier]:
    """Open a provider STREAM, pull its first chunk, and walk while doing it.

    Returns ``(head, source, step)``. ``head`` holds the first chunk, or
    nothing at all. ``source`` is the rest of the stream, already open.

    🔴 **Pulling the first chunk HERE is what makes a stream fail over.** An
    attempt that only opened the stream would call the next step for a refused
    connection and not for a provider that accepts the socket and then dies —
    which is the common outage shape. The first chunk is the earliest proof
    that a step will answer.

    ⚠️ **The caller must replay ``head``, exactly once.** It has left the
    provider and no second attempt can produce it again. The client is owed it
    and is owed it one time.

    ⚠️ **A stream that opens and yields NOTHING is an ANSWER, not a failure.**
    ``StopAsyncIteration`` returns an empty ``head`` rather than walking on. A
    provider that completed with no content has served the request, and paying
    a second vendor to repeat it would bill twice for one empty answer.
    """

    async def _attempt(step: ResolvedTier) -> tuple[list[Any], Any]:
        source = await call_provider(**kwargs_for(step))
        iterator = aiter(source)
        try:
            return [await anext(iterator)], iterator
        except StopAsyncIteration:
            return [], iterator
        except BaseException:
            # The socket is ours the moment the open succeeds. A step we walk
            # away from must not leave a provider connection behind it.
            await aclose_quietly(iterator)
            raise

    (head, source), step = await walk_chain(attempts, _attempt, on_failover)
    return head, source, step
