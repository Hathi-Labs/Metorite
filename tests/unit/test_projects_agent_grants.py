"""The member's grants decide what the Projects chat may change (2026-10-07).

Spec: ``project-docs/specs/projects_agent_parity.md`` §16, the owner's
directive of 2026-10-07:

* what the chat may change is decided by the MEMBER'S own grants, checked by
  the SERVER, exactly as the app's screens are;
* the approval card is the member's consent;
* the assistant never decides a permission itself, and never claims one it
  did not check.

The incident: an org owner asked for new tags. The chat refused before any
write, and named ``projects:settings:write`` and "an organization admin".
The root cause was the persona (``assistantPersona.ts``), which told EVERY
member "may NOT edit … say who can: an organization admin". It read
``projects:settings:write`` from ``access.capabilities``, and that list never
carries the slug (``acb_auth.permissions.CAPABILITIES``).

R7 fences named here, each with the mutation that turns it red:

* ``grants-attempt`` — an owner's "new tag" shows a card and posts the route.
  Mutation: a pre-card refusal in ``create_tag``.
* ``grants-quote`` — the receipt of a refused write quotes the server.
  Mutation: drop the ``Gateway said`` line from ``refusal_text``.
* ``grants-precheck`` — the one check before a card is the server's answer
  (``may_edit``, ``edit_refusal``). Mutation: ``status_edit_refusal``
  returns ``""`` (the local "yes"), or it says no when the flag is absent.
* ``grants-one-predicate`` — the status-set read and the status write ask
  one predicate, ``core.can_manage_settings``. Mutation: ``may_edit`` gets
  its own expression again.
* ``grants-no-claim`` — no instruction, refusal or persona string claims a
  permission without a server check. Mutation: put the persona's old
  sentence back, or add "an organization admin can" to the instructions.
  The allowlist only shrinks (:data:`CLAIM_CEILING`).
* ``grants-r8`` — on a private ladder database, the real routes: an owner's
  tag is created, a member's org-wide tag is refused with the server's words
  in the receipt, and the status pre-check agrees with the status write for
  both people.

Run (real Postgres, a PRIVATE ladder database, see the memory note on R8 in
parallel)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private> \\
        uv run pytest tests/unit/test_projects_agent_grants.py -v -rs
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

# `Request` at module level, not in the function: this file uses postponed
# annotations, and FastAPI resolves a dependency's annotation from the
# module's globals.
from fastapi import Request
from skill_projects import writes as W
from skill_projects.refusals import refusals_as_text

from tests.unit._projects_agent_fakes import (
    REPO_ROOT,
    FakeResponse,
    approve,
    fake_gateway,
    writes,
)

PROJECT = "0f8fad5b-d9cb-469f-a165-70867728950e"
TAG = "1f8fad5b-d9cb-469f-a165-70867728950e"
LANE = "2f8fad5b-d9cb-469f-a165-70867728950e"
DONE = "3f8fad5b-d9cb-469f-a165-70867728950e"
OWNER = "owner@grants.test"

INSTRUCTIONS = REPO_ROOT / "apps" / "agents" / "agent-projects" / "instructions.md"
REFUSALS = REPO_ROOT / "apps" / "skills" / "skill-projects" / "skill_projects" / "refusals.py"
PERSONA = (
    REPO_ROOT
    / "workbench"
    / "control_plane"
    / "src"
    / "app"
    / "projects"
    / "lib"
    / "assistantPersona.ts"
)
RAIL = (
    REPO_ROOT
    / "workbench"
    / "control_plane"
    / "src"
    / "app"
    / "projects"
    / "components"
    / "AssistantRail.tsx"
)

SETTINGS_WORDS = (
    "Changing a project's settings needs the 'projects:settings:write' permission. "
    "Ask an organization admin to grant it to your role."
)


def _board(status_set: dict[str, Any] | None = None, refuse: dict | None = None):
    """A gateway with one space, one lane and no tags. *refuse* maps a
    ``(method, path)`` to ``(status, detail)``."""
    table = refuse or {}

    def answer(call: dict) -> Any:
        key = (call["method"], call["path"])
        if key in table:
            status, detail = table[key]
            return FakeResponse({"detail": detail}, status)
        path, method = call["path"], call["method"]
        if path == f"/projects/nodes/{PROJECT}" and method == "GET":
            return {"id": PROJECT, "name": "Metorite", "parent_project_id": None}
        if path.endswith("/status-set") and method == "GET":
            return (
                status_set
                if status_set is not None
                else {
                    "owns": True,
                    "owner_name": "Metorite",
                    "may_edit": True,
                    "edit_refusal": "",
                }
            )
        if path.endswith("/statuses") and method == "GET":
            return {
                "rows": [
                    {"id": LANE, "name": "To do", "category": "todo"},
                    {"id": DONE, "name": "Done", "category": "done"},
                ],
                "counts": {},
            }
        if path.endswith("/tags") and method == "GET":
            return {"rows": []}
        if method == "POST" and path.endswith("/tags"):
            return {"id": TAG, "project_id": PROJECT, **(call["json"] or {})}
        if method == "POST" and path.endswith("/statuses"):
            return {"id": LANE, **(call["json"] or {})}
        if method == "PATCH":
            return {"id": LANE, **(call["json"] or {})}
        return {"rows": []}

    return answer


def _sent(calls: list[dict], method: str, suffix: str) -> list[dict]:
    return [c for c in writes(calls) if c["method"] == method and c["path"].endswith(suffix)]


# ── grants-attempt: the assistant attempts, the server decides ──────────────


async def test_an_owner_asks_for_a_new_tag_and_the_card_posts_the_route(monkeypatch) -> None:
    """The incident, the right way: a card, the approve, the POST, a receipt."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _board(), user=OWNER)
    out = await skill_projects.create_tag(PROJECT, "urgent")
    assert len(asked) == 1, "the member saw no card"
    assert asked[0]["title"] == "Add this tag?"
    posted = _sent(calls, "POST", f"/projects/nodes/{PROJECT}/tags")
    assert [c["json"] for c in posted] == [{"name": "urgent"}]
    assert out.startswith("Added tag «urgent»"), out


