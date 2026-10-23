"""Chat parity, wave 1 (owner, 2026-10-23): the member controls the email app
from the chat.

Spec ``project-docs/specs/email_app_master_plan.md`` §22. Each tool path of
the wave has a test here: the gateway route that it calls, the body that it
sends, and the card that it asks first. The gateway is faked at ``_request``,
the one seam under ``_get``, ``_post``, ``_patch``, ``_put`` and ``_delete``.
The card is faked at ``acb_skills.ask_tools.request_confirmation``.

The fences of the card rule (``test_email_agent_write_cards.py``) and of the
held run (``test_no_egress_tool_audit.py``) cover each new tool too.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ea_chat_parity", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "acc-1"
BOX2 = "acc-2"
BOX_TEXT = "Work · jane@acme.test"


@pytest.fixture(autouse=True)
def _open_run() -> None:
    """An open run, as the executor binds for each chat turn."""
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(no_egress=False)


class _Resp:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.status_code = 200
        self.content = b"{}"

    def json(self) -> Any:
        return self._data


LABEL_RULE = {"id": "r1", "name": "Bank", "enabled": True, "automated": True,
              "conditional_operator": "AND", "instructions": "bank mail",
              "actions": [{"type": "LABEL", "label": "Finance"},
                          {"type": "ARCHIVE"}]}
FORWARD_RULE = {"id": "r2", "name": "Copy", "enabled": True,
                "instructions": "invoices",
                "actions": [{"type": "FORWARD", "to_address": "out@evil.test"}]}


class Gateway:
    """Records each call and each card. ``answer`` is the reply of the card."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, dict[str, Any]]] = []
        self.cards: list[dict[str, Any]] = []
        self.answer = True
        self.rules: dict[str, list[dict[str, Any]]] = {
            BOX: [dict(LABEL_RULE, actions=[dict(a) for a in LABEL_RULE["actions"]])],
            BOX2: [],
        }
        self.answers: dict[tuple[str, str], Any] = {}

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [(m, p, b) for m, p, b, _ in self.calls if m != "GET"]

    def answer_of(self, method: str, path: str, body: Any, params: dict[str, Any]) -> Any:
        if (method, path) in self.answers:
            got = self.answers[(method, path)]
            return got(body, params) if callable(got) else got
        if method == "GET" and path == "/email/accounts":
            return [
                {"id": BOX, "email_address": "jane@acme.test", "display_label": "Work",
                 "provider": "gmail"},
                {"id": BOX2, "email_address": "jane@home.test", "display_label": "Home",
                 "provider": "microsoft"},
            ]
        if method == "GET" and path == "/email/rules":
            box = params.get("account_id")
            return {"rules": [dict(r, actions=[dict(a) for a in r["actions"]])
                              for r in self.rules.get(box, [])]}
        return {"ok": True, "id": "new-1"}


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    g = Gateway()

    async def fake_request(method: str, path: str, **kwargs: Any) -> _Resp:
        body = kwargs.get("json")
        params = dict(kwargs.get("params") or {})
        g.calls.append((method, path, body, params))
        return _Resp(g.answer_of(method, path, body, params))

    async def card(**kw: Any) -> Any:
        g.cards.append(kw)
        return g.answer

    monkeypatch.setattr(agents, "_request", fake_request)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    return g


def _held(monkeypatch: pytest.MonkeyPatch) -> None:
    """A held run: the chat read mail from outside senders (H-236)."""
    monkeypatch.setattr(agents, "_egress_held", lambda: True)


