"""The embed door of Knowledge (WS-61 KB-3, ``knowledge_base.md`` §6.4).

:func:`embed` is the ONE function that makes a vector for the index. Since
CP-EMB (``customer_console.md`` §6A.16, 2026-10-17) it asks the Customer
Console's Router: ``POST /v1/embeddings`` on ``tier-embed``, through the one
Console client, ``acb_auth.console_resolve.embeddings_on_console``. The
Router serves ``text-embedding-3-small`` at 1536 dimensions on the AI/ML API
account, writes a ``usage_event`` row and draws no credit (D19.2).

🔴 **Why it moved.** It posted to the gateway's own ``/v1/embeddings``, which
calls OpenAI with ``OPENAI_API_KEY``. Production holds no such key, so no
vector was made, and Knowledge search and CRM note search both degraded.

**The member is the run's.** The Console derives the tenant from a PROVEN
member on a deployment-key box (H-152). This module reads the ambient run
context through ``acb_llm.routed.run_attribution``, as every routed call
does. A signed-in request binds its member, and a background job binds the
owner of its row (``job_member_scope``). So the callers pass nothing new.

**A zero vector is a failure.** A zero vector stored would rank every chunk
the same and poison the semantic half. So an all-zero vector, a vector of the
wrong size, a refusal, an outage and a timeout are each ``None``, and the
caller keeps a NULL embedding. 🔴 **This function never raises.**

It imports no ``litellm`` and no ``openai``, so
``tests/unit/test_no_direct_ai_vendor_calls.py`` holds. No session is open
across the call: the callers select, call this, and then write.

The callers check ``KNOWLEDGE_EMBED`` (``routes/knowledge/flags.py``) before
they call. With the flag off, nothing calls this function.

⚠️ **These names are a contract.** CRM-Q27 imports :data:`BATCH`,
:data:`EMBED_DIMS` and :func:`embed`, and ``crm_sync/scheduler.py`` loads it
at import time. ``tests/unit/test_kb_chunk.py`` pins them.
"""

from __future__ import annotations

from typing import Any

from acb_common import get_logger

from gateway.kb_index.chunk import estimate_tokens

_log = get_logger("gateway.kb_index.embed")

#: The model of the index, and the size of its vectors (§6.4, §6.1). The
#: Router's `tier-embed` binding serves this model. ⚠️ A change here makes
#: every stored vector stale, and CRM-Q27 then re-sends each note.
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536

#: The tier this door names. The Router picks the model (D32.7).
EMBED_TIER = "tier-embed"

#: The app a call reports when the run context names none.
DEFAULT_MODULE = "knowledge"

#: The most texts in one request.
BATCH = 64

#: The longest text sent, in characters. A chunk is about 2400.
MAX_TEXT_CHARS = 8000

#: Seconds one request may take when a write embeds its chunks. A query
#: passes its own, shorter limit.
TIMEOUT_S = 20.0


def _vector(value: Any) -> list[float] | None:
    """A usable vector, or ``None`` for a zero, short or malformed one."""
    if not isinstance(value, list) or len(value) != EMBED_DIMS:
        return None
    try:
        vec = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    if not any(vec):
        return None
    return vec


def _attribution() -> dict[str, Any]:
    """Who the call is for, from the run context. Never raises.

    ``acb_llm.routed.run_attribution`` is the one reader. A run with no app
    reports :data:`DEFAULT_MODULE`, so the usage page names Knowledge.
    """
    try:
        from acb_llm.routed import run_attribution

        found = run_attribution()
        if not found.get("module_slug"):
            found = run_attribution(module_slug=DEFAULT_MODULE)
        return found
    except Exception:
        return {}


async def _post(texts: list[str], timeout: float) -> list[list[float] | None]:
    """One request for *texts*. Each failure is ``None`` for every text."""
    nothing: list[list[float] | None] = [None] * len(texts)
    try:
        from acb_auth import console_resolve

        status, body = await console_resolve.embeddings_on_console(
            {"model": EMBED_TIER, "input": texts},
            timeout=timeout,
            **_attribution(),
        )
    except Exception as exc:  # the door down is an answer
        _log.warning("knowledge.embed_failed", error=type(exc).__name__)
        return nothing
    if status != 200:
        _log.warning("knowledge.embed_failed", error="refused", status=status)
        return nothing
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        _log.warning("knowledge.embed_failed", error="count_mismatch")
        return nothing
    ordered = sorted(data, key=lambda d: d.get("index", 0) if isinstance(d, dict) else 0)
    return [_vector(d.get("embedding") if isinstance(d, dict) else None) for d in ordered]


async def embed(
    texts: list[str],
    *,
    org: str | None = None,
    timeout: float = TIMEOUT_S,
) -> list[list[float] | None]:
    """One vector or ``None`` for each text, in order. Never raises.

    ``None`` means no vector: the Router refused, it is down, it timed out,
    or it answered a zero vector. One log line for each call, with the org,
    the count and the token estimate. The Router writes the usage row.

    🔴 **An empty text is ``None``, and it is never SENT.** The door answers
    422 for an empty text, and one 422 makes every text of its request
    ``None``. So a text that is empty after ``strip()`` keeps its position as
    ``None``, and only the other texts go out, in batches of :data:`BATCH`.
    """
    if not texts:
        return []
    clipped = [str(t or "")[:MAX_TEXT_CHARS] for t in texts]
    out: list[list[float] | None] = [None] * len(clipped)
    # The positions that carry text. Only these are sent.
    sendable = [i for i, t in enumerate(clipped) if t.strip()]
    for start in range(0, len(sendable), BATCH):
        positions = sendable[start:start + BATCH]
        batch = [clipped[i] for i in positions]
        try:
            vectors = await _post(batch, timeout)
        except Exception as exc:  # `_post` never raises; this is the backstop
            _log.warning("knowledge.embed_failed", error=type(exc).__name__)
            continue
        for i, vec in zip(positions, vectors, strict=False):
            out[i] = vec
    _log.info(
        "knowledge.embed",
        organization_id=org,
        chunks=len(clipped),
        token_estimate=sum(estimate_tokens(t) for t in clipped),
        embedded=sum(1 for v in out if v is not None),
        model=EMBED_MODEL,
        tier=EMBED_TIER,
    )
    return out
