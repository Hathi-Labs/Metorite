"""F3 — every Projects UI client METHOD has a tool, or a recorded reason.

Spec: ``project-docs/specs/projects_agent_parity.md`` §6.4 (WS-46 P5, D91).

F1 (``test_projects_chat_coverage.py``) holds each ROUTE to a decision, and
F2 (``test_projects_field_parity.py``) holds each request FIELD. Both read the
API. A UI action can be new while its route is old, so this fence reads the
UI's own client, where a UI action first exists in code.

**What it reads.** Each method of each ``export const <name> = {...}`` object
in the Projects client files, and each exported function there that builds a
``/api/projects`` path. It reads them as TEXT, as
``test_projects_report_sections_lockstep.py`` does, because no runtime is
shared. For each method it reads the verb and the path of each gateway call,
and it finds the manifest row of that route. No hand list: the methods and
their routes come from the source.

1. Each method is in exactly one of ``manifest.UI_ACTIONS``, ``UI_EXEMPT``
   and ``UI_PLANNED``. A new method fails, and the message names it and its
   file.
2. No table names a method that the client no longer has.
3. Each call of each method lands on a manifest route.
4. Each ``UI_ACTIONS`` tool is exported or in ``PLANNED``, may reach the route
   that the method calls, and that route is not class X. So a false claim
   fails.
5. An exemption is allowed only where the route is class X. Where a tool can
   reach the route, the method must name the tool.
6. Each ``UI_PLANNED`` gap is a row of the spec's gap table, and its slice is
   not yet built. A slice that ships moves its rows to ``UI_ACTIONS``.
7. No Projects UI file outside the client files calls the gateway. So a new
   call site cannot hide from this fence in a component.

⚠️ The reader is a small tokenizer, not a TypeScript parser. It masks
comments and string text, and it splits an object at its top-level commas.
A shape it cannot read fails as UNREADABLE, by name, and never passes in
silence. That is the right failure: it forces somebody to look.

No database, no gateway process and no node runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import manifest as m

from tests.unit._projects_agent_fakes import REPO_ROOT
from tests.unit.test_projects_field_parity import _built_slices, _spec_gaps

SRC = REPO_ROOT / "workbench" / "control_plane" / "src"
PROJECTS_UI = SRC / "app" / "projects"

#: The Projects client files, relative to ``SRC``. §3.2 names the six objects
#: of ``api.ts`` and the import client. ``export.ts`` builds the one gateway
#: path that ``page.tsx`` fetches by itself (the CSV download).
CLIENT_FILES: tuple[str, ...] = (
    "app/projects/lib/api.ts",
    "app/projects/lib/importApi.ts",
    "app/projects/lib/export.ts",
)

#: The BFF proxy's prefix. ``call()`` adds it to a relative path.
BFF = "/api/projects/"

TABLES = ("UI_ACTIONS", "UI_EXEMPT", "UI_PLANNED")


# ── The reader ──────────────────────────────────────────────────────────────


def mask(src: str) -> str:
    """``src`` with each comment and the text of each string blanked.

    Offsets do not move, and newlines stay. A template literal keeps its
    ``${...}`` code, because a call or a brace can sit inside one. So the
    brackets of the masked text balance, and a search over it finds code only.
    """
    masker = _Masker(src)
    masker.code(0, stop_on_brace=False)
    return "".join(masker.out)


class _Masker:
    """The scanner behind :func:`mask`. Each method returns the next offset."""

    def __init__(self, src: str) -> None:
        self.src = src
        self.n = len(src)
        self.out = list(src)

    def blank(self, a: int, b: int) -> None:
        for k in range(a, min(b, self.n)):
            if self.out[k] != "\n":
                self.out[k] = " "

    def skip_quoted(self, i: int) -> int | None:
        """Past the comment or the string that starts at ``i``, or ``None``."""
        src, n = self.src, self.n
        two = src[i : i + 2]
        if two in ("//", "/*"):
            end = src.find("\n" if two == "//" else "*/", i + 2)
            end = n if end < 0 else end + (0 if two == "//" else 2)
            self.blank(i, end)
            return end
        if src[i] in "'\"":
            j = i + 1
            while j < n and src[j] not in (src[i], "\n"):
                j += 2 if src[j] == "\\" else 1
            self.blank(i + 1, j)
            return j + 1
        if src[i] == "`":
            return self.template(i)
        return None

    def code(self, i: int, stop_on_brace: bool) -> int:
        depth = 0
        while i < self.n:
            skipped = self.skip_quoted(i)
            if skipped is not None:
                i = skipped
                continue
            ch = self.src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0 and stop_on_brace:
                    return i
                depth -= 1
            i += 1
        return i

    def template(self, i: int) -> int:
        src, j = self.src, i + 1
        while j < self.n:
            ch = src[j]
            if ch == "`":
                return j + 1
            if ch == "$" and src[j + 1 : j + 2] == "{":
                j = self.code(j + 2, stop_on_brace=True) + 1
                continue
            width = 2 if ch == "\\" else 1
            self.blank(j, j + width)
            j += width
        return j


_OPEN = {"(": ")", "[": "]", "{": "}"}


def match(masked: str, i: int) -> int:
    """The index of the bracket that closes the one at ``i``."""
    depth = 0
    for j in range(i, len(masked)):
        ch = masked[j]
        if ch in _OPEN:
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError(f"no bracket closes the {masked[i]!r} at offset {i}")


def read_literal(src: str, masked: str, i: int) -> tuple[list[tuple[str, str]], int]:
    """The literal that starts at ``i``: ``[("text", s) | ("expr", code)]``."""
    quote = src[i]
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    j = i + 1
    while j < len(src):
        ch = src[j]
        if ch == "\\":
            buf.append(src[j + 1 : j + 2])
            j += 2
            continue
        if ch == quote:
            if buf:
                parts.append(("text", "".join(buf)))
            return parts, j + 1
        if quote == "`" and ch == "$" and src[j + 1 : j + 2] == "{":
            if buf:
                parts.append(("text", "".join(buf)))
                buf = []
            end = match(masked, j + 1)
            parts.append(("expr", src[j + 2 : end]))
            j = end + 1
            continue
        buf.append(ch)
        j += 1
    raise ValueError(f"the literal at offset {i} does not close")


def route_path(parts: list[tuple[str, str]]) -> str:
    """A literal path as a route template, prefix included.

    An expression that fills a whole segment is an id, and becomes ``{}``.
    An expression after other text in its segment is a suffix that code
    builds, for example a query string, and is dropped. So is all text from
    the first ``?``.
    """
    out = ""
    for kind, value in parts:
        if kind == "text":
            if "?" in value:
                out += value.split("?", 1)[0]
                break
            out += value
        elif out == "" or out.endswith("/"):
            out += "{}"
    if out.startswith(BFF):
        out = out[len(BFF) :]
    return "/projects/" + out.lstrip("/")


@dataclass
class Method:
    name: str
    file: str
    #: ``(verb, template)`` for each gateway call. ``{}`` marks an id.
    calls: list[tuple[str, str]] = field(default_factory=list)
    #: A call site whose path is not a literal the reader can follow.
    unreadable: list[str] = field(default_factory=list)


_CALL = re.compile(r"\b(call|projectsCall|fetch)\s*([<(])")
_VERB = re.compile(r"\bmethod\s*:\s*[\"'`]([A-Za-z]+)[\"'`]")


def _skip_generic(masked: str, i: int) -> int:
    """From the ``<`` at ``i``, the index after its closing ``>``."""
    depth = 0
    for j in range(i, len(masked)):
        ch = masked[j]
        if ch == "<":
            depth += 1
        elif ch == ">" and masked[j - 1] != "=":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError(f"no '>' closes the generic at offset {i}")


def _calls_in(src: str, masked: str, a: int, b: int) -> tuple[list, list, list[int]]:
    """Each gateway call between ``a`` and ``b``: ``(calls, unreadable, offsets)``."""
    calls: list[tuple[str, str]] = []
    unreadable: list[str] = []
    offsets: list[int] = []
    for hit in _CALL.finditer(masked, a, b):
        start = hit.start(2)
        if hit.group(2) == "<":
            start = _skip_generic(masked, start)
            while masked[start].isspace():
                start += 1
            if masked[start] != "(":
                continue  # `call<T>` that is not a call.
        close = match(masked, start)
        arg = start + 1
        while src[arg].isspace():
            arg += 1
        line = src.count("\n", 0, hit.start()) + 1
        if src[arg] not in "'\"`":
            if hit.group(1) != "fetch":
                unreadable.append(f"line {line}: {src[hit.start() : arg + 20].strip()}")
                offsets.append(hit.start())
            continue
        parts, _end = read_literal(src, masked, arg)
        raw = "".join(v for k, v in parts if k == "text")
        if hit.group(1) == "fetch" and not raw.startswith(BFF):
            continue  # Not a Projects gateway call.
        verb = _VERB.search(src, start, close)
        calls.append(((verb.group(1).upper() if verb else "GET"), route_path(parts)))
        offsets.append(hit.start())
    return calls, unreadable, offsets


def _bff_literals(src: str, masked: str, a: int, b: int) -> list[tuple[int, list]]:
    """Each string or template literal between ``a`` and ``b`` that names the
    BFF prefix, with its offset."""
    out: list[tuple[int, list]] = []
    i = a
    while i < b:
        if masked[i] in "'\"`":
            parts, end = read_literal(src, masked, i)
            if "".join(v for k, v in parts if k == "text").startswith(BFF):
                out.append((i, parts))
            i = end
            continue
        i += 1
    return out


_OBJECT = re.compile(r"\bexport\s+const\s+(\w+)\s*=\s*\{")
_FUNCTION = re.compile(r"\bexport\s+(?:async\s+)?function\s+(\w+)\s*[<(]")
#: The one request seam, ``async function call<T>(path, init)`` in api.ts.
#: Its own ``fetch`` is the seam, not a UI action.
_SEAM = re.compile(r"\basync\s+function\s+call\s*<")
_ENTRY = re.compile(r"(?:async\s+)?([A-Za-z_$][\w$]*)\s*([(:])")
_FN_VALUE = re.compile(r"\s*(?:async\b|function\b|\(|[A-Za-z_$][\w$]*\s*=>)")


def _entries(masked: str, o: int, c: int) -> list[tuple[int, int]]:
    """The spans of the top-level entries of the object ``{`` at ``o``."""
    spans: list[tuple[int, int]] = []
    depth = angle = 0
    start = o + 1
    for j in range(o + 1, c):
        ch = masked[j]
        if ch in _OPEN:
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == "<":
            angle += 1
        elif depth == 0 and ch == ">" and masked[j - 1] != "=":
            angle -= 1
        elif depth == 0 and angle == 0 and ch == ",":
            spans.append((start, j))
            start = j + 1
    spans.append((start, c))
    return [(a, b) for a, b in spans if masked[a:b].strip()]


def read_client(rel: str, src: str) -> tuple[dict[str, Method], list[str]]:
    """The client methods of one file, and each gateway call outside them."""
    masked = mask(src)
    stem = Path(rel).stem
    methods: dict[str, Method] = {}
    covered: list[tuple[int, int]] = []

    for obj in _OBJECT.finditer(masked):
        o = obj.end() - 1
        c = match(masked, o)
        for a, b in _entries(masked, o, c):
            lead = len(masked[a:b]) - len(masked[a:b].lstrip())
            head = _ENTRY.match(masked, a + lead)
            if head is None:
                continue
            is_fn = head.group(2) == "(" or bool(_FN_VALUE.match(masked, head.end()))
            if not is_fn:
                continue  # A constant, for example MAX_TIMELINE_PAGE.
            name = f"{obj.group(1)}.{head.group(1)}"
            calls, unreadable, _offsets = _calls_in(src, masked, a, b)
            methods[name] = Method(name, rel, calls, unreadable)
            covered.append((a, b))

    for seam in _SEAM.finditer(masked):
        params = masked.index("(", seam.end())
        body = masked.index("{", match(masked, params))
        covered.append((seam.start(), match(masked, body)))

    for fn in _FUNCTION.finditer(masked):
        params = masked.index("(", fn.end() - 1)
        body = masked.index("{", match(masked, params))
        end = match(masked, body)
        literals = _bff_literals(src, masked, body, end)
        calls, unreadable, offsets = _calls_in(src, masked, body, end)
        if not literals and not offsets:
            continue  # A helper that builds no gateway path.
        if not calls:
            verb = _VERB.search(src, body, end)
            calls = [
                ((verb.group(1).upper() if verb else "GET"), route_path(p)) for _, p in literals
            ]
        methods[f"{stem}.{fn.group(1)}"] = Method(f"{stem}.{fn.group(1)}", rel, calls, unreadable)
        covered.append((fn.start(), end))

    stray: list[str] = []
    sites = [off for off in _calls_in(src, masked, 0, len(src))[2]]
    sites += [off for off, _ in _bff_literals(src, masked, 0, len(src))]
    for off in sorted(set(sites)):
        if not any(a <= off < b for a, b in covered):
            line = src.count("\n", 0, off) + 1
            stray.append(f"{rel}:{line}")
    return methods, stray


def catalogue(sources: dict[str, str] | None = None) -> tuple[dict[str, Method], list[str]]:
    """Every client method of the Projects UI, and every stray gateway call."""
    methods: dict[str, Method] = {}
    stray: list[str] = []
    for rel in CLIENT_FILES:
        src = (sources or {}).get(rel)
        if src is None:
            src = (SRC / rel).read_text(encoding="utf-8")
        found, loose = read_client(rel, src)
        methods.update(found)
        stray.extend(loose)
    return methods, stray


# ── The checks, as pure functions, so a mutation can drive each one ─────────


def _tables() -> dict[str, dict[str, str]]:
    return {t: dict(getattr(m, t)) for t in TABLES}


def unrecorded(methods: dict[str, Method], tables: dict[str, dict[str, str]]) -> list[str]:
    return sorted(
        f"{name} ({meth.file})"
        for name, meth in methods.items()
        if not any(name in tables[t] for t in TABLES)
    )


def _row(verb: str, template: str) -> m.Route | None:
    return m.route_for(verb, template.replace("{}", "_"))


def false_claims(methods: dict[str, Method], tables: dict[str, dict[str, str]]) -> list[str]:
    """Each ``UI_ACTIONS`` claim that the manifest does not bear out."""
    built = set(skill_projects.__all__) | set(m.PLANNED)
    bad: list[str] = []
    for name, tool in sorted(tables["UI_ACTIONS"].items()):
        meth = methods.get(name)
        if meth is None:
            continue  # The stale check names it.
        if tool not in built:
            bad.append(f"{name}: {tool} is not an exported tool and not in PLANNED")
            continue
        for verb, template in meth.calls:
            row = _row(verb, template)
            if row is None:
                continue  # The route check names it.
            if row.cls == "X":
                bad.append(
                    f"{name}: {verb} {row.path} is class X, so no tool reaches it. "
                    "Record the method in UI_EXEMPT with a reason."
                )
            elif not m.reaches(tool, row.tool):
                bad.append(
                    f"{name}: {tool} may not reach {verb} {row.path}, which the "
                    f"manifest gives {row.tool}"
                )
    return bad


def false_exemptions(methods: dict[str, Method], tables: dict[str, dict[str, str]]) -> list[str]:
    """Each exemption on a route that a tool can reach, or with no real reason."""
    bad: list[str] = []
    for name, reason in sorted(tables["UI_EXEMPT"].items()):
        if len(reason.strip()) < 20:
            bad.append(f"{name}: no real reason ({reason!r})")
        meth = methods.get(name)
        for verb, template in meth.calls if meth else ():
            row = _row(verb, template)
            if row is not None and row.cls != "X":
                bad.append(
                    f"{name}: {row.tool} reaches {verb} {row.path}. Name the tool "
                    "in UI_ACTIONS instead of an exemption."
                )
    return bad


# ── 0. The reader reads the client ──────────────────────────────────────────


def test_the_reader_finds_the_client_objects_and_their_routes() -> None:
    """A reader that found nothing would pass every check below."""
    methods, _ = catalogue()
    objects = {name.split(".", 1)[0] for name in methods}
    assert objects == {
        "projectsApi",
        "attachmentsApi",
        "notificationsApi",
        "watchersApi",
        "projectWatchersApi",
        "intakeApi",
        "importApi",
        "export",
    }
    assert len(methods) >= 100
    # A constant of the object is not a method.
    assert "projectsApi.MAX_TIMELINE_PAGE" not in methods
    shapes = {
        "projectsApi.createTask": [("POST", "/projects/tasks")],
        "projectsApi.tasks": [("GET", "/projects/tasks")],
        "projectsApi.deleteStatus": [("DELETE", "/projects/statuses/{}")],
        "projectsApi.patchTask": [("PATCH", "/projects/tasks/{}")],
        "projectsApi.capacity": [("GET", "/projects/analytics/capacity")],
        "projectsApi.vocabularyImpact": [("GET", "/projects/vocabulary/{}/{}/impact")],
        "intakeApi.queue": [("GET", "/projects/intake")],
        "attachmentsApi.upload": [("POST", "/projects/tasks/{}/attachments")],
        "importApi.upload": [("POST", "/projects/import/runs")],
        "importApi.saveMapping": [("PUT", "/projects/import/runs/{}/mapping")],
        "export.exportPath": [("GET", "/projects/export/tasks.csv")],
    }
    for name, calls in shapes.items():
        assert methods[name].calls == calls, f"{name}: read {methods[name].calls}"


def test_every_gateway_call_of_a_method_is_readable() -> None:
    methods, _ = catalogue()
    bad = [f"{n} ({x.file}) {u}" for n, x in sorted(methods.items()) for u in x.unreadable]
    bad += [f"{n} ({x.file}) calls no gateway route" for n, x in methods.items() if not x.calls]
    assert not bad, (
        "The fence cannot read these calls. Write the path as a literal in the "
        "client method, so its route can be read:\n  " + "\n  ".join(bad)
    )


def test_no_gateway_call_sits_outside_a_client_method() -> None:
    """A call in a helper, or in a component, would hide from this fence."""
    _, stray = catalogue()
    for path in sorted(PROJECTS_UI.rglob("*.ts*")):
        rel = path.relative_to(SRC).as_posix()
        if rel in CLIENT_FILES or ".test." in path.name or path.suffix not in (".ts", ".tsx"):
            continue
        src = path.read_text(encoding="utf-8")
        masked = mask(src)
        for hit in re.finditer(r"\bprojectsCall\b", masked):
            stray.append(f"{rel}:{src.count(chr(10), 0, hit.start()) + 1} projectsCall")
        for off, _parts in _bff_literals(src, masked, 0, len(src)):
            stray.append(f"{rel}:{src.count(chr(10), 0, off) + 1} {BFF}")
    assert not stray, (
        "These Projects UI sites call the gateway outside a client method. Put "
        "the call in a method of lib/api.ts, so F3 holds it:\n  " + "\n  ".join(stray)
    )


# ── 1 and 2. Each method in exactly one table, and no stale row ─────────────


def test_every_client_method_is_mapped_exempt_or_planned() -> None:
    methods, _ = catalogue()
    missing = unrecorded(methods, _tables())
    assert not missing, (
        "These Projects UI client methods have no decision. Add each one to "
        "apps/skills/skill-projects/skill_projects/manifest.py: UI_ACTIONS with "
        "the tool that does the same act, UI_EXEMPT with a reason, or "
        "UI_PLANNED with its gap id:\n  " + "\n  ".join(missing)
    )


def test_a_new_client_method_fails_by_name() -> None:
    """Mutation: a method added to the client is red until somebody decides."""
    rel = "app/projects/lib/api.ts"
    src = (SRC / rel).read_text(encoding="utf-8")
    anchor = "export const projectsApi = {\n"
    assert anchor in src
    fake = (
        anchor + "  zzFakeAction: (taskId: string) =>\n"
        '    call<void>(`tasks/${taskId}/archive`, { method: "POST" }),\n'
    )
    methods, _ = catalogue({rel: src.replace(anchor, fake, 1)})
    assert methods["projectsApi.zzFakeAction"].calls == [("POST", "/projects/tasks/{}/archive")]
    assert unrecorded(methods, _tables()) == [f"projectsApi.zzFakeAction ({rel})"]


def test_a_new_free_call_site_fails_by_place() -> None:
    """Mutation: a gateway call in a new helper of a client file is stray."""
    rel = "app/projects/lib/importApi.ts"
    src = (SRC / rel).read_text(encoding="utf-8")
    loose = src + '\nconst sneak = () => projectsCall<void>("tree");\n'
    _, stray = catalogue({rel: loose})
    line = loose.count("\n", 0, loose.index("const sneak")) + 1
    assert stray == [f"{rel}:{line}"]


def test_no_method_is_in_two_tables() -> None:
    tables = _tables()
    seen: dict[str, list[str]] = {}
    for t in TABLES:
        for name in tables[t]:
            seen.setdefault(name, []).append(t)
    both = sorted(f"{n} in {ts}" for n, ts in seen.items() if len(ts) > 1)
    assert not both, "a method has one decision, not two:\n  " + "\n  ".join(both)


def test_no_table_names_a_method_the_client_does_not_have() -> None:
    methods, _ = catalogue()
    stale = sorted(f"{t}: {n}" for t in TABLES for n in _tables()[t] if n not in methods)
    assert not stale, "these rows name client methods that are gone:\n  " + "\n  ".join(stale)


# ── 3 to 5. Each call lands on a route, and each claim is true ──────────────


def test_every_call_lands_on_a_manifest_route() -> None:
    methods, _ = catalogue()
    lost = sorted(
        f"{n} ({x.file}): {verb} {template}"
        for n, x in methods.items()
        for verb, template in x.calls
        if _row(verb, template) is None
    )
    assert not lost, (
        "These client calls name no route of the manifest. Either the route "
        "does not exist, or the manifest lacks it (F1):\n  " + "\n  ".join(lost)
    )


def test_every_claimed_tool_reaches_the_route_the_method_calls() -> None:
    methods, _ = catalogue()
    bad = false_claims(methods, _tables())
    assert not bad, "these UI_ACTIONS claims are false:\n  " + "\n  ".join(bad)


def test_a_false_claim_fails() -> None:
    """Mutation: a tool that cannot do the act, and a tool on a class X route."""
    methods, _ = catalogue()
    tables = _tables()
    tables["UI_ACTIONS"]["projectsApi.createTask"] = "list_tasks"
    tables["UI_ACTIONS"]["projectsApi.deleteProject"] = "archive_project"
    tables["UI_ACTIONS"]["projectsApi.tree"] = "no_such_tool"
    bad = false_claims(methods, tables)
    assert any(b.startswith("projectsApi.createTask: list_tasks may not reach POST") for b in bad)
    assert any(b.startswith("projectsApi.deleteProject: DELETE /projects/nodes/") for b in bad)
    assert "projectsApi.tree: no_such_tool is not an exported tool and not in PLANNED" in bad
    assert len(bad) == 3, bad


def test_an_exemption_is_only_for_a_route_no_tool_reaches() -> None:
    methods, _ = catalogue()
    bad = false_exemptions(methods, _tables())
    assert not bad, "these UI_EXEMPT rows are not exemptions:\n  " + "\n  ".join(bad)


def test_a_false_exemption_fails() -> None:
    """Mutation: an exemption for a method whose route a tool reaches."""
    methods, _ = catalogue()
    tables = _tables()
    tables["UI_EXEMPT"]["projectsApi.createTask"] = "The chat does not make tasks, said nobody."
    tables["UI_EXEMPT"]["projectsApi.deleteTask"] = "short"
    bad = false_exemptions(methods, tables)
    assert bad == [
        "projectsApi.createTask: create_task reaches POST /projects/tasks. Name the tool "
        "in UI_ACTIONS instead of an exemption.",
        "projectsApi.deleteTask: no real reason ('short')",
    ]


# ── 6. Each planned method names an open gap ────────────────────────────────


def test_every_planned_method_names_an_open_gap_and_its_slice() -> None:
    gaps, built = _spec_gaps(), _built_slices()
    methods, _ = catalogue()
    for name, gap in m.UI_PLANNED.items():
        assert gap in gaps, f"{name}: {gap} is not a row of the gap table"
        assert gaps[gap] not in built, (
            f"{name}: {gaps[gap]} is built, so {gap} is closed. Move the method "
            "to UI_ACTIONS with the tool that closed it."
        )
        for verb, template in methods[name].calls:
            row = _row(verb, template)
            assert row is not None and row.cls == "X", (
                f"{name}: {row.tool if row else '?'} reaches {verb} {template} "
                "already. Move the method to UI_ACTIONS."
            )
