"""CP-APP — the pure rules of an app (Aroh), with no database (Metorite task 1105).

The R8 half, which runs every join and every refusal against a real Postgres,
is ``test_customer_console_aroh_app.py``. This file holds what needs no server:
the app vocabulary, the role rule of an app seat, the meter attribution, the
capability list of the mint CLI, the two role-matrix rows, and the static
shape of migration 038.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console import apps, auth, deployment_keys, operator_roles
from customer_console.operators import ADMIN, VIEWER

_ROOT = Path(__file__).resolve().parents[2]
_LADDER = _ROOT / "infra" / "customer_console"


def _migration() -> str:
    """038 found by CONTENT, never by number: R1 may renumber it at merge."""
    for path in sorted(_LADDER.glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        if "CREATE TABLE IF NOT EXISTS org_app" in body:
            return body
    raise AssertionError("no Customer Console migration creates org_app")


class TestTheAppVocabulary:
    def test_aroh_is_the_one_app(self):
        assert frozenset({"aroh"}) == apps.KNOWN_APPS
        assert apps.is_app_plan("aroh") and not apps.is_app_plan("core")
        assert apps.APP_NAMES["aroh"] == "Aroh"

    def test_the_not_entitled_refusal_is_a_reason_not_a_sentence(self):
        assert apps.not_entitled_detail("aroh") == {
            "reason": "app_not_entitled", "app": "aroh"}


class TestTheAppSeatRoleRule:
    @pytest.mark.parametrize("role", ["owner", "admin", "member"])
    def test_a_member_acts_on_their_own_seat_with_any_role(self, role):
        assert apps.may_act_on_app_seat(
            actor_identity_id="i1", target_identity_id="i1",
            actor_role=role, actor_status="invited")

    @pytest.mark.parametrize("role,status,ok", [
        ("owner", "active", True),
        ("admin", "active", True),
        ("member", "active", False),
        ("owner", "invited", False),
    ])
    def test_only_an_active_owner_or_admin_acts_for_another(self, role, status, ok):
        assert apps.may_act_on_app_seat(
            actor_identity_id="i1", target_identity_id="i2",
            actor_role=role, actor_status=status) is ok


class TestTheMeterAttribution:
    def test_a_header_the_box_sends_wins(self):
        assert apps.app_attribution("aroh", agent="aroh-research",
                                    module_slug="notes") == ("aroh-research", "notes")

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_missing_header_takes_the_app_name(self, blank):
        assert apps.app_attribution("aroh", agent=blank, module_slug=blank) == (
            "aroh", "aroh")


class TestTheMintCliVocabulary:
    def test_the_cli_knows_every_capability_auth_names(self):
        named = {
            v for k, v in vars(auth).items()
            if k.endswith("_CAPABILITY") and isinstance(v, str)
        }
        assert named == deployment_keys.KNOWN_CAPABILITIES

    def test_the_aroh_set_is_exactly_resolve_seat_admin_serve(self):
        assert deployment_keys.AROH_CAPABILITIES == ("resolve", "seat_admin", "serve")

    def test_parse_keeps_the_order_and_drops_a_repeat(self):
        assert deployment_keys.parse_capabilities(" resolve, serve ,resolve") == (
            "resolve", "serve")

    @pytest.mark.parametrize("raw", ["", " , ", "resolve,rezolve"])
    def test_parse_refuses_an_empty_set_and_an_unknown_name(self, raw):
        with pytest.raises(deployment_keys.KeyRefused):
            deployment_keys.parse_capabilities(raw)


class TestTheRoleMatrix:
    def test_any_operator_reads_the_apps_of_an_org(self):
        assert operator_roles.rule_for("GET", "/orgs/apps").min_role == VIEWER

    def test_turning_an_app_on_is_the_seat_count_s_gate(self):
        rule = operator_roles.rule_for("POST", "/orgs/apps")
        count = operator_roles.rule_for("POST", "/billing/seats/count")
        assert rule.min_role == ADMIN and rule.elevated is True
        assert rule == count


class TestMigration038:
    def test_it_holds_no_per_cent_character(self):
        """psycopg reads a per-cent sign as a placeholder (see 003)."""
        assert "%" not in _migration()

    def test_it_is_replay_safe_by_construction(self):
        body = _migration()
        assert "ADD COLUMN IF NOT EXISTS app" in body
        assert "CREATE UNIQUE INDEX IF NOT EXISTS deployment_app_uniq" in body
        assert "ON CONFLICT (slug) DO NOTHING" in body
        # Each CHECK swap runs only while its new constraint is absent.
        assert body.count("IF NOT EXISTS (\n        SELECT 1 FROM pg_constraint") == 2

    def test_the_aroh_plan_is_seeded_inactive(self):
        m = re.search(r"VALUES \('aroh', 'Aroh', 'app', 0\.00, (\w+), \d+\)", _migration())
        assert m is not None and m.group(1) == "FALSE"


class TestTheConnectDoorsInTheMatrix:
    def test_reading_the_connection_is_any_operator(self):
        assert operator_roles.rule_for("GET", "/apps/connection").min_role == VIEWER

    @pytest.mark.parametrize("path", [
        "/apps/connection/settings",
        "/apps/connection/connect",
        "/apps/connection/disconnect",
    ])
    def test_each_connect_act_needs_an_elevated_admin(self, path):
        rule = operator_roles.rule_for("POST", path)
        assert rule.min_role == ADMIN and rule.elevated is True


class TestTheAppKeySet:
    def test_the_app_key_set_is_the_cli_aroh_set(self):
        assert apps.APP_KEY_CAPABILITIES["aroh"] == deployment_keys.AROH_CAPABILITIES

    def test_the_default_hub_is_the_dev_aroh(self):
        assert apps.APP_DEFAULT_BASE_URLS["aroh"] == "https://aroh.dev.metorite.com"


class TestTheHubClient:
    @pytest.fixture
    def seen(self):
        import httpx
        from customer_console import app_hub

        calls: list = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"ok": True, "kind": "deployment",
                                             "last4": "abcd", "key": "cc_depl_x_SECRET"})

        app_hub.set_transport(httpx.MockTransport(handler))
        yield calls
        app_hub.set_transport(None)

    def test_a_base_url_is_https_and_the_host_only(self):
        from customer_console.app_hub import clean_base_url

        assert clean_base_url(" https://aroh.dev.metorite.com/ ") == (
            "https://aroh.dev.metorite.com")
        assert clean_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
        for bad in ("http://aroh.dev.metorite.com", "https://h.example/hub", "", "ftp://h"):
            with pytest.raises(ValueError):
                clean_base_url(bad)

    def test_only_the_named_fields_of_an_answer_cross(self, seen):
        from customer_console import app_hub

        answer = app_hub.put_router_key("https://h.invalid", "tok-12345678", "cc_depl_x_y")
        out = app_hub.public_fields(answer)
        assert out == {"hub_status": 200, "ok": True, "kind": "deployment", "last4": "abcd"}
        assert "SECRET" not in repr(out)

    def test_every_hub_call_carries_the_bearer_and_its_path(self, seen):
        from customer_console import app_hub

        base, tok = "https://h.invalid", "tok-12345678"
        app_hub.router_key_status(base, tok)
        app_hub.delete_router_key(base, tok)
        app_hub.list_orgs(base, tok)
        app_hub.list_seats(base, tok, "org-1")
        app_hub.seat_action(base, tok, "s1", "suspend")
        assert [(r.method, r.url.path) for r in seen] == [
            ("GET", "/hub/api/router-key"),
            ("DELETE", "/hub/api/router-key"),
            ("GET", "/hub/api/orgs"),
            ("GET", "/hub/api/seats"),
            ("POST", "/hub/api/seats/s1/suspend"),
        ]
        assert seen[3].url.params["org"] == "org-1"
        assert all(r.headers["authorization"] == f"Bearer {tok}" for r in seen)

    def test_a_seat_act_outside_the_three_is_refused(self):
        from customer_console import app_hub

        with pytest.raises(ValueError):
            app_hub.seat_action("https://h.invalid", "tok-12345678", "s1", "delete")
        with pytest.raises(ValueError):
            app_hub.seat_action("https://h.invalid", "tok-12345678", "a/b", "release")

    def test_an_ok_false_body_is_not_ok(self):
        from customer_console.app_hub import HubAnswer

        assert HubAnswer(200, {"ok": True}).ok is True
        assert HubAnswer(200, {"ok": False}).ok is False
        assert HubAnswer(500, None).ok is False
