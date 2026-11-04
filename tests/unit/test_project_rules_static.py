"""WS-ALN ALN-5a — the static fences of the rules of a project (no database).

Spec: ``project-docs/specs/project_alignment.md`` §4.2. The R8 half is
``test_project_rules.py``. These checks need no database, so they run on
every box:

* the numbered migration writes the CHECKs, the guard and the index, by
  CONTENT and never by number (R1). The R8 fixture also applies the
  generated phases, so a static check of the numbered file is the fence of
  what production gets;
* ``PROJECT_RULES_ORGS`` is owner-only, in the three places of a flag;
* the flag answers 404 and the feature gate 403, before any session opens;
* no statement of the rule store groups by a person (ALN-Q5);
* each statement of the two writers names its own ``kind``, so neither reads
  the rows of the other;
* the rule module holds no copy of the Knowledge SQL, and the quote check is
  an exact substring after one form of line end;
* the R8 suite is in the skip guard of ``pr-check.yml``.

Mutations (R7), each turns the named test red.

* S1. The migration: drop one CHECK. ``test_the_numbered_migration_writes_the_checks_the_guard_and_the_index``.
* S2. ``agent_guidance.py`` ``_LATEST_SQL``: drop ``kind = 'guidance'``. ``test_each_statement_names_its_own_kind``.
* S3. ``knowledge/core.py`` ``quote_occurs``: compare lower case. ``test_the_quote_check_is_an_exact_substring``.
* S4. ``env_guard.py``: drop ``PROJECT_RULES_ORGS``. ``test_the_flag_is_named_in_the_three_places_of_a_flag``.
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "infra" / "postgres"
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_RULES = _GATEWAY / "routes" / "project_rules.py"
_GUIDANCE = _GATEWAY / "routes" / "agent_guidance.py"

CHECKS = (
    "pm_agent_guidance_level",
    "pm_agent_guidance_state",
    "pm_agent_guidance_rule_shape",
    "pm_agent_guidance_rule_body",
    "pm_agent_guidance_quote",
    "pm_agent_guidance_quote_source",
)


def _rule_migration() -> str:
    """The migration that adds the rule CHECKs, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("[0-9]*_*.sql"))
        if "ADD CONSTRAINT pm_agent_guidance_rule_shape" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0].read_text(encoding="utf-8")


def _flat(sql: str) -> str:
    """The SQL with its comments gone and its spaces made one."""
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return " ".join(" ".join(lines).split())


def test_the_numbered_migration_writes_the_checks_the_guard_and_the_index() -> None:
    sql = _flat(_rule_migration())
    for name in CHECKS:
        # Each CHECK is added once, inside a guard on pg_constraint.
        assert f"WHERE conname = '{name}' AND conrelid = 'pm_agent_guidance'::regclass" in sql, name
        assert f"ADD CONSTRAINT {name} CHECK" in sql, name
    assert "CHECK (level IS NULL OR level IN ('soft', 'hard'))" in sql
    assert "CHECK (state IS NULL OR state IN ('proposed', 'active', 'retired'))" in sql
    assert ("CHECK (kind <> 'rule' OR (rule_key IS NOT NULL AND level IS NOT NULL "
            "AND state IS NOT NULL AND proposed_by IS NOT NULL))") in sql
    assert "CHECK (kind <> 'rule' OR char_length(body) BETWEEN 1 AND 300)" in sql
    assert "CHECK (quote IS NULL OR char_length(quote) BETWEEN 1 AND 400)" in sql
    assert ("CHECK ((quote IS NULL) = (source_page_id IS NULL) "
            "AND (quote IS NULL) = (source_page_version IS NULL))") in sql
    # The guard refuses an UPDATE of a guidance row AND of a rule row.
    assert "CREATE OR REPLACE FUNCTION pm_agent_guidance_guard_update()" in sql
    assert "IF OLD.kind = 'guidance' THEN RAISE EXCEPTION" in sql
    assert "IF OLD.kind = 'rule' THEN RAISE EXCEPTION" in sql
    assert ("CREATE INDEX IF NOT EXISTS pm_agent_guidance_rule ON pm_agent_guidance "
            "(organization_id, project_id, rule_key, version DESC) WHERE kind = 'rule'") in sql
    assert "SET LOCAL lock_timeout" in sql and sql.startswith("BEGIN;") and sql.endswith("COMMIT;")
    # Expand only (R6): no new table, no drop, no rename, no policy change.
    for banned in ("CREATE TABLE", "DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "RENAME",
                   "DROP POLICY", "CREATE POLICY", "ROW LEVEL SECURITY", "GRANT "):
        assert banned not in sql, banned


