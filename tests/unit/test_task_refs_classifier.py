"""WS-57 TR-1 — the reference classifier.

Spec: ``project-docs/specs/task_references.md`` §6 TR-1. Done when the
classifier passes a table test of at least 20 URLs.

``gateway.routes.projects.refs_classify.classify`` is pure: no session, no
clock, no network. So this file needs no database and no fake. It proves:

* each known shape gives its ``kind``, ``provider``, ``ref`` and label
  (GitHub pull request, issue, commit, branch and ``#issuecomment-N``;
  GitLab merge request; Google Docs, Sheets and Slides; Figma; Notion);
* every other ``http(s)`` URL is ``kind = url``;
* a scheme that is not ``http(s)`` is refused, and so is an owner or a
  repository outside ``[A-Za-z0-9._-]``. The route answers 422 for both;
* the Python vocabularies equal the CHECKs of the migration, found by
  content and never by number (R1).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects.refs_classify import (
    KINDS,
    MAX_URL,
    PROVIDERS,
    STATES,
    RefRefused,
    classify,
)

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

#: (url, kind, provider, ref, label). The ref is the unique key's last part.
CASES: list[tuple[str, str, str, str, str]] = [
    # ── GitHub ────────────────────────────────────────────────────────────
    ("https://github.com/acme/widgets/pull/12", "pull_request", "github",
     "acme/widgets#12", "widgets#12"),
    ("https://github.com/acme/widgets/pull/12/files", "pull_request", "github",
     "acme/widgets#12", "widgets#12"),
    ("https://www.github.com/Acme/Widgets.js/pull/7?diff=split", "pull_request", "github",
     "Acme/Widgets.js#7", "Widgets.js#7"),
    ("https://github.com/acme/widgets/issues/88", "issue", "github",
     "acme/widgets#88", "widgets#88"),
    ("https://github.com/acme/widgets/commit/9F3C2B1d0aa", "commit", "github",
     "acme/widgets@9f3c2b1d0aa", "widgets@9f3c2b1"),
    ("https://github.com/acme/widgets/pull/12/commits/abcdef1234567", "commit", "github",
     "acme/widgets@abcdef1234567", "widgets@abcdef1"),
    ("https://github.com/acme/widgets/tree/main", "branch", "github",
     "acme/widgets:main", "widgets:main"),
    ("https://github.com/acme/widgets/tree/feature/login-fix", "branch", "github",
     "acme/widgets:feature/login-fix", "widgets:feature/login-fix"),
    ("https://github.com/acme/widgets/issues/88#issuecomment-1234567", "comment", "github",
     "acme/widgets#88/comment-1234567", "widgets#88 comment"),
    ("https://github.com/acme/widgets/pull/12#issuecomment-99", "comment", "github",
     "acme/widgets#12/comment-99", "widgets#12 comment"),
    ("https://github.com/acme.widgets/repo.git/pull/3", "pull_request", "github",
     "acme.widgets/repo#3", "repo#3"),
    # A GitHub URL of no known shape is a plain link with the GitHub provider.
    ("https://github.com/acme/widgets", "url", "github",
     "https://github.com/acme/widgets", "github.com"),
    ("https://github.com/acme/widgets/blob/main/README.md", "url", "github",
     "https://github.com/acme/widgets/blob/main/README.md", "github.com"),
    # ── GitLab and Bitbucket ──────────────────────────────────────────────
    ("https://gitlab.com/acme/platform/api/-/merge_requests/42", "pull_request", "gitlab",
     "acme/platform/api!42", "api!42"),
    ("https://gitlab.com/acme/api/-/issues/5", "issue", "gitlab", "acme/api#5", "api#5"),
    ("https://bitbucket.org/acme/widgets/pull-requests/9", "pull_request", "bitbucket",
     "acme/widgets#9", "widgets#9"),
    # ── Documents ─────────────────────────────────────────────────────────
    ("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit", "doc", "google",
     "document/1AbCdEfGhIjKlMnOp", "Google Doc"),
    ("https://docs.google.com/spreadsheets/d/1ZyXwVuTsRqP_-x/edit#gid=0", "doc", "google",
     "spreadsheets/1ZyXwVuTsRqP_-x", "Google Sheet"),
    ("https://docs.google.com/presentation/d/1QwErTyUiOpAs/view", "doc", "google",
     "presentation/1QwErTyUiOpAs", "Google Slides"),
    ("https://drive.google.com/file/d/1LkJhGfDsA987/view", "doc", "google",
     "file/1LkJhGfDsA987", "Google Drive file"),
    ("https://www.figma.com/design/AbCdEfGhIjKlMn/Checkout-flow?node-id=1-2", "doc", "figma",
     "AbCdEfGhIjKlMn", "Figma file"),
    ("https://www.notion.so/acme/Launch-plan-0123456789abcdef0123456789abcdef", "doc",
     "notion", "0123456789abcdef0123456789abcdef", "Notion page"),
    ("https://acme.notion.site/Roadmap", "doc", "notion",
     "https://acme.notion.site/Roadmap", "Notion page"),
    # ── Every other http(s) URL ───────────────────────────────────────────
    ("https://example.com/spec", "url", "other", "https://example.com/spec", "example.com"),
    ("http://intranet.acme.local:8080/wiki/Runbook", "url", "other",
     "http://intranet.acme.local:8080/wiki/Runbook", "intranet.acme.local"),
    ("HTTPS://Example.COM", "url", "other", "https://example.com/", "example.com"),
    ("https://example.com:443/a?b=1#c", "url", "other", "https://example.com/a?b=1#c",
     "example.com"),
]


def test_the_table_holds_at_least_twenty_urls() -> None:
    assert len(CASES) >= 20


@pytest.mark.parametrize(("url", "kind", "provider", "ref", "label"), CASES)
def test_each_shape_classifies(url: str, kind: str, provider: str, ref: str, label: str) -> None:
    found = classify(url)
    assert (found.kind, found.provider, found.ref, found.label) == (kind, provider, ref, label)
    assert found.kind in KINDS
    assert found.provider in PROVIDERS
    assert found.url.startswith(("http://", "https://"))


def test_two_urls_of_one_pull_request_share_the_ref() -> None:
    """The unique key holds the ref, so the second paste finds the first row."""
    one = classify("https://github.com/acme/widgets/pull/12")
    two = classify("https://github.com/acme/widgets/pull/12/files#diff-1")
    assert (one.kind, one.provider, one.ref) == (two.kind, two.provider, two.ref)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "data:text/html,<b>x</b>",
        "mailto:a@x.io",
        "ssh://git@github.com/acme/widgets.git",
        "example.com/no-scheme",
    ],
)
def test_a_scheme_that_is_not_http_is_refused(url: str) -> None:
    with pytest.raises(RefRefused):
        classify(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/../../pull/1",
        "https://github.com/acme/..%2F..%2Fadmin/pull/1",
        "https://github.com/ac%20me/widgets/pull/1",
        "https://github.com/acme/wid%3Fgets/issues/2",
        "https://github.com/acme/w%C3%BCdgets/commit/abcdef1",
        "https://github.com/acme/wid@gets/tree/main",
        "https://github.com/acme~x/widgets/pull/1",
        "https://gitlab.com/ac%2Fme/api/-/merge_requests/1",
        "https://github.com/acme/widgets/tree/a..b",
    ],
)
def test_a_hostile_owner_repository_or_branch_is_refused(url: str) -> None:
    """The owner and the repository go into an api.github.com path, so a name
    outside [A-Za-z0-9._-] never reaches a row."""
    with pytest.raises(RefRefused):
        classify(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://exa mple.com/",
        "https://example.com/a\tb",
        "https://user:secret@example.com/",
        "https://",
        "https://example.com/" + "a" * MAX_URL,
    ],
)
def test_an_unreadable_or_unsafe_url_is_refused(url: str) -> None:
    with pytest.raises(RefRefused):
        classify(url)


def test_a_non_ascii_path_is_encoded_not_refused() -> None:
    """A person types a word, and the browser would encode it. The stored URL
    is ASCII, so the length bound is a byte bound."""
    found = classify("https://example.com/über")
    assert found.url == "https://example.com/%C3%BCber"
    assert found.url.isascii()


def _migration_text() -> str:
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS pm_task_refs" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0].read_text(encoding="utf-8")


def _check_values(sql: str, column: str) -> tuple[str, ...]:
    match = re.search(rf"CHECK \({column} IN \((.*?)\)\)", sql, re.S)
    if match is None:
        match = re.search(rf"OR {column} IN \((.*?)\)\)", sql, re.S)
    assert match, f"no CHECK on {column}"
    return tuple(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_vocabularies_equal_the_migration_checks() -> None:
    """A tuple that mirrors a CHECK by hand needs a test that reads the CHECK.
    The attachment 422 of `core.ACTIVITY_TYPES` is the incident behind it."""
    sql = _migration_text()
    assert _check_values(sql, "kind") == KINDS
    assert _check_values(sql, "provider") == PROVIDERS
    assert _check_values(sql, "state") == STATES
    assert f"char_length(url) <= {MAX_URL}" in sql
