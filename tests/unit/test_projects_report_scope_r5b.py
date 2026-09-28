"""WS-27bn R5b-1 — T2 and T6 live, T1 takes a team, and the subject rules.

Spec: ``project-docs/specs/projects_reports.md`` §8 R5b, done-when (e) to
(j). The client half, (a) to (d), is in ``reportBuilder.test.ts``.

**The claim.** ``my_day`` (T2) and ``one_on_one`` (T6) are about one person.
``normalise_report_config`` refuses either with no person subject, from the
shape alone. ``require_template_subject`` refuses a T2 whose subject is not
the reader, on create, patch and preview, before the §7.1 check. On a real
database:

* (e) create, patch and preview of T2 or T6 with no subject, or with a team,
  each get 422;
* (f) a member creates T2 on themselves, and an admin gets 422 for T2 on
  another person;
* (g) lead L creates T6 on M, and gets 403 with the §7.1 reason for N;
* (h) T1 with the subject team A renders for L, and a member gets 403;
* (i) the live templates are exactly six (hermetic);
* (j) the subject of a saved T2 sees their waiting items, and an admin who
  renders the same row sees none.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.

⚠️ The people, the teams and the lead role come from the R5a fixture, seeded
on a scratch database only. A lead role on a live organization is a member
and role write, which is the owner's.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import reports as rep

# The R5a fixtures: one organization, two teams, a lead, and a real engine.
from tests.unit.test_projects_report_scope_r5 import (  # noqa: F401
    _ladder,
    _needs_db,
    _person,
    _sql,
    _team,
    _user,
    seeded,
    wired,
)

_T2 = "my_day"
_T6 = "one_on_one"


def _t_config(template: str, subject: Any = None) -> dict[str, Any]:
    """The config the builder sends for a template: its preset, and a subject."""
    t = rep.TEMPLATES[template]
    config: dict[str, Any] = {
        "template": template,
        "sections": list(t["sections"]),
        "weeks": t["weeks"],
        "skip_current_week": t["skip_current_week"],
    }
    if subject is not None:
        config["subject"] = subject
    return config


def _status(fn: Any) -> tuple[int, str]:
    try:
        fn()
    except HTTPException as err:
        return err.status_code, str(err.detail)
    return 200, ""


# ── Hermetic: the catalogue and the shape rule ──────────────────────────────


def test_i_the_live_templates_are_exactly_six() -> None:
    """(i)."""
    live = [k for k, t in rep.TEMPLATES.items() if t["available"]]
    assert live == [
        "team_pulse", "my_day", "weekly_delivery", "project_status",
        "one_on_one", "data_hygiene",
    ]


def test_t2_t6_and_t1_carry_the_spec_preset() -> None:
    t2, t6, t1 = (rep.TEMPLATES[k] for k in (_T2, _T6, "team_pulse"))
    assert t2["sections"] == ["pulse"]
    assert (t2["weeks"], t2["skip_current_week"]) == (1, False)
    assert t2["scope_kinds"] == ["person"] and t2["requires_subject"] == "self"
    assert t6["sections"] == ["finished", "throughput", "pulse"]
    assert (t6["weeks"], t6["skip_current_week"]) == (4, True)
    assert t6["scope_kinds"] == ["person"] and t6["requires_subject"] == "person"
    assert t1["scope_kinds"] == ["team", "project", "org"]
    assert "requires_subject" not in t1
    for t in (t2, t6):
        assert "waits_for" not in t
    # Every scope kind is a subject kind or a node kind. `me` is not one.
    for t in rep.TEMPLATES.values():
        assert set(t["scope_kinds"]) <= {"person", "team", "project", "org"}, t["key"]


@pytest.mark.parametrize("template", [_T2, _T6])
@pytest.mark.parametrize(
    "subject", [None, {"kind": "team", "slug": "hardware"}],
)
def test_e_the_shape_rule_needs_a_person(template: str, subject: Any) -> None:
    """(e), the shape half. No database: the rule reads the config only."""
    with pytest.raises(HTTPException) as err:
        rep.normalise_report_config(_t_config(template, subject))
    assert err.value.status_code == 422
    assert "Choose a person" in err.value.detail


def test_the_shape_rule_passes_a_person() -> None:
    got = rep.normalise_report_config(
        _t_config(_T6, {"kind": "person", "email": "a@x.test"}),
    )
    assert got["template"] == _T6
    assert got["subject"] == {"kind": "person", "email": "a@x.test"}


def test_the_self_rule_needs_the_reader_and_never_the_database() -> None:
    """The T2 rule reads the reader, so it is NOT in the shape rule, and it
    awaits nothing."""
    assert not inspect.iscoroutinefunction(rep.require_template_subject)
    assert "require_template_subject(" not in inspect.getsource(
        rep.normalise_report_config,
    )
    me = _user("me@x.test")
    config = rep.normalise_report_config(
        _t_config(_T2, {"kind": "person", "email": "ME@x.test"}),
    )
    rep.require_template_subject(me, config)
    other = rep.normalise_report_config(
        _t_config(_T2, {"kind": "person", "email": "you@x.test"}),
    )
    with pytest.raises(HTTPException) as err:
        rep.require_template_subject(me, other)
    assert err.value.status_code == 422 and "must be you" in err.value.detail
    # T6 names any person. The §7.1 rule decides who, not this one.
    rep.require_template_subject(me, rep.normalise_report_config(
        _t_config(_T6, {"kind": "person", "email": "you@x.test"}),
    ))


def test_create_patch_and_preview_run_the_self_rule_before_section_7_1() -> None:
    """The order is the rule: an admin gets 422 for T2 on another person,
    and never the 200 that §7.1 gives an admin."""
    for fn, later in (
        (rep.create_report, "require_subject("),
        (rep.update_report, "require_subject("),
        (rep.preview_report, "render_body("),
    ):
        source = inspect.getsource(fn)
        assert "require_template_subject(user," in source, fn.__name__
        assert source.index("require_template_subject(") < source.index(later), (
            fn.__name__
        )
    assert "require_template_subject" not in inspect.getsource(rep.render_report)
    assert "require_template_subject" not in inspect.getsource(rep.render_body)


# ── R8: a real Postgres ─────────────────────────────────────────────────────


def _create(seeded: dict[str, Any], reader: str, config: dict[str, Any], *,  # noqa: F811
            admin: bool = False, name: str = "r5b") -> dict[str, Any]:
    return asyncio.run(rep.create_report(
        {"name": name, "config": config},
        user=_user(seeded["who"][reader], admin=admin),
    ))


def _preview(seeded: dict[str, Any], reader: str, config: dict[str, Any], *,  # noqa: F811
             admin: bool = False) -> dict[str, Any]:
    return asyncio.run(rep.preview_report(
        {"config": config}, user=_user(seeded["who"][reader], admin=admin),
    ))


@_needs_db
@pytest.mark.parametrize("template", [_T2, _T6])
def test_e_create_patch_and_preview_refuse_no_subject_or_a_team(
    seeded, wired, template: str,  # noqa: F811
) -> None:
    """(e) Each of the three routes, for no subject and for a team."""
    boss = _user(seeded["who"]["boss"], admin=True)
    plain = _create(seeded, "boss", {"sections": ["load"]}, admin=True)
    for subject in (None, _team(seeded, "team_a")):
        config = _t_config(template, subject)
        for label, call in (
            ("create", lambda c=config: _create(seeded, "boss", c, admin=True)),
            ("preview", lambda c=config: _preview(seeded, "boss", c, admin=True)),
            ("patch", lambda c=config: asyncio.run(rep.update_report(
                plain["id"], {"config": c}, user=boss,
            ))),
        ):
            code, detail = _status(call)
            assert code == 422, (label, subject, code, detail)
            assert "Choose a person" in detail, (label, detail)


@_needs_db
def test_f_t2_is_the_readers_own_day(seeded, wired) -> None:  # noqa: F811
    """(f) The member on themselves gets a row. The admin on another person
    gets 422, on create, preview and patch."""
    saved = _create(seeded, "m", _t_config(_T2, _person(seeded, "m")))
    assert saved["id"] and saved["config"]["template"] == _T2
    assert saved["config"]["subject"] == _person(seeded, "m")

    other = _t_config(_T2, _person(seeded, "m"))
    boss = _user(seeded["who"]["boss"], admin=True)
    code, detail = _status(lambda: _create(seeded, "boss", other, admin=True))
    assert (code, "must be you" in detail) == (422, True), detail
    code, _ = _status(lambda: _preview(seeded, "boss", other, admin=True))
    assert code == 422
    mine = _create(seeded, "boss", {"sections": ["load"]}, admin=True)
    code, _ = _status(lambda: asyncio.run(rep.update_report(
        mine["id"], {"config": other}, user=boss,
    )))
    assert code == 422
    # The admin's own day is allowed.
    assert _create(seeded, "boss", _t_config(_T2, _person(seeded, "boss")),
                   admin=True)["id"]


@_needs_db
def test_g_t6_follows_section_7_1(seeded, wired) -> None:  # noqa: F811
    """(g) Lead L on M, a member of team A, and 403 on N of team B."""
    saved = _create(seeded, "lead", _t_config(_T6, _person(seeded, "m")))
    assert saved["config"]["template"] == _T6
    body = asyncio.run(rep.render_report(saved["id"], user=_user(seeded["who"]["lead"])))
    assert set(body["sections"]) == {"finished", "throughput", "pulse"}

    code, detail = _status(
        lambda: _create(seeded, "lead", _t_config(_T6, _person(seeded, "n"))),
    )
    assert code == 403, detail
    assert "lead role" in detail and "admin:members:read" in detail


@_needs_db
def test_h_t1_takes_a_team(seeded, wired) -> None:  # noqa: F811
    """(h) T1 on team A renders for its lead, and a member gets 403."""
    config = _t_config("team_pulse", _team(seeded, "team_a"))
    saved = _create(seeded, "lead", config)
    body = asyncio.run(rep.render_report(saved["id"], user=_user(seeded["who"]["lead"])))
    assert set(body["sections"]) == {"pulse", "conflicts", "rebalance"}
    rows = {r["assignee"] for r in body["sections"]["pulse"]["rows"]}
    assert rows == {seeded["who"]["m"], seeded["who"]["lead"]}

    code, detail = _status(lambda: _preview(seeded, "m", config))
    assert code == 403, detail
    code, _ = _status(lambda: _create(seeded, "m", config))
    assert code == 403


@_needs_db
def test_j_the_waiting_items_show_to_the_subject_only(seeded, wired) -> None:  # noqa: F811
    """(j) Owner Q6. M waits on a task that is past its line. M's render of
    their own T2 lists it. An admin's render of the same row does not."""
    _sql(
        seeded,
        "INSERT INTO pm_task_personal (task_id, member_email, organization_id,"
        " disposition, waiting_on, delegated_at, expected_by)"
        " VALUES (CAST(:t AS uuid), :m, CAST(:o AS uuid), 'WAITING',"
        " '{\"name\": \"N\"}'::jsonb, now() - interval '3 days',"
        " now() - interval '1 day')",
        t=seeded["m1"], m=seeded["who"]["m"], o=seeded["org"],
    )
    try:
        saved = _create(seeded, "m", _t_config(_T2, _person(seeded, "m")))
        own = asyncio.run(rep.render_report(saved["id"], user=_user(seeded["who"]["m"])))
        row = next(r for r in own["sections"]["pulse"]["rows"]
                   if r["assignee"] == seeded["who"]["m"])
        assert row["waiting_count"] == 1
        assert [w["id"] for w in row["waiting"]] == [seeded["m1"]]

        admin = asyncio.run(rep.render_report(
            saved["id"], user=_user(seeded["who"]["boss"], admin=True),
        ))
        row = next(r for r in admin["sections"]["pulse"]["rows"]
                   if r["assignee"] == seeded["who"]["m"])
        assert "waiting" not in row and "waiting_count" not in row
        assert "waiting_overdue" not in row["help_reasons"]
    finally:
        _sql(seeded, "DELETE FROM pm_task_personal WHERE task_id = CAST(:t AS uuid)",
             t=seeded["m1"])


