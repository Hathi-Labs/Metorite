"""Custom Apps routes — shared kernel (RFC: docs/app-workshop/README.md).

The shared ``router``, DB infrastructure, auth gate, and the pure helpers the
lifecycle/files/publish/runtime layers share: slug generation, workspace path
containment, the visibility/edit predicates, the AI-budget math, and the
best-effort audit + activity writers. Mirrors ``routes/tasks/core.py`` (the
leaf module: it imports nothing from siblings).

Canonical store: the ``apps`` / ``app_*`` tables from
``infra/postgres/114_custom_apps.sql`` (RFC §4.9). App workspaces live at
``apps_root()/{slug}`` — git-inited folders holding ``app.json`` (the
manifest, RFC §4.1) plus the app's source files.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from acb_auth import UserContext, UserRole, get_current_user
from acb_common import get_logger, get_settings
from fastapi import APIRouter, Depends, HTTPException

# The shared gateway engine (BO-10) — see the DB section below.
from gateway.db import get_db as _get_db
from gateway.db import get_session_factory as _get_session_factory  # noqa: F401

# The tenant-bound seam (BO-10 → MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package for the same reason
# `_get_db` was: every submodule imports it from here BY NAME, which is the
# per-module seam the hermetic tests patch. The tenant comes from the request
# context — bound once in `_with_resolved_access` — so no call site passes
# one (H2). A call outside a bound request raises `TenantUnbound` rather than
# defaulting: fail closed, never "the usual org".
from gateway.db import tenant_session as _tenant_session
from sqlalchemy import text

_log = get_logger("gateway.apps")

router = APIRouter(prefix="/apps", tags=["apps"])

# ── Limits (Phase 0 platform clamps) ─────────────────────────────────────────

MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024        # per workspace file (read/write)
MAX_BUNDLE_BYTES = 5 * 1024 * 1024             # published entry-file snapshot
MAX_STORAGE_VALUE_BYTES = 64 * 1024            # one app_data value (JSON)
MAX_STORAGE_ROWS_PER_APP = 5000                # total app_data rows per app
MAX_AI_OUTPUT_TOKENS = 2000                    # cc.ai.complete max_tokens clamp
DEFAULT_AI_BUDGET_TOKENS = 2_000_000           # monthly tokens when manifest is silent
MAX_AI_BUDGET_TOKENS = 10_000_000              # manifest ai_budget_tokens ceiling

TABLE_NAME_RE = re.compile(r"^[a-z0-9_]{1,48}$")

# Workspace-walk rules shared by the files listing and the durability sync:
# directories never walked (VCS + build/dependency noise — dotdirs are already
# skipped wholesale, same stance as workspace.py's _EXCLUDED_DIRS), and the
# per-walk file ceiling.
WORKSPACE_SKIP_DIRS = frozenset({"node_modules", "dist", "build", "__pycache__"})
MAX_WORKSPACE_FILES = 2000

# Default manifest scopes for a fresh scaffold (RFC §4.1).
DEFAULT_SCOPES = ["identity:read", "storage:app", "ai:tier-1"]

# Manifest/request tier vocabulary → the gateway's tier aliases
# (acb_llm.client._TIER_ALIAS_MAP). Cheapest alias is the fallback.
AI_TIER_ALIASES = ("tier-fast", "tier-balanced", "tier-powerful")
_SCOPE_TIER_MAP = {
    "tier-1": "tier-fast",
    "tier-2": "tier-balanced",
    "tier-3": "tier-powerful",
    "tier-fast": "tier-fast",
    "tier-balanced": "tier-balanced",
    "tier-powerful": "tier-powerful",
}
DEFAULT_AI_TIER = "tier-fast"


# ── DB (the one shared gateway engine — gateway/db.py, BO-10) ────────────────
#
# This package used to build its own engine here with its own 10+20 pool. It now
# has none: `_get_db` / `_get_session_factory` / `_tenant_session` at the top of
# this module are re-exports of the shared seam. Since H2 the request handlers
# acquire sessions through `_tenant_session` only; `_get_db` remains ONLY for
# the named H4-deferred sites (`record_app_audit` below, and `actions.py`'s
# dual-audience dispatch), which `test_db_engine_seam.py` pins by exact count.


# ── Auth gate ────────────────────────────────────────────────────────────────

async def require_app_user(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """The package-wide auth dependency: 401 for anonymous callers.

    ``get_current_user`` never rejects — it labels. Every /apps endpoint needs
    a real principal (an email, or the internal AGENT role) for grants and
    audit attribution, so an unlabelled caller is refused here.
    """
    if not user.email and user.role is not UserRole.AGENT:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _is_service_principal(user: UserContext) -> bool:
    """The internal-token caller, which holds `*` and bypasses org gates.

    The app-builder agent reaches these routes with the internal token on the
    member's behalf; the member's own access was already checked when they
    invoked it.
    """
    return user.role is UserRole.AGENT and user.has_permission("*")


async def require_app_viewer(
    user: UserContext = Depends(require_app_user),
) -> UserContext:
    """Org-level gate for USING apps (`apps:use:*`).

    Distinct from the per-app grant/visibility checks, which decide *which*
    apps a viewer may open. This is the org-level off switch: a member denied
    `apps:use:*` gets no custom apps at all, regardless of how any individual
    app is shared. Without this the permission would appear in the admin UI
    and do nothing, which is worse than not offering it.
    """
    if _is_service_principal(user):
        return user
    if not user.has_permission("apps:use:*"):
        raise HTTPException(
            status_code=403, detail="Forbidden: missing permission 'apps:use:*'."
        )
    return user


async def require_app_author(
    user: UserContext = Depends(require_app_user),
) -> UserContext:
    """Gate for AUTHORING apps (`feature:build.apps`) — the "app creator".

    A separate dependency rather than a router-level gate because `/apps`
    serves two audiences on one prefix: running a shared app is not the same
    permission as building one. This is the check behind "don't give them the
    app creator, but they can still use the apps the team shares".

    Authoring implies use, so this does not additionally require
    `apps:use:*` — an author locked out of their own app would be nonsense.
    """
    if _is_service_principal(user):
        return user
    if not user.has_permission("feature:build.apps"):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: missing permission 'feature:build.apps'.",
        )
    return user


def _uid(user: UserContext | None) -> str:
    return getattr(user, "email", None) or "system:internal"


# ── Workspace root ───────────────────────────────────────────────────────────

def apps_root() -> Path:
    """The Custom Apps workspace root (one subfolder per app slug), created
    on demand. ``CUSTOM_APPS_ROOT`` overrides; empty falls back to
    ``{agents_clone_dir}/custom_apps`` (the reboot-safe clone home)."""
    settings = get_settings()
    configured = (getattr(settings, "custom_apps_root", "") or "").strip()
    root = (
        Path(configured) if configured
        else Path(settings.agents_clone_dir) / "custom_apps"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def t2_vendor_dir() -> Path:
    """The shared, deploy-provisioned T2 (React) build dependency cache —
    ``react``/``react-dom``/``esbuild``, installed once outside any app
    workspace (never per-app ``npm install``). ``CUSTOM_APPS_T2_VENDOR_DIR``
    overrides; empty falls back to ``{agents_clone_dir}/vendor/t2-react``.
    Does not create the directory — deploy provisioning owns that."""
    settings = get_settings()
    configured = (getattr(settings, "custom_apps_t2_vendor_dir", "") or "").strip()
    return (
        Path(configured) if configured
        else Path(settings.agents_clone_dir) / "vendor" / "t2-react"
    )


# ── Slugs ────────────────────────────────────────────────────────────────────

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,46})[a-z0-9]$")


def is_valid_slug(slug: str) -> bool:
    """Lowercase ``[a-z0-9-]``, 3-48 chars, no leading/trailing dash."""
    return bool(SLUG_RE.fullmatch(slug or ""))


def generate_slug(name: str, taken: Collection[str] = ()) -> str:
    """Derive a valid, unused slug from a display name.

    Non-alphanumerics collapse to single dashes; too-short results are padded
    with ``-app``; collisions get a ``-2``/``-3``… suffix (trimming the base so
    the result stays ≤ 48 chars).
    """
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:48].strip("-")
    if len(base) < 3:
        base = f"{base}-app".strip("-") if base else "app"
    taken_set = set(taken)
    if base not in taken_set:
        return base
    for i in range(2, 10_000):
        suffix = f"-{i}"
        candidate = f"{base[:48 - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in taken_set:
            return candidate
    raise HTTPException(status_code=409, detail="Could not allocate a slug")


# ── Path containment (mirrors workspace.py's _safe_resolve + blocked paths) ──

def resolve_app_file(app_workspace: Path, rel: str) -> Path:
    """Resolve *rel* inside *app_workspace* or raise 400.

    Refuses absolute paths, ``..`` traversal, and any dot-segment (which blocks
    ``.git`` and dotfiles wholesale). The final ``resolve()`` follows symlinks,
    so a link that points outside the workspace fails the containment check
    even though its literal path looks safe.
    """
    clean = (rel or "").replace("\\", "/").strip()
    if not clean or clean.startswith("/") or Path(clean).is_absolute():
        raise HTTPException(status_code=400, detail="Invalid path")
    segments = [s for s in clean.split("/") if s]
    if not segments or any(s == ".." or s.startswith(".") for s in segments):
        raise HTTPException(status_code=400, detail="Invalid path")
    root = app_workspace.resolve()
    resolved = (root / "/".join(segments)).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Path escapes workspace")
    if resolved == root:
        raise HTTPException(status_code=400, detail="Invalid path")
    return resolved


# ── Visibility predicates (pure — the tests exercise the full matrix) ────────

def _field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def can_edit(
    app_row: Any,
    user: UserContext | None,
    grants: Sequence[tuple[str, str]] = (),
) -> bool:
    """Owner, or an email grant with role edit/own. AGENTs never edit (Phase 0)."""
    if user is None:
        return False
    email = getattr(user, "email", None)
    if not email or getattr(user, "role", None) is UserRole.AGENT:
        return False
    if _field(app_row, "owner_email") == email:
        return True
    return any(
        subject == email and role in ("edit", "own")
        for subject, role in grants
    )


def can_view(
    app_row: Any,
    user: UserContext | None,
    grants: Sequence[tuple[str, str]] = (),
) -> bool:
    """Editors; anyone when the app is org-visible AND live; email grantees.

    AGENT principals see org+live apps, or apps granted to ``agents:*`` /
    ``agent:<their id>`` (Phase 0: the wildcard is the practical path).
    """
    if user is None:
        return False
    if can_edit(app_row, user, grants):
        return True
    email = getattr(user, "email", None)
    org_live = (
        _field(app_row, "visibility") == "org"
        and _field(app_row, "status") == "live"
    )
    if getattr(user, "role", None) is UserRole.AGENT:
        return org_live or any(
            subject in ("agents:*", f"agent:{email}") for subject, _ in grants
        )
    if org_live:
        return True
    return bool(email) and any(subject == email for subject, _ in grants)


def role_for(
    app_row: Any,
    user: UserContext | None,
    grants: Sequence[tuple[str, str]] = (),
) -> str:
    """The caller's relationship to a (visible) app: own / edit / use."""
    email = getattr(user, "email", None) if user else None
    if email and _field(app_row, "owner_email") == email:
        return "own"
    if email:
        for subject, role in grants:
            if subject == email and role in ("edit", "own"):
                return role
    return "use"


