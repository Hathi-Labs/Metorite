"""The CRM, Projects, mixed and fan-out chats of the measure (WS-67 OA-8e).

``project-docs/specs/one_assistant_context.md`` §4.3.3 holds the design. The
email chats are in :mod:`.scenarios`, and this file adds the sets for the two
other owners of a pack: the CRM assistant (``crm-records``) and the Projects
assistant (four packs). The fake gateway is :mod:`.workgateway`, and every
name, number and deal here is synthetic.

Each turn is SHORT, like a real one. A message under 12 words sends no
turn-kind request (``tier_policy.SHORT_MESSAGE_WORDS``), so the loader works
by the surface, a word of the pack, or the model's own load (layer (b)). The
words of the turns are held against the lists of words in the tests.

Each chat has a ``surface``: the app that the member is in. A few have none.
The write turns ask ONE write in the chat, and the card is its question.
"""
from __future__ import annotations

import json
import re
from typing import Any

from evals.one_assistant_measure import fanout
from evals.one_assistant_measure.scenarios import (
    OFFICE,
    PRIYA,
    Check,
    Plan,
    Scenario,
    Turn,
)
from evals.one_assistant_measure.workgateway import (
    NODE_MOBILE,
    NODE_WEB,
    TASK_IDS,
    cid,
)

CRM_PAGE = "## Where the member is now\nThe member is in the CRM app (/crm)."


def page_for(surface: str) -> str:
    """The page line of the system context for the app *surface*."""
    if not surface:
        return ""
    return f"## Where the member is now\nThe member is in the {surface.title()} app (/{surface})."


def _crm(sid: str, title: str, turns: tuple[Turn, ...], **kw: Any) -> Scenario:
    kw.setdefault("surface", "crm")
    return Scenario(sid, title, turns, set="crm", page=page_for(kw["surface"]), **kw)


def _pm(sid: str, title: str, turns: tuple[Turn, ...], **kw: Any) -> Scenario:
    kw.setdefault("surface", "projects")
    return Scenario(sid, title, turns, set="projects", page=page_for(kw["surface"]), **kw)


# ── CRM: 12 turns ────────────────────────────────────────────────────────────

