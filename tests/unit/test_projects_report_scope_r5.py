"""WS-27bn R5a — the role rule and the subject, on the server.

Spec: ``project-docs/specs/projects_reports.md`` §7.1 and §8 R5a, done-when
(a) to (k).

**The claim is one rule, one filter and one expansion.** ``report_scope.py``
decides who a reader may report on (§7.1), narrows each section to the tasks
that a subject holds, and removes the rows of the people the reader may not
report on AFTER each body. On a real database:

* (a) an admin gets 200 for every person and team;
* (b) lead L of team A gets 200 for team A and for its member M, and 403 for
  team B and for its member N;
* (c) a member gets 200 for themselves and 403 for a colleague, and the 403
  names the role that would allow it;
* (d) a person outside the directory gets 422, and so does a slug of another
  organization;
* (e) on an org report a member sees their own row in ``load``, ``capacity``
  and ``pulse``, and lead L sees the rows of team A;
* (f) a restricted reader's sections hold no address outside the allowed set,
  except the holders of a dependency conflict row;
* (g) ``/analytics/load`` for a member keeps their row and the unassigned row,
  and ``total_tasks`` equals the admin's;
* (h) a team report lists each member once, and a member removed from the
  team is gone on the next render;
* (i) a saved report on N leaves L's list when L leaves team B, and its render
  answers 403;
* (j) a saved row with no ``subject`` renders as it did;
* (k) the chat card and the chat text print the hidden line for ``load``,
  ``capacity`` and ``conflicts`` (the client half is in ``reportVisuals``).

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.

⚠️ The lead role is seeded here, on a scratch database only. A lead role on a
live organization is a member and role write, which is the owner's.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import analytics as ana
from gateway.routes.projects import analytics_capacity as cap_mod
from gateway.routes.projects import analytics_conflicts as conf_mod
from gateway.routes.projects import analytics_pulse as pul
from gateway.routes.projects import report_scope as scope
from gateway.routes.projects import reports as rep
from sqlalchemy import text

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


# ── Hermetic: the shape of the subject ──────────────────────────────────────


def test_a_config_with_no_subject_holds_no_subject_key() -> None:
    """(j), the shape half. A row saved before R5a normalises unchanged."""
    got = rep.normalise_report_config({"sections": ["load"]})
    assert "subject" not in got
    assert rep.normalise_report_config({"subject": None}) == rep.normalise_report_config({})


def test_the_subject_shape_is_normalised_without_the_database() -> None:
    got = rep.normalise_report_config(
        {"subject": {"kind": "person", "email": "  Ana@Example.TEST "}},
    )
    assert got["subject"] == {"kind": "person", "email": "ana@example.test"}
    got = rep.normalise_report_config({"subject": {"kind": "team", "slug": "Hardware"}})
    assert got["subject"] == {"kind": "team", "slug": "hardware"}
    # No database: the function is plain, and it awaits nothing.
    assert not inspect.iscoroutinefunction(scope.normalise_subject)
    assert "db" not in inspect.signature(scope.normalise_subject).parameters


@pytest.mark.parametrize(
    "subject",
    [
        "ana@example.test",
        {"kind": "group", "slug": "x"},
        {"kind": "person"},
        {"kind": "person", "email": "no-at-sign"},
        {"kind": "person", "email": "agent:bot@x"},
        {"kind": "team", "slug": ""},
        {"kind": "team", "slug": "a b"},
    ],
)
def test_a_bad_subject_shape_gets_422(subject: Any) -> None:
    with pytest.raises(HTTPException) as err:
        rep.normalise_report_config({"subject": subject})
    assert err.value.status_code == 422


@pytest.mark.parametrize("section", ["outlook", "hygiene"])
def test_outlook_and_hygiene_refuse_a_subject(section: str) -> None:
    with pytest.raises(HTTPException) as err:
        rep.normalise_report_config({
            "sections": ["finished", section],
            "subject": {"kind": "person", "email": "a@example.test"},
        })
    assert err.value.status_code == 422
    assert "not available for a person or team scope" in err.value.detail


def test_the_rule_names_the_role_that_would_allow_it() -> None:
    """(c), the words. The member is told which grant or role opens it."""
    member = scope.ReaderScope(everyone=False, me="m@x.test",
                               people=frozenset({"m@x.test"}), teams=frozenset())
    ok, reason = member.allows({"kind": "person", "email": "n@x.test"})
    assert not ok
    assert "admin:members:read" in reason and "lead role" in reason
    ok, reason = member.allows({"kind": "team", "slug": "b"})
    assert not ok and "lead role in that team" in reason
    assert member.allows({"kind": "person", "email": "m@x.test"}) == (True, "")
    assert member.allows(None) == (True, "")


def test_the_row_filter_keeps_the_unassigned_and_agent_rows() -> None:
    rows = [
        {"assignee": "m@x.test"}, {"assignee": "n@x.test"},
        {"assignee": None}, {"assignee": "agent:bot"},
    ]
    kept, hidden = scope.filter_person_rows(rows, frozenset({"m@x.test"}))
    assert [r["assignee"] for r in kept] == ["m@x.test", None, "agent:bot"]
    assert hidden == 1
    assert scope.filter_person_rows(rows, None) == (rows, 0)


def test_a_dependency_row_keeps_its_holders() -> None:
    rows = [
        {"kind": "blocker_late", "people": [{"email": "m@x.test"},
                                            {"email": "n@x.test"}]},
        {"kind": "parallel_person", "people": [{"email": "n@x.test"}]},
        {"kind": "parallel_person", "people": [{"email": "m@x.test"}]},
    ]
    kept, hidden = scope.filter_conflict_rows(rows, frozenset({"m@x.test"}))
    assert [r["kind"] for r in kept] == ["blocker_late", "parallel_person"]
    assert kept[1]["people"][0]["email"] == "m@x.test"
    assert hidden == 1


def test_one_home_for_the_rule() -> None:
    """A copy of the rule in a second module is a defect (§8 R5a)."""
    for module in (rep, ana, cap_mod, conf_mod, pul):
        source = inspect.getsource(module)
        assert "def may_report_on" not in source, module.__name__
        assert "def reportable_people" not in source, module.__name__
        assert "def filter_person_rows" not in source, module.__name__
        assert "org_group_member" not in source, module.__name__
    # The group reader is the one helper in routes/admin/groups.py.
    assert "org_group_member" not in inspect.getsource(scope)
    assert "active_memberships" in inspect.getsource(scope)


def test_the_row_filter_runs_after_the_bodies_and_never_in_capacity_body() -> None:
    assert "reportable_people" not in inspect.getsource(cap_mod.capacity_body)
    assert "filter_person_rows" not in inspect.getsource(cap_mod.capacity_body)
    for route in (ana.load, cap_mod.capacity, conf_mod.conflicts):
        source = inspect.getsource(route)
        assert "reportable_people(" in source, route.__name__
    assert "reportable_people(" in inspect.getsource(pul.pulse_body)


def test_the_subjects_route_sits_above_the_id_route() -> None:
    from gateway.routes.projects.core import router

    paths = [getattr(r, "path", "") for r in router.routes]
    assert paths.index("/projects/reports/subjects") < paths.index(
        "/projects/reports/{report_id}"
    )


# ── Hermetic (k): the chat prints the hidden line ───────────────────────────


@pytest.mark.parametrize(
    ("name", "section"),
    [
        ("load", {"people": [], "total_tasks": 3, "hidden_people": 2}),
        ("capacity", {"people": [], "people_total": 3, "total_tasks": 3,
                      "hr_visible": False, "hidden_people": 2}),
        ("conflicts", {"rows": [], "total": 1, "by_kind": {}, "hidden_people": 2}),
    ],
)
def test_the_chat_text_and_card_print_the_hidden_line(
    name: str, section: dict[str, Any],
) -> None:
    from skill_projects.reads import _report_section
    from skill_projects.views import _report_card_notes

    lines = _report_section(name, section)
    assert "  This report hides 2 other people" in lines, lines
    assert "This report hides 2 other people" in _report_card_notes(name, section)
    quiet = {k: v for k, v in section.items() if k != "hidden_people"}
    assert not any("hides" in line for line in _report_section(name, quiet))
    assert _report_card_notes(name, quiet) == []


# ── R8: a real Postgres, through asyncpg ─────────────────────────────────────


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
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """One organization with two teams, and a second organization.

    * ``boss`` is the admin reader.
    * Team A: ``lead`` (role ``lead``) and ``m`` (a member).
    * Team B: ``n`` (a member). ``gone`` is a departed member of team B.
    * ``m`` holds two tasks, and one task with ``n``. ``n`` holds a blocker
      that is past due and blocks a task of ``m``, and three dated tasks
      today in two top-level projects, which is a ``parallel_person`` row.
      ``lead`` holds one task. One task has no holder, and one an agent.
    * A second organization holds a team whose slug is ``other``.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    who = {k: f"{k}-{tag}@example.test"
           for k in ("boss", "lead", "m", "n", "gone", "stranger")}
    made: dict[str, Any] = {"who": who, "projects": [], "tag": tag}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        made["org_b"] = str(c.execute(
            text(
                "INSERT INTO organization (slug, display_name)"
                " VALUES (:s, :s) RETURNING id"
            ),
            {"s": f"r5b-{tag}"},
        ).scalar_one())

        users: dict[str, str] = {}
        for key in ("boss", "lead", "m", "n", "gone"):
            users[key] = str(c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, status,"
                    " organization_id) VALUES (:e, :d, :st, CAST(:o AS uuid))"
                    " RETURNING id"
                ),
                {"e": who[key], "d": f"{key.title()} {tag}",
                 "st": "removed" if key == "gone" else "active", "o": org},
            ).scalar_one())
        made["users"] = users

        def group(slug: str, org_id: str) -> str:
            return str(c.execute(
                text(
                    "INSERT INTO org_group (organization_id, slug, display_name)"
                    " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id"
                ),
                {"o": org_id, "s": slug},
            ).scalar_one())

        made["team_a"], made["team_b"] = f"ta-{tag}", f"tb-{tag}"
        made["other"] = f"ob-{tag}"
        made["groups"] = {
            "a": group(made["team_a"], org),
            "b": group(made["team_b"], org),
            "other": group(made["other"], made["org_b"]),
        }

        def member(g: str, key: str, role: str = "member") -> None:
            c.execute(
                text(
                    "INSERT INTO org_group_member (group_id, user_id, role)"
                    " VALUES (CAST(:g AS uuid), CAST(:u AS uuid), :r)"
                ),
                {"g": made["groups"][g], "u": users[key], "r": role},
            )

        member("a", "lead", "lead")
        member("a", "m")
        member("b", "n")
        member("b", "gone")

        def project(name: str) -> tuple[str, dict[str, str]]:
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n, 'active', 'manual', :me, CAST(:o AS uuid), 'UTC',"
                    " NULL, true) RETURNING id"
                ),
                {"n": f"{name}-{tag}", "me": who["boss"], "o": org},
            ).scalar_one())
            made["projects"].append(pid)
            st = {}
            for pos, (label, cat) in enumerate((("To do", "todo"),
                                                ("Doing", "in_progress"),
                                                ("Done", "done"))):
                st[cat] = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id, name, color,"
                        " position, category) VALUES (CAST(:p AS uuid), :n,"
                        " 'gray', :pos, :cat) RETURNING id"
                    ),
                    {"p": pid, "n": label, "pos": pos, "cat": cat},
                ).scalar_one())
            return pid, st

        main, st = project("scope")
        side, side_st = project("side")
        made["project"] = main

        def task(title: str, *, holders: tuple[str, ...] = (),
                 due_days: int | None = 9, start_today: bool = False,
                 pid: str = main, statuses: dict[str, str] | None = None) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at, start_date)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), :me, CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number), 0) + 1, 60,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END,"
                    " CASE WHEN CAST(:st AS boolean)"
                    "      THEN (now() AT TIME ZONE 'UTC')::date END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{title} {tag}", "p": pid, "s": (statuses or st)["todo"],
                 "me": who["boss"], "o": org, "d": due_days, "st": start_today},
            ).scalar_one())
            for h in holders:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a, :me)"
                    ),
                    {"t": tid, "a": who.get(h, h), "me": who["boss"]},
                )
            return tid

        made["m1"] = task("m one", holders=("m",))
        made["m2"] = task("m two", holders=("m",), due_days=2)
        made["mn"] = task("m and n", holders=("m", "n"))
        made["l1"] = task("lead one", holders=("lead",))
        made["u1"] = task("nobody holds", holders=())
        made["a1"] = task("agent holds", holders=("agent:bot",))
        blocker = task("n blocker", holders=("n",), due_days=-1)
        c.execute(
            text(
                "INSERT INTO pm_task_links (source_task_id, target_task_id,"
                " link_type, created_by) VALUES (CAST(:s AS uuid),"
                " CAST(:t AS uuid), 'blocks', :me)"
            ),
            {"s": blocker, "t": made["m1"], "me": who["boss"]},
        )
        # Three dated tasks today, in two top-level projects: parallel_person.
        task("n par 1", holders=("n",), due_days=2, start_today=True)
        task("n par 2", holders=("n",), due_days=2, start_today=True)
        task("n par 3", holders=("n",), due_days=2, start_today=True,
             pid=side, statuses=side_st)
    yield made
    with eng.begin() as c:
        c.execute(
            text("DELETE FROM pm_reports WHERE created_by = ANY(CAST(:w AS text[]))"),
            {"w": list(who.values())},
        )
        for pid in made["projects"]:
            c.execute(text("DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"),
                      {"p": pid})
            c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                      {"p": pid})
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": pid})
        for gid in made["groups"].values():
            c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"), {"g": gid})
        for uid in made["users"].values():
            c.execute(text("DELETE FROM app_user WHERE id = CAST(:u AS uuid)"), {"u": uid})
        c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                  {"o": made["org_b"]})
    eng.dispose()


