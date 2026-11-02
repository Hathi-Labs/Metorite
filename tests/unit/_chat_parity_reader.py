"""The reader of the chat parity fence (P-0, Metorite task 1238).

Spec: ``project-docs/specs/chat_parity.md``. The fence is
``tests/unit/test_chat_parity.py``, and the table is
``acb_skills.chat_parity``.

This module reads two sides as TEXT, and runs neither:

1. **The UI.** Each request that the control plane can send to its BFF, as a
   verb and a path under ``/api/``. It reads the TypeScript source with the
   tokenizer of fence F3 (``test_projects_ui_actions.mask``), so comments
   and string text never look like code. It follows the shapes that the tree
   uses: a literal path, a constant, an object of constants, a path builder,
   and a request seam (a function that takes a path and calls ``fetch``).
   A seam can call another seam, and an import brings a seam from another
   file.
2. **The tools.** Each chat tool function, and the gateway paths that it and
   the helpers of its module name. It reads the Python source with ``ast``.

A request that the reader cannot follow is UNREAD, by file and line. It never
passes in silence. The fence holds the count of each file to a ceiling.

Deliberately free of ``pytest.importorskip``: this module is imported, not
collected. The F3 tokenizer comes in by import, so there is one tokenizer.
"""

from __future__ import annotations

import ast
import bisect
import contextlib
import dataclasses
import functools
import itertools
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "workbench" / "control_plane" / "src"
BFF_DIR = SRC / "app" / "api"

#: Files that name a BFF path for a reason other than a request: the edge
#: proxy's route lists and the sign-in config. They send nothing.
NOT_A_CLIENT: frozenset[str] = frozenset({"proxy.ts", "auth.ts"})

#: A built-in way to send a request: name -> (path argument, verb rule).
#: A verb rule is ``("fixed", VERB)``, ``("init", k)`` for the ``method`` of
#: the object at argument ``k``, or ``("arg", k)`` for a verb at argument k.
FETCHERS: dict[str, tuple[int, tuple[str, object]]] = {
    "fetch": (0, ("init", 1)),
    "fetchImpl": (0, ("init", 1)),
    "fetchFn": (0, ("init", 1)),
    "sendBeacon": (0, ("fixed", "POST")),
    "EventSource": (0, ("fixed", "GET")),
    "useCachedResource": (0, ("fixed", "GET")),
}

#: A literal in one of these places is a link that the browser follows: a
#: download, a picture, an OAuth hop or a form. The verb is GET, or POST for
#: a form.
_LINK = re.compile(
    r"(?:\b(?:href|src|action)\s*=\s*\{?\s*|\blocation(?:\.href)?\s*=\s*"
    r"|\blocation\.(?:assign|replace)\s*\(\s*|\bwindow\.open\s*\(\s*)$"
)


def _tokenizer():
    """The F3 tokenizer. Imported late, so a reader of this module's
    docstring does not pay for the Projects imports."""
    from tests.unit import test_projects_ui_actions as f3

    return f3.mask, _match, f3.read_literal


#: masked text id -> (the text, {opening bracket: its closing bracket})
_PAIRS: dict[int, tuple[str, dict[int, int]]] = {}


def _match(masked: str, i: int) -> int:
    """F3's ``match``, from a table built once for each text.

    F3 walks from the bracket each time. The whole control plane has many
    thousand calls, so the walk is the cost of the run.
    """
    hit = _PAIRS.get(id(masked))
    if hit is None or hit[0] is not masked:
        pairs: dict[int, int] = {}
        stack: list[int] = []
        for j, ch in enumerate(masked):
            if ch in "([{":
                stack.append(j)
            elif ch in ")]}" and stack:
                pairs[stack.pop()] = j
        hit = (masked, pairs)
        _PAIRS[id(masked)] = hit
    try:
        return hit[1][i]
    except KeyError:
        raise ValueError(f"no bracket closes the one at offset {i}") from None


# ── The model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Site:
    """One request that the UI can send."""

    file: str
    line: int
    verb: str
    #: The BFF path, ``/api/...``, with ``{}`` for each id segment.
    path: str
    #: The function around the call, or ``""`` at the top of the file.
    where: str

    @property
    def action(self) -> str:
        return f"{self.verb} {self.path}"


@dataclass
class Seam:
    """A function that takes a path and sends it."""

    name: str
    file: str
    #: The index of the path argument.
    arg: int
    #: The text that the seam puts before the path, ``/api/...``.
    prefix: str
    verb: tuple[str, object]


@dataclass
class Fn:
    name: str
    params: list[str]
    #: The span of the body (or the expression of an arrow).
    a: int
    b: int
    start: int
    #: The offset of the ``(`` of the parameter list.
    paren: int = -1


@dataclass
class FileModel:
    rel: str
    src: str
    masked: str
    fns: list[Fn] = field(default_factory=list)
    #: name -> the span of the value expression (the last one wins).
    consts: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: name -> {key: span of value}
    objects: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    #: local name -> (resolved file rel, exported name)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: exported name -> local name
    exports: dict[str, str] = field(default_factory=dict)
    seams: dict[str, Seam] = field(default_factory=dict)
    #: Offsets of the calls that a seam's own body makes.
    seam_calls: set[int] = field(default_factory=set)
    #: Every call in the file, ``(name, offset of its paren)``, in order.
    calls: list[tuple[str, int]] = field(default_factory=list)

    def calls_in(self, a: int, b: int) -> list[tuple[str, int]]:
        lo = bisect.bisect_left(self.calls, a, key=lambda c: c[1])
        hi = bisect.bisect_left(self.calls, b, key=lambda c: c[1])
        return self.calls[lo:hi]


# ── Small scanners over masked text ─────────────────────────────────────────

_OPEN = "([{"
_CLOSE = ")]}"


def _expr_end(masked: str, i: int, stops: str = ",;") -> int:
    """The end of the expression that starts at ``i``: the first top-level
    stop character, or the bracket that closes an outer one."""
    depth = 0
    n = len(masked)
    j = i
    while j < n:
        ch = masked[j]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and ch in stops:
            return j
        elif depth == 0 and ch == "\n" and ";" in stops:
            # A statement can end at a newline with no semicolon. Stop only
            # when the next code does not continue the expression.
            k = j + 1
            while k < n and masked[k] in " \t\r\n":
                k += 1
            nxt = masked[k : k + 2]
            p = j - 1
            while p >= i and masked[p] in " \t\r\n":
                p -= 1
            prev = masked[p] if p >= i else ""
            if (
                nxt[:1] not in ".?:+-*/|&,)]}`'\""
                and nxt != "=>"
                and prev not in "=(,[{?:+-*/|&!<>"
            ):
                return j
        j += 1
    return j


