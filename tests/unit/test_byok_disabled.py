"""WS-31 CP-11 slice 4 — BYOK is OFF for the customer.

Spec: ``project-docs/specs/customer_console.md`` §5.1 (provider/model/tier tabs
leave the customer product) · D32.7 (customers never see a model) · D57.7 (BYOK
is reached by configuration, never by a failure path). Owner directive
2026-08-27.

**What this fences.** ``BYOK_ENABLED`` defaults False, and while it is False no
caller can write a provider API key through the product. There are **two**
doors, and closing one alone closes nothing:

1. ``POST /settings/llm/key`` — the front door.
2. ``POST /integrations/configure`` — the fallback the workbench route
   (``api/settings/llm/key/route.ts``) takes whenever the front door answers
   *"No env var for provider"*. It writes an arbitrary env var by design.

**What it must NOT break.** Reading an installed key, and every non-LLM
integration credential. The product runs on the keys already installed while
``ROUTER_SERVING_ENABLED`` is off, so a flag that stopped AI calls would be a
regression, not a fix.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GW = ROOT / "apps/services/gateway/gateway/routes"
SETTINGS_ROUTE = GW / "settings.py"
INTEGRATIONS_ROUTE = GW / "integrations.py"
ACB_SETTINGS = ROOT / "packages/acb_common/acb_common/settings.py"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def byok(monkeypatch):
    """Set BYOK_ENABLED and bust the settings LRU, both directions."""
    from acb_common.settings import get_settings

    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("BYOK_ENABLED", raising=False)
        else:
            monkeypatch.setenv("BYOK_ENABLED", value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


# ── The flag itself ────────────────────────────────────────────────────────


class TestTheFlagDefaultsOff:
    def test_default_is_false(self, byok) -> None:
        from acb_common.settings import get_settings

        byok(None)
        assert get_settings().byok_enabled is False

    def test_the_declared_default_is_false_in_source(self) -> None:
        # A default flipped in a later edit is the whole risk, so pin the
        # literal and not only the runtime value.
        assert re.search(
            r"^\s*byok_enabled:\s*bool\s*=\s*False\s*$",
            _src(ACB_SETTINGS),
            re.MULTILINE,
        ), "byok_enabled must be declared `= False`"

    def test_env_var_turns_it_on(self, byok) -> None:
        from acb_common.settings import get_settings

        byok("true")
        assert get_settings().byok_enabled is True


# ── The guard ──────────────────────────────────────────────────────────────


class TestTheGuard:
    def test_refuses_with_403_when_off(self, byok) -> None:
        from fastapi import HTTPException
        from gateway.routes.settings import refuse_if_byok_disabled

        byok("false")
        with pytest.raises(HTTPException) as exc:
            refuse_if_byok_disabled()
        assert exc.value.status_code == 403

    def test_the_refusal_names_no_env_var(self, byok) -> None:
        # The customer reads this string. Our internal variable vocabulary
        # is not the customer's, and `launch_surface` makes that point about
        # the billing nav entry for the same reason.
        from fastapi import HTTPException
        from gateway.routes.settings import refuse_if_byok_disabled

        byok("false")
        with pytest.raises(HTTPException) as exc:
            refuse_if_byok_disabled()
        assert "BYOK_ENABLED" not in str(exc.value.detail)

    def test_allows_when_on(self, byok) -> None:
        from gateway.routes.settings import refuse_if_byok_disabled

        byok("true")
        refuse_if_byok_disabled()  # must not raise

    def test_byok_writes_allowed_tracks_the_flag(self, byok) -> None:
        from gateway.routes.settings import byok_writes_allowed

        byok("false")
        assert byok_writes_allowed() is False
        byok("true")
        assert byok_writes_allowed() is True


# ── Both doors, structurally ───────────────────────────────────────────────


def _guard_is_first_statement(src: str, func: str) -> bool:
    """True when ``func``'s FIRST statement calls the guard."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == func:
            body = [s for s in node.body if not isinstance(s, ast.Expr)
                    or not isinstance(s.value, ast.Constant)]
            if not body:
                return False
            first = body[0]
            return (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Call)
                and getattr(first.value.func, "id", "") == "refuse_if_byok_disabled"
            )
    raise AssertionError(f"{func} not found")