# ── Manifest helpers ─────────────────────────────────────────────────────────

def default_manifest(
    slug: str, name: str, icon: str = "", description: str = "",
) -> dict[str, Any]:
    """A fresh scaffold's ``app.json`` (RFC §4.1, runtime T1: static).

    ``tier`` is display/prompt metadata only (T1 vanilla vs T2 React) — every
    backend read path (publish, draft preview, durability) already resolves
    the real entry point dynamically from ``entry``, not from ``tier``.
    """
    return {
        "slug": slug,
        "name": name,
        "icon": icon,
        "description": description,
        "runtime": "static",
        "entry": "index.html",
        "tier": "T1",
        "run_as": "viewer",
        "scopes": list(DEFAULT_SCOPES),
        "storage": {"tables": []},
    }


def read_workspace_manifest(workspace: Path) -> dict[str, Any] | None:
    """Parse ``{workspace}/app.json``; None when missing/unparseable."""
    try:
        raw = (workspace / "app.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def parse_db_manifest(val: Any) -> dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            data = json.loads(val)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


def manifest_entry_rel(manifest: dict[str, Any] | None) -> str:
    """Normalized rel path of the manifest's declared entry (default
    ``index.html``) — the one path durability must mirror even when it falls
    under a ``WORKSPACE_SKIP_DIRS`` directory (e.g. a T2 app's
    ``dist/bundle.html``)."""
    raw = str((manifest or {}).get("entry") or "index.html")
    return raw.replace("\\", "/").strip("/")


def manifest_scopes(manifest: dict[str, Any] | None) -> list[str]:
    scopes = (manifest or {}).get("scopes")
    if not isinstance(scopes, list):
        return []
    return [str(s) for s in scopes if isinstance(s, str) and s.strip()]


def scope_set_hash(scopes: Sequence[str]) -> str:
    """sha256 of the sorted scope set — the re-consent key (RFC §4.8)."""
    return hashlib.sha256("\n".join(sorted(scopes)).encode("utf-8")).hexdigest()


# ── AI tier + budget math (pure) ─────────────────────────────────────────────

def manifest_ai_tier(manifest: dict[str, Any] | None) -> str:
    """The tier alias the manifest's ``ai:*`` scope grants (default cheapest)."""
    for scope in manifest_scopes(manifest):
        if scope.startswith("ai:"):
            return _SCOPE_TIER_MAP.get(scope[3:].strip(), DEFAULT_AI_TIER)
    return DEFAULT_AI_TIER


def resolve_ai_tier(requested: str | None, manifest: dict[str, Any] | None) -> str:
    """A request's tier if it names a known alias, else the manifest's."""
    req = (requested or "").strip()
    if req in AI_TIER_ALIASES:
        return req
    if req in _SCOPE_TIER_MAP:
        return _SCOPE_TIER_MAP[req]
    return manifest_ai_tier(manifest)


def resolve_ai_budget(manifest: dict[str, Any] | None) -> int:
    """Monthly token budget: manifest ``ai_budget_tokens`` clamped to the
    platform ceiling; default when missing/invalid; 0 disables AI."""
    raw = (manifest or {}).get("ai_budget_tokens", DEFAULT_AI_BUDGET_TOKENS)
    try:
        budget = int(raw)
    except (TypeError, ValueError):
        budget = DEFAULT_AI_BUDGET_TOKENS
    return max(0, min(budget, MAX_AI_BUDGET_TOKENS))


def check_bundle_size(data: bytes, cap: int = MAX_BUNDLE_BYTES) -> bytes:
    """Pass *data* through, or raise 413 when it exceeds *cap*."""
    if len(data) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"Bundle too large ({len(data)} bytes). Maximum is {cap}.",
        )
    return data


