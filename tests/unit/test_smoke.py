"""Smoke tests — prove the workspace imports cleanly and basic plumbing works."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_settings_load() -> None:
    from acb_common import get_settings

    s = get_settings()
    assert s.acb_env in {"dev", "staging", "prod"}
    assert s.database_url.startswith("postgresql")


def test_acb_env_defaults_to_prod() -> None:
    """An ABSENT ACB_ENV must fail closed. R7 fence for H-90 / H-94.

    `docs_enabled()` returns `env == "dev"`, and FastAPI mounts
    Swagger/ReDoc as plain Starlette routes with no dependency chain, so
    the app-level auth dependency never reaches them. This default is the
    only guard they have.

    It read "dev" until 2026-09-01. Nothing in the deploy set the
    variable, so production ran on the default and served
    `/openapi.json` (1121924 bytes) and `/docs` to anyone.

    ⚠️ Reads the FIELD DEFAULT, not `get_settings()`. Instantiating
    Settings would pick up a real `.env` or a real environment variable,
    and then this test would pass or fail on whatever the developer
    happens to have exported. The default is the thing under test.
    """
    from acb_common.settings import Settings

    assert Settings.model_fields["acb_env"].default == "prod"


def test_docs_disabled_when_env_absent() -> None:
    """The consequence of the default, stated at the surface that leaked."""
    from gateway.main import docs_enabled

    assert docs_enabled("prod") is False
    assert docs_enabled("staging") is False
    assert docs_enabled("dev") is True


def test_router_tiers() -> None:
    from acb_llm import LLMTier
    from orchestrator.router import pick_tier

    assert pick_tier("what is the status of project X?") == LLMTier.TIER_2
    assert pick_tier("why did we lose deal Y?") == LLMTier.TIER_3
    assert pick_tier("ping") == LLMTier.TIER_1


def test_gateway_health() -> None:
    from gateway.main import app

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Customer Foo last met on Mon [person:11111111-1111-1111-1111-111111111111].", True),
        ("Customer Foo last met on Mon.", False),
    ],
)
def test_citation_guardrail(text: str, ok: bool) -> None:
    from acb_llm.guardrails import CitationError, require_citations

    if ok:
        assert require_citations(text) == text
    else:
        with pytest.raises(CitationError):
            require_citations(text)
