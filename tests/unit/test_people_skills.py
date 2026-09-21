"""WS-28h — structured skills & credentials, and the D-PC-6 projection.

Spec: `project-docs/specs/people_center_app.md` §3.3 · **D-PC-6**.

Three claims:

* **The array is a projection of the table, rewritten in the same transaction
  by every write path.** Four live consumers read `people.skills[]` and R6
  forbids breaking them; the fence is these tests asserting array == table
  after each path — replace, flat sync, résumé merge — never a paragraph
  asking people to remember.
* **The vocabularies mirror migration 176's CHECKs**, so a bad value is a 400
  naming the legal words rather than a constraint string at 3am.
* **Two doors, one field class.** Structured skills are authorized as a write
  of `skills` — the same question the flat PATCH answers, asked of the same
  authority.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway import person_skills as ps
from gateway.routes.people import core as people_core
from gateway.routes.people import selfservice as people_self
from gateway.routes.people import skills as people_skills
from gateway.routes.tasks import resume_parse
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
MIGRATION = (REPO / "infra" / "postgres" / "176_people_skills.sql").read_text(
    encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
# 1. The vocabularies mirror the CHECKs
# ══════════════════════════════════════════════════════════════════════════

def test_skill_levels_mirror_the_migration() -> None:
    for level in ps.SKILL_LEVELS:
        assert f"'{level}'" in MIGRATION, level
    # …and the CHECK admits nothing the tuple does not.
    check = re.search(r"level IN \(([^)]+)\)", MIGRATION.replace("\n", " "))
    words = set(re.findall(r"'(\w+)'", check.group(1)))
    assert words == set(ps.SKILL_LEVELS)


def test_evidence_mirrors_the_migration() -> None:
    check = re.search(r"evidence IN \(([^)]+)\)", MIGRATION)
    words = set(re.findall(r"'(\w+)'", check.group(1)))
    assert words == set(ps.EVIDENCE)


def test_manual_is_the_evidence_word_the_existing_map_uses() -> None:
    """The projection writes `evidence` verbatim into `skills_source`, whose
    live values are 'manual' and 'resume' (written since WS-24). A new synonym
    — 'stated' — would split one fact across two spellings."""
    assert "manual" in ps.EVIDENCE
    assert "resume" in ps.EVIDENCE


def test_credential_kinds_mirror_the_migration() -> None:
    check = re.search(r"kind IN \(([^)]+)\)", MIGRATION.replace("\n", " "))
    words = set(re.findall(r"'(\w+)'", check.group(1)))
    assert words == set(ps.CREDENTIAL_KINDS)


def test_the_new_tables_are_tenant_scoped_on_day_one() -> None:
    """R5, and the WS-28k lesson kept: REFERENCES precedes DEFAULT with no
    comma between, or the ratchet reads a scoped table as unscoped."""
    for _ in re.finditer(r"organization_id\s+UUID NOT NULL REFERENCES "
                         r"organization", MIGRATION):
        break
    else:
        raise AssertionError("organization_id not declared with its FK")
    assert MIGRATION.count("current_setting('app.tenant_id', true)") == 2


# ══════════════════════════════════════════════════════════════════════════
# 2. Validation — refusals in words
# ══════════════════════════════════════════════════════════════════════════

def skill(**over: Any) -> dict[str, Any]:
    return {"skill": "python", "level": None, "years": None,
            "last_used_year": None, "evidence": None, **over}


def test_an_unknown_level_is_refused_naming_it() -> None:
    with pytest.raises(HTTPException) as exc:
        ps.validate_skills([skill(level="wizard")])
    assert exc.value.status_code == 400
    assert "wizard" in exc.value.detail and "learning" in exc.value.detail


def test_impossible_years_are_refused() -> None:
    for years in (-1, 61, "many"):
        with pytest.raises(HTTPException):
            ps.validate_skills([skill(years=years)])


def test_a_fake_year_is_refused() -> None:
    for year in (1969, 2101, "recently"):
        with pytest.raises(HTTPException):
            ps.validate_skills([skill(last_used_year=year)])


def test_a_duplicate_skill_is_refused_by_name_not_by_constraint() -> None:
    """The UNIQUE index folds case; so does this — and it answers before
    Postgres does, with the name instead of a constraint string."""
    with pytest.raises(HTTPException) as exc:
        ps.validate_skills([skill(skill="Python"), skill(skill="python")])
    assert "python" in exc.value.detail.lower()
    assert "twice" in exc.value.detail


def test_a_nameless_skill_is_refused() -> None:
    with pytest.raises(HTTPException):
        ps.validate_skills([skill(skill="   ")])


def test_an_absurd_payload_reads_as_an_import_mistake() -> None:
    rows = [skill(skill=f"s{i}") for i in range(ps.MAX_SKILLS + 1)]
    with pytest.raises(HTTPException) as exc:
        ps.validate_skills(rows)
    assert "import" in exc.value.detail


def test_unset_evidence_defaults_to_manual() -> None:
    assert ps.validate_skills([skill()])[0]["evidence"] == "manual"


def test_a_credential_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(HTTPException) as exc:
        ps.validate_credentials([{"kind": "education", "title": "BTech",
                                  "year_from": 2020, "year_to": 2016}])
    assert "before it starts" in exc.value.detail


def test_an_unknown_credential_kind_is_refused_naming_the_three() -> None:
    with pytest.raises(HTTPException) as exc:
        ps.validate_credentials([{"kind": "award", "title": "X"}])
    assert "education" in exc.value.detail


# ══════════════════════════════════════════════════════════════════════════
# 3. The projection — array == table, same transaction (D-PC-6)
# ══════════════════════════════════════════════════════════════════════════

class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    """A stateful stand-in for the two tables and the projection target.

    It executes the module's actual SQL shapes against in-memory lists, so the
    assertion "the projected array equals the table" is about the CODE's
    sequencing, not about SQL semantics — those are the live harness's job
    (R8), and a fake that tried to answer them would agree with anything.
    """

    def __init__(self):
        self.skills: list[dict[str, Any]] = []      # insertion order
        self.credentials: list[dict[str, Any]] = []
        self.projected: dict[str, Any] | None = None
        self.statements: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        p = dict(params or {})
        self.statements.append(s)
        if s.startswith("DELETE FROM people_skills"):
            if "= ANY(:gone)" in s:
                gone = set(p["gone"])
                self.skills = [r for r in self.skills
                               if r["skill"].lower() not in gone]
            else:
                self.skills = []
            return _Result([])
        if s.startswith("INSERT INTO people_skills"):
            row = {k: p.get(k) for k in (
                "skill", "level", "years", "last_used_year", "evidence")}
            if ":evidence" not in s:
                # sync/merge write the evidence as a SQL literal, not a bind.
                literal = re.search(r"VALUES .*'(\w+)'", s)
                row["evidence"] = literal.group(1) if literal else "manual"
            self.skills.append(row)
            return _Result([])
        if s.startswith("DELETE FROM people_credentials"):
            self.credentials = []
            return _Result([])
        if s.startswith("INSERT INTO people_credentials"):
            self.credentials.append(dict(p))
            return _Result([])
        if "FROM people_skills" in s:
            if "lower(skill) AS key" in s:
                return _Result([SimpleNamespace(key=r["skill"].lower())
                                for r in self.skills])
            return _Result([
                SimpleNamespace(id=f"row-{i}", **r)
                for i, r in enumerate(self.skills)])
        if "FROM people_credentials" in s:
            if "lower(title)" in s:
                return _Result([SimpleNamespace(
                    kind=r["kind"], title=r["title"].lower(),
                    issuer=(r.get("issuer") or "").lower())
                    for r in self.credentials])
            return _Result([
                SimpleNamespace(id=f"cred-{i}", source="manual", **r)
                for i, r in enumerate(self.credentials)])
        if s.startswith("UPDATE people SET skills"):
            self.projected = {"skills": p["skills"], "source": p["source"]}
            return _Result([])
        return _Result([])

    async def commit(self) -> None:
        return None

    def table_names(self) -> list[str]:
        return [r["skill"] for r in self.skills]


def assert_projection_matches(db: FakeDB) -> None:
    """THE fence: what landed on people is exactly the table's content."""
    import json
    assert db.projected is not None, "no projection was written"
    assert db.projected["skills"] == db.table_names()
    assert json.loads(db.projected["source"]) == {
        r["skill"]: r["evidence"] for r in db.skills}


