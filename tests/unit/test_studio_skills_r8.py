"""WS-STU F1 — the skill store of AI Studio on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.5 and the §14 F1 row. Code:
``gateway/studio/skills_store.py``. Migration: the file that creates
``studio_skills`` (320 at build).

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_skills_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (F1).**

a. The migration applies, and both tables have ENABLE and FORCE RLS.
b. A new skill is private, a draft, and has no version.
c. One slug for each owner, so two members may each hold one slug, and a
   refusal names no skill of a colleague. The CHECKs hold the states, the
   visibilities, the owner, the slug, the name, the description and the
   version body. The store refuses each body that the CHECK refuses, and a
   body of exactly the store's limit passes the CHECK.
d. Versions take the numbers 1, 2, 3 in order, and the newest is current.
e. A stored version cannot change: the trigger refuses each UPDATE, also as
   the superuser. A DELETE of the skill takes its versions.
f. Two ``add_version`` calls at once give two numbers.
g. Org B reads and writes none of org A's skills or versions. A version
   cannot point at a skill of another org. A member reads only their own.
h. The member purge deletes the member's skills of this org, and their
   versions. The skills of another member and of another org stay, also as
   the superuser.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. The migration and ``generated/04_policies.sql``: drop the two
  ``FORCE  ROW LEVEL SECURITY`` lines of ``studio_skills``.
  ``test_the_migration_holds_the_checks_the_rls_and_the_trigger`` and
  ``test_r8_both_tables_have_enable_and_force_rls`` go red.
* M1b. Drop the ENABLE and the FORCE lines of both tables, in both files.
  ``test_r8_org_b_reads_and_writes_nothing_of_org_a`` goes red. With ENABLE
  alone, RLS binds this suite's role, because the role is not the owner.
* M2. The migration: make ``studio_skill_versions_skill_fk`` a foreign key on
  ``skill_id`` alone, to ``studio_skills (id)``.
  ``test_r8_a_version_cannot_point_at_a_skill_of_another_org`` goes red.
* M3. The migration: drop the trigger ``studio_skill_versions_immutable``.
  ``test_r8_a_stored_version_cannot_be_updated`` goes red.
* M4. ``routes/admin/members.py``: drop the ``studio_skills`` row of
  ``_PURGE_DELETES``. ``test_r8_a_member_purge_removes_the_skills_of_this_org_only``
  goes red, and so do two tests of ``test_admin_member_purge.py``.
* M5. The migration and the store: make the slug key
  ``UNIQUE (organization_id, slug)`` again.
  ``test_r8_a_slug_is_unique_for_each_owner`` goes red.
* M6. ``skills_store.py``: drop the float branch of ``_check_values``.
  ``test_r8_a_refused_body_writes_nothing`` and the grown-body cases of
  ``test_the_store_refuses_a_body_that_jsonb_refuses_or_grows`` go red.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.studio import skills_store as store
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS studio_skills (" in p.read_text(encoding="utf-8"))
_POLICIES = _ROOT / "infra" / "postgres" / "generated" / "04_policies.sql"
_STORE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "studio" / "skills_store.py"

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@stuf1.test"
BEN = f"ben.{TAG}@stuf1.test"
_LIKE = f"%.{TAG}@stuf1.test"
_HASH = "0" * 64


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    # The versions go by the cascade of their foreign key.
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _slug(name: str = "quote") -> str:
    return f"{name}-{TAG}"


def _body(note: str = "one") -> dict[str, Any]:
    return {
        "description": f"Use this when someone asks for a quote ({note}).",
        "instructions": "Read the price list, then fill the quote template.",
        "actions": ["crm.read_deal", "knowledge.search"],
        "cards": [], "knowledge": [], "tests": [],
    }


async def _create(org: str, member: str = ANA, slug: str | None = None) -> store.Skill:
    async with tenant_session(org) as db:
        return await store.create_skill(db, member, name="Quotes", slug=slug or _slug(),
                                        description="Quotes from the price list.")


async def _add(org: str, skill_id: str, member: str = ANA,
               note: str = "one") -> store.Version | None:
    async with tenant_session(org) as db:
        return await store.add_version(db, member, skill_id, _body(note))


_COUNT_SQL = {
    "studio_skills": "SELECT count(*) FROM studio_skills s WHERE s.owner_email LIKE :t",
    "studio_skill_versions": (
        "SELECT count(*) FROM studio_skill_versions v JOIN studio_skills s "
        "ON s.id = v.skill_id WHERE s.owner_email LIKE :t"),
}


def _count(world: Any, table: str) -> int:
    """The rows of this suite in *table*, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(_COUNT_SQL[table]), {"t": _LIKE}).scalar_one())


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role in *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