def _user(email: str, *, admin: bool = False) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:projects"] + (["admin:members:read"] if admin else [])
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(grants))


@pytest.fixture
def wired(seeded, monkeypatch):
    """Bind the report routes and the analytics routes to one engine."""
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    vis = Visibility(unrestricted=True, email="", groups=(),
                     organization_id=seeded["org"])

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _resolve(_db: Any, _user: Any) -> Any:
        return vis

    for module in (rep, ana, cap_mod, conf_mod):
        monkeypatch.setattr(module, "_tenant_session", _session)
        monkeypatch.setattr(module, "resolve_visibility", _resolve)
    yield {"engine": eng, "vis": vis}
    asyncio.run(eng.dispose())


def _sql(seeded: dict[str, Any], statement: str, **params: Any) -> None:
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as c:
        c.execute(text(statement), params)
    eng.dispose()


def _person(seeded: dict[str, Any], key: str) -> dict[str, str]:
    return {"kind": "person", "email": seeded["who"][key]}


def _team(seeded: dict[str, Any], key: str) -> dict[str, str]:
    return {"kind": "team", "slug": seeded[key]}


def _preview(seeded: dict[str, Any], reader: str, *, admin: bool = False,
             subject: Any = None, sections: list[str] | None = None,
             project: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"sections": sections or ["load"]}
    if subject is not None:
        config["subject"] = subject
    return asyncio.run(rep.preview_report(
        {"project_id": project, "config": config},
        user=_user(seeded["who"][reader], admin=admin),
    ))


