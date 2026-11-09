"""WS-17 SG-2 — one resolver gives each reader its signature (D-EM-64, D-EM-66).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.3, §23.5 and
§23.7 (rows 2, 3 and 4).

* **Rule 7.** A mailbox with no row resolves to the stored ``custom`` bytes
  unchanged. With ``EMAIL_SIGNATURE_TEMPLATES_ORGS`` off, the resolver runs
  the one query of main and never reads ``email_signature_settings``, so each
  reader sends the bytes of main.
* **The source scan.** No code outside ``signature.py`` selects ``signature``
  from ``email_assistant_settings``. Each reader of §23.3.3 reaches the
  resolver with its kind. Each caller of ``strip_signature_text`` gets its
  signature from the resolver.
* **One signature.** A reply that a draft path wrote and that the send then
  signs carries exactly one signature, for each ``reply_mode``. A change of
  mode between the draft and the send gives one signature too (§23.9).
* **One reader of the flag.** Only ``signature.templates_on`` reads
  ``EMAIL_SIGNATURE_TEMPLATES_ORGS`` (§23.5).

⚠️ Limit of the scan (R7): it reads string literals, and a ``+`` of two
literals. A column name that the code builds at run time passes it.

No database. ``test_email_signature_settings_r8.py`` runs the SQL against a
real Postgres (R8).
"""
from __future__ import annotations

import ast
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email import signature as sig
from gateway.routes.email import signature_preview, signature_render
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import drafting
from gateway.routes.email.automation import reply_card as rc
from gateway.routes.email.signature import (
    append_signature_text,
    build_signed_bodies,
    signature_text,
)
from gateway.routes.email.transport import send as send_mod
from gateway.routes.email.transport.send import SendEmailRequest
from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps/services/gateway/gateway"
_EMAIL = _GATEWAY / "routes/email"
_SIGNATURE_PY = _EMAIL / "signature.py"
_ACC = "0b6f8d1e-1c4a-4f7e-9a51-3d2c1b0a9e88"
_ME = UserContext(email="jane@sg2.test", role=UserRole.EMPLOYEE)
_FIELDS = {"name": "Jane Doe", "title": "Head of Sales", "company": "Acme Works",
           "phone": "+91 98450 12345", "accent": "#1a73e8"}
_REPLY = "Hi Kim,\n\nThe quote stands.\n\nOn Mon, 9 Nov 2026, Kim wrote:\n> Is the quote fixed?"

#: Stored values of ``email_assistant_settings.signature``: none, NULL, empty,
#: text, HTML, a line-end space, and text that is not ASCII.
_STORED = [None, "\x00NULL", "", "Jane", "<p>Jane <b>Doe</b></p><p>Acme</p>",
           "-- \nJane Doe\nAcme", "Jāné — Ācme ✓"]


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "email_signature_templates_orgs", value,
                        raising=False)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return [self._row] if self._row is not None else []


class _Db:
    """A session that answers the two signature reads and records each SQL.

    ``stored`` None is no settings row, and ``"\\x00NULL"`` is a row whose
    signature is NULL. ``settings`` is the ``email_signature_settings`` row."""

    def __init__(self, stored: str | None = None, settings: Any = None) -> None:
        self.stored = stored
        self.settings = settings
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.sql.append(sql)
        if "FROM email_signature_settings" in sql:
            return _Result(self.settings)
        if "FROM email_assistant_settings" in sql:
            if self.stored is None:
                return _Result(None)
            value = None if self.stored == "\x00NULL" else self.stored
            return _Result(SimpleNamespace(
                signature=value, about="", personal_instructions="",
                writing_style="", learned_writing_style=""))
        return _Result(None)


def _main_bytes(stored: str | None) -> str:
    """What main read: ``(row.signature if row else "") or ""``."""
    if stored is None:
        return ""
    return ("" if stored == "\x00NULL" else stored) or ""


def _template(reply_mode: str = "full", layout: str = "classic", mode: str = "template",
              fields: Any = None) -> SimpleNamespace:
    return SimpleNamespace(mode=mode, layout=layout, reply_mode=reply_mode,
                           fields=_FIELDS if fields is None else fields)


# ── 1. Rule 7: no row resolves to the stored bytes unchanged ────────────────


