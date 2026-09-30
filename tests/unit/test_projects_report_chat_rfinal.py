"""WS-27bn R-final — the chat renders a report by name.

Spec: ``project-docs/specs/projects_reports.md`` §8 R-final, and §7.1.

**The claim.** ``render_report`` takes a template and a subject, and
``report_id`` is optional. With no id, the tool reads the template catalogue
and the reader's subjects from the server, matches the names with no regard
to case, and renders through ``POST /projects/reports/preview``. So the
server runs ``require_template_subject`` and the §7.1 check of
``render_body``. Two names that match one word are listed, never guessed.

* Hermetic: the name form, the choice lists, the one formatter, and the
  manifest rows that the slice changes.
* Real database: the chat renders T1 for a team by name, and a member who
  names a team that they do not lead gets the 403 reason, not a render.

⚠️ The real-database half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and
a skip is not a pass.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("sqlalchemy")

import skill_projects
import skill_projects.client as client
from fastapi import HTTPException
from gateway.routes.projects import reports as rep
from skill_projects import manifest as m

from tests.unit._projects_agent_fakes import (
    FakeResponse,
    fake_gateway,
)

# The R5a fixtures: one organization, two teams, a lead, and a real engine.
from tests.unit.test_projects_report_scope_r5 import (  # noqa: F401
    _ladder,
    _needs_db,
    _user,
    seeded,
    wired,
)

_ME = "pm@fracktal.in"

_BODY = {
    "report": {"id": None, "name": "Team pulse · Design"},
    "period_start": "2026-09-28",
    "period_end": "2026-10-04",
    "sections": {"finished": {"total": 3, "rows": [{"name": "Ops", "finished": 3}]}},
}


def _catalogue() -> dict[str, Any]:
    """The server's own catalogue, so the fake cannot drift from it."""
    return {"templates": json.loads(json.dumps(list(rep.TEMPLATES.values())))}


def _subjects(
    teams: list[dict[str, str]] | None = None, people: list[dict[str, str | None]] | None = None
) -> dict[str, Any]:
    return {
        "everyone": False,
        "me": _ME,
        "people": people if people is not None else [{"email": _ME, "name": "Pat"}],
        "teams": teams if teams is not None else [{"slug": "design", "name": "Design"}],
    }


def _responder(subjects: dict[str, Any]) -> Any:
    def answer(call: dict) -> Any:
        path = call["path"]
        if path == "/projects/reports/templates":
            return _catalogue()
        if path == "/projects/reports/subjects":
            return subjects
        if path == "/projects/reports/preview":
            return _BODY
        if path.endswith("/render"):
            return {**_BODY, "report": {"id": "x", "name": "Weekly"}}
        raise AssertionError(f"unexpected call {call['method']} {path}")

    return answer


def _drawn(monkeypatch) -> list[dict]:
    wa = importlib.import_module("acb_skills.write_artifact")
    specs: list[dict] = []

    async def record(ui: str) -> dict:
        specs.append(json.loads(ui))
        return {"ok": True}

    monkeypatch.setattr(wa, "emit_generative_ui", record)
    return specs


def _previews(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["path"] == "/projects/reports/preview"]


# ── Hermetic: the name form ─────────────────────────────────────────────────


async def test_team_pulse_for_design_resolves_both_names(monkeypatch) -> None:
    """Case does not matter, and a key or a name both match."""
    _drawn(monkeypatch)
    calls = fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    out = await skill_projects.render_report(template="TEAM PULSE", subject="design")
    sent = _previews(calls)
    assert len(sent) == 1 and sent[0]["method"] == "POST"
    t1 = rep.TEMPLATES["team_pulse"]
    assert sent[0]["json"]["config"] == {
        "template": "team_pulse",
        "sections": t1["sections"],
        "weeks": t1["weeks"],
        "skip_current_week": t1["skip_current_week"],
        "subject": {"kind": "team", "slug": "design"},
    }
    assert "Report «Team pulse · Design»" in out


async def test_the_template_key_matches_too(monkeypatch) -> None:
    _drawn(monkeypatch)
    calls = fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    await skill_projects.render_report(template="team_pulse", subject="Design")
    assert _previews(calls)[0]["json"]["config"]["template"] == "team_pulse"


