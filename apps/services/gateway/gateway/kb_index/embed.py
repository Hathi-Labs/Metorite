"""The embed door of Knowledge (WS-61 KB-3, ``knowledge_base.md`` §6.4).

:func:`embed` is the ONE function that makes a vector for the index. It
posts over HTTP to ``{litellm_base_url}/v1/embeddings`` with the platform
bearer (``Settings.gateway_internal_token``, see ``_headers``), with ``text-embedding-3-small`` at 1536
dimensions. It imports no ``litellm`` and no ``openai``, so
``tests/unit/test_no_direct_ai_vendor_calls.py`` holds. When WS-45 S5 gives
the Router an embedding door on ``tier-embed``, that door moves here, and the
callers change nothing.

**A zero vector is a failure.** The door answers zero vectors when it has no
key. A zero vector stored would rank every chunk the same and poison the
semantic half. So an all-zero vector, a vector of the wrong size, an error
and a timeout are each ``None``, and the caller keeps a NULL embedding.

**No usage row yet.** Only the Customer Console writes ``usage_event``, and
nothing serves an embedding through the Router until WS-45 S5. So each call
writes one structured log line with the org, the chunk count and the token
estimate.

The callers check ``KNOWLEDGE_EMBED`` (``routes/knowledge/flags.py``) before
they call. With the flag off, nothing calls this function.
"""

from __future__ import annotations

from typing import Any

import httpx
from acb_common import get_logger, get_settings

from gateway.kb_index.chunk import estimate_tokens

_log = get_logger("gateway.kb_index.embed")

#: The model of the index, and the size of its vectors (§6.4, §6.1).
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536

#: The most texts in one request.
BATCH = 64

#: The longest text sent, in characters. A chunk is about 2400.
MAX_TEXT_CHARS = 8000

#: Seconds one request may take when a write embeds its chunks. A query
#: passes its own, shorter limit.
TIMEOUT_S = 20.0


def _client(timeout: float) -> httpx.AsyncClient:
    """The HTTP client of one request. The suites replace this one function."""
    return httpx.AsyncClient(timeout=timeout)


def _url() -> str:
    return get_settings().litellm_base_url.rstrip("/") + "/v1/embeddings"


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


def _headers() -> dict[str, str]:
    """The service token, because the gateway calls its own route.

    ``POST /v1/embeddings`` authenticates with ``get_current_user``, which
    accepts the service identity token and refuses the ``/v1`` key once
    ``GATEWAY_INTERNAL_TOKEN`` is set (measured on production, 2026-10-16).
    This module is platform code that takes no model output, so it may hold
    the service token, as ``crm_tools.py`` does. With no token set, the call
    fails, and every vector is ``None``, which the caller reads as degraded.
    """
    return {"Authorization": f"Bearer {get_settings().gateway_internal_token}"}


async def _post(texts: list[str], timeout: float) -> list[list[float] | None]:
    """One request for *texts*. Each failure is ``None`` for every text."""
    nothing: list[list[float] | None] = [None] * len(texts)
    try:
        async with _client(timeout) as client:
            response = await client.post(
                _url(),
                json={"model": EMBED_MODEL, "input": texts},
                headers=_headers(),
            )
        response.raise_for_status()
        data = response.json().get("data") or []
    except Exception as exc:  # the door down is an answer
        _log.warning("knowledge.embed_failed", error=type(exc).__name__)
        return nothing
    if len(data) != len(texts):
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
    """One vector or ``None`` for each text, in order.

    ``None`` means no vector: the door is down, it timed out, or it answered
    a zero vector. One log line for each call, with the org, the count and
    the token estimate. No usage row (§6.4).
    """
    if not texts:
        return []
    clipped = [str(t or "")[:MAX_TEXT_CHARS] for t in texts]
    out: list[list[float] | None] = []
    for start in range(0, len(clipped), BATCH):
        out.extend(await _post(clipped[start:start + BATCH], timeout))
    _log.info(
        "knowledge.embed",
        organization_id=org,
        chunks=len(clipped),
        token_estimate=sum(estimate_tokens(t) for t in clipped),
        embedded=sum(1 for v in out if v is not None),
        model=EMBED_MODEL,
    )
    return out
