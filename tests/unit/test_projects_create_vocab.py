"""H-273 — several new tags or task types as ONE batch (``create_tags``, ``create_types``).

Spec: ``project-docs/specs/projects_ai_chat.md`` §24.5, and the owner
directive of 2026-10-07 (``projects_agent_parity.md`` §16): the member's
grants decide, and the server checks.

On 2026-10-08 the owner asked the chat for several new tags. It drew a
picker, and then one card for each tag. These tools take the words in ONE
call, under ONE confirmation card with a checkbox for each row, as
``create_tasks`` does. Each rule names the mutation that turns it red (R7):

1. **Three good rows make one card and three POSTs** to the route of
   ``create_tag``. Mutation: post only the first row ->
   ``test_three_good_rows_make_one_card_and_three_tags``.
2. **A duplicate in the batch is refused before the card.** Mutation: drop the
   twin check -> ``test_a_duplicate_in_the_batch_is_refused_before_the_card``.
3. **A name the project has already starts unticked, with the reason.**
   Mutation: tick every row -> ``test_an_existing_tag_starts_unticked...``.
4. **An unticked row is not written.** Mutation: write every row ->
   ``test_an_unticked_row_is_not_written``.
5. **A forged row id writes nothing.** Mutation: drop the ``<=`` check ->
   ``test_a_forged_row_id_writes_nothing``.
6. **A 403 is quoted, and never decided before.** Mutation: say the reason
   in our own words -> ``test_a_403_is_quoted_in_the_receipt``.
7. **A partial failure is a partial receipt, and nothing raises after the
   first write.** Mutation: ``break`` at the first failure, or let the error
   out -> ``test_a_partial_failure_gives_a_partial_receipt_and_no_raise``.

R8, at the end: the batch through the REAL tag route on asyncpg. It SKIPS
without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import forms as F
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit import test_projects_agent_writes as tw
from tests.unit._projects_agent_fakes import (
    FakeClient,
    FakeResponse,
    answer_rows,
    approve,
    deny,
    fake_gateway,
    writes,
)

UUID = tw.UUID
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
THREE = json.dumps([{"name": "q4", "color": "blue"}, "blocked", {"name": "vip",
                                                                  "description": "Key accounts"}])


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


def _gateway(
    *,
    forbid: tuple[str, ...] = (),
    drop: tuple[str, ...] = (),
    boom: tuple[str, ...] = (),
    org_wide: tuple[str, ...] = (),
):
    """The writes fence's gateway, with numbered rows and failing names.

    A name in ``forbid`` gets a 403 with the route's words, in ``drop`` a lost
    connection, in ``boom`` an error no gateway sends. ``org_wide`` adds tags
    that the organization holds (``project_id`` None) to the tag list.
    """

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if method == "GET" and path.endswith("/tags") and org_wide:
            rows = tw.responder(call)["rows"]
            extra = [{"id": str(uuid.uuid4()), "name": n, "project_id": None} for n in org_wide]
            return {"rows": [*rows, *extra]}
        if method == "POST" and path.endswith(("/tags", "/types")):
            name = call["json"]["name"]
            if name in forbid:
                return FakeResponse({"detail": "Only a space lead may add words here."}, 403)
            if name in drop:
                raise httpx.ConnectError("the gateway went away")
            if name in boom:
                raise KeyError("not a gateway error")
            return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)), **call["json"]}
        return tw.responder(call)

    return answer


def _posts(calls: list[dict], kind: str = "tags") -> list[dict]:
    return [c for c in writes(calls) if c["method"] == "POST" and c["path"].endswith(f"/{kind}")]


# ── 1. Three good rows: one card, three POSTs ───────────────────────────────


async def test_three_good_rows_make_one_card_and_three_tags(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tags(UUID, THREE)

    assert len(asked) == 1, asked
    card = asked[0]
    assert card["title"] == "Add 3 tags to «Ops»?"
    assert [r["id"] for r in card["rows"]] == ["row-1", "row-2", "row-3"]
    assert [r["checked"] for r in card["rows"]] == [True, True, True]
    assert [r["label"] for r in card["rows"]] == ["q4", "blocked", "vip"]
    assert [r["hint"] for r in card["rows"]] == [
        "colour blue", "no colour or description", "description Key accounts",
    ]
    assert not _UUID_RE.search(json.dumps(card)), card
    posts = _posts(calls)
    # Each row is one POST to create_tag's own route, with create_tag's body.
    assert {p["path"] for p in posts} == {f"/projects/nodes/{UUID}/tags"}
    assert [p["json"] for p in posts] == [
        {"name": "q4", "color": "blue"},
        {"name": "blocked"},
        {"name": "vip", "description": "Key accounts"},
    ]
    assert out.startswith("Added 3 tags to «Ops» and every project under it:")
    assert out.count("tag_id:") == 3 and "stopped:" not in out


async def test_every_call_before_the_card_is_a_read(monkeypatch) -> None:
    """No permission read and no write before the card (the #714 rule)."""
    import acb_skills.ask_tools as ask_tools

    from tests.unit._projects_agent_fakes import card_answer

    calls = fake_gateway(monkeypatch, _gateway())

    async def marker(**kwargs: Any) -> Any:
        calls.append({"method": "CARD", "path": "", "headers": {}, "params": {}, "json": kwargs})
        return card_answer(kwargs, True)

    monkeypatch.setattr(ask_tools, "request_confirmation", marker)
    await skill_projects.create_tags(UUID, THREE)
    at = next(i for i, c in enumerate(calls) if c["method"] == "CARD")
    assert all(m.is_read(c["method"], c["path"]) for c in calls[:at])
    assert not any("status-set" in c["path"] or "/me" in c["path"] for c in calls[:at])
    assert len(_posts(calls[at:])) == 3


async def test_the_receipt_has_the_shape_the_receipt_card_parses(monkeypatch) -> None:
    """`ProjectToolCards.test.ts` holds the TypeScript side to these lines."""
    approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(forbid=("vip",)))
    out = await skill_projects.create_tags(UUID, THREE)
    lines = out.splitlines()
    assert lines[0] == (
        "Added 2 of 3 tags to «Ops» and every project under it. 1 was not created."
    )
    assert lines[1] == "- tag «q4» · colour blue"
    assert re.fullmatch(r"  tag_id: [0-9a-f-]{36}", lines[2])
    assert lines[3] == "- tag «blocked»"
    assert "failed: row 3 «vip» refused (403): «Only a space lead may add words here.»" in lines
    assert "stopped: 1 of 3 rows failed." in lines


