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