def _args(masked: str, open_paren: int, match) -> list[tuple[int, int]]:
    """The spans of the top-level arguments of the call at ``open_paren``."""
    close = match(masked, open_paren)
    spans: list[tuple[int, int]] = []
    depth = 0
    start = open_paren + 1
    for j in range(open_paren + 1, close):
        ch = masked[j]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and ch == ",":
            spans.append((start, j))
            start = j + 1
    spans.append((start, close))
    return [(a, b) for a, b in spans if masked[a:b].strip()]


def _strip(masked: str, a: int, b: int) -> tuple[int, int]:
    while a < b and masked[a].isspace():
        a += 1
    while b > a and masked[b - 1].isspace():
        b -= 1
    # `x as T`, `x!` and `x satisfies T` do not change the value.
    tail = re.search(r"\s+(?:as|satisfies)\s+[\w.<>\[\]|\s]+$", masked[a:b])
    if tail:
        b = a + tail.start()
    while b > a and masked[b - 1] == "!":
        b -= 1
    return a, b


def _top_split(masked: str, a: int, b: int, token: str) -> list[tuple[int, int]]:
    """Split ``a..b`` at each top-level ``token``."""
    out: list[tuple[int, int]] = []
    depth = 0
    start = a
    j = a
    while j < b:
        ch = masked[j]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and masked.startswith(token, j):
            if token == "?" and masked[j + 1 : j + 2] in ("?", "."):
                j += 2
                continue
            if token == "?" and j > a and masked[j - 1] == "?":
                j += 1
                continue
            out.append((start, j))
            j += len(token)
            start = j
            continue
        j += 1
    out.append((start, b))
    return out


_FN_DECL = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*(?:<[^()]*?>)?\s*\("
)
_FN_ARROW = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=;]+?)?=\s*(?:async\s+)?(?:function\b\s*\w*\s*)?"
    r"(?:<[\w\s,]+>\s*)?\("
)
_FN_PROP = re.compile(r"(?:^|[,{;\s])(?:async\s+)?(\w+)\s*(?::\s*(?:async\s+)?)?\(")
_NOT_A_NAME = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "function",
        "return",
        "typeof",
        "await",
        "new",
        "else",
        "do",
        "try",
        "super",
        "import",
        "export",
        "case",
        "in",
        "of",
        "void",
        "delete",
        "throw",
        "yield",
        "async",
    }
)

#: A parameter that carries a whole path, not an id. A function that puts an
#: id into its path is a client method, and its own call is the site.
_PATH_PARAM = re.compile(
    r"^(?:path|url|href|hop|endpoint|route|suffix|uri|p|base|link|tail)$"
    r"|(?:Path|Url|Href|Endpoint|Route)$"
)


def _params(masked: str, open_paren: int, match) -> tuple[list[str], int]:
    close = match(masked, open_paren)
    names: list[str] = []
    for a, b in _args(masked, open_paren, match):
        text = masked[a:b].strip()
        hit = re.match(r"(?:\.\.\.)?\s*([A-Za-z_$][\w$]*)", text)
        names.append(hit.group(1) if hit else "")
    return names, close


def _body_after(masked: str, close: int, match) -> tuple[int, int] | None:
    """The body after a parameter list: ``{...}`` or an arrow's expression."""
    j = close + 1
    n = len(masked)
    # A return type: `): Promise<T> {` or `): T =>`.
    while j < n and masked[j].isspace():
        j += 1
    if masked[j : j + 1] == ":":
        depth = 0
        k = j + 1
        while k < n:
            ch = masked[k]
            if ch in "<([":
                depth += 1
            elif ch in ">)]" and not (ch == ">" and masked[k - 1] == "="):
                depth -= 1
            elif depth == 0 and (ch == "{" or masked.startswith("=>", k)):
                # `{` can open an object type. Prefer the arrow or the body.
                if ch == "{":
                    end = match(masked, k)
                    after = masked[end + 1 :].lstrip()
                    if after.startswith("=>") or after.startswith("{"):
                        k = end + 1
                        continue
                break
            k += 1
        j = k
    while j < n and masked[j].isspace():
        j += 1
    if masked.startswith("=>", j):
        j += 2
        while j < n and masked[j].isspace():
            j += 1
        if masked[j : j + 1] == "{":
            return j, match(masked, j)
        return j, _expr_end(masked, j)
    if masked[j : j + 1] == "{":
        return j, match(masked, j)
    return None


def _functions(masked: str, match) -> list[Fn]:
    fns: list[Fn] = []
    seen: set[int] = set()
    for rx in (_FN_DECL, _FN_ARROW, _FN_PROP):
        for hit in rx.finditer(masked):
            name = hit.group(1)
            if name in _NOT_A_NAME:
                continue
            paren = hit.end() - 1
            if paren in seen:
                continue
            try:
                params, close = _params(masked, paren, match)
                body = _body_after(masked, close, match)
            except (ValueError, IndexError):
                continue
            if body is None:
                continue
            seen.add(paren)
            fns.append(Fn(name, params, body[0], body[1], hit.start(1), paren))
    return fns


_CONST = re.compile(r"\b(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[^=;]+?)?=(?!=|>)\s*")
_ENTRY = re.compile(r"\s*([\w$]+|\"[^\"]*\"|'[^']*')\s*:\s*")


def _object_entries(src: str, masked: str, o: int, match) -> dict[str, tuple[int, int]]:
    close = match(masked, o)
    out: dict[str, tuple[int, int]] = {}
    for a, b in _args(masked, o, match) if masked[o] == "(" else _split_obj(masked, o, close):
        hit = _ENTRY.match(masked, a)
        if hit and hit.end() <= b:
            key = src[hit.start(1) : hit.end(1)].strip("\"'")
            out[key] = (hit.end(), b)
    return out


