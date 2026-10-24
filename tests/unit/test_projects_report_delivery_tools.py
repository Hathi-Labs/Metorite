"""WS-64 OP-6d: the report recipient and schedule tools of the Projects chat, no database.

Spec: ``project-docs/specs/one_projects_app.md`` §5a (D-OP-9) and §9 OP-6d, and
``projects_reports.md`` §9.12.8 (delivery is built and dark).

What this file holds, with no gateway and no database:

* ``report_recipients`` is a read. It lists each address with the name of the
  directory, and it sends nothing;
* ``add_report_recipient``, ``remove_report_recipient`` and ``set_report_schedule``
  each show ONE card, and a declined card writes nothing;
* the tool refuses before its card where ``GET /projects/reports/{id}`` says
  ``can_edit`` false, in the words of the server (``reports.CHANGE_REFUSED``);
* the card names the address, the name, the report and the schedule in plain
  words, and says that delivery may be switched off;
* the receipt of the schedule reads ``delivery_armed_on_this_deployment``, the
  field of the route that the Reports app shows;
* the owner's send switch is not a tool's. The fence at the end greps the skill,
  the agent files and the instructions for its name.

The R8 half is ``test_projects_report_delivery_r8.py``.

**The mutations (R7).** Each one turned the named test red. Commit before each.

* D1. ``guarded.py`` ``_report_for_change``: no ``can_edit`` check.
  ``test_a_member_who_may_not_change_the_report_gets_no_card_and_the_servers_words``.
* D2. ``guarded.py`` ``add_report_recipient``: skip the card.
  ``test_a_declined_card_writes_nothing``.
* D3. ``guarded.py`` ``set_report_schedule``: the receipt says "emailed".
  ``test_the_schedule_receipt_says_plainly_that_delivery_is_off``.
* D4. Put the name of the send switch into ``instructions.md``.
  ``test_no_tool_and_no_instruction_names_the_send_switch``.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m
from skill_projects.client import GatewayRefusal

from tests.unit._projects_agent_fakes import (
    SKILL_DIR,
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    writes,
)

ROOT = Path(__file__).resolve().parents[2]
RID = str(uuid.uuid4())
ASHA = "asha@x.test"
PRIYA = "priya@x.test"
NAMES = {ASHA: "Asha Iyer", PRIYA: "Priya Menon"}


def _gateway(
    monkeypatch,
    *,
    can_edit: bool | None = True,
    held: list[str] | None = None,
    enabled: bool = False,
    armed: Any = False,
    write_status: int = 200,
):
    rows = [ASHA] if held is None else held
    report: dict[str, Any] = {
        "id": RID, "name": "Weekly delivery", "enabled": enabled,
        "schedule": "weekly" if enabled else None, "project_id": None,
    }
    if can_edit is not None:
        report["can_edit"] = can_edit

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if method != "GET" and write_status != 200:
            return FakeResponse({"detail": "The server said no."}, write_status)
        if path == f"/projects/reports/{RID}" and method == "GET":
            return report
        if path == f"/projects/reports/{RID}/recipients" and method == "GET":
            return {"report_id": RID, "recipients": [
                {"recipient": r, "added_by": ASHA, "added_at": None} for r in rows]}
        if path == f"/projects/reports/{RID}/recipients" and method == "POST":
            return {"report_id": RID, "recipient": call["json"]["recipient"]}
        if path.startswith(f"/projects/reports/{RID}/recipients/") and method == "DELETE":
            return {}
        if path == f"/projects/reports/{RID}/schedule" and method == "PATCH":
            row = {**report, "enabled": call["json"]["enabled"]}
            if armed is not None:
                row["delivery_armed_on_this_deployment"] = armed
            return row
        if path == "/projects/people/names":
            wanted = [e for e in call["params"].get("emails", "").split(",") if e]
            return {"names": {e: NAMES[e] for e in wanted if e in NAMES}}
        if path == "/projects/assignees":
            return {"people": [{"name": n, "assignee": e} for e, n in NAMES.items()
                               if call["params"].get("q", "").lower() in n.lower()],
                    "agents": []}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, answer)


def _server_words() -> str:
    from gateway.routes.projects.reports import CHANGE_REFUSED

    return CHANGE_REFUSED


# ── the read ────────────────────────────────────────────────────────────────


async def test_report_recipients_lists_each_address_with_its_name(monkeypatch) -> None:
    calls = _gateway(monkeypatch, held=[ASHA, PRIYA, "gone@x.test"])
    out = await skill_projects.report_recipients(RID)
    assert "«Weekly delivery»" in out and "3 recipients" in out
    assert f"«{ASHA}» · «Asha Iyer»" in out and f"«{PRIYA}» · «Priya Menon»" in out
    assert "«gone@x.test» · not in the directory" in out
    assert "schedule: off" in out
    assert "Delivery may be switched off" in out
    assert writes(calls) == []


async def test_report_recipients_says_when_the_member_may_not_change_the_list(monkeypatch) -> None:
    _gateway(monkeypatch, can_edit=False)
    out = await skill_projects.report_recipients(RID)
    assert "may read this list and may not change it" in out


# ── the refusal before the card ─────────────────────────────────────────────


async def test_a_member_who_may_not_change_the_report_gets_no_card_and_the_servers_words(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    for act in (
        lambda: skill_projects.add_report_recipient(RID, PRIYA),
        lambda: skill_projects.remove_report_recipient(RID, ASHA),
        lambda: skill_projects.set_report_schedule(RID, True),
    ):
        calls = _gateway(monkeypatch, can_edit=False)
        out = await act()
        assert _server_words() in out, out
        assert writes(calls) == []
    assert asked == [], "no card for a change the server refuses"


def test_the_skills_sentence_is_the_servers_sentence() -> None:
    from skill_projects.writes import REPORT_CHANGE_REFUSED

    assert _server_words() == REPORT_CHANGE_REFUSED


async def test_a_report_without_the_flag_goes_on_and_the_route_decides(monkeypatch) -> None:
    """No ``can_edit`` key: the card shows, and the route's 403 comes back as text."""
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, can_edit=None, write_status=403)
    with pytest.raises(GatewayRefusal) as raised:
        await skill_projects.add_report_recipient(RID, PRIYA)
    assert len(asked) == 1
    assert raised.value.status == 403 and "The server said no." in str(raised.value)
    assert len(writes(calls)) == 1


