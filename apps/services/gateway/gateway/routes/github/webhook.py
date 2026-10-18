"""``POST /webhooks/github`` — the receiver of the Metorite GitHub App (WS-54 GH-4).

Spec: ``project-docs/specs/github_integration.md`` §8 and §13.1.

⚠️ **A public route.** It is in ``main.PUBLIC_ROUTES``, because GitHub sends no
session. It authenticates itself with the HMAC of §8.2, BEFORE it parses or
reads anything. With ``github_app_enabled`` off it answers 404 and checks no
signature.

**The order of checks (§8.2).** Each one is a test in
``tests/unit/test_github_webhook_r8.py``:

1. **The size cap.** The body is read as a stream and stops at
   :data:`GITHUB_WEBHOOK_MAX_BYTES` (5 MiB). A larger body is 413, and no HMAC
   work runs.
2. **The signature.** ``X-Hub-Signature-256`` must be ``sha256=`` and 64 hex
   characters. The HMAC-SHA256 of the RAW bytes with the webhook secret is
   compared with ``hmac.compare_digest``, never ``==``. A missing, malformed or
   wrong signature is 401. No secret on the box is 503: it fails closed.
3. **The headers.** An event outside :data:`HANDLED_EVENTS` is 202. A
   ``X-GitHub-Delivery`` that is not a GUID is 400.
4. **The installation.** ``installation.id`` goes to the SECURITY DEFINER
   ``github_installation_org`` (migration 282). No org is 202, and nothing is
   written. The org comes from the database, never from the body.
5. **The tenant.** The org is bound (``bind_tenant`` and the Redis
   ``organization_scope``), and every write runs in ``tenant_session(org)``.
6. **The delivery key.** ``SET key("github", "delivery", <guid>) NX EX
   259200`` (3 days, the redelivery window). A key that exists is 200
   ``{"duplicate": true}``. A handler that fails deletes the key and answers
   500, so a redelivery runs again. A Redis outage logs and goes on, because
   every handler is idempotent (§8.6).
7. **The handler**, with a budget of 5 seconds for its database work. A GitHub
   API read never runs before the answer: it goes to a background task.
8. **The answer.** 200 ``{"ok": true}``.

**Idempotent and ordered (§8.6).** A ``pull_request`` write needs GitHub's
``updated_at`` to be newer than the row's ``pr_updated_at``. The auto-link
writes through the unique key of ``pm_task_refs``. So a replay that passes
the delivery key changes nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from acb_common import get_logger, get_settings, tenant_redis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from gateway import github_sync
from gateway.connections import github as github_connection
from gateway.connections import github_app, store
from gateway.db import bind_tenant, get_db, release_tenant, tenant_session
from sqlalchemy import text
from starlette.background import BackgroundTasks

_log = get_logger("gateway.github.webhook")


def _obj(value: Any) -> dict[str, Any]:
    """*value* when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}

ROUTE = "/webhooks/github"

#: §8.2 step 1. GitHub caps a payload at 25 MB. A pull request payload is far
#: smaller, and the reconcile poll repairs a dropped one.
GITHUB_WEBHOOK_MAX_BYTES = 5 * 1024 * 1024

#: §8.2 step 6: the redelivery window of GitHub, 3 days.
DELIVERY_TTL_S = 259_200

#: §8.2 step 7: the budget of a handler's own database work.
HANDLER_DB_BUDGET_S = 5.0

#: §8.4: the events with a handler. Any other event is 202, ignored.
HANDLED_EVENTS: frozenset[str] = frozenset({
    "installation", "installation_repositories", "pull_request",
    "check_suite", "check_run", "status",
})

#: The ``pull_request`` actions that write (§8.4), and those that auto-link (§10.1).
PR_ACTIONS: frozenset[str] = frozenset({
    "opened", "reopened", "edited", "synchronize", "ready_for_review",
    "converted_to_draft", "closed",
})
AUTOLINK_ACTIONS: frozenset[str] = frozenset({
    "opened", "reopened", "edited", "synchronize", "ready_for_review",
})

_SIGNATURE_RE = re.compile(r"\Asha256=([0-9a-f]{64})\Z")
_GUID_RE = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_FULL_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}\Z")

router = APIRouter(tags=["github"])

#: A job that runs after the answer: a GitHub read and its own write.
Job = Callable[[], Awaitable[None]]


