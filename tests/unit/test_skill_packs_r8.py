"""WS-STU F3: the reads of the skill packs on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §7 "Runtime" and the §14 F3 row.
Code: ``gateway/studio/skills_store.list_loadable`` and
``loadable_versions``, and ``orchestrator/_tool_injection._turn_with_skills``,
which reads through them.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_skill_packs_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (F3).**

a. A member loads a published skill that the member owns, and a published
   skill that the org shares (``visibility = 'org'``).
b. A skill of another member that is not shared never loads: private, and
   ``group`` until J2 adds the group.
c. A draft, a paused and an archived skill never load, and nor does a
   published skill that a new version took back to draft.
d. Org B's skill never loads for org A, also when the caller names its id.
e. A turn that names a skill loads its rules and its tools. A turn that names
   a skill that the member may not load gets none.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``skills_store._LOADABLE_WHERE``: drop ``s.state = 'published'``.
  ``test_r8_only_a_published_skill_loads`` goes red.
* M2. ``_LOADABLE_WHERE``: drop ``s.owner_email = :m OR``, or widen it to
  ``OR s.visibility <> 'private'``.
  ``test_r8_another_members_unshared_skill_never_loads`` goes red.
* M3. ``_LOADABLE_WHERE`` and ``_LOADABLE_VERSIONS_SQL``: drop each
  ``organization_id = _BOUND_ORG`` filter AND the FORCE RLS lines of both
  tables. ``test_r8_org_b_skill_never_loads_for_org_a`` goes red.
* M4. ``_LOADABLE_VERSIONS_SQL``: drop ``AND v.version = s.current_version``.
  ``test_r8_a_turn_loads_the_rules_of_its_skill`` reads two rows → red.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_skills import tool_packs as tp
from gateway.studio import skills_store as store
from sqlalchemy import text

from tests.unit import _r8_lists as r8_lists
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuf3.test"
BEN = f"ben.{TAG}@stuf3.test"
_LIKE = f"%.{TAG}@stuf3.test"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _body(name: str) -> dict[str, Any]:
    return {
        "description": f"Use this for {name}.",
        "instructions": f"Rules of {name}: read the price list, then fill the template.",
        "actions": ["knowledge.get_page"],
    }


async def _make(org: str, member: str, name: str, *, state: str = "published",
                versions: int = 1) -> str:
    slug = f"{name.lower().replace(' ', '-')}-{TAG}"
    async with tenant_session(org) as db:
        skill = await store.create_skill(db, member, name=name, slug=slug)
        for _ in range(versions):
            await store.add_version(db, member, skill.id, _body(name))
        await store.set_state(db, member, skill.id, state)
    return skill.id


def _share(world: Any, skill_id: str, visibility: str) -> None:
    """No writer of the visibility exists before J2, so the superuser sets it."""
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_skills SET visibility = :v WHERE id = CAST(:i AS uuid)"),
                  {"v": visibility, "i": skill_id})


async def _loadable(org: str, member: str, ids: list[str]) -> tuple[list[str], list[str]]:
    async with tenant_session(org) as db:
        heads = [s.id for s in await store.list_loadable(db, member)]
        pairs = await store.loadable_versions(db, member, ids)
    return heads, [s.id for s, _v in pairs]


def test_r8_only_a_published_skill_loads(world) -> None:
    """c. A draft, a paused and an archived skill never load. A new version
    of a published skill takes it back to draft, so it stops loading."""
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        out["live"] = await _make(a, ANA, "Live one")
        out["draft"] = await _make(a, ANA, "Draft one", state="draft")
        out["paused"] = await _make(a, ANA, "Paused one", state="paused")
        out["archived"] = await _make(a, ANA, "Archived one", state="archived")
        out["none"] = await _make(a, ANA, "No version", versions=0, state="draft")
        out["before"] = await _loadable(a, ANA, list(out.values()))
        # The F2a route moves a published skill with a new version to draft.
        async with tenant_session(a) as db:
            await store.add_version(db, ANA, out["live"], _body("Live one v2"))
            await store.set_state(db, ANA, out["live"], "draft")
        out["after"] = await _loadable(a, ANA, [out["live"]])
        return out

    out = _go(world, go)
    assert out["before"] == ([out["live"]], [out["live"]])
    assert out["after"] == ([], [])


def test_r8_another_members_unshared_skill_never_loads(world) -> None:
    """a and b. Ana loads her own skill and the skill that Ben shared with the
    org. Ben's private skill and his group skill never load for Ana."""
    a = world.p.org_a

    async def go() -> dict[str, str]:
        return {
            "mine": await _make(a, ANA, "Ana quotes"),
            "private": await _make(a, BEN, "Ben private"),
            "group": await _make(a, BEN, "Ben group"),
            "org": await _make(a, BEN, "Ben shared"),
        }

    ids = _go(world, go)
    _share(world, ids["group"], "group")
    _share(world, ids["org"], "org")
    heads, pairs = _go(world, lambda: _loadable(a, ANA, list(ids.values())))
    want = sorted([ids["mine"], ids["org"]])
    assert sorted(heads) == want and sorted(pairs) == want
    # Ben loads his own three. The group share reaches its owner.
    bheads, _ = _go(world, lambda: _loadable(a, BEN, []))
    assert sorted(bheads) == sorted([ids["private"], ids["group"], ids["org"]])


