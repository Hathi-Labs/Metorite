"""A Bcc of the reply card over WhatsApp (§21.15 review P2).

The WhatsApp summary of a reply card numbered To and Cc only, so a Bcc was
dropped in silence, and the built answer named no ``bcc``. Now each Bcc is a
numbered line marked hidden, the built answer carries the kept Bcc, and a Bcc
that the mail server refused is named in the reply.
"""
from __future__ import annotations

from typing import Any

import pytest
from acb_skills import reply_card as rcard
from acb_skills import whatsapp_acts as wacts

from tests.unit.test_wac_email_confirm import (  # noqa: F401 - gw is a fixture
    BOX,
    MAIL,
    MEMBER,
    ORG,
    _approve,
    _park,
    agents,
    gw,
)

HIDDEN = "veena@acme.test"


@pytest.fixture()
def bcc_gw(gw, monkeypatch):  # noqa: F811
    base = gw.post

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/email/reply-card":
            out = await base(path, body)
            for choice in ("reply", "reply_all"):
                out["recipients"][choice]["bcc"] = [HIDDEN]
            return out
        if path == "/email/reply-card/act":
            gw.posts.append((path, body))
            ok = await rcard.take_reply_answer(ORG, body["card_request_id"], MEMBER,
                                               body["answer"], body["card_thread_id"],
                                               wait_s=0)
            assert ok
            choice = rcard.parse_reply_answer(body["answer"])
            gw.sent.append({"to": list(choice.to), "cc": list(choice.cc),
                            "bcc": list(choice.bcc)})
            return {"ok": True, "action": "send", "to": list(choice.to), "cc": list(choice.cc),
                    "mailbox": f"Work · {MEMBER}", "refused": list(choice.bcc)}
        return await base(path, body)

    monkeypatch.setattr(agents, "_post", post)
    return gw


async def test_the_summary_numbers_the_bcc_and_marks_it_hidden(bcc_gw) -> None:
    _out, act = await _park(agents.compose_reply, {"email_id": MAIL, "account_id": BOX})
    people = wacts.card_people(act.rows, act.reply)
    bcc = [p for p in people if p.kind == "bcc"]
    assert [p.key for p in bcc] == [HIDDEN]
    assert bcc[0].label == f"Bcc (hidden from the others): {HIDDEN}"
    assert f"Bcc (hidden from the others): {HIDDEN}" in wacts.code_summary(act, "7K3Q")


async def test_the_answer_carries_the_kept_bcc_and_a_refused_one_is_said(bcc_gw) -> None:
    _out, act = await _park(agents.compose_reply, {"email_id": MAIL, "account_id": BOX})
    out, yes, _run = await _approve(act)
    assert yes.used
    assert bcc_gw.sent[-1]["bcc"] == [HIDDEN]
    assert f"refused {HIDDEN}" in out


async def test_without_takes_the_bcc_off(bcc_gw) -> None:
    _out, act = await _park(agents.compose_reply, {"email_id": MAIL, "account_id": BOX})
    number = next(p.number for p in wacts.card_people(act.rows, act.reply)
                  if p.kind == "bcc")
    await _approve(act, without=frozenset({number}))
    assert bcc_gw.sent[-1]["bcc"] == []