def _split_obj(masked: str, o: int, c: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    start = o + 1
    for j in range(o + 1, c):
        ch = masked[j]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and ch == ",":
            spans.append((start, j))
            start = j + 1
    spans.append((start, c))
    return [(a, b) for a, b in spans if masked[a:b].strip()]


_IMPORT = re.compile(r"\bimport\s+(?:type\s+)?\{([^}]*)\}\s*from\s*[\"']([^\"']+)[\"']")
_EXPORT_ALIAS = re.compile(r"\bexport\s*\{([^}]*)\}")


_ALL_FILES: set[str] = set()


def _all_files() -> set[str]:
    if not _ALL_FILES:
        _ALL_FILES.update(
            p.relative_to(SRC).as_posix() for p in SRC.rglob("*.ts*") if p.suffix in (".ts", ".tsx")
        )
    return _ALL_FILES


def _module_file(rel: str, spec: str) -> str | None:
    """The file of an import, relative to ``SRC``, or ``None`` for a package."""
    if spec.startswith("@/"):
        base = spec[2:]
    elif spec.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
    else:
        return None
    files = _all_files()
    for cand in (base + ".ts", base + ".tsx", base + "/index.ts", base + "/index.tsx", base):
        if cand in files:
            return cand
    return None


def model(rel: str, src: str) -> FileModel:
    mask, match, _ = _tokenizer()
    masked = mask(src)
    fm = FileModel(rel, src, masked)
    fm.fns = _functions(masked, match)
    decls = {fn.paren for fn in fm.fns}
    fm.calls = [c for c in _calls(masked, 0, len(masked)) if c[1] not in decls]
    for hit in _CONST.finditer(masked):
        a = hit.end()
        if masked[a : a + 1] == "{":
            with contextlib.suppress(ValueError):
                fm.objects[hit.group(1)] = _object_entries(src, masked, a, match)
            continue
        b = _expr_end(masked, a)
        fm.consts[hit.group(1)] = (a, b)
    for hit in _IMPORT.finditer(masked):
        target = _module_file(rel, src[hit.start(2) : hit.end(2)])
        if target is None:
            continue
        for part in src[hit.start(1) : hit.end(1)].split(","):
            bits = part.replace("type ", "").split(" as ")
            name = bits[0].strip()
            local = bits[-1].strip()
            if name:
                fm.imports[local] = (target, name)
    for hit in _EXPORT_ALIAS.finditer(masked):
        for part in src[hit.start(1) : hit.end(1)].split(","):
            bits = part.split(" as ")
            if bits[0].strip():
                fm.exports[bits[-1].strip()] = bits[0].strip()
    return fm


# ── The project: every file, every seam ─────────────────────────────────────


#: A literal that starts with the BFF prefix. A file with none, and with no
#: import of a seam or a path, sends no request.
_NAMES_A_PATH = re.compile(r"[\"'`]/api\b")


def client_files() -> list[str]:
    out: list[str] = []
    for path in sorted(SRC.rglob("*.ts*")):
        if path.suffix not in (".ts", ".tsx"):
            continue
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("app/api/") or rel in NOT_A_CLIENT:
            continue
        if ".test." in path.name or ".spec." in path.name or path.name.endswith(".d.ts"):
            continue
        out.append(rel)
    return out


class Project:
    """Every client file of the control plane, read once."""

    def __init__(self, sources: dict[str, str] | None = None, base: Project | None = None) -> None:
        """Read the tree. ``sources`` replaces the text of some files. With
        ``base``, a file that ``sources`` does not change is not read again,
        so a mutation test costs one file."""
        texts: dict[str, str] = {}
        for rel in client_files():
            src = (sources or {}).get(rel)
            texts[rel] = src if src is not None else (SRC / rel).read_text(encoding="utf-8")
        for rel, src in (sources or {}).items():
            texts.setdefault(rel, src)
        # Only a file that names a BFF path, or that imports from such a
        # file, can send a request. A file that does neither has no site.
        self.files: dict[str, FileModel] = {}
        #: The literals that a seam's own prefix reads. They are not sites.
        self.seam_literals: set[tuple[str, int]] = set()
        todo = {rel for rel, src in texts.items() if _NAMES_A_PATH.search(src)}
        while todo:
            for rel in sorted(todo):
                old = base.files.get(rel) if base is not None else None
                if old is not None and rel not in (sources or {}):
                    self.files[rel] = dataclasses.replace(old, seams={}, seam_calls=set())
                else:
                    self.files[rel] = model(rel, texts[rel])
            self._find_seams()
            todo = {
                rel
                for rel, src in texts.items()
                if rel not in self.files and self._imports_a_path(rel, src)
            }

    def _imports_a_path(self, rel: str, src: str) -> bool:
        """Does ``src`` import a seam, a path or a path builder from a file
        that the reader holds?"""
        for hit in _IMPORT.finditer(src):
            target = self.files.get(_module_file(rel, hit.group(2)) or "")
            if target is None:
                continue
            for part in hit.group(1).split(","):
                name = part.replace("type ", "").split(" as ")[0].strip()
                local = target.exports.get(name, name)
                if local in target.seams:
                    return True
                if local in target.objects and any(
                    "/api" in target.src[slice(*v)] for v in target.objects[local].values()
                ):
                    return True
                if local in target.consts and "/api" in target.src[slice(*target.consts[local])]:
                    return True
                if any(f.name == local and "/api" in target.src[f.a : f.b] for f in target.fns):
                    return True
        return False

    # ── name lookup across imports ──

    def lookup(self, fm: FileModel, name: str, kind: str, depth: int = 0):
        """``(file, value)`` of a seam, a const or an object named ``name``."""
        if depth > 4:
            return None
        table = {"seam": fm.seams, "const": fm.consts, "object": fm.objects}[kind]
        if name in table:
            return fm, table[name]
        if name in fm.imports:
            target, exported = fm.imports[name]
            other = self.files.get(target)
            if other is not None:
                local = other.exports.get(exported, exported)
                return self.lookup(other, local, kind, depth + 1)
        return None

    def fetcher(self, fm: FileModel, name: str) -> Seam | None:
        if name in FETCHERS:
            arg, verb = FETCHERS[name]
            return Seam(name, "", arg, "", verb)
        hit = self.lookup(fm, name, "seam")
        return hit[1] if hit else None

    # ── seams ──

    def _find_seams(self) -> None:
        changed = True
        rounds = 0
        while changed and rounds < 8:
            changed = False
            rounds += 1
            for fm in self.files.values():
                for fn in fm.fns:
                    if fn.name in fm.seams or not fn.params or fn.name in FETCHERS:
                        continue
                    seam = self._seam_of(fm, fn)
                    if seam is not None:
                        fm.seams[fn.name] = seam
                        changed = True

    def _seam_of(self, fm: FileModel, fn: Fn) -> Seam | None:
        _, match, read_literal = _tokenizer()
        masked, src = fm.masked, fm.src
        fetch_params = _default_fetchers(masked, fn)
        aliases = _aliases(fm, fn)
        if not aliases:
            return None
        for call in fm.calls_in(fn.a, fn.b):
            name, paren = call
            target = self.fetcher(fm, name)
            if target is None and name in fetch_params:
                target = Seam(name, "", 0, "", ("init", 1))
            if target is None:
                continue
            spans = _args(masked, paren, match)
            if len(spans) <= target.arg:
                continue
            a, b = _strip(masked, *spans[target.arg])
            hit = _param_ref(src, masked, a, b, aliases, read_literal, lambda n: self._one(fm, n))
            if hit is None:
                continue
            index, before = hit
            verb = self._seam_verb(fm, fn, target, spans, paren, match)
            fm.seam_calls.add(paren)
            return Seam(fn.name, fm.rel, index, target.prefix + before, verb)
        return None

    def _one(self, fm: FileModel, name: str) -> str | None:
        """The one text of the constant ``name``, or ``None``."""
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
            return None
        hit = self.lookup(fm, name, "const")
        if hit is None:
            return None
        other, (ca, cb) = hit
        got = self.resolve(other, ca, cb, self.seam_literals)
        return got[0] if got and len(got) == 1 else None

    def _seam_verb(self, fm, fn, target, spans, paren, match):
        masked, src = fm.masked, fm.src
        kind, val = target.verb
        if kind == "fixed":
            return target.verb
        k = int(val)  # type: ignore[arg-type]
        if len(spans) <= k:
            return ("fixed", "GET")
        a, b = _strip(masked, *spans[k])
        text = src[a:b]
        if kind == "arg":
            lit = re.fullmatch(r"[\"'`]([A-Za-z]+)[\"'`]", text)
            if lit:
                return ("fixed", lit.group(1).upper())
            if text in fn.params:
                return ("arg", fn.params.index(text))
            return ("fixed", "?")
        # kind == "init": an object with `method`.
        verb = _method_in(src, masked, a, b)
        if verb is not None:
            if verb.startswith("="):
                name = verb[1:]
                if name in fn.params:
                    return ("arg", fn.params.index(name))
                return ("fixed", "?")
            return ("fixed", verb)
        spread = re.search(r"\.\.\.\s*(\w+)", masked[a:b])
        if spread and spread.group(1) in fn.params:
            return ("init", fn.params.index(spread.group(1)))
        if text in fn.params:
            return ("init", fn.params.index(text))
        return ("fixed", "GET")

    # ── sites ──

    def sites(self) -> tuple[list[Site], list[str]]:
        """Every request the UI can send, and every call the reader cannot follow."""
        sites: list[Site] = []
        unread: list[str] = []
        used: set[tuple[str, int]] = set()
        for fm in self.files.values():
            s, u, consumed = self._file_sites(fm)
            sites += s
            unread += u
            used |= consumed
        loose, lost = self._loose(used | self.seam_literals)
        return sites + loose, unread + lost

    def _file_sites(self, fm: FileModel):
        _, match, _ = _tokenizer()
        masked, src = fm.masked, fm.src
        sites: list[Site] = []
        unread: list[str] = []
        consumed: set[tuple[str, int]] = set()
        fetch_params: dict[int, set[str]] = {}
        for call in fm.calls:
            name, paren = call
            if paren in fm.seam_calls:
                continue
            fn = _enclosing(fm, paren)
            target = self.fetcher(fm, name)
            if target is None and fn is not None:
                if fn.start not in fetch_params:
                    fetch_params[fn.start] = _default_fetchers(masked, fn)
                if name in fetch_params[fn.start]:
                    target = Seam(name, "", 0, "", ("init", 1))
            if target is None:
                continue
            spans = _args(masked, paren, match)
            line = src.count("\n", 0, paren) + 1
            where = fn.name if fn else ""
            if len(spans) <= target.arg:
                continue
            a, b = _strip(masked, *spans[target.arg])
            paths = self.resolve(fm, a, b, consumed)
            builtin = target.file == ""
            if paths is None:
                # A seam sends only to the gateway. A plain `fetch` may send
                # anywhere, so it is unread only when it names the BFF.
                if not builtin or "/api" in src[a:b]:
                    unread.append(f"{fm.rel}:{line} {name}({src[a:b][:40]})")
                continue
            verb = self._call_verb(fm, target, spans)
            for path in paths:
                full = _join(target.prefix, path)
                if not full.startswith("/api/"):
                    if not builtin:
                        unread.append(f"{fm.rel}:{line} {name}({src[a:b][:40]})")
                    continue
                if verb == "?":
                    unread.append(f"{fm.rel}:{line} {name}: the verb is not a literal")
                    continue
                sites.append(Site(fm.rel, line, verb, normal(full), where))
        return sites, unread, consumed

    def _call_verb(self, fm: FileModel, target: Seam, spans) -> str:
        masked, src = fm.masked, fm.src
        kind, val = target.verb
        if kind == "fixed":
            return str(val)
        k = int(val)  # type: ignore[arg-type]
        if len(spans) <= k:
            return "GET"
        a, b = _strip(masked, *spans[k])
        if kind == "arg":
            lit = re.fullmatch(r"[\"'`]([A-Za-z]+)[\"'`]", src[a:b])
            return lit.group(1).upper() if lit else "?"
        name = re.fullmatch(r"[A-Za-z_$][\w$]*", masked[a:b])
        if name:
            # `const init: RequestInit = { method: "POST" }`, then `call(path, init)`.
            entries = fm.objects.get(name.group(0))
            if entries is None:
                return "?"
            if "method" not in entries:
                return "GET"
            ea, eb = _strip(masked, *entries["method"])
            lit = re.fullmatch(r"[\"'`]([A-Za-z]+)[\"'`]", src[ea:eb])
            return lit.group(1).upper() if lit else "?"
        verb = _method_in(src, masked, a, b)
        if verb is None:
            return "GET"
        return "?" if verb.startswith("=") else verb

    def _loose(self, used: set[tuple[str, int]]) -> tuple[list[Site], list[str]]:
        """A BFF path that no call sends: a link, a download, or a constant
        that another module hands to the browser. The browser GETs it.

        A path in any other place, for example the ``url`` of an object that
        another function sends, has no verb that the reader can see. It is
        UNREAD. So is a constant that only other paths build on.
        """
        _, _, read_literal = _tokenizer()
        out: list[Site] = []
        unread: list[str] = []
        for fm in self.files.values():
            masked, src = fm.masked, fm.src
            i = 0
            n = len(src)
            while i < n:
                if masked[i] not in "'\"`":
                    i += 1
                    continue
                try:
                    _parts, end = read_literal(src, masked, i)
                except ValueError:
                    i += 1
                    continue
                if (fm.rel, i) in used:
                    i = end
                    continue
                paths = self.resolve(fm, i, end, set()) or []
                bff = [p for p in paths if p.startswith("/api/") and len(p) > len("/api/")]
                if not bff:
                    i = end
                    continue
                line = src.count("\n", 0, i) + 1
                before = masked[max(0, i - 80) : i]
                link = _LINK.search(before)
                const = re.search(r"\b(?:const|let)\s+(\w+)\s*(?::[^=;]+)?=\s*$", before)
                name = re.escape(const.group(1)) if const else ""
                if const and re.search(r"\$\{\s*" + name + r"\s*\}", masked):
                    i = end
                    continue  # A base that other paths build on. They are read.
                handed = const and re.search(
                    r"\b(?:url|href|path|endpoint)\s*:\s*"
                    + name
                    + r"\b|[{,]\s*"
                    + name
                    + r"\s*[,}]",
                    masked,
                )
                if handed or (
                    not link and re.search(r"\b(?:url|href|path|endpoint)\s*:\s*$", before)
                ):
                    unread.append(f"{fm.rel}:{line} {bff[0]}: the verb is sent from another place")
                    i = end
                    continue
                is_value = bool(re.search(r"(?:=|return|=>|\?|:)\s*$", before))
                if link or is_value:
                    fn = _enclosing(fm, i)
                    verb = "POST" if link and "action" in link.group(0) else "GET"
                    for path in bff:
                        out.append(Site(fm.rel, line, verb, normal(path), fn.name if fn else ""))
                i = end
        return out, unread

    # ── resolving an expression to paths ──

    def resolve(
        self, fm: FileModel, a: int, b: int, consumed: set, depth: int = 0
    ) -> list[str] | None:
        """The path texts that ``a..b`` can be, or ``None``."""
        if depth > 6:
            return None
        masked = fm.masked
        a, b = _strip(masked, a, b)
        if a >= b:
            return None
        while masked[a] == "(" and _match(masked, a) == b - 1:
            a, b = _strip(masked, a + 1, b - 1)
        for token in ("??", "||"):
            parts = _top_split(masked, a, b, token)
            if len(parts) > 1:
                return self._union(fm, parts, consumed, depth)
        cond = _top_split(masked, a, b, "?")
        if len(cond) == 2:
            alts = _top_split(masked, cond[1][0], cond[1][1], ":")
            if len(alts) == 2:
                return self._union(fm, alts, consumed, depth)
        plus = _top_split(masked, a, b, "+")
        if len(plus) > 1:
            head = self.resolve(fm, *plus[0], consumed, depth + 1)
            return None if head is None else [h + "{}" for h in head]
        if masked[a] in "'\"`":
            return self._literal(fm, a, consumed, depth)
        return self._name(fm, a, b, consumed, depth)

    def _literal(self, fm: FileModel, a: int, consumed: set, depth: int) -> list[str]:
        """The texts of the literal at ``a``. A part that code fills is ``{}``."""
        _, _, read_literal = _tokenizer()
        consumed.add((fm.rel, a))
        parts, _end = read_literal(fm.src, fm.masked, a)
        outs = [""]
        cursor = a
        for kind, value in parts:
            if kind == "text":
                outs = [o + value for o in outs]
                continue
            inner = fm.src.find("${" + value, cursor) + 2
            cursor = inner + len(value)
            # A constant, or a choice of words such as
            # `${archived ? "archive" : "unarchive"}`, fills the segment.
            got = self.resolve(fm, inner, cursor, consumed, depth + 1)
            if got is None or len(got) > 8 or any(" " in g for g in got):
                outs = [o + "{}" for o in outs]
            else:
                outs = [o + g for o in outs for g in got]
        return outs

    def _name(self, fm: FileModel, a: int, b: int, consumed: set, depth: int) -> list[str] | None:
        """A constant, a member of an object of constants, or a call."""
        masked = fm.masked
        text = masked[a:b]
        if re.fullmatch(r"[A-Za-z_$][\w$]*", text):
            hit = self.lookup(fm, text, "const")
            if hit is None:
                return None
            other, (ca, cb) = hit
            return self.resolve(other, ca, cb, consumed, depth + 1)
        member = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*(?:\.\s*([\w$]+)|\[(.*)\])", text, re.S)
        if member:
            return self._member(fm, member, consumed, depth)
        call = re.match(r"(new\s+)?([A-Za-z_$][\w$.]*)\s*(?:<[^()]*>)?\s*\(", text)
        if call and _match(masked, a + call.end() - 1) == b - 1:
            name = call.group(2)
            builder = self._builder(fm, name, consumed, depth)
            if builder is not None:
                return builder
            spans = _args(masked, a + call.end() - 1, _match)
            if spans and name not in ("encodeURIComponent", "String", "JSON.stringify"):
                # A helper such as `at(template, id)` keeps its first argument.
                return self.resolve(fm, *spans[0], consumed, depth + 1)
        return None

    def _member(
        self, fm: FileModel, member: re.Match[str], consumed: set, depth: int
    ) -> list[str] | None:
        """``ROUTES.key`` is one value, and ``ROUTES[expr]`` is each value."""
        hit = self.lookup(fm, member.group(1), "object")
        if hit is None:
            return None
        other, entries = hit
        keys = [member.group(2)] if member.group(2) else list(entries)
        out: list[str] = []
        for key in keys:
            if key not in entries:
                return None
            got = self.resolve(other, *entries[key], consumed, depth + 1)
            if got is None:
                return None
            out += got
        return out

    def _union(self, fm, spans, consumed, depth):
        out: list[str] = []
        for s in spans:
            got = self.resolve(fm, *s, consumed, depth + 1)
            if got:
                out += got
        return out or None

    def _builder(self, fm: FileModel, name: str, consumed, depth) -> list[str] | None:
        """The paths that a path builder returns."""
        target = fm
        local = name
        if name not in {f.name for f in fm.fns} and name in fm.imports:
            rel, exported = fm.imports[name]
            target = self.files.get(rel)  # type: ignore[assignment]
            if target is None:
                return None
            local = target.exports.get(exported, exported)
        for fn in target.fns:
            if fn.name != local:
                continue
            masked = target.masked
            spans: list[tuple[int, int]] = []
            if masked[fn.a] == "{":
                for hit in re.finditer(r"\breturn\b", masked[fn.a : fn.b]):
                    start = fn.a + hit.end()
                    spans.append((start, _expr_end(masked, start)))
            else:
                spans.append((fn.a, fn.b))
            out: list[str] = []
            for s in spans:
                got = self.resolve(target, *s, consumed, depth + 1)
                if got:
                    out += [g for g in got if g.startswith("/api/") or g.startswith("/")]
            return out or None
        return None


def _default_fetchers(masked: str, fn: Fn) -> set[str]:
    """Parameters that default to ``fetch``: ``post: typeof fetch = fetch``."""
    head = masked[max(0, fn.start) : fn.a]
    return set(re.findall(r"(\w+)\s*(?::[^=,()]+)?=\s*fetch\b", head))


def _aliases(fm: FileModel, fn: Fn) -> dict[str, int]:
    """Each name in ``fn`` that stands for a parameter's path."""
    out = {p: i for i, p in enumerate(fn.params) if p and _PATH_PARAM.search(p)}
    body = fm.masked[fn.a : fn.b]
    for hit in re.finditer(r"\b(?:const|let)\s+(\w+)\s*(?::[^=]+)?=\s*([^;\n]+)", body):
        for name, index in list(out.items()):
            if re.search(rf"\b{re.escape(name)}\b", hit.group(2)):
                out.setdefault(hit.group(1), index)
    return out


def _param_ref(src, masked, a, b, aliases, read_literal, const=lambda name: None):
    """``(param index, text before it)`` when ``a..b`` sends a parameter.

    ``const`` gives the one text of a constant, for a prefix such as
    ``${KNOWLEDGE_API}${path}``.
    """
    text = masked[a:b].strip()
    if text in aliases:
        return aliases[text], ""
    if masked[a] in "`'\"":
        parts, _ = read_literal(src, masked, a)
        before = ""
        for kind, value in parts:
            if kind == "text":
                before += value
                continue
            name = value.strip()
            if name in aliases:
                return aliases[name], before
            known = const(name)
            if known is None:
                return None
            before += known
    plus = re.fullmatch(r"([\"'])([^\"']*)\1\s*\+\s*(\w+)", src[a:b].strip())
    if plus and plus.group(3) in aliases:
        return aliases[plus.group(3)], plus.group(2)
    return None


_CALL = re.compile(
    r"\.\s*(fetch|fetchImpl|fetchFn)\s*\("
    r"|(?:\bnew\s+)?(?<![\w$.])(?:navigator\.)?([A-Za-z_$][\w$]*)\s*(?:<[^()<>]*(?:<[^()<>]*>)?[^()<>]*>)?\s*\("
    r"|\(\s*[\w$.]+\s*\?\?\s*(fetch)\s*\)\s*\("
)


def _calls(masked: str, a: int, b: int) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for hit in _CALL.finditer(masked, a, b):
        name = hit.group(1) or hit.group(2) or hit.group(3)
        if name in _NOT_A_NAME:
            continue
        out.append((name, hit.end() - 1))
    return out


def _method_in(src: str, masked: str, a: int, b: int) -> str | None:
    """The verb of ``method`` in an object at ``a..b``: ``"POST"``, ``"=name"``
    for a name, or ``None`` when the object names no method."""
    for hit in re.finditer(r"\bmethod\b\s*(:)?\s*", masked[a:b]):
        at = a + hit.end()
        if hit.group(1) is None:
            return "=method"
        lit = re.match(r"[\"'`]([A-Za-z]+)[\"'`]", src[at : at + 12])
        if lit:
            return lit.group(1).upper()
        name = re.match(r"[\w$.?]+", masked[at:])
        cond = re.match(r"[^,}]*\?\s*[\"']([A-Za-z]+)[\"']\s*:\s*[\"']([A-Za-z]+)[\"']", src[at:b])
        if cond:
            return cond.group(1).upper()
        return "=" + (name.group(0) if name else "?")
    return None


def _enclosing(fm: FileModel, offset: int) -> Fn | None:
    best: Fn | None = None
    for fn in fm.fns:
        if fn.a <= offset < fn.b and (best is None or fn.a >= best.a):
            best = fn
    return best


def _join(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if path == "" or path.startswith(("/", "?")) or prefix.endswith("/"):
        return prefix + path
    return prefix + "/" + path


def normal(path: str) -> str:
    """A path as a template: no query, ``{}`` for each id segment.

    As F3's ``route_path``: an expression that fills a whole segment is an
    id. An expression after text in its segment is a suffix that code builds,
    for example a query string, and is dropped.
    """
    path = re.sub(r"\{[^{}]*\}", "{}", path)
    path = path.split("?", 1)[0].split("#", 1)[0]
    segs: list[str] = []
    for seg in path.split("/"):
        if "{}" in seg and seg != "{}":
            head = seg.split("{}", 1)[0]
            if head:
                segs.append(head)
            else:
                segs.append("{}")
            break
        segs.append(seg)
    out = "/".join(segs)
    out = re.sub(r"/{2,}", "/", out)
    return out.rstrip("/") or "/"


# ── The BFF: which gateway route a BFF path reaches ─────────────────────────


@dataclass(frozen=True)
class BffRoute:
    #: The BFF template, ``/api/...``, with ``{}`` for a dynamic segment and
    #: ``**`` for a catch-all.
    template: str
    file: str
    #: ``verb -> gateway paths`` of a route with its own handlers. Empty for
    #: a catch-all proxy, whose gateway path is its own tail.
    gateway: dict[str, tuple[str, ...]]
    #: The gateway prefix of a catch-all proxy, for example ``/email``.
    proxy: str = ""


@functools.cache
def bff_routes() -> tuple[BffRoute, ...]:
    """Every BFF route file, most specific first."""
    mask, match, read_literal = _tokenizer()
    out: list[BffRoute] = []
    for path in sorted(BFF_DIR.rglob("route.ts")):
        rel = path.relative_to(SRC).as_posix()
        segs = path.parent.relative_to(SRC / "app").as_posix().split("/")
        tmpl: list[str] = []
        for seg in segs:
            if seg.startswith("[[...") or seg.startswith("[..."):
                tmpl.append("**")
            elif seg.startswith("["):
                tmpl.append("{}")
            else:
                tmpl.append(seg)
        template = "/" + "/".join(tmpl)
        src = path.read_text(encoding="utf-8")
        masked = mask(src)
        proxy = ""
        gateway: dict[str, tuple[str, ...]] = {}
        if template.endswith("/**"):
            hit = re.search(r"\$\{GATEWAY_URL\}(/[\w-]+)/\$\{", src) or re.search(
                r"[\"'`](/[\w-]+)/\$\{path", src
            )
            proxy = hit.group(1) if hit else ""
        else:
            for fn in re.finditer(
                r"\bexport\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\s*\(", masked
            ):
                params_close = match(masked, fn.end() - 1)
                body = masked.index("{", params_close)
                end = match(masked, body)
                found: list[str] = []
                k = body
                while k < end:
                    if masked[k] in "'\"`":
                        parts, stop = read_literal(src, masked, k)
                        text = "".join(v if kd == "text" else "{}" for kd, v in parts)
                        if text.startswith("{}/") and "GATEWAY" in src[k : k + 20]:
                            found.append(normal(text[2:]))
                        elif text.startswith("/") and not text.startswith("/api/"):
                            before = masked[max(0, k - 40) : k]
                            if re.search(
                                r"(?:proxyToGateway|gatewayFetch|gatewayJson)\s*\(\s*$", before
                            ):
                                found.append(normal(text))
                        k = stop
                        continue
                    k += 1
                gateway[fn.group(1)] = tuple(dict.fromkeys(found))
        out.append(BffRoute(template, rel, gateway, proxy))
    # Next.js: a static segment beats a dynamic one, and both beat a catch-all.
    out.sort(key=lambda r: (r.template.endswith("/**"), r.template.count("{}"), -len(r.template)))
    return tuple(out)


def _seg_match(pattern: list[str], segs: list[str]) -> bool:
    if pattern and pattern[-1] == "**":
        head = pattern[:-1]
        if len(segs) < len(head):
            return False
        return all(p == "{}" or p == s or s == "{}" for p, s in zip(head, segs, strict=False))
    if len(pattern) != len(segs):
        return False
    # A UI segment that code fills, such as `${kind}` for approve or reject,
    # can be any static segment of the BFF.
    return all(p == "{}" or p == s or s == "{}" for p, s in zip(pattern, segs, strict=True))


def bff_route_for(path: str, routes: tuple[BffRoute, ...]) -> BffRoute | None:
    segs = path.strip("/").split("/")
    for route in routes:
        if _seg_match(route.template.strip("/").split("/"), segs):
            return route
    return None


def gateway_paths(verb: str, path: str, routes: tuple[BffRoute, ...]) -> tuple[str, ...] | None:
    """The gateway paths that a BFF request reaches, or ``None`` when no BFF
    route serves it."""
    route = bff_route_for(path, routes)
    if route is None:
        return None
    if route.template.endswith("/**"):
        head = route.template[: -len("/**")]
        tail = path[len(head) :] if path.startswith(head) else ""
        # A dynamic BFF segment that the catch-all forwards keeps its place.
        return (normal(route.proxy + tail),)
    return route.gateway.get(verb, ())


# ── The tools: which gateway paths each tool function names ─────────────────

_VERB_FUNCS = {
    "get": "GET",
    "post": "POST",
    "patch": "PATCH",
    "put": "PUT",
    "delete": "DELETE",
}


@dataclass
class ToolModule:
    rel: str
    #: function -> [(verb or "*", gateway template)]
    paths: dict[str, list[tuple[str, str]]]
    #: function -> module functions it calls
    calls: dict[str, set[str]]

    def reach(self, name: str) -> list[tuple[str, str]]:
        seen: set[str] = set()
        stack = [name]
        out: list[tuple[str, str]] = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            out += self.paths.get(cur, [])
            stack += list(self.calls.get(cur, ()))
        return out


def _template(node: ast.AST, consts: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if node.value.startswith("/") else None
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.JoinedStr):
        out = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                out += str(part.value)
            else:
                out += "{}"
        return out if out.startswith("/") else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _template(node.left, consts)
        return left + "{}" if left else None
    return None


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


_HTTP_VERBS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})
_PY_PATH_PARAM = re.compile(r"^(?:path|route|suffix|tail|url|endpoint)$")


