"""H-201 part 2 — the email attach and import read only the sender's own
workspace (``projects_ai_chat.md`` §21.14).

``load_artifact_attachments`` (the send and draft paths) and
``POST /email/artifacts/import`` took ``agent`` and ``path`` from the request.
They resolved the agent through ``_agent_workspace_dir``, which gives a
shared agent's one clone, and they checked containment with ``startswith``.
So a member could mail out, or copy into their own folder, the run output of
another org, a ``.env`` or a ``.git/config``, and a sibling folder whose name
starts with the same prefix.

Now both resolve only through ``workspace._member_agent_workspace``. The path
goes through ``_safe_resolve``, and ``_is_blocked_path`` refuses secret names.
No database is involved: both read the disk only.

Mutations this suite catches (R7):

* the attach source back to ``_agent_workspace_dir``: the shared-agent test
  fails;
* the import source back to ``_agent_workspace_dir``: the import test fails;
* ``_is_blocked_path`` removed from the attach or the import: the secret
  tests fail;
* ``_safe_resolve`` back to a ``startswith`` check in the attach or the
  import: the sibling-prefix tests fail.

H-201 part 3 (§21.15) adds two rules and their mutations:

* a refused attach ref FAILS the send with 422. The mutation "skip the
  ref, as before" fails four attach tests;
* a rule action (``actions._load_action_attachments``) reads through the
  same three calls. The mutations "source back to ``_agent_workspace_dir``",
  "``_is_blocked_path`` removed" and "``_safe_resolve`` back to
  ``startswith``" each fail one action test.

Run::

    uv run pytest tests/unit/test_h201_email_artifact_sources.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from tests.unit.test_chat_write_under_rls import _ALICE, _CAROL, _user

_EA = "email-assistant"
_P, _S = "agent-h201p", "agent-h201s"
_ORG_A_SECRET = "ORG-A RUN OUTPUT"


def _cfg(instancing: str) -> str:
    return json.dumps({"sharing": {"instancing": instancing}})


class _Disk:
    def __init__(self, base: Path) -> None:
        from acb_skills.agent_paths import ensure_state_dir

        repos = base / "agents" / "repos"
        for name, inst in ((_EA, "personal"), (_P, "personal"), (_S, "shared")):
            (repos / name).mkdir(parents=True)
            (repos / name / "config.json").write_text(_cfg(inst), encoding="utf-8")
        (repos / _S / "outputs").mkdir()
        (repos / _S / "outputs" / "org-a-run.md").write_text(_ORG_A_SECRET, encoding="utf-8")
        (repos / _S / ".env").write_text("DATABASE_URL=x", encoding="utf-8")
        self.repos = repos
        self.alice_ea = ensure_state_dir(_EA, f"u:{_ALICE}")
        self.alice_p = ensure_state_dir(_P, f"u:{_ALICE}")
        self.carol_ea = ensure_state_dir(_EA, f"u:{_CAROL}")
        (self.alice_ea / "outputs").mkdir()
        (self.alice_ea / "outputs" / "quote.md").write_text("QUOTE", encoding="utf-8")
        (self.alice_ea / ".env").write_text("SECRET=1", encoding="utf-8")
        (self.alice_ea / ".git").mkdir()
        (self.alice_ea / ".git" / "config").write_text("[remote] token", encoding="utf-8")
        # Secrets below a folder. ``_safe_resolve`` strips only a LEADING dot,
        # so only the blocked-path rule stops these two.
        (self.alice_ea / "outputs" / ".env").write_text("SECRET=2", encoding="utf-8")
        (self.alice_ea / "outputs" / "client_secret.json").write_text("{}", encoding="utf-8")
        (self.alice_p / "outputs").mkdir()
        (self.alice_p / "outputs" / "alice.md").write_text("ALICE", encoding="utf-8")
        # A sibling of Alice's folder whose name starts with the same prefix.
        self.sibling = self.alice_ea.parent / (self.alice_ea.name + "X")
        self.sibling.mkdir()
        (self.sibling / "stolen.md").write_text("SIBLING", encoding="utf-8")
        self.sibling_rel = f"outputs/../../{self.sibling.name}/stolen.md"
        # The same file by absolute path. It has no ``..`` segment, so only
        # the containment check (not the blocked-path rule) can stop it.
        self.sibling_abs = (self.sibling / "stolen.md").as_posix()


@pytest.fixture
def disk(tmp_path, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    return _Disk(tmp_path)


def _attach(refs: list[tuple[str | None, str]], email: str) -> list[dict]:
    from gateway.routes.email.transport.send import (
        ArtifactAttachment,
        load_artifact_attachments,
    )

    return load_artifact_attachments(
        [ArtifactAttachment(agent=a, path=p) for a, p in refs], email)


def _attach_status(refs: list[tuple[str | None, str]], email: str) -> int:
    """422 when the send is refused, else 200. H-201 part 3: fail closed."""
    try:
        _attach(refs, email)
    except HTTPException as exc:
        return exc.status_code
    return 200


def _import(agent: str, path: str, email: str, org: str = "org-a") -> dict:
    from gateway.routes.email.transport.send import ImportArtifactRequest, import_artifact

    return asyncio.run(import_artifact(
        ImportArtifactRequest(source_agent=agent, source_path=path), user=_user(email, org)))


def _import_status(agent: str, path: str, email: str) -> int:
    try:
        _import(agent, path, email)
    except HTTPException as exc:
        return exc.status_code
    return 200


# ── The attach (send and draft) ─────────────────────────────────────────────

def test_the_attach_reads_the_senders_own_file(disk) -> None:
    got = _attach([(None, "outputs/quote.md"), (_P, "outputs/alice.md")], _ALICE)
    assert [(a["filename"], a["content"]) for a in got] == [
        ("quote.md", b"QUOTE"), ("alice.md", b"ALICE")]
    # Carol names the same paths. Her own folders do not hold them, so the
    # send is refused rather than sent without the files.
    assert _attach_status([(None, "outputs/quote.md")], _CAROL) == 422
    assert _attach_status([(_P, "outputs/alice.md")], _CAROL) == 422


def test_the_attach_refuses_a_shared_agent(disk) -> None:
    for email in (_ALICE, _CAROL):
        for ref in ((_S, "outputs/org-a-run.md"), (_S, ".env")):
            assert _attach_status([ref], email) == 422, (email, ref)


def test_the_attach_refuses_a_secret(disk) -> None:
    for rel in (".env", ".git/config", "outputs/../.env", "outputs/.env",
                "outputs/client_secret.json"):
        assert _attach_status([(None, rel)], _ALICE) == 422, rel


def test_one_refused_ref_fails_the_whole_send(disk) -> None:
    """H-201 part 3, the P1 of the part 2 review. A good ref and a refused
    ref together fail the send. Nothing is sent without the file."""
    assert _attach_status(
        [(None, "outputs/quote.md"), (_S, "outputs/org-a-run.md")], _ALICE) == 422
    assert _attach_status([(None, "")], _ALICE) == 422
    assert _attach([], _ALICE) == []


def test_the_attach_refuses_an_escape_and_a_sibling_prefix(disk) -> None:
    for rel in (disk.sibling_rel, disk.sibling_abs,
                "../../../repos/agent-h201s/outputs/org-a-run.md",
                f"outputs/../../../../repos/{_S}/outputs/org-a-run.md"):
        assert _attach_status([(None, rel)], _ALICE) == 422, rel


# ── POST /email/artifacts/import ────────────────────────────────────────────

def test_the_import_copies_the_callers_own_file(disk) -> None:
    got = _import(_P, "outputs/alice.md", _ALICE)
    assert got == {"path": "agent-data/alice.md", "name": "alice.md"}
    assert (disk.alice_ea / "agent-data" / "alice.md").read_text(encoding="utf-8") == "ALICE"
    assert not (disk.carol_ea / "agent-data").exists()


def test_the_import_refuses_a_shared_agent(disk) -> None:
    for email in (_ALICE, _CAROL):
        assert _import_status(_S, "outputs/org-a-run.md", email) == 404
    assert not (disk.alice_ea / "agent-data").exists()


def test_the_import_refuses_a_secret(disk) -> None:
    for rel in (".env", ".git/config", "outputs/.env", "outputs/client_secret.json"):
        assert _import_status(_EA, rel, _ALICE) == 404, rel
    assert not (disk.alice_ea / "agent-data").exists()


def test_the_import_refuses_an_escape_and_a_sibling_prefix(disk) -> None:
    for rel in (disk.sibling_rel, disk.sibling_abs,
                f"outputs/../../../../repos/{_S}/outputs/org-a-run.md"):
        assert _import_status(_EA, rel, _ALICE) == 404, rel
    assert not (disk.alice_ea / "agent-data").exists()


# ── A rule action's attachments (email automation) ──────────────────────────

def _action(paths: list[str], email: str) -> list[dict]:
    from gateway.routes.email.automation.actions import _load_action_attachments

    return _load_action_attachments(
        {"attachments": [{"path": p} for p in paths]}, email)


def test_a_rule_action_attaches_only_the_members_own_file(disk) -> None:
    got = _action(["outputs/quote.md"], _ALICE)
    assert [(a["filename"], a["content"]) for a in got] == [("quote.md", b"QUOTE")]
    assert _action(["outputs/quote.md"], _CAROL) == []


def test_a_rule_action_with_no_member_reads_no_clone(disk) -> None:
    """The old source, ``_agent_workspace_dir``, gave the clone of
    ``email-assistant`` to a rule with no member."""
    clone_out = disk.repos / _EA / "outputs"
    clone_out.mkdir()
    (clone_out / "quote.md").write_text("CLONE", encoding="utf-8")
    for nobody in ("", "default"):
        assert _action(["outputs/quote.md"], nobody) == []


def test_a_rule_action_refuses_a_secret(disk) -> None:
    for rel in (".env", ".git/config", "outputs/.env", "outputs/client_secret.json"):
        assert _action([rel], _ALICE) == [], rel


def test_a_rule_action_refuses_an_escape_and_a_sibling_prefix(disk) -> None:
    for rel in (disk.sibling_rel, disk.sibling_abs,
                f"outputs/../../../../repos/{_S}/outputs/org-a-run.md"):
        assert _action([rel], _ALICE) == [], rel
