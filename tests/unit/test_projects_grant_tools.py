"""WS-64 OP-6b: the grant tools of the Projects chat, without a database.

Spec: ``project-docs/specs/one_projects_app.md`` §5a, D-OP-9, D-OP-11 and §9
OP-6b. D-OP-9 reverses D-PM-40: the chat writes a grant behind one class C
card. The card is a brake on a mistake and not authority
(``org_access_control.md`` §8d.3). The route decides, through
``authority.assert_may_write_grant``.

What this file holds, with no gateway and no database:

* the tool refuses before its card where ``GET /projects/nodes/{id}`` says
  ``can_write_grants`` false, and it quotes the server's own sentence;
* ``authority.attach_can_write_grants`` sets that flag from the one rule, and
  the words are the 403 of the two routes;
* a subject outside the ONE grant grammar (D12) is refused before any call,
  through ``acb_auth.permissions.normalize_grant_subject`` and no local regex;
* the card names the project path and the subject in plain words;
* ``revoke_access`` finds a grant by its id or by its subject, and a grant
  that is not there is a sentence, not a card.

The R8 half is ``test_projects_grant_tools_r8.py``.

**The mutations (R7).** Each one turned the named test red.

* G1. ``guarded.py`` ``grant_refusal``: returns ``""``.
  ``test_a_member_who_may_not_write_grants_gets_no_card_and_the_servers_words``.
* G2. ``guarded.py`` ``grant_access``: no ``_subject_or_refusal`` call.
  ``test_a_subject_outside_the_grammar_makes_no_call``.
* G3. ``authority.py`` ``attach_can_write_grants``: no ``grant_refusal`` key.
  ``test_the_flag_comes_from_the_one_rule_with_the_403_words``.

Fix round 1 (adversarial review):

* G4. ``authority.py`` ``grant_write_refusal``: no ``in_personal_tree`` check.
  ``test_a_personal_tree_is_refused_for_an_admin_too``.
* G5. ``guarded.py`` ``grant_access``: no ``_unknown_group`` call.
  ``test_a_group_that_does_not_exist_shows_no_card_and_lists_the_groups``.
* G6. ``guarded.py`` ``_revoke_warnings``: returns ``[]``.
  ``test_the_revoke_card_warns_first_on_the_last_grant_and_on_lost_sight``.
"""

from __future__ import annotations

import ast
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import guarded
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import SKILL_DIR, approve, deny, fake_gateway, writes

PID = str(uuid.uuid4())
PARENT = str(uuid.uuid4())
GRANT = str(uuid.uuid4())


def _refused_words() -> str:
    from gateway.routes.projects.authority import GRANT_WRITE_REFUSED

    return GRANT_WRITE_REFUSED


def _gateway(monkeypatch, node: dict[str, Any], grants: list[dict[str, Any]] | None = None):
    rows = grants if grants is not None else [
        {"id": GRANT, "project_id": PID, "subject": "riya@acme.test", "created_by": "a@x.io"}
    ]

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == f"/projects/nodes/{PID}" and method == "GET":
            return node
        if path == f"/projects/nodes/{PARENT}" and method == "GET":
            return {"id": PARENT, "name": "Product", "parent_project_id": None}
        if path == f"/projects/nodes/{PID}/grants" and method == "GET":
            return {"rows": rows, "total": len(rows)}
        if path == f"/projects/nodes/{PID}/grants" and method == "POST":
            return {"id": str(uuid.uuid4()), "subject": call["json"]["subject"]}
        if method == "DELETE":
            return {"deleted": path.rsplit("/", 1)[-1], "subject": "riya@acme.test"}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, answer)


LAUNCH = {"id": PID, "name": "Launch", "parent_project_id": PARENT, "can_write_grants": True}


async def test_a_member_who_may_not_write_grants_gets_no_card_and_the_servers_words(
    monkeypatch,
) -> None:
    words = _refused_words()
    node = {**LAUNCH, "can_write_grants": False, "grant_refusal": words}
    asked = approve(monkeypatch)
    for act in (
        lambda: skill_projects.grant_access(PID, "org"),
        lambda: skill_projects.revoke_access(PID, GRANT),
    ):
        calls = _gateway(monkeypatch, node)
        out = await act()
        assert out.startswith("Refused:"), out
        assert f"Gateway said: «{words}»" in out
        assert writes(calls) == []
    assert asked == [], "no card for a grant write the server refuses"


def test_a_row_without_the_flag_goes_on_and_the_route_decides() -> None:
    assert guarded.grant_refusal({}) == ""
    assert guarded.grant_refusal({"can_write_grants": True}) == ""
    assert guarded.grant_refusal({"can_write_grants": False}).startswith("Refused:")