_RAW_SKILL = (
    "INSERT INTO studio_skills (name, slug, description, owner_email, visibility, state) "
    "VALUES (:name, :slug, :desc, :m, :vis, :st)")

_RAW_VERSION = (
    "INSERT INTO studio_skill_versions (skill_id, version, body, content_hash, created_by) "
    "VALUES (CAST(:sid AS uuid), :v, CAST(:body AS jsonb), :hash, :by)")


def _skill_params(**kw: Any) -> dict[str, Any]:
    return {"name": "Quotes", "slug": _slug("raw"), "desc": "", "m": ANA,
            "vis": "private", "st": "draft", **kw}


def _version_params(skill_id: str, **kw: Any) -> dict[str, Any]:
    return {"sid": skill_id, "v": 1, "body": '{"description": "d", "instructions": "i"}',
            "hash": _HASH, "by": ANA, **kw}


# ── The schema and the wiring ──────────────────────────────────────────────


def test_the_migration_holds_the_checks_the_rls_and_the_trigger() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    policies = _POLICIES.read_text(encoding="utf-8")
    for table in ("studio_skills", "studio_skill_versions"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    for value in store.STATES + store.VISIBILITIES:
        assert f"'{value}'" in sql, value
    assert re.search(r"\bvisibility\s+TEXT NOT NULL DEFAULT 'private'", sql)
    # One slug for each owner. One slug for each org would leak a private
    # skill's name through the refusal.
    assert "UNIQUE (organization_id, owner_email, slug)" in sql
    assert "UNIQUE (organization_id, slug)" not in sql
    assert "ON CONFLICT (organization_id, owner_email, slug)" in store._CREATE_SQL
    # The target of the versions' foreign key stays.
    assert "UNIQUE (organization_id, id)" in sql
    assert "FOREIGN KEY (organization_id, skill_id)" in sql
    assert "REFERENCES studio_skills (organization_id, id) ON DELETE CASCADE" in sql
    assert "PRIMARY KEY (organization_id, skill_id, version)" in sql
    assert "BEFORE UPDATE ON studio_skill_versions" in sql
    assert f"octet_length(body::text) <= {store.MAX_BODY_BYTES}" in sql
    assert "gtd_" not in sql
    # No statement names an org as a value: the DEFAULT and RLS bind it.
    for name in dir(store):
        if name.endswith("_SQL"):
            stmt = getattr(store, name)
            assert ":org" not in stmt, name
            if "INSERT INTO" in stmt:
                columns = stmt.split("INSERT INTO", 1)[1].split(")", 1)[0]
                assert "organization_id" not in columns, name


def test_the_store_has_no_update_of_a_version_and_no_route() -> None:
    """A version is immutable, so the store holds no UPDATE of it. F1 adds
    no route, and the store imports no manifest validator (A2)."""
    src = _STORE.read_text(encoding="utf-8")
    assert "UPDATE studio_skill_versions" not in src
    assert "APIRouter" not in src and "gateway.routes" not in src
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if "manifest" in ln or "routes" in ln], imports


def test_r8_both_tables_have_enable_and_force_rls(world) -> None:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('studio_skills', 'studio_skill_versions') "
            "ORDER BY relname")).all()
    assert [tuple(r) for r in rows] == [
        ("studio_skill_versions", True, True), ("studio_skills", True, True)]


# ── b. Private by default ──────────────────────────────────────────────────