async def test_no_tag_tool_asks_a_permission_before_its_card(monkeypatch) -> None:
    """Only a read of the server may come before the card, and no tag tool
    reads a permission. A tag write is decided by its route."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _board(), user=OWNER)
    await skill_projects.create_tag(PROJECT, "urgent")
    read = {c["path"] for c in calls if c["method"] == "GET"}
    assert not any(p.endswith(("/status-set", "/me", "/access")) for p in read), read


# ── grants-quote: a refusal on approve is the server's words ────────────────


async def test_a_refused_tag_write_quotes_the_server_in_the_receipt(monkeypatch) -> None:
    words = (
        "An organization-wide entry applies to every project in the "
        "organization, so it needs organization settings permission."
    )
    asked = approve(monkeypatch)
    calls = fake_gateway(
        monkeypatch,
        _board(refuse={("POST", f"/projects/nodes/{PROJECT}/tags"): (403, words)}),
        user="member@grants.test",
    )
    out = await refusals_as_text(skill_projects.create_tag)(PROJECT, "urgent", org_wide=True)
    assert len(asked) == 1, "the card comes first; the server decides on approve"
    assert _sent(calls, "POST", "/tags"), "the write was never attempted"
    assert out.startswith("Refused: Not permitted (403).")
    assert f"Gateway said: «{words}»" in out
    assert "Name no role or permission that the gateway did not name" in out


async def test_the_dark_flag_answer_is_quoted_too(monkeypatch) -> None:
    """``PROJECTS_ORG_VOCABULARIES`` off: the member reads the server's words,
    not a guess about who may do it."""
    words = "Organization-wide vocabularies are not enabled here."
    approve(monkeypatch)
    fake_gateway(
        monkeypatch,
        _board(refuse={("POST", f"/projects/nodes/{PROJECT}/tags"): (403, words)}),
        user=OWNER,
    )
    out = await refusals_as_text(skill_projects.create_tag)(PROJECT, "urgent", org_wide=True)
    assert f"Gateway said: «{words}»" in out


# ── grants-precheck: the one check before a card is the server's answer ─────


REFUSING_SET = {
    "owns": True,
    "owner_name": "Metorite",
    "may_edit": False,
    "edit_refusal": SETTINGS_WORDS,
}


@pytest.mark.parametrize(
    "call",
    [
        lambda: skill_projects.create_status(PROJECT, "Blocked"),
        lambda: skill_projects.update_status(PROJECT, "to do", name="Ready"),
        lambda: skill_projects.delete_status(PROJECT, "to do"),
        lambda: skill_projects.set_status_set(PROJECT, "own"),
    ],
    ids=["create_status", "update_status", "delete_status", "set_status_set"],
)
async def test_the_server_says_no_and_no_card_is_shown(monkeypatch, call) -> None:
    """``may_edit: false`` from the server stops the tool, with its words.
    THE MUTATION: make ``status_edit_refusal`` return ``""`` (a local yes),
    and the card shows and this fails."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _board(status_set=REFUSING_SET))
    out = await call()
    assert asked == [] and writes(calls) == [], out
    assert out.startswith("Refused: The server says that this member may not edit")
    assert f"Gateway said: «{SETTINGS_WORDS}»" in out