@pytest.mark.parametrize("stored", _STORED)
@pytest.mark.parametrize("kind", sig.SIGNATURE_KINDS)
async def test_no_row_resolves_to_the_stored_bytes_unchanged(
    monkeypatch: pytest.MonkeyPatch, stored: str | None, kind: str,
) -> None:
    for value in ("", "*"):
        _flag(monkeypatch, value)
        db = _Db(stored)
        assert await sig.resolve_signature(db, _ACC, kind) == _main_bytes(stored)
        assert await sig.resolve_signature_for_send(db, _ACC, kind, _REPLY) == (
            _main_bytes(stored))
        forms = await sig.signature_forms(db, _ACC)
        assert forms.stored == forms.of(kind) == _main_bytes(stored)


@pytest.mark.parametrize("settings", [None, _template(), _template("none"),
                                      _template(mode="company")])
async def test_with_the_flag_off_the_resolver_runs_the_one_query_of_main(
    monkeypatch: pytest.MonkeyPatch, settings: Any,
) -> None:
    """Even a template row gives the stored bytes while the flag is off, and
    the new table is never read."""
    _flag(monkeypatch, "")
    for kind in sig.SIGNATURE_KINDS:
        db = _Db("<p>Jane</p>", settings)
        assert await sig.resolve_signature(db, _ACC, kind) == "<p>Jane</p>"
        assert db.sql == [sig.STORED_SIGNATURE_SQL], db.sql
        db = _Db("<p>Jane</p>", settings)
        assert await sig.resolve_signature_for_send(db, _ACC, kind, "") == "<p>Jane</p>"
        assert db.sql == [sig.STORED_SIGNATURE_SQL]


def test_the_stored_read_is_the_sql_of_main() -> None:
    """The send, the forward and the draft paths ran this exact text on main."""
    assert sig.STORED_SIGNATURE_SQL == (
        "SELECT signature FROM email_assistant_settings WHERE account_id = :aid")


async def test_a_flag_for_another_org_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    other = "7d0c9a33-0000-4000-8000-000000000001"
    mine = "7d0c9a33-0000-4000-8000-000000000002"
    _flag(monkeypatch, other)
    assert sig.templates_on(mine) is False
    assert sig.templates_on(other) is True
    assert sig.templates_on(None) is False  # no bound tenant under a list
    _flag(monkeypatch, f" {other.upper()} , {mine} ")
    assert sig.templates_on(mine) is True
    _flag(monkeypatch, "*")
    assert sig.templates_on(mine) is True
    _flag(monkeypatch, "")
    assert sig.templates_on(mine) is False


# ── 2. The modes, with the flag on ──────────────────────────────────────────


async def test_a_template_row_renders_the_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    db = _Db("<p>Old</p>", _template())
    full = signature_render.render("classic", _FIELDS)[0]
    assert await sig.resolve_signature(db, _ACC, "new") == full
    assert await sig.resolve_signature(db, _ACC, "reply") == full
    forms = await sig.signature_forms(db, _ACC)
    assert forms.stored == "<p>Old</p>"


@pytest.mark.parametrize(("reply_mode", "want"), [
    ("full", "classic"), ("short", "compact"), ("none", None)])
async def test_reply_mode_applies_to_a_reply_and_a_forward(
    monkeypatch: pytest.MonkeyPatch, reply_mode: str, want: str | None,
) -> None:
    _flag(monkeypatch, "*")
    db = _Db("", _template(reply_mode))
    expected = signature_render.render(want, _FIELDS)[0] if want else ""
    for kind in ("reply", "forward"):
        assert await sig.resolve_signature(db, _ACC, kind) == expected
    assert await sig.resolve_signature(db, _ACC, "new") == (
        signature_render.render("classic", _FIELDS)[0])


async def test_reply_mode_none_on_a_custom_row_drops_the_reply_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    db = _Db("Jane", _template("none", mode="custom", layout=None))
    assert await sig.resolve_signature(db, _ACC, "reply") == ""
    assert await sig.resolve_signature(db, _ACC, "new") == "Jane"
    db = _Db("Jane", _template("short", mode="custom", layout=None))
    assert await sig.resolve_signature(db, _ACC, "reply") == "Jane"  # no fields