@pytest.mark.parametrize(("tool", "kwargs"), [
    ("copy_rules", {"from_account_id": BOX, "to_account_id": BOX2}),
    ("rule_guidance", {"account_id": BOX, "action": "add", "guidance": "all is invoices"}),
    ("run_rules", {"account_id": BOX, "scope": "message", "email_id": "m1",
                   "dry_run": False}),
    ("update_rule", {"account_id": BOX, "rule_id": "r2", "body_pattern": "invoice"}),
])
async def test_the_real_hold_reader_refuses_each_widening_path(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    """The run context of a held chat, bound as the executor binds it, and
    the ONE reader (``acb_skills.egress.no_egress_for_this_run``). No fake of
    ``_egress_held``. A rule that forwards is on, so each path refuses with no
    card and no write."""
    from acb_skills.write_artifact import bind_artifact_context

    gw.rules[BOX].append(dict(FORWARD_RULE))
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1")
    try:
        out = await getattr(agents, tool)(**kwargs)
    finally:
        bind_artifact_context(no_egress=False)
    assert out.startswith("Nothing changed."), out
    assert gw.cards == [] and gw.writes == []


# ── 1. update_rule: the full edit ────────────────────────────────────────────


async def test_update_rule_renames_and_sets_each_condition(gw: Gateway) -> None:
    out = await agents.update_rule(
        BOX, "r1", name="Bank and cards", to_pattern="billing@", body_pattern="IBAN",
        conditional_operator="or", automated=False, run_on_threads=True)
    [card] = gw.cards
    assert card["title"] == 'Change the rule "Bank and cards"?'
    [(method, path, body)] = gw.writes
    assert (method, path) == ("PATCH", "/email/rules/r1")
    assert body["name"] == "Bank and cards"
    assert body["to_pattern"] == "billing@" and body["body_pattern"] == "IBAN"
    assert body["conditional_operator"] == "OR"
    assert body["automated"] is False and body["run_on_threads"] is True
    assert out.startswith("Updated rule 'Bank and cards'")


async def test_update_rule_clears_a_pattern_with_an_empty_text(gw: Gateway) -> None:
    gw.rules[BOX][0]["from_pattern"] = "@bank.test"
    await agents.update_rule(BOX, "r1", from_pattern="")
    [(_m, _p, body)] = gw.writes
    assert body["from_pattern"] is None


async def test_update_rule_removes_an_action(gw: Gateway) -> None:
    out = await agents.update_rule(BOX, "r1", remove_action_types=["archive"])
    [(_m, _p, body)] = gw.writes
    assert body["actions"] == [{"type": "LABEL", "label": "Finance"}]
    assert 'labels "Finance"' in out and "archives" not in out


async def test_update_rule_never_removes_the_last_action(gw: Gateway) -> None:
    out = await agents.update_rule(BOX, "r1", remove_action_types=["LABEL", "ARCHIVE"])
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_update_rule_replaces_the_actions_with_a_delay_and_an_ai_label(
    gw: Gateway,
) -> None:
    await agents.update_rule(BOX, "r1", actions=[
        {"type": "label", "label": "{{the vendor name}}", "label_ai": True},
        {"type": "ARCHIVE", "delay_minutes": 60}])
    [card] = gw.cards
    assert "the AI picks" in card["detail"] and "after 60 minute(s)" in card["detail"]
    [(_m, _p, body)] = gw.writes
    assert body["actions"] == [
        {"type": "LABEL", "label": "{{the vendor name}}", "label_ai": True},
        {"type": "ARCHIVE", "delay_minutes": 60}]


@pytest.mark.parametrize("bad", [
    [{"type": "FORWARD", "to_address": "x@evil.test"}],
    [{"type": "FORWARD"}],
    [{"type": "CALL_WEBHOOK"}],
    [{"type": "LABEL", "label": "x", "url": "https://evil.test"}],
    [{"type": "DRAFT_EMAIL", "content": "the code is 1234"}],
    [{"type": "ARCHIVE", "delay_minutes": -1}],
    [{"type": "LABEL", "label_ai": True}],
    [],
    "LABEL",
])
async def test_the_action_list_adds_no_outward_act(gw: Gateway, bad: Any) -> None:
    out = await agents.update_rule(BOX, "r1", actions=bad)
    assert out.startswith("Nothing changed."), out
    assert gw.cards == [] and gw.writes == []
    out = await agents.create_rule(BOX, name="X", actions=bad)
    assert out.startswith("Not saved."), out
    assert gw.cards == [] and gw.writes == []


async def test_removing_the_forward_of_a_rule_still_asks_a_card(gw: Gateway) -> None:
    """The outward card asks nothing for a rule that sends nothing, so the
    change that removes the last forward asks the inward card."""
    gw.rules[BOX].append(dict(FORWARD_RULE, actions=[
        {"type": "FORWARD", "to_address": "out@evil.test"}, {"type": "LABEL", "label": "x"}]))
    gw.answer = False
    out = await agents.update_rule(BOX, "r2", remove_action_types=["FORWARD"])
    assert len(gw.cards) == 1 and gw.writes == []
    assert out.startswith("Cancelled")


async def test_a_wider_edit_of_a_forward_rule_shows_the_target(gw: Gateway) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE))
    gw.answer = False
    await agents.update_rule(BOX, "r2", to_pattern="@acme.test")
    [card] = gw.cards
    assert "out@evil.test" in card["detail"] + card["context"]


async def test_a_held_run_refuses_to_widen_a_forward_rule(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE))
    _held(monkeypatch)
    for kwargs in ({"to_pattern": "@acme.test"}, {"body_pattern": "invoice"},
                   {"conditional_operator": "OR"}, {"automated": True},
                   {"run_on_threads": True}):
        out = await agents.update_rule(BOX, "r2", **kwargs)
        assert out.startswith("Nothing changed."), (kwargs, out)
    assert gw.cards == [] and gw.writes == []


