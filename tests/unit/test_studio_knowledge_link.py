"""WS-STU I5 — the Knowledge link, hermetic (the pure parts and the reader).

The R8 twin is ``test_studio_knowledge_link_r8.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from acb_common import studio_knowledge_link as kl

PID = str(uuid.uuid4())

BODY = """# Manual

Intro.

## Torque

Tighten to 12 Nm.

```
## not a heading
```

### Detail

Use the blue tool.

## Safety

Wear gloves.
"""


def test_a_good_link_parses_and_trims() -> None:
    link = kl.parse_link({"page_id": PID.upper(), "heading": "  Torque ", "title": " Manual "})
    assert (link.page_id, link.heading, link.title) == (PID, "Torque", "Manual")


@pytest.mark.parametrize("doc", [
    [], "x", {"page_id": "nope", "title": "t"}, {"page_id": PID, "title": " "},
    {"page_id": PID, "title": "t", "extra": 1}, {"page_id": PID},
    {"page_id": PID, "title": "x" * 201}, {"page_id": PID, "title": "t", "heading": "h" * 201},
])
def test_a_bad_link_raises_one_sentence(doc) -> None:
    with pytest.raises(ValueError):
        kl.parse_link(doc)


def test_blank_heading_means_the_whole_page() -> None:
    assert kl.parse_link({"page_id": PID, "heading": "  ", "title": "t"}).heading is None
    assert kl.extract_section(BODY, None) == BODY


def test_section_runs_to_the_next_heading_of_same_or_higher_level() -> None:
    got = kl.extract_section(BODY, "torque")
    assert got.startswith("## Torque") and "Tighten to 12 Nm." in got
    assert "Use the blue tool." in got  # a deeper heading stays inside
    assert "## not a heading" in got  # a fence is not a heading
    assert "Wear gloves" not in got


def test_last_section_runs_to_the_end_and_a_missing_one_is_none() -> None:
    assert kl.extract_section(BODY, "Safety").endswith("Wear gloves.")
    assert kl.extract_section(BODY, "Nothing") is None
    assert kl.extract_section(BODY, "not a heading") is None


def test_the_cap_cuts_and_flags() -> None:
    text, cut = kl.cap_text("a" * (kl.MAX_TEXT_CHARS + 1))
    assert len(text) == kl.MAX_TEXT_CHARS and cut
    assert kl.cap_text("ab") == ("ab", False)


def test_unreadable_has_the_same_keys_as_readable() -> None:
    link = kl.parse_link({"page_id": PID, "title": "Stored"})
    a, b = kl.unreadable(link), kl.readable_result("T", "x", found=True)
    assert set(a) == set(b)
    assert a == {"readable": False, "title": "Stored", "text": None,
                 "section_found": False, "truncated": False}


def _run(coro):
    return asyncio.run(coro)


def test_reader_gives_section_when_the_door_returns_the_page(monkeypatch) -> None:
    from gateway.routes.knowledge import core
    from gateway.studio import knowledge_link as reader

    async def fake(db, user, page_id):
        assert page_id == PID
        return SimpleNamespace(title="Live title", body_md=BODY)

    monkeypatch.setattr(core, "load_page", fake)
    out = _run(reader.read_link(None, object(), {"page_id": PID, "heading": "Safety", "title": "Old"}))
    assert out["readable"] and out["title"] == "Live title" and "Wear gloves." in out["text"]


def test_reader_hides_text_when_the_door_returns_none(monkeypatch) -> None:
    from gateway.routes.knowledge import core
    from gateway.studio import knowledge_link as reader

    async def fake(db, user, page_id):
        return None

    monkeypatch.setattr(core, "load_page", fake)
    out = _run(reader.read_link(None, object(), {"page_id": PID, "title": "Old"}))
    assert out["readable"] is False and out["title"] == "Old" and out["text"] is None


def test_reader_refuses_a_bad_link() -> None:
    from gateway.studio import knowledge_link as reader

    with pytest.raises(ValueError):
        _run(reader.read_link(None, object(), {"page_id": "x", "title": "t"}))


def test_reader_adds_no_connection_or_second_visibility_check() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/studio"
           / "knowledge_link.py").read_text(encoding="utf-8")
    for banned in ("kb_pages", "tenant_session", "create_engine", "get_db", "text("):
        assert banned not in src.split('"""', 2)[2], banned