async def test_the_flag_comes_from_the_one_rule_with_the_403_words() -> None:
    from gateway.routes.projects import authority as a

    class NotPersonal:
        """Answers only the personal-tree walk. An admin needs no lead query."""

        async def execute(self, statement, *_a, **_k):
            assert "personal_owner" in str(statement), "an admin needs no lead query"
            return SimpleNamespace(first=lambda: None)

    admin = SimpleNamespace(email="boss@x.test", has_permission=lambda p: p == "projects:settings:write")
    row = await a.attach_can_write_grants(NotPersonal(), admin, {"id": PID})
    assert row["can_write_grants"] is True and "grant_refusal" not in row

    class NoLeads:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(fetchall=lambda: [], first=lambda: None)

    viewer = SimpleNamespace(email="v@x.test", has_permission=lambda _p: False)
    row = await a.attach_can_write_grants(NoLeads(), viewer, {"id": PID})
    assert row == {"id": PID, "can_write_grants": False, "grant_refusal": a.GRANT_WRITE_REFUSED}


def test_get_node_attaches_the_flag() -> None:
    """The one seam: ``GET /projects/nodes/{id}`` calls the module, as OP-8 did."""
    import inspect

    from gateway.routes.projects import tree

    src = inspect.getsource(tree.get_node)
    assert "attach_can_write_grants(db, user, result)" in src


@pytest.mark.parametrize("subject", ["everybody", "group:", "riya at acme", "a@b@c"])
async def test_a_subject_outside_the_grammar_makes_no_call(subject: str, monkeypatch) -> None:
    calls = _gateway(monkeypatch, LAUNCH)
    asked = approve(monkeypatch)
    out = await skill_projects.grant_access(PID, subject)
    assert out.startswith("Refused: grant_access could not read the subject"), out
    assert calls == [] and asked == []


def test_the_tools_hold_no_copy_of_the_grammar() -> None:
    """D12: one grammar. The grant tools call ``normalize_grant_subject`` and
    compile no pattern of their own."""
    tree = ast.parse((SKILL_DIR / "guarded.py").read_text(encoding="utf-8"))
    names = {"grant_access", "revoke_access", "_subject_or_refusal", "grant_refusal"}
    bodies = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name in names]
    assert {b.name for b in bodies} == names
    source = " ".join(ast.unparse(b) for b in bodies)
    assert "normalize_grant_subject" in source
    assert "re.compile" not in source and "re.fullmatch" not in source


async def test_the_card_names_the_path_and_who_in_plain_words(monkeypatch) -> None:
    asked = approve(monkeypatch)
    for subject, words in (
        ("org", "everyone in the organization"),
        ("group:Design", "every member of the group «design»"),
        ("Riya@Acme.test", "«riya@acme.test»"),
    ):
        calls = _gateway(monkeypatch, LAUNCH, grants=[])
        out = await skill_projects.grant_access(PID, subject)
        card = asked[-1]
        assert card["detail"] == f"«Product» / «Launch» · add {words}", card
        assert "impact: access widens" in card["context"]
        [post] = writes(calls)
        assert post["json"] == {"subject": subject.lower()}
        assert out.startswith(f"{words} can now see «Product» / «Launch»"), out


async def test_a_subject_that_already_has_a_grant_shows_no_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch, LAUNCH)
    asked = approve(monkeypatch)
    out = await skill_projects.grant_access(PID, "RIYA@acme.test")
    assert "can already see «Launch»" in out and "Nothing changed" in out
    assert asked == [] and writes(calls) == []


async def test_revoke_finds_the_grant_by_id_or_subject(monkeypatch) -> None:
    asked = approve(monkeypatch)
    for grant in (GRANT, "riya@ACME.test"):
        calls = _gateway(monkeypatch, LAUNCH)
        out = await skill_projects.revoke_access(PID, grant)
        assert asked[-1]["detail"] == "«Product» / «Launch» · remove «riya@acme.test»"
        assert [(c["method"], c["path"]) for c in writes(calls)] == [
            ("DELETE", f"/projects/nodes/{PID}/grants/{GRANT}")
        ]
        assert out.startswith("Removed the grant of «riya@acme.test»"), out


async def test_revoke_of_a_grant_that_is_not_there_shows_no_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    for grant in ("org", str(uuid.uuid4()), "not a subject"):
        calls = _gateway(monkeypatch, LAUNCH)
        out = await skill_projects.revoke_access(PID, grant)
        assert "has no grant like that" in out, (grant, out)
        assert writes(calls) == []
    assert asked == []


async def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch, LAUNCH, grants=[])
    assert await skill_projects.grant_access(PID, "org") == W.CANCELLED
    calls += _gateway(monkeypatch, LAUNCH)
    assert await skill_projects.revoke_access(PID, GRANT) == W.CANCELLED
    assert writes(calls) == []


# ── Fix round 1 ──────────────────────────────────────────────────────────────