class _TooLarge(Exception):
    pass


def _answer(status: int, body: dict[str, Any], jobs: list[Job] | None = None) -> JSONResponse:
    background = None
    if jobs:
        background = BackgroundTasks()
        for job in jobs:
            background.add_task(job)
    return JSONResponse(body, status_code=status, background=background)


async def _read_capped(request: Request) -> bytes:
    """The raw body, refused past :data:`GITHUB_WEBHOOK_MAX_BYTES` (§8.2 step 1)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > GITHUB_WEBHOOK_MAX_BYTES:
        raise _TooLarge
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > GITHUB_WEBHOOK_MAX_BYTES:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def signature_ok(secret: str, raw: bytes, header: str | None) -> bool:
    """§8.2 step 2. A constant-time compare over the RAW bytes. Pure."""
    match = _SIGNATURE_RE.match(header or "")
    if not match or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, match.group(1))


async def _installation_org(installation_id: int) -> str | None:
    """Which org owns a live installation (§8.3). Tenant DISCOVERY.

    The org is the answer, so the read cannot run bound. It runs on an unbound
    session and reads ONLY through the SECURITY DEFINER
    ``github_installation_org`` of migration 282, which sees through FORCE RLS.
    Only ``acb_app`` may run it. Registered in
    ``test_db_engine_seam.H2_TENANT_DISCOVERY_SITES``.
    """
    db = await get_db()
    try:
        rows = (await db.execute(
            text("SELECT organization_id FROM public.github_installation_org(:iid)"),
            {"iid": int(installation_id)},
        )).fetchall()
    finally:
        await db.close()
    if len(rows) != 1 or rows[0].organization_id is None:
        return None
    return str(rows[0].organization_id)


# ── The delivery key (tenant Redis, R5c) ────────────────────────────────────


def _redis() -> tenant_redis.TenantRedis:
    """The tenant-prefixed client. A test replaces it."""
    return tenant_redis.get_tenant_redis()


async def _claim_delivery(guid: str) -> bool | None:
    """``True`` for a new delivery, ``False`` for a duplicate, ``None`` when
    Redis did not answer (the handlers are idempotent, so the work goes on)."""
    try:
        client = _redis()
        done = await client.set(client.key("github", "delivery", guid), "1",
                                nx=True, ex=DELIVERY_TTL_S)
    except Exception as exc:  # the class only: an outage or a bad client
        _log.warning("github.webhook.dedupe_unavailable", error=type(exc).__name__)
        return None
    return bool(done)


async def _release_delivery(guid: str) -> None:
    try:
        client = _redis()
        await client.delete(client.key("github", "delivery", guid))
    except Exception as exc:  # the class only
        _log.warning("github.webhook.dedupe_release_failed", error=type(exc).__name__)


# ── Parsing ─────────────────────────────────────────────────────────────────


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _repo_full_name(payload: dict[str, Any]) -> str | None:
    repo = payload.get("repository")
    name = repo.get("full_name") if isinstance(repo, dict) else None
    return name.lower() if isinstance(name, str) and _FULL_NAME_RE.match(name) else None


# ── The handlers (§8.4), each in the bound tenant_session(org) ──────────────


async def _org_row(db: Any, org: str, installation_id: int) -> store.Connection | None:
    """The org's ``app`` row of this installation, or ``None``."""
    row = await store.get(db, org, github_connection.PROVIDER, github_connection.OWNING_APP)
    if not github_connection.is_app_row(row) or row is None:
        return None
    if github_app.installation_id_of(row.provider_meta.get("installation_id")) != installation_id:
        return None
    return row


#: §7.5: the status and the code that each installation action writes.
_INSTALLATION_STATUS: dict[str, tuple[str, str | None]] = {
    "deleted": (store.STATUS_DISCONNECTED, "uninstalled"),
    "suspend": (store.STATUS_NEEDS_RECONNECT, "suspended"),
    "unsuspend": (store.STATUS_CONNECTED, None),
}


async def _on_installation(
    db: Any, org: str, installation_id: int, action: str, payload: dict[str, Any],
) -> list[Job]:
    row = await _org_row(db, org, installation_id)
    if row is None:
        return []
    if action in _INSTALLATION_STATUS:
        status, code = _INSTALLATION_STATUS[action]
        await store.set_status(db, org, row.id, status, code)
        if status != store.STATUS_CONNECTED:
            github_app.drop_installation(installation_id)
        _log.info("github.webhook.installation", action=action, installation_id=installation_id)
        return []
    if action == "new_permissions_accepted":
        return [lambda: _refresh_permissions(org, installation_id)]
    return []


