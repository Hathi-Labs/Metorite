"""WS-17 EM-G3b — Gmail: a move to a user label, and the filter list.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.4, items 1 to 15,
with the audit corrections E-M1 to E-M13 and E-F1 to E-F5.

* Items 1 to 3 (E-M1 to E-M4): ``GmailProvider.move_to_folder`` reads the name
  with ``canonical_folder``, so each alias reaches the system branch. Sent and
  drafts raise, and so does a system label name. Each other name is a USER
  label: one ``modify`` adds it and removes ``INBOX``, ``TRASH`` and ``SPAM``
  (E-M5). A label that Gmail could not make raises.
* Items 4 to 6 (E-M6, E-M7): ``folder_after_move`` gives the folder that a
  move leaves, and ``local_folder_after_move`` is its one reader. A fake that
  is no ``BaseEmailProvider`` gets ``canonical_folder``.
* Items 7 to 11 (E-M8 to E-M12): the rule move and the PATCH store the folder
  of the provider. A Gmail rule move mirrors the label into ``categories``.
  The PATCH keeps the case of the name, and answers 400 for a refused move.
  The restore of Reply Zero finds a Gmail rule move. The no-op log still
  fires for IMAP.
* Items 12 to 15 (E-F1 to E-F5): ``list_filters`` reads the key ``filter``,
  maps each criterion and action, answers ``[]`` for a plain 403, and lets a
  rate limit pass up.

Each test drives the REAL ``GmailProvider`` through the real ``_get_client``
against a fake Gmail on ``httpx.MockTransport``, as ``test_gmail_send_and_drafts``
does. No test needs a database: the slice changes no SQL text (E-V1).

Run::

    uv run pytest tests/unit/test_gmail_move_and_filters.py -v -rs
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import email_ingestion.providers.gmail as gmail_mod
import httpx
import pytest
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import canonical_folder, local_folder_after_move
from email_ingestion.providers.gmail import GmailProvider, GmailRateLimited
from email_ingestion.providers.imap import IMAPProvider
from fastapi import HTTPException
from gateway.routes.email.automation import actions
from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.transport import messages as messages_mod

from tests.unit._email_fakes import bind_db

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "/gmail/v1/users/me"
USER = SimpleNamespace(email="u@em-g3b.test")
LABEL_MOVE_REMOVES = ["INBOX", "TRASH", "SPAM"]

#: The system labels that ``labels.list`` gives for each mailbox. Gmail names
#: a system label by its id.
_SYSTEM_LABELS = [
    "INBOX", "SENT", "DRAFT", "TRASH", "SPAM", "UNREAD", "STARRED",
    "IMPORTANT", "CHAT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
]


# ── the fake Gmail ──────────────────────────────────────────────────────────


class _Gmail:
    """A fake Gmail API that keeps its labels and the labels of each message.

    ``modify`` and ``trash`` change the labels of a message, as Gmail does.
    ``refuse_create`` makes ``labels.create`` answer 400. A name in ``late``
    is a label that another client made after our read: its create answers
    409, and the next ``labels.list`` holds it."""

    def __init__(self) -> None:
        self.labels: list[dict[str, str]] = [
            {"id": lid, "name": lid, "type": "system"} for lid in _SYSTEM_LABELS]
        self.labels.append({"id": "Label_1", "name": "Receipts", "type": "user"})
        self.messages: dict[str, list[str]] = {}
        self.seen: list[tuple[str, str, dict[str, Any]]] = []
        self.refuse_create = False
        self.late: dict[str, str] = {}
        self.filters_status = 200
        self.filters_body: dict[str, Any] = {}
        self.n = 1

    def calls(self, method: str, path: str) -> int:
        return sum(1 for m, p, _ in self.seen if (m, p) == (method, path))

    def requests(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.seen]

    def modifies(self) -> list[tuple[str, dict[str, Any]]]:
        return [(p.split("/")[2], b) for m, p, b in self.seen
                if m == "POST" and p.endswith("/modify")]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "at-2"})
        path = request.url.path.removeprefix(API)
        if path == "/profile":
            return httpx.Response(200, json={"historyId": "1"})
        method = request.method
        body = json.loads(request.content) if request.content else {}
        self.seen.append((method, path, body))
        parts = path.strip("/").split("/")
        if (method, path) == ("GET", "/labels"):
            return httpx.Response(200, json={"labels": list(self.labels)})
        if (method, path) == ("POST", "/labels"):
            return self._create_label(body)
        if (method, path) == ("GET", "/settings/filters"):
            return httpx.Response(self.filters_status, json=self.filters_body)
        if (method, path) == ("GET", "/drafts"):
            return httpx.Response(200, json={"drafts": [], "resultSizeEstimate": 0})
        if method == "POST" and parts[0] == "messages" and len(parts) == 3:
            return self._change_labels(parts[1], parts[2], body)
        return httpx.Response(404, json={"error": {"code": 404}})

    def _create_label(self, body: dict[str, Any]) -> httpx.Response:
        name = body.get("name", "")
        if name in self.late:
            self.labels.append({"id": self.late.pop(name), "name": name,
                                "type": "user"})
            return httpx.Response(409, json={"error": {
                "code": 409, "message": "Label name exists or conflicts"}})
        if self.refuse_create:
            return httpx.Response(400, json={"error": {
                "code": 400, "message": "Invalid label name"}})
        self.n += 1
        label = {"id": f"Label_{self.n}", "name": name, "type": "user"}
        self.labels.append(label)
        return httpx.Response(200, json=label)

    def _change_labels(self, mid: str, verb: str,
                       body: dict[str, Any]) -> httpx.Response:
        labels = self.messages.setdefault(mid, [])
        if verb == "modify":
            remove, add = body.get("removeLabelIds", []), body.get("addLabelIds", [])
        elif verb == "trash":
            remove, add = ["INBOX"], ["TRASH"]
        else:
            return httpx.Response(404, json={"error": {"code": 404}})
        labels[:] = [lid for lid in labels if lid not in remove]
        labels.extend(lid for lid in add if lid not in labels)
        return httpx.Response(200, json={"id": mid, "labelIds": labels})


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> _Gmail:
    """Send each ``httpx.AsyncClient`` that the provider builds to one fake.

    The rate-limit helper waits through ``gmail._wait``, so no test sleeps."""
    service = _Gmail()
    transport = httpx.MockTransport(service.handle)
    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    monkeypatch.setattr(gmail_mod, "_wait", _no_sleep)
    return service


def _provider() -> GmailProvider:
    return GmailProvider({"access_token": "at-1", "refresh_token": "r-1"},
                         app=OAuthApp(client_id="cid", client_secret="secret"))


class _Db:
    """A session double that records each statement with its parameters.

    A SELECT finds the row (the ownership read of the PATCH). ``rows``
    answers the read of the Reply Zero restore."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.rows = rows or []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        self.statements.append((sql, dict(params or {})))
        row = (SimpleNamespace(id="row-1", categories=[])
               if sql.lstrip().upper().startswith("SELECT") else None)
        rows = self.rows if "move_labels" in sql else []
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: rows,
                               rowcount=1)

    def params_of(self, fragment: str) -> list[dict[str, Any]]:
        return [p for s, p in self.statements if fragment in s]


