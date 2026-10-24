"""WS-65 SM-2 — the one mailbox access seam, and its fence.

Spec: ``project-docs/specs/shared_mailbox.md`` §8.1, §8.2 step 2 and §8.3.

Before WS-65 the gateway wrote the member boundary by hand as
``user_id = :uid``: 91 SQL literals in 27 files under ``routes/email``
(measured on ``origin/main`` at ``d8b0fd94a`` on 2026-10-23, with the regex
below). Row level security binds the organization only, so a query that drops
that predicate reads the mail of every member. SM-2 moved each one to
``routes/email/mailbox_access.py``, or marked it ``# private-only:`` with a
reason.

What this file holds:

1. **The seam fence.** An AST scan of each SQL string literal under
   ``routes/email`` (docstrings excluded), line by line. A line with an owner
   predicate passes when a ``# private-only: <reason>`` marker stands at most
   four lines above it, and each marker covers ONE line, the first predicate
   line below it. A marker that covers no line is stale and fails. A file can
   instead have an entry in ``email_owner_predicate_allowlist.json`` with an
   exact count and a reason. A new unmarked site fails, so the ceiling only
   goes down.
2. **The flag off is the text of before.** Each builder of the seam returns
   the private arm, character for character, with ``EMAIL_SHARED_MAILBOX``
   off (§8.2 step 2).
3. **The flag on.** The shared arm names the levels at or above the level of
   the act, the bound tenant, and the three grant subjects.
4. **D-SM-4.** The email router carries ``require_feature_router("email")``,
   which checks the feature before any route reads a grant.
5. **One reader of the flag.** Only ``mailbox_access.py`` reads
   ``email_shared_mailbox``.

⚠️ **Limit (stated, R7).** The scan reads string literals. It does not follow
data. A predicate built at run time from pieces (``"user_" + "id = :uid"``)
passes it. The R8 suite ``test_email_shared_mailbox_r8.py`` proves the
behaviour on a real database.

Run::

    uv run pytest tests/unit/test_email_owner_predicate_seam.py -q
"""
from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_EMAIL = _ROOT / "apps/services/gateway/gateway/routes/email"
_ALLOWLIST = Path(__file__).with_name("email_owner_predicate_allowlist.json")

#: The owner predicate on ``user_id``: ``user_id = :uid``, ``ea.user_id =
#: :user_id``, ``lower(user_id) = :m``. The regex of
#: ``test_email_owner_scope_fence.py``, so the two fences agree.
OWNER_PREDICATE = re.compile(
    r"\buser_id\s*\)?\s*=\s*(?:LOWER\(\s*)?:\w+", re.IGNORECASE)

MARKER = re.compile(r"#\s*private-only:\s*(\S.*)?$")

#: How far below its marker a predicate line may stand.
MARKER_REACH = 4


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def predicate_lines(source: str) -> dict[int, int]:
    """Each source line inside a SQL literal (docstrings excluded) that holds
    an owner predicate, with the count of predicates on it."""
    tree = ast.parse(source)
    lines = source.splitlines()
    docs = _docstrings(tree)
    spans: set[int] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs and OWNER_PREDICATE.search(node.value)):
            spans.update(range(node.lineno, node.end_lineno + 1))
    return {n: k for n in sorted(spans)
            if (k := len(OWNER_PREDICATE.findall(lines[n - 1])))}


