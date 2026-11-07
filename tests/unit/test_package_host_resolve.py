"""WS-PKG PKG-5b part 2 — the pure part of the resolve, with no database.

Spec: ``project-docs/specs/package_host.md`` §5.2. Code:
``gateway/package_host/resolve.py`` and ``gateway/routes/shell/manifest.py``.
The R8 twin is ``tests/unit/test_package_host_resolve_r8.py``.

a. One app at two scopes shows the widest: org, then group, then private,
   then the older install, then the lower id.
b. One key of two apps has one winner by the same order. A key of a system
   package stays the system's. ``triggers`` and ``slot`` keep each claim.
c. A feature that the viewer lacks hides the entry. No feature is open, and
   a feature that is not text fails closed. System entries pass the same
   check.
d. ``needs_setup`` follows the needs. A malformed need fails closed. A
   package need is met by a system package that the viewer can use, or by a
   visible install only. A system package counts when one of its entries
   passes the feature check. A sub-app of a gated host shows its setup card.
e. The order in a point: the system entries in file order, then the claims
   by position and key. A payload cannot replace a fixed field.
f. The version is 16 hex digits, stable, and changes with the content.
g. The route sends the ETag, and answers 304 on a match.
h. The SQL reads the one share rule and the one group read, and keeps no
   copy. The route module runs no SQL.

**R7.** Mutations:

* M1. ``resolve.widest_per_app``: keep the narrowest scope.
  ``test_one_app_at_two_scopes_shows_the_widest`` goes red.
* M2. ``resolve.needs_met``: answer True for a need of an unknown kind.
  ``test_a_malformed_need_fails_closed`` goes red.
* M3. ``resolve.resolve``: put the payload after the fixed fields.
  ``test_a_payload_cannot_replace_a_fixed_field`` goes red.
* M4. ``resolve.system_package_ids``: drop the ``feature_ok`` check.
  ``test_a_gated_system_host_is_missing_when_the_viewer_cannot_use_it`` goes
  red.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.app_manifests.shell_manifest import SystemContribution
from gateway.package_host import resolve, sharing
from gateway.package_host.resolve import ClaimRow, InstallRow
from gateway.routes.shell import manifest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 1, tzinfo=UTC)
ORG = "11111111-2222-3333-4444-555555555555"


def _inst(iid: str, app: str, kind: str = "org", *, age: int = 0,
          manifest: Any = None, subject: str | None = None) -> InstallRow:
    subject = subject or {"org": "org", "group": "group:ops"}.get(kind, "ana@pkg.test")
    return InstallRow(id=iid, app_id=app, scope_kind=kind, scope_subject=subject,
                      created_at=T0 + timedelta(minutes=age),
                      manifest={"id": app} if manifest is None else manifest)


def _claim(iid: str, key: str, /, point: str = "jobs", position: int = 0,
           **payload: Any) -> ClaimRow:
    return ClaimRow(install_id=iid, point=point, key=key, payload=payload, position=position)


def _all(_: str) -> bool:
    return True


def _none(_: str) -> bool:
    return False


def _keys(out: dict[str, list[dict]], point: str = "jobs") -> list[tuple[str, str | None]]:
    return [(e["key"], e["install_id"]) for e in out.get(point, [])]


# ── a. One app ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("installs", "winner"), [
    ([_inst("p", "q", "private"), _inst("o", "q", "org", age=5)], "o"),
    ([_inst("p", "q", "private"), _inst("g", "q", "group", age=5)], "g"),
    ([_inst("g", "q", "group"), _inst("o", "q", "org", age=5)], "o"),
    ([_inst("g2", "q", "group", age=1, subject="group:b"),
      _inst("g1", "q", "group", age=0, subject="group:a")], "g1"),
    ([_inst("b", "q", "group"), _inst("a", "q", "group")], "a"),
])
def test_one_app_at_two_scopes_shows_the_widest(installs, winner) -> None:
    kept = resolve.widest_per_app(installs)
    assert [i.id for i in kept] == [winner]
    claims = [_claim(i.id, f"k-{i.id}") for i in installs]
    out = resolve.resolve({}, installs, claims, _all)
    assert _keys(out) == [(f"k-{winner}", winner)]


# ── b. One key ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("installs", "winner"), [
    ([_inst("p", "a1", "private"), _inst("g", "a2", "group", age=5)], "g"),
    ([_inst("g", "a1", "group"), _inst("o", "a2", "org", age=5)], "o"),
    ([_inst("new", "a1", "group", age=5), _inst("old", "a2", "group")], "old"),
    ([_inst("b", "a1", "private"), _inst("a", "a2", "private")], "a"),
])
def test_one_key_of_two_apps_has_one_winner(installs, winner) -> None:
    claims = [_claim(i.id, "quote", label=i.id) for i in installs]
    out = resolve.resolve({}, installs, claims, _all)
    assert _keys(out) == [("quote", winner)]
    assert out["jobs"][0]["label"] == winner


def test_a_system_key_stays_the_systems() -> None:
    system = {"jobs": (SystemContribution("jobs", "quote", "crm", None),)}
    out = resolve.resolve(system, [_inst("o", "a")], [_claim("o", "quote")], _all)
    assert _keys(out) == [("quote", None)]


@pytest.mark.parametrize("point", ["triggers", "slot"])
def test_triggers_and_slot_keep_each_claim(point: str) -> None:
    installs = [_inst("o", "a1"), _inst("g", "a2", "group")]
    claims = [_claim("o", "k", point), _claim("g", "k", point)]
    out = resolve.resolve({}, installs, claims, _all)
    assert _keys(out, point) == [("k", "o"), ("k", "g")]


def test_a_claim_of_an_install_the_viewer_does_not_see_never_shows() -> None:
    out = resolve.resolve({}, [_inst("o", "a")], [_claim("x", "k")], _all)
    assert out == {}


# ── c. The feature ─────────────────────────────────────────────────────────


def test_a_feature_the_viewer_lacks_hides_the_entry() -> None:
    claims = [_claim("o", "open"), _claim("o", "crm", feature="crm"),
              _claim("o", "bad", feature=7), _claim("o", "blank", feature="")]
    can = {"crm"}.__contains__
    out = resolve.resolve({}, [_inst("o", "a")], claims, can)
    assert [k for k, _ in _keys(out)] == ["crm", "open"]
    out = resolve.resolve({}, [_inst("o", "a")], claims, _none)
    assert [k for k, _ in _keys(out)] == ["open"]


def test_a_system_entry_passes_the_same_check() -> None:
    system = {"nav": (SystemContribution("nav", "/crm", "crm", "crm"),
                      SystemContribution("nav", "/home", "home", None))}
    assert _keys(resolve.resolve(system, [], [], _none), "nav") == [("/home", None)]
    entry = resolve.resolve(system, [], [], _all)["nav"][0]
    assert entry == {"key": "/crm", "source": "system", "install_id": None,
                     "needs_setup": False, "feature": "crm", "package": "crm"}


# ── d. The needs ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("needs", "setup"), [
    ([], False),
    ([{"feature": "crm"}], False),
    ([{"feature": "hr"}], True),
    ([{"package": "crm"}], False),
    ([{"package": "other"}], False),
    ([{"package": "missing"}], True),
])
def test_needs_setup_follows_the_needs(needs, setup: bool) -> None:
    system = {"nav": (SystemContribution("nav", "/crm", "crm", None),)}
    installs = [_inst("o", "a", manifest={"id": "mine", "needs": needs}),
                _inst("p", "b", "private", manifest={"id": "other"})]
    out = resolve.resolve(system, installs, [_claim("o", "k")], {"crm"}.__contains__)
    assert out["jobs"][0]["needs_setup"] is setup


@pytest.mark.parametrize("manifest", [
    {"id": "m", "needs": [{"connection": "zoho"}]},
    {"id": "m", "needs": [{"feature": "crm", "package": "crm"}]},
    {"id": "m", "needs": [{"feature": 3}]},
    {"id": "m", "needs": [{"feature": ""}]},
    {"id": "m", "needs": ["crm"]},
    {"id": "m", "needs": {"feature": "crm"}},
    "not an object",
    [],
])
def test_a_malformed_need_fails_closed(manifest) -> None:
    out = resolve.resolve({}, [_inst("o", "a", manifest=manifest)], [_claim("o", "k")], _all)
    assert out["jobs"][0]["needs_setup"] is True


def test_a_need_of_an_app_the_viewer_cannot_see_is_unmet() -> None:
    """The R8 suite proves that step 2 leaves out the install. Here the
    resolve gets no row of it, so the need stays unmet."""
    installs = [_inst("o", "a", manifest={"id": "m", "needs": [{"package": "hidden"}]})]
    out = resolve.resolve({}, installs, [_claim("o", "k")], _all)
    assert out["jobs"][0]["needs_setup"] is True


# ── d2. The host of a sub-app (PKG-15a) ───────────────────────────────────

_CRM = {"nav": (SystemContribution("nav", "/crm", "crm", None),)}


def _sub(extends: Any, iid: str = "s", app: str = "sub") -> InstallRow:
    return _inst(iid, app, manifest={"id": app, "extends": extends})


def _sub_out(installs: list[InstallRow], system: Any = None) -> dict[str, Any]:
    out = resolve.resolve(_CRM if system is None else system, installs,
                          [_claim(i.id, f"k-{i.id}") for i in installs], _all)
    return {e["install_id"]: e for e in out["jobs"]}


def test_a_host_that_is_a_system_package_is_met() -> None:
    entry = _sub_out([_sub("crm")])["s"]
    assert entry["needs_setup"] is False
    assert entry["extends"] == "crm"


def test_a_host_that_is_an_active_install_of_the_viewer_is_met() -> None:
    host = _inst("h", "hostapp", "private")
    assert _sub_out([_sub("hostapp"), host], {})["s"]["needs_setup"] is False


def test_a_host_that_is_absent_is_missing() -> None:
    entry = _sub_out([_sub("nohost")])["s"]
    assert entry["needs_setup"] is True
    assert entry["extends"] == "nohost"


# A paused or archived host, and a host that only another org holds, are
# facts of the SQL of step 2. The R8 twins prove them on a database:
# ``test_r8_a_host_in_the_own_org_is_met_until_it_is_paused`` and
# ``test_r8_a_host_that_only_another_org_holds_is_missing``.

_GATED_CRM = {"jobs": (SystemContribution("jobs", "crm.lead", "crm", "crm"),),
              "nav": (SystemContribution("nav", "/crm", "crm", "crm"),)}


def _gated(extends: Any, can_use: Any, system: Any = None) -> dict[str, Any]:
    out = resolve.resolve(_GATED_CRM if system is None else system,
                          [_sub(extends)], [_claim("s", "k-s")], can_use)
    return {e["install_id"]: e for e in out["jobs"]}


def test_a_gated_system_host_is_met_when_the_viewer_can_use_it() -> None:
    entry = _gated("crm", {"crm"}.__contains__)["s"]
    assert entry["needs_setup"] is False
    assert entry["extends"] == "crm"


def test_a_gated_system_host_is_missing_when_the_viewer_cannot_use_it() -> None:
    """The gate hides each CRM entry, so CRM is not present for this viewer.
    The sub-app shows its setup card, and still names its host."""
    out = _gated("crm", _none)
    assert set(out) == {"s"}
    assert out["s"]["needs_setup"] is True
    assert out["s"]["extends"] == "crm"


def test_one_usable_entry_makes_a_system_host_present() -> None:
    system = {"nav": (SystemContribution("nav", "/crm", "crm", "crm"),
                      SystemContribution("nav", "/crm/open", "crm", None))}
    assert _gated("crm", _none, system)["s"]["needs_setup"] is False


def test_a_system_host_with_no_feature_is_met_for_each_viewer() -> None:
    system = {"nav": (SystemContribution("nav", "/home", "home", None),)}
    assert _gated("home", _none, system)["s"]["needs_setup"] is False


def test_a_bad_feature_on_a_system_entry_fails_closed() -> None:
    system = {"nav": (SystemContribution("nav", "/crm", "crm", ""),)}  # type: ignore[arg-type]
    assert _gated("crm", _all, system)["s"]["needs_setup"] is True


@pytest.mark.parametrize(("can_use", "setup"), [({"crm"}.__contains__, False), (_none, True)])
def test_a_package_need_of_a_gated_system_package_follows_the_gate(can_use, setup) -> None:
    man = {"id": "mine", "needs": [{"package": "crm"}]}
    out = resolve.resolve(_GATED_CRM, [_inst("o", "a", manifest=man)], [_claim("o", "k")],
                          can_use)
    assert _keys(out) == ([("crm.lead", None)] if setup is False else []) + [("k", "o")]
    assert out["jobs"][-1]["needs_setup"] is setup


@pytest.mark.parametrize("extends", [3, "", "Bad Id", "UPPER", "a" * 60, ["crm"], {"id": "crm"}, True])
def test_a_malformed_extends_fails_closed(extends: Any) -> None:
    entry = _sub_out([_sub(extends)])["s"]
    assert entry["needs_setup"] is True
    assert "extends" not in entry


def test_a_null_extends_is_no_host() -> None:
    entry = _sub_out([_sub(None)])["s"]
    assert entry["needs_setup"] is False
    assert "extends" not in entry


def test_a_sub_app_is_never_its_own_host() -> None:
    entry = _sub_out([_sub("sub")], {})["s"]
    assert entry["needs_setup"] is True


def test_the_host_adds_one_need_to_the_other_needs() -> None:
    man = {"id": "sub", "extends": "crm", "needs": [{"feature": "hr"}]}
    out = resolve.resolve(_CRM, [_inst("s", "sub", manifest=man)], [_claim("s", "k")],
                          {"crm"}.__contains__)
    assert out["jobs"][0]["needs_setup"] is True


def test_only_an_install_with_extends_carries_the_extends_key() -> None:
    plain = _inst("p", "plain")
    sub = _sub("crm")
    out = _sub_out([plain, sub])
    assert out["p"] == {"key": "k-p", "source": "org", "install_id": "p", "needs_setup": False}
    assert out["s"]["extends"] == "crm"


def test_a_payload_cannot_set_the_extends_key() -> None:
    out = resolve.resolve({}, [_inst("p", "plain")], [_claim("p", "k", extends="crm")], _all)
    assert "extends" not in out["jobs"][0]


def test_the_version_changes_only_for_an_install_with_extends() -> None:
    plain = [_inst("p", "plain")]
    before = resolve.version_of(resolve.resolve({}, plain, [_claim("p", "k")], _all))
    again = resolve.version_of(resolve.resolve({}, plain, [_claim("p", "k")], _all))
    assert before == again
    sub = [_inst("p", "plain", manifest={"id": "plain", "extends": "crm"})]
    assert before != resolve.version_of(resolve.resolve({}, sub, [_claim("p", "k")], _all))


# ── e. The order ───────────────────────────────────────────────────────────


def test_system_first_then_position_then_key() -> None:
    system = {"jobs": (SystemContribution("jobs", "z-sys", "p", None),
                       SystemContribution("jobs", "a-sys", "p", None))}
    installs = [_inst("o", "a1"), _inst("g", "a2", "group")]
    claims = [_claim("o", "b", position=1), _claim("g", "a", position=1),
              _claim("o", "c", position=0), _claim("g", "d", position=-1)]
    out = resolve.resolve(system, installs, claims, _all)
    assert [k for k, _ in _keys(out)] == ["z-sys", "a-sys", "d", "c", "a", "b"]


def test_a_payload_cannot_replace_a_fixed_field() -> None:
    claim = _claim("o", "k", key="other", source="system", install_id=None,
                   needs_setup=False, label="Quotes")
    entry = resolve.resolve({}, [_inst("o", "a", manifest={"id": "m", "needs": ["x"]})],
                            [claim], _all)["jobs"][0]
    assert entry == {"key": "k", "source": "org", "install_id": "o",
                     "needs_setup": True, "label": "Quotes"}


# ── f. The version ─────────────────────────────────────────────────────────


def test_the_version_is_stable_and_follows_the_content() -> None:
    a = {"jobs": [{"key": "k", "label": "A", "n": 1}]}
    same = {"jobs": [{"n": 1, "label": "A", "key": "k"}]}
    other = {"jobs": [{"key": "k", "label": "B", "n": 1}]}
    v = resolve.version_of(a)
    assert re.fullmatch(r"[0-9a-f]{16}", v)
    assert resolve.version_of(same) == v
    assert resolve.version_of(other) != v
    assert resolve.manifest_body(a) == {"version": v, "contributions": a}


@pytest.mark.parametrize(("header", "want"), [
    (None, False), ("", False), ('"abc"', True), ('W/"abc"', True),
    ('"x", "abc"', True), ("*", True), ('"abcd"', False), ("abc", False),
])
def test_etag_matches(header, want: bool) -> None:
    assert resolve.etag_matches(header, "abc") is want


# ── g. The route ───────────────────────────────────────────────────────────


def _user() -> UserContext:
    return UserContext(email="Ana@Pkg.Test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:crm"]), organization_id=ORG)


def _call(monkeypatch, headers: list[tuple[bytes, bytes]]) -> Any:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    seen: dict[str, Any] = {}

    class _Session:
        async def __aenter__(self):
            return "db"

        async def __aexit__(self, *_a):
            return False

    async def fake_resolve_for(db, *, viewer, can_use):
        seen.update(db=db, viewer=viewer, crm=can_use("crm"), hr=can_use("hr"))
        return resolve.manifest_body({"jobs": [{"key": "k"}]})

    monkeypatch.setattr(manifest, "_tenant_session", lambda: _Session())
    monkeypatch.setattr(resolve, "resolve_for", fake_resolve_for)
    req = Request({"type": "http", "method": "GET", "path": "/shell/manifest",
                   "headers": headers})
    return asyncio.run(manifest.shell_manifest(req, _user())), seen


def test_the_route_sends_the_body_and_the_etag(monkeypatch) -> None:
    resp, seen = _call(monkeypatch, [])
    version = resolve.version_of({"jobs": [{"key": "k"}]})
    assert resp.status_code == 200
    assert resp.headers["etag"] == f'"{version}"'
    assert resp.headers["cache-control"] == "private, no-cache"
    assert seen == {"db": "db", "viewer": "Ana@Pkg.Test", "crm": True, "hr": False}


def test_the_route_answers_304_on_a_match(monkeypatch) -> None:
    version = resolve.version_of({"jobs": [{"key": "k"}]})
    resp, _ = _call(monkeypatch, [(b"if-none-match", f'"{version}"'.encode())])
    assert resp.status_code == 304
    assert resp.body == b""
    assert resp.headers["etag"] == f'"{version}"'
    resp, _ = _call(monkeypatch, [(b"if-none-match", b'"stale"')])
    assert resp.status_code == 200


# ── h. One copy of each rule ───────────────────────────────────────────────


def test_the_sql_reads_the_one_share_rule() -> None:
    assert f"WHERE {sharing.SHARE_ROW} AND lower(g.subject) = :viewer" in resolve.INSTALLS_SQL
    pkg = ROOT / "apps/services/gateway/gateway/package_host"
    copies = [p.name for p in pkg.glob("*.py")
              if "strpos(g.subject" in p.read_text(encoding="utf-8")]
    assert copies == ["sharing.py"]


def test_the_resolve_reads_the_one_group_read() -> None:
    src = inspect.getsource(resolve)
    assert "from gateway.routes.crm.access import my_groups" in src
    assert "org_group_member" not in src
    assert "_MY_GROUPS_SQL" not in src


def test_the_route_module_runs_no_sql() -> None:
    tree = ast.parse(inspect.getsource(manifest))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "text" not in names
    assert not re.search(r"\bSELECT\b", inspect.getsource(manifest))
