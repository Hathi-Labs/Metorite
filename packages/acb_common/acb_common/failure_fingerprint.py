"""The fingerprint of a failure, and the rules that make its fields (TOF-2b).

Spec: ``project-docs/specs/tool_failure_log.md`` §3. The golden rows are in
``tool_failure_log_examples.md``. Fence: ``tests/unit/test_failure_fingerprint.py``
(P2).

The fingerprint is the first 16 hex characters of a SHA-256 over one line::

    v1|<tool>|<error_code>|<exc_type>|<code_site>

Each field comes from code or from a closed list. **The message never enters
it** (§3.3): no ``str(exc)``, no ``exc.args``, no argument, no result, no
header, no tenant, no member, no server name, no release and no time. So two
tenants with one fault share one fingerprint, and no tenant text crosses into
the platform rows.

This module is pure. It does no I/O, and it imports the standard library
only. It imports no ``acb_skills`` module, so ``acb_skills`` may import the
failure log with no cycle (§3.2 rule 1).
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import TracebackType

#: The version of the rules. A change of a rule makes a new version, on purpose.
VERSION = "v1"

#: The closed list of failure codes (§3.2 rule 2): code -> owned by us.
ERROR_CODES: dict[str, bool] = {
    "exception": True,
    "bad_arguments": True,
    "unknown_tool": True,
    "auth_401": True,
    "unhandled_500": True,
    "route_500": True,
    "route_upstream": False,
    "gateway_unreachable": False,
    "outside_unreachable": False,
    "mcp_exception": True,
    "mcp_timeout": False,
    "mcp_unreachable": False,
    "mcp_refused": False,
    "mcp_too_large": False,
    "mcp_tool_error": False,
}

#: The codes that keep the exception type and the code site. Every other code
#: writes ``-`` in both, because the code holds the meaning.
KEPT_CODES = frozenset({"exception", "unhandled_500", "mcp_exception"})
DASH_CODES = frozenset(ERROR_CODES) - KEPT_CODES

#: The sources of §4.2 and the surfaces of §4.2. Each is a closed list.
SOURCES = frozenset({"chat_tool", "mcp_call", "door_5xx", "issue_report", "sandbox"})
SURFACES = frozenset({"chat", "whatsapp", "projects_assistant", "automation",
                      "mcp_edge", "studio", "app", "other"})

#: The kinds of tool name that a capture site passes (§3.2 rule 1).
KINDS = frozenset({"chat", "studio", "mcp", "user_tool", "route"})
PLACEHOLDERS = frozenset({"other", "studio", "mcp", "user_tool"})
ROUTE_OTHER = "route:other"
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

#: The grammar of each column (§3.2). The migration holds the same patterns as
#: CHECKs, so the database refuses what this module would never make.
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
ROUTE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./{}:-]{0,200}$")
ROUTE_RE = re.compile(
    r"^route:(other|(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) /[A-Za-z0-9_./{}:-]{0,200})$")
EXC_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,120}$")
CODE_SITE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,150}:[A-Za-z_][A-Za-z0-9_]{0,80}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,150}$")
_FUNCTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")

#: §3.2 rule 3: the roots of a module whose class may name itself.
_OUR_ROOTS = frozenset({"gateway", "orchestrator", "agent_edge", "email_ingestion", "ingestion"})
_OUR_PREFIXES = ("acb_", "skill_", "_tool_pack_owner_")
_PINNED = frozenset({
    "httpx", "httpcore", "anyio", "sqlalchemy", "asyncpg", "psycopg", "pydantic",
    "pydantic_core", "agent_framework", "mcp", "openai", "redis", "starlette", "fastapi",
})

#: §3.2 rule 4: the four trees of our code, under one root.
_TREES = (("apps", "services"), ("apps", "skills"), ("apps", "agents"), ("packages",))
_WALK_MAX = 32


@dataclass(frozen=True)
class Fields:
    """The four platform fields of one failure, and their fingerprint."""

    tool: str
    error_code: str
    exc_type: str
    code_site: str

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.tool, self.error_code, self.exc_type, self.code_site)


def fingerprint(tool: str, error_code: str, exc_type: str, code_site: str) -> str:
    """The first 16 hex characters of the SHA-256 of the v1 line (§3.1).

    ``failure_tally`` computes the same hash in SQL and refuses a mismatch, so
    a change here without a new version breaks every write."""
    line = "|".join((VERSION, tool, error_code, exc_type, code_site))
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def fields_problem(tool: str, error_code: str, exc_type: str, code_site: str) -> str | None:
    """The name of the first field off its grammar, or None."""
    if not isinstance(tool, str) or not (TOOL_RE.match(tool) or ROUTE_RE.match(tool)):
        return "tool"
    if error_code not in ERROR_CODES:
        return "error_code"
    if not isinstance(exc_type, str) or not (exc_type == "-" or EXC_TYPE_RE.match(exc_type)):
        return "exc_type"
    if not isinstance(code_site, str) or not (code_site == "-" or CODE_SITE_RE.match(code_site)):
        return "code_site"
    if error_code in DASH_CODES and (exc_type, code_site) != ("-", "-"):
        return "error_code"
    return None


def platform_tool(name: object, *, kind: str = "chat",
                  builtin_names: Collection[str] = frozenset(),
                  route_paths: Collection[str] = frozenset()) -> str:
    """The platform name of a tool (§3.2 rule 1). Never tenant text.

    * ``chat``: the name when *builtin_names* holds it and it matches the
      grammar, else ``other``. A name that the model wrote is tenant text.
    * ``studio``, ``mcp`` and ``user_tool``: the kind itself, because each of
      their names is tenant text.
    * ``route``: *name* is ``"<METHOD> <path>"``. It counts only when the path
      equals a declared template of *route_paths*, else ``route:other``. So a
      concrete path with an id or a query string never passes.
    """
    if kind in ("studio", "mcp", "user_tool"):
        return kind
    if not isinstance(name, str):
        return ROUTE_OTHER if kind == "route" else "other"
    if kind == "route":
        method, _, path = name.partition(" ")
        if (method in HTTP_METHODS and path in route_paths
                and ROUTE_PATH_RE.match(path)):
            return f"route:{method} {path}"
        return ROUTE_OTHER
    if kind == "chat" and name in builtin_names and TOOL_RE.match(name):
        return name
    return "other"


def chain(exc: BaseException) -> list[BaseException]:
    """The exception that the site caught, then each deeper one.

    The walk follows ``__cause__``, and the first member of a group. It never
    follows ``__context__`` (§3.2 rule 3): a fault inside an ``except`` block
    is its own cause, not the error it handled (golden row 11)."""
    out: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(out) < _WALK_MAX:
        seen.add(id(cur))
        out.append(cur)
        nxt = cur.__cause__
        if not isinstance(nxt, BaseException):
            members = getattr(cur, "exceptions", None)
            nxt = None
            if isinstance(members, tuple) and members and isinstance(members[0], BaseException):
                nxt = members[0]
        cur = nxt
    return out


def _allowed_root(module: str) -> bool:
    root = module.split(".", 1)[0]
    return (root in sys.stdlib_module_names or root in _OUR_ROOTS or root in _PINNED
            or root.startswith(_OUR_PREFIXES))


def exc_type_of(exc: BaseException | None) -> str:
    """``module.QualName`` of the deepest exception, ``other``, or ``-``
    (§3.2 rule 3).

    The name is kept only when the root of its module is on the allow list,
    the name matches the grammar, and a walk of the qualname through the
    namespace of ``sys.modules[module]`` gives the same class. The walk reads
    ``__dict__`` only, so it runs no code of the class or the module."""
    if exc is None:
        return "-"
    deepest = chain(exc)[-1]
    cls = type(deepest)
    module = getattr(cls, "__module__", None)
    qualname = getattr(cls, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        return "other"
    name = f"{module}.{qualname}"
    if not _allowed_root(module) or not EXC_TYPE_RE.match(name):
        return "other"
    obj: object = sys.modules.get(module)
    for part in qualname.split("."):
        space = getattr(obj, "__dict__", None)
        if space is None:
            return "other"
        try:
            obj = space.get(part)
        except Exception:  # a strange namespace reads as a lie
            return "other"
    return name if obj is cls else "other"


@lru_cache(maxsize=1)
def _repository_root() -> str:
    return str(Path(__file__).resolve().parents[3])


@lru_cache(maxsize=4096)
def _in_tree(filename: str, root: str) -> bool:
    """True when *filename* sits in one of the four trees under *root*."""
    if not filename or filename.startswith("<"):
        return False
    try:
        path = Path(os.path.realpath(filename))
        rel = path.relative_to(Path(os.path.realpath(root)))
    except (OSError, ValueError):
        return False
    parts = rel.parts
    if "site-packages" in parts or "dist-packages" in parts:
        return False
    for tree in _TREES:
        n = len(tree)
        # The tree, one directory of its own, and a file below it.
        if parts[:n] == tree and len(parts) >= n + 2:
            return True
    return False


def _frames(tb: TracebackType | None) -> Iterator[tuple[str, dict[str, object], str]]:
    """The frames of *tb*, the deepest first."""
    frames = []
    while tb is not None and len(frames) < 512:
        frames.append(tb.tb_frame)
        tb = tb.tb_next
    for frame in reversed(frames):
        yield frame.f_code.co_filename, frame.f_globals, frame.f_code.co_name


def code_site_of(exc: BaseException | None, repo_root: str | os.PathLike[str] | None = None) -> str:
    """``module:function`` of the deepest frame in our code, or ``-``
    (§3.2 rule 4).

    The walk takes the exceptions from the deepest one out to the one that the
    site caught. In each, it takes the frames from the deepest one up. The
    first frame whose file is in one of the four trees wins. No line number
    goes in, so a release that moves lines keeps the fingerprint."""
    if exc is None:
        return "-"
    root = str(repo_root) if repo_root is not None else _repository_root()
    for item in reversed(chain(exc)):
        for filename, glb, func in _frames(item.__traceback__):
            if not _in_tree(filename, root):
                continue
            module = glb.get("__name__")
            if not isinstance(module, str) or not _MODULE_RE.match(module):
                continue
            if not _FUNCTION_RE.match(func):
                func = "lambda" if func == "<lambda>" else "module"
            return f"{module}:{func}"
    return "-"


def normalise(exc: BaseException | None, *, error_code: str, tool: object,
              kind: str = "chat", builtin_names: Collection[str] = frozenset(),
              route_paths: Collection[str] = frozenset(),
              repo_root: str | os.PathLike[str] | None = None) -> Fields:
    """The four platform fields of one failure (§3.2).

    Raises ``ValueError`` for a code off the closed list. Every other input
    maps to a safe value. Call it inside the ``except`` block, while the
    traceback is still there."""
    if error_code not in ERROR_CODES:
        raise ValueError("error_code is not on the closed list")
    name = platform_tool(tool, kind=kind if kind in KINDS else "other",
                         builtin_names=builtin_names, route_paths=route_paths)
    if error_code in DASH_CODES:
        return Fields(name, error_code, "-", "-")
    return Fields(name, error_code, exc_type_of(exc), code_site_of(exc, repo_root))