# ── Row loading + gating (shared by every feature module) ────────────────────

async def load_app(db: Any, slug: str) -> Any:
    """The App Workshop app *slug*, or None. A Studio package (format
    'manifest', WS-STU A3) answers None, so each slug route gives 404 for
    it. NULL reads as code. Fence: test_studio_packages_r8.py."""
    return (await db.execute(
        text("SELECT * FROM apps WHERE slug = :slug "
             "AND format IS DISTINCT FROM 'manifest'"),
        {"slug": slug},
    )).fetchone()


async def load_grants(db: Any, app_id: str) -> list[tuple[str, str]]:
    rows = (await db.execute(
        text("SELECT subject, role FROM app_grants WHERE app_id = :app_id"),
        {"app_id": app_id},
    )).fetchall()
    return [(r.subject, r.role) for r in rows]


async def get_app_or_404(
    db: Any, slug: str, user: UserContext, *, edit: bool = False,
) -> tuple[Any, list[tuple[str, str]]]:
    """Load an app + its grants, enforcing view (404) or edit (403) access.

    Non-viewable apps 404 (no existence leak); viewable-but-not-editable hits
    on an edit-gated endpoint 403.
    """
    row = await load_app(db, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="App not found")
    grants = await load_grants(db, str(row.id))
    if not can_view(row, user, grants):
        raise HTTPException(status_code=404, detail="App not found")
    if edit and not can_edit(row, user, grants):
        raise HTTPException(status_code=403, detail="Edit access required")
    return row, grants