@_needs_db
def test_the_subjects_answer_names_the_reader(seeded, wired) -> None:  # noqa: F811
    """The subject chip names the reader's own row "Me" from this key."""
    got = asyncio.run(rep.list_report_subjects(user=_user(seeded["who"]["m"])))
    assert got["me"] == seeded["who"]["m"]
    assert [p["email"] for p in got["people"]] == [seeded["who"]["m"]]


# ── R5b-1 repair: a T2 is always about its AUTHOR ──────────────────────────


def test_the_patch_rule_compares_with_the_author_not_the_caller() -> None:
    """Hermetic. On a PATCH the author decides, so an admin keeps a member's
    T2 about the member, and cannot move it to the admin."""
    boss = _user("boss@x.test", admin=True)
    member_t2 = rep.normalise_report_config(
        _t_config(_T2, {"kind": "person", "email": "a@x.test"}),
    )
    rep.require_template_subject(boss, member_t2, author="A@x.test")
    moved = rep.normalise_report_config(
        _t_config(_T2, {"kind": "person", "email": "boss@x.test"}),
    )
    with pytest.raises(HTTPException) as err:
        rep.require_template_subject(boss, moved, author="a@x.test")
    assert err.value.status_code == 422
    assert rep.T2_AUTHOR_REASON in err.value.detail
    patch = inspect.getsource(rep.update_report)
    assert "stored.created_by" in patch and "author=author" in patch