def _module_consts(tree: ast.Module) -> dict[str, str]:
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                got = _template(node.value, {})
                if got:
                    consts[target.id] = got
    return consts


def _py_seams(
    fns: list[ast.FunctionDef | ast.AsyncFunctionDef], consts: dict[str, str]
) -> dict[str, tuple[int, str]]:
    """``{function: (index of its path argument, the prefix it adds)}``.

    A seam is a helper that puts a constant before a path argument:
    ``f"{PROFILE_ROUTE}{path}"`` or ``PROFILE_ROUTE + path``.
    """
    out: dict[str, tuple[int, str]] = {}
    for fn in fns:
        params = [a.arg for a in fn.args.args]
        for index, name in enumerate(params):
            if not _PY_PATH_PARAM.match(name):
                continue
            for sub in ast.walk(fn):
                if isinstance(sub, ast.JoinedStr):
                    # `f"{_gateway_url()}{PROFILE_ROUTE}{path}"`: a constant
                    # right before the parameter.
                    for head, tail in itertools.pairwise(sub.values):
                        if (
                            isinstance(head, ast.FormattedValue)
                            and isinstance(head.value, ast.Name)
                            and head.value.id in consts
                            and isinstance(tail, ast.FormattedValue)
                            and isinstance(tail.value, ast.Name)
                            and tail.value.id == name
                        ):
                            out[fn.name] = (index, consts[head.value.id])
                if (
                    isinstance(sub, ast.BinOp)
                    and isinstance(sub.op, ast.Add)
                    and isinstance(sub.left, ast.Name)
                    and sub.left.id in consts
                    and isinstance(sub.right, ast.Name)
                    and sub.right.id == name
                ):
                    out[fn.name] = (index, consts[sub.left.id])
    return out