async def test_a_list_where_one_id_is_wanted_is_refused_before_any_call(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    for out in (
        await skill_projects.add_report_recipient(RID, f"{ASHA},{PRIYA}"),
        await skill_projects.remove_report_recipient(RID, f"{ASHA}\n{PRIYA}"),
        await skill_projects.set_report_schedule(f"{RID},{uuid.uuid4()}", True),
    ):
        assert "takes ONE" in out, out
    assert asked == [] and calls == []


# ── add ─────────────────────────────────────────────────────────────────────


async def test_add_shows_one_card_with_the_name_the_address_and_the_report(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.add_report_recipient(RID, PRIYA)
    [card] = asked
    assert "«Weekly delivery»" in card["detail"] and "«Priya Menon»" in card["detail"]
    assert f"«{PRIYA}»" in card["detail"]
    assert "not scheduled" in card["context"]
    assert "Delivery may be switched off" in card["context"]
    [post] = writes(calls)
    assert (post["method"], post["path"], post["json"]) == (
        "POST", f"/projects/reports/{RID}/recipients", {"recipient": PRIYA})
    assert "is now on the list of" in out and "Delivery may be switched off" in out


async def test_add_by_name_resolves_one_person(monkeypatch) -> None:
    approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await skill_projects.add_report_recipient(RID, "Priya Menon")
    [post] = writes(calls)
    assert post["json"] == {"recipient": PRIYA}


async def test_add_refuses_an_agent_in_the_words_of_the_route(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    with pytest.raises(GatewayRefusal) as raised:
        await skill_projects.add_report_recipient(RID, "agent:reporter")
    assert "A report goes to a person, never to an agent." in str(raised.value)
    assert asked == [] and writes(calls) == []


async def test_add_of_a_person_on_the_list_is_a_sentence_and_not_a_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.add_report_recipient(RID, ASHA)
    assert "already receives" in out and asked == [] and writes(calls) == []


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    asked = deny(monkeypatch)
    for act in (
        lambda: skill_projects.add_report_recipient(RID, PRIYA),
        lambda: skill_projects.remove_report_recipient(RID, ASHA),
        lambda: skill_projects.set_report_schedule(RID, True),
    ):
        calls = _gateway(monkeypatch)
        out = await act()
        assert "cancel" in out.lower() or "not " in out.lower(), out
        assert writes(calls) == []
    assert len(asked) == 3


# ── remove ──────────────────────────────────────────────────────────────────


async def test_remove_shows_one_card_and_sends_one_delete(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, held=[ASHA, PRIYA])
    out = await skill_projects.remove_report_recipient(RID, "Priya Menon")
    [card] = asked
    assert f"«{PRIYA}»" in card["detail"] and "«Weekly delivery»" in card["detail"]
    [delete] = writes(calls)
    assert (delete["method"], delete["path"]) == (
        "DELETE", f"/projects/reports/{RID}/recipients/{PRIYA}")
    assert "is off the list of" in out


async def test_remove_of_a_stranger_is_a_sentence_and_not_a_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.remove_report_recipient(RID, "nobody@x.test")
    assert "is not on the list" in out and asked == [] and writes(calls) == []


async def test_remove_of_the_last_person_of_an_armed_report_warns_first(monkeypatch) -> None:
    asked = approve(monkeypatch)
    _gateway(monkeypatch, held=[ASHA], enabled=True)
    await skill_projects.remove_report_recipient(RID, ASHA)
    assert "LAST person on the list" in asked[0]["context"]


# ── the schedule ────────────────────────────────────────────────────────────


async def test_schedule_on_names_the_report_the_week_and_the_people(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, held=[ASHA, PRIYA])
    await skill_projects.set_report_schedule(RID, True)
    [card] = asked
    assert "«Weekly delivery»" in card["detail"] and "every week" in card["detail"]
    assert "«Asha Iyer»" in card["context"] and "«Priya Menon»" in card["context"]
    assert "Delivery may be switched off" in card["context"]
    [patch] = writes(calls)
    assert (patch["method"], patch["json"]) == ("PATCH", {"enabled": True})


@pytest.mark.parametrize(
    ("armed", "words"),
    [
        (False, "Reports are not emailed yet: delivery is switched off."),
        (True, "Delivery is switched on, so the report is emailed on its schedule."),
        (None, "Delivery may be switched off."),
    ],
)
async def test_the_schedule_receipt_says_plainly_that_delivery_is_off(
    monkeypatch, armed, words,
) -> None:
    approve(monkeypatch)
    _gateway(monkeypatch, armed=armed)
    out = await skill_projects.set_report_schedule(RID, True)
    assert words in out, out


async def test_schedule_on_with_nobody_on_the_list_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, held=[])
    out = await skill_projects.set_report_schedule(RID, True)
    assert "Add at least one recipient" in out and asked == [] and writes(calls) == []


async def test_schedule_already_in_the_state_asked_is_a_sentence(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, enabled=True)
    out = await skill_projects.set_report_schedule(RID, True)
    assert "already on" in out and asked == [] and writes(calls) == []


async def test_schedule_off_shows_one_card_and_keeps_the_list(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, enabled=True)
    out = await skill_projects.set_report_schedule(RID, False)
    assert "The list stays" in asked[0]["context"]
    [patch] = writes(calls)
    assert patch["json"] == {"enabled": False}
    assert "is off" in out


# ── the manifest and the registrations ──────────────────────────────────────


def test_the_four_routes_have_their_tools() -> None:
    for verb, path, tool, cls in (
        ("GET", "/projects/reports/{report_id}/recipients", "report_recipients", "A"),
        ("POST", "/projects/reports/{report_id}/recipients", "add_report_recipient", "C"),
        ("DELETE", "/projects/reports/{report_id}/recipients/{email}",
         "remove_report_recipient", "C"),
        ("PATCH", "/projects/reports/{report_id}/schedule", "set_report_schedule", "C"),
    ):
        row = m.route_for(verb, path)
        assert row is not None and (row.tool, row.cls) == (tool, cls)


# ── the fence on the owner's switch ─────────────────────────────────────────

SWITCH = "PROJECT_REPORT_EMAIL_ENABLED"


def _scanned() -> list[Path]:
    files = [p for p in SKILL_DIR.rglob("*.py")]
    agent = ROOT / "apps/agents/agent-projects"
    files += [agent / "instructions.md", agent / "config.json"]
    return files


def test_no_tool_and_no_instruction_names_the_send_switch() -> None:
    """CLAUDE.md §3a rule 3: the send switch stays the owner's. R7 fence: put its
    name in the skill, the instructions or the agent config, and this fails."""
    files = _scanned()
    assert len(files) > 10, "the scan found nothing"
    named = [str(p.relative_to(ROOT)) for p in files if SWITCH in p.read_text(encoding="utf-8")]
    assert named == [], f"the send switch is named in {named}"


def test_no_tool_reads_the_environment_for_delivery() -> None:
    pattern = re.compile(r"os\.environ|getenv|delivery_armed\(")
    hits = [
        str(p.relative_to(ROOT))
        for p in SKILL_DIR.rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
        and p.name in {"guarded.py", "reads.py", "manifest.py"}
    ]
    assert hits == [], f"a tool of the report delivery reads the environment: {hits}"


def test_a_gatewayrefusal_is_still_the_way_a_route_says_no() -> None:
    assert issubclass(GatewayRefusal, Exception)
