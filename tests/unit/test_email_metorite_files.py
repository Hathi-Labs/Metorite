"""A Metorite file on a chat email (H-392, owner decision of 2026-10-24).

``email_app_master_plan.md`` §21.10. A chat may attach a file that the member
can already see: a file of the member's own workspace (Artifacts), a
Knowledge page as its Markdown, or a file on a task. Each one resolves as the
member, through the read of its own app, and a file that the member cannot
see sends nothing. The card names each file and its size. A held chat sends
one only behind the recorded held card, and the act resolves it again and
checks its digest.

R7, each with the mutation that turns it red:

* ``test_each_form_parses_and_any_other_is_refused`` — accept a bare path.
* ``test_a_page_behind_a_closed_gate_is_not_visible`` — drop a gate.
* ``test_a_page_the_member_cannot_read_sends_nothing`` — read the page with
  no visibility clause.
* ``test_a_task_file_resolves_through_the_one_visibility_read`` — read the
  attachment row in another way.
* ``test_a_held_chat_refuses_a_file_of_its_own_workspace`` — drop the
  ``held`` refusal.
* ``test_the_files_are_capped_together`` — drop the cap.
* ``test_a_changed_file_sends_nothing`` — skip ``check_digests``.
* ``test_a_held_card_records_each_file_and_the_act_checks_it`` — send a
  Metorite file from the request, or with no second read.
* ``test_the_free_card_names_each_file_and_its_size`` — leave the size off.
"""
from __future__ import annotations

import contextlib
import hashlib
import types
from typing import Any

import pytest
from acb_skills import held_card as H
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.transport import metorite_files as mf

from tests.unit import _held_card_fakes as F

PAGE = "0a0a0a0a-1111-4000-8000-00000000000a"
TASK_FILE = "0b0b0b0b-2222-4000-8000-00000000000b"


class _User(types.SimpleNamespace):
    def has_permission(self, perm: str) -> bool:
        return perm in self.perms


def _user(*perms: str) -> Any:
    return _User(email=F.MEMBER, organization_id=F.ORG,
                 perms=set(perms or ("feature:knowledge", "feature:projects")))


@contextlib.asynccontextmanager
async def _session():
    yield object()


@pytest.fixture()
def stores(monkeypatch, tmp_path):
    """The three stores, each with one file that the member can see."""
    from gateway.routes.knowledge import core as kb
    from gateway.routes.knowledge import flags
    from gateway.routes.projects import attachments as pa
    from gateway.routes.projects import core as pm

    state: dict[str, Any] = {"pages": {PAGE: types.SimpleNamespace(
        title="Price list / 2026", body_md="# Prices\n\nA: 10")},
        "task_files": {}, "artifacts": {}, "kb_on": True, "reads": []}
    task_path = tmp_path / "spec.pdf"
    task_path.write_bytes(b"%PDF-task")
    state["task_files"][TASK_FILE] = types.SimpleNamespace(
        name="spec.pdf", mime="application/pdf", path=str(task_path), size_bytes=9)

    async def load_page(db: Any, user: Any, page_id: str) -> Any:
        state["reads"].append(("page", user.email, page_id))
        return state["pages"].get(page_id)

    async def visible(db: Any, user: Any, attachment_id: str) -> Any:
        state["reads"].append(("task-file", user.email, attachment_id))
        return state["task_files"].get(attachment_id)

    def artifacts(refs: Any, email: str | None = None) -> list[dict[str, Any]]:
        out = []
        for r in refs:
            state["reads"].append(("artifact", email, f"{r.agent}:{r.path}"))
            got = state["artifacts"].get(f"{r.agent}:{r.path}")
            if got is None:
                raise HTTPException(status_code=422, detail="not in your own workspace")
            out.append({"filename": r.path.rsplit("/", 1)[-1], "content": got,
                        "mime_type": "application/pdf"})
        return out

    from gateway.routes.email.transport import send

    monkeypatch.setattr(kb, "load_page", load_page)
    monkeypatch.setattr(kb, "_tenant_session", _session)
    monkeypatch.setattr(flags, "knowledge_org_allowed", lambda org: state["kb_on"] and bool(org))
    monkeypatch.setattr(pa, "visible_task_attachment", visible)
    monkeypatch.setattr(pm, "_tenant_session", _session)
    monkeypatch.setattr(send, "load_artifact_attachments", artifacts)
    state["artifacts"]["projects-assistant:outputs/plan.pdf"] = b"%PDF-plan"
    state["artifacts"]["email-assistant:outputs/run.pdf"] = b"%PDF-run"
    return state


