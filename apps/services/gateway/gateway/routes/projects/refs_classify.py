"""Projects · the reference classifier (WS-57 TR-1).

Spec: ``project-docs/specs/task_references.md`` §5 and §6 TR-1.

One pure function, :func:`classify`. It takes the URL a person pasted and
gives back what the reference IS: its ``kind``, its ``provider``, the ``ref``
that identifies it, a short ``label`` and the canonical ``url``. The route in
``refs.py`` writes what it returns, and TR-5 will call it for a URL it finds in
a task's text. It opens no session, reads no clock and calls no network.

**Why a refusal is an exception.** A URL that is not ``http`` or ``https``, or a
GitHub URL whose owner or repository holds a character outside
``[A-Za-z0-9._-]``, is refused with :class:`RefRefused`, and the route answers
422 with its message. The owner and repository go into an ``api.github.com``
path later, so this is the first of two checks on them (``refs.py`` checks
again before it builds the request).

**Why ``ref`` and not the URL is the key.** Two URLs can name one thing: a pull
request and its ``/files`` tab, or a Google Doc opened at two places. The
unique key of ``pm_task_refs`` holds ``(kind, provider, ref)``, so the second
paste finds the first row.

The module imports nothing from its siblings, like ``conflicts.py``.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

#: The CHECK on `pm_task_refs.kind` in migration 255, mirrored. The tests read
#: the migration and fail if the two differ.
KINDS: tuple[str, ...] = (
    "pull_request", "issue", "commit", "branch", "comment", "doc", "url",
)
#: The CHECK on `pm_task_refs.provider`, mirrored the same way.
PROVIDERS: tuple[str, ...] = (
    "github", "gitlab", "bitbucket", "google", "figma", "notion", "other",
)
#: The CHECK on `pm_task_refs.state`. NULL is the fifth value: not read.
STATES: tuple[str, ...] = ("open", "draft", "merged", "closed")

#: The CHECK on `pm_task_refs.url` holds the same bound.
MAX_URL = 2048

#: An owner, a repository, or one segment of a GitLab namespace.
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_NUMBER_RE = re.compile(r"^[0-9]{1,10}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_COMMENT_RE = re.compile(r"^issuecomment-([0-9]{1,15})$")
_GOOGLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_FIGMA_KEY_RE = re.compile(r"^[A-Za-z0-9]{10,64}$")
_NOTION_ID_RE = re.compile(r"([0-9a-f]{32})$")
#: Characters `quote` must leave alone when it re-encodes a path, a query or a
#: fragment. Everything printable in ASCII that a URL may carry as is.
_SAFE = "!#$%&'()*+,/:;=?@[]~-._"

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITLAB_HOSTS = frozenset({"gitlab.com", "www.gitlab.com"})
_BITBUCKET_HOSTS = frozenset({"bitbucket.org", "www.bitbucket.org"})
_FIGMA_HOSTS = frozenset({"figma.com", "www.figma.com"})
#: Google's editors: the path word, and what the label calls it.
_GOOGLE_EDITORS: dict[str, str] = {
    "document": "Google Doc",
    "spreadsheets": "Google Sheet",
    "presentation": "Google Slides",
    "forms": "Google Form",
}


class RefRefused(ValueError):
    """A URL this classifier will not store. The message is for the member."""


@dataclass(frozen=True)
class Classified:
    kind: str
    provider: str
    ref: str
    label: str
    url: str


def _name(value: str, what: str) -> str:
    if not NAME_RE.match(value) or value in (".", ".."):
        raise RefRefused(
            f"The {what} in this link is not a valid name. "
            "A name may hold only letters, digits, '.', '_' and '-'."
        )
    return value


def _canonical(raw: str) -> tuple[str, str, str, str, str, str]:
    """``(scheme, host, netloc, path, query, fragment)`` of a URL we accept.

    The host is lower case and IDNA-encoded. The path, the query and the
    fragment are re-encoded to ASCII, so the length bound is a byte bound and
    the unique index never meets a key that is too long.
    """
    text = (raw or "").strip()
    if not text:
        raise RefRefused("Give a link.")
    if any(ord(ch) < 32 or ch.isspace() or ord(ch) == 127 for ch in text):
        raise RefRefused("A link cannot hold spaces or control characters.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise RefRefused("That is not a link that can be read.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise RefRefused("Only http and https links can be added to a task.")
    if parts.username is not None or parts.password is not None:
        raise RefRefused("A link cannot carry a user name or a password.")
    hostname = (parts.hostname or "").rstrip(".")
    if not hostname:
        raise RefRefused("That link has no host.")
    # An IP literal (`https://[::1]/`, `http://10.0.0.5/`) is refused. A task
    # reference names a site, and `urlsplit` drops an IPv6 host's brackets,
    # so the stored URL would not be the one the member pasted.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise RefRefused("A link to an IP address cannot be added to a task.")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise RefRefused("The host of that link is not valid.") from exc
    default = {"http": 80, "https": 443}[scheme]
    netloc = host if port in (None, default) else f"{host}:{port}"
    path = quote(parts.path or "/", safe=_SAFE)
    query = quote(parts.query, safe=_SAFE)
    fragment = quote(parts.fragment, safe=_SAFE)
    return scheme, host, netloc, path, query, fragment


def _segments(path: str) -> list[str]:
    """The path's segments. A dot segment (``.``, ``..``, ``%2e%2e``) is
    refused for every URL: a browser resolves it, so the link would open a
    place other than the one the classifier read."""
    segs = [s for s in path.split("/") if s]
    if any(unquote(s) in (".", "..") for s in segs):
        raise RefRefused("A link cannot hold a '.' or '..' path segment.")
    return segs


# ⚠️ **The stored URL of a known code shape is REBUILT from what the
# classifier read**, never kept as pasted. A tail after `/pull/N` (`/files`,
# `/commits`, anything) is dropped, so the link that opens is the thing the
# card names. The owner and the repository keep the case the member typed in
# the URL, so the link reads as they wrote it. The `ref` holds them in LOWER
# case, because GitHub, GitLab and Bitbucket all treat the names as
# case-insensitive (Bitbucket stores its slugs in lower case), so
# `Org/Repo#1` and `org/repo#1` are one key.
_GITHUB_URL = "https://github.com"
_GITLAB_URL = "https://gitlab.com"
_BITBUCKET_URL = "https://bitbucket.org"


def _github(segs: list[str], fragment: str) -> Classified | None:
    """A GitHub shape, or ``None`` for a GitHub URL of no known shape."""
    if len(segs) < 4:
        return None
    verb = segs[2]
    if verb not in ("pull", "issues", "commit", "tree"):
        return None
    owner = _name(segs[0], "owner")
    repo = _name(segs[1].removesuffix(".git"), "repository")
    key = f"{owner}/{repo}".lower()
    base = f"{_GITHUB_URL}/{owner}/{repo}"
    rest = segs[3:]
    if verb in ("pull", "issues"):
        if not _NUMBER_RE.match(rest[0]):
            return None
        number = int(rest[0])
        # A pull request's own commit: /pull/N/commits/SHA.
        if verb == "pull" and len(rest) >= 3 and rest[1] == "commits" and _SHA_RE.match(rest[2]):
            sha = rest[2].lower()
            return Classified(
                "commit", "github", f"{key}@{sha}", f"{repo}@{sha[:7]}", f"{base}/commit/{sha}",
            )
        comment = _COMMENT_RE.match(fragment)
        if comment:
            cid = comment.group(1)
            return Classified(
                "comment", "github", f"{key}#{number}/comment-{cid}",
                f"{repo}#{number} comment", f"{base}/{verb}/{number}#issuecomment-{cid}",
            )
        kind = "pull_request" if verb == "pull" else "issue"
        return Classified(
            kind, "github", f"{key}#{number}", f"{repo}#{number}", f"{base}/{verb}/{number}",
        )
    if verb == "commit":
        if not _SHA_RE.match(rest[0]):
            return None
        sha = rest[0].lower()
        return Classified(
            "commit", "github", f"{key}@{sha}", f"{repo}@{sha[:7]}", f"{base}/commit/{sha}",
        )
    # verb == "tree": the branch is the rest of the path. A branch may hold a
    # slash, so the whole remainder is the name. A branch name IS case
    # sensitive, so it keeps its case in the ref.
    branch = unquote("/".join(rest))
    if (
        not branch or len(branch) > 255 or ".." in branch
        or any(ord(ch) < 33 or ord(ch) == 127 for ch in branch)
    ):
        raise RefRefused("The branch name in this link is not valid.")
    return Classified(
        "branch", "github", f"{key}:{branch}", f"{repo}:{branch}",
        f"{base}/tree/{'/'.join(rest)}",
    )


def _gitlab(segs: list[str]) -> Classified | None:
    """``group/sub/project/-/merge_requests/N`` and ``…/-/issues/N``."""
    if "-" not in segs:
        return None
    at = segs.index("-")
    namespace, rest = segs[:at], segs[at + 1:]
    if len(namespace) < 2 or len(rest) < 2 or not _NUMBER_RE.match(rest[1]):
        return None
    if rest[0] not in ("merge_requests", "issues"):
        return None
    for seg in namespace:
        _name(seg, "group or project")
    project = "/".join(namespace)
    key = project.lower()
    number = int(rest[1])
    url = f"{_GITLAB_URL}/{project}/-/{rest[0]}/{number}"
    if rest[0] == "merge_requests":
        return Classified(
            "pull_request", "gitlab", f"{key}!{number}", f"{namespace[-1]}!{number}", url,
        )
    return Classified("issue", "gitlab", f"{key}#{number}", f"{namespace[-1]}#{number}", url)


def _bitbucket(segs: list[str]) -> Classified | None:
    """``owner/repo/pull-requests/N``."""
    if len(segs) < 4 or segs[2] != "pull-requests" or not _NUMBER_RE.match(segs[3]):
        return None
    owner = _name(segs[0], "owner")
    repo = _name(segs[1], "repository")
    number = int(segs[3])
    return Classified(
        "pull_request", "bitbucket", f"{owner}/{repo}#{number}".lower(), f"{repo}#{number}",
        f"{_BITBUCKET_URL}/{owner}/{repo}/pull-requests/{number}",
    )


def _google(host: str, segs: list[str], url: str) -> Classified | None:
    """Docs, Sheets, Slides and Forms (``/<editor>/d/<id>``), and a Drive file."""
    if len(segs) < 3 or not _GOOGLE_ID_RE.match(segs[2]):
        return None
    if host == "docs.google.com" and segs[0] in _GOOGLE_EDITORS and segs[1] == "d":
        return Classified(
            "doc", "google", f"{segs[0]}/{segs[2]}", _GOOGLE_EDITORS[segs[0]], url,
        )
    if host == "drive.google.com" and segs[:2] == ["file", "d"]:
        return Classified("doc", "google", f"file/{segs[2]}", "Google Drive file", url)
    return None


def _figma(segs: list[str], url: str) -> Classified | None:
    if (
        len(segs) >= 2
        and segs[0] in ("file", "design", "proto", "board", "slides")
        and _FIGMA_KEY_RE.match(segs[1])
    ):
        return Classified("doc", "figma", segs[1], "Figma file", url)
    return None


def _notion(segs: list[str], url: str) -> Classified:
    """A Notion page. Its id is the 32 hex digits at the end of the last
    segment, when there are any. Else the canonical URL is the key."""
    found = _NOTION_ID_RE.search(segs[-1].lower()) if segs else None
    ref = found.group(1) if found else url
    return Classified("doc", "notion", ref, "Notion page", url)


def classify(raw: str) -> Classified:
    """What one pasted URL is. Raises :class:`RefRefused` for a URL we refuse.

    Every ``http(s)`` URL that matches no known shape is ``kind = url`` with
    the canonical URL as its ``ref``. Its provider is the host's provider
    when the host is known, else ``other``.
    """
    scheme, host, netloc, path, query, fragment = _canonical(raw)
    url = urlunsplit((scheme, netloc, path, query, fragment))
    if len(url) > MAX_URL:
        raise RefRefused(f"That link is longer than {MAX_URL} characters.")
    segs = _segments(path)

    provider = "other"
    found: Classified | None = None
    if netloc != host:
        # A port that is not the scheme's default (`github.com:8443`) is not
        # the provider's site, so the URL is a plain link.
        pass
    elif host in _GITHUB_HOSTS:
        provider = "github"
        found = _github(segs, fragment)
    elif host in _GITLAB_HOSTS:
        provider = "gitlab"
        found = _gitlab(segs)
    elif host in _BITBUCKET_HOSTS:
        provider = "bitbucket"
        found = _bitbucket(segs)
    elif host in ("docs.google.com", "drive.google.com"):
        provider = "google"
        found = _google(host, segs, url)
    elif host in _FIGMA_HOSTS:
        provider = "figma"
        found = _figma(segs, url)
    elif host in ("notion.so", "www.notion.so") or host.endswith(".notion.site"):
        return _notion(segs, url)
    if found is not None:
        return found
    return Classified("url", provider, url, host, url)


__all__ = [
    "KINDS",
    "MAX_URL",
    "NAME_RE",
    "PROVIDERS",
    "STATES",
    "Classified",
    "RefRefused",
    "classify",
]