def test_replace_projects_the_array_in_the_same_call() -> None:
    db = FakeDB()
    run(ps.replace_skills(db, "p1", [
        skill(skill="python", level="expert", years=8),
        skill(skill="altium", level="working"),
    ], "priya@x"))
    assert db.table_names() == ["python", "altium"]
    assert_projection_matches(db)


def test_sync_from_array_keeps_the_structured_row_for_a_retained_skill() -> None:
    """The two-door defect this exists to prevent: a flat save through the
    older door must never strip the level somebody set through the newer one."""
    db = FakeDB()
    run(ps.replace_skills(db, "p1", [
        skill(skill="python", level="expert", years=8),
        skill(skill="altium", level="working"),
    ], "priya@x"))
    run(ps.sync_from_array(db, "p1", ["python", "kicad"], "priya@x"))
    by_name = {r["skill"]: r for r in db.skills}
    assert set(by_name) == {"python", "kicad"}
    assert by_name["python"]["level"] == "expert"      # survived the flat save
    assert by_name["kicad"]["level"] is None           # new, unassessed
    assert by_name["kicad"]["evidence"] == "manual"
    assert_projection_matches(db)


def test_sync_from_array_dedupes_case_insensitively() -> None:
    db = FakeDB()
    run(ps.sync_from_array(db, "p1", ["Python", "python", " python "], "a@x"))
    assert db.table_names() == ["Python"]
    assert_projection_matches(db)


