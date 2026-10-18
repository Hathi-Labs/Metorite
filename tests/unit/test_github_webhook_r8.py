"""R8 — ``POST /webhooks/github`` and the auto-link rules (WS-54 GH-4).

Spec: ``project-docs/specs/github_integration.md`` §8, §10.3 and §13.1. A real
Postgres (the promoted phase-4 catalog, as the NON-privileged role), the real
router, a signed body. Redis is the private server that ``GITHUB_R8_REDIS_URL``
names, or a fake under the real ``TenantRedis`` wrapper (CI).

* **W1** a wrong signature gets 401 and writes nothing.
* **W2** a compare with ``==`` fails a source fence that requires
  ``hmac.compare_digest``.
* **W3** a 6 MiB body gets 413 with no HMAC call.
* **W4** an unknown installation gets 202 and writes nothing.
* **W5** a second delivery with one GUID writes nothing.
* **W6** an older ``pull_request`` event after a newer one writes nothing.
* **W7** an event for org A's installation never writes a row of org B.
* **W8** the flag off gives 404.
* **W9** a handler that raises answers 500 and deletes the delivery key, and a
  redelivery of the same GUID then runs.
* **L1 to L6** the ``rules`` of ``tests/fixtures/github_autolink_cases.json``.
* §7.5, the installation events: uninstall, suspend, unsuspend, repositories.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* W-M1 ``signature_ok`` returns True -> ``test_w1_*``.
* W-M2 ``hmac.compare_digest`` replaced by ``==`` -> ``test_w2_*``.
* W-M3 the size check of ``_read_capped`` removed -> ``test_w3_*``.
* W-M4 the org from the body's ``organization`` field instead of the lookup ->
  ``test_w4_*`` and ``test_w7_*``.
* W-M5 ``_claim_delivery`` always answers True -> ``test_w5_*``.
* W-M6 drop the ``pr_updated_at <`` guard -> ``test_w6_*``.
* W-M7 drop ``organization_id = CAST(:org AS uuid)`` from the update, as the
  owner role -> ``test_w7_*`` (the app role's RLS still holds, the fence reads
  the SQL too).
* W-M8 no ``_release_delivery`` on a failure -> ``test_w9_*``.
* W-M9 ``trusted_source`` returns True -> the ``L5a`` rule.
* W-M10 no ``archived_at`` check in ``_resolve`` -> the ``L4b`` rule.
* W-M11 ``len(rows) != 1`` becomes ``not rows`` -> the ``L4d`` rule.
* W-M12 ``found[:MAX_LINKS]`` becomes ``found`` -> the ``L6`` rule.

Fix round 1, item 2 (a removed link stays removed), each one red when run:

* 2-M1 ``link_pull_request`` skips the tombstone check ->
  ``test_item2_a_link_a_person_removed_stays_removed``.
* 2-M2 ``removed_by_a_person`` reads the OLDEST row (``ORDER BY created_at``)
  -> the same test (a hand add after the removal must clear it).

Run (real Postgres, private Redis)::

    TENANT_LADDER_DATABASE_URL=... GITHUB_R8_REDIS_URL=redis://127.0.0.1:<port>/0 \\
        uv run pytest tests/unit/test_github_webhook_r8.py -v -rs
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
import json
import textwrap
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit import _github_r8 as g
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient
SECRET = "r8-webhook-secret"
INSTALL_A = 7101
INSTALL_B = 7202
REPO = "acme/widgets"
EMAIL_A = "dev-a@gh4-r8.test"
EMAIL_B = "dev-b@gh4-r8.test"

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "github_autolink_cases.json")
    .read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def p(promoted):  # noqa: F811
    g.grant_lookup(promoted)
    return promoted


@pytest.fixture
def world(p, monkeypatch, tmp_path):
    g.configure_app(monkeypatch, g.make_private_key(tmp_path))
    from gateway import github_sync

    # Item 7: a short join window, so a trailing loop ends inside the test.
    monkeypatch.setattr(github_sync, "JOIN_WINDOW_S", 0.05)
    redis = g.use_redis(monkeypatch)
    g.clear_github(p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_activities WHERE created_by = 'system:github'"))
    g.seed_github_row(p, p.org_a, INSTALL_A)
    g.seed_github_row(p, p.org_b, INSTALL_B)
    calls: list[str] = []

    async def _no_github(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500, json={})

    g.patch_github(monkeypatch, _no_github, REAL_CLIENT)

    class W:
        pass

    w = W()
    w.p, w.redis, w.github_calls = p, redis, calls
    yield w
    g.clear_github(p)


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def _post(
    w: Any, event: str, payload: Any, *, delivery: str | None = None,
    signature: str | None = None, raw: bytes | None = None,
    extra_headers: dict | None = None,
) -> httpx.Response:
    from fastapi import FastAPI
    from gateway.routes.github import webhook

    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
        "X-Hub-Signature-256": signature if signature is not None else _sign(body),
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    app = FastAPI()
    app.include_router(webhook.router)
    async with tenant_engine_scope(g.app_dsn(w.p)), REAL_CLIENT(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
    ) as client:
        return await client.post("/webhooks/github", content=body, headers=headers)


def _pr_payload(
    installation: int, *, number: int = 12, action: str = "synchronize",
    updated_at: str = "2026-10-18T10:00:00Z", body: str = "", head_ref: str = "main",
    head_sha: str = "a" * 40, state: str = "open", merged: bool = False,
    head_repo: str = REPO, association: str = "MEMBER", title: str = "Fix the login",
) -> dict:
    return {
        "action": action,
        "number": number,
        "installation": {"id": installation},
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": number, "title": title, "state": state, "merged": merged,
            "merged_at": "2026-10-18T10:00:00Z" if merged else None,
            "draft": False, "updated_at": updated_at, "body": body,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "author_association": association,
            "head": {"ref": head_ref, "sha": head_sha, "repo": {"full_name": head_repo}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        },
    }


def _seed_ref(p: Any, org: str, task: str, number: int = 12) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "via, created_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), 'pull_request', "
            "'github', :ref, :url, 'ui', 'dev@gh4-r8.test') RETURNING id"),
            {"o": org, "t": task, "ref": f"{REPO}#{number}",
             "url": f"https://github.com/{REPO}/pull/{number}"}).scalar_one())


def _ref(p: Any, ref_id: str) -> Any:
    (row,) = g.rows(p, "SELECT title, state, head_sha, head_ref, pr_updated_at, checks_state "
                       "FROM pm_task_refs WHERE id = CAST(:i AS uuid)", i=ref_id)
    return row


def _refs_of(p: Any, task: str) -> list[Any]:
    return g.rows(p, "SELECT ref, via, created_by FROM pm_task_refs "
                     "WHERE task_id = CAST(:t AS uuid)", t=task)


# ── W1, W2, W3 ──────────────────────────────────────────────────────────────


async def test_w1_a_wrong_signature_gets_401_and_writes_nothing(world) -> None:
    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    payload = _pr_payload(INSTALL_A, title="Forged")
    raw = json.dumps(payload).encode()
    for signature in (_sign(raw, "wrong-secret"), "", "sha256=xyz", "sha1=" + "a" * 40,
                      _sign(raw).upper(), _sign(raw + b" ")):
        resp = await _post(w, "pull_request", payload, raw=raw, signature=signature)
        assert resp.status_code == 401, (signature, resp.status_code)
    assert _ref(w.p, ref).title is None
    assert w.redis.items() == {}


def test_w2_the_compare_is_constant_time() -> None:
    from gateway.routes.github import webhook

    source = textwrap.dedent(inspect.getsource(webhook.signature_ok))
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert any(
        isinstance(c.func, ast.Attribute) and c.func.attr == "compare_digest" for c in calls
    ), "signature_ok must compare with hmac.compare_digest"
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in node.ops):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert not names & {"expected", "match", "header"}, "a signature compared with =="


async def test_w3_a_body_over_the_cap_gets_413_with_no_hmac(world, monkeypatch) -> None:
    from gateway.routes.github import webhook

    seen: list[int] = []
    real = webhook.signature_ok
    monkeypatch.setattr(webhook, "signature_ok",
                        lambda *a, **k: seen.append(1) or real(*a, **k))
    raw = b"{" + b" " * (6 * 1024 * 1024) + b"}"
    resp = await _post(world, "pull_request", None, raw=raw)
    assert resp.status_code == 413
    assert seen == []


# ── W4, W8, the headers ─────────────────────────────────────────────────────


async def test_w4_an_unknown_installation_gets_202_and_writes_nothing(world) -> None:
    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    payload = _pr_payload(999_999, title="Unknown")
    payload["organization"] = {"id": w.p.org_a}  # never read: the org is the lookup's
    resp = await _post(w, "pull_request", payload)
    assert resp.status_code == 202
    assert _ref(w.p, ref).title is None
    assert w.redis.items() == {}


async def test_a_disconnected_installation_is_unknown(world) -> None:
    w = world
    g.seed_github_row(w.p, w.p.org_a, INSTALL_A, status="disconnected")
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    resp = await _post(w, "pull_request", _pr_payload(INSTALL_A, title="After uninstall"))
    assert resp.status_code == 202
    assert _ref(w.p, ref).title is None


async def test_w8_the_flag_off_gives_404_with_no_signature_check(world, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "github_app_enabled", False)
    resp = await _post(world, "pull_request", _pr_payload(INSTALL_A), signature="")
    assert resp.status_code == 404


async def test_the_headers_are_checked_after_the_signature(world, monkeypatch) -> None:
    from acb_common import get_settings

    w = world
    assert (await _post(w, "ping", {"zen": "x"})).status_code == 202
    assert (await _post(w, "pull_request", _pr_payload(INSTALL_A),
                        delivery="not-a-guid")).status_code == 400
    assert (await _post(w, "pull_request", None, raw=b"{not json")).status_code == 400
    monkeypatch.setattr(get_settings(), "github_app_webhook_secret", "")
    assert (await _post(w, "pull_request", _pr_payload(INSTALL_A))).status_code == 503


# ── The pull request write, W5, W6, W7 ──────────────────────────────────────


async def test_a_pull_request_event_writes_every_reference_of_the_org(world, monkeypatch) -> None:
    from gateway import github_sync

    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    jobs: list[tuple[str, str, str]] = []

    async def _read(org: str, repo: str, sha: str) -> None:
        jobs.append((org, repo, sha))

    # GH-5: the checks read is a job AFTER the answer. Recorded here, so the
    # handler's own work can be shown to call nothing at GitHub.
    monkeypatch.setattr(github_sync, "read_checks", _read)
    resp = await _post(w, "pull_request", _pr_payload(INSTALL_A, head_ref="feat/x"))
    assert resp.status_code == 200 and resp.json() == {"ok": True}, resp.text
    row = _ref(w.p, ref)
    assert (row.title, row.state, row.head_sha, row.head_ref) == (
        "Fix the login", "open", "a" * 40, "feat/x")
    assert row.checks_state == "pending"
    assert row.pr_updated_at is not None
    assert w.github_calls == [], "no GitHub read may run before the answer"
    assert jobs == [(w.p.org_a, REPO, "a" * 40)]
    key = [k for k in w.redis.items() if ":github:delivery:" in k]
    assert len(key) == 1 and key[0].startswith(f"cc:{w.p.org_a}:")


async def test_w5_a_second_delivery_with_one_guid_writes_nothing(world) -> None:
    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    guid = str(uuid.uuid4())
    first = await _post(w, "pull_request", _pr_payload(INSTALL_A), delivery=guid)
    assert first.status_code == 200
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_task_refs SET title = 'kept', pr_updated_at = NULL "
                       "WHERE id = CAST(:i AS uuid)"), {"i": ref})
    again = await _post(w, "pull_request", _pr_payload(
        INSTALL_A, updated_at="2026-10-18T11:00:00Z", title="Replayed"), delivery=guid)
    assert again.status_code == 200 and again.json() == {"duplicate": True}
    assert _ref(w.p, ref).title == "kept"


async def test_w6_an_older_event_after_a_newer_one_writes_nothing(world) -> None:
    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    newer = await _post(w, "pull_request", _pr_payload(
        INSTALL_A, updated_at="2026-10-18T12:00:00Z", title="Newer", head_sha="b" * 40))
    assert newer.status_code == 200
    older = await _post(w, "pull_request", _pr_payload(
        INSTALL_A, updated_at="2026-10-18T09:00:00Z", title="Older", head_sha="c" * 40,
        state="closed"))
    assert older.status_code == 200
    row = _ref(w.p, ref)
    assert (row.title, row.head_sha, row.state) == ("Newer", "b" * 40, "open")


async def test_w7_an_event_of_org_a_never_writes_a_row_of_org_b(world) -> None:
    w = world
    task_a = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    task_b = g.seed_task(w.p, w.p.org_b, EMAIL_B)
    ref_a = _seed_ref(w.p, w.p.org_a, task_a)
    ref_b = _seed_ref(w.p, w.p.org_b, task_b)
    resp = await _post(w, "pull_request", _pr_payload(INSTALL_A, title="Only A"))
    assert resp.status_code == 200
    assert _ref(w.p, ref_a).title == "Only A"
    assert _ref(w.p, ref_b).title is None, "org A's event wrote a row of org B"
    # And the SQL itself names the org, so an owner role that skips RLS still
    # writes one org only.
    from gateway import github_sync

    assert "organization_id = CAST(:org AS uuid)" in github_sync._PR_UPDATE_SQL


# ── W9 ──────────────────────────────────────────────────────────────────────


async def test_w9_a_failed_handler_answers_500_and_a_redelivery_runs(world, monkeypatch) -> None:
    from gateway.routes.github import webhook

    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)
    real = webhook._handle
    failed: list[int] = []

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(1)
            raise RuntimeError("the database went away")
        return await real(*args, **kwargs)

    monkeypatch.setattr(webhook, "_handle", flaky)
    guid = str(uuid.uuid4())
    first = await _post(w, "pull_request", _pr_payload(INSTALL_A, title="Retry"), delivery=guid)
    assert first.status_code == 500
    assert not [k for k in w.redis.items() if k.endswith(f":github:delivery:{guid}")]
    assert _ref(w.p, ref).title is None
    again = await _post(w, "pull_request", _pr_payload(INSTALL_A, title="Retry"), delivery=guid)
    assert again.status_code == 200 and again.json() == {"ok": True}
    assert _ref(w.p, ref).title == "Retry"


async def test_a_redis_outage_still_handles_the_event(world, monkeypatch) -> None:
    from gateway.routes.github import webhook

    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    ref = _seed_ref(w.p, w.p.org_a, task)

    class Down:
        def key(self, *parts: str) -> Any:
            from acb_common import tenant_redis

            return tenant_redis.key(*parts)

        async def set(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("redis is down")

        async def delete(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("redis is down")

    monkeypatch.setattr(webhook, "_redis", lambda: Down())
    resp = await _post(w, "pull_request", _pr_payload(INSTALL_A, title="No dedupe"))
    assert resp.status_code == 200
    assert _ref(w.p, ref).title == "No dedupe"


# ── §7.5, the installation events ───────────────────────────────────────────


def _row_a(p: Any) -> Any:
    (row,) = g.rows(p, "SELECT status, last_error, provider_meta FROM integration_connections "
                       "WHERE organization_id = CAST(:o AS uuid) AND provider = 'github'",
                    o=p.org_a)
    return row


async def test_the_installation_events(world) -> None:
    w = world
    inst = {"id": INSTALL_A, "account": {"login": "acme"}}
    assert (await _post(w, "installation", {"action": "suspend", "installation": inst}))\
        .status_code == 200
    assert (_row_a(w.p).status, _row_a(w.p).last_error) == ("needs_reconnect", "suspended")
    assert (await _post(w, "installation", {"action": "unsuspend", "installation": inst}))\
        .status_code == 200
    assert (_row_a(w.p).status, _row_a(w.p).last_error) == ("connected", None)
    resp = await _post(w, "installation_repositories", {
        "action": "added", "installation": inst, "repository_selection": "all",
        "repositories_added": [{"full_name": "acme/new"}]})
    assert resp.status_code == 200
    assert _row_a(w.p).provider_meta["repository_selection"] == "all"
    assert "repositories_added" not in json.dumps(_row_a(w.p).provider_meta)
    assert (await _post(w, "installation", {"action": "deleted", "installation": inst}))\
        .status_code == 200
    assert (_row_a(w.p).status, _row_a(w.p).last_error) == ("disconnected", "uninstalled")
    # After the uninstall the installation maps to no org.
    assert (await _post(w, "installation", {"action": "deleted", "installation": inst}))\
        .status_code == 202


# ── L1 to L6: the rules of the cases file ───────────────────────────────────

TASK_UUID = "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"


def _points(rule: dict, task: str, extra_tasks: list[str]) -> dict:
    """The body and the branch that point at *task* in the way the rule names."""
    url = f"https://app.metorite.com/projects?task={task}"
    return {
        "body": {"body": url, "head_ref": "main"},
        "staging_body": {"body": url.replace("app.metorite.com", "dev.metorite.com"),
                         "head_ref": "main"},
        "branch_uuid": {"body": "", "head_ref": f"agent/{task}"},
        "branch_short": {"body": "", "head_ref": f"feat/mt-{task[:8]}-x"},
        "six_in_body": {"body": " ".join(
            f"https://app.metorite.com/projects?task={t}" for t in [*extra_tasks, task]),
            "head_ref": "main"},
    }[rule["points"]]


@pytest.mark.parametrize("rule", CASES["rules"], ids=[r["name"] for r in CASES["rules"]])
async def test_the_autolink_rules(world, rule: dict) -> None:
    w = world
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE id::text LIKE '1a2b3c4d-%'"))
    org = w.p.org_b if rule["task"] == "other_org" else w.p.org_a
    personal = EMAIL_A if rule["task"] == "personal" else None
    task = g.seed_task(w.p, org, EMAIL_A, task_id=TASK_UUID,
                       archived=rule["task"] == "archived", personal_owner=personal)
    if rule["task"] == "prefix_twin":
        g.seed_task(w.p, w.p.org_a, EMAIL_A, task_id="1a2b3c4d-0000-4000-8000-000000000001")
    extra = [g.seed_task(w.p, w.p.org_a, EMAIL_A) for _ in range(5)] \
        if rule["points"] == "six_in_body" else []
    source = {
        "same_repo": (REPO, "NONE"),
        "fork_outsider": ("mallory/widgets", "NONE"),
        "fork_collaborator": ("dana/widgets", "COLLABORATOR"),
    }[rule["source"]]
    pointed = _points(rule, task, extra)
    resp = await _post(w, "pull_request", _pr_payload(
        INSTALL_A, action="opened", body=pointed["body"], head_ref=pointed["head_ref"],
        head_repo=source[0], association=source[1]))
    assert resp.status_code == 200, resp.text
    linked = _refs_of(w.p, task)
    if rule["links"]:
        assert [(r.ref, r.via, r.created_by) for r in linked] == [
            (f"{REPO}#12", "github", "system:github")]
        activity = g.scalar(w.p, "SELECT count(*) FROM pm_activities WHERE task_id = "
                                 "CAST(:t AS uuid) AND type = 'link' AND created_by = "
                                 "'system:github'", t=task)
        assert activity == 1
        # The link took the pull request's facts in the same event.
        assert g.scalar(w.p, "SELECT title FROM pm_task_refs WHERE task_id = "
                             "CAST(:t AS uuid)", t=task) == "Fix the login"
    else:
        assert linked == [], rule["name"]
    if rule["points"] == "six_in_body":
        assert all(len(_refs_of(w.p, t)) == 1 for t in extra), "the first five link"


async def test_an_edit_that_drops_the_url_keeps_the_link(world) -> None:
    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    url = f"https://app.metorite.com/projects?task={task}"
    await _post(w, "pull_request", _pr_payload(INSTALL_A, action="opened", body=url))
    await _post(w, "pull_request", _pr_payload(
        INSTALL_A, action="edited", body="no link now", updated_at="2026-10-18T11:00:00Z"))
    assert len(_refs_of(w.p, task)) == 1


async def test_item2_a_link_a_person_removed_stays_removed(world) -> None:
    """A person deletes a wrong auto-link. The next push does not bring it
    back. A person who adds it again by hand clears the tombstone."""
    from acb_auth import UserContext, UserRole, build_access
    from gateway.routes import projects as projects_package

    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    url = f"https://app.metorite.com/projects?task={task}"
    await _post(w, "pull_request", _pr_payload(INSTALL_A, action="opened", body=url))
    (ref_id,) = [str(r.id) for r in g.rows(
        w.p, "SELECT id FROM pm_task_refs WHERE task_id = CAST(:t AS uuid)", t=task)]
    member = UserContext(email=EMAIL_A, role=UserRole.EMPLOYEE, organization_id=w.p.org_a,
                         access=build_access(["feature:projects", "data:org:read"]))

    async def _person(method: str, path: str, body: Any = None) -> httpx.Response:
        return await g.call(w.p, member, [projects_package.router], method, path, body=body,
                            params={"refresh": "false"}, real_client=REAL_CLIENT)

    resp = await _person("DELETE", f"/projects/tasks/{task}/refs/{ref_id}")
    assert resp.status_code == 200, resp.text
    await _post(w, "pull_request", _pr_payload(
        INSTALL_A, action="synchronize", body=url, updated_at="2026-10-18T11:00:00Z"))
    await _post(w, "pull_request", _pr_payload(
        INSTALL_A, action="edited", body=url, updated_at="2026-10-18T12:00:00Z"))
    assert _refs_of(w.p, task) == [], "a push must not bring a removed link back"
    resp = await _person("POST", f"/projects/tasks/{task}/refs",
                         {"url": f"https://github.com/{REPO}/pull/12"})
    assert resp.status_code == 201, resp.text
    (again,) = _refs_of(w.p, task)
    assert again.via == "ui"
    resp = await _person("DELETE", f"/projects/tasks/{task}/refs/{resp.json()['id']}")
    assert resp.status_code == 200, resp.text
    # The newest row is the removal again, so the auto-link still skips it.
    await _post(w, "pull_request", _pr_payload(
        INSTALL_A, action="synchronize", body=url, updated_at="2026-10-18T13:00:00Z"))
    assert _refs_of(w.p, task) == []
    # The tombstone is for this task only: another task named in the body links.
    other = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    await _post(w, "pull_request", _pr_payload(
        INSTALL_A, action="edited", updated_at="2026-10-18T14:00:00Z",
        body=f"{url} https://app.metorite.com/projects?task={other}"))
    assert len(_refs_of(w.p, other)) == 1 and _refs_of(w.p, task) == []


async def test_item2_a_hand_add_after_a_removal_lets_the_auto_link_run(world) -> None:
    """The newest row wins: after a removal and a hand add, the pair is live,
    and an auto-link of it is a no-op on the row that is there."""
    w = world
    task = g.seed_task(w.p, w.p.org_a, EMAIL_A)
    found = type("F", (), {"provider": "github", "kind": "pull_request",
                           "ref": f"{REPO}#12"})()
    from gateway.db import tenant_session
    from gateway.routes.projects import github_autolink

    def _act(action: str, ago: str) -> None:
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_activities (organization_id, task_id, type, created_by, meta, "
                "body, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), 'link', :b, "
                "CAST(:m AS jsonb), 'seed', now() - CAST(:ago AS interval))"),
                {"o": w.p.org_a, "t": task, "b": EMAIL_A, "ago": ago, "m": json.dumps({
                    "ref_action": action, "provider": "github", "kind": "pull_request",
                    "ref": f"{REPO}#12"})})

    _act("added", "3 hours")
    _act("removed", "2 hours")
    async with tenant_engine_scope(g.app_dsn(w.p)), tenant_session(w.p.org_a) as db:
        assert await github_autolink.removed_by_a_person(db, task, found) is True
    _act("added", "1 hour")
    async with tenant_engine_scope(g.app_dsn(w.p)), tenant_session(w.p.org_a) as db:
        assert await github_autolink.removed_by_a_person(db, task, found) is False
