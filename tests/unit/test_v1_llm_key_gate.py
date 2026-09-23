"""The model door accepts the LLM key, and nothing weaker.

Found by the Projects chat's live trial (2026-09-23): every in-process MAF
agent got 401 from `/v1`. BO-2 residual #4 hands agent code ONLY the LLM API
key (`Settings.llm_api_key`), and `/v1` accepts it through its own
`require_llm_api_auth`. The app-wide default-deny gate ran FIRST and refused
that key, because it is not an identity. So the completion routes are in
`PUBLIC_ROUTES`, and their own check is their lock.

These tests run the REAL dependencies (`require_authenticated` with the real
`PUBLIC_ROUTES`, and `require_llm_api_auth`), with the keys set in the
environment, so a change to either side fails here.
"""

from __future__ import annotations

import pytest
from acb_auth import require_authenticated
from acb_auth.deps import require_llm_api_auth
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

LLM_KEY = "llm-key-for-test"
SERVICE = "service-token-for-test"


def _main():
    import importlib

    return importlib.import_module("gateway.main")


def _app() -> TestClient:
    app = FastAPI(dependencies=[require_authenticated(public=_main().PUBLIC_ROUTES)])

    @app.post("/v1/chat/completions", dependencies=[Depends(require_llm_api_auth)])
    async def _completions() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/chat/completions", dependencies=[Depends(require_llm_api_auth)])
    async def _root_completions() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/projects/tree")
    async def _business() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", LLM_KEY)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", SERVICE)


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/chat/completions"])
def test_the_llm_key_reaches_the_model_door(path: str, keys) -> None:
    """The regression: this was 401 on every box with a service token set."""
    r = _app().post(path, headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/chat/completions"])
def test_the_model_door_still_refuses_a_wrong_key_and_no_key(path: str, keys) -> None:
    client = _app()
    assert client.post(path, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post(path).status_code == 401


def test_the_llm_key_is_not_an_identity_anywhere_else(keys) -> None:
    """Listing `/v1` must not make the LLM key a pass for business routes."""
    r = _app().get("/projects/tree", headers={"Authorization": f"Bearer {LLM_KEY}"})
    assert r.status_code == 401


def test_an_unconfigured_box_outside_dev_refuses_the_model_door(monkeypatch) -> None:
    """With no key at all, `/v1`'s own check is its only lock, so it closes."""
    from acb_common import get_settings

    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_INTERNAL_TOKEN", raising=False)
    settings = get_settings()
    monkeypatch.setattr(settings, "litellm_master_key", "", raising=False)
    monkeypatch.setattr(settings, "gateway_internal_token", "", raising=False)
    monkeypatch.setattr(settings, "acb_env", "prod", raising=False)
    assert _app().post("/v1/chat/completions").status_code == 401
    monkeypatch.setattr(settings, "acb_env", "dev", raising=False)
    assert _app().post("/v1/chat/completions").status_code == 200


def test_only_the_completion_routes_are_listed() -> None:
    """Only the chat completion door. Embeddings and the rest stay gated."""
    listed = {p for p in _main().PUBLIC_ROUTES if "completions" in p or p.startswith("/v1")}
    assert listed == {"/v1/chat/completions", "/chat/completions"}