def _call_route(sub: ast.Call, callee: str, consts, seams) -> tuple[str, str, ast.AST] | None:
    """``(verb, template, the path node)`` of a call that sends a request."""
    args = list(sub.args)
    verbs = [
        a.value.upper()
        for a in args
        if isinstance(a, ast.Constant)
        and isinstance(a.value, str)
        and a.value.upper() in _HTTP_VERBS
    ]
    if callee in seams:
        index, prefix = seams[callee]
        node: ast.AST = args[index] if len(args) > index else sub
        if node is sub or (isinstance(node, ast.Constant) and node.value == ""):
            tmpl: str | None = ""  # The seam's own prefix, with no tail.
        else:
            tmpl = _template(node, consts)
        if tmpl is None:
            return None
        return (verbs[0] if verbs else "*"), prefix + tmpl, node
    verb = _VERB_FUNCS.get(callee.lstrip("_").lower())
    if verb is None and verbs:
        verb = verbs[0]
    if verb is None:
        return None
    for arg in args:
        tmpl = _template(arg, consts)
        if tmpl:
            return verb, tmpl, arg
    return None


def tool_module(rel: str) -> ToolModule:
    tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
    consts = _module_consts(tree)
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    names = {n.name for n in fns}
    seams = _py_seams(fns, consts)
    paths: dict[str, list[tuple[str, str]]] = {}
    calls: dict[str, set[str]] = {}
    for node in fns:
        found: list[tuple[str, str]] = []
        called: set[str] = set()
        seen: set[int] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                callee = _callee(sub)
                if callee in names:
                    called.add(callee)
                hit = _call_route(sub, callee, consts, seams)
                if hit:
                    found.append((hit[0], normal(hit[1])))
                    seen.add(id(hit[2]))
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in names:
                # A helper passed by name, for example to a retry wrapper.
                called.add(sub.id)
        for sub in ast.walk(node):
            if id(sub) in seen:
                continue
            if isinstance(sub, (ast.Constant, ast.JoinedStr)) or (
                isinstance(sub, ast.Name) and sub.id in consts
            ):
                tmpl = _template(sub, consts)
                if tmpl and re.match(r"^/[a-z][\w-]*/", tmpl + "/") and " " not in tmpl:
                    found.append(("*", normal(tmpl)))
        paths[node.name] = found
        calls[node.name] = called
    return ToolModule(rel, paths, calls)


