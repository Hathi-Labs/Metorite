"""P-0 — each UI action has a chat tool, a recorded exemption, or a debt row.

Spec: ``project-docs/specs/chat_parity.md`` (Metorite task 1238). The owner's
rule, 2026-11-01: each action that a member can do by a click in the app is
also possible from the chat. The table is ``acb_skills.chat_parity``, and the
reader is ``tests/unit/_chat_parity_reader.py``.

**What it reads.** Each request that the control plane can send to its BFF,
from the TypeScript source, as fence F3 reads the Projects client. An action
is one verb and one BFF path. The Projects client files stay with F3
(``test_projects_ui_actions.py``). A request to a Projects or a Knowledge
route from any other file takes its decision from that app's manifest.

1. Each action is in exactly one of ``COVERED``, ``EXEMPT`` and ``DEBT``, or
   its app's manifest decides it. A new action fails, and the message names
   it, the file and the function that send it, and its gateway route.
2. No row names an action that the UI no longer sends.
3. Each ``COVERED`` tool is a registered tool, and its own source names the
   gateway route of the action, with the same verb. So a false claim fails.
4. Each exemption names a kind of ``EXEMPT_KINDS`` and a real reason.
5. Each debt row names a wave that holds its app.
6. The debt of each app equals its ceiling. A new debt fails, and a paid
   debt must lower the ceiling in the same change. So the list only shrinks.
7. The requests that the reader cannot follow equal their ceiling per file.

No database, no gateway process and no node runtime.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("skill_my_tasks", reason="skill-my-tasks not installed")

import skill_projects
from acb_skills import chat_parity as cp
from skill_projects import manifest as pm

from tests.unit import _chat_parity_reader as r
from tests.unit.test_projects_ui_actions import CLIENT_FILES as F3_CLIENT_FILES
from tests.unit.test_projects_ui_actions import catalogue as f3_catalogue

TABLES = ("COVERED", "EXEMPT", "DEBT")


@functools.cache
def _project() -> r.Project:
    return r.Project()


@functools.cache
def _read() -> tuple[dict[str, r.Action], tuple[str, ...]]:
    acts, unread = r.inventory(_project(), cp.F3_FILES)
    return acts, tuple(unread)


def _tables() -> dict[str, dict[str, Any]]:
    return {t: dict(getattr(cp, t)) for t in TABLES}


def _decide(act: r.Action, tables: dict[str, dict[str, Any]]) -> r.Decision | None:
    if act.key in tables["COVERED"]:
        value = tables["COVERED"][act.key]
        return r.Decision("covered", "table", value if isinstance(value, str) else value[0])
    if act.key in tables["EXEMPT"]:
        kind, reason = tables["EXEMPT"][act.key]
        return r.Decision("exempt", "table", f"{kind}: {reason}")
    if act.key in tables["DEBT"]:
        return r.Decision("debt", "table", tables["DEBT"][act.key])
    return r.derived(act)


def _route(act: r.Action) -> str:
    if act.gateway is None:
        return "no BFF route serves it"
    if not act.gateway:
        return "the BFF route names no gateway path that the fence can read"
    return "gateway " + ", ".join(f"{act.verb} {g}" for g in act.gateway)


# ── The checks, as pure functions, so a mutation can drive each one ─────────


def unrecorded(acts: dict[str, r.Action], tables: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for key, act in sorted(acts.items()):
        d = _decide(act, tables)
        if d is None or d.state == "unrouted":
            out.append(f"{key} ({act.where()}) -> {_route(act)}")
    return out


def stale(acts: dict[str, r.Action], tables: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(f"{t}: {k}" for t in TABLES for k in tables[t] if k not in acts)


def in_two_tables(tables: dict[str, dict[str, Any]]) -> list[str]:
    seen = Counter(k for t in TABLES for k in tables[t])
    return sorted(k for k, n in seen.items() if n > 1)


def _gateway_of(act: r.Action, value: str | tuple[str, str]) -> list[tuple[str, str]]:
    if isinstance(value, tuple):
        verb, path = value[1].split(" ", 1)
        return [(verb, path)]
    return [(act.verb, g) for g in act.gateway or ()]


def false_claims(acts: dict[str, r.Action], tables: dict[str, dict[str, Any]]) -> list[str]:
    """Each ``COVERED`` row that the tool's own source does not bear out."""
    registered = r.registered_tools()
    bad: list[str] = []
    for key, value in sorted(tables["COVERED"].items()):
        act = acts.get(key)
        if act is None:
            continue  # The stale check names it.
        tool = value if isinstance(value, str) else value[0]
        if tool not in registered:
            bad.append(f"{key}: {tool} is not a registered chat tool")
            continue
        if r.tool_routes(tool) is None:
            bad.append(f"{key}: no file of TOOL_SOURCES defines {tool}")
            continue
        routes = _gateway_of(act, value)
        if not routes:
            bad.append(
                f"{key}: the fence cannot read the gateway route of {act.path}. "
                'Write it in the row: ("tool", "VERB /gateway/path").'
            )
            continue
        if not any(r.reaches(tool, verb, path) for verb, path in routes):
            names = ", ".join(f"{v} {p}" for v, p in routes)
            bad.append(f"{key}: {tool} does not send {names}")
        if isinstance(value, tuple) and not _bff_names(act, routes[0][1]):
            bad.append(f"{key}: the BFF route of {act.path} does not name {value[1]}")
    return bad