def _patch_route(monkeypatch: pytest.MonkeyPatch, db: _Db, provider: Any,
                 built_at: list[int] | None = None) -> None:
    """Patch the seams of the PATCH route. ``built_at`` records how many
    statements ran before the provider was built."""

    async def _build(*_a: Any, **_kw: Any) -> tuple[Any, str, str, Any]:
        if built_at is not None:
            built_at.append(len(db.statements))
        return provider, "m-1", "acc-1", object()

    monkeypatch.setattr(messages_mod, "_tenant_session", bind_db(db))
    monkeypatch.setattr(messages_mod, "_provider_for_message", _build)
    monkeypatch.setattr(messages_mod, "_persist_rotated_creds", AsyncMock())
    monkeypatch.setattr(messages_mod, "get_message",
                        AsyncMock(return_value=object()))


def _move(label: str) -> list[dict[str, str]]:
    return [{"type": "MOVE_FOLDER", "label": label}]


# ── items 1 to 3: the move ──────────────────────────────────────────────────


async def test_a_move_to_a_label_adds_it_and_removes_inbox(fake: _Gmail) -> None:
    """Item 2 (GM-14, D-EM-33). One ``modify`` adds the user label and
    removes ``INBOX``, ``TRASH`` and ``SPAM``. Gmail keeps the id."""
    fake.messages["m-1"] = ["INBOX", "UNREAD"]
    p = _provider()

    assert await p.move_to_folder("m-1", "Receipts") is None

    assert fake.modifies() == [("m-1", {"addLabelIds": ["Label_1"],
                                        "removeLabelIds": LABEL_MOVE_REMOVES})]
    assert fake.calls("POST", "/labels") == 0
    assert fake.messages["m-1"] == ["UNREAD", "Label_1"]
    # The parse files the message as archive, as the row does (O-GM-1).
    assert gmail_mod._gmail_folder_from_labels(fake.messages["m-1"]) == "archive"