@pytest.mark.parametrize("row", [
    _template(mode="company"),          # SG-4 builds the company branch
    _template(layout="fancy"),          # a layout that does not render
    _template(fields="not json"),       # fields that do not parse
    _template(fields={"name": "  "}),   # no field to show
    _template(mode="custom", layout=None),
])
async def test_a_row_that_cannot_render_gives_the_stored_bytes(
    monkeypatch: pytest.MonkeyPatch, row: Any,
) -> None:
    _flag(monkeypatch, "*")
    db = _Db("<p>Jane</p>", row)
    for kind in sig.SIGNATURE_KINDS:
        assert await sig.resolve_signature(db, _ACC, kind) == "<p>Jane</p>"


async def test_an_unknown_kind_is_refused() -> None:
    for kind in ("draft", "", "NEW"):
        with pytest.raises(ValueError):
            await sig.resolve_signature(_Db("x"), _ACC, kind)
        with pytest.raises(ValueError):
            sig.SignatureForms(stored="x", full="x").of(kind)


# ── 3. One signature: a reply, drafted and sent ─────────────────────────────


async def _send(monkeypatch: pytest.MonkeyPatch, db: _Db, body: str,
                reply_to: str | None) -> dict[str, Any]:
    """One ``POST /email/send`` through the real route, with a fake session,
    a fake reply target and a fake provider. Answers the send kwargs."""
    sent: dict[str, Any] = {}

    class _Provider:
        async def send_message(self, **kw: Any) -> str:
            sent.update(kw)
            return "msg-1"

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield db

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=_Provider())

    async def _target(_db: Any, _aid: str, reply: str | None, _owner: str) -> Any:
        return ("pm-1", "thread-1") if reply else (None, None)

    monkeypatch.setattr(send_mod, "_tenant_session", _session)
    monkeypatch.setattr(send_mod, "provider_session", _provider_session)
    monkeypatch.setattr(send_mod, "_reply_target", _target)
    await send_mod.send_email(SendEmailRequest(
        account_id=_ACC, to=["kim@client.test"], subject="Re: Quote", body_text=body,
        reply_to_message_id=reply_to), BackgroundTasks(), user=_ME)
    return sent


async def _drafted(db: _Db, body: str) -> str:
    """The body that a draft path writes (``upsert_draft``, ``save_draft``)."""
    return append_signature_text(await drafting._account_signature(db, _ACC), body)


@pytest.mark.parametrize("reply_mode", sig.REPLY_MODES)
async def test_a_reply_drafted_and_sent_carries_one_signature(
    monkeypatch: pytest.MonkeyPatch, reply_mode: str,
) -> None:
    _flag(monkeypatch, "*")
    db = _Db("", _template(reply_mode))
    body = await _drafted(db, _REPLY)
    assert body.count("Jane Doe") == 1
    sent = await _send(monkeypatch, db, body, "local-1")
    assert sent["body_text"].count("Jane Doe") == 1, sent["body_text"]
    assert sent["body_html"].count("Jane Doe") == 1, sent["body_html"]
    assert sent["body_html"].count('role="presentation"') == 1
    # The signature sits under the new text and above the quote.
    text = sent["body_text"]
    assert text.index("The quote stands.") < text.index("Jane Doe") < text.index("Kim wrote:")


@pytest.mark.parametrize("stored", ["Jane", "<p>Jane <b>Doe</b></p>", "-- \nJane Doe"])
async def test_a_custom_reply_drafted_and_sent_carries_one_signature(
    monkeypatch: pytest.MonkeyPatch, stored: str,
) -> None:
    _flag(monkeypatch, "")
    db = _Db(stored)
    body = await _drafted(db, _REPLY)
    sent = await _send(monkeypatch, db, body, "local-1")
    sig_text = signature_text(stored)
    assert sent["body_text"].count(sig_text) == 1
    assert (sent["body_text"], sent["body_html"]) == build_signed_bodies(stored, body, None)


@pytest.mark.parametrize("reply_mode", sig.REPLY_MODES)
@pytest.mark.parametrize("reply_to", [None, "local-1"])
async def test_a_mode_change_between_draft_and_send_gives_one_signature(
    monkeypatch: pytest.MonkeyPatch, reply_mode: str, reply_to: str | None,
) -> None:
    """§23.9. A draft written while the mailbox was ``custom`` holds the stored
    signature. The mode then changes to ``template`` before the send. The send
    keeps the signature of the draft, and adds no second one."""
    _flag(monkeypatch, "*")
    db = _Db("<p>Ravi Kumar</p><p>Acme</p>")
    body = await _drafted(db, _REPLY)
    assert body.count("Ravi Kumar") == 1
    db.settings = _template(reply_mode)
    sent = await _send(monkeypatch, db, body, reply_to)
    for part in (sent["body_text"], sent["body_html"]):
        assert part.count("Ravi Kumar") == 1, part
        assert "Jane Doe" not in part, part