def _status(fn: Any) -> int:
    try:
        fn()
    except HTTPException as err:
        return err.status_code
    return 200


def _people(section: dict[str, Any]) -> list[str | None]:
    return [r["assignee"] for r in section.get("people", section.get("rows", []))]


#: Every section except the two that refuse a subject.
_WITH_SUBJECT = [s for s in rep.SECTIONS if s not in scope.NO_SUBJECT_SECTIONS]


@_needs_db
def test_a_an_admin_may_report_on_every_person_and_team(seeded, wired) -> None:
    """(a) Save and render, for each person and each team."""
    for subject in (_person(seeded, "m"), _person(seeded, "n"),
                    _person(seeded, "lead"), _team(seeded, "team_a"),
                    _team(seeded, "team_b")):
        # Every section that takes a subject, so each subject clause runs.
        body = _preview(seeded, "boss", admin=True, subject=subject,
                        sections=_WITH_SUBJECT)
        assert set(body["sections"]) == set(_WITH_SUBJECT), subject
        saved = asyncio.run(rep.create_report(
            {"name": "r", "config": {"sections": ["load"], "subject": subject}},
            user=_user(seeded["who"]["boss"], admin=True),
        ))
        assert saved["config"]["subject"] == subject
        body = asyncio.run(rep.render_report(
            saved["id"], user=_user(seeded["who"]["boss"], admin=True),
        ))
        assert "load" in body["sections"]