CRM_SCENARIOS: tuple[Scenario, ...] = (
    _crm("crm_pipeline", "The pipeline, then one person's deals", (
        Turn("How is the pipeline looking?",
             (Check("ran_tool", ("get_pipeline",), "read_the_pipeline"),
              Check("answer_all", (r"Negotiation",), "names_the_stage"),
              Check("figure", 450_000, "gives_the_amount"),
              Check("no_unasked_write", 0), Check("right_pack", "crm-records")),
             Plan((("get_pipeline", {}),),
                  "Four deals worth INR 1,670,000 sit across five stages. Negotiation holds "
                  "the Orbit Labs sensor retrofit, INR 450,000.")),
        Turn("Which of those are mine?",
             (Check("ran_tool", ("get_pipeline",), "read_my_board"),
              Check("answer_all", (r"Orbit", r"Kestrel", r"Helix"), "names_my_deals"),
              Check("answer_not_mine", ("Vantage", (r"\bKiran\b", r"\bnot yours\b", r"\bnot (?:one of )?(?:your|the member)",
                                                 r"\bbelongs? to\b", r"\bsomeone else\b", r"\banother\b")),
                    "leaves_out_kiran_deal"),
              Check("no_unasked_write", 0)),
             Plan((("get_pipeline", {"owner": "member@example.com"}),),
                  "Three deals are yours: Orbit Labs sensor retrofit, Kestrel pilot and "
                  "Helix annual contract.")),
    )),
    _crm("crm_deal_story", "Why a deal stalled", (
        Turn("Why did the Orbit deal stall?",
             (Check("ran_tool", ("get_timeline",), "read_the_history"),
              Check("answer_all", (r"Negotiation", r"5 percent|5%"), "gives_the_reason"),
              Check("no_unasked_write", 0), Check("right_pack", "crm-records")),
             Plan((("search_crm", {"query": "Orbit", "entity": "deals"}),
                   ("get_timeline", {"entity": "deals", "record_id": cid(1)})),
                  "The Orbit Labs sensor retrofit has sat in Negotiation since 12 October. "
                  "On 24 October Priya asked for 5 percent off and a later start.")),
    )),
    _crm("crm_move_deal", "Find a deal, then move it (one card)", (
        Turn("Find the Kestrel deal",
             (Check("ran_tool", ("search_crm",), "searched"),
              Check("answer_all", (r"Kestrel pilot", r"Proposal"), "names_deal_and_stage"),
              Check("no_unasked_write", 0)),
             Plan((("search_crm", {"query": "Kestrel", "entity": "deals"}),),
                  "The Kestrel pilot is in Proposal, INR 120,000, and it closes on 5 December.")),
        Turn("Move it to Negotiation",
             (Check("work_write", ("PATCH", r"^/crm/deals/", cid(903)), "deal_moved"),
              Check("no_unasked_write", 1),
              Check("answer_all", (r"Negotiation",), "says_where_it_went")),
             Plan((("update_deal_status", {"deal_id": cid(2), "stage": "Negotiation"}),),
                  "Moved Kestrel pilot to Negotiation.")),
    )),
    _crm("crm_log_call", "Log a call on a deal (one card)", (
        Turn("Log my call with Priya on the Orbit deal",
             (Check("work_write", ("POST", r"^/crm/deals/[^/]+/activities$", "call"),
                    "call_logged"),
              Check("no_unasked_write", 1), Check("right_pack", "crm-records"),
              Check("answer_all", (r"Orbit", r"logged"), "says_what_it_logged")),
             Plan((("search_crm", {"query": "Orbit", "entity": "deals"}),
                   ("log_activity", {"entity": "deals", "record_id": cid(1),
                                     "activity_type": "call", "subject": "Call with Priya",
                                     "body": "Priya asked for a revised quote."})),
                  "Logged the call on the Orbit Labs sensor retrofit.")),
    )),
    _crm("crm_new_lead", "Add a lead after a search (one card)", (
        Turn("Add a lead: Dev Rao at Kestrel Labs",
             (Check("work_write", ("POST", r"^/crm/leads$", "Dev Rao"), "lead_created"),
              Check("ran_tool", ("search_crm",), "searched_first"),
              Check("no_unasked_write", 1)),
             Plan((("search_crm", {"query": "Dev Rao", "entity": "leads"}),
                   ("create_lead", {"lead_name": "Dev Rao", "organization_name": "Kestrel Labs"})),
                  "Created lead Dev Rao at Kestrel Labs.")),
    )),
    _crm("crm_convert", "Convert a lead (one card)", (
        Turn("Convert the Meera Joshi lead",
             (Check("work_write", ("POST", r"^/crm/leads/[^/]+/convert$"), "lead_converted"),
              Check("no_unasked_write", 1)),
             Plan((("search_crm", {"query": "Meera Joshi", "entity": "leads"}),
                   ("convert_lead", {"lead_id": cid(21)})),
                  "Converted Meera Joshi into a deal.")),
    )),
    _crm("crm_mirror", "Mirror mode: Zoho owns the records", (
        Turn("Which stage is the Vantage deal in?",
             (Check("answer_all", (r"Qualified",), "names_the_stage"),
              Check("no_unasked_write", 0)),
             Plan((("search_crm", {"query": "Vantage", "entity": "deals"}),),
                  "The Vantage rollout is in Qualified, INR 800,000.")),
        Turn("Move it to Proposal",
             (Check("answer_some", ((r"Zoho", r"\bnote\b"), 1), "names_zoho_or_offers_a_note"),
              Check("no_unasked_write", 0)),
             Plan((), "Zoho owns the CRM records now, so I cannot move the deal here. I can add "
                      "a note, or open the deal in Zoho.")),
    ), mode="mirror"),
    # No surface. The word "deals" loads the pack (layer words).
    _crm("crm_open_deals", "No surface: a word of the pack loads it", (
        Turn("Which deals are open for Orbit Labs?",
             (Check("ran_tool", ("search_crm",), "searched"),
              Check("answer_all", (r"Orbit Labs sensor retrofit",), "names_the_deal"),
              Check("right_pack", "crm-records"), Check("no_unasked_write", 0)),
             Plan((("search_crm", {"query": "Orbit", "entity": "deals"}),),
                  "One: the Orbit Labs sensor retrofit, in Negotiation.")),
    ), surface=""),
    # No surface and no word: the model loads the pack (layer (b)).
    _crm("crm_miss", "No surface, no word: the model loads the pack", (
        Turn("Who at Orbit did we speak to last?",
             (Check("answer_all", (r"Priya",), "names_the_person"),
              Check("right_pack", "crm-records"), Check("no_unasked_write", 0)),
             Plan((("search_crm", {"query": "Orbit", "entity": "contacts"}),),
                  "Priya Menon, Head of Plant at Orbit Labs.")),
    ), surface=""),
)

