"""Load the playbook topics from the content folder.

A topic file starts with a header between two lines of three dashes. Every
header has these keys: slug, version, title, summary and audience. The slug
equals the path of the file under content/, without the .md suffix.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONTENT_DIR = Path(__file__).resolve().parent / "content"
AUDIENCES = ("admin", "agent", "both")
REQUIRED_KEYS = ("slug", "version", "title", "summary", "audience")
SUMMARY_MAX = 200


class PlaybookError(ValueError):
    """A topic file has a bad header or a bad body."""


@dataclass(frozen=True)
class Topic:
    slug: str
    version: int
    title: str
    summary: str
    audience: str
    body: str


def parse_topic(text: str, *, expected_slug: str | None = None) -> Topic:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise PlaybookError("the file must start with a header line of three dashes")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise PlaybookError("the header has no closing line of three dashes") from None
    header: dict[str, str] = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise PlaybookError(f"header line without a colon: {raw!r}")
        header[key.strip()] = value.strip()
    missing = [k for k in REQUIRED_KEYS if not header.get(k)]
    if missing:
        raise PlaybookError(f"header lacks: {', '.join(missing)}")
    unknown = sorted(set(header) - set(REQUIRED_KEYS))
    if unknown:
        raise PlaybookError(f"header has unknown keys: {', '.join(unknown)}")
    try:
        version = int(header["version"])
    except ValueError:
        raise PlaybookError("version must be a whole number") from None
    if version < 1:
        raise PlaybookError("version must be 1 or more")
    if header["audience"] not in AUDIENCES:
        raise PlaybookError(f"audience must be one of {', '.join(AUDIENCES)}")
    if len(header["summary"]) > SUMMARY_MAX:
        raise PlaybookError(f"summary is longer than {SUMMARY_MAX} characters")
    if expected_slug is not None and header["slug"] != expected_slug:
        raise PlaybookError(f"slug {header['slug']!r} does not match the path {expected_slug!r}")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise PlaybookError("the body is empty")
    return Topic(header["slug"], version, header["title"], header["summary"], header["audience"], body)


def load_all(root: Path | None = None) -> list[Topic]:
    """Every topic, sorted by slug. A bad file raises PlaybookError with its path."""
    base = root or CONTENT_DIR
    topics: list[Topic] = []
    for path in sorted(base.rglob("*.md")):
        slug = path.relative_to(base).with_suffix("").as_posix()
        try:
            topics.append(parse_topic(path.read_text(encoding="utf-8"), expected_slug=slug))
        except PlaybookError as exc:
            raise PlaybookError(f"{slug}: {exc}") from exc
    return topics


def load_topic(slug: str, root: Path | None = None) -> Topic | None:
    for topic in load_all(root):
        if topic.slug == slug:
            return topic
    return None