def path_matches(tool_path: str, route: str) -> bool:
    """Does a tool's path template name a gateway route template?

    A ``{}`` on either side matches one segment. A tool that builds the tail
    of a path in code (``/crm/{}``) reaches each route of that shape.
    """
    a = tool_path.strip("/").split("/")
    b = route.strip("/").split("/")
    if len(a) != len(b):
        return False
    return all(x == y or x == "{}" or y == "{}" for x, y in zip(a, b, strict=True))


# ── The tool registries: which names the chat can call ──────────────────────


def registered_tools() -> set[str]:
    """Each tool name that some agent or the platform registers.

    The registries: each agent's ``config.json`` scopes, the platform's
    ``SKILL_FAMILIES``, the exports of the skill packages, and the profile
    tools of the default assistant.
    """
    import json

    import skill_my_tasks
    import skill_projects
    from acb_skills import knowledge_tools, profile_tools
    from acb_skills.skill_families import SKILL_FAMILIES

    names: set[str] = set()
    for config in sorted((REPO_ROOT / "apps" / "agents").glob("*/config.json")):
        data = json.loads(config.read_text(encoding="utf-8"))
        names |= set(data.get("own_tool_scope") or ()) | set(data.get("tool_scope") or ())
    for family in SKILL_FAMILIES.values():
        names |= set(family["tools"])
    names |= set(skill_projects.__all__) | set(skill_my_tasks.__all__)
    names |= set(knowledge_tools.__all__)
    names |= {fn.__name__ for fn in profile_tools.WRITE_TOOLS} | {"resolve_person"}
    return names


