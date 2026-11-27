"""Activity bus — live, cross-app agent/model activation feed (E2 live).

One Redis stream for each tenant (``cc:<org>:activity``) that EVERY agent run
and model call publishes a small event to, so an operator UI can show a live
feed of "who / what is running right now" across chat AND every app (email,
tasks, …). Because the two publish sites are shared libraries — the
orchestrator run boundary (agent activations) and ``acb_llm`` (model
activations) — cross-app coverage is automatic: anything that runs an agent or
calls a model shows up.

Design contract (mirrors the per-thread ``stream_relay``, but ONE low-volume
stream for each tenant instead of one per conversation):

* **Tenant-keyed (H-201c, MT-1e).** The stream and the presence keys go
  through :mod:`acb_common.tenant_redis`, so each key starts ``cc:<org>:``.
  A writer captures the org at call time: the explicit ``organization_id``,
  else the bound tenant (``acb_common.db.current_tenant()``). With no org it
  writes NOTHING. There is no global fallback and no dual read of the old
  global ``cc:activity`` key. Each reader takes a required
  ``organization_id`` and gives an empty answer without one.
* **Best-effort, never blocks, never raises.** A failed publish drops the
  event; it must never affect the run that emitted it. All Redis IO is
  scheduled off the caller's path.
* **Small, stable event shape.** One JSON object per stream entry under the
  ``event`` field (same convention as ``stream_relay.push_event``). The org
  is NOT in the event body: the key carries it.
* **Presence keys** (``cc:<org>:activity:live:{run_id}``) track in-flight
  agent runs (and, identically, in-flight Custom App AI calls —
  ``kind="app"``) with a TTL so the "running now" panel survives a crash
  without leaking. The key's body is the ``phase="start"`` event, so a field
  the run only learns about *after* start (``instance``) reaches
  ``active_runs()`` — and therefore ``/observability/active`` and
  ``/observability/roster`` — only via :func:`refresh_run_presence`.

The daily cost rollup is keyed for each tenant too (H-201e, task 1748):
``cc:<org>:cost:<YYYY-MM-DD>``, through the same wrapper. The org of a priced
call is captured as for the stream. A priced call with NO org (the gateway's
``/v1/chat/completions`` under the deployment key) folds into ONE separate,
deployment-level hash, ``cc:_unattributed:cost:<YYYY-MM-DD>``, on the raw
client. No tenant reader reads that hash. An org id cannot start with ``_``
(``tenant_redis._ORG_ID_RE``), so that key can never equal a tenant key. There
is no dual read of the old global ``cc:cost:{day}`` hash. Those hashes are
orphaned at deploy and expire by their own TTL.

This is the live signal only — the durable record stays in ``agent_run``
(migration 50) and the correlated logs. Retention here is bounded by
``MAXLEN`` and by the presence-key TTL.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from acb_common._log import get_run_context
from acb_common.settings import get_settings
from acb_common.tenant_redis import (
    TenantKey,
    TenantRedis,
    get_tenant_redis,
    key,
    match,
    organization_scope,
)

# Deliberately NOT get_logger here to avoid any import cycle surprises during
# early process start; publish failures are silent by contract anyway.

#: The tenant namespace of the stream (``cc:<org>:activity``) and of the
#: presence keys (``cc:<org>:activity:live:<run_id>``). H-201c.
ACTIVITY_NAMESPACE = "activity"
LIVE_PART = "live"
#: The tenant namespace of the daily cost rollup: ``cc:<org>:cost:<day>``.
COST_NAMESPACE = "cost"
#: The ONE deployment-level cost hash: ``cc:_unattributed:cost:<day>``. It holds
#: the priced calls that have no tenant. No tenant route reads it. The ``_``
#: lead is not a legal org id, so no tenant key or tenant SCAN can reach it.
UNATTRIBUTED_COST_PREFIX = "cc:_unattributed:cost"
STREAM_MAXLEN = 2_000          # ~ last N activations; bounds memory
LIVE_TTL_SECONDS = 900         # presence key self-heals if an "end" is lost
COST_TTL_SECONDS = 60 * 60 * 24 * 45   # keep ~45 days of daily cost rollups

# Fields copied from the run-correlation context when the caller omits them, so
# a model call inside an agent run inherits that run's agent/user/thread/source.
# ``instance`` (WS-6a/6c) rides the same mechanism: because it is bound into
# the run context, a model activation is attributed to the run's tenant
# partition with no change at the emitting call site — completing decision D1's
# (run_id, member, agent, instance) stamp on the live feed. A shared run binds
# no instance, so the field is simply absent, exactly as before.
_INHERIT = (
    "agent", "user", "thread_id", "run_id", "source", "instance",
    "app", "member_verified",
)


# ── Raw async client: the unattributed cost hash only (created lazily) ───────
# Every tenant key goes through acb_common.tenant_redis. Only the
# deployment-level ``cc:_unattributed:cost:<day>`` hash is written here, because
# it has no tenant by definition (H-201e).
_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    """Return the raw pooled client of the unattributed cost hash."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            max_connections=16,
            health_check_interval=30,
        )
    return _client