@pytest.mark.parametrize(("ref", "want"), [
    (f"knowledge:{PAGE.upper()}", ("knowledge", PAGE)),
    (f"task-file:{TASK_FILE}", ("task-file", TASK_FILE)),
    ("artifact:projects-assistant:outputs/plan.pdf",
     ("artifact", "projects-assistant:outputs/plan.pdf")),
])
def test_each_form_parses_and_any_other_is_refused(ref: str, want: tuple[str, str]) -> None:
    assert H.parse_file_ref(ref) == want
    assert H.is_metorite_file_ref(ref)
    for bad in ("outputs/plan.pdf", "knowledge:not-a-uuid", "artifact::x.pdf",
                "artifact:agent:", "task-file:", "projects-assistant:outputs/plan.pdf"):
        with pytest.raises(ValueError):
            H.parse_file_ref(bad)


async def test_a_page_goes_as_its_markdown_with_a_safe_name(stores) -> None:
    [f] = await mf.resolve_metorite_files(_user(), [f"knowledge:{PAGE}"])
    assert f.name == "Price list 2026.md" and f.mime == "text/markdown"
    assert f.content == b"# Prices\n\nA: 10" and f.size == len(f.content)
    assert f.digest == hashlib.sha256(f.content).hexdigest()
    assert stores["reads"] == [("page", F.MEMBER, PAGE)]


@pytest.mark.parametrize("closed", ["flag", "feature"])
async def test_a_page_behind_a_closed_gate_is_not_visible(stores, closed: str) -> None:
    """The gates of the Knowledge router: the switch for the organization,
    then ``feature:knowledge``. Either one closed is a 404, with no read."""
    if closed == "flag":
        stores["kb_on"] = False
        user = _user()
    else:
        user = _user("feature:projects")
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(user, [f"knowledge:{PAGE}"])
    assert e.value.status_code == 404 and stores["reads"] == []


async def test_a_page_the_member_cannot_read_sends_nothing(stores) -> None:
    other = "0c0c0c0c-3333-4000-8000-00000000000c"
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(_user(), [f"knowledge:{other}"])
    assert e.value.status_code == 404 and e.value.detail == mf.FILE_NOT_VISIBLE


async def test_a_task_file_resolves_through_the_one_visibility_read(stores) -> None:
    [f] = await mf.resolve_metorite_files(_user(), [f"task-file:{TASK_FILE}"])
    assert (f.name, f.content, f.mime) == ("spec.pdf", b"%PDF-task", "application/pdf")
    assert stores["reads"] == [("task-file", F.MEMBER, TASK_FILE)]
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(_user("feature:knowledge"), [f"task-file:{TASK_FILE}"])
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(
            _user(), ["task-file:0d0d0d0d-4444-4000-8000-00000000000d"])
    assert e.value.status_code == 404


async def test_an_artifact_comes_from_the_members_own_workspace(stores) -> None:
    [f] = await mf.resolve_metorite_files(
        _user(), ["artifact:projects-assistant:outputs/plan.pdf"])
    assert f.content == b"%PDF-plan"
    assert stores["reads"] == [("artifact", F.MEMBER, "projects-assistant:outputs/plan.pdf")]
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(_user(), ["artifact:projects-assistant:outputs/no.pdf"])
    assert e.value.status_code == 422


async def test_a_held_chat_refuses_a_file_of_its_own_workspace(stores) -> None:
    ref = "artifact:email-assistant:outputs/run.pdf"
    assert len(await mf.resolve_metorite_files(_user(), [ref])) == 1
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(_user(), [ref], held=True)
    assert e.value.status_code == 422 and e.value.detail == H.HELD_WORKSPACE_REFUSED


async def test_a_mail_file_is_not_a_metorite_file(stores) -> None:
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(_user(), [f"attachment:{F.FILE_PDF}"])
    assert e.value.status_code == 422


async def test_the_files_are_capped_together(stores, monkeypatch) -> None:
    monkeypatch.setattr(hc, "MAX_FILES_BYTES", 12)
    with pytest.raises(HTTPException) as e:
        await mf.resolve_metorite_files(
            _user(), ["artifact:projects-assistant:outputs/plan.pdf", f"task-file:{TASK_FILE}"])
    assert e.value.status_code == 422


async def test_a_changed_file_sends_nothing(stores) -> None:
    [f] = await mf.resolve_metorite_files(_user(), [f"knowledge:{PAGE}"])
    mf.check_digests([f], {f.ref: f.digest})
    with pytest.raises(HTTPException) as e:
        mf.check_digests([f], {f.ref: "0" * 64})
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        mf.check_digests([f], {f.ref: f.digest, "knowledge:other": "0" * 64})


def test_the_send_route_resolves_and_checks_each_file() -> None:
    """The send path reads each file again and checks the digest, before the
    provider call. A source fence, beside the behaviour of the two helpers."""
    import inspect

    from gateway.routes.email.transport import send

    src = inspect.getsource(send.send_email)
    resolve_at = src.index("resolve_metorite_files(")
    assert src.index("check_digests(", resolve_at) < src.index("send_message(")
    fields = send.SendEmailRequest.model_fields
    assert "metorite_files" in fields


# ── The held card (§21.10) ───────────────────────────────────────────────────


