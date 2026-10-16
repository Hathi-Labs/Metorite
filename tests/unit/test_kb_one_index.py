"""WS-61 KB-3 — one index for the whole product, and one embed door.

Spec: ``project-docs/specs/knowledge_base.md`` §6.1 and §6.4.

Two rules, each a static read of the tree:

1. **One index.** No table under ``infra/postgres/`` other than ``kb_chunks``
   declares both a ``VECTOR`` column and a ``kb_`` name. A second vector
   table of Knowledge is a second index, and the two would drift. The read
   covers ``CREATE TABLE`` and ``ALTER TABLE … ADD COLUMN``.
2. **One door.** No module under ``gateway/kb_index/`` or
   ``gateway/routes/knowledge/`` imports ``litellm`` or ``openai``, or calls
   ``aembedding``. The vector comes from ``kb_index.embed.embed``, which asks
   the Console Router's ``/v1/embeddings`` (CP-EMB, 2026-10-17).

Mutations this file catches (R7): a second ``kb_*`` table with a vector
column, a vector column added to ``kb_pages``, and an import of ``litellm``
in the index -> each fails here. A file that does not parse fails, and never
skips.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_POSTGRES = _ROOT / "infra" / "postgres"
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"

#: The one table of the index.
_THE_INDEX = "kb_chunks"

_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?\"?(\w+)\"?\s+(.*?);",
    re.IGNORECASE | re.DOTALL,
)
_VECTOR = re.compile(r"\bvector\s*\(", re.IGNORECASE)
_ADD_VECTOR = re.compile(r"\bADD\s+COLUMN\b[^,;]*?\bvector\s*\(", re.IGNORECASE)
_COMMENT = re.compile(r"--[^\n]*")

#: Vendor SDKs the index may not import (D90 clause 3, §6.4).
_VENDOR_MODULES = frozenset({"litellm", "openai"})


def _sql_files() -> list[Path]:
    return sorted(_POSTGRES.rglob("*.sql"))


def vector_kb_tables(sql: str) -> set[str]:
    """The ``kb_*`` tables that *sql* gives a ``vector`` column."""
    body = _COMMENT.sub("", sql)
    found: set[str] = set()
    for match in _CREATE.finditer(body):
        name, cols = match.group(1).lower(), match.group(2)
        if name.startswith("kb_") and _VECTOR.search(cols):
            found.add(name)
    for match in _ALTER.finditer(body):
        name, rest = match.group(1).lower(), match.group(2)
        if name.startswith("kb_") and _ADD_VECTOR.search(rest):
            found.add(name)
    return found


def _all_vector_kb_tables() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in _sql_files():
        for name in vector_kb_tables(path.read_text(encoding="utf-8")):
            out.setdefault(name, set()).add(path.relative_to(_ROOT).as_posix())
    return out


def _index_modules() -> list[Path]:
    roots = [_GATEWAY / "kb_index", _GATEWAY / "routes" / "knowledge",
             *sorted(p for p in _GATEWAY.glob("kb_*") if p.name != "kb_index")]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(sorted(root.rglob("*.py")))
    return found


def vendor_findings(source: str, name: str) -> list[str]:
    """Each import of a vendor SDK and each ``aembedding`` call in *source*."""
    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _VENDOR_MODULES:
                    out.append(f"{name}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _VENDOR_MODULES:
                out.append(f"{name}:{node.lineno} imports from {node.module}")
        elif isinstance(node, ast.Call):
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if called == "aembedding":
                out.append(f"{name}:{node.lineno} calls aembedding")
    return out


# ── One index ───────────────────────────────────────────────────────────────

def test_the_index_table_exists_with_a_vector() -> None:
    """Not vacuous: the scan finds ``kb_chunks`` and its vector column."""
    found = _all_vector_kb_tables()
    assert _THE_INDEX in found, (
        f"no migration under infra/postgres/ declares {_THE_INDEX} with a vector "
        "column, so the scan is broken or the index is gone"
    )


def test_no_second_kb_table_has_a_vector() -> None:
    extra = {k: sorted(v) for k, v in _all_vector_kb_tables().items() if k != _THE_INDEX}
    assert not extra, (
        "A second Knowledge table with a VECTOR column is a second index "
        f"(knowledge_base.md §6.1). Use {_THE_INDEX}: {extra}"
    )


def test_the_scan_sees_a_second_index() -> None:
    sql = (
        "CREATE TABLE IF NOT EXISTS kb_other (\n"
        "    id BIGSERIAL PRIMARY KEY,\n"
        "    embedding VECTOR(1536)\n"
        ");\n"
    )
    assert vector_kb_tables(sql) == {"kb_other"}


def test_the_scan_sees_a_vector_added_to_a_kb_table() -> None:
    sql = "ALTER TABLE kb_pages ADD COLUMN IF NOT EXISTS embedding vector(1536);\n"
    assert vector_kb_tables(sql) == {"kb_pages"}


def test_the_scan_ignores_other_tables_and_comments() -> None:
    sql = (
        "-- CREATE TABLE kb_ghost (e vector(3));\n"
        "CREATE TABLE email_embeddings (embedding vector(1536) NOT NULL);\n"
        "CREATE TABLE kb_spaces (id UUID PRIMARY KEY);\n"
    )
    assert vector_kb_tables(sql) == set()


# ── One door ────────────────────────────────────────────────────────────────

def test_the_index_modules_exist() -> None:
    names = {p.name for p in _index_modules()}
    assert {"embed.py", "query.py", "index.py", "search.py"} <= names


def test_no_vendor_sdk_and_no_aembedding_in_the_index() -> None:
    found: list[str] = []
    for path in _index_modules():
        found.extend(vendor_findings(path.read_text(encoding="utf-8"),
                                     path.relative_to(_GATEWAY).as_posix()))
    assert not found, (
        "The index makes a vector through kb_index.embed.embed only, over the "
        "Console Router's /v1/embeddings (knowledge_base.md §6.4):\n" + "\n".join(found)
    )


def test_the_door_check_sees_each_shape() -> None:
    src = (
        "import litellm\n"
        "from openai import AsyncOpenAI\n"
        "import openai.types as t\n"
        "async def f():\n"
        "    await litellm.aembedding(model='m', input=['x'])\n"
        "    await aembedding(model='m', input=['x'])\n"
    )
    assert len(vendor_findings(src, "x.py")) == 5


def test_the_embeddings_door_does_not_block_the_event_loop() -> None:
    """KB-3 review, 2026-10-16. The gateway calls its own ``/v1/embeddings``
    on each Knowledge search and publish. The OpenAI client is synchronous,
    so the route must hand the call to a worker thread. A direct call would
    stall the event loop for every tenant for one OpenAI round trip."""
    import ast
    from pathlib import Path

    main = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/main.py"
    tree = ast.parse(main.read_text(encoding="utf-8"))
    route = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "embeddings"
    )
    direct = [
        n for n in ast.walk(route)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "embeddings"
    ]
    assert not direct, "call client.embeddings.create through asyncio.to_thread"
    threaded = [
        n for n in ast.walk(route)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "to_thread"
    ]
    assert threaded, "the embeddings route must run the OpenAI call in a thread"