def _stream_key() -> TenantKey:
    """``cc:<org>:activity``. The caller binds the org first."""
    return key(ACTIVITY_NAMESPACE)


def _live_key(run_id: str) -> TenantKey:
    """``cc:<org>:activity:live:<run_id>``. The caller binds the org first."""
    return key(ACTIVITY_NAMESPACE, LIVE_PART, run_id)


def _capture_org(organization_id: str | None) -> str | None:
    """The org of a write: the explicit value, else the bound tenant.

    The caller's own context supplies it, at call time, because the write runs
    later on a loop task. It never comes from request input. ``None`` means
    that the write is dropped: there is no global fallback.
    """
    if organization_id:
        return organization_id
    try:
        from acb_common.db import current_tenant

        return current_tenant() or None
    except Exception:
        return None


def _cost_key(day: str) -> TenantKey:
    """``cc:<org>:cost:<day>``. The caller binds the org first."""
    return key(COST_NAMESPACE, day)


def _unattributed_cost_key(day: str) -> str:
    """``cc:_unattributed:cost:<day>``: the priced calls with no tenant."""
    return f"{UNATTRIBUTED_COST_PREFIX}:{day}"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _record_cost(r: Any, hkey: TenantKey | str, evt: dict[str, Any]) -> None:
    """Fold one priced model call into the rollup hash *hkey*. Best-effort.

    *r* is a :class:`TenantRedis` with *hkey* a tenant key
    (``cc:<org>:cost:<date>``), or the raw client with *hkey* the unattributed
    key. Both pipelines take the same commands, so the fold is written once.

    One hash per UTC day with additive fields so a daily
    cost breakdown by model / app / agent / tenant partition is a single
    HGETALL — no migration, no per-call Postgres write (the deliberate default;
    LLM_USAGE_AUDIT stays the opt-in durable path). Fields:
        total|cost|tokens|calls
        model|<m>|cost|tokens|calls
        source|<s>|cost   ·   agent|<a>|cost   ·   instance|<i>|cost

    The ``instance`` dimension (WS-6a) is what makes "what did alice's personal
    agents cost today" answerable at all: ``instance`` otherwise exists only on
    raw stream entries, which are bounded by ``STREAM_MAXLEN``. It is the LIVE
    rollup only — bounded by ``COST_TTL_SECONDS`` and lost on a Redis flush;
    the durable per-call record is still WS-6d. A shared run carries no
    ``instance``, so it folds into the totals and no dimension row, exactly as
    before.
    """
    cost = evt.get("cost_usd")
    if not isinstance(cost, (int, float)):
        return
    tokens = int(evt.get("tokens") or 0)
    model = str(evt.get("model") or "unknown")
    source = str(evt.get("source") or "unattributed")
    agent = str(evt.get("agent") or "")
    instance = str(evt.get("instance") or "")
    pipe = r.pipeline(transaction=False)
    pipe.hincrbyfloat(hkey, "total|cost", float(cost))
    pipe.hincrby(hkey, "total|tokens", tokens)
    pipe.hincrby(hkey, "total|calls", 1)
    pipe.hincrbyfloat(hkey, f"model|{model}|cost", float(cost))
    pipe.hincrby(hkey, f"model|{model}|tokens", tokens)
    pipe.hincrby(hkey, f"model|{model}|calls", 1)
    pipe.hincrbyfloat(hkey, f"source|{source}|cost", float(cost))
    pipe.hincrby(hkey, f"source|{source}|calls", 1)
    if agent:
        pipe.hincrbyfloat(hkey, f"agent|{agent}|cost", float(cost))
        pipe.hincrby(hkey, f"agent|{agent}|calls", 1)
    if instance:
        pipe.hincrbyfloat(hkey, f"instance|{instance}|cost", float(cost))
        pipe.hincrby(hkey, f"instance|{instance}|calls", 1)
    pipe.expire(hkey, COST_TTL_SECONDS)
    await pipe.execute()


