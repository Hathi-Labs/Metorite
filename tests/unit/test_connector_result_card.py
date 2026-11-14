"""WS-54 IN-9: the model of the result card after a connect (task 3172).

Code: ``packages/acb_common/acb_common/connector_result_card.py``. No database.
The card reads its rows from the connector list (IN-11), so the first test
walks the whole list and holds the one rule: the build offer is the opposite
of ``built_in_app_shows_data``.
"""

from __future__ import annotations

from pathlib import Path

from acb_common import connector_list as cl
from acb_common.connector_result_card import (
    MAX_APPS,
    MAX_NAME,
    MAX_TEMPLATES,
    chat_line,
    result_card,
)

_SRC = (
    Path(__file__).resolve().parents[2] / "packages/acb_common/acb_common/connector_result_card.py"
).read_text(encoding="utf-8")


def test_each_row_of_the_list_has_a_card_and_the_offer_follows_the_row() -> None:
    for row in cl.connector_rows():
        card = result_card(row["id"])
        assert card is not None and card["label"] == row["label"]
        assert card["build_offer"] is (not row["built_in_app_shows_data"]), row["id"]
        assert card["works_now"]["unlocks"] == row["unlocks"]


def test_a_mailbox_connect_shows_no_build_offer() -> None:
    card = result_card("mailbox", apps=["Email"])
    assert card is not None and card["build_offer"] is False
    assert "build an app" not in chat_line(card)


def test_an_mcp_server_that_no_app_uses_shows_the_offer() -> None:
    card = result_card("mcp:tally")
    assert card is not None and card["build_offer"] is True and card["label"] == "tally"
    assert "build an app on tally" in chat_line(card)
    used = result_card("mcp:tally", apps=["Stock sheet"])
    assert used is not None and used["build_offer"] is False


def test_an_unknown_provider_has_no_card() -> None:
    for pid in ("nope", "", "mcp:", "mcp:UPPER", "mcp:a b", None, 5):
        assert result_card(pid) is None  # type: ignore[arg-type]


def test_the_lists_are_trimmed_capped_and_text_only() -> None:
    card = result_card(
        "github",
        apps=[" A ", "A", "", 7, *[f"app{i}" for i in range(20)]],  # type: ignore[list-item]
        templates=["t" * 500, *[f"t{i}" for i in range(9)]],
    )
    assert card is not None
    assert card["works_now"]["apps"][0] == "A" and len(card["works_now"]["apps"]) == MAX_APPS
    assert len(card["templates"]) == MAX_TEMPLATES and len(card["templates"][0]) == MAX_NAME


def test_chat_line_gives_one_next_step_and_a_template_beats_a_build() -> None:
    both = result_card("github", templates=["Pull request digest"])
    assert both is not None and both["build_offer"] is True
    line = chat_line(both)
    assert line.count("Next step") == 1 and "template Pull request digest" in line
    assert "build an app" not in line
    only = result_card("github")
    assert only is not None and "Next step: build an app on GitHub" in chat_line(only)
    none = result_card("mailbox")
    assert none is not None and "Next step" not in chat_line(none)


def test_the_module_is_pure() -> None:
    body = _SRC.split('"""', 2)[2]
    for banned in ("import os", "requests", "httpx", "open(", "execute(", "secret", "token"):
        assert banned not in body, banned
