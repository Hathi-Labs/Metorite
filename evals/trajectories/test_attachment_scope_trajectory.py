"""Golden trajectory: a chat attachment reads only in its own thread (H-229).

D85 took ``code_task`` from every shared agent, and that tool was how
projects-assistant read a member's ``.docx`` on production. ``read_attachment``
gives the flow back by pure parsing. This trajectory locks the harness side
of it, offline, with no model and no database:

  1. The upload folder of a shared agent's tenant dir is the THREAD's own
     (``agent_paths.upload_dir_rel``), so two threads of one org never share
     a folder.
  2. A run of thread A reads the Word file that A attached, as text.
  3. A run of thread B, in the SAME tenant dir, gets "No file named ..." for
     the same name, for a path into A's folder and for a ``..`` climb.
  4. No step starts a process.

The unit fence is ``tests/unit/test_read_attachment.py`` (the parsers, the
caps, the R8 store and the real executor). This file is the CI-blocking
golden path of root ``AGENTS.md`` harness rule 3.
"""
from __future__ import annotations

import asyncio
import io
import subprocess
import zipfile

import pytest
from acb_skills.agent_paths import (
    ensure_state_dir,
    tenant_instance,
    thread_slug,
    upload_dir_rel,
)
from acb_skills.attachment_tools import read_attachment
from acb_skills.write_artifact import bind_artifact_context

ORG = "org-trajectory-h229"
AGENT = "agent-trajectory-h229"
KEY = tenant_instance(ORG)
THREAD_A, THREAD_B = "thread-a-trajectory", "thread-b-trajectory"
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _word(text: str) -> bytes:
    """The smallest .docx that Word opens: one paragraph."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {_W}><w:body><w:p><w:r>'
            f"<w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def _run(ws, thread: str, name: str) -> str:
    async def _go() -> str:
        bind_artifact_context(session_id=thread, agent_name=AGENT, run_id=f"r-{thread}",
                              workspace_root=str(ws), instance=KEY)
        return await read_attachment(name)

    return asyncio.run(_go())


def test_an_attachment_reads_in_its_own_thread_and_in_no_other(tmp_path, monkeypatch) -> None:
    from acb_common import get_settings

    started: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append("Popen"))
    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    ws = ensure_state_dir(AGENT, KEY)

    # 1. Two threads, two folders.
    folder_a, folder_b = upload_dir_rel(KEY, THREAD_A), upload_dir_rel(KEY, THREAD_B)
    assert folder_a == f"inputs/{thread_slug(THREAD_A)}" and folder_a != folder_b

    # The member of thread A attaches a Word file.
    (ws / folder_a).mkdir(parents=True)
    (ws / folder_a / "brief.docx").write_bytes(_word("The pilot ships on 1 November"))

    # 2. A run of thread A reads it as text.
    own = _run(ws, THREAD_A, "brief.docx")
    assert own.startswith("Attachment: brief.docx (Word document, 1 paragraph read)")
    assert "The pilot ships on 1 November" in own

    # 3. A run of thread B, in the same tenant dir, reads nothing of it.
    for name in ("brief.docx", f"{folder_a}/brief.docx", f"../{thread_slug(THREAD_A)}/brief.docx"):
        other = _run(ws, THREAD_B, name)
        assert "The pilot ships" not in other, name
        assert other.startswith("No file named brief.docx was attached in this chat"), other

    # 4. No step started a process.
    assert started == []


@pytest.mark.parametrize("thread", ["", None])
def test_a_run_with_no_thread_reads_nothing(tmp_path, monkeypatch, thread) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    ws = ensure_state_dir(AGENT, KEY)
    assert _run(ws, thread, "brief.docx").startswith("This chat has no workspace")