class TestBothDoorsAreClosed:
    def test_front_door_guards_before_any_work(self) -> None:
        # Ordering matters: a guard placed after `_write_env_key` would refuse
        # the request AFTER writing the key.
        assert _guard_is_first_statement(_src(SETTINGS_ROUTE), "set_provider_key")

    def test_the_fallback_door_calls_the_same_guard(self) -> None:
        src = _src(INTEGRATIONS_ROUTE)
        assert "refuse_if_byok_disabled" in src, (
            "/integrations/configure is the documented fallback for provider "
            "keys — guarding only /settings/llm/key closes nothing"
        )

    def test_there_is_exactly_one_guard_definition(self) -> None:
        # One seam, not two. A second copy drifts and then lies.
        assert _src(SETTINGS_ROUTE).count("def refuse_if_byok_disabled") == 1
        assert "def refuse_if_byok_disabled" not in _src(INTEGRATIONS_ROUTE)


# ── What must keep working ─────────────────────────────────────────────────


class TestTheBlastRadiusIsProviderKeysOnly:
    def test_non_llm_integrations_are_not_refused(self) -> None:
        """Slack, GitHub OAuth and WhatsApp are not BYOK and must still save.

        The guard fires only when a submitted key is an LLM PROVIDER variable.
        `_PROVIDER_ENV_MAP` is the list of record.
        """
        from gateway.routes.settings import _PROVIDER_ENV_MAP

        provider_vars = {v for v in _PROVIDER_ENV_MAP.values() if v}
        for unrelated in ("SLACK_BOT_TOKEN", "GITHUB_CLIENT_ID", "WHATSAPP_TOKEN"):
            assert unrelated not in provider_vars

    def test_the_fallback_guard_is_conditional_on_a_provider_var(self) -> None:
        # Guarding the whole endpoint would break every integration on the
        # box. Assert the call sits under an `any(... _provider_vars)` test.
        src = _src(INTEGRATIONS_ROUTE)
        window = src[src.index("_provider_vars"): src.index("refuse_if_byok_disabled()") + 40]
        assert "if any(" in window, (
            "the fallback guard must fire only for LLM provider variables"
        )

    def test_no_read_path_is_guarded(self) -> None:
        """Reading an installed key must be untouched — the product uses it."""
        tree = ast.parse(_src(SETTINGS_ROUTE))
        guarded = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
            and any(
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Call)
                and getattr(s.value.func, "id", "") == "refuse_if_byok_disabled"
                for s in ast.walk(n)
            )
        }
        assert guarded == {"set_provider_key"}, (
            f"exactly one handler may be guarded, got: {sorted(guarded)}"
        )


# ── D57.7's sibling: configuration only, never a failure path ──────────────


class TestBYOKIsNeverReachedByFailure:
    def test_nothing_sets_the_flag_at_runtime(self) -> None:
        """A failure path that re-opened the key doors would stop metering.

        D57.7 forbids the same move one layer down: an outage must never
        reclassify a paying customer as BYOK.

        The env var is read in exactly ONE place — the ``byok_enabled`` field
        on ``acb_common.settings``, which names it by FIELD name. So no
        service may mention the environment string at all, whatever the
        mechanism. ``os.environ[...] =``, ``setdefault``, ``putenv``,
        ``setenv`` and every wrapper over them are caught by naming it.
        """
        offenders: list[str] = []
        for path in ROOT.joinpath("apps").rglob("*.py"):
            src = path.read_text(encoding="utf-8", errors="replace")
            if "BYOK_ENABLED" in src:
                offenders.append(f"{path.relative_to(ROOT)} (names BYOK_ENABLED)")
            if re.search(r"""\bbyok_enabled\s*=\s*(?!=)""", src):
                offenders.append(f"{path.relative_to(ROOT)} (assigns byok_enabled)")
        assert not offenders, (
            "BYOK must be reached by CONFIGURATION only (D57.7's sibling). "
            f"A runtime re-open would stop metering: {offenders}"
        )

    def test_the_only_declaration_lives_on_the_settings_seam(self) -> None:
        """One flag, one home. A second declaration drifts and then lies."""
        pattern = re.compile(r"^\s*byok_enabled\s*:\s*bool", re.MULTILINE)
        declarers = sorted(
            p.relative_to(ROOT).as_posix()
            for base in ("packages", "apps")
            for p in ROOT.joinpath(base).rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8", errors="replace"))
        )
        assert declarers == ["packages/acb_common/acb_common/settings.py"], (
            f"expected exactly one declaration, got {declarers}"
        )