def _member_sees_both_apps(monkeypatch) -> None:
    """The session member holds feature:knowledge and feature:projects."""
    monkeypatch.setattr(type(F.user()), "has_permission",
                        lambda self, perm: perm in {"feature:knowledge", "feature:projects"})


async def test_a_held_card_records_each_file_and_the_act_checks_it(stores, monkeypatch) -> None:
    F.flag_on(monkeypatch)
    _member_sees_both_apps(monkeypatch)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)
    sent = F.install_sends(monkeypatch)
    card = await hc.held_card(hc.HeldCardRequest(
        kind="new", account_id=F.BOX, to=[F.KNOWN], subject="Prices", note="Here they are.",
        files=[f"knowledge:{PAGE}", f"task-file:{TASK_FILE}"]), F.user())
    files = [r for r in card["rows"] if r["kind"] == "file"]
    assert [r["label"] for r in files] == ['File: "Price list 2026.md" (1 KB)',
                                           'File: "spec.pdf" (1 KB)']
    assert "Knowledge" in files[0]["hint"] and "task" in files[1]["hint"]
    # The page changes after the card: nothing goes.
    stores["pages"][PAGE].body_md = "# Prices\n\nA: 999"
    answer = F.rows_answer("to-0", "file-0", "file-1")
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await hc.held_card_act(hc.HeldCardActRequest(
            card_id=card["card_id"], card_thread_id="chat-thread-1", answer=answer),
            BackgroundTasks(), F.user())
    assert e.value.status_code == 409 and sent.mails == []


async def test_a_held_card_sends_only_the_ticked_files(stores, monkeypatch) -> None:
    F.flag_on(monkeypatch)
    _member_sees_both_apps(monkeypatch)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)
    sent = F.install_sends(monkeypatch)
    card = await hc.held_card(hc.HeldCardRequest(
        kind="new", account_id=F.BOX, to=[F.KNOWN], subject="Prices", note="Here.",
        files=[f"knowledge:{PAGE}", f"task-file:{TASK_FILE}"]), F.user())
    answer = F.rows_answer("to-0", "file-1")
    await F.record_answer(card["card_id"], answer)
    out = await hc.held_card_act(hc.HeldCardActRequest(
        card_id=card["card_id"], card_thread_id="chat-thread-1", answer=answer),
        BackgroundTasks(), F.user())
    [mail] = sent.mails
    assert [a.filename for a in mail.attachments] == ["spec.pdf"]
    assert out["files"] == 1


async def test_a_held_card_refuses_the_own_workspace(stores, monkeypatch) -> None:
    F.flag_on(monkeypatch)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)
    with pytest.raises(HTTPException) as e:
        await hc.held_card(hc.HeldCardRequest(
            kind="new", account_id=F.BOX, to=[F.KNOWN], note="Hi.",
            files=["artifact:email-assistant:outputs/run.pdf"]), F.user())
    assert e.value.status_code == 422


# ── The free chat (send_email) ───────────────────────────────────────────────


async def test_the_free_card_names_each_file_and_its_size(monkeypatch) -> None:
    from acb_skills.write_artifact import bind_artifact_context

    agents = F.load_email_agent("ea_metorite_files")
    bind_artifact_context(no_egress=False)
    posts: list[tuple[str, dict[str, Any]]] = []
    cards: list[dict[str, Any]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        if path == "/email/metorite-files/resolve":
            return {"files": [{"ref": f"knowledge:{PAGE}", "source": "knowledge",
                               "name": "Price list.md", "size": 2048, "digest": "a" * 64}]}
        return {"id": "sent-1"}

    async def name(_aid: str) -> str:
        return f"Work · {F.MEMBER}"

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", lambda *_a: _empty())
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    out = await agents.send_email(F.BOX, body="Prices attached.", to=[F.KNOWN],
                                  subject="Prices", attachments=[f"knowledge:{PAGE}"])
    [c] = cards
    assert 'file "Price list.md" (2 KB)' in c["context"]
    assert "Price list.md (2 KB)" in c["detail"]
    [send] = [b for p, b in posts if p == "/email/send"]
    assert send["metorite_files"] == [{"ref": f"knowledge:{PAGE}", "digest": "a" * 64}]
    assert "artifacts" not in send
    assert "1 attachment(s)" in out


async def _empty() -> str:
    return ""


async def test_a_file_the_member_cannot_see_asks_no_card(monkeypatch) -> None:
    from acb_skills.write_artifact import bind_artifact_context

    agents = F.load_email_agent("ea_metorite_files_404")
    bind_artifact_context(no_egress=False)
    cards: list[Any] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        raise agents.GatewayError("Email POST failed (404)", 404, detail=mf.FILE_NOT_VISIBLE)

    async def name(_aid: str) -> str:
        return f"Work · {F.MEMBER}"

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", lambda *_a: _empty())
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    out = await agents.send_email(F.BOX, body="x", to=[F.KNOWN],
                                  attachments=[f"task-file:{TASK_FILE}"])
    assert out.startswith("Not sent.") and mf.FILE_NOT_VISIBLE in out
    assert cards == []
