"""Custom Apps · runtime — the App Runtime API the ``cc`` bridge calls.

View-gated: a "viewer" is a person OR a platform agent (RFC §4.7). Identity,
app-scoped JSON storage (shared + per-user partitions), and metered LLM
completions — every AI call rides the platform's own routing
(``acb_llm.acompletion_with_fallback``: tier aliases, BYOK keys, context
fitting, cost tracking) and is budgeted against the app's monthly token
allowance from ``app_audit`` (RFC §4.5, §4.6).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from acb_auth import UserContext
from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from gateway.routes.apps._common import (
    MAX_AI_OUTPUT_TOKENS,
    MAX_STORAGE_ROWS_PER_APP,
    MAX_STORAGE_VALUE_BYTES,
    TABLE_NAME_RE,
    _log,
    _tenant_session,
    _uid,
    app_workspace_or_none,
    get_app_or_404,
    iso,
    parse_db_manifest,
    publish_app_activity,
    read_workspace_manifest,
    record_app_audit,
    require_app_user,
    require_app_viewer,
    resolve_ai_budget,
    resolve_ai_tier,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

_MAX_KEY_CHARS = 256


# ── Models ───────────────────────────────────────────────────────────────────

class StorageRow(BaseModel):
    key: str
    value: Any
    user_scope: str = ""
    updated_by: str = ""
    updated_at: str = ""


class StoragePut(BaseModel):
    value: Any
    scope: str = "shared"       # 'shared' | 'user'


class AiCompleteRequest(BaseModel):
    prompt: str | None = None
    messages: list[dict[str, Any]] | None = None
    tier: str | None = None
    max_tokens: int | None = None


# ── Shared helpers ───────────────────────────────────────────────────────────

def _validate_table(table: str) -> str:
    if not TABLE_NAME_RE.fullmatch(table):
        raise HTTPException(
            status_code=422,
            detail="table must match [a-z0-9_]{1,48}",
        )
    return table


def _partition(scope: str, user: UserContext) -> str:
    """'' for the shared partition; the caller's email for scope=user."""
    return _uid(user) if scope == "user" else ""


def _row_out(row: Any) -> StorageRow:
    value = row.value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            value = json.loads(value)
    return StorageRow(
        key=row.key,
        value=value,
        user_scope=row.user_scope or "",
        updated_by=row.updated_by or "",
        updated_at=iso(row.updated_at) or "",
    )


def _effective_manifest(row: Any) -> dict[str, Any]:
    """The workspace ``app.json``, else the stored manifest. A row with no
    folder reads the stored manifest, never the working directory (WS-STU
    A3). Fence: ``test_studio_packages_store.py``."""
    workspace = app_workspace_or_none(row)
    return (
        (read_workspace_manifest(workspace) if workspace is not None else None)
        or parse_db_manifest(row.manifest)
    )


async def _month_ai_usage(db: Any, app_id: str) -> dict[str, Any]:
    row = (await db.execute(
        text(
            """SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost,
                      COUNT(*) AS calls
               FROM app_audit
               WHERE app_id = :app_id AND kind = 'ai'
                 AND at >= date_trunc('month', now())"""
        ),
        {"app_id": app_id},
    )).fetchone()
    return {
        "tokens": int(row.tokens or 0),
        "cost_usd": float(row.cost or 0),
        "calls": int(row.calls or 0),
    }


# ── Identity ─────────────────────────────────────────────────────────────────

@router.get("/{slug}/me")
async def app_me(
    slug: str,
    user: UserContext = Depends(require_app_viewer),
) -> dict[str, str]:
    """Who is viewing — what ``cc.user()`` returns."""
    async with _tenant_session() as db:
        await get_app_or_404(db, slug, user)
    return {"email": _uid(user), "role": user.role.value}


# ── Storage (cc.storage) ─────────────────────────────────────────────────────

@router.get("/{slug}/data/{table}")
async def list_storage_rows(
    slug: str,
    table: str,
    scope: str = Query("shared", description="'shared' (default) | 'user'"),
    user: UserContext = Depends(require_app_viewer),
) -> dict[str, list[StorageRow]]:
    _validate_table(table)
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        rows = (await db.execute(
            text(
                """SELECT key, value, user_scope, updated_by, updated_at
                   FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND user_scope = :scope
                   ORDER BY key"""
            ),
            {"app_id": str(row.id), "table": table,
             "scope": _partition(scope, user)},
        )).fetchall()
    return {"rows": [_row_out(r) for r in rows]}


