"""The Studio chats of the measure (WS-67 TS-5a, ``one_assistant_context.md`` §4.2.9).

Five chats test one thing: a tool that no layer loaded before the first request.
Layer (a) must NOT load the ``studio`` pack for these chats. The surface is
``crm`` or none, and no word of any pack is in the words. So the model must load
the pack itself (``load_skill_tools``, the ``pack`` arm) or search
(``find_tools``, the ``search`` arm, TS-5b). A test holds the words of every chat
against the words of every pack with ``tool_packs.matched_layer``.

The chats sit outside ``WORK_SCENARIOS`` on purpose. The delegation arm has no
Studio specialist (``studio-builder`` takes no hand-off), so only the ``pack`` and
``search`` arms run them (``run.arms_for``). Each chat is under 12 words, so no
turn-kind request goes out. Do NOT use ``list_workflows`` here: it reads the
local database in process (``workflow_tools.py``). The fake gateway answers the
Studio read routes (:mod:`.workgateway`), and no chat writes.
"""

from __future__ import annotations

from evals.one_assistant_measure.scenarios import PRIYA, Check, Plan, Scenario, Turn, _link
from evals.one_assistant_measure.scenarios_work import page_for


def pack_of(tool: str) -> str:
    """The pack that OWNS *tool*, read from ``tool_packs`` (never hard-coded). After
    TS-1b the core ``studio`` holds 10 tools and ``studio-more`` the other 13."""
    from acb_skills import tool_packs

    return next(slug for slug, found in tool_packs.PACKS.items() if tool in found.tools)


SKILLS_ASK = "Which of my saved skills are still drafts?"
APP_ASK = "Make me a small app to log visitor badges."
PLAIN_ASK = "Give me a one-line thank-you note for the team."
MAIL_ASK = "What does Priya want about the board order?"

#: A tool that the member's own skills answer. The two drafts of the fixture.
_DRAFTS = (r"Weekly digest writer", r"Meeting notes tidy")
#: The skill that is live. A list of drafts must not name it.
_LIVE = r"Quote follow-up"
_SKILLS_ANSWER = "Two skills are still drafts: Weekly digest writer and Meeting notes tidy."


def _studio(sid: str, title: str, turns: tuple[Turn, ...], **kw: object) -> Scenario:
    kw.setdefault("surface", "crm")
    return Scenario(sid, title, turns, set="studio", page=page_for(str(kw["surface"])), **kw)  # type: ignore[arg-type]


STUDIO_SCENARIOS: tuple[Scenario, ...] = (
    _studio(
        "stu_skills",
        "Read the saved skills from the CRM app",
        (
            Turn(
                SKILLS_ASK,
                (
                    Check("ran_tool", ("studio_skill_list",), "read_the_skills"),
                    Check("right_pack", pack_of("studio_skill_list")),
                    Check("answer_all", _DRAFTS, "names_the_drafts"),
                    Check("answer_none", (_LIVE,), "leaves_out_the_live_skill"),
                    Check("found_tool"),
                    Check("search_round"),
                    Check("no_unasked_write", 0),
                ),
                Plan((("studio_skill_list", {}),), _SKILLS_ANSWER),
            ),
        ),
    ),
    _studio(
        "stu_new_app",
        "Draft a new app, with no app open",
        (
            Turn(
                APP_ASK,
                (
                    Check("ran_tool", ("studio_draft",), "drafted_the_app"),
                    Check("right_pack", pack_of("studio_draft")),
                    Check("answer_all", (r"badge",), "names_the_app"),
                    Check("found_tool"),
                    Check("search_round"),
                    Check("no_unasked_write", 0),
                ),
                Plan(
                    (("studio_draft", {"brief": "Log visitor badges"}),),
                    "I drafted a Visitor badges app. Tell me what it should hold, "
                    "and I will add it.",
                ),
            ),
        ),
        surface="",
    ),
    _studio(
        "stu_plain",
        "A plain request that needs no tool",
        (
            Turn(
                PLAIN_ASK,
                (
                    Check("not_searched"),
                    Check("answer_all", (r"thank",), "writes_the_note"),
                    Check("no_unasked_write", 0),
                ),
                Plan((), "Thank you all for the hard work this week. It made a real difference."),
            ),
        ),
    ),
    _studio(
        "stu_no_feature",
        "The member lacks feature:studio",
        (
            Turn(
                SKILLS_ASK,
                (
                    Check("no_studio_names"),
                    Check("answer_none", (*_DRAFTS, _LIVE), "lists_no_skill"),
                    Check("no_studio_ran"),
                    Check("no_unasked_write", 0),
                ),
                # The plan TRIES: it loads the pack and calls the tool, as a model
                # that holds no hint of the gate would. The product answers 'Not loaded.
                # "studio" is not a pack that you can load in this chat.' and 'Function
                # "studio_skill_list" not found.' With the feature ON the tool runs, and
                # no_studio_ran and no_studio_names fail.
                Plan(
                    (("studio_draft", {"brief": "Skills"}), ("studio_skill_list", {})),
                    "I cannot read saved skills in this chat.",
                ),
            ),
        ),
        without_features=("studio",),
    ),
    _studio(
        "stu_held",
        "A chat that read mail, then a Studio read (held)",
        (
            Turn(
                MAIL_ASK,
                (
                    Check("called", ("read_email",), "read_the_mail"),
                    Check("answer_all", (r"\b250\b",), "the_quantity"),
                ),
                Plan(
                    (
                        ("query_inbox", {"from_email": "priya"}),
                        ("read_email", {"email_id": PRIYA.id}),
                    ),
                    f"Priya wants 250 boards instead of 200, and asks you to confirm by "
                    f"Friday. {_link(PRIYA)}",
                ),
            ),
            Turn(
                SKILLS_ASK,
                (
                    Check("ran_tool", ("studio_skill_list",), "read_the_skills"),
                    Check("right_pack", pack_of("studio_skill_list")),
                    Check("answer_all", _DRAFTS, "names_the_drafts"),
                    Check("held_note"),
                    Check("found_tool"),
                    Check("search_round"),
                    Check("no_unasked_write", 0),
                ),
                Plan((("studio_skill_list", {}),), _SKILLS_ANSWER),
            ),
        ),
    ),
)

__all__ = ["STUDIO_SCENARIOS"]