@_needs_db
def test_b_a_lead_may_report_on_their_team_and_its_members_only(seeded, wired) -> None:
    """(b)."""
    def lead(subject: Any) -> int:
        return _status(lambda: _preview(seeded, "lead", subject=subject))

    assert lead(_team(seeded, "team_a")) == 200
    assert lead(_person(seeded, "m")) == 200
    assert lead(_person(seeded, "lead")) == 200
    assert lead(_team(seeded, "team_b")) == 403
    assert lead(_person(seeded, "n")) == 403


@_needs_db
def test_c_a_member_may_report_on_themselves_only(seeded, wired) -> None:
    """(c) The 403 names the grant and the role that would allow it."""
    assert _status(lambda: _preview(seeded, "m", subject=_person(seeded, "m"))) == 200
    with pytest.raises(HTTPException) as err:
        _preview(seeded, "m", subject=_person(seeded, "n"))
    assert err.value.status_code == 403
    assert "admin:members:read" in err.value.detail
    assert "lead role" in err.value.detail
    with pytest.raises(HTTPException) as err:
        asyncio.run(rep.create_report(
            {"name": "r", "config": {"subject": _person(seeded, "n")}},
            user=_user(seeded["who"]["m"]),
        ))
    assert err.value.status_code == 403
    assert _status(lambda: _preview(seeded, "m", subject=_team(seeded, "team_a"))) == 403