async def test_the_server_says_yes_and_the_card_is_shown(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _board())
    await skill_projects.create_status(PROJECT, "Blocked")
    assert len(asked) == 1 and _sent(calls, "POST", "/statuses")


async def test_no_answer_from_the_server_is_not_a_no(monkeypatch) -> None:
    """A read without ``may_edit`` decides nothing, so the card shows and
    the write decides. The tool never infers a "no" from a missing flag."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _board(status_set={"owns": True, "owner_name": "Metorite"}))
    await skill_projects.create_status(PROJECT, "Blocked")
    assert len(asked) == 1 and _sent(calls, "POST", "/statuses")


def test_the_precheck_reads_only_the_servers_two_fields() -> None:
    """``status_edit_refusal`` decides from ``may_edit`` alone, and quotes
    ``edit_refusal``. Owner, role and place do not change its answer."""
    assert W.status_edit_refusal({"may_edit": True, "owns": False}, "X") == ""
    assert W.status_edit_refusal({}, "X") == ""
    no = W.status_edit_refusal({"may_edit": False, "owns": True, "owner_name": "X"}, "X")
    assert no.startswith("Refused:") and "Gateway said" not in no


async def test_the_vocabulary_read_quotes_the_server_and_claims_nothing_else(monkeypatch) -> None:
    fake_gateway(monkeypatch, _board(status_set=REFUSING_SET))
    out = await skill_projects.vocabulary(PROJECT)
    assert "Status set owned by this project · the server says you may not edit it" in out
    assert f"Gateway said: «{SETTINGS_WORDS}»" in out
    assert "Types, tags and fields: the server checks each change" in out
    fake_gateway(monkeypatch, _board(status_set={"owns": True, "owner_name": "Metorite"}))
    out = await skill_projects.vocabulary(PROJECT)
    assert "may edit" not in out and "may not" not in out, out


# ── grants-one-predicate: the read before the card is the write's own check ─


def test_the_status_set_read_and_the_status_write_ask_one_predicate() -> None:
    """Source-level, because the read needs a database (R8 below runs it).
    THE MUTATION: give ``may_edit`` its own ``has_permission`` again."""
    import inspect

    from gateway.routes.projects import admin, core

    read = inspect.getsource(admin.describe_status_set)
    assert "can_manage_settings(user)" in read
    assert "has_permission" not in read
    write = inspect.getsource(core.assert_can_manage_settings)
    assert "can_manage_settings(user)" in write and "SETTINGS_REFUSAL" in write
    assert core.SETTINGS_REFUSAL == SETTINGS_WORDS


def test_the_predicate_answers_from_the_members_grants() -> None:
    from fastapi import HTTPException
    from gateway.routes.projects import core

    from tests.unit._projects_fakes import member_user, projects_user

    owner = projects_user(OWNER, features="*")
    manager = projects_user("m@grants.test", features="projects:settings:write")
    member = member_user("x@grants.test")
    assert core.can_manage_settings(owner) and core.can_manage_settings(manager)
    assert not core.can_manage_settings(member) and not core.can_manage_settings(None)
    with pytest.raises(HTTPException) as err:
        core.assert_can_manage_settings(member)
    assert err.value.status_code == 403 and err.value.detail == SETTINGS_WORDS


# ── grants-no-claim: no string claims a permission the server did not check ─

#: Phrases that claim a permission or name who holds one.
CLAIM = re.compile(
    r"admin can|you cannot|can't (?:create|add|edit|change)|doesn'?t grant|does not grant"
    r"|organi[sz]ation admin|only an admin|may not edit|(?-i:may NOT)|settings:write"
    r"|canManageSettings|settings permission",
    re.IGNORECASE,
)

#: Each phrase that stays, with the server check that backs it. ⚠️ THIS LIST
#: ONLY SHRINKS. Remove an entry when its sentence goes; never add one. A new
#: claim is a server check the tool quotes, not a sentence here.
ALLOWED_CLAIMS: dict[str, str] = {
    # The HR tier: the route hides the hours, skills and per-person figures
    # without `admin:members:read`, and the read says hidden (reads.py).
    "an admin can see capacity": "server-hidden: analytics_capacity.py HR tier",
    "an admin can see fit": "server-hidden: candidates.py HR tier",
    "an admin can see them": "server-hidden: rebalance, conflicts, dataset HR tier",
    "an admin can see it": "server-hidden: analytics_dataset.py HR tier",
    # A capability of the run, not a permission: no tool makes a PDF.
    "You cannot make a PDF yourself": "no PDF tool without run_command (spec §14)",
    # The server's own answer: `may_edit` on the status-set read, which is
    # `core.can_manage_settings`, the status write's predicate.
    "may not edit the statuses": "server answer: writes.status_edit_refusal",
    "the server says you may not edit it": "server answer: reads.vocabulary",
    # Visibility, not a permission claim: the server filters the rows.
    "rows you cannot see": "server-filtered: guarded.move_project subtree count",
}
#: ``len(ALLOWED_CLAIMS)`` on 2026-10-07. Lower it with each removal.
CLAIM_CEILING = 8


def _md_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _py_strings(path: Path) -> str:
    """Every string literal the model can read, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    parts = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]
    return " ".join(" ".join(parts).split())


