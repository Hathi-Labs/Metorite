"""HRD-17e-1 (task 3646): no shrink before the seal. The parts with no database.

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-17e-1.
``tests/unit/test_chat_upsert_no_shrink_r8.py`` proves the SQL on a real
Postgres (R8). This file proves the rest:

* the two flags are empty by default, and the one reader reads each org;
* the flag-off text of the upsert is the text of before, byte for byte;
* each variant is the base text with one line changed, and no other change;
* only a client write of an agent row of a listed org gets a variant;
* with the flags off, a save runs the base text once for each row, and no
  other statement;
* the log line names lengths and never the text.

**The mutations (R7).** Copy the file, change it, run, and restore it with
``cp`` (``cmp`` then says the file is the same again).

* In ``chat_no_shrink._listed`` return ``bool(entries)`` (drop the per-org
  check): ``test_the_reader_reads_each_org`` goes red.
* In ``routes/chat.py::_no_shrink_sql`` drop ``or author_from_run``:
  ``test_only_a_client_write_of_an_agent_row_gets_a_variant`` goes red.
* In ``_no_shrink_sql`` drop ``kind != "agent"``: the same test goes red.
* Give ``_no_shrink_sql`` the variant with the flag off (drop
  ``mode == OFF or``): ``test_flag_off_runs_the_base_text_and_no_other_statement``
  goes red.
* Add ``AND char_length(chat_message.content) >= 0`` to the ``WHERE`` of
  ``_MESSAGE_UPSERT_SQL``: ``test_the_flag_off_text_is_pinned`` goes red.
* In ``_upsert_messages`` put the content into the ``message_id`` of the
  ``_note_shrink`` call (log the text):
  ``test_a_save_logs_one_line_with_no_text`` goes red.
* Add a field to the log call in ``chat_no_shrink.log_shrink``:
  ``test_the_log_line_names_lengths_and_no_text`` goes red.
* In ``_no_shrink_sql`` drop ``or server_write``:
  ``test_only_a_client_write_of_an_agent_row_gets_a_variant`` goes red.
* In ``chat_brief._post_row`` drop ``server_write=True``:
  ``test_only_the_server_fill_passes_server_write`` goes red.
* In ``_note_shrink`` use ``contextlib.nullcontext()`` in place of
  ``contextlib.suppress(Exception)``: ``test_a_fault_of_the_log_never_fails_the_save``
  goes red.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_common import get_settings
from acb_common.settings import Settings
from gateway import chat_no_shrink as ns
from gateway.routes import chat

#: sha256 of ``_MESSAGE_UPSERT_SQL`` on ``origin/main`` before HRD-17e-1
#: (f7d109bc4). The flag-off write must run this text, byte for byte.
PINNED_UPSERT_SHA256 = "93dd9a0eea8d179de9ccc9ca352ef2d764df90463d1728dc50a0eba9d9a64a1c"

ORG_A = "org-a-3646"
ORG_B = "org-b-3646"
SECRET = "Quarterly figures for Acme: 41,200"


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _flags(monkeypatch: pytest.MonkeyPatch, *, enforce: str = "", shadow: str = "") -> None:
    monkeypatch.setattr(get_settings(), "chat_no_shrink_orgs", enforce)
    monkeypatch.setattr(get_settings(), "chat_no_shrink_shadow_orgs", shadow)


# ── 1. The flags ─────────────────────────────────────────────────────────────


def test_both_flags_are_empty_by_default(monkeypatch) -> None:
    assert Settings.model_fields["chat_no_shrink_orgs"].default == ""
    assert Settings.model_fields["chat_no_shrink_shadow_orgs"].default == ""
    _flags(monkeypatch)
    assert ns.no_shrink_mode(ORG_A) == ns.OFF


def test_the_reader_reads_each_org(monkeypatch) -> None:
    _flags(monkeypatch, shadow=f" {ORG_A.upper()} ,x")
    assert ns.no_shrink_mode(ORG_A) == ns.SHADOW
    assert ns.no_shrink_mode(ORG_B) == ns.OFF
    _flags(monkeypatch, enforce=ORG_A)
    assert ns.no_shrink_mode(ORG_A) == ns.ON
    assert ns.no_shrink_mode(ORG_B) == ns.OFF
    # An org in both lists enforces.
    _flags(monkeypatch, enforce=ORG_A, shadow=f"{ORG_A},{ORG_B}")
    assert (ns.no_shrink_mode(ORG_A), ns.no_shrink_mode(ORG_B)) == (ns.ON, ns.SHADOW)
    _flags(monkeypatch, shadow="*")
    assert ns.no_shrink_mode(ORG_B) == ns.SHADOW
    _flags(monkeypatch, enforce="*")
    assert ns.no_shrink_mode(ORG_B) == ns.ON


def test_both_flags_reload_and_only_the_operator_writes_them() -> None:
    from acb_common import env_guard
    from gateway.flag_reload import RELOADABLE

    for name in ("CHAT_NO_SHRINK_ORGS", "CHAT_NO_SHRINK_SHADOW_ORGS"):
        assert name in RELOADABLE
        assert env_guard.is_platform_env(name)
        assert name in env_guard.PLATFORM_ENV_NAMES


def test_no_org_reads_off_even_for_star(monkeypatch) -> None:
    _flags(monkeypatch, enforce="*", shadow="*")
    assert ns.no_shrink_mode(None) == ns.OFF
    assert ns.no_shrink_mode("  ") == ns.OFF


def test_a_settings_fault_reads_off(monkeypatch) -> None:
    import acb_common

    def _broken():
        raise RuntimeError("no settings")

    monkeypatch.setattr(acb_common, "get_settings", _broken)
    assert ns.no_shrink_mode(ORG_A) == ns.OFF


# ── 2. The text of the upsert ────────────────────────────────────────────────


def test_the_flag_off_text_is_pinned() -> None:
    got = hashlib.sha256(chat._MESSAGE_UPSERT_SQL.encode("utf-8")).hexdigest()
    assert got == PINNED_UPSERT_SHA256, "the flag-off upsert must stay the text of before"
    assert "char_length" not in chat._MESSAGE_UPSERT_SQL
    assert "prior" not in chat._MESSAGE_UPSERT_SQL


def test_the_shadow_text_is_the_base_text_with_a_read() -> None:
    """Take out the CTE and the new RETURNING columns, and the base text is
    left, byte for byte. So the shadow write is the write of before."""
    shadow = chat._NO_SHRINK_SHADOW_SQL
    head, sep, rest = shadow.partition("\n    INSERT INTO chat_message")
    assert sep and head.lstrip().startswith("WITH prior AS (")
    body = (sep + rest).replace(
        "    RETURNING id, (xmax = 0) AS inserted,\n"
        "        (SELECT old_chars FROM prior) AS old_chars,\n"
        "        char_length(CAST(:content AS text)) AS new_chars,\n"
        "        (SELECT prefix FROM prior) AS prefix\n",
        "    RETURNING id, (xmax = 0) AS inserted\n",
    )
    assert body == chat._MESSAGE_UPSERT_SQL
    assert "content        = EXCLUDED.content," in shadow


def test_the_enforce_text_changes_the_content_line_only() -> None:
    enforce = chat._NO_SHRINK_ENFORCE_SQL
    back = enforce.replace(
        "        content        = CASE\n"
        "            WHEN char_length(EXCLUDED.content) < char_length(chat_message.content)\n"
        "            THEN chat_message.content ELSE EXCLUDED.content END,\n",
        "        content        = EXCLUDED.content,\n",
    )
    head, sep, _ = back.partition("    RETURNING id, (xmax = 0) AS inserted,\n")
    assert sep
    assert head + "    RETURNING id, (xmax = 0) AS inserted\n" == chat._MESSAGE_UPSERT_SQL
    # Characters, not bytes.
    assert "octet_length" not in enforce and "char_length(EXCLUDED.content)" in enforce


def test_the_replace_refuses_a_line_that_is_not_there_once() -> None:
    with pytest.raises(RuntimeError):
        chat._replace_once("a\nb\n", "c\n", "d\n")
    with pytest.raises(RuntimeError):
        chat._replace_once("a\na\n", "a\n", "d\n")


# ── 3. Which write gets a variant ────────────────────────────────────────────


@pytest.mark.parametrize("mode", [ns.OFF, ns.SHADOW, ns.ON])
def test_only_a_client_write_of_an_agent_row_gets_a_variant(mode: str) -> None:
    base = chat._MESSAGE_UPSERT_SQL
    want = {ns.OFF: base, ns.SHADOW: chat._NO_SHRINK_SHADOW_SQL,
            ns.ON: chat._NO_SHRINK_ENFORCE_SQL}[mode]
    assert chat._no_shrink_sql(mode, kind="agent", author_from_run=False, mint=False) is want
    # The fold, the mint, a human row and a system row: the base, always.
    for kind, fold, mint in (("agent", True, False), ("agent", False, True),
                             ("human", False, False), ("system", False, False),
                             (None, False, False), ("human", True, False)):
        got = chat._no_shrink_sql(mode, kind=kind, author_from_run=fold, mint=mint)
        assert got is base, (mode, kind, fold, mint)
    # The server fill of ``chat_brief._post_row`` (fix round 1, F1): the base.
    got = chat._no_shrink_sql(mode, kind="agent", author_from_run=False, mint=False,
                              server_write=True)
    assert got is base, mode


def test_only_the_server_fill_passes_server_write() -> None:
    """``server_write`` is for ``chat_brief._post_row`` ONLY. The message
    route of the browser never names it, and no other gateway file passes it.
    A client that could pass it would bypass the guard."""
    import inspect
    from pathlib import Path

    from gateway.routes import chat_brief

    assert "server_write=True" in inspect.getsource(chat_brief._post_row)
    assert "server_write" not in inspect.getsource(chat.save_messages)
    root = Path(chat.__file__).resolve().parents[1]
    users = sorted(
        str(f.relative_to(root)).replace("\\", "/")
        for f in root.rglob("*.py")
        if "server_write=True" in f.read_text(encoding="utf-8")
    )
    assert users == ["routes/chat_brief.py"], users
    param = inspect.signature(chat._upsert_messages).parameters["server_write"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is False


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    """Records each statement. Each upsert answers *row*."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.statements: list[str] = []

    def execute(self, clause: Any, params: Any = None) -> _FakeResult:
        self.statements.append(getattr(clause, "text", str(clause)))
        return _FakeResult(self.row)