@router.get("/{slug}/data/{table}/{key}")
async def get_storage_row(
    slug: str,
    table: str,
    key: str,
    scope: str = Query("shared"),
    user: UserContext = Depends(require_app_viewer),
) -> StorageRow:
    _validate_table(table)
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        rec = (await db.execute(
            text(
                """SELECT key, value, user_scope, updated_by, updated_at
                   FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND key = :key AND user_scope = :scope"""
            ),
            {"app_id": str(row.id), "table": table, "key": key,
             "scope": _partition(scope, user)},
        )).fetchone()
    if rec is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return _row_out(rec)


@router.put("/{slug}/data/{table}/{key}")
async def put_storage_row(
    slug: str,
    table: str,
    key: str,
    body: StoragePut,
    user: UserContext = Depends(require_app_viewer),
) -> StorageRow:
    _validate_table(table)
    if not key or len(key) > _MAX_KEY_CHARS:
        raise HTTPException(status_code=422, detail="Invalid key")
    if body.scope not in ("shared", "user"):
        raise HTTPException(status_code=422, detail="scope must be shared|user")
    encoded = json.dumps(body.value, default=str)
    if len(encoded.encode("utf-8")) > MAX_STORAGE_VALUE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Value too large. Maximum is {MAX_STORAGE_VALUE_BYTES} bytes.",
        )
    partition = _partition(body.scope, user)
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        # Per-app row quota. Overwrites of an existing key stay allowed at the
        # cap so a full app can still update (not brick) its own data.
        exists = (await db.execute(
            text(
                """SELECT 1 FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND key = :key AND user_scope = :scope"""
            ),
            {"app_id": str(row.id), "table": table, "key": key,
             "scope": partition},
        )).fetchone()
        if exists is None:
            count = (await db.execute(
                text("SELECT COUNT(*) FROM app_data WHERE app_id = :app_id"),
                {"app_id": str(row.id)},
            )).scalar_one()
            if int(count) >= MAX_STORAGE_ROWS_PER_APP:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "storage_quota_exhausted",
                        "rows": int(count),
                        "cap": MAX_STORAGE_ROWS_PER_APP,
                    },
                )
        rec = (await db.execute(
            text(
                """INSERT INTO app_data
                   (app_id, table_name, key, value, user_scope, updated_by)
                   VALUES (:app_id, :table, :key, :value ::jsonb, :scope, :by)
                   ON CONFLICT (app_id, table_name, key, user_scope)
                   DO UPDATE SET value = EXCLUDED.value,
                                 updated_by = EXCLUDED.updated_by,
                                 updated_at = now()
                   RETURNING key, value, user_scope, updated_by, updated_at"""
            ),
            {"app_id": str(row.id), "table": table, "key": key,
             "value": encoded, "scope": partition, "by": _uid(user)},
        )).fetchone()
    await record_app_audit(
        app_id=str(row.id), user_email=_uid(user), kind="storage",
        detail={"table": table, "key": key, "op": "put"},
    )
    return _row_out(rec)


@router.delete("/{slug}/data/{table}/{key}")
async def delete_storage_row(
    slug: str,
    table: str,
    key: str,
    scope: str = Query("shared"),
    user: UserContext = Depends(require_app_viewer),
) -> dict[str, bool]:
    _validate_table(table)
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        result = await db.execute(
            text(
                """DELETE FROM app_data
                   WHERE app_id = :app_id AND table_name = :table
                     AND key = :key AND user_scope = :scope"""
            ),
            {"app_id": str(row.id), "table": table, "key": key,
             "scope": _partition(scope, user)},
        )
    deleted = bool(getattr(result, "rowcount", 0))
    if deleted:
        await record_app_audit(
            app_id=str(row.id), user_email=_uid(user), kind="storage",
            detail={"table": table, "key": key, "op": "delete"},
        )
    return {"deleted": deleted}


# ── AI (cc.ai.complete) ──────────────────────────────────────────────────────

#: The 502 detail of a completion with no text (task 1128).
EMPTY_AI_ANSWER = "The model returned no text. Try again."

def _build_messages(body: AiCompleteRequest) -> list[dict[str, Any]]:
    if body.messages:
        msgs: list[dict[str, Any]] = []
        for m in body.messages[:50]:
            role = str(m.get("role") or "user")
            if role not in ("system", "user", "assistant"):
                role = "user"
            msgs.append({"role": role, "content": str(m.get("content") or "")})
        if msgs:
            return msgs
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(
            status_code=422, detail="prompt or messages required",
        )
    return [{"role": "user", "content": prompt}]