async def _refresh_permissions(org: str, installation_id: int) -> None:
    """§7.5: read the permissions as the App, after the answer, then merge them."""
    try:
        facts = await github_app.installation_facts(installation_id)
    except (github_app.GitHubAppNotConfigured, github_app.GitHubCallFailed) as exc:
        _log.info("github.webhook.permissions_unread", error=type(exc).__name__)
        return
    if facts is None:
        return
    async with tenant_session(org) as db:
        row = await _org_row(db, org, installation_id)
        if row is not None:
            await store.merge_provider_meta(db, org, row.id, {
                "permissions": facts["permissions"],
                "repository_selection": facts["repository_selection"],
            })


async def _on_installation_repositories(
    db: Any, org: str, installation_id: int, payload: dict[str, Any],
) -> list[Job]:
    """§7.5: the repository selection refreshes. No repository list is stored."""
    row = await _org_row(db, org, installation_id)
    selection = payload.get("repository_selection")
    if row is not None and selection in ("all", "selected"):
        await store.merge_provider_meta(db, org, row.id, {"repository_selection": selection})
    return []


#: The pull request write lives in ``gateway/github_sync.py``, shared with the
#: reconcile poll (§8.4, §8.6). Re-exported here for the callers of GH-4.
write_pull_request = github_sync.write_pull_request


async def _on_pull_request(
    db: Any, org: str, action: str, payload: dict[str, Any], delivery: str,
) -> list[Job]:
    if action not in PR_ACTIONS:
        return []
    pull_request = payload.get("pull_request")
    full_name = _repo_full_name(payload)
    number = payload.get("number")
    if not isinstance(pull_request, dict) or full_name is None:
        return []
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return []
    if action in AUTOLINK_ACTIONS:
        from gateway.routes.projects import github_autolink

        host = github_autolink.app_host(str(getattr(get_settings(), "app_public_url", "") or ""))
        await github_autolink.link_pull_request(db, org, pull_request, host=host)
    ref = f"{full_name}#{number}"
    # The ONE write path of the facts (github_sync.apply_pull_request). A write
    # that puts the reference in `merged` or `closed` runs GH-6 (§12) in this
    # transaction, and its events go out after the answer, so after the commit.
    _written, outcome = await github_sync.apply_pull_request(
        db, org, ref, pull_request, delivery=delivery, action=action,
    )
    jobs: list[Job] = [outcome.emit_all] if outcome is not None and outcome.events else []
    if action != "closed":
        # GH-5 (§8.5): a new or moved head gets a checks read after the answer.
        sha = str(_obj(pull_request.get("head")).get("sha") or "").lower()
        if action in CHECKS_ACTIONS and _SHA_RE.match(sha):
            jobs.append(github_sync.checks_job(org, full_name, sha))
    return jobs


#: §8.4: the checks events, and the actions that schedule a read.
CHECK_EVENTS: dict[str, frozenset[str] | None] = {
    "check_suite": frozenset({"completed"}),
    "check_run": frozenset({"created", "completed"}),
    "status": None,  # every state
}
#: The ``pull_request`` actions whose head needs a checks read.
CHECKS_ACTIONS: frozenset[str] = frozenset({
    "opened", "reopened", "synchronize", "ready_for_review",
})

_HEAD_HELD_SQL = (
    "SELECT 1 FROM pm_task_refs WHERE organization_id = CAST(:org AS uuid) "
    "AND provider = 'github' AND kind = 'pull_request' AND head_sha = :sha LIMIT 1"
)


async def _on_checks(
    db: Any, org: str, event: str, action: str, payload: dict[str, Any],
) -> list[Job]:
    """§8.4: schedule a checks read for a head that a reference holds. A head
    that no reference holds does nothing."""
    allowed = CHECK_EVENTS[event]
    if allowed is not None and action not in allowed:
        return []
    if event == "status":
        sha = str(payload.get("sha") or "")
    else:
        sha = str(_obj(payload.get(event)).get("head_sha") or "")
    sha = sha.lower()
    full_name = _repo_full_name(payload)
    if not _SHA_RE.match(sha) or full_name is None:
        return []
    held = (await db.execute(text(_HEAD_HELD_SQL), {"org": org, "sha": sha})).first()
    if held is None:
        return []
    return [github_sync.checks_job(org, full_name, sha)]