def test_r8_a_new_skill_is_private_and_a_draft_with_no_version(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[store.Skill, store.Skill | None, list[store.Skill]]:
        made = await _create(a, member=ANA.upper())
        async with tenant_session(a) as db:
            return (made, await store.get_skill(db, ANA, made.id),
                    await store.list_for(db, ANA))

    made, got, listed = _go(world, go)
    assert made.visibility == "private" and made.state == "draft"
    assert made.current_version is None and made.owner_email == ANA
    assert made.name == "Quotes" and made.slug == _slug()
    assert got == made and [s.id for s in listed] == [made.id]
    with world.p.admin_engine.connect() as c:
        vis = c.execute(text(
            "SELECT visibility FROM studio_skills WHERE id = CAST(:i AS uuid)"),
            {"i": made.id}).scalar_one()
    assert vis == "private"


# ── c. The slug and the CHECKs ─────────────────────────────────────────────


def test_r8_a_slug_is_unique_for_each_owner(world) -> None:
    """Two members of one org may each own ``layoff-plan``. So Ben's create
    succeeds, and no refusal tells him the name of a private skill of Ana.
    The same member twice is refused, in any case of the address."""
    a, b = world.p.org_a, world.p.org_b
    slug = "layoff-plan"

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {"anas": await _create(a, member=ANA, slug=slug)}
        out["bens"] = await _create(a, member=BEN, slug=slug)
        with pytest.raises(store.SkillSlugTaken) as taken:
            await _create(a, member=ANA, slug=slug)
        out["message"] = str(taken.value)
        with pytest.raises(store.SkillSlugTaken):
            await _create(a, member=ANA.upper(), slug=slug)
        # Another org may hold the same slug for the same address.
        out["other"] = await _create(b, member=ANA, slug=slug)
        async with tenant_session(a) as db:
            out["ben_lists"] = await store.list_for(db, BEN)
        return out

    out = _go(world, go)
    anas, bens, other = out["anas"], out["bens"], out["other"]
    assert anas.slug == bens.slug == other.slug == slug
    assert len({anas.id, bens.id, other.id}) == 3
    assert (anas.owner_email, bens.owner_email) == (ANA, BEN)
    assert out["message"] == "You already have a skill with this name"
    assert [s.id for s in out["ben_lists"]] == [bens.id]
    assert _count(world, "studio_skills") == 3
    # The table refuses a second row of one owner on its own, not only the store.
    err = _refused(world, a, _RAW_SKILL, _skill_params(slug=slug))
    assert "studio_skills_slug_key" in err
    assert _count(world, "studio_skills") == 3


@pytest.mark.parametrize(("params", "constraint"), [
    ({"vis": "public"}, "studio_skills_visibility"),
    ({"st": "live"}, "studio_skills_state"),
    ({"m": "Ana@STUF1.test"}, "studio_skills_owner_email"),
    ({"m": ""}, "studio_skills_owner_email"),
    ({"slug": "Bad Slug"}, "studio_skills_slug"),
    ({"slug": "a--b"}, "studio_skills_slug"),
    ({"slug": "a" * 65}, "studio_skills_slug"),
    ({"name": "   "}, "studio_skills_name"),
    ({"name": "n" * 121}, "studio_skills_name"),
    ({"desc": "d" * 1025}, "studio_skills_description"),
])
def test_r8_the_skill_checks_refuse_a_bad_row(world, params, constraint) -> None:
    err = _refused(world, world.p.org_a, _RAW_SKILL, _skill_params(**params))
    assert constraint in err


@pytest.mark.parametrize(("params", "constraint"), [
    ({"body": "[]"}, "studio_skill_versions_body_object"),
    ({"body": '{"x": "' + "y" * 262144 + '"}'}, "studio_skill_versions_body_size"),
    ({"hash": "xyz"}, "studio_skill_versions_content_hash"),
    ({"hash": "A" * 64}, "studio_skill_versions_content_hash"),
    ({"v": 0}, "studio_skill_versions_version"),
    ({"by": "Ana@STUF1.test"}, "studio_skill_versions_created_by"),
])
def test_r8_the_version_checks_refuse_a_bad_row(world, params, constraint) -> None:
    skill = _go(world, lambda: _create(world.p.org_a))
    err = _refused(world, world.p.org_a, _RAW_VERSION, _version_params(skill.id, **params))
    assert constraint in err


def test_the_store_refuses_a_bad_write_before_the_db() -> None:
    sid = str(uuid.uuid4())
    bad_bodies: list[Any] = [
        [], "text", {"instructions": "i"}, {"description": " ", "instructions": "i"},
        {"description": "d"}, {"description": "d", "instructions": ""},
        {"description": "d", "instructions": "i", "actions": "crm.read"},
        {"description": "d", "instructions": "i", "actions": ["crm.read", 3]},
        {"description": "d", "instructions": "i", "actions": [""]},
        {"description": "d", "instructions": "i", "x": float("nan")},
        {"description": "d", "instructions": "i", "x": object()},
        {"description": "d", "instructions": "i\x00"},
        {"description": "d", "instructions": "i", "k\x00": 1},
        {"description": "d", "instructions": "i" * (store.MAX_BODY_BYTES + 1)},
    ]

    async def go() -> None:
        for body in bad_bodies:
            with pytest.raises(store.SkillStoreError):
                await store.add_version(None, ANA, sid, body)
        with pytest.raises(store.SkillStoreError):
            await store.add_version(None, ANA, "not-a-uuid", _body())
        with pytest.raises(store.SkillStoreError):
            await store.add_version(None, " ", sid, _body())
        with pytest.raises(store.SkillStoreError):
            await store.set_state(None, ANA, sid, "live")
        with pytest.raises(store.SkillStoreError):
            await store.get_version(None, ANA, sid, 0)
        with pytest.raises(store.SkillStoreError):
            await store.get_version(None, ANA, sid, True)
        for slug in ("Quote", "quote-", "-quote", "a--b", "quote me", "a" * 65, ""):
            with pytest.raises(store.SkillStoreError):
                await store.create_skill(None, ANA, name="Quotes", slug=slug)
        for name in ("", "   ", "n" * 121):
            with pytest.raises(store.SkillStoreError):
                await store.create_skill(None, ANA, name=name, slug="quote")
        with pytest.raises(store.SkillStoreError):
            await store.create_skill(None, ANA, name="Quotes", slug="quote",
                                     description="d" * 1025)

    asyncio.run(go())


def test_the_content_hash_is_canonical() -> None:
    one = {"description": "d", "instructions": "i", "actions": ["a"]}
    two = {"actions": ["a"], "instructions": "i", "description": "d"}
    assert store.content_hash(one) == store.content_hash(two)
    assert len(store.content_hash(one)) == 64
    assert store.content_hash(one) != store.content_hash({**one, "actions": ["b"]})


# ── c. The store's limit and the CHECK's limit ─────────────────────────────

#: Python writes 1e308 as ``1e+308``, 6 bytes, so the store measures 7248
#: bytes. Postgres writes each one with 309 digits, so the CHECK measures
#: some 280000 bytes and refuses.
_GROWN = {"description": "d", "instructions": "i", "x": [1e308] * 900}
#: ``json.loads`` takes a lone surrogate. It has no UTF-8 form.
_SURROGATE = json.loads('{"description": "d", "instructions": "\\ud800"}')

#: The three bodies of the review. The store refuses each, before the
#: database, with a SkillStoreError.
_JSONB_BAD = {
    "grown_float": _GROWN,
    "nan": {"description": "d", "instructions": "i", "x": float("nan")},
    "lone_surrogate": _SURROGATE,
}


def _with(value: Any) -> dict[str, Any]:
    return {"description": "d", "instructions": "i", "x": [value]}


@pytest.mark.parametrize("body", [
    *_JSONB_BAD.values(),
    _with(float("inf")), _with(float("-inf")),
    _with(1e16), _with(-1e16), _with(1e15), _with(1e-5), _with(1e-300),
    {"description": "d", "instructions": "i", "k": {"n": [[2.5e20]]}},
    json.loads('{"description": "d", "instructions": "i", "\\udfff": 1}'),
], ids=[*_JSONB_BAD, "inf", "minus_inf", "1e16", "minus_1e16", "1e15", "1e-5",
        "1e-300", "nested_float", "surrogate_key"])
def test_the_store_refuses_a_body_that_jsonb_refuses_or_grows(body: Any) -> None:
    """A SkillStoreError, and never a UnicodeEncodeError or a DBAPIError. The
    session is None, so a call that reached the database fails on it."""
    with pytest.raises(store.SkillStoreError):
        store.check_body(body)
    with pytest.raises(store.SkillStoreError):
        asyncio.run(store.add_version(None, ANA, str(uuid.uuid4()), body))


@pytest.mark.parametrize("value", [
    0.5, -3.25, 0.0001, 123456789012345.6, -999999999999999.9, 0.0,
    10 ** 20, -(10 ** 20), 7, True, None,
])
def test_the_store_takes_a_number_whose_text_does_not_grow(value: Any) -> None:
    """A float below 10^15 with no exponent, and any whole number: Postgres
    writes each with the digits that Python writes."""
    assert json.loads(store.check_body(_with(value)))["x"] == [value]


def _body_of_size(size: int) -> dict[str, Any]:
    """A body whose JSON text is *size* bytes. It holds each kind of value
    whose text could differ between Python and Postgres: escapes, control
    characters, text of 2, 3 and 4 bytes, floats, whole numbers, booleans,
    null, an empty object and an empty list, and keys out of jsonb order."""
    body: dict[str, Any] = {
        "description": "Use it for a \u201cquote\u201d, a tab\t, a / and a \\.\n",
        "instructions": "i",
        "actions": ["crm.read_deal"],
        "cards": [{"bb": [1.5, -2, 0.25, 123456789012345.6, True, False, None]}],
        "knowledge": [{"zz": {}, "a": [], "ctl": "\x01\x1f\x7f", "ls": "\u2028"}],
        "tests": [{"q": "\u20ac and \U0001f600", "n": 10 ** 20}],
    }
    pad = size - len(store.check_body(body).encode("utf-8"))
    body["instructions"] = "i" * (1 + pad)
    return body


def test_r8_a_body_at_the_store_limit_passes_the_check(world) -> None:
    """The store measures its JSON text, and the CHECK measures the jsonb
    text. A body of exactly MAX_BODY_BYTES passes both and is stored at that
    length. One byte more, and the store refuses, and so does the CHECK. So
    the two limits stay in step."""
    a = world.p.org_a
    body = _body_of_size(store.MAX_BODY_BYTES)
    assert len(store.check_body(body).encode("utf-8")) == store.MAX_BODY_BYTES
    over = {**body, "instructions": body["instructions"] + "i"}
    with pytest.raises(store.SkillStoreError):
        store.check_body(over)

    async def go() -> store.Version | None:
        skill = await _create(a)
        async with tenant_session(a) as db:
            return await store.add_version(db, ANA, skill.id, body)

    v = _go(world, go)
    assert v is not None and v.version == 1 and v.body == body
    with world.p.admin_engine.connect() as c:
        stored = c.execute(text(
            "SELECT octet_length(body::text) FROM studio_skill_versions "
            "WHERE skill_id = CAST(:i AS uuid)"), {"i": v.skill_id}).scalar_one()
    assert stored == store.MAX_BODY_BYTES
    # The CHECK refuses the next byte on its own, not only the store.
    raw = json.dumps(over, ensure_ascii=False)
    err = _refused(world, a, _RAW_VERSION, _version_params(v.skill_id, v=2, body=raw))
    assert "studio_skill_versions_body_size" in err
    assert _count(world, "studio_skill_versions") == 1


def test_r8_a_refused_body_writes_nothing(world) -> None:
    """The CHECK refuses the Python text of the grown body, so only the
    store's own refusal stands between it and a DBAPIError. Each of the three
    bodies raises a SkillStoreError before a statement. The session stays
    usable, and no version and no number moves."""
    a = world.p.org_a
    skill = _go(world, lambda: _create(a))
    raw = json.dumps(_GROWN, ensure_ascii=False)
    assert len(raw.encode("utf-8")) < store.MAX_BODY_BYTES
    err = _refused(world, a, _RAW_VERSION, _version_params(skill.id, body=raw))
    assert "studio_skill_versions_body_size" in err

    async def go() -> list[store.Skill | None]:
        after: list[store.Skill | None] = []
        for body in _JSONB_BAD.values():
            async with tenant_session(a) as db:
                with pytest.raises(store.SkillStoreError):
                    await store.add_version(db, ANA, skill.id, body)
                # A failed statement would end the transaction, and this
                # read would raise.
                after.append(await store.get_skill(db, ANA, skill.id))
        return after

    after = _go(world, go)
    assert len(after) == len(_JSONB_BAD)
    assert all(s is not None and s.current_version is None for s in after)
    assert _count(world, "studio_skill_versions") == 0


# ── d. Versions in order ───────────────────────────────────────────────────


def test_r8_versions_take_numbers_in_order_and_round_trip(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        skill = await _create(a)
        out: dict[str, Any] = {"skill": skill}
        out["v1"] = await _add(a, skill.id, note="one")
        out["v2"] = await _add(a, skill.id, note="two")
        async with tenant_session(a) as db:
            out["newest"] = await store.get_version(db, ANA, skill.id)
            out["first"] = await store.get_version(db, ANA, skill.id, 1)
            out["none"] = await store.get_version(db, ANA, skill.id, 3)
            out["after"] = await store.get_skill(db, ANA, skill.id)
            out["empty"] = await store.get_version(
                db, ANA, (await store.create_skill(db, ANA, name="Empty",
                                                   slug=_slug("empty"))).id)
        return out

    out = _go(world, go)
    assert (out["v1"].version, out["v2"].version) == (1, 2)
    assert out["newest"] == out["v2"] and out["first"] == out["v1"]
    assert out["none"] is None and out["empty"] is None
    assert out["first"].body == _body("one")
    assert out["first"].content_hash == store.content_hash(_body("one"))
    assert out["first"].created_by == ANA
    assert out["after"].current_version == 2
    assert out["after"].updated_at >= out["skill"].updated_at
    assert _count(world, "studio_skill_versions") == 2


@pytest.mark.parametrize("state", store.STATES)
def test_r8_set_state_moves_the_skill(world, state) -> None:
    async def go() -> tuple[bool, store.Skill | None]:
        skill = await _create(world.p.org_a)
        async with tenant_session(world.p.org_a) as db:
            moved = await store.set_state(db, ANA, skill.id, state)
            return moved, await store.get_skill(db, ANA, skill.id)

    moved, got = _go(world, go)
    assert moved is True and got is not None and got.state == state
    assert got.visibility == "private"


# ── e. A stored version cannot change ──────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
@pytest.mark.parametrize("assignment", [
    "body = CAST('{\"description\": \"x\", \"instructions\": \"y\"}' AS jsonb)",
    "content_hash = repeat('1', 64)",
    "created_by = 'someone@stuf1.test'",
    "version = version + 10",
])
def test_r8_a_stored_version_cannot_be_updated(world, role: str, assignment: str) -> None:
    a = world.p.org_a

    async def seed() -> store.Version:
        skill = await _create(a)
        v = await _add(a, skill.id)
        assert v is not None
        return v

    v = _go(world, seed)
    sql = (f"UPDATE studio_skill_versions SET {assignment} "
           "WHERE skill_id = CAST(:i AS uuid)")
    if role == "app":
        err = _refused(world, a, sql, {"i": v.skill_id})
    else:
        with pytest.raises(DBAPIError) as exc, world.p.admin_engine.begin() as c:
            c.execute(text(sql), {"i": v.skill_id})
        err = str(exc.value)
    assert "a stored version cannot change" in err
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT version, body, content_hash, created_by FROM studio_skill_versions "
            "WHERE skill_id = CAST(:i AS uuid)"), {"i": v.skill_id}).one()
    assert tuple(row) == (1, _body(), v.content_hash, ANA)


