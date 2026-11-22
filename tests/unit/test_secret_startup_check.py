"""A service with no usable secret says so at start (task 574, WS-2d, BO-8).

Spec: ``docs/secrets_drop.md`` §6. The one check is
``acb_auth.secret_check.check_startup_secrets``. ``SECRET_STARTUP_CHECK``
selects the mode: ``shadow`` (the default) logs each unusable required secret
by NAME and starts. ``enforce`` refuses to start. ``ACB_ENV=dev`` checks
nothing.

The first test is the gap that task 574 names. Before the check, the agent
edge started under ``enforce`` with no signing key and said nothing.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import structlog

ROOT = Path(__file__).resolve().parents[2]

#: Values that are set and not public. None of them may reach a log line.
GOOD = {
    "gateway_session_secret": "good-session-secret-6f1c2a9e0b7d4c3a",
    "gateway_internal_token": "good-internal-token-8a2d5e7f9c1b3a4d",
    "acb_master_key": "good-master-key-3c5e7a9b1d2f4e6a8c0b",
    "litellm_master_key": "good-llm-key-1a3c5e7b9d2f4a6c8e0b",
    "database_url": "postgresql+psycopg://acb:good-db-pass-5e7a9c1b@db:5432/acb",
    "agent_edge_signing_key": "good-edge-signing-key-0123456789abcdef0123",
}


def _settings(**over):
    from acb_common.settings import Settings

    values = {"acb_env": "prod", **GOOD, **over}
    return Settings(_env_file=None, **values)


def _events(logs, name):
    return [e for e in logs if e.get("event") == name]


# ── The gap: before task 574 a service started with no secret, silently ──────


@pytest.fixture
def edge_env(monkeypatch):
    """Run the edge app on a prod box with the access switch on and no key."""
    from acb_common.settings import get_settings

    for name, value in GOOD.items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("ACB_ENV", "prod")
    monkeypatch.setenv("AGENT_ACCESS_ENABLED", "true")
    monkeypatch.setenv("AGENT_EDGE_SIGNING_KEY", "")
    monkeypatch.setenv("SECRET_STARTUP_CHECK", "enforce")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_the_edge_refuses_to_start_under_enforce_with_no_signing_key(edge_env):
    """RED before task 574: the edge started and logged nothing."""
    from agent_edge.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with (structlog.testing.capture_logs() as logs, pytest.raises(Exception) as caught,
          TestClient(app)):
        pass
    assert "agent_edge_signing_key" in str(caught.value)
    assert _events(logs, "secrets.unusable")


def test_the_edge_starts_under_shadow_and_names_the_setting(edge_env):
    from acb_common.settings import get_settings
    from agent_edge.app import create_app
    from fastapi.testclient import TestClient

    edge_env.setenv("SECRET_STARTUP_CHECK", "shadow")
    get_settings.cache_clear()
    with structlog.testing.capture_logs() as logs, TestClient(create_app()):
        pass
    named = [e["setting"] for e in _events(logs, "secrets.unusable")]
    assert named == ["agent_edge_signing_key"]


# ── Shadow logs the names and starts ──────────────────────────────────────────


def test_shadow_logs_exactly_the_bad_names_and_returns():
    from acb_auth.secret_check import check_startup_secrets

    s = _settings(gateway_session_secret="", litellm_master_key="sk-local",
                  secret_startup_check="shadow")
    with structlog.testing.capture_logs() as logs:
        bad = check_startup_secrets("gateway", s)
    assert bad == ["gateway_session_secret", "litellm_master_key"]
    warned = _events(logs, "secrets.unusable")
    assert [e["setting"] for e in warned] == bad
    assert all(e["log_level"] == "warning" for e in warned)


def test_the_default_mode_is_shadow():
    from acb_auth.secret_check import check_startup_secrets
    from acb_common.settings import Settings, secret_startup_check_mode

    assert Settings.model_fields["secret_startup_check"].default == "shadow"
    s = _settings(gateway_internal_token="")
    assert secret_startup_check_mode(s) == "shadow"
    assert check_startup_secrets("gateway", s) == ["gateway_internal_token"]


@pytest.mark.parametrize("raw", ["", "on", "ENFORCED", "1", "true", "off"])
def test_an_unknown_mode_reads_as_shadow(raw):
    from acb_auth.secret_check import check_startup_secrets

    s = _settings(acb_master_key="", secret_startup_check=raw)
    assert check_startup_secrets("gateway", s) == ["acb_master_key"]


def test_a_summary_line_is_always_logged():
    """No warning on a box must mean "all usable", never "never ran"."""
    from acb_auth.secret_check import check_startup_secrets

    with structlog.testing.capture_logs() as logs:
        check_startup_secrets("gateway", _settings())
    summary = _events(logs, "secrets.startup_check")
    assert len(summary) == 1
    assert summary[0]["mode"] == "shadow"
    assert summary[0]["service"] == "gateway"
    assert summary[0]["unusable"] == 0


def test_shadow_never_raises_on_a_bug_of_the_check(monkeypatch):
    from acb_auth import secret_check

    def boom(*_a, **_k):
        raise KeyError("a bug")

    monkeypatch.setattr(secret_check, "unusable_secrets", boom)
    with structlog.testing.capture_logs() as logs:
        assert secret_check.check_startup_secrets("gateway", _settings()) == []
    assert _events(logs, "secrets.startup_check_failed")


# ── Enforce refuses with the names ────────────────────────────────────────────


def test_enforce_refuses_and_names_each_setting():
    from acb_auth.secret_check import SecretsUnusable, check_startup_secrets

    s = _settings(gateway_session_secret="change-me-dev-only",
                  gateway_internal_token="", secret_startup_check="enforce")
    with pytest.raises(SecretsUnusable) as caught:
        check_startup_secrets("gateway", s)
    assert caught.value.names == ("gateway_internal_token", "gateway_session_secret")
    assert "gateway_internal_token" in str(caught.value)
    assert "gateway_session_secret" in str(caught.value)
    assert "SECRET_STARTUP_CHECK" in str(caught.value)


def test_enforce_starts_when_every_secret_is_usable():
    from acb_auth.secret_check import check_startup_secrets

    assert check_startup_secrets("gateway", _settings(secret_startup_check="enforce")) == []


def test_enforce_fails_closed_on_a_bug_of_the_check(monkeypatch):
    from acb_auth import secret_check

    def boom(*_a, **_k):
        raise KeyError("a bug")

    monkeypatch.setattr(secret_check, "unusable_secrets", boom)
    with pytest.raises(secret_check.SecretsUnusable):
        secret_check.check_startup_secrets(
            "gateway", _settings(secret_startup_check="enforce"))


# ── Dev never refuses ─────────────────────────────────────────────────────────


def test_dev_never_refuses_and_logs_nothing():
    from acb_auth.secret_check import check_startup_secrets

    s = _settings(acb_env="dev", gateway_session_secret="", gateway_internal_token="",
                  acb_master_key="", litellm_master_key="sk-local",
                  secret_startup_check="enforce")
    with structlog.testing.capture_logs() as logs:
        assert check_startup_secrets("gateway", s) == []
    assert not _events(logs, "secrets.unusable")


def test_staging_is_checked_like_prod():
    from acb_auth.secret_check import SecretsUnusable, check_startup_secrets

    s = _settings(acb_env="staging", acb_master_key="", secret_startup_check="enforce")
    with pytest.raises(SecretsUnusable):
        check_startup_secrets("gateway", s)


# ── A public default is refused like an empty value ───────────────────────────


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_value_is_unusable(value):
    from acb_auth.secret_check import unusable_secrets

    assert unusable_secrets("gateway", _settings(acb_master_key=value)) == ["acb_master_key"]


def test_each_public_default_is_refused_like_an_empty_value():
    from acb_auth.member_proof import PUBLIC_DEFAULT_SECRETS
    from acb_auth.secret_check import unusable_secrets

    for public in sorted(PUBLIC_DEFAULT_SECRETS):
        for padded in (public, f" {public} "):
            s = _settings(gateway_internal_token=padded)
            assert unusable_secrets("gateway", s) == ["gateway_internal_token"], public


def test_the_settings_defaults_are_unusable():
    """A box that sets no secret at all gets every required name."""
    from acb_auth.secret_check import unusable_secrets
    from acb_common.settings import Settings

    defaults = {
        name: Settings.model_fields[name].default
        for name in ("gateway_session_secret", "gateway_internal_token",
                     "acb_master_key", "litellm_master_key", "database_url")
    }
    s = _settings(**defaults)
    assert unusable_secrets("gateway", s) == sorted(defaults)


def test_a_database_url_with_a_public_password_is_unusable():
    from acb_auth.secret_check import unusable_secrets

    public = "postgresql+psycopg://acb:acb_dev_change_me@localhost:5432/acb"
    assert unusable_secrets("gateway", _settings(database_url=public)) == ["database_url"]
    assert unusable_secrets("gateway", _settings(database_url="")) == ["database_url"]
    # A socket or peer login carries no password, and that is not a default.
    peer = "postgresql+psycopg://acb@/acb?host=/var/run/postgresql"
    assert unusable_secrets("gateway", _settings(database_url=peer)) == []


def test_a_conditional_secret_binds_only_when_its_switch_is_on():
    from acb_auth.secret_check import unusable_secrets

    off = _settings(agent_edge_signing_key="", agent_access_enabled=False)
    assert unusable_secrets("gateway", off) == []
    assert unusable_secrets("agent_edge", off) == []
    on = _settings(agent_edge_signing_key="", agent_access_enabled=True)
    assert unusable_secrets("gateway", on) == ["agent_edge_signing_key"]
    assert unusable_secrets("agent_edge", on) == ["agent_edge_signing_key"]


def test_the_edge_does_not_need_the_gateway_secrets():
    """The edge holds no internal bearer by design (D-EA-2)."""
    from acb_auth.secret_check import unusable_secrets

    s = _settings(gateway_session_secret="", gateway_internal_token="",
                  acb_master_key="", litellm_master_key="")
    assert unusable_secrets("agent_edge", s) == []


def test_an_unknown_service_is_an_error():
    """A typo in the wiring must not check nothing in silence."""
    from acb_auth.secret_check import check_startup_secrets

    with pytest.raises(ValueError):
        check_startup_secrets("gatewya", _settings())


# ── The log and the error never hold a value ──────────────────────────────────


def test_no_value_reaches_the_log_or_the_error():
    from acb_auth.secret_check import SecretsUnusable, check_startup_secrets

    s = _settings(gateway_session_secret="change-me-dev-only",
                  litellm_master_key="sk-local-dev-change-me")
    with structlog.testing.capture_logs() as logs:
        check_startup_secrets("gateway", s)
    with pytest.raises(SecretsUnusable) as caught:
        check_startup_secrets("gateway", _settings(
            gateway_session_secret="change-me-dev-only",
            litellm_master_key="sk-local-dev-change-me",
            secret_startup_check="enforce"))
    text = repr(logs) + str(caught.value) + repr(caught.value.args)
    for value in (*GOOD.values(), "change-me-dev-only", "sk-local-dev-change-me",
                  "good-db-pass-5e7a9c1b"):
        assert value not in text


# ── Fences: the classification cannot go stale ────────────────────────────────

#: A `Settings` field whose name says it may hold a secret.
SECRET_NAME = re.compile(r"(^|_)(key|secret|token|password|passwd|salt|pem)(_|$)")

def _secret_like_fields() -> set[str]:
    from acb_common.settings import Settings

    return {
        name for name, field in Settings.model_fields.items()
        if SECRET_NAME.search(name) and field.annotation is str
    }


def test_every_secret_like_setting_is_required_or_optional():
    """A new secret setting must say if a service needs it (task 574)."""
    from acb_auth.secret_check import OPTIONAL, REQUIRED
    from acb_common.settings import Settings

    required = {need.setting for need in REQUIRED}
    assert not required & set(OPTIONAL), "a setting is both required and optional"
    unsorted = sorted(_secret_like_fields() - required - set(OPTIONAL))
    assert unsorted == [], (
        "add each to REQUIRED or OPTIONAL in acb_auth/secret_check.py, and to "
        f"the table of docs/secrets_drop.md §6: {unsorted}")
    stale = sorted((required | set(OPTIONAL)) - set(Settings.model_fields))
    assert stale == [], f"names that Settings no longer has: {stale}"
    assert all(reason.strip() for reason in OPTIONAL.values())


def test_each_need_names_real_services_and_switches():
    from acb_auth.secret_check import REQUIRED, SERVICES
    from acb_common.settings import Settings

    for need in REQUIRED:
        assert need.services and need.services <= set(SERVICES), need
        if need.when is not None:
            assert Settings.model_fields[need.when].annotation is bool, need


def test_every_shipped_placeholder_is_on_the_public_list():
    """A default in `Settings` or `.env.example` is public, so it proves nothing."""
    from acb_auth.member_proof import PUBLIC_DEFAULT_SECRETS
    from acb_auth.secret_check import NOT_A_SECRET, OPTIONAL, REQUIRED
    from acb_common.settings import Settings

    _NOT_SECRET = {n for n, why in OPTIONAL.items() if why.startswith(NOT_A_SECRET)}
    classified = {need.setting for need in REQUIRED} | set(OPTIONAL)
    missing = []
    for name in sorted(classified):
        default = Settings.model_fields[name].default
        if name == "database_url":
            default = urlsplit(default).password or ""
        if (isinstance(default, str) and default.strip() and name not in _NOT_SECRET
                and default.strip() not in PUBLIC_DEFAULT_SECRETS):
            missing.append(name)
    line = re.compile(r"^([A-Z0-9_]+)=(.*)$")
    for raw in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        m = line.match(raw.strip())
        if not m or not SECRET_NAME.search(m.group(1).lower()):
            continue
        value = m.group(2).split("#", 1)[0].strip()
        if value and m.group(1).lower() not in _NOT_SECRET and value not in PUBLIC_DEFAULT_SECRETS:
            missing.append(m.group(1))
    assert missing == [], f"public placeholders not on PUBLIC_DEFAULT_SECRETS: {missing}"


# ── Fences: the services call the one check ───────────────────────────────────


def _call_args(node: ast.AST) -> list[str]:
    return [a.value for a in getattr(node, "args", []) if isinstance(a, ast.Constant)]


def _is_check(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    return name == "check_startup_secrets"


def test_the_gateway_lifespan_runs_the_check_outside_any_try():
    """A swallow-all `try` around the call would make `enforce` a log line."""
    src = (ROOT / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    life = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    top = [stmt for stmt in life.body
           if isinstance(stmt, ast.Expr) and _is_check(stmt.value)]
    assert len(top) == 1, "lifespan must call check_startup_secrets once, at its top level"
    assert _call_args(top[0].value) == ["gateway"]
    nested = [n for n in ast.walk(life) if _is_check(n)]
    assert len(nested) == 1, "a second call (inside a try) is not allowed"
    # Before the first await, so a refused start opens no connection.
    first_await = min(n.lineno for n in ast.walk(life) if isinstance(n, ast.Await))
    assert top[0].lineno < first_await


def test_the_edge_app_runs_the_check_at_start():
    src = (ROOT / "apps/services/agent_edge/agent_edge/app.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src)) if _is_check(n)]
    assert [_call_args(c) for c in calls] == [["agent_edge"]]
