"""EM-T2c — the owner-scope fence (D-EM-4: a mailbox is private to its member).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.5, subsection
EM-T2c, items 3 to 7.

Two lists, two scans, both AST-parsed and never grepped:

1. **Every ``@router`` handler in ``gateway/routes/email/``** shows an owner
   proof in its own body, or has an entry in :data:`OWNER_SCOPE_EXEMPT` with a
   reason. An owner proof is a SQL string literal with a predicate on
   ``user_id`` (``user_id = :uid``), or a call to ``_account_scope``,
   ``_assert_account_owner`` or ``provider_session``.
2. **Every module outside ``routes/email``** that names an email child table in
   a SQL string literal has an entry in :data:`OUTSIDE_EMAIL_READERS` with a
   reason. The child tables come from ``infra/postgres/``: each ``email_*``
   table except ``email_accounts``.

An entry that no longer matches anything is stale and fails. An entry with no
reason fails. Synthetic sources prove that each scan can still go red.

⚠️ **Limit (stated, R7).** The fence reads one function at a time. It does not
follow data, and it does not read helpers that a handler calls. A handler that
reads ``email_accounts WHERE user_id = :uid`` and then uses a body-supplied id
anyway passes it. ``test_email_chat_context_owner.py`` (R8) covers the one leak
of that shape that was measured on 2026-10-02.

Run::

    uv run pytest tests/unit/test_email_owner_scope_fence.py -q
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_EMAIL = _ROOT / "apps/services/gateway/gateway/routes/email"
_MIGRATIONS = _ROOT / "infra/postgres"
#: Where a module outside ``routes/email`` can read an email table.
_OUTSIDE_ROOTS = (_ROOT / "apps", _ROOT / "packages")

#: The three owner helpers of ``routes/email/core.py``.
OWNER_HELPERS = frozenset({
    "_account_scope", "_assert_account_owner", "provider_session",
})
#: A SQL owner predicate on ``user_id``: ``user_id = :uid``, ``ea.user_id =
#: :user_id``, ``LOWER(user_id) = LOWER(:uid)``.
_OWNER_PREDICATE = re.compile(
    r"\buser_id\s*\)?\s*=\s*(?:LOWER\(\s*)?:\w+", re.IGNORECASE)

# ── List 1: the handlers that carry no owner proof of their own ─────────────

#: Each handler in ``routes/email`` with no owner proof in its own body, and
#: why that is safe. The first 13 entries are the measurement of 2026-10-02.
#: EM-T3d added the fourteenth. A new entry needs a reason that a reviewer can
#: check against the code.
OWNER_SCOPE_EXEMPT: dict[str, str] = {
    "ai_chat": (
        "Delegates to _build_chat_context, which keeps account_id only when "
        "email_accounts WHERE user_id = :uid lists it, else the one mailbox "
        "of the member, else None. _account_models reads that resolved id. "
        "R8: test_email_chat_context_owner.py."
    ),
    "quick_action": (
        "Reads no table. It calls the email-assistant agent tools as the "
        "member (_set_memory_user_id), and each tool reaches a gateway route "
        "that makes its own owner check."
    ),
    "cleanup_status": (
        "Reads the in-process _SWEEP_JOBS registry, and answers idle unless "
        "the job's owner is the caller. No table read."
    ),
    "compose_assist": (
        "A thin wrapper. _compose_assist_run calls _assert_account_owner "
        "before it reads the account."
    ),
    "compose_assist_stream": (
        "A thin wrapper. _compose_assist_run calls _assert_account_owner "
        "before it reads the account."
    ),
    "process_past_status": (
        "Reads the in-process _PAST_JOBS registry, and answers idle unless "
        "the job's owner is the caller. No table read."
    ),
    "voice_profile_status": (
        "Reads the in-process _VOICE_JOBS registry, and answers idle unless "
        "the job's owner is the caller. No table read."
    ),
    "image_proxy": (
        "Fetches a remote image URL for an authenticated caller, behind the "
        "SSRF checks. It reads no mailbox and no table."
    ),
    "oauth_authorize": (
        "Starts the connect flow. It signs a state that names the session's "
        "own member and organization, and reads no mailbox."
    ),
    "oauth_app_info": (
        "Returns the client ID and redirect URI of the Microsoft app, never "
        "the secret. It reads no mailbox."
    ),
    "oauth_callback": (
        "Writes the caller's OWN mailbox row. It checks that the session's "
        "member is the member in the signed state before any write."
    ),
    "import_artifact": (
        "Copies a file between the caller's own agent workspaces through "
        "_member_agent_workspace. It reads no mailbox and no table."
    ),
    "microsoft_webhook": (
        "Machine-called by Microsoft Graph with no member. It matches a "
        "subscription id and its clientState inside the tenant of a signed "
        "org, and only schedules a sync of that mailbox."
    ),
    "org_connection_counts": (
        "EM-T3d. For admins only: require_permission('admin:members:read') "
        "on the route. It reads across members on purpose, and returns "
        "seven integer counts: no address, no member and no account id. "
        "R8: test_email_org_connection_counts.py."
    ),
}

# ── List 2: the modules outside routes/email that read an email child table ─

#: Each module outside ``routes/email`` whose SQL names an email child table,
#: and why its read is scoped. A key that ends in ``/`` is a package. Paths
#: are relative to the repository root.
OUTSIDE_EMAIL_READERS: dict[str, str] = {
    "apps/services/gateway/gateway/routes/crm/activities.py": (
        "The CRM timeline reads email through _email_account_scope, a copy "
        "of _account_scope bound to the caller (WS-26d-email). Mutation "
        "fence: test_crm_email_timeline.py."
    ),
    "apps/services/gateway/gateway/routes/crm/auto_lead.py": (
        "Runs inside process_new_mail for ONE mailbox, after its own sync. It "
        "reads that mailbox only and files leads for its owner."
    ),
    "apps/services/gateway/gateway/routes/tasks/capture_email.py": (
        "Captures a task from a message. _load_email joins email_accounts on "
        "user_id = the caller, and _account_owner checks id AND user_id, each "
        "a 404 first. _fetch_thread then reads that same owned mailbox."
    ),
    "apps/services/gateway/gateway/routes/tasks/email_link.py": (
        "The task-close hop. _closer_owns_mailbox checks that the member who "
        "closes the task owns the mailbox before any write (EM-T2c item 8)."
    ),
    "apps/services/gateway/gateway/routes/notes/dispatch.py": (
        "Reads the signature of the account that _default_email_account "
        "selected with user_id = the meeting owner, after "
        "cross_owner_refusal. Not in the spec list of 2026-10-02."
    ),
    "apps/services/email_ingestion/email_ingestion/": (
        "The sync engine. It reads and writes the mailbox it syncs, by "
        "account id, with no member request. A member never reaches it with "
        "an id of their choice."
    ),
}


# ── The scans ───────────────────────────────────────────────────────────────


def _is_router_handler(fn: ast.AST) -> bool:
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"):
            return True
    return False


def _strings(node: ast.AST) -> list[str]:
    """Every string literal under ``node``, f-string parts included."""
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                names.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                names.add(n.func.attr)
    return names


def has_owner_proof(fn: ast.AST) -> bool:
    """True when the handler body carries an owner predicate or calls an
    owner helper. Docstrings count as strings, so a docstring that quotes
    ``user_id = :uid`` would pass. That is an accepted gap of a text rule."""
    if _called_names(fn) & OWNER_HELPERS:
        return True
    return any(_OWNER_PREDICATE.search(s) for s in _strings(fn))


def handlers_of(source: str) -> list[tuple[str, bool]]:
    """``(name, has_owner_proof)`` for each ``@router`` handler in ``source``."""
    tree = ast.parse(source)
    return [(fn.name, has_owner_proof(fn))
            for fn in ast.walk(tree) if _is_router_handler(fn)]


def scan_email_handlers() -> dict[str, list[tuple[str, bool]]]:
    out: dict[str, list[tuple[str, bool]]] = {}
    for path in sorted(_EMAIL.rglob("*.py")):
        found = handlers_of(path.read_text(encoding="utf-8-sig"))
        if found:
            out[path.relative_to(_ROOT).as_posix()] = found
    return out


def unproven_violations(
    handlers: list[tuple[str, bool]], exempt: dict[str, str],
) -> list[str]:
    return sorted(n for n, proof in handlers if not proof and n not in exempt)


def stale_exemptions(
    handlers: list[tuple[str, bool]], exempt: dict[str, str],
) -> list[str]:
    """An entry is stale when no handler has its name, or when the handler
    now carries its own proof and needs no entry."""
    unproven = {n for n, proof in handlers if not proof}
    return sorted(n for n in exempt if n not in unproven)


def reasonless(entries: dict[str, str]) -> list[str]:
    return sorted(k for k, v in entries.items()
                  if not isinstance(v, str) or len(v.strip()) < 20)


def email_child_tables() -> frozenset[str]:
    """Every ``email_*`` table the migrations create, minus ``email_accounts``."""
    create = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(email_\w+)", re.IGNORECASE)
    tables: set[str] = set()
    for sql in _MIGRATIONS.rglob("*.sql"):
        tables.update(m.lower() for m in
                      create.findall(sql.read_text(encoding="utf-8-sig")))
    tables.discard("email_accounts")
    return frozenset(tables)


def reads_child_table(source: str, tables: frozenset[str]) -> set[str]:
    """The email child tables that a SQL string literal in ``source`` names."""
    sql = re.compile(
        r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(email_\w+)\b", re.IGNORECASE)
    hits: set[str] = set()
    for s in _strings(ast.parse(source)):
        hits.update(m.lower() for m in sql.findall(s) if m.lower() in tables)
    return hits


def _covered(rel: str, entries: dict[str, str]) -> str | None:
    for key in entries:
        if rel == key or (key.endswith("/") and rel.startswith(key)):
            return key
    return None


def scan_outside_readers(tables: frozenset[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    email_prefix = _EMAIL.relative_to(_ROOT).as_posix() + "/"
    for base in _OUTSIDE_ROOTS:
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(_ROOT).as_posix()
            if (rel.startswith(email_prefix) or "/tests/" in rel
                    or "/.venv/" in rel or "/node_modules/" in rel):
                continue
            hits = reads_child_table(path.read_text(encoding="utf-8-sig"), tables)
            if hits:
                out[rel] = hits
    return out


def uncovered_readers(
    readers: dict[str, set[str]], entries: dict[str, str],
) -> list[str]:
    return sorted(r for r in readers if _covered(r, entries) is None)


def stale_readers(
    readers: dict[str, set[str]], entries: dict[str, str],
) -> list[str]:
    used = {_covered(r, entries) for r in readers}
    return sorted(k for k in entries if k not in used)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def handlers() -> list[tuple[str, bool]]:
    return [h for found in scan_email_handlers().values() for h in found]


@pytest.fixture(scope="module")
def tables() -> frozenset[str]:
    return email_child_tables()


@pytest.fixture(scope="module")
def readers(tables) -> dict[str, set[str]]:
    return scan_outside_readers(tables)


# ── List 1 ──────────────────────────────────────────────────────────────────


class TestEveryEmailHandlerProvesOwnership:

    def test_the_scan_sees_the_handlers(self, handlers):
        """A scan that finds nothing passes everything. 107 handlers were
        measured on 2026-10-02."""
        assert len(handlers) >= 100, len(handlers)
        assert sum(1 for _, proof in handlers if proof) >= 90

    def test_handler_names_are_unique(self, handlers):
        """The exempt list is keyed by name, so two handlers with one name
        would share one entry."""
        names = [n for n, _ in handlers]
        assert len(names) == len(set(names)), sorted(
            n for n in set(names) if names.count(n) > 1)

    def test_every_handler_has_a_proof_or_an_exemption(self, handlers):
        missing = unproven_violations(handlers, OWNER_SCOPE_EXEMPT)
        assert not missing, (
            "these @router handlers in routes/email show no owner proof "
            "(a user_id predicate, _account_scope, _assert_account_owner or "
            f"provider_session) and have no OWNER_SCOPE_EXEMPT entry: {missing}"
        )

    def test_no_exemption_is_stale(self, handlers):
        stale = stale_exemptions(handlers, OWNER_SCOPE_EXEMPT)
        assert not stale, (
            "these OWNER_SCOPE_EXEMPT entries name no handler, or a handler "
            f"that now proves ownership itself. Delete them: {stale}"
        )

    def test_every_exemption_has_a_reason(self):
        assert not reasonless(OWNER_SCOPE_EXEMPT)


class TestTheHandlerScanCanFail:
    """Synthetic sources. Each proves that a check still goes red."""

    _LEAK = (
        "@router.get('/leak')\n"
        "async def leak(account_id: str, user=None):\n"
        "    return await db.execute(text(\n"
        "        'SELECT subject FROM email_messages WHERE account_id = :aid'),\n"
        "        {'aid': account_id})\n"
    )

    def test_a_handler_that_reads_email_messages_with_no_proof_fails(self):
        found = handlers_of(self._LEAK)
        assert found == [("leak", False)]
        assert unproven_violations(found, OWNER_SCOPE_EXEMPT) == ["leak"]

    @pytest.mark.parametrize("proof", [
        "    q = 'SELECT 1 FROM email_accounts WHERE id = :a AND user_id = :uid'\n",
        "    frag = _account_scope(None, params)\n",
        "    await _assert_account_owner(db, account_id, user.email)\n",
        "    async with provider_session(db, user.email, account_id=a) as s:\n"
        "        pass\n",
    ])
    def test_each_kind_of_proof_passes(self, proof):
        src = self._LEAK.replace("    return", proof + "    return", 1)
        assert handlers_of(src) == [("leak", True)]

    def test_a_comment_is_not_a_proof(self):
        src = self._LEAK.replace(
            "    return", "    # _assert_account_owner(db, a, u)\n    return", 1)
        assert handlers_of(src) == [("leak", False)]

    def test_a_stale_entry_fails(self):
        found = [("real", True)]
        assert stale_exemptions(found, {"gone": "x" * 30, "real": "y" * 30}) \
            == ["gone", "real"]

    def test_an_entry_with_no_reason_fails(self):
        assert reasonless({"a": "", "b": "   ", "c": "too short"}) == ["a", "b", "c"]


# ── List 2 ──────────────────────────────────────────────────────────────────


class TestEveryOutsideReaderIsListed:

    def test_the_child_tables_come_from_the_migrations(self, tables):
        assert {"email_messages", "email_thread_status", "email_senders",
                "email_attachments"} <= tables
        assert "email_accounts" not in tables

    def test_every_outside_reader_has_an_entry(self, readers):
        missing = uncovered_readers(readers, OUTSIDE_EMAIL_READERS)
        assert not missing, (
            "these modules outside routes/email read an email child table "
            f"and have no OUTSIDE_EMAIL_READERS entry: "
            f"{ {m: sorted(readers[m]) for m in missing} }"
        )

    def test_no_reader_entry_is_stale(self, readers):
        stale = stale_readers(readers, OUTSIDE_EMAIL_READERS)
        assert not stale, (
            f"these OUTSIDE_EMAIL_READERS entries match no module. Delete them: "
            f"{stale}"
        )

    def test_every_reader_entry_has_a_reason(self):
        assert not reasonless(OUTSIDE_EMAIL_READERS)


class TestTheReaderScanCanFail:

    def test_a_new_module_that_reads_a_child_table_fails(self, tables, readers):
        src = ("from sqlalchemy import text\n"
               "Q = text('SELECT count(*) FROM email_thread_status "
               "WHERE account_id = :aid')\n")
        assert reads_child_table(src, tables) == {"email_thread_status"}
        synthetic = {**readers,
                     "apps/services/gateway/gateway/routes/new/leak.py":
                     {"email_thread_status"}}
        assert uncovered_readers(synthetic, OUTSIDE_EMAIL_READERS) == [
            "apps/services/gateway/gateway/routes/new/leak.py"]

    def test_a_package_entry_covers_its_modules_only(self):
        entries = {"apps/x/pkg/": "a reason that is long enough"}
        assert uncovered_readers(
            {"apps/x/pkg/a.py": {"email_messages"},
             "apps/x/pkgother/b.py": {"email_messages"}}, entries,
        ) == ["apps/x/pkgother/b.py"]

    def test_a_module_that_names_no_table_is_not_a_reader(self, tables):
        assert reads_child_table(
            "from email_ingestion.inbound import start\n"
            "X = 'SELECT 1 FROM email_accounts'\n", tables) == set()