async def test_a_move_to_a_new_label_creates_it_first(fake: _Gmail) -> None:
    """Item 2. A new name becomes a user label before the ``modify``. The
    name goes to Google unchanged, with its case and its "/"."""
    fake.messages["m-1"] = ["INBOX"]
    p = _provider()

    await p.move_to_folder("m-1", "Clients/Acme Corp")

    assert fake.requests() == [("GET", "/labels"), ("POST", "/labels"),
                               ("POST", "/messages/m-1/modify")]
    created = [b for m, p_, b in fake.seen if (m, p_) == ("POST", "/labels")]
    assert created[0]["name"] == "Clients/Acme Corp"
    assert fake.modifies() == [("m-1", {"addLabelIds": ["Label_2"],
                                        "removeLabelIds": LABEL_MOVE_REMOVES})]


@pytest.mark.parametrize(("name", "key"), [
    ("Receipts", "archive"),
    ("Clients/Acme Corp", "archive"),
    ("INBOX", "inbox"),
    ("Archive", "archive"),
    ("Bin", "trash"),
    ("Deleted Items", "trash"),
    ("Junk Email", "junk"),
    ("Spam", "junk"),
    ("Sent", None),
    ("Sent Items", None),
    ("Drafts", None),
    ("draft", None),
    ("Starred", None),
    ("CATEGORY_SOCIAL", None),
])
def test_the_folder_after_a_gmail_label_move_is_archive(
        name: str, key: str | None) -> None:
    """Items 4 to 6 (E-M6, E-M7). Gmail gives ``archive`` for a user label,
    the key for a system folder, and ``None`` for a move that it refuses.
    The base gives ``canonical_folder``. The helper gives ``canonical_folder``
    for a fake that is no ``BaseEmailProvider``, and calls nothing on it."""
    gmail = _provider()
    assert gmail.folder_after_move(name) == key
    assert local_folder_after_move(gmail, name) == key

    assert IMAPProvider({}).folder_after_move(name) == canonical_folder(name)

    mock = AsyncMock()
    assert local_folder_after_move(mock, name) == canonical_folder(name)
    assert not mock.folder_after_move.called

    class _Plain:
        async def move_to_folder(self, *_a: Any) -> None:
            return None

    assert local_folder_after_move(_Plain(), name) == canonical_folder(name)


@pytest.mark.parametrize(("name", "request_"), [
    ("Bin", ("POST", "/messages/m-1/trash", {})),
    ("Deleted Items", ("POST", "/messages/m-1/trash", {})),
    ("Junk Email", ("POST", "/messages/m-1/modify",
                    {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]})),
    ("Spam", ("POST", "/messages/m-1/modify",
              {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]})),
    ("Archive", ("POST", "/messages/m-1/modify", {"removeLabelIds": ["INBOX"]})),
    ("INBOX", ("POST", "/messages/m-1/modify",
               {"addLabelIds": ["INBOX"], "removeLabelIds": ["TRASH", "SPAM"]})),
])
async def test_a_gmail_move_to_an_alias_hits_the_system_branch(
        fake: _Gmail, name: str, request_: tuple[str, str, dict[str, Any]]) -> None:
    """Item 1 (E-M1). ``canonical_folder`` reads the name, so an alias of a
    system folder never becomes a user label."""
    fake.messages["m-1"] = ["INBOX"]
    p = _provider()

    await p.move_to_folder("m-1", name)

    changes = [s for s in fake.seen if s[1].startswith("/messages/")]
    assert changes == [request_]
    assert fake.calls("GET", "/labels") == 0
    assert fake.calls("POST", "/labels") == 0


