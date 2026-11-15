"""CIV-14b — images on a question, on a real database, through branch 0 (R8).

Spec: ``project-docs/specs/external_agent_access.md`` §12.9 ("Done when" and
"Fences") and §15 row CIV-14b.

**R8.** The real edge app takes a real token from the real store. The tool
signs each call, the gateway wrapper keeps the bytes of the signed form, and
branch 0 of the REAL gateway app admits it as the member. The routes run as a
NON-privileged role on a promoted catalog of their own, so FORCE RLS binds
every read and write. Organization A holds:

* MEMBER, with a connection of ``projects:read projects:write``;
* a team project with the tasks ``mine``, ``other``, ``flagless``,
  ``thought``, ``norun``, ``flipped`` and ``gone``, a PNG on ``other`` and a
  PDF on ``mine``;
* OWNER's personal tree, SEALED as D63 stamps it, with a stray grant to
  MEMBER and one task.

Organization B holds MEMBER_B, a connection of the same scopes, and a task
with a PNG.

``test_mcp_upload_image_limits.py`` and ``test_mcp_upload_image_types.py``
hold the edge rules with a fake gateway. The gate is
``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_upload_image_tenant_r8.py -v -rs

**The mutations (R7).**

* Drop the task filter of ``agent_runs._TASK_IMAGES_SQL``
  (``ta.task_id = CAST(:tid AS uuid)``), and
  ``test_r3_an_image_of_another_task_on_the_question_is_refused`` turns red:
  the question takes a file of another task.
* Fix round 1, item 8. Drop the ``_question_images`` call of
  ``tools._ask_on_task``, and ``test_r3_a_refused_question_opens_no_run``
  turns red: the run starts before the gateway refuses the question.
* Fix round 1, item 4. Keep the copy of ``agent_upload_body.py`` at the
  first part, and ``test_r8_a_signed_form_in_many_parts_lands`` turns red.
* Fix round 2, item B. Drop the release of ``tools._ask_on_task``, and
  ``test_r9_a_question_that_the_gateway_refuses_leaves_no_open_run`` turns
  red: the run that the call started stays ``in_progress``.
* Fix round 3, item 3. Drop the ``TEXT_KEY_RUN_ENDED`` step of
  ``tools._ask_on_task``, and the same test turns red: a retry with the key
  of the released run reads ``TEXT_RUN_CONFLICT``.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import agent_signing, agent_tokens
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from agent_edge import gateway_client, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder
from tests.unit.test_mcp_edge_attachment import JPEG, PNG, TINY_PNG

_DB_GATE = w._DB_GATE
# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_mcpciv14b"
_APP_PW = "mcpciv14b_scratch_pw"
MEMBER = w.MEMBER
MEMBER_B = "member-b@mcpwrites.test"
OWNER = w.OWNER
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>steal()</script></svg>"
PDF = b"%PDF-1.7\n" + b"\x00" * 64

redis_fake = tw.redis_fake


def _attach(seeder: Any, folder: str, task: str, name: str, mime: str, data: bytes) -> str:
    """A file on disk, and its two rows: ``attachments`` and the join."""
    path = os.path.join(folder, f"{uuid.uuid4()}.bin")
    with open(path, "wb") as fh:
        fh.write(data)
    aid = str(seeder.one(
        "INSERT INTO attachments (user_id, name, mime, size_bytes, path, organization_id)"
        " VALUES (:u, :n, :m, :s, :p, CAST(:o AS uuid)) RETURNING id",
        u=OWNER, n=name, m=mime, s=len(data), p=path, o=seeder.org))
    seeder.one(
        "INSERT INTO pm_task_attachments (task_id, attachment_id, added_by, organization_id)"
        " VALUES (CAST(:t AS uuid), CAST(:a AS uuid), :b, CAST(:o AS uuid)) RETURNING task_id",
        t=task, a=aid, b=OWNER, o=seeder.org)
    return aid


def _connection_b(seeder: Any) -> tuple[str, str]:
    """A live connection of MEMBER_B in organization B, and its token."""
    cid = str(seeder.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
        " now() + interval '1 day') RETURNING id",
        o=seeder.org, m=MEMBER_B, origin=w.ORIGIN,
        scopes=["projects:read", "projects:write"], pins=[]))
    raw = agent_tokens.new_token()
    seeder.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
               " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
               " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
               " 'https://client.example/meta.json', :r, :s) RETURNING id",
               o=seeder.org, c=cid, h=agent_tokens.hash_token(raw), r=w.RESOURCE,
               s="projects:read projects:write")
    return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants, the files of the docstring."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpciv14b"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "mcpciv14b"), (org_b, "mcpciv14b-other")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    folder = tempfile.mkdtemp(prefix="mcpciv14b-")
    store = tempfile.mkdtemp(prefix="mcpciv14b-store-")
    a = w._Seeder(eng, org)
    a.number = 300_000
    a.member(MEMBER, ["feature:projects"])
    team = a.project("Team", in_space=True)
    todo = a.status(team, "To do", "todo", 0)
    t = {key: a.task(team, todo, key)
         for key in ("mine", "other", "flagless", "thought", "norun", "flipped", "gone")}
    f = {
        "other": _attach(a, folder, t["other"], "other.png", "image/png", TINY_PNG),
        "pdf": _attach(a, folder, t["mine"], "spec.pdf", "application/pdf", PDF),
    }
    sealed = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, personal_owner) VALUES ('Owner root', 'active',"
        " 'manual', :a, CAST(:o AS uuid), 'Asia/Kolkata', true, :a) RETURNING id",
        a=OWNER, o=org))
    a.one("INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id)"
          " VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid)) RETURNING project_id",
          p=sealed, s=MEMBER, a=OWNER, o=org)
    t["sealed"] = a.task(sealed, a.status(sealed, "To do", "todo", 0), "sealed")
    a.one("UPDATE pm_projects SET sealed_at = now() WHERE id = CAST(:p AS uuid)"
          " RETURNING id", p=sealed)

    b = w._Seeder(eng, org_b)
    b.number = 400_000
    b.member(MEMBER_B, ["feature:projects"])
    theirs = b.project("Theirs", in_space=True)
    b.one("INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id)"
          " VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid)) RETURNING project_id",
          p=theirs, s=MEMBER_B, a=OWNER, o=org_b)
    t["theirs"] = b.task(theirs, b.status(theirs, "To do", "todo", 0), "theirs")
    f["theirs"] = _attach(b, folder, t["theirs"], "theirs.png", "image/png", TINY_PNG)

    world_ = SimpleNamespace(admin=eng, org=org, org_b=org_b, seeder=a, c={}, tok={}, t=t,
                             f=f, store=store)
    world_.c["write"], world_.tok["write"] = a.connection(
        scopes=("projects:read", "projects:write"))
    world_.c["b"], world_.tok["b"] = _connection_b(b)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(store, ignore_errors=True)


@pytest.fixture
def live_app(monkeypatch, world, redis_fake):
    """The real gateway app at ``agent_edge_gateway_url`` (EA3-20)."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_runs_enabled", True), ("agent_access_orgs", "*"),
            ("mcp_image_upload_orgs", "*"), ("agent_questions_orgs", "*"),
            ("agent_edge_signing_key", tw.KEY), ("agent_edge_signing_key_previous", ""),
            ("agent_issuer", w.ISSUER), ("agent_resource", w.RESOURCE),
            ("agent_edge_gateway_url", "http://gateway.test")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", w.INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "mcpwrites.test")
    monkeypatch.setenv("GTD_ATTACHMENTS_DIR", world.store)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    world.redis = redis_fake
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