def _py_all_strings(path: Path) -> str:
    """Every string literal of a tool module, docstrings INCLUDED: the model
    reads a tool's docstring as its description, and its return text."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    return " ".join(" ".join(parts).split())


def _ts_code(path: Path) -> str:
    """The code without its comments: a comment never reaches the model."""
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    src = re.sub(r"(?m)//.*$", " ", src)
    return " ".join(src.split())


def _claims(text: str) -> list[str]:
    """Each claim in *text*, as the sentence around it."""
    found = []
    for m in CLAIM.finditer(text):
        start = max(text.rfind(". ", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        end = text.find(". ", m.end())
        found.append(text[start : end if end != -1 else len(text)].strip())
    return found


SOURCES = {
    "instructions.md": lambda: _md_text(INSTRUCTIONS),
    "refusals.py": lambda: _py_strings(REFUSALS),
    "assistantPersona.ts": lambda: _ts_code(PERSONA),
    "AssistantRail.tsx": lambda: _ts_code(RAIL),
    # Review round 1: every tool module, so a tool's return text or its
    # docstring cannot bring a claim back (the old "It needs the settings
    # permission." in guarded.py is the case this catches).
    **{
        f"skill_projects/{p.name}": (lambda p=p: _py_all_strings(p))
        for p in sorted(REFUSALS.parent.glob("*.py"))
        if p.name != "refusals.py"
    },
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_no_string_claims_a_permission_without_a_server_check(name: str) -> None:
    """THE MUTATION: put "They may NOT edit … say who can: an organization
    admin." back into the persona, and this fails for the persona."""
    bad = [
        s
        for s in _claims(SOURCES[name]())
        if not any(allowed.lower() in s.lower() for allowed in ALLOWED_CLAIMS)
    ]
    assert bad == [], f"{name} claims a permission no server check backs: {bad}"