@_needs_db
def test_an_admin_edits_a_members_t2_and_it_stays_theirs(seeded, wired) -> None:  # noqa: F811
    """Owner Q13 and the R5b-1 author rule, on a real database."""
    m = _user(seeded["who"]["m"])
    boss = _user(seeded["who"]["boss"], admin=True)
    saved = _create(seeded, "m", _t_config(_T2, _person(seeded, "m")),
                    name="M day")
    assert saved["can_edit"] is True

    # The admin renames it and keeps its subject: 200, and it stays M's.
    got = asyncio.run(rep.update_report(
        saved["id"],
        {"name": "M day renamed", "config": _t_config(_T2, _person(seeded, "m"))},
        user=boss,
    ))
    assert got["name"] == "M day renamed"
    assert got["config"]["subject"] == _person(seeded, "m")
    listed = asyncio.run(rep.list_reports(user=m))["reports"]
    assert saved["id"] in [r["id"] for r in listed]
    body = asyncio.run(rep.render_report(saved["id"], user=m))
    assert body["report"]["config"]["subject"] == _person(seeded, "m")

    # The admin may not move it to the admin: 422 with the reason.
    code, detail = _status(lambda: asyncio.run(rep.update_report(
        saved["id"], {"config": _t_config(_T2, _person(seeded, "boss"))},
        user=boss,
    )))
    assert code == 422 and rep.T2_AUTHOR_REASON in detail, detail
    again = asyncio.run(rep.get_report(saved["id"], user=m))
    assert again["config"]["subject"] == _person(seeded, "m")

    # The author edits their own T2: 200.
    own = asyncio.run(rep.update_report(
        saved["id"],
        {"name": "Mine", "config": _t_config(_T2, _person(seeded, "m"))},
        user=m,
    ))
    assert own["name"] == "Mine"