@pytest.fixture
async def live(live_app):
    """:func:`live_app`, after one warm-up GET (see ``test_mcp_edge_task_writes``)."""
    if not getattr(live_app, "warmed", False):
        r = await tw._send(live_app, "GET", "/projects/tree")
        assert r.status_code == 200, r.text
        live_app.warmed = True
    return live_app


def _key() -> str:
    return f"civ14b-{uuid.uuid4().hex[:16]}"


async def _upload(world, task: str, data: bytes, *, name: str = "capture.png",
                  who: str = "write") -> dict[str, Any]:
    import base64

    return await tw._mcp(world, who, "upload_task_image", {
        "task_id": world.t[task], "filename": name, "idempotency_key": _key(),
        "content_base64": base64.b64encode(data).decode("ascii")})


async def _ask(world, task: str, ids: list[str], *, who: str = "write",
               key: str | None = None) -> dict[str, Any]:
    return await tw._mcp(world, who, "ask_on_task", {
        "task_id": world.t[task], "question": "Approve this layout?",
        "options": [{"id": "yes", "label": "Approve"}, {"id": "no", "label": "Change it"}],
        "attachment_ids": ids, "idempotency_key": key or _key()})


def _files_of(world, task: str) -> list[Any]:
    with world.admin.begin() as c:
        return c.execute(text(
            "SELECT a.id, a.name, a.mime, a.size_bytes, a.path, a.organization_id"
            "  FROM pm_task_attachments ta JOIN attachments a ON a.id = ta.attachment_id"
            " WHERE ta.task_id = CAST(:t AS uuid) ORDER BY ta.created_at"),
            {"t": world.t[task]}).fetchall()