# ── 2. A duplicate in the batch is refused before the card ──────────────────


@pytest.mark.parametrize(
    ("rows", "words"),
    [
        (["q4", "Q4"], "Rows 1 and 2 have the same name, «Q4»"),
        (["q4", "needs  review", "needs review"], "Rows 2 and 3 have the same name"),
        ([{"name": "q4"}, {"color": "red"}], "Row 2 has no name"),
        ([{"name": "q4", "colour": "red"}], "has no key «colour»"),
        ([{"name": "q4", "org_wide": True}], "call create_tag for that tag alone"),
        ([{"name": "q4", "color": ["red"]}], "color takes text"),
        ([], "tags is a JSON list"),
        ("not json", "tags is a JSON list"),
        ([3], "Row 1 is not an object"),
    ],
)
async def test_a_duplicate_in_the_batch_is_refused_before_the_card(
    rows: Any, words: str, monkeypatch
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    raw = rows if isinstance(rows, str) else json.dumps(rows)
    out = await skill_projects.create_tags(UUID, raw)
    assert words in out, out
    assert asked == [] and calls == []


async def test_the_batch_is_capped_at_one_card(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tags(UUID, json.dumps([f"t{n}" for n in range(W.MAX_BATCH + 1)]))
    assert f"The limit for one card is {W.MAX_BATCH}" in out and calls == []


async def test_a_batch_that_does_not_fit_on_the_card_is_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    rows = [{"name": f"t{n}", "description": "x" * 400} for n in range(20)]
    out = await skill_projects.create_tags(UUID, json.dumps(rows))
    assert "do not fit on one confirmation card" in out
    assert asked == [] and writes(calls) == []


# ── 3. A name the project has already starts unticked ───────────────────────


async def test_an_existing_tag_starts_unticked_with_the_reason(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tags(UUID, json.dumps(["q4", "URGENT"]))

    rows = asked[0]["rows"]
    assert [r["checked"] for r in rows] == [True, False]
    assert rows[1]["hint"].endswith("a tag with this name exists here already, so it starts unticked")
    assert [p["json"]["name"] for p in _posts(calls)] == ["q4"]
    assert out.endswith(
        "left out: row 2 «URGENT», unticked on the card, because a tag with this name "
        "exists here already."
    )


async def test_an_organization_wide_tag_starts_unticked_and_says_so(monkeypatch) -> None:
    """A tree may shadow an org-wide tag (D-PM-16), so the member may tick it."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(org_wide=("Customer",)))
    await skill_projects.create_tags(UUID, json.dumps(["q4", "customer"]))
    row = asked[0]["rows"][1]
    assert row["checked"] is False
    assert "organization-wide tag with this name exists" in row["hint"]


async def test_an_existing_name_the_member_ticks_again_goes_to_the_server(monkeypatch) -> None:
    """The tool decides nothing: a ticked row is posted, and the route answers."""
    answer_rows(monkeypatch, {"row-1", "row-2"})
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_tags(UUID, json.dumps(["q4", "urgent"]))
    assert [p["json"]["name"] for p in _posts(calls)] == ["q4", "urgent"]


# ── 4 and 5. Only the ticked rows, and only the card's own rows ─────────────


async def test_an_unticked_row_is_not_written(monkeypatch) -> None:
    asked = answer_rows(monkeypatch, {"row-1", "row-3"})
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_tags(UUID, THREE)
    assert len(asked) == 1
    assert [p["json"]["name"] for p in _posts(calls)] == ["q4", "vip"]
    assert "left out: row 2 «blocked», unticked on the card." in out
    assert out.startswith("Added 2 tags to")


async def test_zero_ticked_rows_and_a_declined_card_write_nothing(monkeypatch) -> None:
    answer_rows(monkeypatch, set())
    calls = fake_gateway(monkeypatch, _gateway())
    assert await skill_projects.create_tags(UUID, THREE) == W.CANCELLED
    deny(monkeypatch)
    assert await skill_projects.create_tags(UUID, THREE) == W.CANCELLED
    assert writes(calls) == []


async def test_a_forged_row_id_writes_nothing(monkeypatch) -> None:
    answer_rows(monkeypatch, {"row-1", "row-9"})
    calls = fake_gateway(monkeypatch, _gateway())
    assert await skill_projects.create_tags(UUID, THREE) == F.FORGED_ROWS
    assert writes(calls) == []


async def test_a_door_that_answers_a_bool_writes_nothing(monkeypatch) -> None:
    import acb_skills.ask_tools as ask_tools

    async def yes(**_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(ask_tools, "request_confirmation", yes)
    calls = fake_gateway(monkeypatch, _gateway())
    assert await skill_projects.create_tags(UUID, THREE) == W.CANCELLED
    assert writes(calls) == []


# ── 6. A 403 is the server's, and the receipt quotes it ─────────────────────


async def test_a_403_is_quoted_in_the_receipt(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(forbid=("blocked",)))
    out = await skill_projects.create_tags(UUID, THREE)
    # The route was asked: the tool decided no permission itself.
    assert [p["json"]["name"] for p in _posts(calls)] == ["q4", "blocked", "vip"]
    assert (
        "failed: row 2 «blocked» refused (403): «Only a space lead may add words here.»"
        in out.splitlines()
    )
    # No role or permission of our own words.
    assert not re.search(r"permission|admin|settings:write", out, re.I), out


async def test_a_403_on_every_row_is_text_and_names_no_tag(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(forbid=("q4", "blocked")))
    out = await skill_projects.create_tags(UUID, json.dumps(["q4", "blocked"]))
    assert out.startswith("No tag of the 2 is known to exist in «Ops»")
    assert "tag_id:" not in out and out.count("refused (403)") == 2


# ── 7. A partial failure: a partial receipt, and no raise ───────────────────


async def test_a_partial_failure_gives_a_partial_receipt_and_no_raise(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(drop=("q4",), boom=("blocked",)))
    out = await skill_projects.create_tags(UUID, THREE)
    # The first row failed, and the other two were still tried.
    assert [p["json"]["name"] for p in _posts(calls)] == ["q4", "blocked", "vip"]
    assert out.startswith("Added 1 of 3 tags to «Ops» and every project under it.")
    assert "2 may have been created: read vocabulary before a retry." in out
    assert "unknown: row 1 «q4» failed (ConnectError), so it may or may not exist." in out
    assert "unknown: row 2 «blocked» failed (KeyError)" in out
    assert "- tag «vip» · description Key accounts" in out
    assert "stopped: 2 of 3 rows failed." in out
    assert "The 1 tag listed above exist" in out


# ── create_types: the same helper, the route of create_type ────────────────


async def test_create_types_makes_one_card_and_posts_create_types_body(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    rows = [{"name": "Chore", "icon": "broom"}, {"name": "Spike", "is_epic": "yes"}, "bug"]
    out = await skill_projects.create_types(UUID, json.dumps(rows))
    card = asked[0]
    assert card["title"] == "Add 3 types to «Ops»?"
    assert [r["checked"] for r in card["rows"]] == [True, True, False]
    assert card["rows"][1]["hint"] == "a top level (epic)"
    assert [p["json"] for p in _posts(calls, "types")] == [
        {"name": "Chore", "icon": "broom"},
        {"name": "Spike", "is_epic": True},
    ]
    assert out.count("type_id:") == 2 and "left out: row 3 «bug»" in out


async def test_create_types_refuses_the_default_and_a_bad_epic_before_the_card(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_types(UUID, json.dumps([{"name": "A", "is_default": True}]))
    assert "call create_type for that type alone" in out
    out = await skill_projects.create_types(UUID, json.dumps([{"name": "A", "is_epic": "maybe"}]))
    assert out.startswith("Row 1 («A»): is_epic is yes or no.")
    assert asked == [] and calls == []


def test_the_batch_tools_are_class_b_composites_of_the_single_tools() -> None:
    assert m.COMPOSITE["create_tags"] == frozenset({"create_tag"})
    assert m.COMPOSITE["create_types"] == frozenset({"create_type"})
    assert m.tool_class("create_tags") == m.tool_class("create_types") == "B"


def test_a_row_key_means_what_the_single_tools_argument_means() -> None:
    """Each row key is an argument of the single tool. Mutation: a row key
    that create_tag or create_type does not take -> this test fails."""
    import inspect

    for spec in F.VOCAB_KINDS.values():
        params = set(inspect.signature(getattr(skill_projects, spec.single)).parameters)
        assert set(spec.keys) <= params, (spec.tool, set(spec.keys) - params)


def test_the_tools_are_annotated_as_closed_world_writes() -> None:
    """H-236: a covered run may hold these, because they reach only the gateway."""
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    for name in ("create_tags", "create_types"):
        hints = TOOL_ANNOTATIONS[name]
        assert hints["read_only"] is False and hints["open_world"] is False, name


# ── R8 — the batch through the REAL tag route, on asyncpg ───────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_r8 = pytest.mark.skipif(
    not _TENANT_URL, reason="TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres."
)


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def _ladder():
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("asyncpg")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """One root project with one tag of its own."""
    from sqlalchemy import create_engine, text

    eng = create_engine(_TENANT_URL, future=True)
    stamp = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {}
    with eng.begin() as c:
        org = str(
            c.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1")).scalar_one()
        )
        made["org"] = org
        made["project"] = pid = str(
            c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
                    " timezone, owns_statuses) VALUES (:n, 'active', 'manual',"
                    " 'h273@example.test', CAST(:o AS uuid), 'UTC', true) RETURNING id"
                ),
                {"n": f"h273-{stamp}", "o": org},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO pm_tags (project_id, organization_id, name, created_by)"
                " VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'urgent', 'h273@example.test')"
            ),
            {"p": pid, "o": org},
        )
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM pm_tags WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
    eng.dispose()


class _RouteClient(FakeClient):
    """The fake client, with the tag list and the tag create answered by the
    REAL routes on one asyncpg database. Every other read is answered by *fake*."""

    def __init__(self, calls: list[dict], fake: Any, routes: Any) -> None:
        super().__init__(calls, fake)
        self._routes = routes

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        path = "/" + url.split("/", 3)[-1]
        answered = await self._routes(method, path, kwargs)
        if answered is None:
            return await super().request(method, url, **kwargs)
        self._calls.append(
            {"method": method, "path": path, "params": kwargs.get("params") or {},
             "json": kwargs.get("json"), "headers": kwargs.get("headers") or {}}
        )
        return answered


def _real_routes(seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    from contextlib import asynccontextmanager

    from fastapi import HTTPException
    from fastapi.encoders import jsonable_encoder
    from gateway.routes.projects import tags as pm_tags
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from tests.unit._projects_fakes import projects_user

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _visibility(_db: Any, _user: Any) -> Any:
        return Visibility(unrestricted=True, email="", groups=(), organization_id=seeded["org"])

    monkeypatch.setattr(pm_tags, "_tenant_session", _session)
    monkeypatch.setattr(pm_tags, "resolve_visibility", _visibility)
    user = projects_user("h273@example.test")
    pid = seeded["project"]

    async def routes(method: str, path: str, kwargs: dict[str, Any]) -> Any:
        try:
            if path == f"/projects/nodes/{pid}/tags" and method == "POST":
                made = await pm_tags.create_tag(pid, pm_tags.TagIn(**kwargs["json"]), user=user)
                return FakeResponse(jsonable_encoder(made), 201)
            if path == f"/projects/nodes/{pid}/tags" and method == "GET":
                return FakeResponse(jsonable_encoder(await pm_tags.list_tags(pid, user=user)))
        except HTTPException as exc:
            return FakeResponse({"detail": exc.detail}, exc.status_code)
        return None

    return routes, eng


def _stored(seeded: dict[str, Any]) -> list[tuple[str, Any]]:
    from sqlalchemy import create_engine, text

    eng = create_engine(_TENANT_URL, future=True)
    try:
        with eng.begin() as c:
            rows = c.execute(
                text("SELECT name, color FROM pm_tags WHERE project_id = CAST(:p AS uuid)"
                     " ORDER BY name"),
                {"p": seeded["project"]},
            ).fetchall()
    finally:
        eng.dispose()
    return [(r.name, r.color) for r in rows]


@_r8
async def test_r8_the_batch_lands_through_the_real_tag_route(seeded, monkeypatch) -> None:
    """Three rows over the real list and create. ``urgent`` exists, so it
    starts unticked. The member ticks it again, and the route's own 409 is
    quoted while the other rows land."""
    pid = seeded["project"]
    routes, eng = _real_routes(seeded, monkeypatch)
    calls: list[dict] = []

    def fake(call: dict) -> Any:
        if call["path"] == f"/projects/nodes/{pid}":
            return {"id": pid, "name": "Kits", "kind": "project"}
        return {"rows": []}

    monkeypatch.setattr(
        client, "httpx",
        SimpleNamespace(AsyncClient=lambda **_kw: _RouteClient(calls, fake, routes)),
    )
    monkeypatch.setattr(client, "current_user_email", lambda: "h273@example.test")
    asked = answer_rows(monkeypatch, {"row-1", "row-2", "row-3"})
    rows = [{"name": "q4", "color": "blue"}, "URGENT", {"name": "vip"}]
    try:
        out = await skill_projects.create_tags(pid, json.dumps(rows))
    finally:
        await eng.dispose()
    assert [r["checked"] for r in asked[0]["rows"]] == [True, False, True]
    assert out.startswith("Added 2 of 3 tags to «Kits» and every project under it."), out
    assert "failed: row 2 «URGENT» refused (409): «'urgent' already exists here.»" in out
    # The column default is gray (migration 156), and only q4 sent a colour.
    assert _stored(seeded) == [("q4", "blue"), ("urgent", "gray"), ("vip", "gray")]