def test_the_flag_is_named_in_the_three_places_of_a_flag() -> None:
    from acb_common import env_guard
    from acb_common.settings import Settings

    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^PROJECT_RULES_ORGS=$", env, re.MULTILINE)
    assert "PROJECT_RULES_ORGS" in set(env_guard.PLATFORM_ENV_NAMES)
    assert Settings.model_fields["project_rules_orgs"].default == ""
    # A name that ends in `_orgs` reloads on SIGHUP (gateway/flag_reload.py).
    from gateway import flag_reload

    assert "project_rules_orgs".endswith("_orgs")
    assert "_orgs" in Path(flag_reload.__file__).read_text(encoding="utf-8")


def test_the_flag_is_per_org(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.routes import project_rules

    org = str(uuid.uuid4())
    monkeypatch.setattr(get_settings(), "project_rules_orgs", "")
    assert not project_rules.project_rules_allowed(org)
    monkeypatch.setattr(get_settings(), "project_rules_orgs", f"x, {org.upper()}")
    assert project_rules.project_rules_allowed(org)
    assert not project_rules.project_rules_allowed(str(uuid.uuid4()))
    monkeypatch.setattr(get_settings(), "project_rules_orgs", "*")
    assert project_rules.project_rules_allowed(org)
    assert not project_rules.project_rules_allowed(None)


def _client(perms: list[str]):
    from acb_auth import UserContext, UserRole, build_access, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import project_rules

    org = str(uuid.uuid4())
    user = UserContext(email="m@x.test", role=UserRole.EMPLOYEE,
                       access=build_access(perms), organization_id=org)
    app = FastAPI()
    app.include_router(project_rules.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), org


def _answers(client) -> list[int]:
    pid, key = str(uuid.uuid4()), str(uuid.uuid4())
    return [
        client.get(f"/project-rules/projects/{pid}").status_code,
        client.post(f"/project-rules/projects/{pid}", json={"body": "x"}).status_code,
        client.get(f"/project-rules/rules/{key}/history").status_code,
        client.post(f"/project-rules/rules/{key}/accept").status_code,
        client.patch(f"/project-rules/rules/{key}", json={"body": "x"}).status_code,
        client.post(f"/project-rules/rules/{key}/retire").status_code,
        client.post(f"/project-rules/rules/{key}/level", json={"level": "hard"}).status_code,
    ]


def test_flag_off_is_404_and_no_projects_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """No database: the flag and the feature gate answer before any session."""
    client, org = _client(["feature:projects"])
    monkeypatch.setattr(get_settings(), "project_rules_orgs", "")
    assert _answers(client) == [404] * 7
    # On, the caps answer 422 before any session opens.
    monkeypatch.setattr(get_settings(), "project_rules_orgs", org)
    pid = str(uuid.uuid4())
    assert client.post(f"/project-rules/projects/{pid}",
                       json={"body": "x" * 301}).status_code == 422
    assert client.post(f"/project-rules/projects/{pid}",
                       json={"body": "x", "quote": "q" * 401}).status_code == 422
    assert client.post(f"/project-rules/projects/{pid}",
                       json={"body": "x", "written_by": "mira"}).status_code == 422
    no_projects, org2 = _client(["feature:chat"])
    monkeypatch.setattr(get_settings(), "project_rules_orgs", org2)
    assert _answers(no_projects) == [403] * 7


def _sql_literals(path: Path) -> list[str]:
    """Each string constant of a module, an f-string as its literal parts."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def _statements(path: Path) -> list[str]:
    """The SQL of a module that reads or writes ``pm_agent_guidance``.

    A statement split over string pieces (``"... " "..."``) is one constant
    after the parse, so each piece list is one statement here.
    """
    return [s for s in _sql_literals(path)
            if re.search(r"\b(FROM|INTO|UPDATE)\s+pm_agent_guidance\b", s)]


def test_each_statement_names_its_own_kind() -> None:
    """A guidance statement names ``kind = 'guidance'`` and a rule statement
    names ``kind = 'rule'``, so no read of one writer meets a row of the
    other. R8 case 9 proves the guidance reads on a real database."""
    guidance = _statements(_GUIDANCE)
    rules = _statements(_RULES)
    assert len(guidance) == 3 and len(rules) >= 5, (len(guidance), len(rules))
    for sql in guidance:
        flat = " ".join(sql.split())
        assert "kind = 'guidance'" in flat or "'guidance', " in flat, flat
        assert "'rule'" not in flat, flat
    for sql in rules:
        flat = " ".join(sql.split())
        assert "kind = 'rule'" in flat, flat
        assert "'guidance'" not in flat, flat


def test_only_the_two_writers_write_the_table() -> None:
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+pm_agent_guidance\b",
                       re.IGNORECASE)
    found = []
    for base in (_ROOT / "apps", _ROOT / "packages", _ROOT / "scripts"):
        for path in base.rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            if write.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(path.relative_to(_ROOT).as_posix())
    assert sorted(found) == [
        "apps/services/gateway/gateway/routes/agent_guidance.py",
        "apps/services/gateway/gateway/routes/project_rules.py",
    ], found


#: A grouping of rows by a person: GROUP BY, PARTITION BY or DISTINCT ON
#: whose list names one of the two person columns.
_BY_PERSON = re.compile(
    r"\bDISTINCT\s+ON\s*\([^)]*\b(?:written_by|proposed_by)\b"
    r"|\b(?:GROUP|PARTITION)\s+BY\b"
    r"(?:(?!\b(?:ORDER|LIMIT|HAVING|WINDOW|UNION|SELECT|FROM|WHERE|RETURNING)\b)[^;)])*?"
    r"\b(?:written_by|proposed_by)\b",
    re.IGNORECASE)


def test_no_statement_of_the_rule_store_groups_by_a_person() -> None:
    """ALN-Q5: no score for any person. No statement of a file that names
    ``pm_agent_guidance`` groups, partitions or picks one row by
    ``written_by`` or ``proposed_by``."""
    files = [
        p for base in (_ROOT / "apps", _ROOT / "packages", _MIGRATIONS)
        for p in list(base.rglob("*.py")) + list(base.rglob("*.sql"))
        if "node_modules" not in p.parts and ".venv" not in p.parts
        and "pm_agent_guidance" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert _RULES in files and _GUIDANCE in files
    hits = []
    for path in files:
        for match in _BY_PERSON.finditer(path.read_text(encoding="utf-8", errors="replace")):
            hits.append((path.name, match.group(0)))
    assert hits == []
    # The fence catches a grouping, so it is not blind. A column list after
    # a DISTINCT ON of another column is not a grouping.
    assert _BY_PERSON.search("SELECT count(*) FROM pm_agent_guidance GROUP BY written_by")
    assert _BY_PERSON.search("SELECT x FROM y GROUP BY g.kind,\n g.proposed_by ORDER BY 1")
    assert _BY_PERSON.search("SELECT DISTINCT ON (g.proposed_by) g.id FROM x g")
    assert _BY_PERSON.search("count(*) OVER (PARTITION BY written_by)")
    assert not _BY_PERSON.search("SELECT DISTINCT ON (g.rule_key) g.written_by FROM x g")
    assert not _BY_PERSON.search("GROUP BY g.kind ORDER BY g.written_by")


def test_the_rule_module_holds_no_copy_of_the_knowledge_sql() -> None:
    src = _RULES.read_text(encoding="utf-8")
    for needle in ("FROM kb_", "JOIN kb_", "kb_page_versions", "kb_space_grants",
                   "visible_pages_clause", "_subject_match"):
        assert needle not in src, needle
    for helper in ("load_page", "load_space", "page_version_text", "quote_occurs",
                   "same_line_ends", "visible_page_heads"):
        assert f"kb.{helper}(" in src or f"_kb().{helper}(" in src, helper


def test_the_quote_check_is_an_exact_substring() -> None:
    from gateway.routes.knowledge import core

    body = "# Billing\r\nEach change needs a plan.\rKeep the old column."
    assert core.quote_occurs("Each change needs a plan.\nKeep", body)
    assert core.quote_occurs("needs a plan.\r\nKeep", body)
    assert not core.quote_occurs("each change needs a plan.", body), "no case fold"
    assert not core.quote_occurs("Each  change", body), "no space fold"
    assert not core.quote_occurs("Each change needs a plan!", body)
    assert not core.quote_occurs("", body)
    assert core.same_line_ends("a\r\nb\rc\n") == "a\nb\nc\n"


def test_the_page_version_cap_is_the_knowledge_cap() -> None:
    from gateway.routes import project_rules
    from gateway.routes.knowledge import core

    assert project_rules.PAGE_VERSION_MAX == core.MAX_VERSION


def test_the_router_is_mounted_and_is_not_under_projects() -> None:
    """The prefix is not ``/projects``, so ``test_projects_chat_coverage``
    asks for no manifest row. The chat tools are a follow-up slice."""
    from gateway.routes import project_rules

    assert project_rules.router.prefix == "/project-rules"
    main = (_GATEWAY / "main.py").read_text(encoding="utf-8")
    assert "from gateway.routes.project_rules import router as _project_rules_router" in main
    assert "app.include_router(_project_rules_router)" in main


def test_the_r8_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_project_rules.py" in workflow
