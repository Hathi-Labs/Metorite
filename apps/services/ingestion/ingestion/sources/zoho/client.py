"""Async Zoho CRM client. OAuth refresh-token flow with disk cache.

Auth env: ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN,
ZOHO_API_DOMAIN (default zohoapis.com), ZOHO_ACCOUNTS_URL (default accounts.zoho.com).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from acb_common import get_settings

_TOKEN_CACHE = Path(".zoho_token_cache.json")


def _read_cache() -> str | None:
    if not _TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(data["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < expires - timedelta(minutes=5):
            return str(data["access_token"])
    except Exception:
        return None
    return None


def _write_cache(token: str, expires_in: int) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    _TOKEN_CACHE.write_text(
        json.dumps(
            {
                "access_token": token,
                "expires_at": expires_at.isoformat(),
                "cached_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


async def get_access_token() -> str:
    cached = _read_cache()
    if cached:
        return cached
    s = get_settings()
    if not (s.zoho_client_id and s.zoho_client_secret and s.zoho_refresh_token):
        raise RuntimeError("Zoho credentials missing in env")
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(
            f"{s.zoho_accounts_url}/oauth/v2/token",
            params={
                "refresh_token": s.zoho_refresh_token,
                "client_id": s.zoho_client_id,
                "client_secret": s.zoho_client_secret,
                "grant_type": "refresh_token",
            },
        )
        r.raise_for_status()
        data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"Zoho token refresh failed: {data}")
    _write_cache(data["access_token"], int(data.get("expires_in", 3600)))
    return str(data["access_token"])


async def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {await get_access_token()}",
        "Content-Type": "application/json",
    }


def _with_modified_since(
    headers: dict[str, str], since: datetime | None,
) -> dict[str, str]:
    """Add Zoho's ``If-Modified-Since`` header, in the RFC 1123 form it wants.

    One helper because the record modules and the deleted-records endpoint
    both take the same cursor: a second copy of this formatting is a second
    place for the incremental read to quietly stop being incremental.
    """
    if since is None:
        return headers
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    # e.g. "Tue, 01 Jan 2026 00:00:00 +0000".
    return {
        **headers,
        "If-Modified-Since": since.strftime("%a, %d %b %Y %H:%M:%S %z"),
    }


async def _list_module(
    module: str,
    *,
    per_page: int = 200,
    modified_since: "datetime | None" = None,
) -> list[dict[str, Any]]:
    """Paginated GET of a Zoho module.

    When ``modified_since`` is provided, sends the ``If-Modified-Since`` header
    Zoho honours on Accounts/Deals/Contacts to return only records changed
    after that timestamp — the basis of incremental sync (WBS 1.1).
    """
    s = get_settings()
    out: list[dict[str, Any]] = []
    page = 1
    headers = _with_modified_since(await _headers(), modified_since)
    async with httpx.AsyncClient(timeout=60.0) as http:
        while True:
            r = await http.get(
                f"{s.zoho_api_domain}/crm/v2/{module}",
                headers=headers,
                params={
                    "page": page,
                    "per_page": per_page,
                    # Stable pagination. Zoho's default order is by
                    # Modified_Time DESCENDING, so a record edited (by anyone,
                    # including our own push) between page 1 and page 2 jumps
                    # to the front and shifts every later record back one slot
                    # — the record that was at the page boundary is never
                    # returned. Ascending by the same key we cursor on makes
                    # the sequence append-only for the duration of the pull:
                    # a concurrent edit lands at the END, past the pages we
                    # have already read, and the next cycle picks it up.
                    "sort_by": "Modified_Time",
                    "sort_order": "asc",
                },
            )
            if r.status_code == 204 or r.status_code == 304:
                break
            r.raise_for_status()
            body = r.json()
            rows = body.get("data") or []
            out.extend(rows)
            info = body.get("info") or {}
            if not info.get("more_records") or len(rows) == 0:
                break
            page += 1
    return out


async def list_accounts(*, modified_since: "datetime | None" = None) -> list[dict[str, Any]]:
    return await _list_module("Accounts", modified_since=modified_since)


async def list_deals(*, modified_since: "datetime | None" = None) -> list[dict[str, Any]]:
    return await _list_module("Deals", modified_since=modified_since)


async def list_contacts(*, modified_since: "datetime | None" = None) -> list[dict[str, Any]]:
    return await _list_module("Contacts", modified_since=modified_since)


async def list_leads(*, modified_since: datetime | None = None) -> list[dict[str, Any]]:
    """Zoho Leads — the module the Phase-0 graph mirror never pulled.

    Added for the native CRM (spec ``crm_app.md`` §7.1): ``crm_leads`` is the
    entry point of the whole funnel, so a backfill without it starts the
    pipeline at conversion. Same single ``GET`` as every sibling here — this
    module still reads and never writes; writes live in ``writer.py``.
    """
    return await _list_module("Leads", modified_since=modified_since)


async def list_notes(*, modified_since: "datetime | None" = None) -> list[dict[str, Any]]:
    """Notes are first-class activity rows in Zoho CRM (WBS 1.1)."""
    return await _list_module("Notes", modified_since=modified_since)


async def list_tasks(*, modified_since: "datetime | None" = None) -> list[dict[str, Any]]:
    """Zoho CRM Tasks (not to be confused with Metorite `pm_tasks`)."""
    return await _list_module("Tasks", modified_since=modified_since)


async def list_deleted(
    module: str,
    *,
    since: datetime | None = None,
    kind: str = "all",
    per_page: int = 200,
) -> list[dict[str, Any]]:
    """Records Zoho has DELETED in ``module`` — ``GET /crm/v2/{module}/deleted``.

    A record removed in Zoho simply stops appearing in ``_list_module``, so an
    incremental pull can never see it: the only honest way to propagate a Zoho
    delete is to ask for the tombstones. Rows look like
    ``{"id", "display_name", "type", "deleted_time", "deleted_by": {...}}``.

    ``kind`` is Zoho's ``type`` filter — ``all`` (default), ``recycle`` (still
    restorable) or ``permanent``. ``since`` is sent as ``If-Modified-Since``,
    the same incremental lever the record modules use, so a cycle asks only
    about deletions after its cursor.

    Read-only, like every other function in this module.
    """
    s = get_settings()
    out: list[dict[str, Any]] = []
    page = 1
    headers = _with_modified_since(await _headers(), since)
    async with httpx.AsyncClient(timeout=60.0) as http:
        while True:
            r = await http.get(
                f"{s.zoho_api_domain}/crm/v2/{module}/deleted",
                headers=headers,
                params={"type": kind, "page": page, "per_page": per_page},
            )
            # 204 = nothing deleted; 304 = nothing deleted since the cursor.
            if r.status_code in (204, 304):
                break
            r.raise_for_status()
            body = r.json()
            rows = body.get("data") or []
            out.extend(rows)
            info = body.get("info") or {}
            if not info.get("more_records") or len(rows) == 0:
                break
            page += 1
    return out


async def list_users() -> list[dict[str, Any]]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.get(
            f"{s.zoho_api_domain}/crm/v2/users",
            headers=await _headers(),
            params={"type": "AllUsers"},
        )
        r.raise_for_status()
        return r.json().get("users", [])  # type: ignore[no-any-return]


# ── Pipeline metadata (WS-26f f1) ───────────────────────────────────────────
#
# The tenant's REAL lane order and forecast semantics live in the settings API,
# not on the records. Both reads below are GETs like every sibling above — this
# module still reads and never writes.

#: The settings reads are a deliberate divergence from the ``v2`` path every
#: record reader here uses: ``settings/pipeline`` does not exist on v2. The
#: version is named ONCE, so a tenant that refuses it produces one reported
#: outcome (:class:`ZohoApiVersionError`) rather than a silent retry ladder
#: down the version list.
SETTINGS_API_VERSION = "v8"

#: The OAuth scopes these two reads need. The tenant's refresh token was minted
#: without them (``crm_app.md`` §7.1), so "no scope" is the EXPECTED first
#: outcome — and re-minting the token is the owner's act, which is why the
#: caller has to be able to print the exact string an owner must add.
LAYOUTS_SCOPE = "ZohoCRM.settings.layouts.READ"
PIPELINE_SCOPE = "ZohoCRM.settings.pipeline.READ"

#: Zoho's own error codes. A scope refusal and a version refusal arrive as the
#: same 401/404 shape as any other failure, and telling them apart is the whole
#: of done-when 3 — "the token cannot see this" and "this tenant has no such
#: endpoint" are different sentences to an owner.
_SCOPE_ERROR_CODES = frozenset({"OAUTH_SCOPE_MISMATCH"})
_VERSION_ERROR_CODES = frozenset({"INVALID_URL_PATTERN", "INVALID_MODULE"})


class ZohoScopeError(RuntimeError):
    """The refresh token was not minted with the scope this read needs."""

    def __init__(self, scope: str, detail: str = "") -> None:
        super().__init__(f"Zoho refused the read: the token lacks {scope}")
        self.scope = scope
        self.detail = detail


class ZohoApiVersionError(RuntimeError):
    """The tenant does not serve this endpoint on the version we ask for."""

    def __init__(self, version: str, path: str, detail: str = "") -> None:
        super().__init__(f"Zoho refused /crm/{version}/{path}")
        self.version = version
        self.path = path
        self.detail = detail


def _error_body(response: httpx.Response) -> dict[str, Any]:
    """Zoho's JSON error envelope, or an empty one for a non-JSON body."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _settings_json(
    path: str, params: dict[str, Any], *, scope: str,
) -> dict[str, Any]:
    """One settings GET, with the two refusals it can produce named."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.get(
            f"{s.zoho_api_domain}/crm/{SETTINGS_API_VERSION}/{path}",
            headers=await _headers(),
            params=params,
        )
    if r.status_code in (204, 304):
        return {}
    body = _error_body(r)
    code = str(body.get("code") or "")
    message = str(body.get("message") or "")[:200]
    if code in _SCOPE_ERROR_CODES or (
        r.status_code in (401, 403) and "scope" in message.lower()
    ):
        raise ZohoScopeError(scope, message)
    if code in _VERSION_ERROR_CODES or r.status_code == 404:
        raise ZohoApiVersionError(SETTINGS_API_VERSION, path, message)
    r.raise_for_status()
    return body


async def list_deal_layouts() -> list[dict[str, Any]]:
    """The Deals module's layouts — ``GET settings/layouts?module=Deals``.

    Two things come out of one read: the ``layout_id`` the pipeline call needs,
    and the Stage field's ``pick_list_values``, which is where a per-stage
    ``probability`` lives when the tenant exposes one at all.
    """
    body = await _settings_json(
        "settings/layouts", {"module": "Deals"}, scope=LAYOUTS_SCOPE,
    )
    layouts = body.get("layouts")
    return list(layouts) if isinstance(layouts, list) else []


async def list_deal_pipelines(layout_id: str) -> list[dict[str, Any]]:
    """The pipelines configured on one Deals layout, with their stages.

    ⚠️ The response key is read tolerantly (``pipeline`` is what the API
    documents, ``pipelines`` is what some versions return) because getting it
    wrong reads as "this tenant has no pipelines" — indistinguishable from the
    honest empty answer, and the caller writes nothing on either. Tolerance
    here is not a retry ladder: it is one response, parsed twice.
    """
    body = await _settings_json(
        "settings/pipeline", {"layout_id": layout_id}, scope=PIPELINE_SCOPE,
    )
    for key in ("pipeline", "pipelines"):
        found = body.get(key)
        if isinstance(found, list):
            return list(found)
    return []


__all__ = [
    "LAYOUTS_SCOPE",
    "PIPELINE_SCOPE",
    "SETTINGS_API_VERSION",
    "ZohoApiVersionError",
    "ZohoScopeError",
    "get_access_token",
    "list_accounts",
    "list_contacts",
    "list_deal_layouts",
    "list_deal_pipelines",
    "list_deals",
    "list_deleted",
    "list_leads",
    "list_notes",
    "list_tasks",
    "list_users",
]