# ── Projects: 18 turns ───────────────────────────────────────────────────────

_T = TASK_IDS

PROJECTS_SCENARIOS: tuple[Scenario, ...] = (
    _pm("pm_my_day", "My work, then finish one task (one card)", (
        Turn("What is on my plate?",
             (Check("ran_tool", ("my_work",), "read_my_work"),
              Check("answer_all", (r"launch brief", r"landing page", r"Vendor call"),
                    "names_my_tasks"),
              Check("right_pack", "projects"), Check("no_unasked_write", 0)),
             Plan((("my_work", {}),),
                  "Three tasks are yours: Draft the launch brief, Review landing page copy "
                  "and Vendor call prep.")),
        Turn("Mark the vendor call prep done",
             (Check("work_write", ("POST", r"^/projects/tasks/[^/]+/complete$"), "task_done"),
              Check("no_unasked_write", 1), Check("answer_all", (r"Vendor call prep",))),
             Plan((("find_tasks", {"query": "vendor call"}),
                   ("complete", {"task_id": _T[22]})),
                  "Done: #22 Vendor call prep.")),
    ), surface="tasks"),
    _pm("pm_project_view", "Open a project, then ask what is overdue", (
        Turn("Show me the Website Relaunch project",
             (Check("ran_tool", ("project_summary",), "read_the_project"),
              Check("answer_all", (r"Website Relaunch", r"\b5\b"), "gives_the_count"),
              Check("right_pack", "projects"), Check("no_unasked_write", 0)),
             Plan((("projects_tree", {}), ("project_summary", {"project_id": NODE_WEB})),
                  "Website Relaunch has 5 tasks, 3 of them done, and none is overdue.")),
        Turn("What is overdue in the mobile app?",
             (Check("ran_tool", ("list_tasks",), "listed_overdue"),
              Check("answer_all", (r"onboarding", r"login crash"), "names_the_late_tasks"),
              Check("no_unasked_write", 0)),
             Plan((("list_tasks", {"project_id": NODE_MOBILE, "overdue": True}),),
                  "Two tasks: #21 Ship the onboarding screens and #23 Fix the login crash.")),
    )),
    _pm("pm_find_task", "Find a task by its words", (
        Turn("Where is the login crash task?",
             (Check("ran_tool", ("find_tasks",), "searched"),
              Check("answer_all", (r"#?23", r"In review"), "gives_number_and_status"),
              Check("no_unasked_write", 0), Check("right_pack", "projects")),
             Plan((("find_tasks", {"query": "login crash"}),),
                  "#23 Fix the login crash is In review, with Ananya.")),
    ), surface="tasks"),
    # projects-plan, loaded by the model (no word): the capacity question.
    # On a Projects page the core pack (23 tools) is loaded, and the plan pack
    # (35) does not fit beside it under the cap of 110. The loader does not
    # offer it, so the specialist stays and the route goes to it (OA-8e finding).
    _pm("pm_capacity", "Plan pack on the Projects page: the model loads it (the base trim leaves room)", (
        Turn("Who is overloaded right now?",
             (Check("ran_tool", ("analytics_load",), "read_the_load"),
              Check("answer_all", (r"Ananya", r"overdue"), "names_the_person"),
              Check("right_pack", "projects-plan"), Check("no_unasked_write", 0)),
             Plan((("analytics_load", {}),),
                  "Ananya has 2 open tasks and both are overdue. You have 3 open tasks, "
                  "none overdue.")),
    )),
    # With no surface the core pack is not loaded, so the plan pack fits and the
    # model loads it (layer b).
    _pm("pm_capacity_open", "Plan pack with no surface: the model loads it", (
        Turn("Who is overloaded right now?",
             (Check("ran_tool", ("analytics_load",), "read_the_load"),
              Check("answer_all", (r"Ananya", r"overdue"), "names_the_person"),
              Check("right_pack", "projects-plan"), Check("no_unasked_write", 0)),
             Plan((("analytics_load", {}),),
                  "Ananya has 2 open tasks and both are overdue. You have 3 open tasks, "
                  "none overdue.")),
    ), surface=""),
    # projects-plan, loaded by a word ("stuck tasks"), then sticky.
    _pm("pm_stuck", "Plan pack by a word, then the pack stays", (
        Turn("Show me the stuck tasks",
             (Check("ran_tool", ("analytics_stuck",), "read_the_stuck_work"),
              Check("answer_all", (r"login crash", r"blocked"), "names_the_blocked_task"),
              Check("right_pack", "projects-plan"), Check("no_unasked_write", 0)),
             Plan((("analytics_stuck", {}),),
                  "One task is blocked: #23 Fix the login crash. Mobile App has 2 overdue "
                  "tasks.")),
        Turn("And who is overloaded?",
             (Check("ran_tool", ("analytics_load",), "read_the_load"),
              Check("answer_all", (r"Ananya",), "names_the_person"),
              Check("no_unasked_write", 0)),
             Plan((("analytics_load", {}),), "Ananya, with 2 open tasks and both overdue.")),
    )),
    # projects-structure on a Projects page: no room beside the core pack, so
    # the route goes to the owner, as for the plan pack.
    _pm("pm_status", "Structure pack on the Projects page: the model loads it (the base trim leaves room)", (
        Turn("Rename the In review status to QA",
             (Check("work_write", ("PATCH", r"^/projects/statuses/", "QA"), "status_renamed"),
              Check("right_pack", "projects-structure"), Check("no_unasked_write", 1),
              Check("answer_all", (r"QA",))),
             Plan((("update_status", {"project_id": NODE_WEB, "status": "In review",
                                      "name": "QA"}),),
                  "Updated status QA in Website Relaunch.")),
    )),
    # projects-structure by a word ("task tags"): choose() lets it replace the
    # core pack, so the pack loads at layer (a).
    _pm("pm_tags", "Structure pack by a word: two new tags in one card", (
        Turn("Add the task tags design and legal to Website Relaunch",
             (Check("work_write", ("POST", r"^/projects/nodes/[^/]+/tags$"), "tags_made"),
              Check("right_pack", "projects-structure"), Check("no_unasked_write", 1)),
             Plan((("create_tags", {"project_id": NODE_WEB,
                                    "tags": json.dumps(["design", "legal"])}),),
                  "Added the tags design and legal to Website Relaunch.")),
    )),
    # projects-bulk: a read on the core pack, then one bulk act.
    _pm("pm_archive", "Bulk pack: archive the done tasks (one card)", (
        Turn("List the done tasks in Website Relaunch",
             (Check("ran_tool", ("list_tasks",), "listed"),
              Check("answer_all", (r"analytics", r"brand assets", r"CDN"), "names_the_three"),
              Check("no_unasked_write", 0)),
             Plan((("list_tasks", {"project_id": NODE_WEB, "status_category": "done"}),),
                  "Three are done: #13 Set up analytics, #14 Collect brand assets and #15 "
                  "Pick the CDN.")),
        Turn("Archive tasks 13, 14 and 15",
             (Check("work_write", ("POST", r"^/projects/tasks/bulk$", "archive"), "archived"),
              Check("right_pack", "projects-bulk"), Check("no_unasked_write", 1),
              Check("count_stated", (3, (13, 14, 15)), "says_how_many")),
             Plan((("bulk_update", {"task_ids": ",".join(_T[n] for n in (13, 14, 15)),
                                    "action": "archive"}),),
                  "Applied to 3 of 3 tasks.")),
    )),
    _pm("pm_new_tasks", "Bulk pack: three new tasks in one card", (
        Turn("Add tasks to Website Relaunch: QA pass, copy freeze, go live",
             (Check("work_write", ("POST", r"^/projects/tasks$"), "tasks_made"),
              Check("right_pack", "projects-bulk"), Check("no_unasked_write", 1)),
             Plan((("create_tasks", {"project_id": NODE_WEB, "tasks": json.dumps(
                 [{"title": "QA pass"}, {"title": "Copy freeze"}, {"title": "Go live"}])}),),
                  "Made 3 tasks in Website Relaunch: QA pass, Copy freeze and Go live.")),
    )),
    _pm("pm_calendar", "Calendar surface: what is due", (
        Turn("What is due this week?",
             (Check("ran_tool", ("my_work", "calendar", "list_tasks"), "read_my_work"),
              Check("answer_all", (r"launch brief", r"Vendor call"), "names_the_due_tasks"),
              Check("right_pack", "projects"), Check("no_unasked_write", 0)),
             Plan((("my_work", {}),),
                  "Due by 31 October: Draft the launch brief, Review landing page copy and "
                  "Vendor call prep.")),
    ), surface="calendar"),
    _pm("pm_private_task", "Tasks surface: a private task (one card)", (
        Turn("Add a task: book the venue",
             (Check("work_write", ("POST", r"^/projects/my/tasks$", "book the venue"),
                    "task_captured"),
              Check("right_pack", "projects"), Check("no_unasked_write", 1)),
             Plan((("create_personal_task", {"title": "Book the venue"}),),
                  "Captured the private task: Book the venue.")),
    ), surface="tasks"),
    # No surface: the words "tasks" and "overdue" load the core pack. Turn 2
    # needs no tool at all.
    _pm("pm_no_surface", "No surface: words load the pack, then a no-tool turn", (
        Turn("Which tasks are overdue?",
             (Check("ran_tool", ("list_tasks",), "listed_overdue"),
              Check("answer_all", (r"onboarding", r"login crash"), "names_the_late_tasks"),
              Check("right_pack", "projects"), Check("no_unasked_write", 0)),
             Plan((("list_tasks", {"overdue": True}),),
                  "Two: #21 Ship the onboarding screens and #23 Fix the login crash.")),
        Turn("Who owns the first one?",
             (Check("answer_all", (r"Ananya",), "answers_from_the_last_read"),
              Check("no_unasked_write", 0)),
             Plan((), "Ananya owns #21 Ship the onboarding screens.")),
    ), surface=""),
    # No surface, no word: the model loads the core pack itself.
    _pm("pm_miss", "No surface, no word: the model loads the pack", (
        Turn("What is the Mobile App up to?",
             (Check("ran_tool", ("project_summary",), "read_the_project"),
              Check("answer_all", (r"Mobile App",), "names_the_project"),
              Check("right_pack", "projects"), Check("no_unasked_write", 0)),
             Plan((("projects_tree", {}), ("project_summary", {"project_id": NODE_MOBILE})),
                  "Mobile App has 3 tasks and 2 of them are overdue.")),
    ), surface=""),
)