def test_the_allowlist_only_shrinks_and_every_entry_is_still_used() -> None:
    assert len(ALLOWED_CLAIMS) <= CLAIM_CEILING, "the allowlist grew; quote the server instead"
    text = " ".join(load() for load in SOURCES.values()).lower()
    stale = [k for k in ALLOWED_CLAIMS if k.lower() not in text]
    assert stale == [], f"remove these entries and lower CLAIM_CEILING: {stale}"


def test_the_instructions_carry_the_owners_rule() -> None:
    text = _md_text(INSTRUCTIONS)
    for phrase in (
        "## Who decides what the member may change",
        "The server decides.",
        "You never decide a permission yourself.",
        "The member's approval on the card is their consent.",
        "Until a tool gives a refusal, never say that the member may not make a change.",
        "Do not name a permission, a role, or a person who can do the change, "
        "unless the refusal names it.",
    ):
        assert phrase in text, phrase


def test_the_product_rules_stay_with_their_decisions() -> None:
    """Kept on purpose: these are decisions, the same for every member."""
    text = _md_text(INSTRUCTIONS)
    assert "Archive is the remove verb (D-PM-35)." in text
    assert "The chat does not change who may see a project (D-PM-40)." in text


# ── grants-r8: the real routes on a private ladder database ─────────────────

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    promoted,
)

R8_OWNER = "owner@grants-r8.test"
R8_MEMBER = "member@grants-r8.test"


@pytest.fixture
def space(promoted):  # noqa: F811
    """One space with one lane in org B, visible to the whole organization,
    and the two people in the directory. Seeded as the superuser."""
    from sqlalchemy import text

    org = promoted.org_b
    pid, lane, grant = (str(uuid.uuid4()) for _ in range(3))
    with promoted.admin_engine.begin() as c:
        for email in (R8_OWNER, R8_MEMBER):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )
        c.execute(
            text(
                "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
                "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'Metorite', "
                "'manual', :me, true)"
            ),
            {"p": pid, "o": org, "me": R8_OWNER},
        )
        c.execute(
            text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
                "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 10, "
                "'todo', true)"
            ),
            {"s": lane, "p": pid},
        )
        c.execute(
            text(
                "INSERT INTO pm_project_grants (id, project_id, organization_id, subject, "
                "created_by) VALUES (CAST(:g AS uuid), CAST(:p AS uuid), CAST(:o AS uuid), "
                "'org', :me)"
            ),
            {"g": grant, "p": pid, "o": org, "me": R8_OWNER},
        )
    yield type("Space", (), {"id": pid, "org": org, "promoted": promoted})
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_tags WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": pid})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": [R8_OWNER, R8_MEMBER]})


def _gateway_app():
    """The real Projects router, with the identity the gateway's auth seam
    would resolve for each address: the owner holds ``*``, the member holds
    ``feature:projects`` only."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import member_user, projects_user

    people = {R8_OWNER: projects_user(R8_OWNER, features="*"), R8_MEMBER: member_user(R8_MEMBER)}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


def _route_tools_to(monkeypatch, app: Any, email: str) -> None:
    """The skill's client talks to the real router in-process, as *email*."""
    import types

    import httpx
    import skill_projects.client as client

    real = httpx.AsyncClient
    monkeypatch.setattr(
        client,
        "httpx",
        types.SimpleNamespace(
            AsyncClient=lambda **kw: real(transport=httpx.ASGITransport(app=app), **kw),
        ),
    )
    monkeypatch.setattr(client, "gateway_url", lambda: "http://gateway.test")
    monkeypatch.setattr(client, "current_user_email", lambda: email)