async def test_a_held_run_keeps_an_inward_edit(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held(monkeypatch)
    out = await agents.update_rule(BOX, "r1", name="Bank", to_pattern="@bank.test",
                                   actions=[{"type": "LABEL", "label": "Finance",
                                             "delay_minutes": 5}])
    assert out.startswith("Updated rule"), out
    assert len(gw.cards) == 1 and len(gw.writes) == 1


# ── 2. create_rule: the action list ──────────────────────────────────────────


async def test_create_rule_takes_three_actions_with_a_delay(gw: Gateway) -> None:
    out = await agents.create_rule(BOX, name="Receipts", instructions="receipts", actions=[
        {"type": "LABEL", "label": "Receipt"}, {"type": "MARK_READ"},
        {"type": "ARCHIVE", "delay_minutes": 1440}])
    [card] = gw.cards
    assert card["title"] == ('Save the rule "Receipts" that labels "Receipt" and marks '
                             'read and archives after 1440 minute(s)?')
    [(method, path, body)] = gw.writes
    assert (method, path) == ("POST", "/email/rules")
    assert [a["type"] for a in body["actions"]] == ["LABEL", "MARK_READ", "ARCHIVE"]
    assert body["actions"][2]["delay_minutes"] == 1440
    assert out.startswith("Created rule 'Receipts'")


async def test_create_rule_refuses_the_list_beside_a_forward(gw: Gateway) -> None:
    out = await agents.create_rule(BOX, name="X", forward_to="a@b.test",
                                   actions=[{"type": "ARCHIVE"}])
    assert out.startswith("Not saved.")
    assert gw.cards == [] and gw.writes == []


# ── 3. Block a sender, and several senders in one call ───────────────────────


async def test_block_is_a_status_of_set_sender_status(gw: Gateway) -> None:
    gw.answers[("POST", "/email/newsletters")] = {"ok": True, "archived": 4}
    out = await agents.set_sender_status(BOX, "news@list.test", "block")
    [card] = gw.cards
    assert card["title"] == f"Block news@list.test in {BOX_TEXT}?"
    assert "Nothing is sent to the sender" in card["detail"]
    assert gw.writes == [("POST", "/email/newsletters", {
        "account_id": BOX, "email": "news@list.test", "status": "AUTO_ARCHIVED"})]
    assert "4 email(s) were archived" in out


async def test_several_senders_ask_one_card_that_lists_each(gw: Gateway) -> None:
    gw.answer = False
    out = await agents.set_sender_status(
        BOX, status="block", emails=["a@x.test", "b@y.test", "A@x.test"])
    [card] = gw.cards
    assert card["title"] == f"Block 2 senders in {BOX_TEXT}?"
    assert card["context"].splitlines() == ["Each sender:", "- a@x.test", "- b@y.test"]
    assert gw.writes == [] and out.startswith("Cancelled")
    gw.answer = True
    await agents.set_sender_status(BOX, status="cold", emails=["a@x.test", "b@y.test"])
    assert [b["from_email"] for _m, p, b in gw.writes if p == "/email/cold-senders"] == [
        "a@x.test", "b@y.test"]


async def test_a_sender_status_with_no_sender_changes_nothing(gw: Gateway) -> None:
    out = await agents.set_sender_status(BOX, status="block")
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_bulk_over_several_senders_counts_each_and_asks_once(gw: Gateway) -> None:
    counts = {"a@x.test": 3, "b@y.test": 0, "c@z.test": 5}

    def bulk(body: Any, _params: Any) -> Any:
        if body.get("dry_run"):
            return {"matched": counts[body["sender_email"]]}
        return {"affected": counts[body["sender_email"]]}

    gw.answers[("POST", "/email/messages/bulk")] = bulk
    out = await agents.bulk_manage_inbox(
        "archive", sender_emails=["a@x.test", "b@y.test", "c@z.test"])
    [card] = gw.cards
    assert card["title"] == "Archive about 8 emails from 2 senders?"
    assert card["context"].splitlines() == [
        "Each sender, with its count:", "- a@x.test: about 3", "- c@z.test: about 5"]
    acts = [b for _m, _p, b in gw.writes if not b.get("dry_run")]
    assert [b["sender_email"] for b in acts] == ["a@x.test", "c@z.test"]
    assert out == "Archive: 8 emails changed, from 2 senders."


async def test_bulk_over_several_senders_writes_nothing_on_a_no_first(gw: Gateway) -> None:
    gw.answers[("POST", "/email/messages/bulk")] = {"matched": 0}
    out = await agents.bulk_manage_inbox("read", sender_emails=["a@x.test", "b@y.test"])
    assert out.startswith("Nothing matches") and gw.cards == []


async def test_bulk_over_several_senders_writes_nothing_on_a_no(gw: Gateway) -> None:
    gw.answers[("POST", "/email/messages/bulk")] = {"matched": 2}
    gw.answer = False
    await agents.bulk_manage_inbox("trash", sender_emails=["a@x.test", "b@y.test"])
    assert len(gw.cards) == 1
    assert all(b.get("dry_run") for _m, _p, b in gw.writes)


# ── 4. Rule runs: one email, the estimate, the status, the retry, the test ───


async def test_a_preview_on_one_email_asks_nothing(gw: Gateway) -> None:
    gw.answers[("POST", "/email/rules/run-message")] = {
        "matched": True, "rule": {"id": "r1", "name": "Bank"}, "reason": "bank",
        "actions": [{"type": "LABEL", "label": "Finance"}]}
    out = await agents.run_rules(BOX, scope="message", email_id="m1")
    assert gw.cards == []
    [(_m, path, body)] = gw.writes
    assert path == "/email/rules/run-message"
    assert body == {"account_id": BOX, "message_id": "m1", "is_test": True}
    assert "Bank" in out and 'labels "Finance"' in out and "Nothing changed" in out


async def test_applying_the_rules_to_one_email_asks_first(gw: Gateway) -> None:
    gw.answers[("POST", "/email/messages/summaries")] = {
        "summaries": [{"id": "m1", "from": "Kim", "subject": "Invoice 7"}]}
    gw.answers[("POST", "/email/rules/run-message")] = {
        "matched": True, "rule": {"id": "r1", "name": "Bank"}}
    gw.answer = False
    out = await agents.run_rules(BOX, scope="message", email_id="m1", dry_run=False)
    [card] = gw.cards
    assert card["title"] == f'Apply the rules to "Invoice 7" from Kim in {BOX_TEXT}?'
    assert '- rule "Bank"' in card["context"]
    assert [p for _m, p, _b in gw.writes] == ["/email/messages/summaries"]
    assert out.startswith("Cancelled")
    gw.answer = True
    out = await agents.run_rules(BOX, scope="message", email_id="m1", dry_run=False)
    assert gw.writes[-1] == ("POST", "/email/rules/run-message",
                             {"account_id": BOX, "message_id": "m1", "is_test": False})
    assert out == f"Applied the rule 'Bank' to the email in {BOX_TEXT}."


async def test_a_held_run_applies_no_rule_on_one_email_while_one_forwards(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE))
    _held(monkeypatch)
    out = await agents.run_rules(BOX, scope="message", email_id="m1", dry_run=False)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_the_estimate_and_the_status_are_reads(gw: Gateway) -> None:
    gw.answers[("GET", "/email/rules/process-past/estimate")] = {
        "in_range": 900, "eligible": 700, "already_processed": 200,
        "will_process": 700, "capped": False, "limit": 1000}
    gw.answers[("GET", "/email/rules/process-past/status")] = {
        "status": "running", "phase": "classifying", "processed": 40, "total": 700}
    out = await agents.run_rules(BOX, scope="estimate", days=30, include_read=False)
    assert "would process 700 email(s)" in out
    [(_m, _p, _b, params)] = [c for c in gw.calls if c[1].endswith("/estimate")]
    assert params["include_read"] == "false" and params["start_date"]
    out = await agents.run_rules(BOX, scope="status")
    assert "processed: 40" in out and "total: 700" in out
    assert gw.cards == [] and gw.writes == []


