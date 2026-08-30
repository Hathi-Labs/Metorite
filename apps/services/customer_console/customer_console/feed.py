"""The vendor feed — upstream model facts, fetched instead of typed.

🔴 **Why this module exists (owner directive, 2026-08-30).** Every fact on
/models was hand-typed: windows, output caps and vendor prices copied out of
eleven vendors' HTML pricing pages into ``model_profile``. Hand-copied vendor
prices go stale silently, and a stale upstream price mis-states every margin
computed downstream of it. The directive: facts flow from upstream, or from a
reliable source when the vendor publishes none — and no vendor publishes a
machine-readable price list.

⚠️ **The source is litellm's price map, and the choice is structural.**
``model_prices_and_context_window.json`` is the community-maintained
aggregation of every vendor's published prices and limits, updated
near-daily — and it is keyed on the EXACT provider ids this system already
routes on. The Router resolves a vendor as ``model.split("/", 1)[0]`` and
hands the call to litellm (CP-4); a feed in any other vocabulary would need
a mapping table, and mapping tables drift. litellm also *bundles* a snapshot
of the same file inside the installed package, so the feed degrades to
"recent" rather than "absent" when the network is down.

⚠️ **The feed is a cache of upstream claims, never billing truth.** Billing
cost reads ``model_profile``, which only an explicit staff write changes;
``usage_event.provider_cost_usd`` snapshots it at call time. This module's
entire job is to make that staff write a one-click copy instead of a
transcription — and to make a vendor's price change VISIBLE (drift) instead
of silent.

⚠️ **Money maths is Decimal end-to-end.** The JSON carries per-token floats
like ``2.8e-07``; ``Decimal(str(v)) * 1_000_000`` turns that into an exact
``0.28`` per million. Multiplying the float first would manufacture
``0.27999999999999997`` and a phantom drift warning against every profile.
"""

from __future__ import annotations

import importlib.resources
import json
import logging
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, NamedTuple

from sqlalchemy import text

_log = logging.getLogger("platform.feed")

#: The live feed. raw.githubusercontent serves the file litellm maintains on
#: its default branch; there is no API key and no rate ceiling that a
#: once-a-day fetch would meet.
FEED_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

#: The snapshot litellm bundles inside the installed package — same shape,
#: older clock. The fallback when the network refuses.
PACKAGED_FEED = "model_prices_and_context_window_backup.json"

#: litellm's ``mode`` → our (task, invocation). One place, server-side; the
#: console consumes the mapped words and never re-derives them (one seam).
#: A mode absent here (completion, rerank, moderation, …) still lands as a
#: row — prices inform — but offers no one-click declare.
#:
#: ⚠️ Values must stay inside ``task_catalog`` (010) and
#: ``catalog.KNOWN_INVOCATIONS`` — ``test_customer_console_vendor_feed.py``
#: fences both, so a rename there breaks HERE first, not in production.
MODE_MAP: dict[str, tuple[str, str]] = {
    "chat": ("chat", "acompletion"),
    "embedding": ("embed", "aembedding"),
    "audio_transcription": ("transcribe", "atranscription"),
    "audio_speech": ("speak", "aspeech"),
    "image_generation": ("image", "aimage_generation"),
}

_PER_1M = Decimal(1_000_000)
_CENT6 = Decimal("0.000001")

#: Ten decimal places, the width of the per-unit columns (019). A per-unit
#: price is far smaller than a per-million-token one: OpenAI text-to-speech
#: charges 0.000015 per character today, and six places would round a cheaper
#: future model to zero.
_UNIT10 = Decimal("0.0000000001")


class FeedRow(NamedTuple):
    """One model's upstream facts, in this system's vocabulary."""

    model: str
    provider: str
    mode: str
    task: str | None
    invocation: str | None
    context_window: int | None
    max_output: int | None
    input_per_1m: Decimal | None
    output_per_1m: Decimal | None
    cached_per_1m: Decimal | None
    reads_images: bool
    thinks_first: bool
    deprecated_on: date | None
    # ⚠️ The vendor's own unit, unconverted (019, §6A.11a). Per SECOND, not
    # per minute: `task_catalog` prices `transcribe` per minute, and the x60
    # conversion belongs to the declare-and-prefill seam alone.
    per_second: Decimal | None = None
    per_character: Decimal | None = None
    per_image: Decimal | None = None