def _build_event(fields: dict[str, Any]) -> dict[str, Any]:
    """Merge run-context defaults and stamp a timestamp. Pure; never raises."""
    ctx = {}
    try:
        ctx = get_run_context()
    except Exception:  # noqa: BLE001
        ctx = {}
    evt: dict[str, Any] = {}
    for k in _INHERIT:
        v = fields.get(k)
        if v is None:
            v = ctx.get(k)
        if v is not None:
            evt[k] = v
    # Non-inherited fields (kind, phase, model, tier, status, tokens, …).
    for k, v in fields.items():
        if k not in _INHERIT and v is not None:
            evt[k] = v
    evt.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return evt


def _is_cost_event(evt: dict[str, Any]) -> bool:
    """A priced model call: the only event that folds into the cost rollup."""
    return evt.get("kind") == "model" and evt.get("cost_usd") is not None


async def _axadd(evt: dict[str, Any], organization_id: str | None) -> None:
    """Append one event to the tenant's stream + maintain the presence key.

    With no org, the stream and the presence writes are skipped. A priced
    call with no org (the gateway's deployment-key ``/v1/chat/completions``)
    still counts: it folds into the deployment-level unattributed hash, never
    into the hash of a tenant (H-201e).
    """
    if organization_id:
        with organization_scope(organization_id):
            r = get_tenant_redis()
            await r.xadd(
                _stream_key(),
                {"event": json.dumps(evt, default=str)},
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            # Presence: an agent run — or an app AI call, RFC
            # docs/app-workshop/README.md §4.6 — is "live" between its start
            # and end events. Apps publish through ``publish_app_activity``
            # (kind="app") with the same phase/run_id contract as an agent
            # run, so this one gate covers both actor kinds identically — the
            # office view and /active treat a working app exactly like a
            # working agent, no separate presence mechanism needed.
            run_id = evt.get("run_id")
            if evt.get("kind") in ("agent", "app") and run_id:
                if evt.get("phase") == "start":
                    await r.set(_live_key(str(run_id)), json.dumps(evt, default=str),
                                ex=LIVE_TTL_SECONDS)
                elif evt.get("phase") == "end":
                    await r.delete(_live_key(str(run_id)))
    # Cost rollup (H-201e): a priced call folds into the tenant's daily hash.
    # With no org it folds into the unattributed hash, so no spend is lost and
    # no tenant reads it.
    if _is_cost_event(evt):
        try:
            if organization_id:
                with organization_scope(organization_id):
                    await _record_cost(get_tenant_redis(), _cost_key(_today()), evt)
            else:
                await _record_cost(
                    _get_client(), _unattributed_cost_key(_today()), evt,
                )
        except Exception:  # noqa: BLE001 — cost rollup is best-effort
            pass


def publish_activity(*, organization_id: str | None = None, **fields: Any) -> None:
    """Publish one activation event to the tenant's feed. Best-effort, non-blocking.

    Call from anywhere (sync or async). Common fields:
      ``kind``     — "agent" | "app" | "model" (required to be useful)
      ``phase``    — "start" | "end"   (agents + apps; models are single events)
      ``model``, ``tier``              — model activations
      ``status``, ``duration_ms``      — agent "end" events
      ``agent``, ``user``, ``thread_id``, ``run_id``, ``source``, ``instance``
      — inherited from the current run context when omitted.

    ``organization_id`` names the tenant. Without it, the bound tenant
    (``acb_common.db.current_tenant()``) is captured NOW, in the caller's
    context, and handed to the scheduled write. With neither, no stream or
    presence key is written (H-201c): an event with no tenant has no key to
    go to. A priced model call with no tenant still counts. It folds into the
    deployment-level ``cc:_unattributed:cost:<day>`` hash (H-201e).

    Never raises and never blocks the caller: the Redis write is scheduled onto
    the running event loop (or a throwaway loop if none is running). A drop is
    acceptable — the durable record lives in ``agent_run`` and the logs.
    """
    org = _capture_org(organization_id)
    try:
        evt = _build_event(fields)
    except Exception:  # noqa: BLE001
        return
    if not org and not _is_cost_event(evt):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        try:
            task = loop.create_task(_axadd(evt, org))
            # Swallow any exception so it isn't reported as "never retrieved".
            task.add_done_callback(
                lambda t: None if t.cancelled() else t.exception()
            )
        except Exception:  # noqa: BLE001
            pass
    elif org:
        # No running loop (rare — e.g. a sync worker). Fire a one-shot loop
        # with its own short-lived client so we don't touch the shared one.
        async def _one_shot() -> None:
            raw = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            try:
                with organization_scope(org):
                    await TenantRedis(raw).xadd(
                        _stream_key(),
                        {"event": json.dumps(evt, default=str)},
                        maxlen=STREAM_MAXLEN,
                        approximate=True,
                    )
            finally:
                await raw.aclose()
        try:
            asyncio.run(_one_shot())
        except Exception:  # noqa: BLE001
            pass


async def _apply_presence_patch(
    r: TenantRedis, run_id: str, patch: dict[str, Any],
) -> None:
    """Merge *patch* into an EXISTING presence snapshot and refresh its TTL.

    The caller binds the org, so the key is ``cc:<org>:activity:live:<run>``.

    A miss is a deliberate no-op: the key is absent when the run has already
    ended (or its start event was dropped), and re-creating it there would
    resurrect a finished run in the "running now" panel. ``xx=True`` closes
    the same race on the write side, so the worst case is that the patch is
    lost — never that presence outlives the run.
    """
    key = _live_key(run_id)
    raw = await r.get(key)
    if not raw:
        return
    try:
        snap = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(snap, dict):
        return
    snap.update(patch)
    await r.set(
        key, json.dumps(snap, default=str),
        ex=LIVE_TTL_SECONDS, xx=True,
    )


async def _axpresence(
    run_id: str, patch: dict[str, Any], organization_id: str,
) -> None:
    with organization_scope(organization_id):
        await _apply_presence_patch(get_tenant_redis(), run_id, patch)


def refresh_run_presence(
    run_id: str, *, organization_id: str | None = None, **fields: Any,
) -> None:
    """Patch the live presence snapshot of an in-flight run. Best-effort.

    The presence key is written once, from the ``phase="start"`` event body.
    A run that only learns a correlation field *after* it starts — the
    executor resolves ``instance`` from ``loaded.config``, which is not
    available at the run boundary (WS-6a) — would otherwise leave that field
    permanently absent from ``active_runs()``, ``/observability/active`` and
    the office roster, for **every** run.

    This patches the snapshot in place instead of re-publishing the start
    event: a duplicate ``start`` entry is visible to every stream consumer
    (the SSE feed, the office view) and would be read as a second activation.
    Presence is a snapshot, so overwriting it is invisible and idempotent.

    Same contract as :func:`publish_activity` — never raises, never blocks,
    a drop is acceptable. ``None``/empty values are skipped so a caller can
    pass a field unconditionally without writing an empty one. The org is
    captured as in :func:`publish_activity`. With no org, nothing is patched.

    Both writes are loop tasks, so a patch issued before its own start event
    has been flushed finds no key and is dropped. In practice the agent load
    sits between them (many awaits); if it ever loses that race the result is
    the pre-fix behaviour — a presence entry without the field — never a
    corrupt or resurrected one.
    """
    patch = {k: v for k, v in fields.items() if v}
    if not run_id or not patch:
        return
    org = _capture_org(organization_id)
    if not org:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        try:
            task = loop.create_task(_axpresence(run_id, patch, org))
            task.add_done_callback(
                lambda t: None if t.cancelled() else t.exception()
            )
        except Exception:  # noqa: BLE001
            pass
        return

    async def _one_shot() -> None:
        raw = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            with organization_scope(org):
                await _apply_presence_patch(TenantRedis(raw), run_id, patch)
        finally:
            await raw.aclose()

    try:
        asyncio.run(_one_shot())
    except Exception:  # noqa: BLE001
        pass


def _parse_entry(eid: str, fields: dict[str, str]) -> dict[str, Any] | None:
    try:
        evt = json.loads(fields.get("event", "{}"))
    except (json.JSONDecodeError, TypeError):
        return None
    evt["_id"] = eid
    return evt


async def recent_activity(
    limit: int = 100, *, organization_id: str | None,
) -> list[dict[str, Any]]:
    """Return the tenant's most recent activations, oldest-first (backfill).

    ``organization_id`` is required, and the caller takes it from the
    authenticated session. A falsy org gives ``[]``.
    """
    if not organization_id:
        return []
    try:
        with organization_scope(organization_id):
            raw = await get_tenant_redis().xrevrange(
                _stream_key(), count=max(1, min(limit, 500)),
            )
    except (aioredis.ResponseError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for eid, fields in raw:
        evt = _parse_entry(eid, fields)
        if evt is not None:
            out.append(evt)
    out.reverse()  # xrevrange is newest-first; feed wants chronological
    return out


#: Seconds that :func:`read_activity_since` waits after a failed read, so a
#: caller loop cannot spin. Module-level, so a test can patch it.
READ_ERROR_BACKOFF_SECONDS = 1.0


async def read_activity_since(
    since_id: str = "$",
    *,
    organization_id: str | None,
    block_ms: int = 15_000,
    count: int = 200,
) -> tuple[list[dict[str, Any]], str]:
    """Block up to *block_ms* for new activations of the tenant after *since_id*.

    Returns ``(events, new_cursor)``. On timeout returns ``([], since_id)`` so
    the caller can emit a heartbeat and loop. Never raises. A falsy
    ``organization_id`` returns ``([], since_id)`` at once. A failed read
    waits :data:`READ_ERROR_BACKOFF_SECONDS` and then returns
    ``([], since_id)``, so the loop of the caller cannot spin.

    The blocking ``XREAD`` holds its connection for up to *block_ms*, so it
    runs on the stream pool (``get_tenant_redis(blocking=True)``). On the
    shared pool, a few open feeds would take every connection.
    """
    cursor = since_id
    if not organization_id:
        return [], cursor
    try:
        with organization_scope(organization_id):
            raw = await get_tenant_redis(blocking=True).xread(
                {_stream_key(): cursor}, count=count, block=block_ms,
            )
    except Exception:  # noqa: BLE001 — stream missing / transient
        # A failed read returns at once: a full blocking pool raises
        # "Too many connections" with no wait. Sleep here, so a caller that
        # loops (the SSE feed) does not spin. The normal empty answer after
        # BLOCK already waited, so it does not sleep.
        await asyncio.sleep(READ_ERROR_BACKOFF_SECONDS)
        return [], cursor
    events: list[dict[str, Any]] = []
    for _name, entries in raw or []:
        for eid, fields in entries:
            evt = _parse_entry(eid, fields)
            if evt is not None:
                events.append(evt)
            cursor = eid
    return events, cursor


#: Keys read by one MGET in :func:`active_runs`.
_ACTIVE_BATCH = 200


async def active_runs(*, organization_id: str | None) -> list[dict[str, Any]]:
    """Return the tenant's agent runs in flight (presence keys not yet expired).

    Newest-first by start time. Self-healing: a run whose "end" was lost simply
    ages out after ``LIVE_TTL_SECONDS``. Each entry is the run's start event as
    last patched by :func:`refresh_run_presence` — which is how a late-resolved
    field like ``instance`` gets here at all.

    ``organization_id`` is required, and a falsy org gives ``[]``. The SCAN
    pattern is :func:`acb_common.tenant_redis.match`, so it cannot leave the
    tenant's prefix. Each key it gives is built again with ``key``, because the
    client takes a ``TenantKey`` only.
    """
    out: list[dict[str, Any]] = []
    if not organization_id:
        return out
    try:
        with organization_scope(organization_id):
            r = get_tenant_redis()
            head = _live_key("x").value[:-1]
            batch: list[TenantKey] = []
            vals: list[Any] = []
            async for raw in r.scan_iter(
                match(ACTIVITY_NAMESPACE, LIVE_PART), count=_ACTIVE_BATCH,
            ):
                name = raw.decode() if isinstance(raw, bytes) else str(raw)
                run_id = name[len(head):] if name.startswith(head) else ""
                if not run_id:
                    continue
                try:
                    batch.append(_live_key(run_id))
                except ValueError:
                    continue
                if len(batch) >= _ACTIVE_BATCH:
                    vals.extend(await r.mget(batch))
                    batch = []
            if batch:
                vals.extend(await r.mget(batch))
    except Exception:  # noqa: BLE001
        return out
    for v in vals:
        if not v:
            continue
        try:
            out.append(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            continue
    out.sort(key=lambda e: str(e.get("ts", "")), reverse=True)
    return out


def _split_field(field: str) -> tuple[str, str, str]:
    """Parse a rollup hash field ``dim|name|metric`` → (dim, name, metric).

    ``name`` may itself contain ``/`` (provider-prefixed models) but not ``|``,
    so joining the middle segments is safe. ``total|cost`` → ("total","","cost").
    """
    parts = field.split("|")
    if len(parts) < 2:
        return field, "", ""
    return parts[0], "|".join(parts[1:-1]), parts[-1]


async def _read_cost_hashes(
    dates: list[str], organization_id: str | None,
) -> list[dict[str, Any]]:
    """The tenant's daily cost hashes for *dates*, in order. Never raises.

    A falsy or invalid org reads nothing, so each day is ``{}``. The keys are
    built under ``organization_scope``, so the read cannot leave the tenant's
    prefix. It never reads the unattributed hash or the old global hash.
    """
    empty: list[dict[str, Any]] = [{} for _ in dates]
    if not organization_id:
        return empty
    out: list[dict[str, Any]] = []
    try:
        with organization_scope(organization_id):
            r = get_tenant_redis()
            for d in dates:
                try:
                    out.append(await r.hgetall(_cost_key(d)) or {})
                except Exception:  # noqa: BLE001
                    out.append({})
    except Exception:  # noqa: BLE001 — an invalid org id reads nothing
        return empty
    return out


async def cost_summary(
    days: int = 7, *, organization_id: str | None,
) -> dict[str, Any]:
    """Aggregate the last *days* daily cost rollups of ONE tenant.

    ``organization_id`` is required (H-201e). The route passes the viewer's
    org from the session. A falsy org gives the empty summary: every day and
    every total is zero. The summary never includes the unattributed hash.

    Returns per-day totals (chronological), plus by-model, by-source, by-agent
    and by-instance rollups and grand totals. ``by_instance`` is keyed by the
    tenant partition (``u:<email>`` / ``t:<team>``); shared runs carry no
    partition and appear only in the totals. Costs are in USD (best-effort
    litellm pricing). Never raises — missing/short history just yields zeroes.
    """
    days = max(1, min(days, 90))
    today = datetime.now(timezone.utc).date()
    out_days: list[dict[str, Any]] = []
    by_model: dict[str, dict[str, float]] = {}
    by_source: dict[str, dict[str, float]] = {}
    by_agent: dict[str, dict[str, float]] = {}
    by_instance: dict[str, dict[str, float]] = {}
    # Dimensions whose rollup is just cost+calls keyed by name (unlike `model`,
    # which also carries tokens and a per-day breakdown).
    flat_dims = {
        "source": by_source, "agent": by_agent, "instance": by_instance,
    }
    totals = {"cost": 0.0, "tokens": 0, "calls": 0}

    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    hashes = await _read_cost_hashes(dates, organization_id)
    for d, h in zip(dates, hashes, strict=True):
        day_rec: dict[str, Any] = {
            "date": d, "cost": 0.0, "tokens": 0, "calls": 0, "by_model": {},
        }
        for field, raw in (h or {}).items():
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            dim, name, metric = _split_field(field)
            if dim == "total":
                if metric == "cost":
                    day_rec["cost"] = round(v, 6)
                    totals["cost"] += v
                elif metric == "tokens":
                    day_rec["tokens"] = int(v)
                    totals["tokens"] += int(v)
                elif metric == "calls":
                    day_rec["calls"] = int(v)
                    totals["calls"] += int(v)
            elif dim == "model" and name:
                m = by_model.setdefault(name, {"cost": 0.0, "tokens": 0, "calls": 0})
                dm = day_rec["by_model"].setdefault(
                    name, {"cost": 0.0, "tokens": 0, "calls": 0})
                if metric == "cost":
                    m["cost"] += v
                    dm["cost"] = round(v, 6)
                elif metric == "tokens":
                    m["tokens"] += int(v)
                    dm["tokens"] = int(v)
                elif metric == "calls":
                    m["calls"] += int(v)
                    dm["calls"] = int(v)
            elif name and dim in flat_dims:
                # source / agent / instance accumulate identically — one branch
                # instead of three keeps this loop from growing a copy-pasted
                # arm per dimension (it is already a C901 hotspot).
                e = flat_dims[dim].setdefault(name, {"cost": 0.0, "calls": 0})
                if metric == "cost":
                    e["cost"] += v
                elif metric == "calls":
                    e["calls"] += int(v)
        out_days.append(day_rec)

    out_days.reverse()  # oldest → newest for a left-to-right chart
    for m in by_model.values():
        m["cost"] = round(m["cost"], 6)
    for s in by_source.values():
        s["cost"] = round(s["cost"], 6)
    for bucket in flat_dims.values():
        for e in bucket.values():
            e["cost"] = round(e["cost"], 6)
    totals["cost"] = round(totals["cost"], 6)
    return {
        "days": out_days,
        "by_model": by_model,
        "by_source": by_source,
        "by_agent": by_agent,
        "by_instance": by_instance,
        "totals": totals,
        "window_days": days,
    }