async def test_retry_failed_asks_and_says_what_it_skips(gw: Gateway) -> None:
    gw.answers[("POST", "/email/rules/history/retry-failed")] = {
        "considered": 5, "repaired": 4, "still_failing": 1,
        "skipped_actions": ["DRAFT_EMAIL"]}
    out = await agents.run_rules(BOX, scope="retry_failed")
    [card] = gw.cards
    assert "never drafts, sends, forwards" in card["detail"]
    assert gw.writes == [("POST", "/email/rules/history/retry-failed",
                          {"account_id": BOX, "limit": 200})]
    assert "4 repaired, 1 still failing" in out and "DRAFT_EMAIL" in out


async def test_an_unknown_scope_runs_nothing(gw: Gateway) -> None:
    out = await agents.run_rules(BOX, scope="everything", dry_run=False)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_rules_on_recent_mail_is_a_framed_dry_run(gw: Gateway) -> None:
    gw.answers[("POST", "/email/rules/test/recent")] = {"results": [
        {"email_id": "e1", "subject": "Invoice", "from": "Kim", "matched": True,
         "rule": {"id": "r1", "name": "Bank"}, "actions": ["LABEL"]},
        {"email_id": "e2", "subject": "Hello", "from": "Lee", "matched": False}]}
    out = await agents.test_rule_match(BOX, recent=True, limit=50)
    assert gw.writes == [("POST", "/email/rules/test/recent",
                          {"account_id": BOX, "limit": 15})]
    assert "1 match a rule" in out and "Bank (LABEL)" in out and "no rule" in out
    assert "EMAIL" in out  # the untrusted frame of a sender's text
    assert gw.cards == []


# ── 5. Copy the rules of one mailbox to another ──────────────────────────────


async def test_copy_rules_lists_each_rule_then_copies(gw: Gateway) -> None:
    gw.answers[("POST", "/email/rules/copy")] = {
        "copied": ["Bank (copy)"], "renamed": [{"name": "Bank", "copied_as": "Bank (copy)"}],
        "left_out": [{"name": "Loop", "reason": "forward_loop"}],
        "will_create": [{"rule": "Bank (copy)", "action": "LABEL", "name": "Finance"}]}
    out = await agents.copy_rules(BOX, BOX2)
    [card] = gw.cards
    assert card["title"] == f"Copy 1 rule(s) from {BOX_TEXT} to Home · jane@home.test?"
    assert '- rule "Bank": labels "Finance" and archives (when: bank mail)' in card["context"]
    assert gw.writes == [("POST", "/email/rules/copy",
                          {"from_account_id": BOX, "to_account_id": BOX2})]
    assert "'Bank' is called 'Bank (copy)'" in out
    assert "'Loop' was left out (forward_loop)" in out
    assert "label 'Finance' on first use" in out


async def test_a_copy_of_a_forward_rule_shows_its_target(gw: Gateway) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE))
    gw.answer = False
    out = await agents.copy_rules(BOX, BOX2)
    [card] = gw.cards
    assert "out@evil.test" in card["detail"] + card["context"]
    assert gw.writes == [] and out.startswith("Cancelled")


