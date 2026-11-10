"""One door makes a vector. KG-2, D128, R7.

Spec: ``project-docs/specs/company_context.md`` §12 (KG-2).

``gateway/kb_index/embed.py`` is the embed door. It asks the Router on
``tier-embed``, writes a ``usage_event`` row and draws no credit. An app that
needs a vector calls the door. It does not call a vendor, and it does not
call the Console itself.

What the test reads
-------------------
Every ``.py`` file under ``apps/`` and ``packages/``, outside a ``tests``
directory. It reads the syntax tree, so a comment or a docstring never trips
it. A CALL of ``aembedding`` or of ``embeddings_on_console`` outside
:data:`_ALLOWED` fails. A definition, an import and a mention do not count.

The allowed exceptions
----------------------
Each entry has a reason. The list must not grow without a cited reason.
``test_every_allowed_file_exists`` fails when an entry names a file that is
gone, so a moved call cannot hide behind a stale entry.

* the door itself,
* ``wa_embeddings.py``: WhatsApp is frozen (D115),
* ``capability.py``: the capability index. It moves in a later slice,
* ``main.py`` of the gateway: the ``/v1/embeddings`` route,
* ``customer_console/``: the Router IS the place that calls a vendor.

The count of direct vendor calls is held by the ratchet in
``test_no_direct_ai_vendor_calls.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCAN_DIRS = ("apps", "packages")
_SKIP_PARTS = frozenset({"__pycache__", ".venv", "node_modules", ".git", "tests"})
_VERBS = frozenset({"aembedding", "embeddings_on_console"})

#: path (prefix when it ends with ``/``) -> reason.
_ALLOWED: dict[str, str] = {
    "apps/services/gateway/gateway/kb_index/embed.py":
        "The embed door. The one function that makes a vector.",
    "apps/services/whatsapp_ingestion/whatsapp_ingestion/wa_embeddings.py":
        "WhatsApp is frozen (D115). No new WhatsApp work.",
    "apps/services/gateway/gateway/routes/tasks/capability.py":
        "The capability index. It moves to the door in a later slice.",
    "apps/services/gateway/gateway/main.py":
        "The /v1/embeddings route of the gateway.",
    "apps/services/customer_console/":
        "The Console is the Router, the one place that calls a vendor.",
}


def _files() -> list[Path]:
    out: list[Path] = []
    for d in _SCAN_DIRS:
        for p in (_REPO / d).rglob("*.py"):
            if not _SKIP_PARTS.intersection(p.relative_to(_REPO).parts):
                out.append(p)
    return out


def _allowed(rel: str) -> bool:
    return any(rel == k or (k.endswith("/") and rel.startswith(k)) for k in _ALLOWED)


def _calls(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in _VERBS:
                lines.append(node.lineno)
    return lines


def test_no_embed_call_outside_the_door() -> None:
    found: list[str] = []
    for p in _files():
        rel = p.relative_to(_REPO).as_posix()
        if _allowed(rel):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8-sig"), filename=rel)
        found += [f"{rel}:{n}" for n in _calls(tree)]
    assert not found, (
        "These files make a vector outside the embed door "
        "(gateway/kb_index/embed.py). Call embed.embed() instead: "
        + ", ".join(found)
    )


def test_every_allowed_file_exists() -> None:
    for k in _ALLOWED:
        assert (_REPO / k).exists(), f"stale entry in _ALLOWED: {k}"


def test_the_scan_sees_a_call() -> None:
    """The scan is not blind: it finds the call in the door itself."""
    door = _REPO / "apps/services/gateway/gateway/kb_index/embed.py"
    assert _calls(ast.parse(door.read_text(encoding="utf-8")))