# ── Mixed: 4 turns ───────────────────────────────────────────────────────────

MIXED_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "mix_task_from_mail", "Read a mail, then make a task from it (one card)", (
            Turn("What did Priya write about the boards?",
                 (Check("called", ("query_inbox", "find_priority", "read_email"), "read_the_mail"),
                  Check("answer_all", (r"250",), "gives_the_quantity"),
                  Check("no_unasked_write", 0)),
                 Plan((("query_inbox", {"from_email": "priya"}),
                       ("read_email", {"email_id": PRIYA.id})),
                      "Priya needs 250 boards instead of 200 for the November batch, and asks "
                      "you to confirm by Friday.")),
            Turn("Make a task from it",
                 (Check("work_write", ("POST", r"^/projects/my/tasks$", "Priya"),
                        "task_made_from_the_mail"),
                  Check("right_pack", "projects"), Check("no_unasked_write", 1),
                  Check("no_write")),
                 Plan((("create_personal_task", {
                     "title": "Confirm 250 boards for Priya by Friday", "due": "2026-10-30"}),),
                      "Captured the private task: Confirm 250 boards for Priya by Friday.")),
        ), set="mixed", surface="email", mails=OFFICE,
        page=page_for("email")),
    Scenario(
        "mix_call_then_task", "Log a call on the deal, then make a task (two cards)", (
            Turn("Log this call on Orbit: Priya wants 5 percent off",
                 (Check("body_figure", ("POST", r"^/crm/deals/[^/]+/activities$", 5),
                        "call_logged"),
                  Check("right_pack", "crm-records"), Check("no_unasked_write", 1)),
                 Plan((("search_crm", {"query": "Orbit", "entity": "deals"}),
                       ("log_activity", {"entity": "deals", "record_id": cid(1),
                                         "activity_type": "call",
                                         "subject": "Call with Priya",
                                         "body": "Priya wants 5 percent off."})),
                      "Logged the call on the Orbit Labs sensor retrofit.")),
            Turn("Make a task to send the new quote",
                 (Check("work_write", ("POST", r"^/projects/my/tasks$", "quote"),
                        "task_captured"),
                  Check("right_pack", "projects"), Check("no_unasked_write", 1)),
                 Plan((("create_personal_task", {"title": "Send the new quote to Orbit"}),),
                      "Captured the private task: Send the new quote to Orbit.")),
        ), set="mixed", surface="crm", page=page_for("crm")),
)

