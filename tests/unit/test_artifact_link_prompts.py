"""The file tools tell the model to name a file, and never to paste its link.

Owner report, 2026-10-16: "when I'm asking to generate a report and it
generates a Markdown file, you also give the link to the Markdown file itself.
That opens up in just plain text, in unformatted Markdown."

The cause in the prompt: ``write_artifact`` said "embed the returned
``download_url`` in your text response", ``share_artifact`` said "the
returned ``download_url`` is the link", and a delegated run was told "Give
that chat the download_url". The addendum said the opposite. The chat
already draws a card for each file, so a pasted link is a second way in, and
it opened the raw file.

The fix keeps ``download_url`` in each result: a delegated run and a share
to another chat still read it. Only the instruction changes. The schema
cost of both floor tools went down, and ``test_tool_schema_diet.py`` holds
the new ceilings.

The frontend half (a pasted link opens the viewer) is fenced by
``workbench/control_plane/src/components/artifactLink.test.ts``.
"""
from __future__ import annotations

import importlib
import inspect
import re

import pytest
from acb_skills import addendum as ad

# The package exports the FUNCTION ``write_artifact`` under the module's name,
# so ``from acb_skills import write_artifact`` gives the function.
wa = importlib.import_module("acb_skills.write_artifact")

#: Words that tell the model to put the link in its reply.
_PASTE_THE_LINK = re.compile(
    r"embed the returned|is the link|give that chat the download_url"
    r"|download_url\W+in your (text|answer|reply)",
    re.IGNORECASE,
)

#: Words that tell the model to name the file and paste no link.
_PASTE_NO_LINK = re.compile(r"paste no link|do not paste|do NOT paste", re.IGNORECASE)


def _doc(fn: object) -> str:
    return inspect.getdoc(fn) or ""


@pytest.mark.parametrize("tool", [wa.write_artifact, wa.share_artifact])
def test_a_floor_file_tool_says_paste_no_link(tool) -> None:
    doc = _doc(tool)
    assert not _PASTE_THE_LINK.search(doc), (
        f"{tool.__name__}: its docstring tells the model to paste the link. "
        "The chat already shows a card, and a pasted link opened raw Markdown."
    )
    assert _PASTE_NO_LINK.search(doc), (
        f"{tool.__name__}: its docstring must tell the model to paste no link"
    )


def test_a_delegated_run_is_told_to_name_the_file() -> None:
    note = wa._DELIVERED_NOTE
    assert not _PASTE_THE_LINK.search(note), note
    assert _PASTE_NO_LINK.search(note), note


def test_the_addendum_agrees_with_the_tools() -> None:
    text = ad.render_injected_tools_addendum()
    assert not _PASTE_THE_LINK.search(text)
    lines = [
        line for line in text.splitlines()
        if line.startswith(("- **write_artifact(", "- **share_artifact("))
    ]
    assert len(lines) == 2, "the addendum lost a write_artifact or share_artifact line"
    for line in lines:
        assert _PASTE_NO_LINK.search(line), line


def test_the_result_still_carries_download_url() -> None:
    """A delegated run and a share to another chat read it, so it stays."""
    assert "download_url" in _doc(wa.write_artifact)
    assert "download_url" in _doc(wa.share_artifact)