async def load_live_version_scopes(
    db: Any, app_id: str, version: int,
) -> tuple[list[str], str] | None:
    """The LIVE (published) version's declared scopes + ``scope_set_hash``,
    read from ``app_versions`` — never the draft workspace's ``app.json``.

    Shared by ``lifecycle.get_app`` (computing ``needs_consent``, RFC §4.8)
    and ``grants``'s consent endpoint (re-verifying the client's claimed
    hash server-side): a viewer must consent to exactly what was published
    and reviewed, never to whatever the builder has since drafted. ``None``
    when the version row is missing.
    """
    vrow = (await db.execute(
        text(
            "SELECT manifest, scope_set_hash FROM app_versions "
            "WHERE app_id = :app_id AND version = :version"
        ),
        {"app_id": app_id, "version": version},
    )).fetchone()
    if vrow is None:
        return None
    return manifest_scopes(parse_db_manifest(vrow.manifest)), (vrow.scope_set_hash or "")


def workspace_dir(value: object) -> Path | None:
    """The folder that an ``apps.workspace_path`` names, or None when it is
    empty or blank.

    ⚠️ ``Path('')`` is ``.``, so it resolves to the working directory of the
    process. An empty path must never mean that folder. A Studio package
    (WS-STU A3) stores ``workspace_path = ''``. Each reader of the column
    goes through here. Fence: ``tests/unit/test_studio_packages_store.py``.
    """
    raw = "" if value is None else str(value)
    return Path(raw) if raw.strip() else None


