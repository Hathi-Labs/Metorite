"""BO-2 residuals #3 and #4 — service identity vs. LLM key, and webhook signing.

Two holes that let a caller act as the platform:

* **The LLM key was the identity token.** ``_get_internal_token`` fell back to
  ``LITELLM_MASTER_KEY``, and the orchestrator handed agents whichever value it
  resolved to. Every agent therefore held a credential that grants
  ``SERVICE_ACCESS`` — it could call ``/admin/*`` and grant itself anything,
  defeating the entire org access model. WS-24b (2026-11-06) removed the
  fallback. An unset token now fails closed outside ``ACB_ENV=dev``.

* **``/agent/webhook/{source}`` was unauthenticated.** It dispatches an agent
  run, so it was a fourth run path bypassing ``assert_can_run_agent``.
"""
from __future__ import annotations

import hashlib
import hmac
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acb_auth import require_internal_auth, require_llm_api_auth
from acb_auth.deps import _get_internal_token, _get_llm_api_token

IDENTITY = "sk-identity-secret"
LLM_KEY = "sk-llm-api-key"


@pytest.fixture
def split_tokens(monkeypatch):
    """A correctly provisioned deployment: two distinct secrets."""
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", IDENTITY)
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    return IDENTITY, LLM_KEY


# ── Token resolution ────────────────────────────────────────────────────────

def test_the_two_tokens_are_distinct_when_provisioned(split_tokens) -> None:
    assert _get_internal_token() == IDENTITY
    assert _get_llm_api_token() == LLM_KEY
    assert _get_internal_token() != _get_llm_api_token()


def test_identity_never_falls_back_to_the_llm_key(unprovisioned) -> None:
    """WS-24b (2026-11-06): an unset token is unset. It is never the LLM key.

    This test used to pin the fallback (``..._falls_back_to_the_llm_key_when_
    unprovisioned``). The fallback made the key that every agent holds a
    platform credential, so the pin now runs the other way.
    """
    assert _get_llm_api_token() == LLM_KEY
    assert _get_internal_token() == ""


def test_no_secrets_at_all_gives_no_identity_token(unprovisioned, monkeypatch) -> None:
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    assert _get_internal_token() == ""


# ── An unprovisioned box fails closed ───────────────────────────────────────
#
# With no GATEWAY_INTERNAL_TOKEN, no Bearer is service identity, and nothing
# else stands in for one. Before WS-24b the LLM-key fallback kept the token
# non-empty, so these checks were closed on every real box. Removing the
# fallback must not open them.

VICTIM = "someone-else@fracktal.in"