def test_r8_deleting_a_skill_deletes_its_versions(world) -> None:
    async def go() -> str:
        skill = await _create(world.p.org_a)
        await _add(world.p.org_a, skill.id)
        await _add(world.p.org_a, skill.id, note="two")
        return skill.id

    skill_id = _go(world, go)
    assert _count(world, "studio_skill_versions") == 2
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE id = CAST(:i AS uuid)"),
                  {"i": skill_id})
    assert _count(world, "studio_skill_versions") == 0


# ── f. Two writers ─────────────────────────────────────────────────────────


def test_r8_two_writers_at_once_get_two_numbers(world) -> None:
    """The first writer holds the row lock of the skill. The second waits for
    the commit, then takes the next number. Neither fails."""
    a = world.p.org_a
    hold = 0.6

    async def go() -> tuple[int, int, float]:
        skill = await _create(a)
        locked = asyncio.Event()

        async def first() -> int:
            async with tenant_session(a) as db:
                v = await store.add_version(db, ANA, skill.id, _body("first"))
                locked.set()
                await asyncio.sleep(hold)
            assert v is not None
            return v.version

        async def second() -> tuple[int, float]:
            await locked.wait()
            started = time.monotonic()
            async with tenant_session(a) as db:
                v = await store.add_version(db, ANA, skill.id, _body("second"))
                waited = time.monotonic() - started
            assert v is not None
            return v.version, waited

        n1, (n2, waited) = await asyncio.gather(first(), second())
        return n1, n2, waited

    n1, n2, waited = _go(world, go)
    assert (n1, n2) == (1, 2)
    # The second statement waited on the row lock of the first.
    assert waited >= hold * 0.5, waited
    assert _count(world, "studio_skill_versions") == 2


