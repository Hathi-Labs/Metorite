"""WS43-F31 — the runtime-label fence (WS-43n, D92).

D92 (2026-10-07) says that every agent runs on MAF. The owning spec is
``project-docs/specs/maf_coding_engine.md`` §17.6. WS43-F15
(``test_no_copilot_sdk.py``) reads the Python code. This test reads the
LABELS, because a label alone can send an agent down the Copilot path: the
sub-agent path of the executor reads only the label.

The four places that can name a runtime
---------------------------------------
1. each agent ``config.json`` under ``apps/agents/``, key ``runtime``,
2. the root ``config.json`` (the root ``metorite`` agent), key ``runtime``,
3. ``apps/services/gateway/agents.json``, key ``agent_runtime``,
4. ``_AGENT_REGISTRY`` in ``apps/services/gateway/gateway/routes/agent.py``,
   key ``agent_runtime``.

⚠️ The label of task-manager and app-builder is NOT in their
``config.json``, which says ``"maf"``. It is in ``_AGENT_REGISTRY``. So a
fence that reads only the ``config.json`` files passes today (§17.3).

Each place is read for BOTH keys, so a label under the other key counts too.
A value is a Copilot label when it contains ``copilot`` in any case. That
covers each alias that ``_normalize_runtime`` maps to ``github-copilot``.

``_AGENT_REGISTRY`` is read from the syntax tree, with ``ast.literal_eval``.
A registry that is not a literal FAILS the test, so a computed entry cannot
hide from it. When the gateway imports, the imported registry must agree.

The ratchet
-----------
``_ALLOWLIST`` names each Copilot label at build time, with its reason and
the slice that removes it. A Copilot label off the list fails the test. An
entry whose label is gone fails the test too, so the PR that removes a label
deletes its entry. ``_AT_BUILD`` is the frozen list of build time, and the
allowlist must stay inside it. So the list only shrinks.

Mutations this file catches (R7), each run red before the change:

* a new agent ``config.json`` says ``"runtime": "github-copilot"`` ->
  ``test_no_copilot_label_off_the_allowlist`` (and its synthetic twin
  ``test_a_new_copilot_config_is_caught``);
* an allowlist entry is added, or its label is removed and the entry stays ->
  ``test_the_allowlist_only_shrinks``, ``test_allowlist_has_no_stale_entries``;
* the fence stops reading one of the four places ->
  ``test_each_place_is_read``;
* a registry entry is built at run time -> ``test_the_registry_is_a_literal``
  and ``test_the_imported_registry_agrees``.

Hermetic: it reads files only, and no SQL runs, so R8 binds nothing here.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

_AGENTS_DIR = "apps/agents"
_ROOT_CONFIG = "config.json"
_AGENTS_JSON = "apps/services/gateway/agents.json"
_REGISTRY_FILE = "apps/services/gateway/gateway/routes/agent.py"

#: The keys that name a runtime. Each place is read for both.
_RUNTIME_KEYS = ("runtime", "agent_runtime")

#: Place labels, used in the allowlist keys and the failure text.
P_AGENT_CONFIG = "agent config.json"
P_ROOT_CONFIG = "root config.json"
P_AGENTS_JSON = "gateway/agents.json"
P_REGISTRY = "_AGENT_REGISTRY"

#: (place, agent) -> (why it is still Copilot, the slice that removes it).
#: Delete an entry in the PR that removes its label. Never add one.
_ALLOWLIST: dict[tuple[str, str], tuple[str, str]] = {
    (P_REGISTRY, "task-manager"): (
        "Held on the Copilot path: its confirm turn needs the tool output of "
        "the turn before (H-215). Its factory still builds a Copilot agent",
        "WS-8i, after the soak of WS-43t2",
    ),
    (P_REGISTRY, "app-builder"): (
        "Its factory still builds a Copilot agent, for the native file and "
        "shell tools of the Copilot CLI",
        "WS-43h",
    ),
    (P_ROOT_CONFIG, "metorite"): (
        "The root self-anneal agent still builds a Copilot agent "
        "(root agents.py)",
        "WS-43m",
    ),
    (P_AGENTS_JSON, "agent-sales-assistant"): (
        "Lives in FracktalWorks/agent-sales-assistant, which still builds a "
        "Copilot agent. Its own repo must change first (WS43-G11). The "
        "label stays until then, because the sub-agent path reads it",
        "WS-43q, after WS43-G11",
    ),
}

#: The allowlist at build time (WS-43n, 2026-10-08). Frozen on purpose: a
#: set derived from the current allowlist would agree with whatever it says.
_AT_BUILD: frozenset[tuple[str, str]] = frozenset(
    {
        (P_REGISTRY, "task-manager"),
        (P_REGISTRY, "app-builder"),
        (P_ROOT_CONFIG, "metorite"),
        (P_AGENTS_JSON, "agent-sales-assistant"),
    }
)

_SLICE_RE = re.compile(r"\bWS-\d+[a-z]*\d*\b")


def _is_copilot_label(value: object) -> bool:
    return isinstance(value, str) and "copilot" in value.lower()


def _labels_of(entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return []
    return [entry[k] for k in _RUNTIME_KEYS if isinstance(entry.get(k), str)]


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry_literal(source: str) -> list[dict]:
    """``_AGENT_REGISTRY`` from *source*, read as a literal. Raises if not."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(getattr(t, "id", None) == "_AGENT_REGISTRY" for t in targets):
            assert node.value is not None
            value = ast.literal_eval(node.value)
            assert isinstance(value, list), "_AGENT_REGISTRY is not a list"
            return value
    raise AssertionError("_AGENT_REGISTRY is not assigned at module level")


