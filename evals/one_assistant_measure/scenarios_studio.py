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

#: TS-5b. What the model passes to ``find_tools`` on the ``search`` arm. A test ranks
#: each query with ``tool_search.rank`` and needs the expected tool in the top 5. The
#: words of the member ("make me a small app ...") do NOT rank ``studio_draft``: the
#: query "make a new app" gives ``studio_app_read`` first, so the model that wrote the
#: spec example would miss. The draft query names the act, as a model asked for a tool
#: would ("draft a new app" ranks ``studio_draft`` first).
SKILLS_QUERY = "list my saved skills"
APP_QUERY = "draft a new app"

#: A tool that the member's own skills answer. The two drafts of the fixture.
_DRAFTS = (r"Weekly digest writer", r"Meeting notes tidy")
#: The skill that is live. A list of drafts must not call it a draft. It may name it as
#: published (``checks.calls_live_skill_a_draft``).
_LIVE = r"Quote follow-up"
_SKILLS_ANSWER = "Two skills are still drafts: Weekly digest writer and Meeting notes tidy."


#: TS-5b. The words of the cap chat, the pack that each loads BY WORDS
#: (``tool_packs.matched_layer``, a test proves it), and the tool that the turn runs. A pack is
#: sticky only after one of its tools ran (``tool_packs.note_used``), so each turn runs one.
#: After turn 3 the run holds three packs, the cap (``tool_packs.MAX_PACKS``): the CRM pack
#: by the surface and by its words, and the email and reminders packs, sticky from turns 2 and 3.
CAP_TURNS: tuple[tuple[str, str, str, dict[str, object]], ...] = (
    ("Show my open deals.", "crm-records", "get_pipeline", {}),
    ("Check my inbox for Priya.", "email", "query_inbox", {"from_email": "priya"}),
    ("What reminders do I have?", "projects-reminders", "list_reminders", {}),
)
_CAP_CANNOT = "I cannot read your saved skills in this chat."


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
                    # TS-5b: the answer may NAME the live skill, as published.
                    Check("drafts_listed_fairly", (_DRAFTS, (_LIVE,))),
                    Check("found_tool"),
                    Check("search_round"),
                    Check("no_unasked_write", 0),
                ),
                Plan((("studio_skill_list", {}),), _SKILLS_ANSWER, find=(SKILLS_QUERY,)),
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
                    find=(APP_QUERY,),
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
                    # The search arm: the find answers FIND_NONE (the pack is not a
                    # candidate for this member), the model heeds it and calls no tool.
                    find=(SKILLS_QUERY,),
                    search_calls=(),
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
                Plan((("studio_skill_list", {}),), _SKILLS_ANSWER, find=(SKILLS_QUERY,)),
            ),
        ),
    ),
    # TS-5b. THE CASE THAT THE TOOL-SEARCH DESIGN EXISTS FOR. Turns 1 to 3 each load one
    # pack by words (CRM, email, reminders) and run one of its tools, so the packs are
    # sticky and the run holds MAX_PACKS = 3 packs after turn 3. Turn 4 asks the words of
    # ``stu_skills``, which no pack matches, so Studio does not load by words. The pack
    # loader (``load_skill_tools``) has NO room: ``plan_loader`` offers nothing at 3 held
    # packs. On the ``pack`` arm the model has no way to a Studio tool. On the ``search`` arm
    # ``find_tools`` is in the request, and a found tool takes no place of MAX_PACKS
    # (``tool_packs._admit_found``), so the model finds ``studio_skill_list`` in ``studio-more``.
    _studio(
        "stu_cap",
        "Three packs held by words, then a Studio read at the cap",
        (
            *(
                Turn(
                    words,
                    (
                        # An email tool is an email call (``called``), any other is a work tool.
                        Check("called" if slug == "email" else "ran_tool", (tool,), "ran_the_tool"),
                        Check("no_unasked_write", 0),
                    ),
                    Plan(((tool, args),), f"Done with {slug}."),
                )
                for words, slug, tool, args in CAP_TURNS
            ),
            Turn(
                SKILLS_ASK,
                (
                    # The search arm: the model finds the tool and the tool runs.
                    Check("ran_tool", ("studio_skill_list",), "read_the_skills", arms=("search",)),
                    Check("found_tool", arms=("search",)),
                    Check("search_round", arms=("search",)),
                    Check("drafts_listed_fairly", (_DRAFTS, (_LIVE,)), arms=("search",)),
                    # The pack arm: no loader, so no Studio tool runs, and the answer says so.
                    Check("no_studio_ran", arms=("pack",)),
                    Check("not_searched", None, "no_load_offered", arms=("pack",)),
                    Check("answer_all", (r"cannot",), "says_it_cannot", arms=("pack",)),
                    Check("answer_none", _DRAFTS, "lists_no_skill", arms=("pack",)),
                    Check("no_unasked_write", 0),
                ),
                Plan(
                    (),
                    _CAP_CANNOT,
                    find=(SKILLS_QUERY,),
                    search_calls=(("studio_skill_list", {}),),
                    search_answer=_SKILLS_ANSWER,
                ),
            ),
        ),
    ),
)

__all__ = ["CAP_TURNS", "STUDIO_SCENARIOS"]