def _per_1m(entry: dict[str, Any], key: str) -> Decimal | None:
    """A per-token price as exact per-million, or None for absent/garbage."""
    v = entry.get(key)
    if v is None:
        return None
    try:
        d = Decimal(str(v)) * _PER_1M
        # is_finite BEFORE the comparison: json.loads admits bare Infinity
        # and NaN, Decimal carries them, and a NaN COMPARISON raises — one
        # poisoned entry among 3,000 used to 500 the whole sync, fallback
        # included. A negative price is upstream garbage the table's CHECK
        # would refuse the whole batch over. Unknown beats poisoned.
        if not d.is_finite() or d < 0:
            return None
        return d.quantize(_CENT6, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _per_unit(entry: dict[str, Any], *keys: str) -> Decimal | None:
    """A per-unit price in the VENDOR's own unit, or None for absent/garbage.

    :func:`_per_1m`'s rule without the per-million multiply — same is_finite
    check before the comparison, same "unknown beats poisoned" answer — so a
    negative, a NaN, an Infinity or a non-number leaves the field NULL.

    ⚠️ Several keys mean a FALLBACK on ABSENCE, never a sum and never a
    second chance for garbage. An image entry carries ``output_cost_per_image``
    or ``input_cost_per_image``, and the first one the entry CARRIES decides.
    Summing is the whisper-1 trap one field up: two fields that both describe
    the same price charge twice when they are added.
    """
    for key in keys:
        v = entry.get(key)
        if v is None:
            continue
        try:
            d = Decimal(str(v))
            if not d.is_finite() or d < 0:
                return None
            return d.quantize(_UNIT10, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError, TypeError):
            return None
    return None


def _tokens(entry: dict[str, Any], *keys: str) -> int | None:
    """First positive integer among ``keys``, else None (never zero)."""
    for key in keys:
        v = entry.get(key)
        try:
            n = int(v)  # litellm mixes int and float for the same field
        except (TypeError, ValueError, OverflowError):
            # OverflowError: int(float("inf")) — a poisoned window must
            # skip the field, not kill the sync.
            continue
        if n > 0:
            return n
    return None


def _deprecated(entry: dict[str, Any]) -> date | None:
    v = entry.get("deprecation_date")
    if not isinstance(v, str):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_feed(raw: dict[str, Any]) -> list[FeedRow]:
    """The raw litellm map → rows in this system's vocabulary.

    Skips ``sample_spec`` (litellm's embedded schema doc) and any entry
    without a provider and a mode — a price with no owner routes nowhere.
    Keys are normalised to ``provider/model`` because litellm's raw keys are
    inconsistent (``deepseek/deepseek-chat`` but bare ``gpt-4o``), and the
    Router's grammar demands the prefix. Last write wins on a collision.
    """
    rows: dict[str, FeedRow] = {}
    for key, entry in raw.items():
        if key == "sample_spec" or not isinstance(entry, dict):
            continue
        provider = entry.get("litellm_provider")
        mode = entry.get("mode")
        if not isinstance(provider, str) or not provider:
            continue
        if not isinstance(mode, str) or not mode:
            continue
        model = key if key.startswith(provider + "/") else f"{provider}/{key}"
        task, invocation = MODE_MAP.get(mode, (None, None))
        # ⚠️ One field per TASK, and a task reads only its own unit. Several
        # chat models on Vertex price per character, so an ungated read would
        # give a chat row a speak model's column.
        per_second = (
            _per_unit(entry, "input_cost_per_second")
            if task == "transcribe" else None
        )
        per_character = (
            _per_unit(entry, "input_cost_per_character")
            if task == "speak" else None
        )
        per_image = (
            _per_unit(entry, "output_cost_per_image", "input_cost_per_image")
            if task == "image" else None
        )
        rows[model] = FeedRow(
            model=model,
            provider=provider,
            mode=mode,
            task=task,
            invocation=invocation,
            # ⚠️ `max_tokens` is litellm's LEGACY name for max OUTPUT, so it
            # backs up max_output and never the window — read it as a window
            # and every older entry claims a context 30x too small.
            context_window=_tokens(entry, "max_input_tokens"),
            max_output=_tokens(entry, "max_output_tokens", "max_tokens"),
            input_per_1m=_per_1m(entry, "input_cost_per_token"),
            output_per_1m=_per_1m(entry, "output_cost_per_token"),
            cached_per_1m=_per_1m(entry, "cache_read_input_token_cost"),
            reads_images=bool(entry.get("supports_vision")),
            thinks_first=bool(entry.get("supports_reasoning")),
            deprecated_on=_deprecated(entry),
            per_second=per_second,
            per_character=per_character,
            per_image=per_image,
        )
    return list(rows.values())


def _github_fetch() -> dict[str, Any]:
    # Imported here so the module works (packaged path, parsing, sync) in an
    # environment that blocks sockets — most of the test suite.
    import httpx

    resp = httpx.get(FEED_URL, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or len(data) < 100:
        # A truncated or error-page response must not become "the feed
        # shrank to nothing" — refuse it and let the fallback answer.
        raise ValueError(f"feed answered with {type(data).__name__}, refusing")
    return data


def packaged_feed() -> dict[str, Any]:
    """The snapshot bundled inside the installed litellm. Never networks."""
    f = importlib.resources.files("litellm").joinpath(PACKAGED_FEED)
    return json.loads(f.read_text(encoding="utf-8"))


def fetch_feed(fetcher: Any = None) -> tuple[dict[str, Any], str]:
    """The live feed, or the packaged snapshot when the network refuses.

    Returns ``(raw, source)`` where source is ``github`` or
    ``packaged:litellm`` — recorded in ``feed_sync_log`` so "current as of"
    is a provable claim, not a belief.
    """
    try:
        return (fetcher or _github_fetch)(), "github"
    except Exception as exc:
        _log.warning(
            "feed.fetch_fell_back source=github error=%s", exc)
        return packaged_feed(), "packaged:litellm"


_UPSERT = text(
    """
    INSERT INTO vendor_price_feed (
        model, provider, mode, task, invocation,
        context_window, max_output,
        vendor_input_per_1m_usd, vendor_output_per_1m_usd,
        vendor_cached_input_per_1m_usd,
        vendor_per_second_usd, vendor_per_character_usd, vendor_per_image_usd,
        reads_images, thinks_first, deprecated_on, synced_at
    ) VALUES (
        :model, :provider, :mode, :task, :invocation,
        :ctx, :out, :vin, :vout, :vcached,
        :vsec, :vchar, :vimg,
        :imgs, :think, :dep, now()
    )
    ON CONFLICT (model) DO UPDATE SET
        provider = EXCLUDED.provider,
        mode = EXCLUDED.mode,
        task = EXCLUDED.task,
        invocation = EXCLUDED.invocation,
        context_window = EXCLUDED.context_window,
        max_output = EXCLUDED.max_output,
        vendor_input_per_1m_usd = EXCLUDED.vendor_input_per_1m_usd,
        vendor_output_per_1m_usd = EXCLUDED.vendor_output_per_1m_usd,
        vendor_cached_input_per_1m_usd =
            EXCLUDED.vendor_cached_input_per_1m_usd,
        vendor_per_second_usd = EXCLUDED.vendor_per_second_usd,
        vendor_per_character_usd = EXCLUDED.vendor_per_character_usd,
        vendor_per_image_usd = EXCLUDED.vendor_per_image_usd,
        reads_images = EXCLUDED.reads_images,
        thinks_first = EXCLUDED.thinks_first,
        deprecated_on = EXCLUDED.deprecated_on,
        synced_at = now()
    """
)


def sync(conn: Any, rows: list[FeedRow], source: str,
         started_at: datetime) -> dict[str, int]:
    """Upsert the batch and write the evidence row. One transaction, caller's.

    ⚠️ Upsert, never delete — a model litellm drops keeps its last-known
    facts (with a stale ``synced_at``) rather than stripping the prefill out
    from under a model an operator already declared.
    """
    if rows:
        conn.execute(_UPSERT, [
            {
                "model": r.model, "provider": r.provider, "mode": r.mode,
                "task": r.task, "invocation": r.invocation,
                "ctx": r.context_window, "out": r.max_output,
                "vin": r.input_per_1m, "vout": r.output_per_1m,
                "vcached": r.cached_per_1m,
                "vsec": r.per_second, "vchar": r.per_character,
                "vimg": r.per_image,
                "imgs": r.reads_images, "think": r.thinks_first,
                "dep": r.deprecated_on,
            }
            for r in rows
        ])
    conn.execute(
        text(
            "INSERT INTO feed_sync_log "
            "(source, models_seen, rows_upserted, started_at) "
            "VALUES (:src, :seen, :up, :started)"
        ),
        {"src": source, "seen": len(rows), "up": len(rows),
         "started": started_at},
    )
    return {"models_seen": len(rows), "rows_upserted": len(rows)}