def _bff_names(act: r.Action, gateway: str) -> bool:
    """Does the BFF route file, or a file that it imports by a relative
    path, name each static segment of ``gateway``?"""
    route = r.bff_route_for(act.path, r.bff_routes())
    if route is None:
        return False
    path = r.SRC / route.file
    text = path.read_text(encoding="utf-8")
    for spec in re.findall(r"from\s+[\"'](\.[^\"']+)[\"']", text):
        for ext in (".ts", ".tsx"):
            near = (path.parent / (spec + ext)).resolve()
            if near.is_file():
                text += near.read_text(encoding="utf-8")
    return all(seg in text for seg in gateway.strip("/").split("/") if seg != "{}")


def bad_exemptions(tables: dict[str, dict[str, Any]]) -> list[str]:
    bad: list[str] = []
    for key, value in sorted(tables["EXEMPT"].items()):
        if not (isinstance(value, tuple) and len(value) == 2):
            bad.append(f"{key}: an exemption is (kind, reason)")
            continue
        kind, reason = value
        if kind not in cp.EXEMPT_KINDS:
            bad.append(f"{key}: {kind!r} is not a kind of EXEMPT_KINDS")
        if len(reason.strip()) < 20:
            bad.append(f"{key}: no real reason ({reason!r})")
    return bad


def bad_debt(acts: dict[str, r.Action], tables: dict[str, dict[str, Any]]) -> list[str]:
    bad: list[str] = []
    for key, wave in sorted(tables["DEBT"].items()):
        if wave not in cp.WAVES:
            bad.append(f"{key}: {wave!r} is not a wave of WAVES")
            continue
        act = acts.get(key)
        if act is not None and act.app not in cp.WAVES[wave][1]:
            bad.append(f"{key}: wave {wave} does not hold the app {act.app}")
    return bad


def projects_debt() -> int:
    """The Projects debt that F3 holds: a planned gap, or a planned tool."""
    planned = sum(1 for tool in pm.UI_ACTIONS.values() if tool in pm.PLANNED)
    return len(pm.UI_PLANNED) + planned