@pytest.fixture
def unprovisioned(monkeypatch):
    """A production box that forgot GATEWAY_INTERNAL_TOKEN, LLM key set."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.delenv("GATEWAY_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(settings, "gateway_internal_token", "", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    monkeypatch.setattr(settings, "acb_env", "prod", raising=False)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)

    async def _passthrough(user):
        return user

    monkeypatch.setattr("acb_auth.deps._with_resolved_access", _passthrough)
    return settings


async def test_the_llm_key_cannot_assert_an_identity(unprovisioned) -> None:
    from acb_auth.deps import get_current_user

    user = await get_current_user(
        x_user_email=VICTIM, x_user_role=None,
        authorization=f"Bearer {LLM_KEY}",
    )
    assert user.email is None
    assert not user.has_permission("*")


async def test_the_llm_key_is_not_service_access(unprovisioned) -> None:
    """A bare Bearer that matched would be SERVICE_ACCESS (branch 1b)."""
    from acb_auth.deps import get_current_user

    user = await get_current_user(
        x_user_email=None, x_user_role=None,
        authorization=f"Bearer {LLM_KEY}",
    )
    assert user.email != "system:internal"
    assert not user.has_permission("*")


async def test_a_bare_identity_header_is_refused(unprovisioned) -> None:
    """No token to check means no proof, so the header is not trusted."""
    from acb_auth.deps import get_current_user

    user = await get_current_user(
        x_user_email=VICTIM, x_user_role=None, authorization=None,
    )
    assert user.email is None
    assert not user.has_permission("*")


def test_identity_routes_answer_401(unprovisioned) -> None:
    client = _client()
    assert client.get("/identity").status_code == 401
    assert client.get(
        "/identity", headers={"Authorization": f"Bearer {LLM_KEY}"}
    ).status_code == 401
    assert client.get(
        "/identity", headers={"Authorization": "Bearer "}
    ).status_code == 401


def test_v1_still_accepts_the_llm_key(unprovisioned) -> None:
    """The /v1 key keeps its one job, so agents can still reach their model."""
    res = _client().get("/v1/models", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert res.status_code == 200


async def test_a_dev_laptop_stays_open_but_the_llm_key_is_still_no_identity(
    unprovisioned, monkeypatch
) -> None:
    """ACB_ENV=dev keeps the old open behaviour of an unset token. The LLM key
    is not service identity there either."""
    from acb_auth.deps import get_current_user

    monkeypatch.setattr(unprovisioned, "acb_env", "dev", raising=False)
    assert _client().get("/identity").status_code == 200
    header_only = await get_current_user(
        x_user_email="dev@fracktal.in", x_user_role=None, authorization=None,
    )
    assert header_only.email == "dev@fracktal.in"
    bare_key = await get_current_user(
        x_user_email=None, x_user_role=None, authorization=f"Bearer {LLM_KEY}",
    )
    assert bare_key.email != "system:internal"
    assert not bare_key.has_permission("*")


async def test_a_provisioned_token_is_identity_and_the_llm_key_is_not(
    split_tokens, monkeypatch
) -> None:
    from acb_auth.deps import get_current_user

    async def _passthrough(user):
        return user

    monkeypatch.setattr("acb_auth.deps._with_resolved_access", _passthrough)

    proxied = await get_current_user(
        x_user_email=VICTIM, x_user_role=None,
        authorization=f"Bearer {IDENTITY}",
    )
    assert proxied.email == VICTIM

    agent = await get_current_user(
        x_user_email=VICTIM, x_user_role=None,
        authorization=f"Bearer {LLM_KEY}",
    )
    assert agent.email is None


# ── No caller sends the LLM key as service identity ─────────────────────────

#: An expression that reads the identity token and then falls back to the LLM
#: key: ``gateway_internal_token ... or ... litellm_master_key`` in Python, or
#: ``GATEWAY_INTERNAL_TOKEN ?? process.env.LITELLM_MASTER_KEY`` in TypeScript.
_FALLBACK = re.compile(
    r"(?:gateway_internal_token|GATEWAY_INTERNAL_TOKEN)[^\n]{0,80}"
    r"(?:\n[^\n]{0,80}){0,2}?(?:\bor\b|\?\?)\s*"
    r"(?:getattr\([^)]*?|os\.environ\.get\(|os\.getenv\(|process\.env\.)\s*"
    r"[\"']?(?:litellm_master_key|LITELLM_MASTER_KEY)"
)

#: The nested-default shape of the same fallback.
_NESTED = re.compile(
    r"GATEWAY_INTERNAL_TOKEN[\"'],\s*os\.environ\.get\(\s*[\"']LITELLM_MASTER_KEY"
)


def test_the_fallback_pattern_matches_the_old_shapes() -> None:
    """The fence must see the shapes that WS-24b removed, or it fences nothing."""
    old_py = (
        'return (\n    getattr(settings, "gateway_internal_token", "")\n'
        '    or getattr(settings, "litellm_master_key", "")\n    or "sk-local"\n)'
    )
    old_env = 'os.environ.get("GATEWAY_INTERNAL_TOKEN", os.environ.get("LITELLM_MASTER_KEY", ""))'
    old_ts = "process.env.GATEWAY_INTERNAL_TOKEN ??\n  process.env.LITELLM_MASTER_KEY ??"
    old_or = 'os.environ.get("GATEWAY_INTERNAL_TOKEN")\n    or os.environ.get("LITELLM_MASTER_KEY")'
    for shape in (old_py, old_ts, old_or):
        assert _FALLBACK.search(shape), shape
    assert _NESTED.search(old_env)
    # The /v1 key read alone, and the two names side by side, are legal.
    assert not _FALLBACK.search('key = getattr(settings, "litellm_master_key", "")')
    assert not _FALLBACK.search(
        'os.getenv("GATEWAY_INTERNAL_TOKEN", ""),\n        os.getenv("LITELLM_MASTER_KEY", ""),'
    )


def test_no_caller_sends_the_llm_key_as_service_identity() -> None:
    """WS-24b: every internal caller sends ``gateway_internal_token`` only.

    The gateway no longer accepts the LLM key as identity, so a caller that
    falls back to it sends a token that gets 401. This scan keeps a new caller
    from bringing the fallback back.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    trees = [
        "apps", "packages", "scripts", "evals", "tests/integration",
        "workbench/control_plane/src", "workbench/operator_console/src",
    ]
    found: list[str] = []
    for tree in trees:
        base = root / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _FALLBACK.search(text) or _NESTED.search(text):
                found.append(str(path.relative_to(root)))
    assert not found, (
        "These files fall back to LITELLM_MASTER_KEY for the internal bearer. "
        f"Send gateway_internal_token only: {found}"
    )


# ── The guards ──────────────────────────────────────────────────────────────