def _hermetic(monkeypatch: pytest.MonkeyPatch, row: Any) -> _FakeSession:
    import acb_graph

    sess = _FakeSession(row)

    @contextmanager
    def _tenant_session(org: Any = None):
        yield sess

    monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
    monkeypatch.setattr(chat, "_refuse_if_elsewhere", lambda s, sid: True)
    return sess


def _agent(mid: str, content: str) -> chat.MessageRecord:
    return chat.MessageRecord(id=mid, role="assistant", content=content,
                              timestamp=1_800_000_000_000, author_kind="agent")


def test_flag_off_runs_the_base_text_and_no_other_statement(monkeypatch) -> None:
    _flags(monkeypatch)
    sess = _hermetic(monkeypatch, SimpleNamespace(id="a1", inserted=False))
    with structlog.testing.capture_logs() as caps:
        declined = chat._upsert_messages(
            "sid-3646", [_agent("a1", "short"), _agent("a2", "")],
            actor_email="m@x.test", agent_name="orchestrator", organization_id=ORG_A)
    assert declined == []
    assert sess.statements == [chat._MESSAGE_UPSERT_SQL, chat._MESSAGE_UPSERT_SQL]
    assert not [c for c in caps if str(c.get("event", "")).startswith("chat_no_shrink")]