@pytest.mark.parametrize("name", ["sent", "Sent Items", "Drafts", "draft"])
async def test_a_gmail_move_to_sent_or_drafts_is_refused(
        fake: _Gmail, name: str) -> None:
    """Item 1 (E-M2). Gmail sets ``SENT`` and ``DRAFT`` itself, so a move to
    either raises before any request."""
    p = _provider()

    with pytest.raises(ValueError, match="cannot move"):
        await p.move_to_folder("m-1", name)

    assert fake.seen == []


@pytest.mark.parametrize("name", [
    "Starred", "important", "UNREAD", "Chat", "CATEGORY_PROMOTIONS",
    "category_social",
])
async def test_a_move_to_a_system_label_name_is_refused(
        fake: _Gmail, name: str) -> None:
    """Item 1 (E-M3). Without the rule, "Starred" stars the message and
    archives it. A reserved name raises before any request. A system label
    that the reserved set does not name raises too, because the move checks
    the resolved id against the USER labels (E-M2)."""
    p = _provider()
    with pytest.raises(ValueError, match="system label"):
        await p.move_to_folder("m-1", name)
    assert fake.seen == []

    # A system label that Google may add later, which no list here names.
    fake.labels.append({"id": "FUTURE_SYSTEM", "name": "FUTURE_SYSTEM",
                        "type": "system"})
    with pytest.raises(ValueError, match="system label"):
        await p.move_to_folder("m-1", "future_system")
    assert fake.calls("POST", "/labels") == 0
    assert fake.modifies() == []


async def test_a_failed_label_create_raises(fake: _Gmail) -> None:
    """Item 3 (E-M4). A quiet return would let the caller store ``archive``
    for a move that did not occur."""
    fake.refuse_create = True
    fake.messages["m-1"] = ["INBOX"]
    p = _provider()

    with pytest.raises(ValueError, match="Could not create Gmail label"):
        await p.move_to_folder("m-1", "Clients")

    assert fake.modifies() == []
    assert fake.messages["m-1"] == ["INBOX"]


async def test_a_move_from_trash_to_a_label_removes_trash(fake: _Gmail) -> None:
    """Item 2 (E-M5). A move out of Trash or Spam leaves ``archive``."""
    fake.messages["t-1"] = ["TRASH"]
    fake.messages["s-1"] = ["SPAM", "UNREAD"]
    p = _provider()

    await p.move_to_folder("t-1", "Receipts")
    await p.move_to_folder("s-1", "Receipts")

    assert fake.messages["t-1"] == ["Label_1"]
    assert fake.messages["s-1"] == ["UNREAD", "Label_1"]
    for mid in ("t-1", "s-1"):
        assert gmail_mod._gmail_folder_from_labels(fake.messages[mid]) == "archive"


async def test_a_label_create_409_resolves_by_reread(fake: _Gmail) -> None:
    """Item 2. Another client made the label after our read, so the create
    answers 409. The move reads the list again and uses that label."""
    fake.late = {"Suppliers": "Label_7"}
    fake.messages["m-1"] = ["INBOX"]
    p = _provider()

    await p.move_to_folder("m-1", "Suppliers")

    assert fake.calls("GET", "/labels") == 2
    assert fake.calls("POST", "/labels") == 1
    assert fake.modifies() == [("m-1", {"addLabelIds": ["Label_7"],
                                        "removeLabelIds": LABEL_MOVE_REMOVES})]


# ── items 7 to 11: the callers ──────────────────────────────────────────────