async def _handle(
    db: Any, org: str, event: str, payload: dict[str, Any], installation_id: int,
    delivery: str,
) -> list[Job]:
    """One event, in the bound session. Returns the jobs that run after the answer."""
    action = str(payload.get("action") or "")
    if event == "installation":
        return await _on_installation(db, org, installation_id, action, payload)
    if event == "installation_repositories":
        return await _on_installation_repositories(db, org, installation_id, payload)
    if event == "pull_request":
        return await _on_pull_request(db, org, action, payload, delivery)
    if event in CHECK_EVENTS:
        return await _on_checks(db, org, event, action, payload)
    return []


async def _run_handler(
    org: str, event: str, payload: dict[str, Any], installation_id: int, delivery: str,
) -> list[Job]:
    async def _work() -> list[Job]:
        async with tenant_session(org) as db:
            return await _handle(db, org, event, payload, installation_id, delivery)

    return await asyncio.wait_for(_work(), timeout=HANDLER_DB_BUDGET_S)


# ── The route ───────────────────────────────────────────────────────────────


@router.post(ROUTE)
async def receive_github_webhook(request: Request) -> JSONResponse:
    """See the module doc for the order of the checks."""
    if not github_app.app_enabled():
        return _answer(404, {"detail": "Not Found"})
    # 1. The size cap, before any HMAC work.
    try:
        raw = await _read_capped(request)
    except _TooLarge:
        _log.warning("github.webhook.too_large")
        return _answer(413, {"detail": "too_large"})
    delivery = request.headers.get("x-github-delivery", "")
    # 2. The signature, over the raw bytes, before any parse.
    secret = str(getattr(get_settings(), "github_app_webhook_secret", "") or "")
    if not secret:
        _log.warning("github.webhook.no_secret")
        return _answer(503, {"detail": "not_configured"})
    if not signature_ok(secret, raw, request.headers.get("x-hub-signature-256")):
        _log.warning("github.webhook.bad_signature", delivery=delivery[:64])
        return _answer(401, {"detail": "bad_signature"})
    # 3. The headers.
    event = request.headers.get("x-github-event", "")
    if event not in HANDLED_EVENTS:
        _log.info("github.webhook.ignored", github_event=event[:64])
        return _answer(202, {"ignored": True})
    if not _GUID_RE.match(delivery):
        return _answer(400, {"detail": "bad_delivery"})
    # 4. The installation, and the org through the definer function only.
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return _answer(400, {"detail": "bad_json"})
    if not isinstance(payload, dict):
        return _answer(400, {"detail": "bad_json"})
    installation = payload.get("installation")
    installation_id = github_app.installation_id_of(
        installation.get("id") if isinstance(installation, dict) else None)
    org = await _installation_org(installation_id) if installation_id is not None else None
    if org is None or installation_id is None:
        _log.info("github.webhook.unknown_installation", delivery=delivery,
                  github_event=event)
        return _answer(202, {"ignored": True})
    # 5. The tenant: every write after this point is bound (R5).
    token = bind_tenant(org)
    try:
        with tenant_redis.organization_scope(org):
            # 6. The delivery key.
            claimed = await _claim_delivery(delivery)
            if claimed is False:
                return _answer(200, {"duplicate": True})
            # 7. The handler.
            try:
                jobs = await _run_handler(org, event, payload, installation_id, delivery)
            except Exception as exc:
                _log.warning("github.webhook.handler_failed", delivery=delivery,
                             github_event=event, error=type(exc).__name__)
                if claimed:
                    await _release_delivery(delivery)
                return _answer(500, {"detail": "retry"})
    finally:
        release_tenant(token)
    # 8. The answer. A GitHub read runs only after it.
    _log.info("github.webhook.handled", delivery=delivery, github_event=event)
    return _answer(200, {"ok": True}, jobs)


__all__ = [
    "GITHUB_WEBHOOK_MAX_BYTES",
    "HANDLED_EVENTS",
    "ROUTE",
    "receive_github_webhook",
    "router",
    "signature_ok",
    "write_pull_request",
]