def _marker_comments(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` of each ``# private-only:`` COMMENT. Text inside a
    string or a docstring is not a marker."""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and MARKER.search(tok.string):
            out.append((tok.start[0], tok.string))
    return out


def _marker_lines(source: str) -> list[int]:
    return [line for line, _text in _marker_comments(source)]


def _covered(source: str) -> tuple[set[int], list[int]]:
    """The predicate lines that a marker covers, and the stale markers.

    A marker covers ONE line: the first predicate line at or below it, within
    :data:`MARKER_REACH` lines. A second predicate in the same SQL needs a
    marker of its own. A marker that covers no line is stale.
    """
    preds = predicate_lines(source)
    covered: set[int] = set()
    stale: list[int] = []
    for m in _marker_lines(source):
        hit = next((n for n in preds if m <= n <= m + MARKER_REACH), None)
        if hit is None:
            stale.append(m)
        else:
            covered.add(hit)
    return covered, stale


def unmarked_sites(source: str) -> list[int]:
    """The line of each owner predicate in a SQL literal with no marker."""
    covered, _stale = _covered(source)
    return [n for n, k in predicate_lines(source).items() if n not in covered
            for _ in range(k)]


def stale_markers(source: str) -> list[int]:
    """The line of each ``# private-only:`` marker that covers no predicate."""
    return _covered(source)[1]


def markers_without_reason(source: str) -> list[int]:
    """The line of each ``# private-only:`` marker with no reason after it."""
    return [line for line, text in _marker_comments(source)
            if not ((m := MARKER.search(text)) and (m.group(1) or "").strip())]


def scan() -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for path in sorted(_EMAIL.rglob("*.py")):
        sites = unmarked_sites(path.read_text(encoding="utf-8"))
        if sites:
            out[path.relative_to(_EMAIL).as_posix()] = sites
    return out


def _allowlist() -> dict[str, dict]:
    return json.loads(_ALLOWLIST.read_text(encoding="utf-8"))["files"]


# ── 1. The seam fence ───────────────────────────────────────────────────────

class TestTheSeamFence:
    def test_the_scan_still_finds_the_routes(self) -> None:
        files = list(_EMAIL.rglob("*.py"))
        assert len(files) > 40, "the scan lost the email routes"
        assert (_EMAIL / "mailbox_access.py").exists()

    def test_no_new_unmarked_owner_predicate(self) -> None:
        allowed = _allowlist()
        over = {
            path: sites for path, sites in scan().items()
            if len(sites) > allowed.get(path, {}).get("count", 0)
        }
        assert not over, (
            "A new inline `user_id = :uid` owner check under routes/email. Use "
            "routes/email/mailbox_access.py (mailbox_predicate, mailbox_ids_sql, "
            "private_predicate, assert_mailbox_access), or put "
            "`# private-only: <reason>` above the SQL. Lines: " + repr(over)
        )

    def test_each_allowlist_entry_is_exact_and_has_a_reason(self) -> None:
        found = scan()
        for path, entry in _allowlist().items():
            assert (entry.get("reason") or "").strip(), f"{path} has no reason"
            assert len(found.get(path, [])) == entry["count"], (
                f"{path} holds {len(found.get(path, []))} unmarked sites and the "
                f"allowlist says {entry['count']}. Lower the count, or remove "
                "the entry when it reaches 0."
            )

    def test_no_marker_is_stale(self) -> None:
        bad = {
            path.relative_to(_EMAIL).as_posix(): lines
            for path in sorted(_EMAIL.rglob("*.py"))
            if (lines := stale_markers(path.read_text(encoding="utf-8")))
        }
        assert not bad, f"a `# private-only:` marker that covers no predicate: {bad}"

    def test_each_marker_names_a_reason(self) -> None:
        bad = {
            path.relative_to(_EMAIL).as_posix(): lines
            for path in sorted(_EMAIL.rglob("*.py"))
            if (lines := markers_without_reason(path.read_text(encoding="utf-8")))
        }
        assert not bad, f"a `# private-only:` marker with no reason: {bad}"

    def test_a_synthetic_site_goes_red_and_a_marked_one_passes(self) -> None:
        bare = 'SQL = "SELECT id FROM email_accounts WHERE user_id = :uid"\n'
        assert unmarked_sites(bare) == [1]
        marked = "# private-only: self is private (D-SM-5).\n" + bare
        assert unmarked_sites(marked) == []
        far = "# private-only: too far.\n\n\n\n\n" + bare
        assert unmarked_sites(far) == [6]
        assert stale_markers(far) == [1]
        doc = 'def f():\n    """It reads user_id = :uid."""\n'
        assert unmarked_sites(doc) == []
        fstr = ('# private-only: an f-string.\nX = f"""SELECT {a}\n'
                '  FROM t WHERE lower(user_id) = :m"""\n')
        assert unmarked_sites(fstr) == []
        # A marker inside the text of a string is not a marker.
        quoted = 'X = "# private-only: no."\nY = "SELECT 1 WHERE user_id = :uid"\n'
        assert unmarked_sites(quoted) == [2]
        # One marker covers one line. A second site in the same SQL is red.
        two = ('# private-only: the first.\nX = """SELECT 1 WHERE user_id = :uid\n'
               '  AND x IN (SELECT 1 WHERE user_id = :uid)"""\n')
        assert unmarked_sites(two) == [3]
        assert markers_without_reason("# private-only:\n") == [1]