def _usage_from_response(resp: Any) -> tuple[int, int]:
    try:
        usage = resp.get("usage") or {}
        get = usage.get if hasattr(usage, "get") else (
            lambda k: getattr(usage, k, None)
        )
        return int(get("prompt_tokens") or 0), int(get("completion_tokens") or 0)
    except Exception:
        return 0, 0


def _cost_from_response(model: str, resp: Any) -> float:
    """Best-effort USD cost via acb_llm's pricer; 0 when unknown."""
    try:
        from acb_llm.client import _compute_cost, _usage_stats
        cost = _compute_cost(model, resp, _usage_stats(resp))
        return float(cost) if cost is not None else 0.0
    except Exception:
        return 0.0


@router.post("/{slug}/ai/complete")
async def ai_complete(
    slug: str,
    body: AiCompleteRequest,
    user: UserContext = Depends(require_app_viewer),
) -> Any:
    """Run one completion for the app, on the platform's tier routing, within
    the app's monthly token budget (429 ``ai_budget_exhausted`` when spent)."""
    messages = _build_messages(body)
    max_tokens = min(max(int(body.max_tokens or 1000), 1), MAX_AI_OUTPUT_TOKENS)
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        manifest = _effective_manifest(row)
        budget = resolve_ai_budget(manifest)
        used = (await _month_ai_usage(db, str(row.id)))["tokens"]
    if used >= budget:
        return JSONResponse(
            status_code=429,
            content={"error": "ai_budget_exhausted",
                     "used": used, "budget": budget},
        )
    tier = resolve_ai_tier(body.tier, manifest)
    from acb_llm import acompletion_with_fallback

    # Presence (RFC §4.6): a start/end pair around the actual provider call —
    # NOT the budget check above, which never reaches the LLM — makes this app
    # "working" in the observability office view for the call's real duration,
    # the same start/end contract an agent run uses. A fresh run_id per call so
    # concurrent completions from the same app (different users) don't collide
    # on one presence key.
    run_id = str(uuid.uuid4())
    publish_app_activity(slug, user=_uid(user), phase="start", run_id=run_id, action="ai")
    try:
        resp, used_model = await acompletion_with_fallback(
            model=tier,
            fallback_model="tier-fast",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
            # Every custom app's AI call runs through this SAME module
            # regardless of slug, so the stack-inferred source can only ever
            # resolve to the shared "apps" package — an explicit per-slug
            # source is the only way cost attribution (/observability/cost's
            # "by app" view) can tell one app's spend from another's.
            source=f"app:{slug}",
            # Writing: thinking off, so the app's cap (at most
            # MAX_AI_OUTPUT_TOKENS) holds the answer on any tier. The request
            # has no field that asks for thinking (task 1128).
            job="writing",
        )
    except Exception as exc:
        publish_app_activity(
            slug, user=_uid(user), phase="end", run_id=run_id,
            action="ai", status="error",
        )
        _log.warning("apps.ai_failed", slug=slug, error=str(exc)[:200])
        raise HTTPException(
            status_code=502, detail="LLM completion failed",
        ) from exc
    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        content = ""
    tokens_in, tokens_out = _usage_from_response(resp)
    cost = _cost_from_response(used_model, resp)
    await record_app_audit(
        app_id=str(row.id), user_email=_uid(user), kind="ai",
        app_version=row.live_version,
        tokens_in=tokens_in, tokens_out=tokens_out,
        cost_usd=cost, model=used_model,
        detail={"surface": "app-runtime", "tier": tier},
    )
    if not content.strip():
        # The tokens are spent and recorded above, but there is no text. A
        # 200 with "" reads as a real answer in the app (task 1128).
        publish_app_activity(
            slug, user=_uid(user), phase="end", run_id=run_id,
            action="ai", status="error",
        )
        _log.warning("apps.ai_empty_answer", slug=slug, model=used_model)
        raise HTTPException(status_code=502, detail=EMPTY_AI_ANSWER)
    publish_app_activity(
        slug, user=_uid(user), phase="end", run_id=run_id, action="ai",
        model=used_model, tokens=tokens_in + tokens_out,
    )
    return {
        "text": content,
        "model": used_model,
        "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out},
    }


@router.get("/{slug}/usage")
async def app_usage(
    slug: str,
    user: UserContext = Depends(require_app_viewer),
) -> dict[str, Any]:
    """This calendar month's AI usage + budget (the info-popover numbers)."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user)
        usage = await _month_ai_usage(db, str(row.id))
    return {
        "month_tokens": usage["tokens"],
        "month_cost_usd": round(usage["cost_usd"], 6),
        "month_calls": usage["calls"],
        "budget_tokens": resolve_ai_budget(_effective_manifest(row)),
    }