def _runs_of(world, task: str) -> int:
    with world.admin.begin() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"),
            {"t": world.t[task]}).scalar())


def _run_statuses(world, task: str) -> list[str]:
    with world.admin.begin() as c:
        return [str(s) for s in c.execute(text(
            "SELECT status FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"
            " ORDER BY created_at"), {"t": world.t[task]}).scalars()]


def _questions_of(world, task: str) -> list[Any]:
    with world.admin.begin() as c:
        return c.execute(text(
            "SELECT id, content FROM pm_agent_run_activities"
            " WHERE task_id = CAST(:t AS uuid) AND type = 'elicitation' ORDER BY seq"),
            {"t": world.t[task]}).fetchall()


async def _person(world, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request as MEMBER through the internal bearer: the browser's path."""
    from gateway.main import app as gateway_app

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers={
                "Authorization": f"Bearer {w.INTERNAL}", "X-User-Email": MEMBER}, **kwargs)
    return await tw._in_scope(world, go)


def _not_found(result: dict[str, Any]) -> bool:
    return (result["isError"] is True
            and result["content"] == [{"type": "text", "text": tools.TEXT_TASK_NOT_FOUND}])


class TestAnImageOnAQuestion:
    """Done when: an agent uploads a capture, asks with its id, and a person
    sees it through the cookie serve route."""

    async def test_r1_an_upload_lands_with_the_type_of_its_bytes(self, live):
        before = len(_files_of(live, "mine"))
        result = await _upload(live, "mine", PNG, name="layout one.png")
        assert result["isError"] is False, result
        body = result["structuredContent"]
        assert body["created"] is True and body["attachment"]["size"] == len(PNG)
        rows = _files_of(live, "mine")
        assert len(rows) == before + 1
        row = rows[-1]
        assert (str(row.id), row.name, row.mime, row.size_bytes, str(row.organization_id)) == (
            body["attachment"]["attachment_id"], "layout one.png", "image/png", len(PNG),
            live.org)
        with open(row.path, "rb") as fh:
            assert fh.read() == PNG

    async def test_r2_the_question_holds_the_image_and_a_person_reads_it(self, live):
        up = await _upload(live, "mine", TINY_PNG, name="shot.png")
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        asked = await _ask(live, "mine", [aid])
        assert asked["isError"] is False, asked
        run_id = asked["structuredContent"]["run"]["id"]
        content = _questions_of(live, "mine")[-1].content
        assert content["attachments"] == [
            {"attachment_id": aid, "name": "shot.png", "mime": "image/png",
             "size": len(TINY_PNG)}]
        # The run read of the panel carries it, and so does the Questions list.
        run = await _person(live, "GET", f"/projects/agent-runs/{run_id}")
        assert run.status_code == 200, run.text
        (shown,) = [a for a in run.json()["activities"] if a["type"] == "elicitation"]
        # The row stores no url. Each read adds the cookie serve route.
        assert "url" not in content["attachments"][0]
        served = f"/api/projects/attachments/{aid}/shot.png"
        assert shown["content"]["attachments"][0] == {**content["attachments"][0],
                                                      "url": served}
        listed = await _person(live, "GET", "/projects/agent-questions")
        assert listed.status_code == 200, listed.text
        (row,) = [r for r in listed.json()["rows"] if r["run_id"] == run_id]
        assert row["question"]["attachments"] == [{**content["attachments"][0], "url": served}]
        # The cookie serve route of the card serves the bytes inline.
        served = await _person(live, "GET", f"/projects/attachments/{aid}/shot.png")
        assert served.status_code == 200 and served.content == TINY_PNG
        assert served.headers["content-type"] == "image/png"
        assert served.headers["content-disposition"].startswith("inline")

    async def test_r2_a_thought_takes_images_too(self, live):
        up = await _upload(live, "thought", JPEG, name="before.jpg")
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        asked = await _ask(live, "thought", [aid])
        assert asked["isError"] is False, asked
        run_id = asked["structuredContent"]["run"]["id"]
        activities = f"/projects/agent-runs/{run_id}/activities"
        # Fix round 1, item 5: a prompt takes no images, and the gateway
        # refuses them with its code. So does a signed response, which the
        # edge refuses before it sends one.
        refused = await _person(live, "POST", activities, json={
            "type": "prompt", "idempotency_key": _key(), "selected": ["yes"],
            "attachment_ids": [aid]})
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["error"] == "attachments_not_on_type"
        signed = await tw._send(live, "POST", activities, body={
            "type": "response", "idempotency_key": _key(), "body": "Done.",
            "attachment_ids": [aid]})
        assert signed.status_code == 422, signed.text
        assert signed.json()["detail"]["error"] == "attachments_not_on_type"
        answer = await _person(live, "POST", activities,
                               json={"type": "prompt", "idempotency_key": _key(),
                                     "selected": ["yes"]})
        assert answer.status_code in (200, 201), answer.text
        posted = await tw._mcp(live, "write", "post_agent_activity", {
            "run_id": run_id, "type": "thought", "body": "Here is the new state.",
            "attachment_ids": [aid, aid], "idempotency_key": _key()})
        assert posted["isError"] is False, posted
        with live.admin.begin() as c:
            content = c.execute(text(
                "SELECT content FROM pm_agent_run_activities WHERE run_id = CAST(:r AS uuid)"
                " AND type = 'thought' ORDER BY seq DESC LIMIT 1"), {"r": run_id}).scalar()
        assert [i["attachment_id"] for i in content["attachments"]] == [aid]

    async def test_r3_an_image_of_another_task_on_the_question_is_refused(self, live):
        before = len(_questions_of(live, "mine"))
        result = await _ask(live, "mine", [live.f["other"]])
        assert result["isError"] is True
        assert result["content"][0]["text"] == tools.TEXT_ATTACHMENT_NOT_ON_TASK
        assert len(_questions_of(live, "mine")) == before

    async def test_r3_a_file_that_is_not_an_image_is_refused(self, live):
        result = await _ask(live, "mine", [live.f["pdf"]])
        assert result["content"][0]["text"] == tools.TEXT_ATTACHMENT_NOT_IMAGE

    async def test_r3_a_refused_question_opens_no_run(self, live):
        """Fix round 1, item 8. ``norun`` has no run. A question with an image
        of another task is refused before the start, so the task still has
        no run. A good question then starts one."""
        assert _runs_of(live, "norun") == 0
        result = await _ask(live, "norun", [live.f["other"]])
        assert result["content"][0]["text"] == tools.TEXT_ATTACHMENT_NOT_ON_TASK
        assert _runs_of(live, "norun") == 0
        up = await _upload(live, "norun", TINY_PNG)
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        asked = await _ask(live, "norun", [aid])
        assert asked["isError"] is False, asked
        assert _runs_of(live, "norun") == 1

    @pytest.mark.parametrize("task,change,text_", [
        ("flipped", "flag", tools.TEXT_IMAGES_OFF),
        ("gone", "remove", tools.TEXT_ATTACHMENT_NOT_ON_TASK),
    ], ids=["flag_off_at_the_gateway", "image_removed"])
    async def test_r9_a_question_that_the_gateway_refuses_leaves_no_open_run(
            self, live, monkeypatch, task, change, text_):
        """Fix round 2, item B, the probe of the verifier on a real gateway.
        The checks of the edge pass. Then the gateway reads the flag off, or
        a member removes the image before the write. The call releases the
        run that it started. Mutation: drop the release of
        ``tools._ask_on_task``, and the run stays ``in_progress``."""
        up = await _upload(live, task, TINY_PNG)
        aid = up["structuredContent"]["attachment"]["attachment_id"]
        checked = tools._question_images

        async def then_change(ctx: Any, task_id: str, ids: list[str]) -> None:
            await checked(ctx, task_id, ids)
            if change == "flag":
                monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", "")
            else:
                with live.admin.begin() as c:
                    c.execute(text("DELETE FROM pm_task_attachments"
                                   " WHERE attachment_id = CAST(:a AS uuid)"), {"a": aid})

        monkeypatch.setattr(tools, "_question_images", then_change)
        assert _runs_of(live, task) == 0
        key = _key()
        result = await _ask(live, task, [aid], key=key)
        assert result["content"][0]["text"] == text_
        assert _run_statuses(live, task) == ["released"]
        assert _questions_of(live, task) == []
        # Fix round 3, item 3: a retry with the same key replays the released
        # run. The answer says to send a new key, and no run opens. Mutation:
        # drop the TEXT_KEY_RUN_ENDED step, and this reads TEXT_RUN_CONFLICT.
        retry = await tw._mcp(live, "write", "ask_on_task", {
            "task_id": live.t[task], "question": "Approve this layout?",
            "idempotency_key": key})
        assert retry["content"][0]["text"] == tools.TEXT_KEY_RUN_ENDED
        assert _run_statuses(live, task) == ["released"]
        assert _questions_of(live, task) == []