def test_r8_org_b_skill_never_loads_for_org_a(world) -> None:
    """d. Org B's skill, also one that org B shares with its org, never loads
    in org A, also for the same member address and when the caller names its
    id."""
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, str]:
        return {"b": await _make(b, ANA, "Org b quotes"),
                "a": await _make(a, ANA, "Org a quotes")}

    ids = _go(world, go)
    _share(world, ids["b"], "org")
    heads, pairs = _go(world, lambda: _loadable(a, ANA, [ids["b"], ids["a"]]))
    assert heads == [ids["a"]] and pairs == [ids["a"]]
    other = _go(world, lambda: _loadable(a, BEN, [ids["b"]]))
    assert other == ([], [])
    # Non-vacuity: org B reads its own skill.
    assert _go(world, lambda: _loadable(b, ANA, [ids["b"]])) == ([ids["b"]], [ids["b"]])


@contextlib.contextmanager
def _tenant(org: str) -> Iterator[None]:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def test_r8_a_turn_loads_the_rules_of_its_skill(world, monkeypatch) -> None:
    """e. Through ``_turn_with_skills`` on the real database: a turn that
    names Ana's skill and Ben's shared skill loads both. A turn that names
    Ben's private skill and org B's skill loads neither."""
    from orchestrator import _tool_injection as ti
    from orchestrator import executor as ex

    a, b = world.p.org_a, world.p.org_b

    async def seed() -> dict[str, str]:
        return {
            "mine": await _make(a, ANA, "Quote desk"),
            "org": await _make(a, BEN, "Price check"),
            "private": await _make(a, BEN, "Payroll notes"),
            "orgb": await _make(b, ANA, "Layoff plan"),
        }

    ids = _go(world, seed)
    _share(world, ids["org"], "org")
    _share(world, ids["orgb"], "org")
    monkeypatch.setattr(ti, "_studio_open", lambda: True)

    async def _open() -> bool:
        return True

    monkeypatch.setattr(ti, "_studio_member_open", _open)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: ANA)

    def _turn(words: str) -> tp.PackTurn:
        return tp.PackTurn(a, "chat-stuf3-r8", tp._member_part(ANA), 1, (), words, True)

    async def go(words: str) -> Any:
        with _tenant(a):
            return await ti._turn_with_skills(_turn(words))

    hit = _go(world, lambda: go("Use the quote desk and the price check for Acme"))
    got = {s.skill_id: s for s in hit.skills}
    assert sorted(got) == sorted([ids["mine"], ids["org"]])
    assert "Rules of Quote desk" in got[ids["mine"]].rules
    assert got[ids["mine"]].tools == ("read_page",)
    assert got[ids["mine"]].version == 1
    miss = _go(world, lambda: go("Open the payroll notes and the layoff plan"))
    assert miss.skills == ()


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    assert "tests/unit/test_skill_packs_r8.py" in r8_lists.must_run()