# ── The inventory: each action, its gateway route, and its decision ─────────


@dataclass
class Action:
    """One request that the UI can send, with every place that sends it."""

    key: str
    verb: str
    path: str
    app: str | None
    sites: list[Site]
    #: The gateway paths that the BFF route reaches, or ``None`` when no BFF
    #: route serves the path.
    gateway: tuple[str, ...] | None

    def where(self) -> str:
        first = self.sites[0]
        more = f" (+{len(self.sites) - 1} more)" if len(self.sites) > 1 else ""
        name = f" in {first.where}" if first.where else ""
        return f"{first.file}:{first.line}{name}{more}"


def inventory(project: Project, skip: tuple[str, ...]) -> tuple[dict[str, Action], list[str]]:
    """Each action of the UI outside the files in ``skip``, and the unread calls."""
    from acb_skills import chat_parity

    routes = bff_routes()
    sites, unread = project.sites()
    out: dict[str, Action] = {}
    for site in sites:
        if site.file in skip:
            continue
        act = out.get(site.action)
        if act is None:
            act = Action(
                site.action,
                site.verb,
                site.path,
                chat_parity.app_of(site.path),
                [],
                gateway_paths(site.verb, site.path, routes),
            )
            out[site.action] = act
        act.sites.append(site)
    for act in out.values():
        act.sites.sort(key=lambda s: (s.file, s.line))
    return out, [u for u in unread if u.split(":", 1)[0] not in skip]