async def test_the_name_form_uses_the_saved_report_formatter(monkeypatch) -> None:
    """One formatter: the text and the card of a preview are the text and the
    card of a saved report with the same body."""
    specs = _drawn(monkeypatch)
    fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    by_name = await skill_projects.render_report(template="team pulse", subject="Design")
    saved = await skill_projects.render_report(
        report_id="0f8fad5b-d9cb-469f-a165-70867728950e",
    )
    assert by_name.splitlines()[2:] == saved.splitlines()[2:]
    assert specs[0]["props"]["data"]["tables"] == specs[1]["props"]["data"]["tables"]
    # A preview has no row, so the card carries no report id.
    assert "reportId" not in specs[0]["props"]["data"]
    assert specs[1]["props"]["data"]["reportId"]


async def test_two_teams_with_one_name_are_listed_not_guessed(monkeypatch) -> None:
    _drawn(monkeypatch)
    teams = [
        {"slug": "design", "name": "Design"},
        {"slug": "design-2", "name": "design"},
    ]
    calls = fake_gateway(monkeypatch, _responder(_subjects(teams)), user=_ME)
    out = await skill_projects.render_report(template="team pulse", subject="Design")
    assert _previews(calls) == []
    assert "design" in out and "design-2" in out
    assert "more than one" in out.lower()


async def test_a_person_and_a_team_with_one_name_are_listed(monkeypatch) -> None:
    _drawn(monkeypatch)
    people = [{"email": _ME, "name": "Pat"}, {"email": "d@x.io", "name": "Design"}]
    calls = fake_gateway(
        monkeypatch,
        _responder(_subjects(people=people)),
        user=_ME,
    )
    out = await skill_projects.render_report(template="team pulse", subject="design")
    assert _previews(calls) == []
    assert "d@x.io" in out and "design" in out


async def test_an_unknown_template_lists_the_live_templates(monkeypatch) -> None:
    _drawn(monkeypatch)
    calls = fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    out = await skill_projects.render_report(template="morning", subject="Design")
    assert _previews(calls) == []
    assert "Team pulse" in out and "My day" in out
    # A coming-soon template is not a choice.
    assert "What changed" not in out


async def test_a_name_that_matches_nothing_goes_to_the_server(monkeypatch) -> None:
    """The subjects list holds only what the reader may report on (§7.1
    rule 1). A name outside it goes to the server as a slug, so the member
    gets the server's reason, and never a guess."""
    _drawn(monkeypatch)
    calls = fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    await skill_projects.render_report(template="team pulse", subject="Hardware Ops")
    assert _previews(calls)[0]["json"]["config"]["subject"] == {
        "kind": "team",
        "slug": "hardware-ops",
    }
    await skill_projects.render_report(template="1:1 prep", subject="N@X.io")
    assert _previews(calls)[1]["json"]["config"]["subject"] == {
        "kind": "person",
        "email": "n@x.io",
    }


async def test_my_day_takes_the_reader_with_no_subject(monkeypatch) -> None:
    _drawn(monkeypatch)
    calls = fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    await skill_projects.render_report(template="my day")
    assert _previews(calls)[0]["json"]["config"]["subject"] == {
        "kind": "person",
        "email": _ME,
    }


async def test_neither_an_id_nor_a_template_is_refused_with_no_call(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _responder(_subjects()), user=_ME)
    with pytest.raises(client.GatewayRefusal):
        await skill_projects.render_report()
    assert calls == []


async def test_a_403_from_the_preview_reaches_the_member(monkeypatch) -> None:
    """The client relays the server's reason. The tool draws nothing."""
    specs = _drawn(monkeypatch)
    reason = "A report on the team hardware needs the admin grant, or the lead role."

    class _Refusing:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> _Refusing:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def request(self, method: str, url: str, **_kw: Any) -> FakeResponse:
            path = "/" + url.split("/", 3)[-1]
            if path == "/projects/reports/preview":
                return FakeResponse({"detail": reason}, status_code=403)
            return FakeResponse(_responder(_subjects())({"method": method, "path": path}))

    monkeypatch.setattr(client, "httpx", type("H", (), {"AsyncClient": _Refusing}))
    monkeypatch.setattr(client, "current_user_email", lambda: _ME)
    with pytest.raises(client.GatewayRefusal) as err:
        await skill_projects.render_report(template="team pulse", subject="hardware")
    assert reason in str(err.value)
    assert specs == []