@pytest.mark.parametrize("stored", _STORED)
@pytest.mark.parametrize("reply_to", [None, "local-1"])
@pytest.mark.parametrize("signed", [False, True])
async def test_rule_7_the_send_gives_the_bytes_of_main(
    monkeypatch: pytest.MonkeyPatch, stored: str | None, reply_to: str | None, signed: bool,
) -> None:
    """With no row and the flag off, ``POST /email/send`` sends exactly what
    ``build_signed_bodies`` of the stored value gives, as on main."""
    _flag(monkeypatch, "")
    body = append_signature_text(_main_bytes(stored), _REPLY) if signed else _REPLY
    db = _Db(stored)
    sent = await _send(monkeypatch, db, body, reply_to)
    assert (sent["body_text"], sent["body_html"]) == build_signed_bodies(
        _main_bytes(stored), body, None)
    assert db.sql == [sig.STORED_SIGNATURE_SQL]


@pytest.mark.parametrize("stored", _STORED)
async def test_rule_7_each_draft_reader_gets_the_bytes_of_main(
    monkeypatch: pytest.MonkeyPatch, stored: str | None,
) -> None:
    _flag(monkeypatch, "")

    async def _no_profile(*_a: Any, **_k: Any) -> str:
        return ""

    from gateway.routes.email.automation import voice_profile

    monkeypatch.setattr(voice_profile, "load_voice_profile_block", _no_profile)
    assert await drafting._account_signature(_Db(stored), _ACC) == _main_bytes(stored)
    _, got = await assistant_mod._load_assistant_about(_Db(stored), _ACC)
    assert got == _main_bytes(stored)