def test_r8_many_writers_at_once_get_distinct_numbers(world) -> None:
    a = world.p.org_a

    async def go() -> tuple[list[int], int | None]:
        skill = await _create(a)
        got = await asyncio.gather(*(_add(a, skill.id, note=str(i)) for i in range(5)))
        async with tenant_session(a) as db:
            after = await store.get_skill(db, ANA, skill.id)
        return sorted(v.version for v in got if v is not None), (
            None if after is None else after.current_version)

    numbers, current = _go(world, go)
    assert numbers == [1, 2, 3, 4, 5] and current == 5


# ── g. Two orgs and two members ────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_nothing_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        skill = await _create(a)
        await _add(a, skill.id)
        out: dict[str, Any] = {"skill": skill}
        async with tenant_session(b) as db:
            out["get"] = await store.get_skill(db, ANA, skill.id)
            out["version"] = await store.get_version(db, ANA, skill.id, 1)
            out["list"] = await store.list_for(db, ANA)
            out["set"] = await store.set_state(db, ANA, skill.id, "archived")
            out["add"] = await store.add_version(db, ANA, skill.id, _body("b"))
            out["raw_skills"] = (await db.execute(text(
                "SELECT count(*) FROM studio_skills WHERE owner_email = :m"),
                {"m": ANA})).scalar_one()
            out["raw_versions"] = (await db.execute(text(
                "SELECT count(*) FROM studio_skill_versions "
                "WHERE skill_id = CAST(:i AS uuid)"), {"i": skill.id})).scalar_one()
        return out

    out = _go(world, go)
    assert out["get"] is None and out["version"] is None and out["list"] == []
    assert out["set"] is False and out["add"] is None
    assert out["raw_skills"] == 0 and out["raw_versions"] == 0
    with world.p.admin_engine.connect() as c:
        state, current = c.execute(text(
            "SELECT state, current_version FROM studio_skills WHERE id = CAST(:i AS uuid)"),
            {"i": out["skill"].id}).one()
        orgs = c.execute(text(
            "SELECT DISTINCT organization_id::text FROM studio_skill_versions "
            "WHERE skill_id = CAST(:i AS uuid)"), {"i": out["skill"].id}).scalars().all()
    assert (state, current) == ("draft", 1)
    assert orgs == [a]
    assert _count(world, "studio_skill_versions") == 1