def test_another_org_keeps_the_base_text(monkeypatch) -> None:
    _flags(monkeypatch, enforce=ORG_A, shadow=ORG_A)
    sess = _hermetic(monkeypatch, SimpleNamespace(id="a1", inserted=False))
    chat._upsert_messages("sid-3646", [_agent("a1", "x")], actor_email="m@x.test",
                          organization_id=ORG_B)
    assert sess.statements == [chat._MESSAGE_UPSERT_SQL]


# ── 4. The log line ──────────────────────────────────────────────────────────


def _no_shrink_lines(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in caps if str(c.get("event", "")).startswith("chat_no_shrink")]


def test_the_log_line_names_lengths_and_no_text() -> None:
    with structlog.testing.capture_logs() as caps:
        ns.log_shrink(ns.SHADOW_EVENT, session_id="s" * 40, message_id="m" * 60,
                      organization_id=ORG_A, old_chars=34, new_chars=5, prefix=True)
    (line,) = _no_shrink_lines(caps)
    assert set(line) == {"event", "log_level", "thread_id", "message_id", "org",
                         "old_chars", "new_chars", "prefix"}
    assert (line["event"], line["thread_id"], line["message_id"]) == (
        "chat_no_shrink_shadow", "s" * 12, "m" * 40)
    # A warning, so a box with LOG_LEVEL above INFO still counts it.
    assert line["log_level"] == "warning"
    assert (line["org"], line["old_chars"], line["new_chars"], line["prefix"]) == (
        ORG_A, 34, 5, True)
    with pytest.raises(ValueError):
        ns.log_shrink("chat_no_shrink_other", session_id="s", message_id="m",
                      organization_id=ORG_A, old_chars=1, new_chars=0, prefix=True)