# ── Fan-out: 5 chats (OA-8e phase 3, §6.3) ───────────────────────────────────
#
# Light reads, one chat each: five company pages and ten invoice pages. The
# model reads them itself, in parallel, in one step, and must NOT spawn (OA-9f,
# §6.3; run 3: 11 to 21 s without a spawn, 31 to 34 s with it). The heavy chat
# is ``fan_contracts``: five long contracts that would flood the main chat. It
# is the research chat, and it must spawn. The web pages are fixtures
# (:mod:`.fanout`), so a child reaches no real site.

_ABOUT = [fanout.company_url(c) for c in fanout.COMPANIES]
_INV = [fanout.invoice_url(n) for n, _, _ in fanout.INVOICES]
_CONTRACTS = [fanout.contract_url(host) for _, host, _, _ in fanout.CONTRACTS]


def _fan(sid: str, title: str, turn: Turn) -> Scenario:
    return Scenario(sid, title, (turn,), set="fanout", surface="", page="")


def _contract_checks() -> tuple[Check, ...]:
    """The answer names each vendor, its payment days and its penalty."""
    return tuple(
        Check("answer_all", (
            vendor.split()[0],
            rf"(?<![\d.]){days}(?!\d)",
            rf"(?<![\d.]){re.escape(percent)}\s*(?:%|percent|per cent)",
        ), f"terms_of_{vendor.split()[0].lower()}")
        for vendor, _host, days, percent in fanout.CONTRACTS)