def test_resume_merge_adds_and_never_removes() -> None:
    """A résumé is evidence for what it contains and silent about everything
    else — somebody's Rust does not disappear because their CV predates it."""
    db = FakeDB()
    run(ps.replace_skills(db, "p1", [
        skill(skill="rust", level="proficient"),
    ], "priya@x"))
    added = run(ps.merge_from_resume(db, "p1", ["python", "rust"], [], "a@x"))
    assert added == ["python"]
    by_name = {r["skill"]: r for r in db.skills}
    assert by_name["rust"]["level"] == "proficient"    # untouched by re-parse
    assert by_name["python"]["evidence"] == "resume"
    assert_projection_matches(db)


def test_resume_credentials_are_deduplicated_not_doubled() -> None:
    """Re-uploading a CV must not double a degree."""
    db = FakeDB()
    cred = {"kind": "education", "title": "BTech", "issuer": "IIT",
            "year_from": 2012, "year_to": 2016}
    run(ps.merge_from_resume(db, "p1", [], [cred], "a@x"))
    run(ps.merge_from_resume(db, "p1", [], [dict(cred)], "a@x"))
    assert len(db.credentials) == 1


def test_every_write_path_ends_in_exactly_one_projection() -> None:
    """Not two — a second UPDATE of the array in one call would mean two
    authorities racing inside one transaction."""
    for call in (
        lambda db: ps.replace_skills(db, "p1", [skill()], "a@x"),
        lambda db: ps.sync_from_array(db, "p1", ["python"], "a@x"),
        lambda db: ps.merge_from_resume(db, "p1", ["python"], [], "a@x"),
    ):
        db = FakeDB()
        run(call(db))
        writes = [s for s in db.statements
                  if s.startswith("UPDATE people SET skills")]
        assert len(writes) == 1, db.statements


# ══════════════════════════════════════════════════════════════════════════
# 4. The doors
# ══════════════════════════════════════════════════════════════════════════

PERSON = SimpleNamespace(id="11111111-1111-1111-1111-111111111111",
                         email="priya@fracktal.in")