def test_the_docstring_says_when_to_use_the_name_form() -> None:
    doc = skill_projects.render_report.__doc__ or ""
    assert "team pulse for Design" in doc
    assert "report_id" in doc and "template" in doc and "subject" in doc


def test_the_instructions_name_the_name_form() -> None:
    from tests.unit._projects_agent_fakes import AGENT_DIR

    text = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    assert "team pulse for Design" in text


# ── Hermetic: the manifest rows of this slice ───────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/projects/reports/preview"),
        ("GET", "/projects/reports/subjects"),
        ("GET", "/projects/reports/templates"),
    ],
)
def test_the_three_rows_map_to_render_report_class_a(method: str, path: str) -> None:
    row = m.route_for(method, path)
    assert row is not None and row.path == path
    assert (row.tool, row.cls) == ("render_report", "A")


def test_the_preview_stays_a_read_only_post() -> None:
    assert ("POST", "/projects/reports/preview") in m.READ_ONLY_POSTS


def test_every_delivery_row_stays_excluded() -> None:
    rows = [r for r in m.MANIFEST if "recipients" in r.path or r.path.endswith("/schedule")]
    assert len(rows) == 4
    for row in rows:
        assert row.cls == "X" and row.reason == m._DELIVERY_REASON, row.path


# ── R8: a real Postgres, the tool through the real routes ───────────────────


def _through_routes(monkeypatch, *, admin: bool = False) -> list[dict]:
    """Route each chat call to the real route function, as the reader in
    ``X-User-Email``. An HTTPException becomes the status the gateway sends."""
    calls: list[dict] = []

    class _Routed:
        def __init__(self, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> _Routed:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def request(self, method: str, url: str, **kw: Any) -> FakeResponse:
            path = "/" + url.split("/", 3)[-1]
            user = _user(kw["headers"]["X-User-Email"], admin=admin)
            calls.append({"method": method, "path": path, "json": kw.get("json")})
            try:
                if (method, path) == ("GET", "/projects/reports/templates"):
                    body = await rep.list_report_templates(user=user)
                elif (method, path) == ("GET", "/projects/reports/subjects"):
                    body = await rep.list_report_subjects(user=user)
                elif (method, path) == ("POST", "/projects/reports/preview"):
                    body = await rep.preview_report(kw["json"], user=user)
                else:
                    raise AssertionError(f"unexpected {method} {path}")
            except HTTPException as exc:
                return FakeResponse({"detail": exc.detail}, status_code=exc.status_code)
            return FakeResponse(json.loads(json.dumps(body, default=str)))

    monkeypatch.setattr(client, "httpx", type("H", (), {"AsyncClient": _Routed}))
    return calls


@_needs_db
def test_the_chat_renders_t1_for_a_team_by_name(seeded, wired, monkeypatch) -> None:  # noqa: F811
    """Done when 1. The lead of team A names it, and the card draws T1."""
    specs = _drawn(monkeypatch)
    calls = _through_routes(monkeypatch)
    monkeypatch.setattr(client, "current_user_email", lambda: seeded["who"]["lead"])
    out = asyncio.run(
        skill_projects.render_report(
            template="Team Pulse",
            subject=seeded["team_a"].upper(),
        )
    )
    assert [c["path"] for c in calls][-1] == "/projects/reports/preview"
    assert "Report «Team pulse" in out
    card = specs[0]["props"]["data"]
    assert "reportId" not in card
    assert card["tables"], card


@_needs_db
def test_a_member_who_names_a_team_they_do_not_lead_gets_the_403_reason(
    seeded,
    wired,
    monkeypatch,  # noqa: F811
) -> None:
    """Done when 2. The member ``m`` of team A names team B. The server's
    §7.1 reason reaches the member, and nothing is drawn."""
    specs = _drawn(monkeypatch)
    calls = _through_routes(monkeypatch)
    monkeypatch.setattr(client, "current_user_email", lambda: seeded["who"]["m"])
    with pytest.raises(client.GatewayRefusal) as err:
        asyncio.run(
            skill_projects.render_report(
                template="team pulse",
                subject=seeded["team_b"],
            )
        )
    said = str(err.value)
    assert "Not permitted." in said
    assert f"A report on the team {seeded['team_b']} needs" in said
    assert "lead role in that team" in said
    assert specs == []
    assert _previews(calls), "the refusal must come from the server"