async def test_a_rule_move_stores_the_folder_of_the_provider(fake: _Gmail) -> None:
    """Item 7 (E-M12). The row stores the folder that the provider says the
    move leaves, and a Gmail label move mirrors the label into
    ``categories``. A refused move writes nothing and calls nothing."""
    fake.messages["m-1"] = ["INBOX"]
    db, errors = _Db(), []

    done = await actions._apply_rule_actions(
        db, _provider(), "row-1", "m-1", _move(" Receipts "), {},
        account_id="acc-1", errors_out=errors)

    assert (done, errors) == (["MOVE_FOLDER"], [])
    assert db.params_of("SET folder=:f") == [{"id": "row-1", "f": "archive"}]
    assert db.params_of("array_append(categories, :lbl)") == [
        {"id": "row-1", "lbl": "Receipts"}]

    # A fake that is no BaseEmailProvider keeps the canonical key, no mirror.
    db = _Db()
    mock = AsyncMock()
    mock.move_to_folder.return_value = None
    await actions._apply_rule_actions(db, mock, "row-2", "pm-2",
                                      _move("Receipts"), {})
    assert db.params_of("SET folder=:f") == [{"id": "row-2", "f": "receipts"}]
    assert db.params_of("array_append") == []

    # Gmail refuses Sent: no request, no write, and the error is recorded.
    db, errors, fake.seen = _Db(), [], []
    done = await actions._apply_rule_actions(
        db, _provider(), "row-3", "m-1", _move("Sent"), {}, errors_out=errors)
    assert done == []
    assert [e["type"] for e in errors] == ["MOVE_FOLDER"]
    assert db.statements == []
    assert fake.seen == []