class RouteDB(FakeDB):
    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        if hits(s, "FROM people"):
            self.statements.append(s)
            wanted = (params or {}).get("email")
            if wanted is not None and wanted != PERSON.email:
                return _Result([])
            return _Result([PERSON])
        return await super().execute(sql, params)


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    for module in (people_core, people_self, people_skills):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)


def _user(email: str | None, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


SUBJECT = _user("priya@fracktal.in")                    # no grants at all
ADMIN = _user("admin@fracktal.in", "feature:people", "admin:members:manage",
              "admin:members:read")
STRANGER = _user("someone@fracktal.in", "feature:people")


def test_the_subject_edits_their_own_through_the_ungated_door(monkeypatch) -> None:
    db = RouteDB()
    bind(monkeypatch, db)
    out = run(people_self.put_my_skills(
        people_skills.SkillsWrite(rows=[people_skills.SkillIn(
            skill="python", level="expert")]), user=SUBJECT))
    assert [r["skill"] for r in out["skills"]] == ["python"]
    assert_projection_matches(db)


def test_a_stranger_may_not_write_somebody_elses(monkeypatch) -> None:
    db = RouteDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_skills.put_skills(
            str(PERSON.id),
            people_skills.SkillsWrite(rows=[people_skills.SkillIn(skill="x")]),
            user=STRANGER))
    assert exc.value.status_code == 403
    assert db.skills == []                    # refused whole, nothing applied


def test_a_stranger_may_not_read_the_hr_tier(monkeypatch) -> None:
    db = RouteDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_skills.get_skills(str(PERSON.id), user=STRANGER))
    assert exc.value.status_code == 403
    assert "admin:members:read" in exc.value.detail


def test_the_admin_may_write_anybodys(monkeypatch) -> None:
    db = RouteDB()
    bind(monkeypatch, db)
    out = run(people_skills.put_skills(
        str(PERSON.id),
        people_skills.SkillsWrite(rows=[people_skills.SkillIn(skill="sql")]),
        user=ADMIN))
    assert [r["skill"] for r in out["skills"]] == ["sql"]


def test_credentials_take_the_same_field_class(monkeypatch) -> None:
    db = RouteDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_skills.put_credentials(
            str(PERSON.id),
            people_skills.CredentialsWrite(rows=[people_skills.CredentialIn(
                kind="education", title="BTech")]), user=STRANGER))
    assert exc.value.status_code == 403


def test_the_payload_carries_the_vocabularies(monkeypatch) -> None:
    """So the editor renders selects without a second round trip — and without
    a client-side copy that could drift (the D-PC-4 shape)."""
    db = RouteDB()
    bind(monkeypatch, db)
    out = run(people_skills.get_skills(str(PERSON.id), user=ADMIN))
    assert out["levels"] == list(ps.SKILL_LEVELS)
    assert out["credential_kinds"] == list(ps.CREDENTIAL_KINDS)


# ══════════════════════════════════════════════════════════════════════════
# 5. The parser's credential coercion
# ══════════════════════════════════════════════════════════════════════════

def test_clean_credentials_keeps_the_usable_and_drops_the_rest() -> None:
    out = resume_parse._clean_credentials([
        {"kind": "education", "title": "BTech Mechatronics", "issuer": "MIT",
         "year_from": 2012, "year_to": 2016},
        {"kind": "prior_role", "title": "Firmware Lead", "issuer": "Acme",
         "year_from": "notayear", "year_to": None},
        {"kind": "award", "title": "Employee of the month"},   # unknown kind
        {"kind": "education", "title": ""},                    # no title
        "not even a dict",
    ])
    assert [c["title"] for c in out] == ["BTech Mechatronics", "Firmware Lead"]
    assert out[1]["year_from"] is None      # a hallucinated year costs itself


def test_parse_degrades_to_no_credentials_without_the_llm() -> None:
    """The keyword pass cannot know a degree from a job title, and an empty
    list on LLM failure degrades exactly like the rest of the profile half."""
    parsed = run(resume_parse.parse_resume(
        b"python and altium designer", "cv.txt", "text/plain", []))
    assert parsed["credentials"] == []
    assert "python" in parsed["skills"]