def _scan(root: Path) -> dict[str, dict[str, list[str]]]:
    """Every runtime label under *root*, as ``{place: {agent: [labels]}}``."""
    found: dict[str, dict[str, list[str]]] = {
        P_AGENT_CONFIG: {},
        P_ROOT_CONFIG: {},
        P_AGENTS_JSON: {},
        P_REGISTRY: {},
    }
    for cfg in sorted((root / _AGENTS_DIR).glob("*/config.json")):
        data = _read_json(cfg)
        name = data.get("name") if isinstance(data, dict) else None
        found[P_AGENT_CONFIG][str(name or cfg.parent.name)] = _labels_of(data)

    root_cfg = root / _ROOT_CONFIG
    if root_cfg.is_file():
        data = _read_json(root_cfg)
        name = data.get("name") if isinstance(data, dict) else None
        found[P_ROOT_CONFIG][str(name or "<root>")] = _labels_of(data)

    agents_json = root / _AGENTS_JSON
    if agents_json.is_file():
        data = _read_json(agents_json)
        assert isinstance(data, list), f"{_AGENTS_JSON} is not a list"
        for i, entry in enumerate(data):
            name = entry.get("name") if isinstance(entry, dict) else None
            found[P_AGENTS_JSON][str(name or f"<entry {i}>")] = _labels_of(entry)

    registry = root / _REGISTRY_FILE
    if registry.is_file():
        for i, entry in enumerate(_registry_literal(registry.read_text(encoding="utf-8"))):
            name = entry.get("name") if isinstance(entry, dict) else None
            found[P_REGISTRY][str(name or f"<entry {i}>")] = _labels_of(entry)
    return found


def _copilot_labels(found: dict[str, dict[str, list[str]]]) -> dict[tuple[str, str], list[str]]:
    return {
        (place, agent): [v for v in labels if _is_copilot_label(v)]
        for place, agents in found.items()
        for agent, labels in agents.items()
        if any(_is_copilot_label(v) for v in labels)
    }


# ── The fence on the tree ───────────────────────────────────────────────────


def test_each_place_is_read() -> None:
    found = _scan(_REPO)
    # Eight first-party agents carry a config.json at build time. Fewer means
    # the glob stopped matching, not that the agents went.
    assert len(found[P_AGENT_CONFIG]) >= 8, found[P_AGENT_CONFIG]
    assert "metorite" in found[P_ROOT_CONFIG], found[P_ROOT_CONFIG]
    assert found[P_AGENTS_JSON], f"nothing read from {_AGENTS_JSON}"
    assert len(found[P_REGISTRY]) >= 8, found[P_REGISTRY]
    # Each place yields a label, so the key read is the right one.
    for place, agents in found.items():
        assert any(agents.values()), f"no runtime label read from {place}"


def test_no_copilot_label_off_the_allowlist() -> None:
    hits = _copilot_labels(_scan(_REPO))
    new = {k: v for k, v in sorted(hits.items()) if k not in _ALLOWLIST}
    assert not new, (
        "D92: every agent runs on MAF (maf_coding_engine.md §17). These "
        "agents carry a Copilot runtime label off the WS43-F31 allowlist:\n  "
        + "\n  ".join(f"{place} / {agent}: {labels}" for (place, agent), labels in new.items())
        + "\n\nBuild an agent_framework.Agent and label it \"maf\". Do not "
        "add the agent to _ALLOWLIST."
    )


