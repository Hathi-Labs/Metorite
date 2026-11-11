"""The five cases, and the known-good tool sequence of each one (scripted mode).

Each case is one member turn to the chat assistant (``orchestrator``, "Mira"),
asked from the Projects app. The scripted plan is the path that the failed chat
of 2026-11-11 should have taken: search each name alone, read the mail that the
search finds, hand the write to ``projects-assistant`` with the facts and the
source mail, and answer from what was read.

The plan is data for ``ScriptedModel``-style replay (``ReplyModel``). A live
run ignores it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from acb_skills import tool_packs

from evals.email_to_projects import checkers as ck
from evals.email_to_projects import fixtures as fx
from evals.routing.harness import text_turn, tool_turn

PROJECTS_PAGE = "## Where the member is now\nThe member is in the Projects app (/projects)."
PROJECTS_AGENT = "projects-assistant"
#: The id that the fake gives to the first project that a run creates.
FIRST_NEW_ID = "7a5e0000-0000-4000-9000-000000000901"

#: The facts of the Aroh mails. A create of Aroh carries one of them.
AROH_FACTS = ("30 Nov", "4,00,000", "dashboard", "data export")


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    prompt: str
    #: The names that the member gave. Each one must be searched alone.
    names: tuple[str, ...]
    #: The turns before this one, as the browser sends them.
    history: tuple[dict[str, str], ...] = ()
    #: False: ``GET /projects/nodes/<id>/updates`` answers 404 (the flag is off).
    updates_on: bool = True
    #: The rules that bind this case beside the five common rules.
    extra: Callable[[ck.Evidence], list[ck.Rule]] = field(default=lambda ev: [])
    #: ``(orchestrator turns, projects-assistant turns)`` of the known-good run.
    plan: Callable[[], tuple[list[Any], list[Any]]] = field(default=lambda: ([], []))



def _load(*slugs: str) -> list[Any]:
    """The model loads each pack itself (layer b). A pack that is loaded already answers so."""
    return [tool_turn(tool_packs.LOAD_TOOL, {"pack": slug}, call_id=f"load_{slug}")
            for slug in slugs]


def _read_each(names: tuple[str, ...], n: int = 0) -> tuple[list[Any], int]:
    """Search each name alone and read each mail that the search finds."""
    turns: list[Any] = []
    from evals.email_to_projects.gateway import mails_for

    for name in names:
        n += 1
        turns.append(tool_turn("search_company", {"query": name}, call_id=f"s{n}"))
        for m in mails_for(name):
            n += 1
            turns.append(tool_turn("read_email", {"email_id": m.id}, call_id=f"r{n}"))
    return turns, n


def _source(m: Any) -> str:
    return f"Source mail: «{m.subject}» from {m.sender} (email {m.id})"


# ── (a) update every project status from the mail ────────────────────────────

PROMPT_A = "Update all project statuses based on the emails so far."


def _update_calls() -> list[Any]:
    out = []
    for i, name in enumerate(fx.EXISTING):
        mail, health = fx.STATUS_MAIL[name]
        body = " ".join(mail.body.split("\n\n")[1].split()) + " " + _source(mail)
        out.append(tool_turn("post_project_update", {
            "project_id": fx.PROJECT_IDS[name], "health": health, "body": body,
        }, call_id=f"u{i}"))
    return out


def _brief_a() -> str:
    lines = [f"- {n}: post an update, health {fx.STATUS_MAIL[n][1]}. {_source(fx.STATUS_MAIL[n][0])}"
             for n in fx.EXISTING]
    return "Post one status update on each project. Each update names its source mail.\n" + "\n".join(lines)


def plan_a() -> tuple[list[Any], list[Any]]:
    orch = [*_load("projects", "email"), tool_turn("projects_tree", {}, call_id="tree")]
    reads, _ = _read_each(fx.EXISTING)
    orch += reads
    orch.append(tool_turn("call_agent", {"agent_name": PROJECTS_AGENT, "message": _brief_a()},
                          call_id="hand"))
    orch.append(text_turn(
        "I read the mail of each project and posted one update on each, with its source mail. "
        "Vozzo Creditvision is at risk: UAT sign-off moves to 20 Nov («Vozzo Creditvision: UAT "
        "sign-off moves to 20 Nov»). TATV is on track: two of three sites are live. Kestrel Rollout "
        "is on track: the vendor contract is signed. Lumen Billing is off track: the go-live is on "
        "hold until December."))
    sub = [*_update_calls(), text_turn("Posted four updates, one on each project.")]
    return orch, sub


def rules_a(ev: ck.Evidence) -> list[ck.Rule]:
    done = {ev.node_names.get(str(c.args.get("project_id")), "")
            for c in ev.calls if c.name == "post_project_update"}
    missing = [n for n in fx.EXISTING if n not in done]
    return [ck.Rule("updated_every_project", not missing,
                    f"no update call for {missing}" if missing else "")]


# ── (b) add a new project and check the mail for it ──────────────────────────

PROMPT_B = ("Add Aroh as a new product project, and Attero as a new project under Aroh. "
            "Check the mail for each one first.")


def plan_b() -> tuple[list[Any], list[Any]]:
    # The words "new project" pull ``projects-structure``, which the cap refuses, so the
    # orchestrator does not read the tree. The projects assistant reads it (as on 2026-11-11).
    orch = [*_load("email")]
    reads, _ = _read_each((fx.NEW_PRODUCT, fx.NEW_CHILD))
    orch += reads
    brief = (
        "Create the project Aroh under the space Hathi Labs, with this description: the Aroh "
        "pilot needs the dashboard module by 30 Nov, budget INR 4,00,000, and one owner for the "
        "data export. Then create the project Attero under Aroh: sandbox API keys sent, kickoff "
        f"Monday. {_source(fx.AROH_1)}. {_source(fx.ATTERO)}.")
    orch.append(tool_turn("call_agent", {"agent_name": PROJECTS_AGENT, "message": brief},
                          call_id="hand"))
    orch.append(text_turn(
        "I made both projects after I read the mail. Aroh holds the pilot scope («Pilot scope for "
        "the first release»: dashboard module by 30 Nov, budget INR 4,00,000) and the question "
        "of an owner for the data export. Attero is under Aroh. Its mail says the sandbox API keys "
        "are sent, and the kickoff call is on Monday."))
    sub = [
        tool_turn("projects_tree", {}, call_id="t1"),
        tool_turn("create_project", {
            "name": fx.NEW_PRODUCT, "parent_project_id": fx.SPACE, "kind": "project",
            "description": ("Pilot for the first release. The dashboard module is due 30 Nov. "
                            "Budget approved: INR 4,00,000. Open: one owner for the data export. "
                            + _source(fx.AROH_1))}, call_id="c1"),
        tool_turn("create_project", {
            "name": fx.NEW_CHILD, "parent_project_id": FIRST_NEW_ID, "kind": "project",
            "description": ("Integration with Attero. Sandbox API keys are sent. Kickoff call on "
                            "Monday at 10:00. Open: the webhook list. " + _source(fx.ATTERO))},
            call_id="c2"),
        text_turn("Created Aroh under Hathi Labs and Attero under Aroh."),
    ]
    return orch, sub


def rules_b(ev: ck.Evidence) -> list[ck.Rule]:
    parent = {str(c.args.get("name")): str(c.args.get("parent_project_id") or "")
              for c in ev.calls if c.name == "create_project"}
    ids = {n: i for i, n in ev.node_names.items()}
    under = parent.get(fx.NEW_CHILD) == ids.get(fx.NEW_PRODUCT)
    return [
        ck.wrote(ev, (fx.NEW_PRODUCT, fx.NEW_CHILD)),
        ck.facts_in_write(ev, fx.NEW_PRODUCT, AROH_FACTS),
        ck.facts_in_write(ev, fx.NEW_CHILD, ("API keys", "Kickoff", "webhook")),
        ck.Rule("attero_under_aroh", under, "" if under else "Attero is not under Aroh"),
    ]


# ── (c) the member says: check again ─────────────────────────────────────────

PROMPT_C = "Check again, there are mails about Aroh."
HISTORY_C = (
    {"role": "user", "content": "Add Aroh as a new product project. Look in the mail for it."},
    {"role": "assistant", "content": "I found no mail about Aroh, so I made a one-line project."},
)


def plan_c() -> tuple[list[Any], list[Any]]:
    orch = [*_load("email")]
    reads, _ = _read_each((fx.NEW_PRODUCT,))
    orch += reads
    orch.append(text_turn(
        "You are right, and my first answer was wrong. Two mails come from the aroh.si domain. "
        "Ravi Menon wrote «Pilot scope for the first release»: the dashboard module by 30 Nov, "
        "budget INR 4,00,000. Nisha Pillai wrote «Who owns the data export?»: Aroh needs one "
        "owner on our side. Shall I add these facts to the Aroh project?"))
    return orch, []


def rules_c(ev: ck.Evidence) -> list[ck.Rule]:
    read = {c.args.get("email_id") for c in ev.calls if c.name == "read_email"}
    want = {fx.AROH_1.id, fx.AROH_2.id}
    return [ck.Rule("read_every_aroh_mail", want <= read,
                    "" if want <= read else f"unread: {sorted(want - read)}"),
            ck.wrote_nothing(ev)]


# ── (d) project updates are refused ──────────────────────────────────────────

PROMPT_D = PROMPT_A


def plan_d() -> tuple[list[Any], list[Any]]:
    orch, _ = plan_a()
    orch = [*orch[:-1], text_turn(
        "Project updates are not on for this organization yet, so I posted nothing. "
        "Here is what the mail says. Vozzo Creditvision: UAT sign-off moves to 20 Nov. "
        "TATV: two of three sites are live. Kestrel Rollout: the vendor contract is signed. "
        "Lumen Billing: the go-live is on hold until December.")]
    first = fx.EXISTING[0]
    mail, health = fx.STATUS_MAIL[first]
    sub = [tool_turn("post_project_update", {
        "project_id": fx.PROJECT_IDS[first], "health": health,
        "body": "UAT sign-off moves to 20 Nov. " + _source(mail)}, call_id="u0"),
        text_turn("Project updates are not on for this organization yet. I posted nothing.")]
    return orch, sub


def rules_d(ev: ck.Evidence) -> list[ck.Rule]:
    return [ck.refusal_honest(ev)]


# ── (e) a name with no mail at all ───────────────────────────────────────────

PROMPT_E = "Add Zephyr as a new project, and check the mail for it first."


def plan_e() -> tuple[list[Any], list[Any]]:
    orch = [*_load("email")]
    orch.append(tool_turn("search_company", {"query": fx.NO_MAIL}, call_id="s1"))
    orch.append(tool_turn("query_inbox", {"query": fx.NO_MAIL}, call_id="s2"))
    orch.append(text_turn(
        "I found no mail about Zephyr. I searched the company records for «Zephyr», and I searched "
        "the inbox for «Zephyr». Both searches for Zephyr came back empty. Do you want me to "
        "make the project "
        "Zephyr with no mail behind it, or do you know another spelling of Zephyr?"))
    return orch, []


def rules_e(ev: ck.Evidence) -> list[ck.Rule]:
    return [ck.says_what_was_searched(ev, fx.NO_MAIL), ck.wrote_nothing(ev)]


CASES: tuple[Case, ...] = (
    Case("EP-1", "Update every project status from the mail", PROMPT_A, fx.EXISTING,
         extra=rules_a, plan=plan_a),
    Case("EP-2", "Add a new project and check the mail for it", PROMPT_B,
         (fx.NEW_PRODUCT, fx.NEW_CHILD), extra=rules_b, plan=plan_b),
    Case("EP-3", "The member says: check again", PROMPT_C, (fx.NEW_PRODUCT,),
         history=HISTORY_C, extra=rules_c, plan=plan_c),
    Case("EP-4", "Project updates are refused", PROMPT_D, fx.EXISTING,
         updates_on=False, extra=rules_d, plan=plan_d),
    Case("EP-5", "A name with no mail at all", PROMPT_E, (fx.NO_MAIL,),
         extra=rules_e, plan=plan_e),
)


def select(spec: str) -> list[Case]:
    if spec == "all":
        return list(CASES)
    by_id = {c.id: c for c in CASES}
    out = []
    for part in spec.split(","):
        if part.strip() not in by_id:
            raise ValueError(f"no case {part.strip()!r}: the cases are {', '.join(by_id)}")
        out.append(by_id[part.strip()])
    return out
