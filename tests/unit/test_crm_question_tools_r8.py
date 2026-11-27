"""R8 (task 3955): the batch card of ``crm_accept_best_guesses`` names what the
plan gets, on the real ``/crm`` router and a real database.

The review found it (P0). ``GET /crm/questions`` steers the best guess by the
business rules (``questions.Steering.apply``). ``POST /crm/questions/accept-best``
applies the guess before the steer (``questions._best_guesses``). With the org
rule "net of GST", the card named one field and the route wrote another. The
tool now builds its card from the route's own dry run.

The case is DEWiN's Collection of ``test_crm_answer_first_r8.py``: the AI
ranked Rent Net Amount (``Billing_Amount``) first for the amount, and the rule
steers the list to the fields before GST.

Mutation: build the card from the list again -> the card names a field that
the plan does not get -> red.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from tests.unit._crm_agent_fakes import approve, load_agent_module
from tests.unit.test_crm_answer_first_r8 import (  # noqa: F401
    NET_RULE,
    QID,
    _through_the_crm,
    add_rule,
    af,
    seed_dewin,
)
from tests.unit.test_crm_questions_r8 import _user, q_env  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_M = load_agent_module("crm_assistant_question_tools_r8")


def _latest_amount(p: Any, org: str) -> Any:
    """The amount of Collection in the newest version of the plan."""
    with p.admin_engine.begin() as c:
        row = c.execute(text(
            "SELECT rules FROM crm_mapping_plans WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY version DESC LIMIT 1"), {"o": org}).first()
    rules = row[0] if not isinstance(row[0], str) else json.loads(row[0])
    item = next(i for i in rules if i.get("kind") == "catalogue"
                and i.get("module") == "Payment_Received")
    return item.get("amount")


async def test_the_batch_card_names_what_the_plan_gets_under_a_net_of_gst_rule(
    af, app_engine, monkeypatch,  # noqa: F811
) -> None:
    p = af
    seed_dewin(p, p.org_a)
    admin = _user(p.org_a)
    assert (await add_rule(p, admin, NET_RULE, "org")).status_code == 200
    cards = approve(monkeypatch)

    async def run() -> tuple[str, str]:
        monkeypatch.setattr(_M, "_current_user_email", lambda: admin.email)
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "x")
        listed = await _M.crm_list_questions()
        return listed, await _M.crm_accept_best_guesses([QID])

    listed, out = await _through_the_crm(p, admin, monkeypatch, run)
    assert QID in listed, listed
    # The steered list shows another best guess: the two disagree, so this
    # case reads the bug, not a quiet agreement.
    assert "choice: «IP Basic Amount» (key «IP») · the best guess" in listed, listed
    assert len(cards) == 1, out
    context = cards[0]["context"]
    # The route applies the guess of the AI before the steer: Rent Net Amount.
    assert _latest_amount(p, p.org_a) == "Billing_Amount", out
    assert "now use «Rent Net Amount»." in context, context
    assert "IP Basic Amount" not in context, context
    assert "Accepted 1 best guesses" in out, out