async def test_the_reply_card_reads_the_reply_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply card asks for ``reply``. Rule 7: with the flag off it holds
    the stored bytes. With reply_mode none it holds none, and says nothing."""
    row = SimpleNamespace(
        id="m-1", account_id=_ACC, provider_message_id="p1", thread_id="t1",
        subject="Quote", from_address={"email": "kim@client.test"}, to_addresses=[],
        cc_addresses=[], received_at=None, snippet="", body_text="", folder="inbox",
        provider="gmail", internet_message_id=None)
    me = SimpleNamespace(address="jane@sg2.test", label="Work",
                         self_addresses=frozenset({"jane@sg2.test"}))
    holder: dict[str, _Db] = {}

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield holder["db"]

    async def _row(*_a: Any) -> Any:
        return row

    async def _self(*_a: Any) -> Any:
        return me

    async def _reply_to(*_a: Any) -> list[str]:
        return []

    monkeypatch.setattr(rc, "_tenant_session", _session)
    monkeypatch.setattr(rc, "_reply_row", _row)
    monkeypatch.setattr(rc, "resolve_self", _self)
    monkeypatch.setattr(rc, "_read_reply_to", _reply_to)
    _flag(monkeypatch, "")
    holder["db"] = _Db("<p>Jane</p>", _template("none"))
    mail = await rc._load_mail("m-1", "jane@sg2.test")
    assert mail.signature == "<p>Jane</p>"
    assert any("signature" in n for n in mail.notices())
    _flag(monkeypatch, "*")
    holder["db"] = _Db("<p>Jane</p>", _template("none"))
    mail = await rc._load_mail("m-1", "jane@sg2.test")
    assert mail.signature == ""
    assert not any("signature" in n for n in mail.notices())


# ── 4. The source scan ──────────────────────────────────────────────────────


def _literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Each string literal, and each ``+`` of string literals, with its line."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts: list[str] = []
            stack: list[ast.AST] = [node]
            while stack:
                cur = stack.pop()
                if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
                    stack += [cur.right, cur.left]
                elif isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                    parts.append(cur.value)
                else:
                    parts = []
                    break
            if parts:
                out.append((node.lineno, "".join(parts)))
    return out


_SELECT = re.compile(r"\bSELECT\b", re.IGNORECASE)
_TABLE = re.compile(r"\bemail_assistant_settings\b", re.IGNORECASE)
#: ``signature`` as a column: bare or with an alias, never ``signature_text``.
_COLUMN = re.compile(r"(?<![\w])(?:\w+\.)?signature\b(?!_)", re.IGNORECASE)
_STAR = re.compile(r"\bSELECT\s+(?:\w+\.)?\*", re.IGNORECASE)


def selects_the_signature(sql: str) -> bool:
    """True when ``sql`` selects ``signature`` from ``email_assistant_settings``,
    as a column or through ``*``."""
    flat = " ".join(sql.split())
    if not (_TABLE.search(flat) and _SELECT.search(flat)):
        return False
    return bool(_COLUMN.search(flat) or _STAR.search(flat))


def raw_signature_reads(source: str) -> list[int]:
    return sorted({line for line, s in _literals(ast.parse(source)) if selects_the_signature(s)})


def _product_files() -> list[Path]:
    roots = (_ROOT / "apps", _ROOT / "packages", _ROOT / "scripts")
    return sorted(p for root in roots for p in root.rglob("*.py")
                  if "node_modules" not in p.parts and ".venv" not in p.parts)


def test_no_code_outside_signature_py_selects_the_signature() -> None:
    found = {}
    for path in _product_files():
        if path == _SIGNATURE_PY:
            continue
        lines = raw_signature_reads(path.read_text(encoding="utf-8-sig"))
        if lines:
            found[path.relative_to(_ROOT).as_posix()] = lines
    assert not found, (
        "A reader selects email_assistant_settings.signature outside "
        f"routes/email/signature.py: {found}. Call resolve_signature (D-EM-66).")


def test_signature_py_holds_the_one_read() -> None:
    assert raw_signature_reads(_SIGNATURE_PY.read_text(encoding="utf-8")) != []


@pytest.mark.parametrize("source", [
    'X = "SELECT signature FROM email_assistant_settings WHERE account_id = :aid"\n',
    'X = ("SELECT about, signature, auto_run "\n     "FROM email_assistant_settings")\n',
    'X = "(SELECT s.signature FROM email_assistant_settings s WHERE s.account_id = 1)"\n',
    'X = "SELECT * FROM email_assistant_settings"\n',
    'X = "SELECT signature " + "FROM email_assistant_settings"\n',
    'X = """SELECT eas.signature\n  FROM email_accounts ea\n'
    '  JOIN email_assistant_settings eas ON eas.account_id = ea.id"""\n',
])
def test_the_scan_sees_a_raw_read(source: str) -> None:
    assert raw_signature_reads(source) == [1]


@pytest.mark.parametrize("source", [
    'X = "SELECT about, auto_run FROM email_assistant_settings"\n',
    'X = "INSERT INTO email_assistant_settings (account_id, signature) VALUES (1, 2)"\n',
    'X = "SELECT signature_text FROM email_assistant_settings"\n',
    'X = "SELECT signature FROM email_signature_settings"\n',
])
def test_the_scan_passes_a_read_of_another_column(source: str) -> None:
    assert raw_signature_reads(source) == []


#: Each reader of §23.3.3: its file, its function, the resolver call it makes,
#: and the kinds it passes. The draft paths pass ``new`` until SG-5.
READERS: dict[tuple[str, str], tuple[str, frozenset[str]]] = {
    ("routes/email/transport/send.py", "send_email"):
        ("resolve_signature_for_send", frozenset({"reply", "new"})),
    ("routes/email/transport/forward.py", "forward_email"):
        ("resolve_signature_for_send", frozenset({"forward"})),
    ("routes/notes/dispatch.py", "_dispatch_email"):
        ("resolve_signature", frozenset({"new"})),
    ("routes/email/automation/drafting.py", "_account_signature"):
        ("resolve_signature", frozenset({"new"})),
    ("routes/email/automation/drafting.py", "send_draft_endpoint"):
        ("resolve_signature", frozenset({"new"})),
    ("routes/email/automation/assistant.py", "_load_assistant_about"):
        ("resolve_signature", frozenset({"new"})),
    ("routes/email/automation/assistant.py", "get_assistant_settings"):
        ("signature_forms", frozenset({"new"})),
    ("routes/email/automation/assistant.py", "put_assistant_settings"):
        ("signature_forms", frozenset({"new"})),
    ("routes/email/automation/reply_card.py", "_load_mail"):
        ("resolve_signature", frozenset({"reply"})),
    ("routes/email/automation/held_card.py", "seal_of"):
        ("_account_signature", frozenset()),
}


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _calls(fn: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)]