def test_allowlist_has_no_stale_entries() -> None:
    hits = _copilot_labels(_scan(_REPO))
    stale = sorted(k for k in _ALLOWLIST if k not in hits)
    assert not stale, (
        "These _ALLOWLIST entries no longer carry a Copilot label. Delete "
        "them in this PR, so the list only shrinks:\n  "
        + "\n  ".join(f"{place} / {agent}" for place, agent in stale)
    )


def test_the_allowlist_only_shrinks() -> None:
    added = sorted(set(_ALLOWLIST) - _AT_BUILD)
    assert not added, (
        "WS43-F31 only shrinks. These entries were not on the list at build "
        f"time: {added}. Build a MAF agent instead."
    )


@pytest.mark.parametrize("key", sorted(_ALLOWLIST))
def test_each_entry_has_a_reason_and_a_slice(key: tuple[str, str]) -> None:
    reason, removed_by = _ALLOWLIST[key]
    assert reason.strip(), f"{key} has no reason"
    assert _SLICE_RE.search(removed_by), f"{key} names no slice: {removed_by!r}"


def test_the_registry_is_a_literal() -> None:
    source = (_REPO / _REGISTRY_FILE).read_text(encoding="utf-8")
    entries = _registry_literal(source)
    assert all(isinstance(e, dict) and e.get("name") for e in entries)


def test_the_imported_registry_agrees() -> None:
    """The registry that runs equals the one this fence reads."""
    routes_agent = pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed in this environment",
    )
    on_disk = _scan(_REPO)[P_REGISTRY]
    # Only the names are compared. list_agents writes the declared runtime
    # into these dicts at run time, so another test in the same process can
    # change an imported label. The literal is the label of record.
    imported = {e["name"] for e in routes_agent._AGENT_REGISTRY}
    assert imported == set(on_disk), (
        "_AGENT_REGISTRY gains or loses an entry at import time, where "
        "WS43-F31 cannot read its label"
    )


# ── The scan itself, on a synthetic tree ────────────────────────────────────


def _write(root: Path, rel: str, data: object) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")


def _a_clean_tree(root: Path) -> None:
    _write(root, "apps/agents/agent-a/config.json", {"name": "a", "runtime": "maf"})
    _write(root, "config.json", {"name": "metorite", "runtime": "maf"})
    _write(root, _AGENTS_JSON, [{"name": "x", "agent_runtime": "maf"}])
    _write(
        root,
        _REGISTRY_FILE,
        '_AGENT_REGISTRY: list[dict] = [\n'
        '    {"name": "t", "description": ("one " "two"), "agent_runtime": "maf"},\n'
        ']\n',
    )


def test_a_clean_tree_has_no_copilot_label(tmp_path: Path) -> None:
    _a_clean_tree(tmp_path)
    assert _copilot_labels(_scan(tmp_path)) == {}


def test_a_new_copilot_config_is_caught(tmp_path: Path) -> None:
    _a_clean_tree(tmp_path)
    _write(
        tmp_path,
        "apps/agents/agent-new/config.json",
        {"name": "new-agent", "runtime": "github-copilot"},
    )
    assert _copilot_labels(_scan(tmp_path)) == {
        (P_AGENT_CONFIG, "new-agent"): ["github-copilot"],
    }


@pytest.mark.parametrize(
    ("rel", "data", "key"),
    [
        ("config.json", {"name": "metorite", "runtime": "Copilot"}, (P_ROOT_CONFIG, "metorite")),
        (
            _AGENTS_JSON,
            [{"name": "x", "agent_runtime": "github_copilot"}],
            (P_AGENTS_JSON, "x"),
        ),
        # The other key counts too.
        (
            "apps/agents/agent-a/config.json",
            {"name": "a", "runtime": "maf", "agent_runtime": "copilot-sdk"},
            (P_AGENT_CONFIG, "a"),
        ),
        (
            _REGISTRY_FILE,
            '_AGENT_REGISTRY = [{"name": "t", "agent_runtime": "github-copilot"}]\n',
            (P_REGISTRY, "t"),
        ),
    ],
)
def test_each_place_and_alias_is_caught(tmp_path: Path, rel: str, data: object, key) -> None:
    _a_clean_tree(tmp_path)
    _write(tmp_path, rel, data)
    assert key in _copilot_labels(_scan(tmp_path))


def test_a_computed_registry_fails(tmp_path: Path) -> None:
    _a_clean_tree(tmp_path)
    _write(
        tmp_path,
        _REGISTRY_FILE,
        'RT = "github-copilot"\n_AGENT_REGISTRY = [{"name": "t", "agent_runtime": RT}]\n',
    )
    with pytest.raises(ValueError):
        _scan(tmp_path)