async def test_a_held_run_copies_no_forward_rule(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE))
    _held(monkeypatch)
    out = await agents.copy_rules(BOX, BOX2)
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_a_held_run_copies_an_inward_rule_behind_a_card(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held(monkeypatch)
    await agents.copy_rules(BOX, BOX2)
    assert len(gw.cards) == 1 and len(gw.writes) == 1


async def test_a_copy_stops_when_the_rules_change_under_the_card(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def card(**kw: Any) -> Any:
        gw.cards.append(kw)
        gw.rules[BOX][0]["name"] = "Changed"
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    out = await agents.copy_rules(BOX, BOX2)
    assert out.startswith("Nothing changed.") and gw.writes == []


async def test_a_copy_needs_two_mailboxes(gw: Gateway) -> None:
    assert (await agents.copy_rules(BOX, BOX)).startswith("Nothing changed.")
    assert gw.calls == []


# ── 6. Rule guidance and the policies ────────────────────────────────────────

GUIDANCE = {"guidance": [{"id": "g1", "rule_id": None, "rule_name": None,
                          "guidance": "Mail from my accountant is never Marketing",
                          "source": "USER"}]}


async def test_guidance_list_and_policies_are_reads(gw: Gateway) -> None:
    gw.answers[("GET", "/email/rules/guidance")] = GUIDANCE
    gw.answers[("GET", "/email/rules/policies")] = {
        "cold_email_blocker": "LABEL", "dispositions": {"AUTO_ARCHIVED": 3},
        "filters_active": 2, "provider_rules_supported": True,
        "provider_rules": [{"id": "p1", "name": "Block news", "managed": True}]}
    out = await agents.rule_guidance(BOX)
    assert "never Marketing" in out and "guidance_id g1" in out
    out = await agents.rule_guidance(BOX, action="policies")
    assert "Cold-email blocker: LABEL" in out and "3 auto_archived" in out
    assert "Block news (made by Metorite)" in out
    assert gw.cards == [] and gw.writes == []


async def test_adding_guidance_asks_and_shows_the_text(gw: Gateway) -> None:
    out = await agents.rule_guidance(BOX, action="add", guidance="  Receipts  are\nnot FYI ",
                                     rule_id="r1")
    [card] = gw.cards
    assert card["context"] == "Receipts are not FYI"
    assert 'the rule "Bank"' in card["detail"]
    assert gw.writes == [("POST", "/email/rules/guidance", {
        "account_id": BOX, "guidance": "Receipts are not FYI", "rule_id": "r1"})]
    assert out.startswith("Saved the guidance")


async def test_deleting_guidance_asks_and_deletes_by_id(gw: Gateway) -> None:
    gw.answers[("GET", "/email/rules/guidance")] = GUIDANCE
    await agents.rule_guidance(BOX, action="delete", guidance_id="g1")
    [card] = gw.cards
    assert "never Marketing" in card["context"]
    [(method, path, _b, params)] = [c for c in gw.calls if c[0] == "DELETE"]
    assert (method, path, params) == ("DELETE", "/email/rules/guidance/g1",
                                      {"account_id": BOX})


async def test_a_held_run_teaches_no_guidance_while_a_rule_forwards(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE))
    _held(monkeypatch)
    out = await agents.rule_guidance(BOX, action="add", guidance="all mail is invoices")
    assert out.startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_a_held_run_keeps_guidance_when_no_rule_sends_out(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held(monkeypatch)
    await agents.rule_guidance(BOX, action="add", guidance="Receipts are not FYI")
    assert len(gw.cards) == 1 and len(gw.writes) == 1


# ── 7. The email cleaner ─────────────────────────────────────────────────────


async def test_the_cleaner_status_and_backlog_are_reads(gw: Gateway) -> None:
    gw.answers[("GET", "/email/cleanup/status")] = {
        "status": "running", "phase": "downloading", "synced": 120}
    gw.answers[("GET", "/email/cleanup/uncategorized")] = {
        "uncategorized": 42, "pending_patterns": 2, "pending_pattern_reach": 9,
        "top_senders": [{"email": "news@list.test", "name": "News", "count": 30}]}
    out = await agents.auto_categorize_inbox(BOX, mode="status")
    assert "phase: downloading" in out and "synced: 120" in out
    out = await agents.auto_categorize_inbox(BOX, mode="uncategorized")
    assert out.startswith("42 email(s)") and "reach about 9" in out
    assert "News <news@list.test>: 30" in out
    assert gw.cards == [] and gw.writes == []


async def test_backfill_asks_then_starts_with_the_date(gw: Gateway) -> None:
    gw.answers[("POST", "/email/cleanup/backfill")] = {"scheduled": True}
    out = await agents.auto_categorize_inbox(BOX, mode="backfill", since_date="2026-01-01")
    [card] = gw.cards
    assert "back to 2026-01-01" in card["detail"] and "No AI call" in card["detail"]
    assert gw.writes == [("POST", "/email/cleanup/backfill",
                          {"account_id": BOX, "since_date": "2026-01-01"})]
    assert "started" in out


async def test_backfill_refuses_a_bad_date_before_the_card(gw: Gateway) -> None:
    out = await agents.auto_categorize_inbox(BOX, mode="backfill", since_date="last year")
    assert out.startswith("Nothing changed.") and gw.cards == [] and gw.writes == []


async def test_restore_labels_asks_then_reports_the_counts(gw: Gateway) -> None:
    gw.answers[("POST", "/email/cleanup/restore-labels")] = {"updated": 7, "scanned": 300}
    out = await agents.auto_categorize_inbox(BOX, mode="restore_labels")
    assert len(gw.cards) == 1
    assert gw.writes == [("POST", "/email/cleanup/restore-labels", {"account_id": BOX})]
    assert "updated: 7" in out


# ── 8. The voice profile, and the approval of a knowledge suggestion ─────────


async def test_the_voice_profile_reads_ask_nothing(gw: Gateway) -> None:
    gw.answers[("GET", "/email/voice-profile")] = {
        "status": "READY", "enabled": True, "analyzed_count": 120,
        "style_guide": "Short, warm, no emoji.", "suggested_knowledge": 3}
    gw.answers[("GET", "/email/voice-profile/preview")] = {
        "sent": 400, "drafts": 20, "total": 420, "will_analyze": 150}
    gw.answers[("GET", "/email/voice-profile/status")] = {"status": "idle"}
    out = await agents.voice_profile(BOX)
    assert "READY" in out and "Short, warm" in out and "3 suggested" in out
    out = await agents.voice_profile(BOX, action="preview", start_date="2026-01-01",
                                     sources=["sent", "drafts"])
    assert "study 150 of 420" in out
    [(_m, _p, _b, params)] = [c for c in gw.calls if c[1].endswith("/preview")]
    assert params == {"account_id": BOX, "sources": "sent,drafts",
                      "start_date": "2026-01-01"}
    assert "No voice-profile build" in await agents.voice_profile(BOX, action="status")
    gw.answers[("POST", "/email/voice-profile/sample")] = {
        "sample": "Hi Kim,", "scenario": "pricing"}
    out = await agents.voice_profile(BOX, action="sample", scenario="pricing")
    assert "Hi Kim," in out and "Nothing was saved or sent" in out
    assert gw.cards == []


async def test_a_build_asks_then_starts(gw: Gateway) -> None:
    await agents.voice_profile(BOX, action="build", start_date="2026-01-01",
                               end_date="2026-06-30", extract_knowledge=False)
    [card] = gw.cards
    assert "from 2026-01-01 to 2026-06-30" in card["detail"]
    assert gw.writes == [("POST", "/email/voice-profile/build", {
        "account_id": BOX, "start_date": "2026-01-01", "end_date": "2026-06-30",
        "sources": ["sent"], "extract_knowledge": False})]


async def test_save_shows_the_new_guide_and_refuses_hidden_text(gw: Gateway) -> None:
    out = await agents.voice_profile(BOX, action="save", style_guide="Short‮ text")
    assert out.startswith("Nothing changed.") and gw.cards == []
    await agents.voice_profile(BOX, action="save", style_guide="Short.", enabled=False)
    [card] = gw.cards
    assert "The style guide becomes: Short." in card["context"]
    assert "The profile is off." in card["context"]
    assert gw.writes == [("PUT", "/email/voice-profile",
                          {"account_id": BOX, "style_guide": "Short.", "enabled": False})]


async def test_delete_asks_and_deletes_the_profile(gw: Gateway) -> None:
    gw.answer = False
    out = await agents.voice_profile(BOX, action="delete")
    assert out.startswith("Cancelled") and gw.writes == []
    gw.answer = True
    await agents.voice_profile(BOX, action="delete")
    [(_m, path, _b, params)] = [c for c in gw.calls if c[0] == "DELETE"]
    assert (path, params) == ("/email/voice-profile", {"account_id": BOX})


async def test_approving_a_suggestion_shows_its_text(gw: Gateway) -> None:
    gw.answers[("GET", "/email/knowledge")] = {"entries": [
        {"id": "k9", "title": "Lead time", "content": "Six weeks", "status": "suggested"}]}
    out = await agents.voice_profile(BOX, action="approve_knowledge", knowledge_id="k9")
    [card] = gw.cards
    assert card["title"] == f'Approve the knowledge entry "Lead time" in {BOX_TEXT}?'
    assert card["context"] == "Six weeks"
    assert gw.writes == [("POST", "/email/knowledge/k9/approve", {})]
    assert out.startswith("Approved")
    listed = await agents.list_knowledge(BOX)
    assert "knowledge_id k9, suggested, waits for approval" in listed


# ── 9. manage_inbox: spam, and the follow-up flag ────────────────────────────


async def test_spam_moves_to_the_junk_folder_and_never_to_trash(gw: Gateway) -> None:
    """The approach of branch ``email-chat-polish`` (H-389): folder=junk,
    which the gateway reads as Gmail SPAM and as Outlook Junk Email."""
    gw.answer = False
    out = await agents.manage_inbox("spam", ["m1", "m2"])
    [card] = gw.cards
    assert card["title"].startswith("Move 2 messages") and card["title"].endswith("to Spam?")
    assert out == "Cancelled — nothing was moved to Spam."
    assert [w for w in gw.writes if w[1] != "/email/messages/summaries"] == []
    gw.answer = True
    out = await agents.manage_inbox("junk", ["m1", "m2"])
    assert [w for w in gw.writes if w[0] == "PATCH"] == [
        ("PATCH", "/email/messages/m1", {"folder": "junk"}),
        ("PATCH", "/email/messages/m2", {"folder": "junk"})]
    assert out == "Moved 2 message(s) to Spam."


@pytest.mark.parametrize(("action", "flag"), [("flag", True), ("unflag", False)])
async def test_flag_patches_each_message(gw: Gateway, action: str, flag: bool) -> None:
    out = await agents.manage_inbox(action, ["m1", "m2"])
    [card] = gw.cards
    assert card["title"].startswith(f"{action.capitalize()} 2 messages")
    assert [w for w in gw.writes if w[0] == "PATCH"] == [
        ("PATCH", "/email/messages/m1", {"is_flagged": flag}),
        ("PATCH", "/email/messages/m2", {"is_flagged": flag})]
    assert out.endswith("2 message(s).")


# ── 11. The mailbox: name, colour, All inboxes, default, disconnect ──────────


async def test_update_account_lists_each_change_on_one_card(gw: Gateway) -> None:
    out = await agents.update_account(BOX, name="Sales", color_slot=4,
                                      in_all_inboxes=False, make_default=True)
    [card] = gw.cards
    assert card["title"] == f"Change the mailbox {BOX_TEXT}?"
    assert card["context"].splitlines() == [
        '- Name: "Sales"', "- Colour: slot 4",
        "- Kept separate: All inboxes leaves it out",
        "- The default mailbox: the Email app opens on it"]
    assert gw.writes == [
        ("PATCH", f"/email/accounts/{BOX}",
         {"label": "Sales", "color_slot": 4, "in_all_inboxes": False}),
        ("POST", f"/email/accounts/{BOX}/default", {})]
    assert out.startswith("Changed the mailbox")


@pytest.mark.parametrize("kwargs", [{"color_slot": 13}, {"color_slot": 0}, {}])
async def test_update_account_refuses_a_bad_change(gw: Gateway, kwargs: dict) -> None:
    out = await agents.update_account(BOX, **kwargs)
    assert out.startswith("Nothing changed.") and gw.cards == [] and gw.writes == []


async def test_disconnect_asks_alone_and_deletes(gw: Gateway) -> None:
    out = await agents.update_account(BOX, disconnect=True, name="x")
    assert out.startswith("Nothing changed.") and gw.cards == []
    gw.answer = False
    out = await agents.update_account(BOX, disconnect=True)
    [card] = gw.cards
    assert card["title"] == f"Disconnect {BOX_TEXT}?"
    assert "The mail at the provider stays" in card["detail"]
    assert gw.writes == [] and out.startswith("Cancelled")
    gw.answer = True
    await agents.update_account(BOX, disconnect=True)
    assert gw.writes == [("DELETE", f"/email/accounts/{BOX}", None)]


async def test_an_unknown_mailbox_changes_nothing(gw: Gateway) -> None:
    out = await agents.update_account("nope", name="x")
    assert out.startswith("Nothing changed.") and gw.cards == [] and gw.writes == []


# ── 12. Reads: the contact card, the timeline, the overview; a label colour ──


async def test_the_contact_card_is_a_framed_read(gw: Gateway) -> None:
    gw.answers[("GET", "/email/contacts/card")] = {
        "email": "kim@contoso.test", "name": "Kim Lee", "category": "Conversation",
        "status": "APPROVED",
        "stats": {"received": 12, "sent": 4, "unread": 1, "threads": 5},
        "details": {"title": "Buyer", "organization": "Contoso", "phones": ["+91 98450 00000"]},
        "recent": [{"id": "e1", "account_id": BOX, "subject": "PO 77",
                    "received_at": "2026-10-20T09:00:00"}]}
    out = await agents.find_contact(email="kim@contoso.test", account_id=BOX)
    [(_m, _p, _b, params)] = [c for c in gw.calls if c[1] == "/email/contacts/card"]
    assert params == {"email": "kim@contoso.test", "limit": 5, "account_id": BOX}
    assert "Kim Lee" in out and "12 received" in out and "Company: Contoso" in out
    assert "PO 77" in out and "EMAIL" in out
    assert gw.cards == [] and gw.writes == []


async def test_the_timeline_says_why_an_email_moved(gw: Gateway) -> None:
    gw.answers[("GET", "/email/messages/e1/timeline")] = {
        "subject": "Sale!", "events": [
            {"kind": "received", "at": "2026-10-20T09:00:00", "from": "Shop"},
            {"kind": "rule", "at": "2026-10-20T09:01:00", "rule_name": "Marketing",
             "status": "APPLIED", "automated": True,
             "actions": [{"type": "LABEL"}, {"type": "ARCHIVE"}],
             "reason": "a promotion"}]}
    out = await agents.read_email("e1", timeline=True)
    assert "arrived from Shop" in out
    assert "rule 'Marketing' applied: LABEL, ARCHIVE. Why: a promotion" in out
    assert gw.writes == []


async def test_the_overview_reads_the_fields_that_the_route_sends(gw: Gateway) -> None:
    gw.answers[("GET", "/email/analytics/overview")] = {
        "flow": {"received": 300, "received_prev": 250, "sent": 40, "unread": 12,
                 "auto_handled_rate": 0.5},
        "responsiveness": {"reply_rate": 0.8, "median_hours": 3.5},
        "backlog": {"needs_reply": {"threads": 6}, "awaiting": {"threads": 2}},
        "noisy_senders": [{"email": "n@x.test", "name": "News", "messages": 40}]}
    gw.answers[("GET", "/email/senders/categories")] = {"counts": {"Newsletter": 9}}
    out = await agents.get_account_overview(BOX, days=500)
    [(_m, _p, _b, params)] = [c for c in gw.calls if c[1] == "/email/analytics/overview"]
    assert params["days"] == "365"
    assert "300 received (250 the 365d before)" in out and "50% handled" in out
    assert "6 need a reply" in out and "News (40)" in out and "Newsletter: 9" in out


async def test_a_label_colour_asks_and_patches_the_label(gw: Gateway) -> None:
    gw.answers[("POST", f"/email/accounts/{BOX}/folders")] = {"name": "Finance"}
    out = await agents.create_label(BOX, "Finance", color="preset7")
    [card] = gw.cards
    assert "Its colour becomes preset7" in card["detail"]
    assert gw.writes[-1] == ("PATCH", f"/email/accounts/{BOX}/labels",
                             {"name": "Finance", "color": "preset7"})
    assert out.endswith("with the colour preset7.")
    out = await agents.create_label(BOX, "Finance", color="red")
    assert out.startswith("Nothing changed.") and len(gw.cards) == 1


# ── 13. A new email in Drafts, and the drafter's rewrite ─────────────────────


async def test_a_new_draft_shows_each_recipient_and_the_text_then_saves(
    gw: Gateway,
) -> None:
    gw.answers[("PUT", "/email/drafts")] = {"id": "d9"}
    gw.answer = False
    out = await agents.edit_draft(BOX, to=["kim@contoso.test"], cc=["lee@contoso.test"],
                                  subject="Quote", body="Hi Kim,\n\nThe quote is attached.")
    [card] = gw.cards
    assert card["title"] == "Save this new email in Drafts?"
    assert "- To: kim@contoso.test" in card["context"]
    assert "- Cc: lee@contoso.test" in card["context"]
    assert "The quote is attached." in card["context"]
    assert "Nothing is sent." in card["detail"]
    assert gw.writes == [] and out.startswith("Cancelled")
    gw.answer = True
    out = await agents.edit_draft(BOX, to=["kim@contoso.test"], subject="Quote",
                                  body="Hi Kim,")
    [(method, path, body)] = gw.writes
    assert (method, path) == ("PUT", "/email/drafts")
    assert "draft_id" not in body and "reply_to_message_id" not in body
    assert body["to"] == ["kim@contoso.test"] and body["from_chat"] is True
    assert "draft_id d9" in out and "Nothing was sent" in out


async def test_a_rewrite_asks_the_drafter_and_shows_its_text(gw: Gateway) -> None:
    gw.answers[("POST", "/email/compose-assist")] = {"draft": "Dear Kim,\n\nShort text."}
    await agents.edit_draft(BOX, to=["kim@contoso.test"], subject="Quote",
                            body="hi kim here is the quote thx", rewrite="more formal")
    [card] = gw.cards
    assert "Short text." in card["context"]
    assist = [b for _m, p, b in gw.writes if p == "/email/compose-assist"]
    assert assist == [{"account_id": BOX, "body": "hi kim here is the quote thx",
                       "instruction": "more formal", "mode": "new", "subject": "Quote",
                       "to": ["kim@contoso.test"]}]
    [(_m, _p, saved)] = [w for w in gw.writes if w[1] == "/email/drafts"]
    assert saved["body"] == "Dear Kim,\n\nShort text."


async def test_a_new_draft_needs_a_recipient_and_a_text(gw: Gateway) -> None:
    assert (await agents.edit_draft(BOX, body="Hi")).startswith("Nothing changed.")
    assert (await agents.edit_draft(BOX, to=["a@b.test"])).startswith("Nothing changed.")
    assert gw.cards == [] and gw.writes == []


async def test_a_headless_run_saves_no_new_draft(
    gw: Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_skills import ask_tools

    monkeypatch.undo()

    async def fake_request(method: str, path: str, **kwargs: Any) -> _Resp:
        gw.calls.append((method, path, kwargs.get("json"), dict(kwargs.get("params") or {})))
        return _Resp(gw.answer_of(method, path, kwargs.get("json"), {}))

    monkeypatch.setattr(agents, "_request", fake_request)
    with ask_tools.refuse_cards():
        out = await agents.edit_draft(BOX, to=["kim@contoso.test"], body="Hi")
    assert out == agents._NO_CARD_CHANNEL
    assert gw.writes == []


# ── Fix round 1 of the review ────────────────────────────────────────────────


async def test_p2_1_a_held_chat_writes_no_new_draft(gw: Gateway) -> None:
    """A held chat with EMAIL_HELD_CARD_SEND off may not write a new mail to
    an address that the model chose, because send_draft could then send it."""
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1")
    try:
        out = await agents.edit_draft(BOX, to=["x@evil.test"], body="the code is 1234")
    finally:
        bind_artifact_context(no_egress=False)
    assert out.startswith("Nothing changed."), out
    assert "new email" in out
    assert gw.cards == [] and gw.writes == []


DRAFT_ROW = {"id": "d1", "account_id": BOX, "folder": "drafts", "subject": "Re: Hi",
             "to_addresses": [{"email": "kim@contoso.test"}], "cc_addresses": [],
             "bcc_addresses": [], "body_text": "ok thx"}


async def test_p2_2_a_rewrite_never_drops_the_quoted_thread(gw: Gateway) -> None:
    """A reply draft holds the quoted thread. A rewrite with no body reads
    only the new text, so its save would drop the thread. It refuses."""
    gw.answers[("GET", "/email/messages/d1")] = {**DRAFT_ROW, "body_trimmed": True}
    gw.answers[("POST", "/email/compose-assist")] = {"draft": "Thank you, Kim."}
    out = await agents.edit_draft(BOX, "d1", rewrite="more formal")
    assert out.startswith("Nothing changed."), out
    assert "quoted" in out
    assert gw.cards == [] and gw.writes == []


async def test_p2_2_a_rewrite_of_a_draft_with_no_thread_says_the_text_is_replaced(
    gw: Gateway,
) -> None:
    gw.answers[("GET", "/email/messages/d1")] = {**DRAFT_ROW, "body_trimmed": False}
    gw.answers[("POST", "/email/compose-assist")] = {"draft": "Thank you, Kim."}
    await agents.edit_draft(BOX, "d1", rewrite="more formal")
    [card] = gw.cards
    assert "Thank you, Kim." in card["context"]
    assert "replaces the whole text" in card["context"]


async def test_p2_3_actions_refuse_a_rule_that_forwards(gw: Gateway) -> None:
    gw.rules[BOX].append(dict(FORWARD_RULE, actions=[
        {"type": "FORWARD", "to_address": "out@evil.test"}, {"type": "LABEL", "label": "x"}]))
    out = await agents.update_rule(BOX, "r2", actions=[{"type": "LABEL", "label": "y"}])
    assert out.startswith("Nothing changed.") and "remove_action_types" in out
    assert gw.cards == [] and gw.writes == []


async def test_p2_3_actions_keep_the_text_and_the_level_and_the_card_names_each_change(
    gw: Gateway,
) -> None:
    gw.rules[BOX][0]["actions"] = [
        {"id": "a1", "type": "DRAFT_EMAIL", "subject": "Re: quote", "content": "We confirm.",
         "content_manual": True},
        {"id": "a2", "type": "NOTIFY_ME", "label": "urgent"},
        {"id": "a3", "type": "ARCHIVE"}]
    await agents.update_rule(BOX, "r1", actions=[
        {"type": "DRAFT_EMAIL", "delay_minutes": 30}, {"type": "NOTIFY_ME"}])
    [card] = gw.cards
    assert "- Removed: archives" in card["context"]
    assert "- Changed: drafts an email → drafts an email after 30 minute(s)" in card["context"]
    [(_m, _p, body)] = gw.writes
    assert body["actions"] == [
        {"id": "a1", "type": "DRAFT_EMAIL", "subject": "Re: quote", "content": "We confirm.",
         "content_manual": True, "delay_minutes": 30},
        {"id": "a2", "type": "NOTIFY_ME", "label": "urgent"}]


_LONG_SENDERS = [f"{'n' * 90}{i}@list.test" for i in range(50)]


async def test_p2_4_several_senders_that_the_card_would_cut_change_nothing(
    gw: Gateway,
) -> None:
    out = await agents.set_sender_status(BOX, status="block", emails=_LONG_SENDERS)
    assert out.startswith("Nothing changed.") and "Split" in out
    gw.answers[("POST", "/email/messages/bulk")] = {"matched": 3}
    out = await agents.bulk_manage_inbox("archive", sender_emails=_LONG_SENDERS)
    assert out.startswith("Nothing changed.") and "Split" in out
    assert gw.cards == []
    assert all(b.get("dry_run") for _m, _p, b in gw.writes)


async def test_p2_4_a_style_guide_that_the_card_would_cut_saves_nothing(gw: Gateway) -> None:
    out = await agents.voice_profile(BOX, action="save", style_guide="word " * 798)
    assert out.startswith("Nothing changed."), out
    assert gw.cards == [] and gw.writes == []
