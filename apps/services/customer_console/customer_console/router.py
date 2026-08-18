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
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection

__all__ = [
    "ResolvedTier",
    "TierUnknown",
    "call_provider",
    "decrypt_secret",
    "encrypt_secret",
    "provider_credential",
    "resolve_tier",
    "set_provider_call",
    "usage_from_response",
]


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


def resolve_tier(conn: Connection, tier: str) -> ResolvedTier:
    """Resolve a tier alias to the model currently bound to it.

    Picks the newest binding whose ``effective_from`` has passed, so a future
    dated row can be staged without taking effect — the same shape as
    ``seat_grant`` and ``model_rate_card``. Repricing and re-pointing are both
    "insert a row with a later date", never "edit the live one", so history
    stays reconstructable.
    """
    row = conn.execute(
        text(
            """
            SELECT model FROM tier_binding
            WHERE tier = :tier AND effective_from <= now()
            ORDER BY effective_from DESC
            LIMIT 1
            """
        ),
        {"tier": tier},
    ).first()
    if row is None:
        raise TierUnknown(f"no binding for tier {tier!r}")
    return ResolvedTier(tier=tier, model=row[0])


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


def provider_credential(conn: Connection, *, provider: str,
                        org_id: str | None = None) -> tuple[str, str | None] | None:
    """The live credential for a provider. Returns ``(secret, api_base)``.

    Prefers the organization's OWN credential when it has one — that is BYOK
    (§3.4): a customer insisting on their own provider account is metered but
    not charged for tokens, which also caps our exposure on the largest
    accounts. Falls back to the platform's account (``organization_id IS NULL``).
    """
    row = conn.execute(
        text(
            """
            SELECT secret_enc, api_base FROM provider_credential
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
    return decrypt_secret(row[0]), row[1]


# ── The provider call, behind a seam ────────────────────────────────────────

ProviderCall = Callable[..., Awaitable[Any]]


async def _litellm_call(**kwargs: Any) -> Any:
    from litellm import acompletion

    return await acompletion(**kwargs)


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
    extra: dict[str, Any] = field(default_factory=dict)


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

        cached = _get(usage, "cache_read_input_tokens")
        if not isinstance(cached, int):
            details = _get(usage, "prompt_tokens_details")
            cached = _get(details, "cached_tokens")
        if not isinstance(cached, int):
            cached = 0

        return ExtractedUsage(
            prompt_tokens=int(prompt) if isinstance(prompt, int) else 0,
            completion_tokens=int(completion) if isinstance(completion, int) else 0,
            cached_tokens=cached,
        )
    except Exception:
        return ExtractedUsage()
