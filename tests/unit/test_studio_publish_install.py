"""WS-PKG PKG-8a — a published Studio app joins the registry, with no database.

Spec: ``project-docs/specs/package_host.md``, the PKG-8a row of §10. Code:
``gateway/package_host/studio_app.py``, ``store.set_version`` and the
publish route ``gateway/routes/studio/packages.py``. The R8 twin is
``tests/unit/test_studio_publish_install_r8.py``.

The checks here:

1. The claims of a published app are one ``nav`` entry: the key is the
   href, and the payload holds the href, the label, the icon and the feature.
2. The href is outside each system pane, so the sidebar keeps it
   (``systemOwns`` in ``packageManifest.ts``).
3. The gateway and the workbench name one prefix, the route exists, and the
   gate of ``/apps`` is the feature of the payload.
4. ``keep_private_install`` moves an install that exists, and writes one only
   when there is none.
5. The publish route answers a store refusal with ``detail_for`` of the
   member, never with ``holder``, and it keeps the install after the sync.

**R7.** M1: give ``claims_of`` an href under ``/studio``. Check 2 goes red.
M2: drop the ``moved is not None`` return of ``keep_private_install``. Check
4 goes red. M3: read ``exc.holder`` in ``_keep_install``. Check 5 goes red.
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any

from acb_common.app_manifests.shell_manifest import load_contributes, load_gates
from gateway.package_host import store, studio_app
from gateway.routes.studio import packages

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "workbench" / "control_plane" / "src"
PID = "3f2b7c1e-0a4d-4e8b-9c6f-1d2e3f4a5b6c"


# ── 1. The claims ──────────────────────────────────────────────────────────


def test_a_published_app_claims_one_nav_entry_keyed_by_its_href() -> None:
    claims = studio_app.claims_of(PID, {"name": "  Field visits  "})
    assert claims == [store.Contribution(point="nav", key=f"/apps/{PID}", payload={
        "href": f"/apps/{PID}", "label": "Field visits", "icon": "Boxes", "feature": "studio"})]


def test_an_app_with_no_name_is_untitled() -> None:
    for manifest in ({}, {"name": "   "}, {"name": 7}):
        [claim] = studio_app.claims_of(PID, manifest)
        assert claim.payload["label"] == "Untitled app"


# ── 1b. The jobs (PKG-8c) ──────────────────────────────────────────────────


def _jobs(*items: Any) -> dict[str, Any]:
    return {"name": "V", "shell": {"jobs": list(items)}}


def test_a_job_claims_the_key_app_and_view_and_opens_the_app() -> None:
    claims = studio_app.claims_of(PID, _jobs({"verb": "Log a visit", "view": "visits"}))
    assert claims[1:] == [store.Contribution(point="jobs", key=f"{PID}:visits", payload={
        "label": "Log a visit", "href": f"/apps/{PID}", "feature": "studio", "app": "V"},
        position=0)]
    assert claims[0].point == "nav"


def test_a_job_href_is_the_href_of_the_nav_claim() -> None:
    nav, job = studio_app.claims_of(PID, _jobs({"verb": "Go", "view": "v"}))
    assert job.payload["href"] == nav.payload["href"] == nav.key == studio_app.app_href(PID)


def test_no_shell_block_gives_the_nav_claim_only() -> None:
    for manifest in ({"name": "V"}, {"shell": None}, {"shell": {}}, {"shell": {"jobs": []}},
                     {"shell": {"jobs": "x"}}, {"shell": "x"}):
        assert [c.point for c in studio_app.claims_of(PID, manifest)] == ["nav"], manifest


def test_two_jobs_on_one_view_keep_the_first() -> None:
    claims = studio_app.claims_of(PID, _jobs(
        {"verb": "First", "view": "v"}, {"verb": "Second", "view": "v"}))
    assert [(c.key, c.payload["label"]) for c in claims[1:]] == [(f"{PID}:v", "First")]


def test_the_jobs_stop_at_twenty() -> None:
    items = [{"verb": f"Do {n}", "view": f"v{n}"} for n in range(30)]
    claims = studio_app.claims_of(PID, _jobs(*items))
    assert len(claims) == 1 + studio_app.MAX_JOBS == 21
    assert claims[-1].key == f"{PID}:v19"


def test_a_malformed_job_is_skipped() -> None:
    claims = studio_app.claims_of(PID, _jobs(
        None, "x", {}, {"verb": "", "view": "v"}, {"verb": "  ", "view": "v"},
        {"verb": "Go"}, {"view": "v"}, {"verb": 1, "view": "v"}, {"verb": "Go", "view": 2},
        {"verb": "Good", "view": "ok"}))
    assert [c.key for c in claims[1:]] == [f"{PID}:ok"]


def test_the_jobs_keep_the_manifest_order_in_position() -> None:
    claims = studio_app.claims_of(PID, _jobs(
        {"verb": "A", "view": "a"}, {"verb": "", "view": "x"}, {"verb": "B", "view": "b"}))
    assert [(c.key.split(":")[1], c.position) for c in claims[1:]] == [("a", 0), ("b", 1)]


def test_the_verb_is_stripped_and_a_long_or_unsafe_verb_is_skipped() -> None:
    limit = studio_app.MAX_LABEL_CHARS
    claims = studio_app.claims_of(PID, _jobs(
        {"verb": "  Go  ", "view": "a"},
        {"verb": "x" * limit, "view": "edge"},
        {"verb": "x" * (limit + 1), "view": "long"},
        {"verb": "Del\u202eete", "view": "bidi"},
        {"verb": "Del\u2066ete", "view": "bidi2"},
        {"verb": "Del\x07ete", "view": "ctl"},
        {"verb": "Del\nete", "view": "nl"}))
    assert [c.key.split(":")[1] for c in claims[1:]] == ["a", "edge"]
    assert claims[1].payload["label"] == "Go"


def test_the_app_name_in_the_payload_is_clean_and_cut() -> None:
    limit = studio_app.MAX_LABEL_CHARS
    job = {"verb": "Go", "view": "v"}
    name = "  Fi\u202eeld\x07" + "n" * 200
    [_, got] = studio_app.claims_of(PID, {**_jobs(job), "name": name})
    assert got.payload["app"] == "Field" + "n" * (limit - 5)
    for bad in (None, "", "  ", "\u202e", 7):
        [_, got] = studio_app.claims_of(PID, {**_jobs(job), "name": bad})
        assert "app" not in got.payload, bad


# ── 2. The sidebar keeps the href ──────────────────────────────────────────


def test_no_system_pane_owns_the_href() -> None:
    """``systemOwns`` drops an href that equals a system pane or sits under
    it. ``/studio`` is a system pane, so an href there never shows."""
    href = studio_app.app_href(PID)
    panes = [c.key for c in load_contributes()["nav"]]
    assert "/studio" in panes
    owners = [p for p in panes if href == p or href.startswith(f"{p}/")]
    assert owners == [], owners


# ── 3. One prefix, one route, one gate ─────────────────────────────────────


def test_the_workbench_names_the_same_prefix_and_serves_it() -> None:
    helper = (WORKBENCH / "lib" / "shell" / "installedApp.ts").read_text(encoding="utf-8")
    found = re.search(r'INSTALLED_APP_PREFIX = "([^"]+)"', helper)
    assert found and found.group(1) == studio_app.APP_HREF_PREFIX
    page = (WORKBENCH / "app" / "apps" / "[id]" / "page.tsx").read_text(encoding="utf-8")
    assert "installedAppTarget" in page and "redirect(" in page


def test_the_route_is_gated_on_the_feature_of_the_payload() -> None:
    gates = dict(load_gates().href_features)
    assert gates[studio_app.APP_HREF_PREFIX.rstrip("/")] == studio_app.NAV_FEATURE == "studio"
    assert gates["/studio"] == studio_app.NAV_FEATURE


# ── 4. Move or write ───────────────────────────────────────────────────────


def _fake_store(monkeypatch: Any, moved: store.Install | None) -> list[str]:
    calls: list[str] = []

    async def set_version(db: Any, **kw: Any) -> store.Install | None:
        calls.append(f"set_version:{kw['scope_kind']}:{kw['scope_subject']}:{kw['version']}")
        return moved

    async def install(db: Any, **kw: Any) -> store.Install:
        calls.append(f"install:{kw['scope_kind']}:{kw['installed_by']}:{kw['ends_at']}")
        return store.Install(id="new", app_id=kw["app_id"], version=kw["version"],
                             scope_kind="private", scope_subject=kw["scope_subject"],
                             installed_by=kw["installed_by"], ends_at=None, contributions=())

    monkeypatch.setattr(store, "set_version", set_version)
    monkeypatch.setattr(store, "install", install)
    return calls


def _keep() -> store.Install:
    return asyncio.run(studio_app.keep_private_install(
        object(), app_id=PID, version=3, owner="ana@pkg8a.test", manifest={"name": "V"}))


def test_the_first_publish_writes_the_install(monkeypatch) -> None:
    calls = _fake_store(monkeypatch, None)
    assert _keep().id == "new"
    assert calls == ["set_version:private:ana@pkg8a.test:3",
                     "install:private:ana@pkg8a.test:None"]


def test_a_later_publish_moves_the_install_and_writes_no_second(monkeypatch) -> None:
    moved = store.Install(id="old", app_id=PID, version=3, scope_kind="private",
                          scope_subject="ana@pkg8a.test", installed_by="ana@pkg8a.test",
                          ends_at=None, contributions=())
    calls = _fake_store(monkeypatch, moved)
    assert _keep().id == "old"
    assert calls == ["set_version:private:ana@pkg8a.test:3"]


# ── 5. The route ───────────────────────────────────────────────────────────


def _tree() -> ast.Module:
    return ast.parse(Path(packages.__file__).read_text(encoding="utf-8"))


def _function(name: str) -> ast.AsyncFunctionDef:
    return next(n for n in ast.walk(_tree())
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def test_a_store_refusal_is_its_detail_for_the_member_and_never_the_holder() -> None:
    keep = _function("_keep_install")
    source = ast.unparse(keep)
    assert "exc.detail_for(user.email)" in source
    assert "status_code=exc.status" in source
    attrs = {n.attr for n in ast.walk(_tree()) if isinstance(n, ast.Attribute)}
    assert "holder" not in attrs and "holder_for" not in attrs


def test_the_switch_of_the_package_host_decides_and_the_install_follows_the_sync() -> None:
    keep = ast.unparse(_function("_keep_install"))
    assert "package_host_allowed(user.organization_id)" in keep
    publish = _function("publish")
    lines = {ast.unparse(n.func): n.lineno for n in ast.walk(publish) if isinstance(n, ast.Call)}
    assert lines["objects_store.sync_objects"] < lines["_keep_install"]
    assert lines["store.publish_version"] < lines["_keep_install"]


# ── 6. The lock of a new version (fix round 1) ─────────────────────────────


def test_set_version_takes_the_org_lock_through_the_one_helper() -> None:
    """PKG-9 merged first, so set_version calls _lock_org and keeps no copy
    of the lock block. It runs no _APP_SQL, and its comment says why."""
    import inspect

    src = inspect.getsource(store.set_version)
    assert '_lock_org(db, "a new version")' in src
    for copied in ("_LOCK_SQL", "_ORG_SQL", "transaction_isolation"):
        assert copied not in src, copied
    assert "_APP_SQL" in src and "check the app first" in src