def test_a_save_logs_one_line_with_no_text(monkeypatch) -> None:
    """The save path itself: a shorter client write of a shadow org logs
    ONE line, and the text of neither write is in it."""
    _flags(monkeypatch, shadow=ORG_A)
    row = SimpleNamespace(id="a1", inserted=False, old_chars=len(SECRET),
                          new_chars=len(SECRET[:9]), prefix=True)
    sess = _hermetic(monkeypatch, row)
    with structlog.testing.capture_logs() as caps:
        chat._upsert_messages("sid-3646", [_agent("a1", SECRET[:9])],
                              actor_email="m@x.test", organization_id=ORG_A)
    assert sess.statements == [chat._NO_SHRINK_SHADOW_SQL]
    (line,) = _no_shrink_lines(caps)
    assert (line["old_chars"], line["new_chars"], line["prefix"]) == (len(SECRET), 9, True)
    dump = repr(caps)
    assert SECRET not in dump and SECRET[:9] not in dump
    assert not re.search(r"Acme|41,200|Quarterly", dump)


def test_the_enforce_line_comes_only_when_the_text_was_kept(monkeypatch) -> None:
    _flags(monkeypatch, enforce=ORG_A)
    kept = SimpleNamespace(id="a1", inserted=False, kept=True, old_chars=20,
                           new_chars=3, prefix=False)
    _hermetic(monkeypatch, kept)
    with structlog.testing.capture_logs() as caps:
        assert chat._upsert_messages("sid-3646", [_agent("a1", "abc")],
                                     actor_email="m@x.test", organization_id=ORG_A) == []
    (line,) = _no_shrink_lines(caps)
    assert (line["event"], line["log_level"]) == ("chat_no_shrink_refused", "warning")
    passed = SimpleNamespace(id="a1", inserted=False, kept=False, old_chars=3,
                             new_chars=3, prefix=True)
    _hermetic(monkeypatch, passed)
    with structlog.testing.capture_logs() as caps:
        chat._upsert_messages("sid-3646", [_agent("a1", "abc")],
                              actor_email="m@x.test", organization_id=ORG_A)
    assert _no_shrink_lines(caps) == []


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_a_fault_of_the_log_never_fails_the_save(monkeypatch, mode: str) -> None:
    """Fix round 1, F2. The line is written inside the tenant session of the
    save. A fault of the log must give the save of a working log."""
    _flags(monkeypatch, **{mode: ORG_A})
    row = SimpleNamespace(id="a1", inserted=False, kept=True, old_chars=30,
                          new_chars=3, prefix=True)
    sess = _hermetic(monkeypatch, row)

    def _broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the log is down")

    monkeypatch.setattr(ns, "log_shrink", _broken)
    assert chat._upsert_messages("sid-3646", [_agent("a1", "abc")],
                                 actor_email="m@x.test", organization_id=ORG_A) == []
    assert len(sess.statements) == 1


def test_the_new_module_never_names_the_fold_keyword() -> None:
    """``test_rooms.py`` lets only ``chat_fold.py`` and ``routes/chat.py``
    name the keyword of the fold. The new module must not."""
    from pathlib import Path

    src = Path(ns.__file__).read_text(encoding="utf-8")
    assert "author" + "_from_run" not in src