def app_workspace_or_none(row: Any) -> Path | None:
    """The workspace folder of an app for a READ, or None when it has none.

    A read path (the detail of an app, the effective manifest of a run)
    falls back to the stored manifest on None, so a row with an empty
    ``workspace_path`` still reads, and never from the working directory."""
    return workspace_dir(_field(row, "workspace_path"))


def app_workspace(row: Any) -> Path:
    """The workspace folder of an App Workshop app, for a WRITE or a file
    operation: publish, the file routes, sync, delete and rehydrate.

    An app with no folder (an empty or blank ``workspace_path``) answers 409,
    never the working directory. A writer calls this BEFORE its statement
    commits, so the 409 comes before any write. A read path calls
    :func:`app_workspace_or_none` instead. See :func:`workspace_dir`."""
    workspace = app_workspace_or_none(row)
    if workspace is None:
        raise HTTPException(status_code=409, detail="This app has no workspace.")
    return workspace


# ── Audit + activity (best-effort — never raise into the request path) ───────

#: The one statement that writes an ``app_audit`` row. Both writers below run it.
_APP_AUDIT_SQL = (
    """INSERT INTO app_audit
       (app_id, app_version, user_email, kind, detail,
        tokens_in, tokens_out, cost_usd, model)
       VALUES (:app_id, :app_version, :user_email, :kind,
               :detail ::jsonb, :tokens_in, :tokens_out,
               :cost_usd, :model)"""
)