def _client() -> TestClient:
    from fastapi import Depends

    app = FastAPI()

    @app.get("/identity", dependencies=[Depends(require_internal_auth)])
    async def _identity() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/models", dependencies=[Depends(require_llm_api_auth)])
    async def _v1() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_llm_key_does_not_open_identity_routes(split_tokens) -> None:
    """The headline fix: an agent's key is not a platform credential."""
    res = _client().get("/identity", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert res.status_code == 401


def test_identity_token_opens_identity_routes(split_tokens) -> None:
    res = _client().get("/identity", headers={"Authorization": f"Bearer {IDENTITY}"})
    assert res.status_code == 200


def test_v1_accepts_the_llm_key(split_tokens) -> None:
    """Agents route completions with it, so /v1 must keep working."""
    res = _client().get("/v1/models", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert res.status_code == 200


def test_v1_also_accepts_the_identity_token(split_tokens) -> None:
    """The Next.js server and internal jobs route completions too."""
    res = _client().get("/v1/models", headers={"Authorization": f"Bearer {IDENTITY}"})
    assert res.status_code == 200


def test_v1_rejects_an_unknown_token(split_tokens) -> None:
    res = _client().get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_both_routes_reject_a_missing_header(split_tokens) -> None:
    client = _client()
    assert client.get("/identity").status_code == 401
    assert client.get("/v1/models").status_code == 401


# ── The BYOK clients must not carry identity ────────────────────────────────

def test_llm_api_key_accessor_never_returns_the_identity_token(
    split_tokens, monkeypatch
) -> None:
    """`settings.llm_api_key` is what every /v1 client is pointed at."""
    from acb_common import Settings

    settings = Settings(
        litellm_master_key=LLM_KEY, gateway_internal_token=IDENTITY
    )
    assert settings.llm_api_key == LLM_KEY
    assert settings.llm_api_key != settings.gateway_internal_token


def test_no_v1_client_reads_the_identity_token() -> None:
    """Guards against a regression re-introducing the escalation.

    Every module that builds a `/v1` provider config must resolve its key from
    `llm_api_key`. Modules calling gateway *business* APIs still legitimately
    use `gateway_internal_token` and are excluded by name.
    """
    import pathlib

    v1_client_modules = [
        "apps/services/orchestrator/orchestrator/agents.py",
        "apps/services/orchestrator/orchestrator/_model_resolution.py",
        "apps/services/orchestrator/orchestrator/code_session.py",
        "apps/services/orchestrator/orchestrator/mutation.py",
    ]
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in v1_client_modules:
        source = (root / rel).read_text(encoding="utf-8")
        offending = [
            line.strip()
            for line in source.split("\n")
            if "gateway_internal_token" in line and not line.strip().startswith("#")
        ]
        assert not offending, (
            f"{rel} resolves gateway_internal_token for a /v1 client: {offending}. "
            f"Use settings.llm_api_key — the identity token must not reach "
            f"model-authored code."
        )


# ── Webhook signing ─────────────────────────────────────────────────────────

BODY = b'{"event_type":"taskCreated","payload":{}}'


def _sign(secret: str, body: bytes = BODY) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_accepts_a_valid_signature(monkeypatch) -> None:
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    verify_webhook_signature("clickup", BODY, _sign("wh-secret"))  # must not raise


def test_webhook_accepts_a_bare_hex_signature(monkeypatch) -> None:
    """The `sha256=` prefix is conventional, not required."""
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    bare = _sign("wh-secret").removeprefix("sha256=")
    verify_webhook_signature("clickup", BODY, bare)


def test_webhook_rejects_a_wrong_signature(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", BODY, _sign("wrong-secret"))
    assert exc.value.status_code == 401


def test_webhook_rejects_a_tampered_body(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    signature = _sign("wh-secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", b'{"event_type":"evil"}', signature)
    assert exc.value.status_code == 401


def test_webhook_rejects_a_missing_signature(monkeypatch) -> None:
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "wh-secret")
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", BODY, None)
    assert exc.value.status_code == 401


def test_webhook_fails_closed_when_unconfigured(monkeypatch) -> None:
    """No secret means the endpoint is shut, not open.

    This endpoint starts an agent run and is internet-reachable, so an
    implicit allow would leave the hole this change exists to close.
    """
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.delenv("AGENT_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("AGENT_WEBHOOK_SECRET_CLICKUP", raising=False)
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature("clickup", BODY, _sign("anything"))
    assert exc.value.status_code == 503
    assert "AGENT_WEBHOOK_SECRET" in exc.value.detail


def test_per_source_secret_overrides_the_global(monkeypatch) -> None:
    """One leaked integration must not authorise every agent on the platform."""
    from fastapi import HTTPException
    from gateway.routes.agent import verify_webhook_signature

    monkeypatch.setenv("AGENT_WEBHOOK_SECRET", "global-secret")
    monkeypatch.setenv("AGENT_WEBHOOK_SECRET_CLICKUP", "clickup-secret")

    verify_webhook_signature("clickup", BODY, _sign("clickup-secret"))
    # The global secret no longer works for this source...
    with pytest.raises(HTTPException):
        verify_webhook_signature("clickup", BODY, _sign("global-secret"))
    # ...but still does for a source with no override.
    verify_webhook_signature("zoho", BODY, _sign("global-secret"))


def test_source_name_is_normalised_for_the_env_lookup(monkeypatch) -> None:
    """`zoho-crm` → AGENT_WEBHOOK_SECRET_ZOHO_CRM (dashes are not env-safe)."""
    monkeypatch.setenv("AGENT_WEBHOOK_SECRET_ZOHO_CRM", "zoho-secret")
    from gateway.routes.agent import verify_webhook_signature

    verify_webhook_signature("zoho-crm", BODY, _sign("zoho-secret"))