async def test_a_personal_tree_is_refused_for_an_admin_too() -> None:
    """D-OP-2, §8 Q4: a personal space is not shared. An admin who passes
    ``may_write_grant`` still gets the personal sentence, and so does a row
    that carries ``personal_owner`` itself, with no query."""
    from gateway.routes.projects import authority as a

    class Personal:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(first=lambda: (1,))

    class NoDb:
        async def execute(self, *_a, **_k):
            raise AssertionError("a row with personal_owner needs no query")

    admin = SimpleNamespace(email="boss@x.test", has_permission=lambda p: p == "projects:settings:write")
    row = await a.attach_can_write_grants(Personal(), admin, {"id": PID})
    assert row == {"id": PID, "can_write_grants": False,
                   "grant_refusal": a.PERSONAL_GRANT_REFUSED}
    own = await a.grant_write_refusal(NoDb(), admin, {"id": PID, "personal_owner": "me@x.test"})
    assert own == a.PERSONAL_GRANT_REFUSED


GROUPS = [{"slug": "design", "display_name": "Design"}, {"slug": "ops", "display_name": "Ops"}]


def _grants_gateway(monkeypatch, node, payload):
    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == f"/projects/nodes/{PID}" and method == "GET":
            return node
        if path == f"/projects/nodes/{PARENT}" and method == "GET":
            return {"id": PARENT, "name": "Product", "parent_project_id": None}
        if path == f"/projects/nodes/{PID}/grants" and method == "GET":
            return payload
        if method == "POST":
            return {"id": str(uuid.uuid4()), "subject": call["json"]["subject"]}
        if method == "DELETE":
            return {"deleted": path.rsplit("/", 1)[-1]}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, answer)


async def test_a_group_that_does_not_exist_shows_no_card_and_lists_the_groups(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = _grants_gateway(monkeypatch, LAUNCH, {"rows": [], "total": 0, "groups": GROUPS})
    out = await skill_projects.grant_access(PID, "group:desing")
    assert out.startswith("Refused: There is no group called «desing»"), out
    assert "«Design» («group:design»), «Ops» («group:ops»)" in out, out
    assert asked == [] and writes(calls) == []

    calls = _grants_gateway(monkeypatch, LAUNCH, {"rows": [], "total": 0, "groups": []})
    out = await skill_projects.grant_access(PID, "group:design")
    assert "This organization has no groups." in out, out
    assert asked == [] and writes(calls) == []

    # A group that exists goes on to the card.
    calls = _grants_gateway(monkeypatch, LAUNCH, {"rows": [], "total": 0, "groups": GROUPS})
    await skill_projects.grant_access(PID, "group:Design")
    assert len(asked) == 1 and len(writes(calls)) == 1


def _impact(card: dict) -> str:
    [line] = [x for x in card["context"].splitlines() if x.startswith("impact: ")]
    return line


async def test_the_revoke_card_warns_first_on_the_last_grant_and_on_lost_sight(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    top = {**LAUNCH, "parent_project_id": None}
    mine = {"id": GRANT, "subject": "org", "yours": True}

    # The last grant on a top project, and the one that lets the member see it.
    _grants_gateway(monkeypatch, top, {"rows": [mine], "you_see_all": False})
    await skill_projects.revoke_access(PID, GRANT)
    assert _impact(asked[-1]).startswith(
        f"impact: {guarded.LAST_GRANT_CLAUSE} {guarded.LOSE_SIGHT_CLAUSE} access narrows"
    ), asked[-1]

    # Another grant names the member, so only the last-grant clause is gone.
    other = {"id": str(uuid.uuid4()), "subject": "me@x.test", "yours": True}
    _grants_gateway(monkeypatch, top, {"rows": [mine, other], "you_see_all": False})
    await skill_projects.revoke_access(PID, GRANT)
    assert _impact(asked[-1]).startswith("impact: access narrows"), asked[-1]

    # A grant of somebody else, with another left: no clause.
    theirs = {"id": GRANT, "subject": "riya@acme.test", "yours": False}
    _grants_gateway(monkeypatch, top, {"rows": [theirs, other], "you_see_all": False})
    await skill_projects.revoke_access(PID, GRANT)
    assert _impact(asked[-1]).startswith("impact: access narrows"), asked[-1]

    # A sub-project whose parent the member sees: the parent's grants still
    # cover it, so neither clause applies.
    _grants_gateway(monkeypatch, LAUNCH, {"rows": [mine], "you_see_all": False})
    await skill_projects.revoke_access(PID, GRANT)
    assert _impact(asked[-1]).startswith("impact: access narrows"), asked[-1]

    # A member who sees every project keeps sight.
    _grants_gateway(monkeypatch, top, {"rows": [mine], "you_see_all": True})
    await skill_projects.revoke_access(PID, GRANT)
    assert _impact(asked[-1]).startswith(
        f"impact: {guarded.LAST_GRANT_CLAUSE} access narrows"), asked[-1]
