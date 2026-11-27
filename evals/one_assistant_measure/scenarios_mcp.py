"""The connected-systems chats of the measure (CSY-3, ``chat_connected_systems.md`` §8.2).

Four chats ask where a question goes when an MCP server is attached. The server is a fake
(:mod:`.mcp_fixture`) behind the REAL path. SCRIPTED ONLY: the set has no live arm, because
a live arm spends money and the owner has not opened one (``run.live_refusal``). A scripted
run plays a known-good plan, so it proves the path and the checks, never the model's choice.

| Chat | The question | What the plan does |
|---|---|---|
| ``csy_only_server`` | Only the server holds the fact | Calls the server |
| ``csy_metorite`` | Metorite holds the answer | Reads Metorite, leaves the server out |
| ``csy_miss`` | Metorite finds nothing, the server covers it | Tries Metorite, then the server |
| ``csy_mail`` | A mail question | The email pack wins, and the server stays out (OA-7b) |

Each turn has fewer than 12 words, so no turn-kind request goes out. A test holds the words
of each chat against the words of every pack with ``tool_packs.matched_layer``. The chats sit
outside ``WORK_SCENARIOS`` on purpose: the delegation arm never runs them (``run.arms_for``).
"""

from __future__ import annotations

from evals.one_assistant_measure import mcp_fixture as mx
from evals.one_assistant_measure.scenarios import PRIYA, Check, Plan, Scenario, Turn, _link
from evals.one_assistant_measure.scenarios_work import page_for

ONLY_ASK = "When was circuit C-17 last serviced?"
METORITE_ASK = "What is on my plate?"
MISS_ASK = "Where is the circuit audit task?"
MAIL_ASK = "What does Priya want about the board order?"

#: The surface of each chat, and the words that must load NO pack by themselves except
#: where the chat names the pack. A test reads this table.
SERVER_ANSWER = (f"Circuit C-17 was last serviced on 2026-03-02 by Vendor Nova, "
                 f"as the {mx.SERVER} server says.")
_NO_MAIL = Check("no_mail_pack_with_mcp")


def _mcp(sid: str, title: str, turns: tuple[Turn, ...], **kw: object) -> Scenario:
    kw.setdefault("surface", "")
    surface = str(kw["surface"])
    if surface != "email":
        kw["page"] = page_for(surface)
    return Scenario(sid, title, turns, set="mcp", **kw)  # type: ignore[arg-type]


MCP_SCENARIOS: tuple[Scenario, ...] = (
    _mcp(
        "csy_only_server",
        "A question that only the server answers",
        (
            Turn(
                ONLY_ASK,
                (
                    Check("connected_block"),
                    Check("mcp_offered"),
                    Check("mcp_called"),
                    Check("answer_all", (r"2026-03-02", mx.SERVER), "gives_the_fact_and_the_server"),
                    Check("no_unasked_write", 0),
                    _NO_MAIL,
                ),
                Plan(((mx.FULL, {"query": "circuit C-17 last service date"}),), SERVER_ANSWER),
            ),
        ),
    ),
    _mcp(
        "csy_metorite",
        "A Metorite question: the server stays out",
        (
            Turn(
                METORITE_ASK,
                (
                    Check("ran_tool", ("my_work",), "read_my_work"),
                    Check("mcp_offered"),
                    Check("mcp_not_called"),
                    Check("answer_all", (r"launch brief", r"Vendor call"), "names_my_tasks"),
                    Check("no_unasked_write", 0),
                    _NO_MAIL,
                ),
                Plan(
                    (("my_work", {}),),
                    "Three tasks are yours: Draft the launch brief, Review landing page copy "
                    "and Vendor call prep.",
                ),
            ),
        ),
        surface="tasks",
    ),
    _mcp(
        "csy_miss",
        "A miss in Metorite that the server covers",
        (
            Turn(
                MISS_ASK,
                (
                    Check("ran_tool", ("find_tasks",), "tried_metorite"),
                    Check("connected_block"),
                    Check("mcp_offered"),
                    Check("server_after_metorite"),
                    Check("mcp_called"),
                    Check("answer_all", (r"2026-03-02",), "gives_the_servers_fact"),
                    # The chat must not stop at "not found" while a server covers the topic.
                    Check("answer_none", (r"not found", r"no such"), "no_dead_end"),
                    Check("no_unasked_write", 0),
                    _NO_MAIL,
                ),
                Plan(
                    (
                        ("find_tasks", {"query": "circuit audit"}),
                        (mx.FULL, {"query": "circuit audit C-17"}),
                    ),
                    "Metorite holds no task for it. The opsapp server says circuit C-17 was "
                    "last serviced on 2026-03-02.",
                ),
            ),
        ),
        surface="tasks",
    ),
    _mcp(
        "csy_mail",
        "A mail question: the email pack wins, and the server stays out",
        (
            Turn(
                MAIL_ASK,
                (
                    Check("called", ("read_email",), "read_the_mail"),
                    Check("answer_all", (r"\b250\b",), "the_quantity"),
                    Check("mail_without_server"),
                    Check("mcp_not_called"),
                    _NO_MAIL,
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
        ),
        surface="email",
    ),
)

__all__ = ["MCP_SCENARIOS"]
