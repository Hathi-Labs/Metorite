"""The playbook content and its loader (PB-1, D186). R7: this file is the fence.

Each topic file has a header (slug, version, title, summary, audience). The
loader reads them all. A bad header or a bad slug fails the load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from acb_playbook import PlaybookError, load_all, load_topic
from acb_playbook.loader import CONTENT_DIR, parse_topic

GOOD = "---\nslug: demo\nversion: 1\ntitle: Demo\nsummary: A demo.\naudience: both\n---\n\n# Demo\n\nText.\n"


def test_the_loader_lists_every_file_with_its_version():
    files = sorted(p.relative_to(CONTENT_DIR).with_suffix("").as_posix() for p in CONTENT_DIR.rglob("*.md"))
    topics = load_all()
    assert [t.slug for t in topics] == files
    assert files, "the playbook has no topics"
    assert all(t.version >= 1 for t in topics)


def test_the_required_topics_exist():
    slugs = {t.slug for t in load_all()}
    required = {
        "index",
        "brief-contract",
        "gates",
        "failures",
        "cost-model",
        "patterns/one-agent-first",
        "patterns/sub-agent-contract",
        "patterns/facts-need-provenance",
        "patterns/shared-artifact-and-arbiter",
        "patterns/budgets-and-loop-guards",
        "patterns/structural-failures",
        "kit/agents-md",
        "kit/hooks-example",
        "kit/verifier-agent",
        "kit/reviewer-agent",
        "kit/plugin-guide",
        "default-guidance",
    }
    assert required <= slugs


def test_the_index_names_every_pattern_and_every_core_topic():
    index = load_topic("index")
    assert index is not None
    for topic in load_all():
        if topic.slug.startswith("patterns/") or topic.slug in {"brief-contract", "gates", "failures", "cost-model"}:
            assert f"`{topic.slug}`" in index.body, f"the index does not name {topic.slug}"


def test_the_failure_causes_are_the_fixed_list():
    body = load_topic("failures").body
    causes = [
        "ci_wait", "no_disposition", "question_terminal_only", "answer_unread", "merge_not_linked",
        "session_dead", "epic_hold", "box_down", "signed_out", "capacity", "human_wait", "other",
    ]
    for cause in causes:
        assert f"`{cause}`" in body


def test_a_good_header_parses():
    topic = parse_topic(GOOD, expected_slug="demo")
    assert (topic.slug, topic.version, topic.audience) == ("demo", 1, "both")


@pytest.mark.parametrize(
    "bad",
    [
        GOOD.replace("---\n", "", 1),  # no opening line
        GOOD.replace("audience: both\n---", "audience: both"),  # no closing line
        GOOD.replace("version: 1", "version: one"),
        GOOD.replace("version: 1", "version: 0"),
        GOOD.replace("audience: both", "audience: nobody"),
        GOOD.replace("title: Demo\n", ""),
        GOOD.replace("summary: A demo.", "summary: " + "x" * 201),
        GOOD.replace("audience: both\n", "audience: both\nextra: 1\n"),
        GOOD.split("---\n\n")[0] + "---\n\n",  # empty body
    ],
)
def test_a_bad_header_fails_the_load(bad):
    with pytest.raises(PlaybookError):
        parse_topic(bad, expected_slug="demo")


def test_a_slug_that_does_not_match_the_path_fails():
    with pytest.raises(PlaybookError):
        parse_topic(GOOD, expected_slug="other")


def test_load_all_names_the_bad_file(tmp_path: Path):
    (tmp_path / "ok.md").write_text(GOOD.replace("slug: demo", "slug: ok"), encoding="utf-8")
    (tmp_path / "bad.md").write_text(GOOD.replace("slug: demo", "slug: bad").replace("version: 1", "version: x"), encoding="utf-8")
    with pytest.raises(PlaybookError, match="bad"):
        load_all(tmp_path)