@dataclass(frozen=True)
class Decision:
    #: ``covered``, ``exempt`` or ``debt``.
    state: str
    #: ``table``, ``projects manifest`` or ``knowledge manifest``.
    source: str
    #: The tool, the kind and reason, or the wave.
    detail: str


def _concrete(path: str) -> str:
    return path.replace("{}", "_")


def derived(act: Action) -> Decision | None:
    """The decision that the Projects or the Knowledge manifest gives."""
    if not act.gateway:
        return None
    gw = act.gateway[0]
    if gw.startswith("/projects/"):
        import skill_projects
        from skill_projects import manifest as pm

        row = pm.route_for(act.verb, _concrete(gw))
        if row is None:
            return Decision("unrouted", "projects manifest", f"no row for {act.verb} {gw}")
        if row.cls == "X":
            return Decision("exempt", "projects manifest", f"class X: {row.reason}")
        if row.tool in skill_projects.__all__:
            return Decision("covered", "projects manifest", row.tool)
        return Decision(
            "debt", "projects manifest", f"{row.tool} is PLANNED: {pm.PLANNED.get(row.tool, '?')}"
        )
    if gw.startswith("/knowledge/") or gw == "/knowledge":
        from acb_skills import knowledge_manifest as km
        from acb_skills import knowledge_tools as kt

        row = km.route_for(act.verb, _concrete(gw))
        if row is None:
            return Decision("unrouted", "knowledge manifest", f"no row for {act.verb} {gw}")
        if row.cls == "X":
            return Decision("exempt", "knowledge manifest", f"class X: {row.reason}")
        built = [t for t in (row.tool, *row.also) if t and t in kt.__all__]
        if built:
            return Decision("covered", "knowledge manifest", built[0])
        return Decision("debt", "knowledge manifest", f"planned in {row.planned or '?'}")
    return None


def decide(act: Action) -> Decision | None:
    """The one decision of an action: a row of the table, else the manifest's."""
    from acb_skills import chat_parity as cp

    if act.key in cp.COVERED:
        value = cp.COVERED[act.key]
        return Decision("covered", "table", value if isinstance(value, str) else value[0])
    if act.key in cp.EXEMPT:
        kind, reason = cp.EXEMPT[act.key]
        return Decision("exempt", "table", f"{kind}: {reason}")
    if act.key in cp.DEBT:
        return Decision("debt", "table", cp.DEBT[act.key])
    return derived(act)


# ── Reach: may a tool send an action's request? ─────────────────────────────


_TOOL_MODULES: dict[str, ToolModule] = {}


def tool_modules() -> list[ToolModule]:
    from acb_skills import chat_parity

    for rel in chat_parity.TOOL_SOURCES:
        if rel not in _TOOL_MODULES:
            _TOOL_MODULES[rel] = tool_module(rel)
    return [_TOOL_MODULES[rel] for rel in chat_parity.TOOL_SOURCES]


def tool_routes(tool: str) -> list[tuple[str, str]] | None:
    """Each ``(verb, path)`` that ``tool`` names, or ``None`` when no tool
    source defines a function of that name."""
    found = False
    out: list[tuple[str, str]] = []
    for mod in tool_modules():
        if tool in mod.paths:
            found = True
            out += mod.reach(tool)
    return out if found else None


def reaches(tool: str, verb: str, gateway: str) -> bool:
    """Does ``tool`` name ``gateway`` with ``verb``, or with a verb that its
    source builds in code (``*``)?"""
    for v, path in tool_routes(tool) or ():
        if v in (verb, "*") and path_matches(path, gateway):
            return True
    return False