def debt_counts(acts: dict[str, r.Action], tables: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for act in acts.values():
        d = _decide(act, tables)
        if d is not None and d.state == "debt":
            counts[act.app or "?"] += 1
    counts["projects"] += projects_debt()
    return {app: n for app, n in counts.items() if n}


def ratchet(counts: dict[str, int], ceiling: dict[str, int], what: str) -> list[str]:
    bad: list[str] = []
    for key in sorted(set(counts) | set(ceiling)):
        have, cap = counts.get(key, 0), ceiling.get(key, 0)
        if have > cap:
            bad.append(f"{key}: {have} {what}, over the ceiling of {cap}")
        elif have < cap:
            bad.append(f"{key}: {have} {what}, under the ceiling of {cap}. Lower it to {have}.")
    return bad


def unread_counts(unread: tuple[str, ...]) -> dict[str, int]:
    return dict(Counter(u.split(":", 1)[0] for u in unread))


def manifest_duplicates(acts: dict[str, r.Action], tables: dict[str, dict[str, Any]]) -> list[str]:
    """A row for a route that its app's manifest already gives a tool."""
    bad: list[str] = []
    for t in TABLES:
        for key in sorted(tables[t]):
            act = acts.get(key)
            d = r.derived(act) if act is not None else None
            if d is not None and d.state == "covered":
                bad.append(f"{t}: {key} — the {d.source} gives it {d.detail}. Drop the row.")
    return bad


# ── 0. The reader reads the UI ──────────────────────────────────────────────


def test_the_reader_finds_the_actions_of_every_app() -> None:
    """A reader that found nothing would pass every check below."""
    acts, _ = _read()
    assert len(acts) >= 500
    apps = Counter(a.app for a in acts.values())
    assert None not in apps, sorted(k for k, a in acts.items() if a.app is None)
    assert len(apps) >= 15, apps
    shapes = {
        # A seam with a prefix (`gatewayFetch` adds `/api`).
        "POST /api/email/drafts/send": "app/email/lib/api.ts",
        # A seam that adds `/api/crm`, an id in the path, and a verb in a
        # variable (`const init = { method: "POST" }`).
        "PATCH /api/crm/{}/{}": "app/crm/lib/api.ts",
        "POST /api/crm/sync/zoho": "app/crm/lib/api.ts",
        # A constant prefix: `${KNOWLEDGE_API}${path}`.
        "GET /api/knowledge/spaces": "app/knowledge/lib/api.ts",
        # An imported seam (`projectsCall`) and an object of routes.
        "POST /api/projects/my/tasks": "app/tasks/lib/lens.ts",
        "GET /api/projects/my/today": "app/tasks/lib/lens.ts",
        # A seam whose verb is an argument (`sendJSON(path, "POST", body)`).
        "POST /api/whatsapp/chats/{}/draft": "app/whatsapp/lib/api.ts",
        # A link that the browser follows.
        "GET /api/email/attachments/{}/download": "app/email/lib/api.ts",
        # A plain `fetch` in a component.
        "DELETE /api/agent/{}": "app/agents/page.tsx",
    }
    for key, file in shapes.items():
        assert key in acts, f"the reader lost {key}"
        assert file in {s.file for s in acts[key].sites}, (key, acts[key].where())


def test_the_projects_client_stays_with_f3() -> None:
    """This fence leaves the Projects client to F3, so the two lists agree."""
    assert set(cp.F3_FILES) == set(F3_CLIENT_FILES)
    methods, _ = f3_catalogue()
    assert len(methods) >= 100


def test_every_action_has_an_app() -> None:
    acts, _ = _read()
    lost = sorted(f"{k} ({a.where()})" for k, a in acts.items() if a.app is None)
    assert not lost, "add a prefix to APP_OF in acb_skills/chat_parity.py:\n  " + "\n  ".join(lost)


# ── 1 and 2. Each action in exactly one table, and no stale row ─────────────


def test_every_ui_action_is_covered_exempt_or_debt() -> None:
    acts, _ = _read()
    missing = unrecorded(acts, _tables())
    assert not missing, (
        "These UI actions have no decision. Add each one to "
        "packages/acb_skills/acb_skills/chat_parity.py: COVERED with the chat "
        "tool that sends the same request, EXEMPT with a kind and a reason, or "
        "DEBT with the wave that builds the tool:\n  " + "\n  ".join(missing)
    )


def test_a_new_ui_action_fails_by_name() -> None:
    """Mutation: a new client function is red until somebody decides."""
    rel = "app/email/lib/api.ts"
    src = (r.SRC / rel).read_text(encoding="utf-8")
    fake = (
        "\nexport async function zzFakeArchiveAll(): Promise<void> {\n"
        '  await gatewayFetch("/email/zz-fake/archive-all", { method: "POST" });\n'
        "}\n"
    )
    project = r.Project({rel: src + fake}, base=_project())
    acts, _ = r.inventory(project, cp.F3_FILES)
    line = (src + fake).count("\n", 0, (src + fake).index('await gatewayFetch("/email/zz-fake')) + 1
    assert unrecorded(acts, _tables()) == [
        f"POST /api/email/zz-fake/archive-all ({rel}:{line} in zzFakeArchiveAll) -> "
        "gateway POST /email/zz-fake/archive-all"
    ]


def test_no_row_is_in_two_tables() -> None:
    both = in_two_tables(_tables())
    assert not both, "an action has one decision, not two:\n  " + "\n  ".join(both)


def test_no_row_names_an_action_the_ui_does_not_send() -> None:
    acts, _ = _read()
    gone = stale(acts, _tables())
    assert not gone, (
        "These rows name UI actions that no file sends any more. A stale row "
        "is a claim nobody checks. Drop them:\n  " + "\n  ".join(gone)
    )


def test_a_stale_row_fails() -> None:
    """Mutation: a row whose action the UI no longer sends."""
    acts, _ = _read()
    tables = _tables()
    tables["DEBT"]["POST /api/email/zz-gone"] = "P-2"
    assert stale(acts, tables) == ["DEBT: POST /api/email/zz-gone"]


def test_no_row_repeats_a_manifest_decision() -> None:
    """Projects and Knowledge keep their own tables. A row here is only for
    a route that the manifest gives no built tool."""
    acts, _ = _read()
    bad = manifest_duplicates(acts, _tables())
    assert not bad, "\n  ".join(["these rows copy a manifest:", *bad])


# ── 3. Each claim is true ───────────────────────────────────────────────────


def test_every_covered_tool_sends_the_route_of_its_action() -> None:
    acts, _ = _read()
    bad = false_claims(acts, _tables())
    assert not bad, "these COVERED claims are false:\n  " + "\n  ".join(bad)


def test_a_false_claim_fails() -> None:
    """Mutation: a tool that sends another route, and a name that is no tool."""
    acts, _ = _read()
    tables = _tables()
    tables["COVERED"]["POST /api/email/drafts/send"] = "list_accounts"
    tables["COVERED"]["GET /api/email/accounts"] = "zz_no_such_tool"
    bad = false_claims(acts, tables)
    assert "POST /api/email/drafts/send: list_accounts does not send POST /email/drafts/send" in bad
    assert "GET /api/email/accounts: zz_no_such_tool is not a registered chat tool" in bad
    assert len(bad) == 2, bad


# ── 4 and 5. Each exemption has a kind, and each debt a wave ────────────────


def test_every_exemption_names_a_kind_and_a_reason() -> None:
    bad = bad_exemptions(_tables())
    assert not bad, "\n  ".join(["these exemptions are not exemptions:", *bad])


def test_a_bad_exemption_fails() -> None:
    tables = _tables()
    tables["EXEMPT"]["POST /api/email/send"] = ("convenience", "The chat is slower than a button.")
    tables["EXEMPT"]["POST /api/email/forward"] = ("oauth", "short")
    assert bad_exemptions(tables) == [
        "POST /api/email/forward: no real reason ('short')",
        "POST /api/email/send: 'convenience' is not a kind of EXEMPT_KINDS",
    ]


def test_every_debt_names_a_wave_of_its_app() -> None:
    acts, _ = _read()
    bad = bad_debt(acts, _tables())
    assert not bad, "\n  ".join(["these debt rows name no wave of their app:", *bad])


# ── 6 and 7. The ratchets ───────────────────────────────────────────────────


def test_the_debt_of_each_app_equals_its_ceiling() -> None:
    acts, _ = _read()
    bad = ratchet(debt_counts(acts, _tables()), cp.DEBT_CEILING, "actions in debt")
    assert not bad, (
        "The debt of an app may only go down. A new action needs a tool, not "
        "a debt row. Paid debt lowers DEBT_CEILING in the same change:\n  " + "\n  ".join(bad)
    )


def test_new_debt_fails() -> None:
    """Mutation: a covered action that moves to the debt list."""
    acts, _ = _read()
    tables = _tables()
    del tables["COVERED"]["POST /api/email/drafts/send"]
    tables["DEBT"]["POST /api/email/drafts/send"] = "P-2"
    counts = debt_counts(acts, tables)
    assert ratchet(counts, cp.DEBT_CEILING, "actions in debt") == [
        f"email: {cp.DEBT_CEILING['email'] + 1} actions in debt, over the ceiling of "
        f"{cp.DEBT_CEILING['email']}"
    ]


def test_the_unread_requests_of_each_file_equal_their_ceiling() -> None:
    _, unread = _read()
    bad = ratchet(unread_counts(unread), cp.UNREAD_CEILING, "requests the fence cannot read")
    assert not bad, (
        "Write the path of a request as a literal, or through a seam, so the "
        "fence can read it:\n  " + "\n  ".join([*bad, "", *unread])
    )


def test_projects_tools_named_by_f3_are_real() -> None:
    """The Projects debt comes from F3's tables. A planned tool is not built."""
    assert projects_debt() == len(pm.UI_PLANNED) + sum(
        1 for t in pm.UI_ACTIONS.values() if t in pm.PLANNED and t not in skill_projects.__all__
    )