@_needs_db
def test_d_a_subject_outside_the_directory_gets_422(seeded, wired) -> None:
    """(d) An unknown address, a departed member, and another org's slug."""
    for subject in (
        _person(seeded, "stranger"),
        _person(seeded, "gone"),
        _team(seeded, "other"),
    ):
        assert _status(lambda s=subject: _preview(seeded, "boss", admin=True,
                                                  subject=s)) == 422, subject
        with pytest.raises(HTTPException) as err:
            asyncio.run(rep.create_report(
                {"name": "r", "config": {"subject": subject}},
                user=_user(seeded["who"]["boss"], admin=True),
            ))
        assert err.value.status_code == 422


@_needs_db
def test_e_an_org_report_shows_a_reader_the_rows_they_may_report_on(seeded, wired) -> None:
    """(e) The member sees their row, and the lead sees team A."""
    who = seeded["who"]
    sections = ["load", "capacity", "pulse"]
    admin = _preview(seeded, "boss", admin=True, sections=sections,
                     project=seeded["project"])["sections"]
    member = _preview(seeded, "m", sections=sections,
                      project=seeded["project"])["sections"]
    lead = _preview(seeded, "lead", sections=sections,
                    project=seeded["project"])["sections"]
    everyone = {who["m"], who["n"], who["lead"]}
    assert set(_people(admin["load"])) >= everyone
    assert "hidden_people" not in admin["load"]
    assert "hidden_people" not in admin["capacity"]
    assert admin["pulse"]["hidden_people"] == 0

    for name in sections:
        rows = {p for p in _people(member[name]) if p and not p.startswith("agent:")}
        assert rows == {who["m"]}, (name, rows)
        assert member[name]["hidden_people"] == 2, name
        rows = {p for p in _people(lead[name]) if p and not p.startswith("agent:")}
        assert rows == {who["m"], who["lead"]}, (name, rows)
        assert lead[name]["hidden_people"] == 1, name
    # The unassigned row and the agent row are not people, and they stay.
    assert None in _people(member["load"]) and "agent:bot" in _people(member["load"])
    # Totals count every person.
    assert member["load"]["total_tasks"] == admin["load"]["total_tasks"]
    assert member["capacity"]["people_total"] == admin["capacity"]["people_total"]
    assert member["pulse"]["people_total"] == admin["pulse"]["people_total"]