@_needs_db
def test_one_bad_t2_row_never_422s_the_whole_list(seeded, wired) -> None:  # noqa: F811
    """A stored T2 with no subject is left out of the list, and the list
    answers 200. An admin may still delete it (Q12 and Q13)."""
    import json

    from sqlalchemy import create_engine, text

    from tests.unit.test_projects_report_scope_r5 import _TENANT_URL

    boss = _user(seeded["who"]["boss"], admin=True)
    good = _create(seeded, "boss", {"sections": ["load"]}, admin=True)
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as c:
        bad = str(c.execute(
            text(
                "INSERT INTO pm_reports (project_id, organization_id, name,"
                " config, created_by) VALUES (NULL, CAST(:o AS uuid), 'bad t2',"
                " CAST(:c AS jsonb), :by) RETURNING id"
            ),
            {"o": seeded["org"], "by": seeded["who"]["m"],
             "c": json.dumps({"template": _T2})},
        ).scalar_one())
    eng.dispose()

    ids = [r["id"] for r in asyncio.run(rep.list_reports(user=boss))["reports"]]
    assert good["id"] in ids and bad not in ids
    assert _status(lambda: asyncio.run(rep.delete_report(bad, user=boss))) == (200, "")
    assert _status(lambda: asyncio.run(rep.get_report(bad, user=boss)))[0] == 404