async def test_a_patch_move_stores_the_folder_of_the_provider(
        fake: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 8 (E-M8). The route builds the provider before it writes the
    folder, and stores the folder of the provider. A refused move answers
    400 and writes nothing."""
    fake.messages["m-1"] = ["INBOX"]
    db, built_at = _Db(), []
    _patch_route(monkeypatch, db, _provider(), built_at)

    await messages_mod.update_message(
        "row-1", messages_mod.MessageUpdateModel(folder="Receipts"), user=USER)

    assert built_at == [1], "only the ownership read runs before the build"
    assert db.params_of("folder = :folder")[0]["folder"] == "archive"
    assert fake.modifies() == [("m-1", {"addLabelIds": ["Label_1"],
                                        "removeLabelIds": LABEL_MOVE_REMOVES})]

    db, fake.seen = _Db(), []
    _patch_route(monkeypatch, db, _provider())
    with pytest.raises(HTTPException) as refused:
        await messages_mod.update_message(
            "row-1", messages_mod.MessageUpdateModel(folder="Sent", is_read=True),
            user=USER)
    assert refused.value.status_code == 400
    assert db.params_of("UPDATE") == []
    assert fake.seen == []


async def test_a_patch_move_keeps_the_case_of_the_name(
        fake: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 9 (E-M9). The PATCH sends the name with ``.strip()``, so a new
    label or Outlook folder keeps its case, as a rule move does."""
    mock = AsyncMock()
    mock.authenticate.return_value = True
    mock.move_to_folder.return_value = None
    db = _Db()
    _patch_route(monkeypatch, db, mock)

    await messages_mod.update_message(
        "row-1", messages_mod.MessageUpdateModel(folder=" Cold Email "), user=USER)

    mock.move_to_folder.assert_awaited_once_with("m-1", "Cold Email")
    assert db.params_of("folder = :folder")[0]["folder"] == "cold email"

    fake.messages["m-1"] = ["INBOX"]
    _patch_route(monkeypatch, _Db(), _provider())
    await messages_mod.update_message(
        "row-1", messages_mod.MessageUpdateModel(folder="Cold Email"), user=USER)
    created = [b for m, p, b in fake.seen if (m, p) == ("POST", "/labels")]
    assert [b["name"] for b in created] == ["Cold Email"]


async def test_the_restore_finds_a_gmail_label_move(fake: _Gmail) -> None:
    """Item 10 (E-M10). A Gmail rule move leaves the row in ``archive``, so
    the restore compares with the folder of the provider. A rule label that
    the provider refuses matches no row."""
    fake.messages["g-2"] = ["Label_1"]
    rows = [
        SimpleNamespace(id="r-2", provider_message_id="g-2", folder="archive",
                        move_labels=["Receipts"]),
        SimpleNamespace(id="r-3", provider_message_id="g-3", folder="archive",
                        move_labels=["Sent"]),
    ]
    db = _Db(rows)

    await rz._restore_conversation_messages(db, _provider(), "acc-1", "t-1")

    assert fake.modifies() == [("g-2", {"addLabelIds": ["INBOX"],
                                        "removeLabelIds": ["TRASH", "SPAM"]})]
    assert db.params_of("SET folder = 'inbox'") == [{"id": "r-2", "pid": None}]


async def test_the_noop_log_still_fires_for_imap(
        fake: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 11 (E-M11). IMAP keeps the no-op move of the base, so a move to
    a user folder logs ``email.move_folder_noop``. So does a provider that
    re-keys its ids and gets no new id. A Gmail label move logs nothing."""
    log = MagicMock()
    monkeypatch.setattr(actions, "_log", log)

    def _noops() -> list[Any]:
        return [c for c in log.info.call_args_list
                if c.args == ("email.move_folder_noop",)]

    await actions._apply_rule_actions(_Db(), IMAPProvider({}), "row-1", "7",
                                      _move("Cold Email"), {}, account_id="acc-1")
    assert [c.kwargs for c in _noops()] == [
        {"account_id": "acc-1", "folder": "cold email"}]

    class _Rekeys(IMAPProvider):
        REKEYS_MESSAGE_IDS = True

        async def move_to_folder(self, *_a: Any) -> None:
            return None

    log.reset_mock()
    await actions._apply_rule_actions(_Db(), _Rekeys({}), "row-1", "7",
                                      _move("Cold Email"), {}, account_id="acc-1")
    assert len(_noops()) == 1

    log.reset_mock()
    fake.messages["m-1"] = ["INBOX"]
    await actions._apply_rule_actions(_Db(), _provider(), "row-1", "m-1",
                                      _move("Cold Email"), {}, account_id="acc-1")
    assert fake.modifies(), "the Gmail label move ran"
    assert _noops() == []


# ── items 12 to 15: the filter list ─────────────────────────────────────────


async def test_list_filters_maps_criteria_and_actions(fake: _Gmail) -> None:
    """Items 12 to 14 (E-F1 to E-F4). Google answers with the key
    ``filter``, which is singular, and with ``{}`` for no filter."""
    fake.filters_body = {"filter": [
        {"id": "f-1",
         "criteria": {"from": "news@shop.test OR deals@shop.test"},
         "action": {"addLabelIds": ["Label_1", "STARRED", "IMPORTANT",
                                    "CATEGORY_PROMOTIONS"],
                    "removeLabelIds": ["INBOX", "UNREAD", "SPAM"]}},
        {"id": "f-2",
         "criteria": {"subject": "invoice", "to": "billing@me.test",
                      "query": "has:pdf", "hasAttachment": True,
                      "negatedQuery": "spam", "size": 1000},
         "action": {"addLabelIds": ["Label_gone", "TRASH"],
                    "forward": "boss@me.test"}},
        {"id": "f-3", "criteria": {"subject": "receipt"},
         "action": {"removeLabelIds": ["INBOX"]}},
    ]}
    p = _provider()

    got = await p.list_filters()

    assert got == [
        {"id": "f-1", "name": "From news@shop.test OR deals@shop.test",
         "enabled": True,
         "from_addresses": ["news@shop.test OR deals@shop.test"],
         "summary": ["label: Receipts", "skip inbox", "mark read", "star"]},
        {"id": "f-2", "name": "has:pdf", "enabled": True, "from_addresses": [],
         "summary": ["subject contains “invoice”",
                     "to contains “billing@me.test”", "matches “has:pdf”",
                     "has attachment", "label: Label_gone", "forward",
                     "trash"]},
        {"id": "f-3", "name": "receipt", "enabled": True, "from_addresses": [],
         "summary": ["subject contains “receipt”", "skip inbox"]},
    ]
    assert fake.calls("GET", "/settings/filters") == 1

    # A mailbox with no filter: Google answers {}, and no label read runs.
    fake.filters_body, fake.seen = {}, []
    assert await _provider().list_filters() == []
    assert fake.requests() == [("GET", "/settings/filters")]


async def test_list_filters_answers_empty_on_a_403(fake: _Gmail) -> None:
    """Item 15 (E-F5). A plain 403, such as a missing
    ``gmail.settings.basic`` scope, gives ``[]`` after one try. A rate limit
    passes up, so the rules screen answers ``provider_rules_supported:
    false`` for it (``rules.py``)."""
    fake.filters_status = 403
    fake.filters_body = {"error": {"code": 403, "errors": [
        {"reason": "insufficientPermissions"}]}}

    assert await _provider().list_filters() == []
    assert fake.calls("GET", "/settings/filters") == 1

    fake.filters_status = 429
    fake.filters_body = {"error": {"code": 429}}
    with pytest.raises(GmailRateLimited):
        await _provider().list_filters()