def _name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _kinds(fn: ast.AST, resolver: str) -> set[str]:
    """The kinds that ``fn`` passes to the resolver, or to ``.of(...)``."""
    out: set[str] = set()
    for call in _calls(fn):
        if _name(call) in (resolver, "of"):
            for arg in call.args:
                out |= {n.value for n in ast.walk(arg) if isinstance(n, ast.Constant)
                        and n.value in sig.SIGNATURE_KINDS}
    return out


@pytest.mark.parametrize("reader", list(READERS), ids=lambda r: f"{r[0]}::{r[1]}")
def test_each_reader_reaches_the_resolver_with_its_kind(reader: tuple[str, str]) -> None:
    rel, name = reader
    resolver, kinds = READERS[reader]
    fn = _functions(_GATEWAY / rel)[name]
    assert resolver in {_name(c) for c in _calls(fn)}, (rel, name, resolver)
    assert _kinds(fn, resolver) == kinds, (rel, name)


def test_the_two_wrappers_call_the_resolver() -> None:
    """``_account_signature`` and ``_load_assistant_about`` feed each draft
    path, the follow-ups, the rule actions and the compose step."""
    drafting_fns = _functions(_EMAIL / "automation/drafting.py")
    assistant_fns = _functions(_EMAIL / "automation/assistant.py")
    assert "resolve_signature" in {_name(c) for c in _calls(drafting_fns["_account_signature"])}
    assert "resolve_signature" in {
        _name(c) for c in _calls(assistant_fns["_load_assistant_about"])}


#: Each function that calls ``strip_signature_text``, and how its signature
#: reaches it: a call of a resolver-backed function, or a parameter whose
#: callers are such functions. A new caller fails until it is added here.
STRIP_CALLERS: dict[str, str] = {
    "_cleanup_thread_drafts": "calls _account_signature",
    "_learn_from_sent": "calls _account_signature",
    "_compose_assist_run": "step.signature, from _ComposeStep.block_1",
    "_compose_draft": "a parameter of _compose_assist_run",
    "_fold_draft": "a parameter of four callers",
}
#: The names that give a resolved signature.
_SOURCES = frozenset({"resolve_signature", "resolve_signature_for_send", "signature_forms"})


def _scan_defs() -> list[tuple[str, ast.AST]]:
    out: list[tuple[str, ast.AST]] = []
    for path in [*sorted(_EMAIL.rglob("*.py")), *sorted((_GATEWAY / "routes/notes").rglob("*.py"))]:
        if path == _SIGNATURE_PY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        out += [(n.name, n) for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return out


def _sourced_names(defs: list[tuple[str, ast.AST]]) -> set[str]:
    """Each top-level def that calls a source, to a fixed point."""
    names = set(_SOURCES)
    while True:
        more = {name for name, node in defs
                if name not in names and {_name(c) for c in _calls(node)} & names}
        if not more:
            return names
        names |= more


def _is_sourced(name: str, defs: list[tuple[str, ast.AST]], sourced: set[str],
                seen: frozenset[str] = frozenset()) -> bool:
    if name in sourced:
        return True
    callers = [n for n, node in defs if n != name and name in {_name(c) for c in _calls(node)}]
    if not callers or name in seen:
        return False
    return all(_is_sourced(c, defs, sourced, seen | {name}) for c in callers)


def test_each_strip_caller_takes_the_resolved_signature() -> None:
    defs = _scan_defs()
    callers = {name for name, node in defs
               if "strip_signature_text" in {_name(c) for c in _calls(node)}}
    assert callers == set(STRIP_CALLERS), (
        "The callers of strip_signature_text changed. The draft write and the "
        "send must strip the form that the resolver gives (§23.3.3). Add the "
        f"new caller to STRIP_CALLERS with its source: {sorted(callers)}")
    sourced = _sourced_names(defs)
    assert "_ComposeStep" in sourced and "_account_signature" in sourced
    for name in STRIP_CALLERS:
        assert _is_sourced(name, defs, sourced), name


def test_the_strip_scan_can_go_red() -> None:
    defs = [(n.name, n) for n in ast.parse(
        "def reader(db):\n    return strip_signature_text(raw(db), 'x')\n"
        "def raw(db):\n    return db.execute('x')\n").body]
    assert _is_sourced("reader", defs, _sourced_names(defs)) is False


_FLAG = "email_signature_templates_orgs"
#: The settings field declares the flag, and the env guard names it. Neither reads it.
_FLAG_DECLARED = (
    _ROOT / "packages/acb_common/acb_common/settings.py",
    _ROOT / "packages/acb_common/acb_common/env_guard.py",
)


def flag_reads(source: str) -> list[int]:
    """The lines that name the flag in code: a string, an attribute or a name.
    A docstring only describes the flag, so it is not a read."""
    tree = ast.parse(source)
    docs: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            docs.add(id(body[0].value))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docs and _FLAG in node.value.lower():
                lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr.lower() == _FLAG:
            lines.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id.lower() == _FLAG:
            lines.add(node.lineno)
    return sorted(lines)


def test_templates_on_is_the_one_reader_of_the_flag() -> None:
    """§23.5 (R7). A second reader could disagree with ``templates_on`` on the
    parse, the ``*`` or the bound tenant, and switch one reader on alone."""
    found = {}
    for path in _product_files():
        if path == _SIGNATURE_PY or path in _FLAG_DECLARED:
            continue
        lines = flag_reads(path.read_text(encoding="utf-8-sig"))
        if lines:
            found[path.relative_to(_ROOT).as_posix()] = lines
    assert not found, (
        f"Code outside signature.templates_on reads {_FLAG.upper()}: {found}. "
        "Call templates_on().")
    source = _SIGNATURE_PY.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.FunctionDef) and n.name == "templates_on")
    reads = flag_reads(source)
    assert reads, "templates_on no longer reads the flag"
    assert all(fn.lineno <= line <= (fn.end_lineno or fn.lineno) for line in reads), reads


