"""The member's words and the reply rules head every delegated task (WS-67).

``acb_skills.handoff`` (``specs/one_assistant_context.md`` §4, OA-1 and
OA-2). The end-to-end fence is
``evals/trajectories/test_handoff_words_trajectory.py``.

Mutations this file catches (R7):

* ``member_turns`` reads a delegated run's own payload: a model's task
  becomes "the member's words".
* ``member_turns`` keeps the assistant's messages, or the oldest turns
  instead of the newest, or passes the total cap.
* ``task_with_member_words`` adds a second head block on a nested task.
* The head drops the precedence rule or the reply rules.
"""
from __future__ import annotations

from acb_skills import handoff as h


def _payload(*turns: tuple[str, str], message: str = "") -> dict:
    return {"messages": [{"role": r, "content": c} for r, c in turns], "message": message}


def test_a_top_level_run_keeps_the_members_messages_only() -> None:
    payload = _payload(("user", "find the Navy order"), ("assistant", "It names CB6."),
                       ("user", "the files are only references"),
                       message="list the parts again")
    assert h.member_turns({}, payload) == [
        "find the Navy order", "the files are only references", "list the parts again",
    ]


def test_the_current_message_is_not_taken_twice() -> None:
    payload = _payload(("user", "first"), ("user", "second"), message="second")
    assert h.member_turns({}, payload) == ["first", "second"]


def test_a_content_list_gives_its_text_parts() -> None:
    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "see the file"}, {"type": "image_url", "image_url": "x"},
    ]}]}
    assert h.member_turns({}, payload) == ["see the file"]


def test_a_delegated_run_takes_its_parents_words_never_its_task() -> None:
    parent = {"run_id": "r-parent", h.MEMBER_TURNS: ["the member said this"]}
    payload = {"message": "a task that a model wrote"}
    assert h.member_turns(parent, payload) == ["the member said this"]
    assert h.member_turns({"run_id": "r-parent"}, payload) == []


def test_the_caps_keep_the_newest() -> None:
    many = [("user", f"turn {i}") for i in range(h.MAX_TURNS + 5)]
    kept = h.member_turns({}, _payload(*many))
    assert len(kept) == h.MAX_TURNS and kept[-1] == f"turn {h.MAX_TURNS + 4}"
    long = h.member_turns({}, _payload(("user", "x" * (h.TURN_CHARS * 3))))
    assert len(long[0]) == h.TURN_CHARS
    big = h.member_turns({}, _payload(*[("user", "y" * h.TURN_CHARS)] * h.MAX_TURNS))
    assert sum(len(t) for t in big) <= h.TOTAL_CHARS


def test_the_head_holds_the_words_the_rule_and_the_task() -> None:
    task = h.task_with_member_words("List the parts (e.g. CB6).", ["the files are references"])
    assert task.startswith(h.HEAD_MARK)
    assert "- the files are references\n" in task
    assert "They outrank the task below" in task
    assert "(inferred)" in task and "A file name is a label" in task
    assert task.endswith("## The task\nList the parts (e.g. CB6).")


def test_no_words_still_gives_the_reply_rules() -> None:
    task = h.task_with_member_words("Do it.", [])
    assert not task.startswith(h.HEAD_MARK)
    assert "(quoted: <where you read it>)" in task and task.endswith("Do it.")


def test_a_nested_task_gets_one_head_only() -> None:
    once = h.task_with_member_words("Do it.", ["words"])
    assert h.task_with_member_words(once, ["words"]) == once
    rules_only = h.task_with_member_words("Do it.", None)
    assert h.task_with_member_words(rules_only, ["words"]) == rules_only