# ── 2 and 3. The builders ───────────────────────────────────────────────────

@pytest.fixture
def flag(monkeypatch):
    from acb_common import get_settings

    def _set(value: bool) -> None:
        monkeypatch.setattr(get_settings(), "email_shared_mailbox", value, raising=False)

    _set(False)
    return _set


class TestTheBuilders:
    def test_the_flag_is_off_by_default(self) -> None:
        from acb_common.settings import Settings

        assert Settings.model_fields["email_shared_mailbox"].default is False

    def test_the_flag_off_is_the_text_of_before(self, flag) -> None:
        from gateway.routes.email import core
        from gateway.routes.email import mailbox_access as ma

        assert ma.shared_enabled() is False
        for level in ma.LEVELS:
            assert ma.mailbox_predicate("ea", uid="user_id", level=level) == (
                "ea.user_id = :user_id")
            assert ma.mailbox_predicate(uid="uid", level=level) == "user_id = :uid"
            assert ma.mailbox_ids_sql(uid="uid", level=level) == (
                "SELECT id FROM email_accounts WHERE user_id = :uid")
        # "Self" never holds a shared box, at every flag (D-SM-5).
        assert ma.private_predicate("a", uid="uid") == (
            "(a.user_id = :uid AND a.shared_since IS NULL)")
        params: dict = {}
        assert core._account_scope(None, params) == (
            "em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid)")

    def test_the_flag_on_adds_the_shared_arm(self, flag) -> None:
        from gateway.routes.email import mailbox_access as ma

        flag(True)
        sql = ma.mailbox_predicate("ea", uid="user_id", level="send")
        assert sql.startswith("((ea.user_id = :user_id AND ea.shared_since IS NULL)")
        assert "ea.shared_since IS NOT NULL" in sql
        assert "emg.level IN ('send', 'manage')" in sql
        assert "emg.subject = 'org'" in sql
        assert "emg.subject = lower(CAST(:user_id AS text))" in sql
        assert "'group:' || g.slug" in sql
        assert "current_setting('app.tenant_id', true)" in sql
        assert "emg.organization_id = ea.organization_id" in sql
        assert "emg.level IN ('read', 'send', 'manage')" in ma.mailbox_predicate(level="read")
        assert "emg.level IN ('manage')" in ma.mailbox_predicate()
        assert ma.private_predicate("a", uid="uid") == (
            "(a.user_id = :uid AND a.shared_since IS NULL)")

    def test_an_unknown_level_raises(self, flag) -> None:
        from gateway.routes.email import mailbox_access as ma

        with pytest.raises(ValueError):
            ma.mailbox_predicate(level="write")


# ── 4. D-SM-4 and 5. one reader ─────────────────────────────────────────────

def test_the_email_router_checks_the_feature_before_any_grant() -> None:
    """D-SM-4: the router dependency is the feature check. A route of the
    package is under it by construction."""
    src = (_EMAIL / "core.py").read_text(encoding="utf-8")
    assert 'dependencies=[require_feature_router("email", exempt=[' in src


def test_only_the_seam_reads_the_flag() -> None:
    readers = []
    for root in (_ROOT / "apps", _ROOT / "packages"):
        for path in root.rglob("*.py"):
            if "node_modules" in path.parts:
                continue
            if "email_shared_mailbox" in path.read_text(encoding="utf-8", errors="ignore"):
                readers.append(path.relative_to(_ROOT).as_posix())
    assert sorted(readers) == [
        "apps/services/gateway/gateway/routes/email/mailbox_access.py",
        "packages/acb_common/acb_common/settings.py",
    ], readers