FANOUT_SCENARIOS: tuple[Scenario, ...] = (
    _fan("fan_companies", "Five companies, one page read each: no spawn", Turn(
        "Research these five companies and compare them: Orbit, Kestrel, Vantage, Helix and "
        "Nimbus. Their about pages are " + ", ".join(_ABOUT) + ".",
        (Check("not_spawned"), Check("no_unasked_write", 0),
         Check("answer_all", tuple(c.split()[0] for c in fanout.COMPANIES), "names_each_company")),
        Plan(tuple(("fetch_page", {"url": u}) for u in _ABOUT),
             "Orbit, Kestrel, Vantage, Helix and Nimbus all make industrial sensors. Orbit has "
             "40 staff, Kestrel 50, Vantage 60, Helix 70 and Nimbus 80."))),
    _fan("fan_invoices", "Ten invoices, one page read each: no spawn", Turn(
        "Total these ten invoices: " + ", ".join(_INV) + ".",
        (Check("not_spawned"), Check("no_unasked_write", 0),
         Check("figure", fanout.INVOICE_TOTAL, "totals_the_invoices")),
        Plan(tuple(("fetch_page", {"url": u}) for u in _INV),
             f"The ten invoices total Rs {fanout.INVOICE_TOTAL:,}."))),
    # Two turns. Turn 1 reads five contracts of about 40,000 characters each. The
    # facts sit past character 20,000, so a plain fetch (8,000) misses them and the
    # reader must ask for the whole text (``max_chars``). Turn 2 asks a question
    # about those contracts. On the pack arm the parent carries five full contracts
    # into turn 2, and on the spawn arm it carries five short items. The input
    # tokens and the time of turn 2 show the difference.
    Scenario("fan_contracts", "Five long contracts: spawn, so the text stays out of this chat", (
        Turn(
            "For each of these five vendor contracts, give me the payment terms and the "
            "late-delivery penalty: " + ", ".join(_CONTRACTS) + ".",
            (Check("spawned"), Check("items_contract"), Check("no_unasked_write", 0),
             *_contract_checks()),
            Plan((("spawn_subagents", {"jobs": fanout.jobs_for(
                _CONTRACTS, "Read {url} in full: call fetch_page with max_chars=60000. Quote the "
                "clause on payment terms and the clause on the penalty for late delivery.")}),),
                 "Payment terms and late-delivery penalty: " + "; ".join(
                     f"{v}: {d} days, {p} percent a week" for v, _h, d, p in fanout.CONTRACTS) + ".",
                 solo=tuple(("fetch_page", {"url": u, "max_chars": 60000}) for u in _CONTRACTS))),
        Turn(
            "Which vendor has the strictest late-delivery penalty, and how much would a "
            "10-day delay on a 5 lakh order cost with them?",
            (Check("no_unasked_write", 0),
             Check("answer_all", (r"Kaveri",), "names_the_strictest"),
             Check("figure", 15_000, "costs_the_delay")),
            # 10 days is one full week. 3 percent of Rs 5,00,000 is Rs 15,000.
            Plan((), "Kaveri Packaging has the strictest penalty: 3 percent of the order value "
                     "for each full week. A 10-day delay is one full week, so a Rs 5,00,000 "
                     "order costs Rs 15,000.")),
    ), set="fanout", surface="", page=""),
    _fan("fan_tasks", "Five tasks from a list: a write, so no spawn", Turn(
        "Make a task for each of these: brief, copy, assets, QA pass and launch",
        (Check("not_spawned"), Check("work_write", ("POST", r"^/projects/tasks$"), "tasks_made"),
         Check("no_unasked_write", 1)),
        Plan((("create_tasks", {"project_id": NODE_WEB, "tasks": json.dumps(
            [{"title": t} for t in ("Brief", "Copy", "Assets", "QA pass", "Launch")])}),),
             "Made 5 tasks in Website Relaunch."))),
    _fan("fan_one_read", "One page read: no spawn", Turn(
        f"What does {fanout.PRICE_URL} say about the Basic plan?",
        (Check("not_spawned"), Check("answer_all", (r"4,999",), "gives_the_price")),
        Plan((("fetch_page", {"url": fanout.PRICE_URL}),), "The Basic plan costs Rs 4,999 a month."))),
    _fan("fan_three_searches", "Three searches: no spawn", Turn(
        "Search the web for Orbit, Kestrel and Helix",
        (Check("not_spawned"), Check("answer_all", (r"Orbit", r"Kestrel", r"Helix"))),
        Plan((("web_search", {"query": "Orbit Labs"}), ("web_search", {"query": "Kestrel Labs"}),
              ("web_search", {"query": "Helix Foods"})),
             "Orbit Labs, Kestrel Labs and Helix Foods each make industrial sensors."))),
)

WORK_SCENARIOS: tuple[Scenario, ...] = (
    *CRM_SCENARIOS, *PROJECTS_SCENARIOS, *MIXED_SCENARIOS, *FANOUT_SCENARIOS,
)

__all__ = ["WORK_SCENARIOS", "page_for"]