@_needs_db
def test_f_a_restricted_body_holds_no_other_address(seeded, wired) -> None:
    """(f) Every section a member may ask for, as JSON. Dependency conflict
    rows keep their holders, and they are the one exception."""
    who = seeded["who"]
    body = _preview(
        seeded, "m",
        sections=["finished", "throughput", "load", "capacity", "pulse",
                  "stuck", "conflicts", "rebalance"],
    )
    sections = body["sections"]
    kinds = [r["kind"] for r in sections["conflicts"]["rows"]]
    assert "blocker_late" in kinds, kinds
    admin = _preview(seeded, "boss", admin=True, sections=["conflicts"])
    assert any(r["kind"] == "parallel_person"
               for r in admin["sections"]["conflicts"]["rows"])
    assert sections["conflicts"]["hidden_people"] >= 1
    assert "parallel_person" not in kinds
    sections["conflicts"]["rows"] = [
        r for r in sections["conflicts"]["rows"]
        if r["kind"] not in scope.DEPENDENCY_KINDS
    ]
    dumped = json.dumps(body)
    for other in ("n", "lead", "boss", "gone"):
        assert who[other] not in dumped, other
        assert f"{other.title()} {seeded['tag']}" not in dumped, other
    assert who["m"] in dumped


@_needs_db
def test_g_the_analytics_load_route_follows_the_reader(seeded, wired) -> None:
    """(g) The Analytics panel and the report read one filter."""
    who = seeded["who"]
    member = asyncio.run(ana.load(project_id=seeded["project"], include_subtree=True,
                                  user=_user(who["m"])))
    admin = asyncio.run(ana.load(project_id=seeded["project"], include_subtree=True,
                                 user=_user(who["boss"], admin=True)))
    people = [p["assignee"] for p in member["people"]]
    assert who["m"] in people and None in people
    assert who["n"] not in people and who["lead"] not in people
    assert member["hidden_people"] == 2
    assert member["total_tasks"] == admin["total_tasks"]
    assert member["people_total"] == admin["people_total"]
    assert "hidden_people" not in admin
    # The capacity and conflicts routes filter the same way.
    cap = asyncio.run(cap_mod.capacity(project_id=seeded["project"],
                                       user=_user(who["m"])))
    assert {r["assignee"] for r in cap["rows"] if r["kind"] == "person"} == {who["m"]}
    assert cap["hidden_people"] == 2
    conf = asyncio.run(conf_mod.conflicts(project_id=None, user=_user(who["m"])))
    assert all(r["kind"] != "parallel_person" for r in conf["rows"])
    assert conf["hidden_people"] >= 1


@_needs_db
def test_h_a_team_lists_each_member_once_and_follows_the_team(seeded, wired) -> None:
    """(h) The team expands at render time."""
    who = seeded["who"]
    got = _preview(seeded, "boss", admin=True, subject=_team(seeded, "team_a"),
                   sections=["load", "pulse"], project=seeded["project"])["sections"]
    people = [p for p in _people(got["load"]) if p and not p.startswith("agent:")]
    assert sorted(people) == sorted([who["m"], who["lead"]])
    assert sorted(_people(got["pulse"])) == sorted([who["m"], who["lead"]])
    # `mn` is a task of m that n also holds. n is not in the team.
    assert who["n"] not in json.dumps(got["load"])

    _sql(seeded, "DELETE FROM org_group_member WHERE group_id = CAST(:g AS uuid)"
                 " AND user_id = CAST(:u AS uuid)",
         g=seeded["groups"]["a"], u=seeded["users"]["m"])
    again = _preview(seeded, "boss", admin=True, subject=_team(seeded, "team_a"),
                     sections=["load"], project=seeded["project"])["sections"]
    assert who["m"] not in _people(again["load"])
    assert who["lead"] in _people(again["load"])