class TestTheReachOfAnUpload:
    """A hidden task and another organization: no file lands, and none is read."""

    async def test_r4_a_sealed_task_of_another_member_is_not_found(self, live):
        result = await _upload(live, "sealed", TINY_PNG)
        assert _not_found(result), result
        assert _files_of(live, "sealed") == []

    async def test_r5_org_b_can_neither_attach_to_nor_read_a_task_of_org_a(self, live):
        before = len(_files_of(live, "mine"))
        result = await _upload(live, "mine", TINY_PNG, who="b")
        assert _not_found(result), result
        assert len(_files_of(live, "mine")) == before
        read = await tw._mcp(live, "b", "get_attachment",
                             {"task_id": live.t["other"], "attachment_id": live.f["other"]})
        assert read["isError"] is True
        assert read["content"][0]["text"] == tools.TEXT_ATTACHMENT_NOT_FOUND

    async def test_r5_an_id_of_another_organization_is_not_on_the_task(self, live):
        mine = await _ask(live, "mine", [live.f["theirs"]])
        assert mine["content"][0]["text"] == tools.TEXT_ATTACHMENT_NOT_ON_TASK
        theirs = await _ask(live, "theirs", [live.f["other"]], who="b")
        assert theirs["content"][0]["text"] == tools.TEXT_ATTACHMENT_NOT_ON_TASK

    async def test_r5_org_b_attaches_to_its_own_task(self, live):
        result = await _upload(live, "theirs", TINY_PNG, who="b")
        assert result["isError"] is False, result
        (row,) = [r for r in _files_of(live, "theirs")
                  if str(r.id) == result["structuredContent"]["attachment"]["attachment_id"]]
        assert str(row.organization_id) == live.org_b