def _app_audit_params(
    *, app_id: str, user_email: str, kind: str, detail: dict[str, Any] | None,
    app_version: int | None, tokens_in: int = 0, tokens_out: int = 0,
    cost_usd: float = 0.0, model: str = "",
) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "app_version": app_version,
        "user_email": user_email,
        "kind": kind,
        "detail": json.dumps(detail or {}, default=str),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "model": model,
    }


async def record_app_audit_bound(
    *,
    app_id: str,
    user_email: str,
    kind: str,
    detail: dict[str, Any] | None = None,
    app_version: int | None = None,
) -> None:
    """Append one ``app_audit`` row in the tenant that the request bound.

    The twin of :func:`record_app_audit` for a caller in a request, such as
    the Studio action door (STU-B2a). ``app_audit`` has FORCE RLS, so a row
    needs a bound session. The unbound session of :func:`record_app_audit`
    writes no row as a role without BYPASSRLS. Best-effort: it opens its own
    ``tenant_session()``, and a failure logs and never raises. Call it after
    the caller's own session closes, so a request holds one session at a time.
    """
    try:
        async with _tenant_session() as db:
            await db.execute(text(_APP_AUDIT_SQL), _app_audit_params(
                app_id=app_id, user_email=user_email, kind=kind, detail=detail,
                app_version=app_version))
    except Exception as exc:
        _log.warning("apps.audit_failed", kind=kind, error=str(exc)[:160])


async def record_app_audit(
    *,
    app_id: str,
    user_email: str,
    kind: str,
    detail: dict[str, Any] | None = None,
    app_version: int | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    model: str = "",
) -> None:
    """Append one ``app_audit`` row. Best-effort: opens its own session so a
    failed audit can neither raise nor poison the caller's transaction.

    ⚠️ H4, DELIBERATELY NOT H2 (`saas_multitenancy_handover.md`): this helper
    is reached from OUTSIDE any request — `actions.execute_app_action` audits
    every dispatch, and the orchestrator's agent tools call that function
    in-process during an agent run with no request and no bound tenant
    (`orchestrator/app_tools.py`). Converting this site to the ambient
    `tenant_session()` would raise `TenantUnbound` there, and this except-all
    wrapper would then silently DROP the audit row — a security trail. It
    stays on the unbound `get_db()` until H4 threads the explicit tenant (the
    app row's organization, resolvable from ``app_id``) through the call —
    `tenant_session(org_id)`. The H2 ratchet in `test_db_engine_seam.py`
    carries this site as a named, counted exemption.
    """
    try:
        db = await _get_db()
        try:
            await db.execute(
                text(_APP_AUDIT_SQL),
                _app_audit_params(
                    app_id=app_id, user_email=user_email, kind=kind, detail=detail,
                    app_version=app_version, tokens_in=tokens_in,
                    tokens_out=tokens_out, cost_usd=cost_usd, model=model),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception as exc:
        _log.warning("apps.audit_failed", kind=kind, error=str(exc)[:160])


def publish_app_activity(
    slug: str, *, user: str | None = None, **fields: Any,
) -> None:
    """Publish an app activation to the global activity bus (best-effort).

    Apps are a distinct actor class on the feed: ``kind="app"`` with
    ``agent="app:<slug>"`` so the observability view shows app activity
    alongside agent runs (RFC §4.6).
    """
    try:
        from acb_common import publish_activity
        publish_activity(
            kind="app", agent=f"app:{slug}", user=user, source="apps",
            **fields,
        )
    except Exception:
        pass


def iso(val: Any) -> str | None:
    return val.isoformat() if val is not None else None