async def _as(space: Any, monkeypatch, email: str, act):
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    _route_tools_to(monkeypatch, _gateway_app(), email)
    token = bind_tenant(space.org)
    try:
        url = space.promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            return await act()
    finally:
        release_tenant(token)


def _tags(space: Any) -> list[tuple[str, Any]]:
    from sqlalchemy import text

    with space.promoted.admin_engine.connect() as c:
        return [
            (r.name, r.project_id)
            for r in c.execute(
                text(
                    "SELECT name, project_id FROM pm_tags WHERE organization_id = CAST(:o AS uuid) "
                    "ORDER BY name"
                ),
                {"o": space.org},
            )
        ]


@_DB_GATE
async def test_r8_an_owner_creates_a_tag_through_the_real_route(space, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    asked = approve(monkeypatch)
    out = await _as(
        space,
        monkeypatch,
        R8_OWNER,
        lambda: refusals_as_text(skill_projects.create_tag)(space.id, "urgent"),
    )
    assert len(asked) == 1 and out.startswith("Added tag «urgent»"), out
    assert [(n, str(p)) for n, p in _tags(space)] == [("urgent", space.id)]


@_DB_GATE
async def test_r8_a_member_without_the_grant_reads_the_servers_refusal(
    space,
    monkeypatch,
) -> None:
    """An org-wide tag needs ``admin:settings:manage``. The member approves
    the card, the route refuses, and the receipt quotes the route."""
    pytest.importorskip("asyncpg")
    monkeypatch.setenv("PROJECTS_ORG_VOCABULARIES", "1")
    asked = approve(monkeypatch)
    out = await _as(
        space,
        monkeypatch,
        R8_MEMBER,
        lambda: refusals_as_text(skill_projects.create_tag)(space.id, "urgent", org_wide=True),
    )
    assert len(asked) == 1, out
    assert out.startswith("Refused: Not permitted (403)."), out
    assert "Gateway said: «An organization-wide entry applies to every project" in out
    assert _tags(space) == []


@_DB_GATE
async def test_r8_the_flag_off_answer_reaches_the_owner_as_the_servers_words(
    space,
    monkeypatch,
) -> None:
    pytest.importorskip("asyncpg")
    monkeypatch.delenv("PROJECTS_ORG_VOCABULARIES", raising=False)
    approve(monkeypatch)
    out = await _as(
        space,
        monkeypatch,
        R8_OWNER,
        lambda: refusals_as_text(skill_projects.create_tag)(space.id, "urgent", org_wide=True),
    )
    assert "Gateway said: «Organization-wide vocabularies are not enabled here.»" in out
    assert _tags(space) == []


@_DB_GATE
async def test_r8_the_status_precheck_agrees_with_the_status_write(space, monkeypatch) -> None:
    """For each person, the read before the card and the write give ONE
    answer: the member is refused before the card with the write's own
    words, and the same write sent by hand answers 403 with those words. The
    owner gets the card and the lane."""
    pytest.importorskip("asyncpg")
    from skill_projects.client import GatewayRefusal, post

    asked = approve(monkeypatch)
    out = await _as(
        space, monkeypatch, R8_MEMBER, lambda: skill_projects.create_status(space.id, "Blocked")
    )
    assert asked == [] and f"Gateway said: «{SETTINGS_WORDS}»" in out, out

    async def by_hand() -> Any:
        return await post(
            f"/projects/nodes/{space.id}/statuses",
            {"name": "Blocked", "category": "todo", "position": 20},
        )

    with pytest.raises(GatewayRefusal) as err:
        await _as(space, monkeypatch, R8_MEMBER, by_hand)
    assert err.value.status == 403 and err.value.detail == SETTINGS_WORDS

    out = await _as(
        space, monkeypatch, R8_OWNER, lambda: skill_projects.create_status(space.id, "Blocked")
    )
    assert len(asked) == 1 and out.startswith("Added status «Blocked»"), out