class TestTheFlag:
    """Scope item 5: dark behind ``mcp_image_upload_orgs``, read by the gateway."""

    async def test_r6_with_the_flag_off_nothing_lands(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", "")
        before = len(_files_of(live, "flagless"))
        result = await _upload(live, "flagless", TINY_PNG)
        assert result["content"][0]["text"] == tools.TEXT_IMAGES_OFF
        assert len(_files_of(live, "flagless")) == before
        asked = await _ask(live, "flagless", [live.f["other"]])
        assert asked["content"][0]["text"] == tools.TEXT_IMAGES_OFF
        assert _runs_of(live, "flagless") == 0

    async def test_r6_the_flag_names_one_organization(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", live.org)
        assert (await _upload(live, "theirs", TINY_PNG, who="b"))["content"][0]["text"] == (
            tools.TEXT_IMAGES_OFF)
        assert (await _upload(live, "flagless", TINY_PNG))["isError"] is False

    async def test_r6_a_person_is_never_refused_by_the_flag(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "mcp_image_upload_orgs", "")
        r = await _person(live, "POST", f"/projects/tasks/{live.t['flagless']}/attachments",
                          files={"file": ("spec.pdf", PDF, "application/pdf")})
        assert r.status_code == 201, r.text
        assert r.json()["mime"] == "application/pdf"


class TestTheRouteForAPerson:
    """Scope item 4: the route checks the bytes and refuses an SVG for every caller."""

    async def test_r7_a_person_cannot_store_an_svg(self, live):
        before = len(_files_of(live, "mine"))
        for name, mime, data in (("logo.svg", "image/svg+xml", SVG),
                                 ("logo.png", "image/png", SVG)):
            r = await _person(live, "POST", f"/projects/tasks/{live.t['mine']}/attachments",
                              files={"file": (name, data, mime)})
            assert r.status_code == 415, r.text
        assert len(_files_of(live, "mine")) == before

    async def test_r7_a_jpeg_labelled_png_is_stored_as_jpeg(self, live):
        r = await _person(live, "POST", f"/projects/tasks/{live.t['mine']}/attachments",
                          files={"file": ("photo.png", JPEG, "image/png")})
        assert r.status_code == 201, r.text
        assert r.json()["mime"] == "image/jpeg"


class TestTheSignedForm:
    """The bytes that branch 0 checks are the bytes that the route stores."""

    async def test_r8_a_form_that_differs_from_the_signed_bytes_is_refused(self, live):
        from gateway.main import app as gateway_app

        path = f"/projects/tasks/{live.t['mine']}/attachments"
        signed, sent = (gateway_client._form("a.png", data, "image/png")
                        for data in (TINY_PNG, TINY_PNG + b"\x00"))
        headers = agent_signing.signed_headers(
            tw.KEY, organization_id=live.org, connection_id=live.c["write"],
            actor_via=tw.R8_VIA, method="POST", path=path, query="", body=signed[0])
        before = len(_files_of(live, "mine"))

        async def go(body: bytes, kind: str) -> httpx.Response:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                         base_url="http://gateway.test") as client:
                return await client.post(path, content=body,
                                         headers={**headers, "Content-Type": kind})
        forged = await tw._in_scope(live, lambda: go(sent[0], sent[1]))
        assert forged.status_code == 401, forged.text
        assert len(_files_of(live, "mine")) == before

    async def _parts(self, live, body: bytes, kind: str,
                     headers: dict[str, str]) -> httpx.Response:
        """One signed POST whose body goes as many ASGI messages."""
        from gateway.main import app as gateway_app

        path = f"/projects/tasks/{live.t['mine']}/attachments"

        async def chunks():
            for start in range(0, len(body), 4096):
                yield body[start:start + 4096]

        async def go() -> httpx.Response:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                         base_url="http://gateway.test") as client:
                return await client.post(path, content=chunks(),
                                         headers={**headers, "Content-Type": kind})
        return await tw._in_scope(live, go)

    def _signed(self, live, body: bytes) -> dict[str, str]:
        return agent_signing.signed_headers(
            tw.KEY, organization_id=live.org, connection_id=live.c["write"],
            actor_via=tw.R8_VIA, method="POST",
            path=f"/projects/tasks/{live.t['mine']}/attachments", query="", body=body)

    async def test_r8_a_signed_form_in_many_parts_lands(self, live):
        """Fix round 1, item 4: the wrapper keeps every part, so branch 0
        checks the signature over the whole body."""
        body, kind = gateway_client._form("parts.png", PNG, "image/png")
        assert len(body) > 4 * 4096
        before = len(_files_of(live, "mine"))
        r = await self._parts(live, body, kind, self._signed(live, body))
        assert r.status_code == 201, r.text
        rows = _files_of(live, "mine")
        assert len(rows) == before + 1
        with open(rows[-1].path, "rb") as fh:
            assert fh.read() == PNG

    @pytest.mark.parametrize("signed_part", ["whole_body", "first_part"])
    async def test_r8_a_later_part_changed_after_signing_is_refused(self, live, signed_part):
        """A part after the first changes once the edge signed. Branch 0
        refuses it with the signature of the whole first body, and with a
        signature of the first part only (the mutation above passes that)."""
        body, kind = gateway_client._form("parts.png", PNG, "image/png")
        headers = self._signed(live, body if signed_part == "whole_body" else body[:4096])
        at = 3 * 4096 + 17
        changed = body[:at] + bytes([body[at] ^ 0x01]) + body[at + 1:]
        before = len(_files_of(live, "mine"))
        r = await self._parts(live, changed, kind, headers)
        assert r.status_code == 401, r.text
        assert len(_files_of(live, "mine")) == before

    async def test_r8_parallel_signed_uploads_each_verify(self, live):
        async def go() -> list[dict[str, Any]]:
            import base64

            calls = [tw._mcp_unscoped(live, "write", "upload_task_image", {
                "task_id": live.t["mine"], "filename": f"p{i}.png", "idempotency_key": _key(),
                "content_base64": base64.b64encode(TINY_PNG + bytes([i])).decode("ascii")})
                for i in range(3)]
            return list(await asyncio.gather(*calls))
        results = await tw._in_scope(live, go)
        assert all(r["isError"] is False for r in results), json.dumps(results)[:500]
