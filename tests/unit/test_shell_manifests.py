"""Fence for the shell manifests: one source for the command bar's jobs (WS-PKG, PKG-1).

Spec: ``project-docs/specs/package_host.md`` §3.3 and §4. The pinned values
below are the two hand-kept lists as they stood before the slice, so a job
cannot change its id, label, link, gate or order by accident.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from acb_common.app_manifests import shell_manifest
from acb_common.app_manifests.shell_manifest import (
    ShellManifestError,
    load_gates,
    load_jobs,
    load_vocabulary,
)
from gateway.routes.shell import intent

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "workbench/control_plane/src/lib/shell/jobs.generated.ts"
VOCABULARY = ROOT / "workbench/control_plane/src/lib/shell/vocabulary.generated.ts"
ME = ROOT / "apps/services/gateway/gateway/routes/admin/me.py"
REGISTRY = ROOT / "workbench/control_plane/src/lib/shell/registry.ts"
INTENT = ROOT / "apps/services/gateway/gateway/routes/shell/intent.py"

ORDER = [
    "capture", "compose", "plan-day", "new-project", "new-chat", "invite", "new-lead",
    "new-deal", "log-call", "find-record", "add-mcp", "review-integrations",
    "connect-agent", "find-person", "find-knowledge", "write-page", "add-product",
    "add-topic", "edit-profile", "appearance", "whatsapp-reply", "find-chat-file",
    "review-approvals",
]

#: (id, label, meaning, feature, href, fields, admin, (fallback_feature, fallback_href))
PRE_SLICE_PY = [
    ('capture', 'New task', 'add a task, a to-do or a reminder for themselves', 'projects', '/projects?do=capture', {'title': "the task itself, in the member's own words, without 'remind me to'"}, False, ('tasks', '/tasks?do=capture')),
    ('compose', 'Write an email', 'write, send or reply to an email', 'email', '/email?do=compose', {'to': 'who the email goes to: a name or an email address', 'subject': 'a short subject line for the email'}, False, (None, None)),
    ('plan-day', 'Plan my day', 'plan, schedule or look at their day, their calendar or their agenda', 'tasks', '/calendar', {}, False, (None, None)),
    ('new-project', 'New space', 'start a new space, project or department board in Projects', 'projects', '/projects?do=new-project', {}, False, (None, None)),
    ('new-chat', 'New chat', 'start a new conversation with the AI assistant', 'chat', '/chat?do=new-chat', {}, False, (None, None)),
    ('invite', 'Invite a member', 'invite a new person to join the organization', None, '/settings/organization?do=invite', {}, True, (None, None)),
    ('new-lead', 'New lead', 'add a new sales lead or prospect to the CRM', 'crm', '/crm?do=new-lead', {}, False, (None, None)),
    ('new-deal', 'New deal', 'add a new deal or sales opportunity to the CRM', 'crm', '/crm?do=new-deal', {}, False, (None, None)),
    ('log-call', 'Log a call', 'log a phone call with a lead, a contact or a customer in the CRM', 'crm', '/crm?do=log-call', {}, False, (None, None)),
    ('find-record', 'Find a record', 'find a lead, a deal, a contact or a company in the CRM', 'crm', '/crm?do=find-record', {}, False, (None, None)),
    ('add-mcp', 'Add an MCP server', 'connect a new MCP server, a tool source for the agents', 'integrations', '/integrations?do=add-mcp', {}, False, (None, None)),
    ('review-integrations', 'Review connections', 'review the connections the company has made and their health', 'integrations', '/integrations', {}, False, (None, None)),
    ('connect-agent', 'Connect an AI agent', 'connect an outside AI agent such as Claude Code to Metorite, or see and revoke their agents', None, '/integrations?view=agents', {}, False, (None, None)),
    ('find-person', 'Find a colleague', 'find a colleague, or who knows or does something', 'people', '/people', {}, False, (None, None)),
    ('find-knowledge', 'Find in Knowledge', 'find a page, a policy, a handbook entry or a how-to the company wrote down', 'knowledge', '/knowledge/search', {}, False, (None, None)),
    ('write-page', 'Write a page', 'write a new page of company knowledge, such as a policy, a handbook entry or a how-to', 'knowledge', '/knowledge?do=write-page', {}, False, (None, None)),
    ('add-product', 'Add a product', 'add a product of the company to the Knowledge directory, with a name and other names', 'knowledge', '/knowledge?do=add-product', {}, False, (None, None)),
    ('add-topic', 'Add a topic', 'add a topic to the Knowledge directory, with a name and other names', 'knowledge', '/knowledge?do=add-topic', {}, False, (None, None)),
    ('edit-profile', 'Update my profile', 'change their own profile, skills, CV or working hours', None, '/people/me', {}, False, (None, None)),
    ('appearance', 'Change how Metorite looks', 'change the theme, dark or light mode, density or accent', None, '/settings/appearance', {}, False, (None, None)),
    ('whatsapp-reply', 'Reply on WhatsApp', 'answer the customers who wrote to the business on WhatsApp', 'whatsapp', '/whatsapp', {}, False, (None, None)),
    ('find-chat-file', 'Find a file from a chat', 'find a file, report or document the assistant made in a chat', 'artifacts', '/artifacts', {}, False, (None, None)),
    ('review-approvals', 'Review pending approvals', 'check and approve what the AI wants to send before it goes out', 'approvals', '/approvals', {}, False, (None, None)),
]

#: (id, label, words, app, href, icon, own feature, fallback)
PRE_SLICE_TS = [
    ('capture', 'New task', ['task', 'todo', 'to-do', 'capture', 'add', 'remind', 'reminder', 'note'], '/projects', '/projects?do=capture', 'CirclePlus', None, ('tasks', '/tasks?do=capture')),
    ('compose', 'Write an email', ['email', 'mail', 'compose', 'send', 'message', 'write'], '/email', '/email?do=compose', 'PenSquare', None, None),
    ('plan-day', 'Plan my day', ['calendar', 'schedule', 'plan', 'day', 'timebox', 'today', 'agenda'], '/calendar', '/calendar', 'CalendarClock', None, None),
    ('new-project', 'New space', ['space', 'project', 'workspace', 'department', 'board'], '/projects', '/projects?do=new-project', 'FolderPlus', None, None),
    ('new-chat', 'New chat', ['chat', 'conversation', 'assistant', 'ai', 'talk', 'thread'], '/chat', '/chat?do=new-chat', 'MessageSquarePlus', None, None),
    ('invite', 'Invite a member', ['invite', 'join', 'onboard', 'welcome', 'newcomer'], '/settings/organization', '/settings/organization?do=invite', 'UserPlus', None, None),
    ('new-lead', 'New lead', ['lead', 'prospect', 'crm', 'sales', 'enquiry', 'inquiry'], '/crm', '/crm?do=new-lead', 'UserPlus', None, None),
    ('new-deal', 'New deal', ['deal', 'deals', 'opportunity', 'pipeline', 'sale'], '/crm', '/crm?do=new-deal', 'Handshake', None, None),
    ('log-call', 'Log a call', ['call', 'phone', 'rang', 'dial', 'spoke', 'log'], '/crm', '/crm?do=log-call', 'PhoneCall', None, None),
    ('find-record', 'Find a record', ['record', 'records', 'company', 'companies', 'account', 'lookup'], '/crm', '/crm?do=find-record', 'Search', None, None),
    ('add-mcp', 'Add an MCP server', ['mcp', 'server', 'tool', 'tools', 'connect', 'integration', 'integrations'], '/integrations', '/integrations?do=add-mcp', 'Server', 'integrations', None),
    ('review-integrations', 'Review connections', ['connections', 'review', 'integrations', 'reconnect', 'health', 'status'], '/integrations', '/integrations', 'Plug', 'integrations', None),
    ('connect-agent', 'Connect an AI agent', ['agent', 'agents', 'claude', 'mcp', 'connect', 'cursor', 'ai', 'revoke'], '/integrations', '/integrations?view=agents', 'Bot', None, None),
    ('find-person', 'Find a colleague', ['people', 'person', 'colleague', 'directory', 'who', 'team', 'contact'], '/people', '/people', 'Users', None, None),
    ('find-knowledge', 'Find in Knowledge', ['knowledge', 'wiki', 'handbook', 'docs', 'manual', 'policy', 'policies', 'page'], '/knowledge', '/knowledge/search', 'FileSearch', None, None),
    ('write-page', 'Write a page', ['page', 'pages', 'article', 'wiki', 'knowledge', 'handbook'], '/knowledge', '/knowledge?do=write-page', 'FilePlus', None, None),
    ('add-product', 'Add a product', ['product', 'products', 'item', 'catalogue', 'catalog', 'knowledge', 'add', 'new'], '/knowledge', '/knowledge?do=add-product', 'Package', None, None),
    ('add-topic', 'Add a topic', ['topic', 'topics', 'subject', 'theme', 'knowledge', 'add', 'new'], '/knowledge', '/knowledge?do=add-topic', 'Tag', None, None),
    ('edit-profile', 'Update my profile', ['profile', 'skills', 'cv', 'resume', 'hours', 'me'], '/people/me', '/people/me', 'UserPen', None, None),
    ('appearance', 'Change how Metorite looks', ['appearance', 'theme', 'dark', 'light', 'colour', 'color', 'density', 'accent', 'sidebar'], '/settings/appearance', '/settings/appearance', 'Palette', None, None),
    ('whatsapp-reply', 'Reply on WhatsApp', ['whatsapp', 'wa', 'reply', 'customer', 'customers'], '/whatsapp', '/whatsapp', 'MessageCircle', None, None),
    ('find-chat-file', 'Find a file from a chat', ['file', 'files', 'artifact', 'artifacts', 'download', 'report', 'document', 'documents'], '/artifacts', '/artifacts', 'FolderOpen', None, None),
    ('review-approvals', 'Review pending approvals', ['approve', 'approval', 'approvals', 'pending', 'review', 'outgoing'], '/approvals', '/approvals', 'ShieldCheck', None, None),
]


def _generator():
    spec = importlib.util.spec_from_file_location("gen_shell_manifests", ROOT / "scripts/gen_shell_manifests.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_shell_manifests"] = module
    spec.loader.exec_module(module)
    return module


def _ts_ids(text: str) -> list[str]:
    return re.findall(r'^    id: "([^"]+)",$', text, re.M)


def _write(folder: Path, name: str, jobs: list[dict]) -> None:
    doc = {"studio": 1, "kind": "system", "id": name, "contributes": {"jobs": jobs}}
    (folder / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def _job(id_: str, position: int, **over) -> dict:
    base = {"id": id_, "label": "L", "position": position, "words": ["w"], "icon": "Bot",
            "app": "/x", "href": "/x", "meaning": "m"}
    base.update(over)
    return base


def test_generated_ts_is_current():
    want = _generator().render_ts(load_jobs())
    have = GENERATED.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert have == want, "jobs.generated.ts is stale: run `uv run python scripts/gen_shell_manifests.py`"


def test_job_order_and_ids_are_pinned():
    assert [j.id for j in intent.JOBS] == ORDER
    assert _ts_ids(GENERATED.read_text(encoding="utf-8")) == ORDER


def test_python_jobs_equal_the_pre_slice_values():
    got = [
        (j.id, j.label, j.meaning, j.feature, j.href, j.fields, j.admin,
         (j.fallback_feature, j.fallback_href))
        for j in intent.JOBS
    ]
    assert got == PRE_SLICE_PY


def test_ts_jobs_equal_the_pre_slice_values():
    text = GENERATED.read_text(encoding="utf-8")
    got = []
    for chunk in re.split(r"\n  \{\n", text)[1:]:
        def one(key: str, chunk=chunk):
            m = re.search(rf'^    {key}: "([^"]*)",$', chunk, re.M)
            return m.group(1) if m else None
        words = re.search(r"^    words: \[(.*)\],$", chunk, re.M).group(1)
        fb = re.search(r'fallback: \{ feature: "([^"]+)", href: "([^"]+)" \}', chunk)
        got.append((one("id"), one("label"), tuple(json.loads("[" + words + "]")), one("app"),
                    one("href"), one("icon"), one("feature"), fb.groups() if fb else None))
    want = [(i, lb, tuple(w), a, h, ic, f, fb) for (i, lb, w, a, h, ic, f, fb) in PRE_SLICE_TS]
    assert got == want


def test_loader_rejects_bad_files(tmp_path):
    def attempt(*jobs: dict):
        for p in tmp_path.glob("*.json"):
            p.unlink()
        _write(tmp_path, "a", list(jobs))
        return load_jobs(tmp_path)

    assert [j.id for j in attempt(_job("x", 10), _job("y", 20))] == ["x", "y"]
    with pytest.raises(ShellManifestError, match="duplicate job id"):
        attempt(_job("x", 10), _job("x", 20))
    with pytest.raises(ShellManifestError, match="duplicate job position"):
        attempt(_job("x", 10), _job("y", 10))
    with pytest.raises(ShellManifestError, match="unknown key"):
        attempt(_job("x", 10, surprise=1))
    with pytest.raises(ShellManifestError, match="words"):
        attempt(_job("x", 10, words=[]))
    bad = _job("x", 10)
    del bad["position"]
    with pytest.raises(ShellManifestError, match="missing key"):
        attempt(bad)


def test_no_hand_kept_job_list_remains():
    assert 'id: "capture"' not in REGISTRY.read_text(encoding="utf-8")
    assert 'Job("capture"' not in INTENT.read_text(encoding="utf-8")
    assert "GENERATED_JOBS" in REGISTRY.read_text(encoding="utf-8")


def test_manifest_files_ship():
    files = sorted(shell_manifest.SHELL_DIR.glob("*.json"))
    assert files, "no shell manifest files found"
    count = sum(
        len(json.loads(f.read_text(encoding="utf-8"))["contributes"].get("jobs", [])) for f in files
    )
    assert count == len(ORDER) == 23
    assert not (shell_manifest.SHELL_DIR / "__init__.py").exists()


def _vocab_write(folder: Path, name: str, contributes: dict) -> None:
    doc = {"studio": 1, "kind": "system", "id": name, "contributes": contributes}
    (folder / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_vocabulary_ts_is_current():
    want = _generator().render_vocabulary_ts(load_vocabulary())
    have = VOCABULARY.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert have == want, "vocabulary.generated.ts is stale: run `uv run python scripts/gen_shell_manifests.py`"


def test_vocabulary_counts():
    vocab = load_vocabulary()
    assert len(vocab.panes) == 23
    assert len(vocab.presets) == 8
    assert len(vocab.cards) == 5
    assert vocab.cards == ("needs", "today", "next", "team-pulse", "out-today")


def test_vocabulary_loader_rejects_bad_files(tmp_path):
    def attempt(**files: dict):
        for p in tmp_path.glob("*.json"):
            p.unlink()
        for name, contributes in files.items():
            _vocab_write(tmp_path, name, contributes)
        return load_vocabulary(tmp_path)

    ok = attempt(a={"nav": [{"href": "/a"}]}, home={"presets": [{"id": "p"}], "cards": [{"key": "c"}]})
    assert (ok.panes, ok.presets, ok.cards) == (("/a",), ("p",), ("c",))
    with pytest.raises(ShellManifestError, match="duplicate pane"):
        attempt(a={"nav": [{"href": "/a"}]}, b={"nav": [{"href": "/a"}]})
    with pytest.raises(ShellManifestError, match="only 'href'"):
        attempt(a={"nav": [{"href": "/a", "label": "A"}]})
    with pytest.raises(ShellManifestError, match=r"no '[?]'"):
        attempt(a={"nav": [{"href": "/a?do=x"}]})
    with pytest.raises(ShellManifestError, match=r"home.json only"):
        attempt(a={"presets": [{"id": "p"}]})
    with pytest.raises(ShellManifestError, match=r"home.json only"):
        attempt(a={"cards": [{"key": "c"}]})
    with pytest.raises(ShellManifestError, match="contributes must hold only"):
        attempt(a={})
    with pytest.raises(ShellManifestError, match="contributes must hold only"):
        attempt(a={"nav": [], "surprise": []})
    with pytest.raises(ShellManifestError, match="duplicate preset"):
        attempt(home={"presets": [{"id": "p"}, {"id": "p"}]})


def test_no_hand_kept_vocabulary_remains():
    me = ME.read_text(encoding="utf-8")
    assert "load_vocabulary()" in me
    assert '"founder"' not in me
    assert '"/tasks"' not in me
    assert '"team-pulse"' not in me


# --- PKG-3a: SCOPE_APPS and ALSO_SERVES ---------------------------------------

GATES_TS = VOCABULARY.with_name("gates.generated.ts")
SEARCH_PY = ROOT / "apps/services/gateway/gateway/routes/shell/search.py"
ASSISTANT_TS = VOCABULARY.with_name("assistant.ts")

#: The two maps as they stood before the slice. This copy is FROZEN.
FROZEN_SCOPE = {
    "/email": "email",
    "/projects": "tasks",
    "/tasks": "tasks",
    "/people": "people",
    "/crm": "crm",
    "/knowledge": "knowledge",
}
FROZEN_ALSO_SERVES = {"projects-assistant": ("/tasks", "/calendar")}


def test_gates_ts_is_current():
    want = _generator().render_gates_ts(load_gates())
    have = GATES_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert have == want, "gates.generated.ts is stale: run `uv run python scripts/gen_shell_manifests.py`"


def test_gates_equal_the_pre_slice_values():
    gates = load_gates()
    assert gates.scope == FROZEN_SCOPE
    assert gates.also_serves == FROZEN_ALSO_SERVES
    from gateway.routes.shell import search

    assert search.SCOPE_APPS == FROZEN_SCOPE


def test_gates_counts():
    gates = load_gates()
    assert len(gates.scope) == 6
    assert sum(len(h) for h in gates.also_serves.values()) == 2


def test_gates_loader_rejects_bad_files(tmp_path):
    def attempt(**files: dict):
        for p in tmp_path.glob("*.json"):
            p.unlink()
        for name, contributes in files.items():
            _vocab_write(tmp_path, name, contributes)
        return load_gates(tmp_path)

    s = {"href": "/a", "source": "a"}
    p = {"agent": "x", "href": "/a"}
    ok = attempt(a={"scope": [s], "also_serves": [p]})
    assert (ok.scope, ok.also_serves) == ({"/a": "a"}, {"x": ("/a",)})
    with pytest.raises(ShellManifestError, match="must hold exactly"):
        attempt(a={"scope": [{**s, "extra": "y"}]})
    with pytest.raises(ShellManifestError, match="must hold exactly"):
        attempt(a={"scope": [{"href": "/a"}]})
    with pytest.raises(ShellManifestError, match="must hold exactly"):
        attempt(a={"also_serves": [{"agent": "x"}]})
    with pytest.raises(ShellManifestError, match=r"no '[?]'"):
        attempt(a={"scope": [{"href": "/a?x=1", "source": "a"}]})
    with pytest.raises(ShellManifestError, match=r"no '[?]'"):
        attempt(a={"also_serves": [{"agent": "x", "href": "/a?x=1"}]})
    with pytest.raises(ShellManifestError, match=r"no '[?]'"):
        attempt(a={"scope": [{"href": "a", "source": "a"}]})
    with pytest.raises(ShellManifestError, match="duplicate scope href"):
        attempt(a={"scope": [s]}, b={"scope": [{"href": "/a", "source": "b"}]})
    with pytest.raises(ShellManifestError, match="duplicate also_serves pair"):
        attempt(a={"also_serves": [p, p]})


def test_no_hand_kept_shell_map_remains():
    import ast

    tree = ast.parse(SEARCH_PY.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "SCOPE_APPS":
                found = True
                assert not isinstance(node.value, ast.Dict), (
                    "SCOPE_APPS is a hand-kept dict literal: it must come from load_gates()"
                )
    assert found, "SCOPE_APPS moved out of search.py"
    ts = ASSISTANT_TS.read_text(encoding="utf-8")
    assert re.search(r"export const ALSO_SERVES[^=]*=\s*GENERATED_ALSO_SERVES\s*;", ts), (
        "ALSO_SERVES in assistant.ts must be GENERATED_ALSO_SERVES"
    )
    assert not re.search(r"export const ALSO_SERVES[^=]*=\s*\{", ts), (
        "ALSO_SERVES is a hand-kept object literal: it must come from gates.generated.ts"
    )


def test_eval_reads_the_same_also_serves():
    from evals.routing import shell

    assert shell.also_serves() == FROZEN_ALSO_SERVES