def test_r8_a_version_cannot_point_at_a_skill_of_another_org(world) -> None:
    """A foreign key check skips RLS, so the pair (org, skill) is the fence."""
    skill = _go(world, lambda: _create(world.p.org_b))
    err = _refused(world, world.p.org_a, _RAW_VERSION, _version_params(skill.id))
    assert "studio_skill_versions_skill_fk" in err
    assert _count(world, "studio_skill_versions") == 0


def test_r8_a_member_reads_and_writes_only_their_own_skills(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        skill = await _create(a, member=ANA)
        await _add(a, skill.id)
        out: dict[str, Any] = {}
        async with tenant_session(a) as db:
            out["get"] = await store.get_skill(db, BEN, skill.id)
            out["version"] = await store.get_version(db, BEN, skill.id)
            out["list"] = await store.list_for(db, BEN)
            out["set"] = await store.set_state(db, BEN, skill.id, "archived")
            out["add"] = await store.add_version(db, BEN, skill.id, _body("ben"))
            # The positive control: Ana reads her own skill and its version.
            out["own"] = await store.get_skill(db, ANA, skill.id)
            out["own_version"] = await store.get_version(db, ANA, skill.id)
        return out

    out = _go(world, go)
    assert out["get"] is None and out["version"] is None and out["list"] == []
    assert out["set"] is False and out["add"] is None
    assert out["own"] is not None and out["own"].state == "draft"
    assert out["own"].current_version == 1
    assert out["own_version"] is not None and out["own_version"].version == 1


# ── h. The member purge ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_a_member_purge_removes_the_skills_of_this_org_only(world, role: str) -> None:
    """The purge clause of ``studio_skills`` deletes the member's skills of
    THIS org, and their versions by cascade. A colleague's skills stay, and
    so do the skills of the same address in another org. The superuser run
    bypasses RLS, so only the clause's own org predicate keeps org B's row
    safe there."""
    from gateway.routes.admin.members import _PURGE_DELETES, _purge_params

    [rows] = [r for r in _PURGE_DELETES if r.table == "studio_skills"]
    a, b = world.p.org_a, world.p.org_b

    async def make(org: str, member: str, slug: str) -> str:
        skill = await _create(org, member=member, slug=slug)
        await _add(org, skill.id, member=member)
        await _add(org, skill.id, member=member, note="two")
        return skill.id

    async def seed() -> dict[str, str]:
        return {"mine": await make(a, ANA, _slug("mine")),
                "bens": await make(a, BEN, _slug("bens")),
                "theirs": await make(b, ANA, _slug("mine"))}

    ids = _go(world, seed)
    engine = world.p.app_url if role == "app" else world.p.admin_engine.url

    async def purge() -> tuple[int, int]:
        params = _purge_params(rows, uid=str(uuid.uuid4()), email=ANA)
        async with tenant_engine_scope(_dsn(engine)), tenant_session(a) as db:
            n = (await db.execute(text(rows.count_sql), params)).scalar_one()
            gone = await db.execute(text(rows.delete_sql), params)
            return int(n), int(gone.rowcount)

    counted, deleted = asyncio.run(purge())
    assert counted == deleted == 1
    with world.p.admin_engine.connect() as c:
        skills = set(c.execute(text(
            "SELECT id::text FROM studio_skills WHERE id = ANY(CAST(:i AS uuid[]))"),
            {"i": list(ids.values())}).scalars().all())
        versions = c.execute(text(
            "SELECT skill_id::text, count(*) FROM studio_skill_versions "
            "WHERE skill_id = ANY(CAST(:i AS uuid[])) GROUP BY skill_id"),
            {"i": list(ids.values())}).all()
    assert skills == {ids["bens"], ids["theirs"]}
    assert dict(versions) == {ids["bens"]: 2, ids["theirs"]: 2}