@_needs_db
def test_i_a_saved_report_leaves_when_the_lead_leaves_the_team(seeded, wired) -> None:
    """(i) The list hides it, and the render, get and delete answer 403."""
    who = seeded["who"]
    lead = _user(who["lead"])
    _sql(seeded, "INSERT INTO org_group_member (group_id, user_id, role)"
                 " VALUES (CAST(:g AS uuid), CAST(:u AS uuid), 'lead')",
         g=seeded["groups"]["b"], u=seeded["users"]["lead"])
    saved = asyncio.run(rep.create_report(
        {"name": "on n", "config": {"sections": ["load"],
                                    "subject": _person(seeded, "n")}},
        user=lead,
    ))
    listed = asyncio.run(rep.list_reports(user=lead))["reports"]
    assert saved["id"] in {r["id"] for r in listed}
    asyncio.run(rep.render_report(saved["id"], user=lead))

    _sql(seeded, "DELETE FROM org_group_member WHERE group_id = CAST(:g AS uuid)"
                 " AND user_id = CAST(:u AS uuid)",
         g=seeded["groups"]["b"], u=seeded["users"]["lead"])
    listed = asyncio.run(rep.list_reports(user=lead))["reports"]
    assert saved["id"] not in {r["id"] for r in listed}
    for call in (rep.render_report, rep.get_report, rep.delete_report):
        with pytest.raises(HTTPException) as err:
            asyncio.run(call(saved["id"], user=lead))
        assert err.value.status_code == 403, call.__name__
        assert "lead role" in err.value.detail
    # The admin still lists it.
    admin = asyncio.run(rep.list_reports(user=_user(who["boss"], admin=True)))
    assert saved["id"] in {r["id"] for r in admin["reports"]}


@_needs_db
def test_j_a_saved_row_with_no_subject_renders_as_before(seeded, wired) -> None:
    """(j) An admin's render of a row saved before R5a carries no new key,
    and its `load` rows are Load's own query, as before."""
    _sql(seeded, "INSERT INTO pm_reports (project_id, organization_id, name, config,"
                 " created_by) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'old',"
                 " CAST(:c AS jsonb), :by)",
         p=seeded["project"], o=seeded["org"], by=seeded["who"]["boss"],
         c=json.dumps({"sections": ["load", "capacity", "conflicts"]}))
    admin = _user(seeded["who"]["boss"], admin=True)
    listed = asyncio.run(rep.list_reports(user=admin))["reports"]
    old = next(r for r in listed if r["name"] == "old"
               and r["project_id"] == seeded["project"])
    assert "subject" not in old["config"]
    body = asyncio.run(rep.render_report(old["id"], user=admin))
    for name in ("load", "capacity", "conflicts"):
        assert "hidden_people" not in body["sections"][name], name
    assert set(body["sections"]["load"]) == {"people", "total_tasks"}

    async def direct() -> list[Any]:
        async with wired["engine"].connect() as db:
            where = ana.load_open_where(
                await ana.scope_clause(db, wired["vis"], seeded["project"], True),
                wired["vis"],
            )
            return (await db.execute(
                text(ana.load_sql(where)), ana.load_params(wired["vis"], seeded["project"]),
            )).fetchall()
    rows = asyncio.run(direct())
    want = [
        {"assignee": r.who or None, "open_tasks": int(r.open_tasks),
         "overdue": int(r.overdue), "due_next_7d": int(r.due_next_7d),
         "later": int(r.later)}
        for r in rows
    ][:ana.MAX_PEOPLE]
    assert body["sections"]["load"]["people"] == want


@_needs_db
def test_the_subjects_route_lists_what_the_rule_allows(seeded, wired) -> None:
    """The picker lists what `may_report_on` allows, and nothing else."""
    who = seeded["who"]

    def subjects(key: str, admin: bool = False) -> dict[str, Any]:
        return asyncio.run(rep.list_report_subjects(user=_user(who[key], admin=admin)))

    admin = subjects("boss", admin=True)
    emails = {p["email"] for p in admin["people"]}
    assert {who["m"], who["n"], who["lead"], who["boss"]} <= emails
    assert who["gone"] not in emails
    slugs = {t["slug"] for t in admin["teams"]}
    assert {seeded["team_a"], seeded["team_b"]} <= slugs
    assert seeded["other"] not in slugs

    lead = subjects("lead")
    assert {p["email"] for p in lead["people"]} == {who["lead"], who["m"]}
    assert [t["slug"] for t in lead["teams"]] == [seeded["team_a"]]

    member = subjects("m")
    assert {p["email"] for p in member["people"]} == {who["m"]}
    assert member["teams"] == [] and member["everyone"] is False