@pytest.mark.parametrize(("source", "want"), [
    ("X = get_settings().email_signature_templates_orgs\n", [1]),
    ("import os\nX = os.environ['EMAIL_SIGNATURE_TEMPLATES_ORGS']\n", [2]),
    ("X = getattr(s, 'email_signature_templates_orgs', '')\n", [1]),
    ('"""EMAIL_SIGNATURE_TEMPLATES_ORGS gates the layouts."""\n', []),
    ('def f():\n    """Reads EMAIL_SIGNATURE_TEMPLATES_ORGS."""\n    return 1\n', []),
])
def test_the_flag_scan_can_go_red(source: str, want: list[int]) -> None:
    assert flag_reads(source) == want


# ── 5. The preview route ────────────────────────────────────────────────────


async def test_the_preview_is_dark_while_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "")
    req = signature_preview.SignaturePreviewRequest(layout="classic", fields=_FIELDS)
    with pytest.raises(HTTPException) as caught:
        await signature_preview.preview_signature(req, user=_ME)
    assert caught.value.status_code == 404


async def test_the_preview_renders_with_the_renderer_of_the_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    for layout in signature_render.LAYOUTS:
        req = signature_preview.SignaturePreviewRequest(layout=layout, fields=_FIELDS)
        out = await signature_preview.preview_signature(req, user=_ME)
        html, text = signature_render.render(layout, _FIELDS)
        assert out == {"layout": layout, "html": html, "text": text}


def test_the_preview_refuses_a_bad_body() -> None:
    with pytest.raises(ValidationError):
        signature_preview.SignaturePreviewRequest(layout="fancy", fields={})
    with pytest.raises(ValidationError):
        signature_preview.SignaturePreviewRequest(layout="classic", fields={}, account_id="x")
    with pytest.raises(ValidationError):
        signature_preview.SignaturePreviewRequest(
            layout="classic", fields={"legal": "x" * (signature_preview.MAX_FIELDS_BYTES + 1)})


def test_the_preview_is_a_gated_member_route_that_writes_nothing() -> None:
    from gateway.main import PUBLIC_ROUTES
    from gateway.routes.email.core import router

    [route] = [r for r in router.routes
               if getattr(r, "path", "") == "/email/signature/preview"]
    assert route.methods == {"POST"}
    assert "/email/signature/preview" not in PUBLIC_ROUTES
    source = Path(signature_preview.__file__).read_text(encoding="utf-8")
    for word in ("execute(", "_tenant_session", "get_db", "INSERT", "UPDATE"):
        assert word not in source, word
