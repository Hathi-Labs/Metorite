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
   Each ``ALSO`` tool passes the same check, on an action that ``COVERED``
   holds (STU-B2c-2b: one door route, a list tool and three write tools).
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
from pathlib import Path
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


def false_also_claims(
    acts: dict[str, r.Action], tables: dict[str, dict[str, Any]],
    also: dict[str, tuple[str, ...]],
) -> list[str]:
    """Each ``ALSO`` tool that its own source does not bear out, with the check
    of :func:`false_claims`. A row needs a ``COVERED`` row of its action."""
    registered = r.registered_tools()
    bad: list[str] = []
    for key, tools in sorted(also.items()):
        if key not in tables["COVERED"]:
            bad.append(f"{key}: an ALSO row needs a COVERED row of the same action")
            continue
        act = acts.get(key)
        if act is None:
            continue  # The stale check names the COVERED row.
        routes = _gateway_of(act, tables["COVERED"][key])
        for tool in tools:
            if tool not in registered:
                bad.append(f"{key}: {tool} is not a registered chat tool")
            elif r.tool_routes(tool) is None:
                bad.append(f"{key}: no file of TOOL_SOURCES defines {tool}")
            elif not any(r.reaches(tool, verb, path) for verb, path in routes):
                names = ", ".join(f"{v} {p}" for v, p in routes)
                bad.append(f"{key}: {tool} does not send {names}")
    return bad


def _bff_names(act: r.Action, gateway: str) -> bool:
    """Does the BFF route file, or a file that it imports by a relative
    path, name each static segment of ``gateway``?"""
    route = r.bff_route_for(act.path, r.bff_routes(), act.verb)
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


def test_every_also_tool_sends_the_route_of_its_action() -> None:
    """STU-B2c-2b: the three record write tools send the door route of the
    package page, as the list tool of its ``COVERED`` row does."""
    acts, _ = _read()
    assert cp.ALSO["POST /api/studio/apps/{}/actions/{}"] == (
        "studio_record_create", "studio_record_update", "studio_record_delete",
        "studio_app_read")
    bad = false_also_claims(acts, _tables(), cp.ALSO)
    assert not bad, "these ALSO claims are false:\n  " + "\n  ".join(bad)


def test_the_delete_of_a_package_is_covered_by_its_chat_tool() -> None:
    """STU-A5c-2 pays the two delete requests of the app page (BETA-1): the
    preview and the DELETE are ``COVERED`` by ``studio_delete_package``, whose
    own source sends both gateway routes. The debt of AI Studio went from 3
    to 1. Mutation: drop the DELETE call from the tool -> red."""
    acts, _ = _read()
    pairs = {"GET /api/studio/packages/{}/delete-preview": ("GET",
                                                           "/studio/packages/{}/delete-preview"),
             "DELETE /api/studio/packages/{}": ("DELETE", "/studio/packages/{}")}
    for key, (verb, path) in pairs.items():
        assert key in acts, key
        assert cp.COVERED[key] == "studio_delete_package" and key not in cp.DEBT
        assert r.reaches("studio_delete_package", verb, path), (key, verb, path)
    # 1 after A5c-2 paid the two delete requests, plus Keep and Restore of PKG-10e-2 (task 2971, paid by P-9).
    # STU-K1a-ui (task 1203) added the template list and the one-step install (3 to 5). STU-K1d (task 2967) pays both with chat tools (5 to 3).
    assert cp.DEBT_CEILING["studio"] == 3


#: PAR-8a: each Workflows row whose UI path fits ``/api/workflows/hooks/{}`` by
#: its shape, and the gateway route that Next.js really reaches (the catch-all).
_HOOK_SHAPED = {
    ("GET", "/api/workflows/{}/runs"): "/workflows/{}/runs",
    ("POST", "/api/workflows/{}/run"): "/workflows/{}/run",
    ("POST", "/api/workflows/{}/copilot"): "/workflows/{}/copilot",
    ("POST", "/api/workflows/{}/validate"): "/workflows/{}/validate",
    ("POST", "/api/workflows/{}/duplicate"): "/workflows/{}/duplicate",
    ("POST", "/api/workflows/{}/publish"): "/workflows/{}/publish",
    ("POST", "/api/workflows/{}/disable"): "/workflows/{}/disable",
    ("POST", "/api/workflows/{}/enable"): "/workflows/{}/enable",
}


def test_an_id_segment_of_the_ui_never_stands_on_a_static_bff_segment() -> None:
    """PAR-8a. An id of the UI is never the literal ``hooks``, so Next.js
    sends each of these requests to the catch-all. Only a path with the
    literal ``hooks`` reaches the hooks route. Mutation: let a UI ``{}`` match
    the static ``hooks`` again -> each row reads ``/workflows/hooks/{}`` -> red."""
    routes = r.bff_routes()
    for (verb, path), gateway in _HOOK_SHAPED.items():
        assert r.gateway_paths(verb, path, routes) == (gateway,), (verb, path)
        assert r.bff_route_for(path, routes, verb).template == "/api/workflows/**", path
    hooks = r.bff_route_for("/api/workflows/hooks/{}", routes, "POST")
    assert hooks is not None and hooks.template == "/api/workflows/hooks/{}"


def test_a_workflow_row_mapped_to_a_tool_that_never_sends_it_fails_by_name() -> None:
    """PAR-8a. Mutation: map the run of a workflow to the check tool. The
    check sends ``POST /workflows/{}/validate`` and never ``.../run``."""
    acts, _ = _read()
    tables = _tables()
    tables["COVERED"]["POST /api/workflows/{}/run"] = "studio_workflow_check"
    bad = false_claims(acts, tables)
    assert bad == ["POST /api/workflows/{}/run: studio_workflow_check does not send "
                   "POST /workflows/{}/run"], bad


def test_a_false_also_claim_fails() -> None:
    """Mutation: a tool that sends another route, a name that is no tool, and
    a row with no ``COVERED`` row."""
    acts, _ = _read()
    also = {
        "POST /api/studio/apps/{}/actions/{}": ("studio_package_get", "zz_no_such_tool"),
        "POST /api/zz/nothing": ("studio_record_create",),
    }
    bad = false_also_claims(acts, _tables(), also)
    assert bad == [
        "POST /api/studio/apps/{}/actions/{}: studio_package_get does not send "
        "POST /studio/apps/{}/actions/{}",
        "POST /api/studio/apps/{}/actions/{}: zz_no_such_tool is not a registered chat tool",
        "POST /api/zz/nothing: an ALSO row needs a COVERED row of the same action",
    ], bad


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


# ── PAR-1: each app has a page, and the chat can open it ────────────────────

def test_every_app_names_its_page_or_none() -> None:
    from acb_skills.chat_parity import APP_PAGE, APPS

    assert set(APP_PAGE) == set(APPS)


def test_a_page_of_an_app_opens_for_an_admin() -> None:
    """The fence of PAR-1: each app with a page names a page that the chat may open
    (``in_app_path.refusal`` accepts it for an admin who holds every feature).
    Mutation: point an app at a path that is not a live pane -> red."""
    from acb_skills.chat_parity import APP_PAGE
    from orchestrator import in_app_path as iap

    every = frozenset(p.feature for p in iap.PANES if p.feature)
    for app, page in APP_PAGE.items():
        if page is not None:
            assert iap.refusal(page, every, True) is None, (app, page)


def test_an_app_with_no_page_has_no_live_pane() -> None:
    """``None`` means that the app is not live. An app that goes live must get a page."""
    from acb_skills.chat_parity import APP_PAGE
    from orchestrator import in_app_path as iap

    live = {p.href for p in iap.PANES}
    none = {app for app, page in APP_PAGE.items() if page is None}
    assert none == {"notes", "memory", "agents"}
    for app in none:
        assert f"/{app}" not in live, app
    for app in set(APP_PAGE) - none:
        assert APP_PAGE[app] in live, app


def test_each_app_with_debt_has_a_page_or_a_reason() -> None:
    from acb_skills.chat_parity import APP_PAGE, DEBT_CEILING

    for app, count in DEBT_CEILING.items():
        assert app in APP_PAGE, app
        if count and APP_PAGE[app] is None:
            assert app in {"notes", "memory", "agents"}, app


# ── PAR-1b: the ratchet names its tasks (task 3100) ─────────────────────────
#
# Since P-0 (2026-11-01) eight ceilings of ``DEBT_CEILING`` went UP, and no wave
# after P-0 had a task on the board, so the debt only grew. Now each wave names
# its board tasks (``WAVES``), the baseline of P-0 stays in ``P0_CEILING``, and a
# ceiling above its baseline names the task that pays it in its comment
# ("Paid by task N", N a task of the wave of that app). Each check below has a
# test that gives it bad data, so each one can fail (R7).

def waves_without_a_task(waves: dict[str, tuple]) -> list[str]:
    """The waves that name no board task, or a task that is not a positive number."""
    bad: list[str] = []
    for wave, (_name, _apps, tasks) in sorted(waves.items()):
        if not tasks or not all(isinstance(t, int) and not isinstance(t, bool) and t > 0
                                for t in tasks):
            bad.append(f"{wave}: no board task")
    return bad


def raises_without_a_task(
    ceiling: dict[str, int], baseline: dict[str, int], comments: dict[str, str],
    waves: dict[str, tuple],
) -> list[str]:
    """The apps whose ceiling is above the P-0 baseline and whose comment does not
    name a task of their wave ("Paid by task N")."""
    bad: list[str] = []
    for app, value in sorted(ceiling.items()):
        if value <= baseline.get(app, 0):
            continue
        wave_tasks = {t for _n, apps, tasks in waves.values() if app in apps for t in tasks}
        named = {int(n) for n in re.findall(r"[Pp]aid by task (\d+)", comments.get(app, ""))}
        if not named & wave_tasks:
            bad.append(f"{app}: the ceiling {value} is above its baseline "
                       f"{baseline.get(app, 0)} and names no task of its wave")
    return bad


def _ceiling_comments() -> dict[str, str]:
    """The trailing comment of each ``DEBT_CEILING`` line, read from the source."""
    text = Path(cp.__file__).read_text(encoding="utf-8")
    block = text[text.index("DEBT_CEILING: dict[str, int] = {"):]
    block = block[:block.index("\n}\n")]
    out: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r'^    "(\w+)": \d+,(?:\s*#\s*(.*))?$', line)
        if m:
            out[m.group(1)] = m.group(2) or ""
    return out


def test_each_wave_names_a_board_task() -> None:
    assert waves_without_a_task(cp.WAVES) == []


def baseline_tasks_not_in_waves(table: str, waves: dict[str, Any]) -> list[str]:
    """Each task that a row of the baseline table (``chat_parity.md`` §6.2)
    names, and that the ``WAVES`` entry of the wave of its app does not hold."""
    wave_of = {app: name for name, (_label, apps, _tasks) in waves.items() for app in apps}
    bad: list[str] = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] not in wave_of:
            continue
        wave = wave_of[cells[0]]
        for task in re.findall(r"\b\d{4}\b", cells[4]):
            if int(task) not in waves[wave][2]:
                bad.append(f"{cells[0]}: task {task} is not in WAVES[{wave!r}]")
    return bad


def _baseline_table() -> str:
    spec = (r.REPO_ROOT / "project-docs" / "specs" / "chat_parity.md").read_text(encoding="utf-8")
    start = spec.index("### 6.2 The debt today")
    return spec[start:spec.index("### 6.3", start)]


def test_each_task_of_the_baseline_table_is_in_the_wave_of_its_app() -> None:
    """PAR-8a (D-PAR-5). A task that builds a wave joins its ``WAVES`` tuple.
    Mutation: drop 3903 from ``WAVES["P-6"]`` -> red."""
    assert baseline_tasks_not_in_waves(_baseline_table(), cp.WAVES) == []
    waves = {**cp.WAVES, "P-6": (cp.WAVES["P-6"][0], cp.WAVES["P-6"][1], (3115,))}
    assert baseline_tasks_not_in_waves(_baseline_table(), waves) == [
        "workflows: task 3903 is not in WAVES['P-6']"]


def test_a_wave_with_no_task_is_found() -> None:
    bad = {**cp.WAVES, "P-X": ("A wave nobody builds", ("crm",), ()),
           "P-Y": ("A wave with a bad task", ("crm",), (0,))}
    assert waves_without_a_task(bad) == ["P-X: no board task", "P-Y: no board task"]


def test_the_waves_cover_each_app_with_debt_once() -> None:
    covered = [app for _n, apps, _t in cp.WAVES.values() for app in apps]
    assert sorted(covered) == sorted(set(covered))
    assert set(cp.DEBT_CEILING) - {"projects"} <= set(covered)


def test_the_baseline_names_the_same_apps_as_the_ceiling() -> None:
    assert set(cp.P0_CEILING) == set(cp.DEBT_CEILING)


def test_a_ceiling_above_its_baseline_names_the_task_that_pays_it() -> None:
    assert raises_without_a_task(
        cp.DEBT_CEILING, cp.P0_CEILING, _ceiling_comments(), cp.WAVES) == []


def test_eight_ceilings_went_up_since_p0() -> None:
    """The count that the owner met on 2026-11-13. A ceiling may go down, and one that
    goes up here must name its task. Mutation: raise a ceiling and the test above is red."""
    up = sorted(a for a, v in cp.DEBT_CEILING.items() if v > cp.P0_CEILING[a])
    # PAR-8b (task 3903) took the shell below its baseline (16 to 7).
    # P-2c (task 3919) took email below its baseline (14 to 10). PAR-6a (task
    # 3109) took the integrations below their baseline (22 to 4).
    assert up == ["calendar", "chat", "knowledge", "settings", "studio"]


def test_a_raise_with_no_task_in_its_comment_is_found() -> None:
    ceiling = {**cp.DEBT_CEILING, "crm": cp.P0_CEILING["crm"] + 1}
    comments = {**_ceiling_comments(), "crm": "added a row, nobody pays it"}
    assert [m.split(":")[0] for m in
            raises_without_a_task(ceiling, cp.P0_CEILING, comments, cp.WAVES)] == ["crm"]


def test_a_raise_that_names_a_task_of_another_wave_is_found() -> None:
    # P-2c (task 3919) took email below its baseline, so the case reads settings (P-8).
    comments = {**_ceiling_comments(), "settings": "added a row. Paid by task 3112."}  # P-1, not P-8
    assert [m.split(":")[0] for m in raises_without_a_task(
        cp.DEBT_CEILING, cp.P0_CEILING, comments, cp.WAVES)] == ["settings"]


def test_a_ceiling_at_or_below_its_baseline_needs_no_task() -> None:
    ceiling = {**cp.DEBT_CEILING, "crm": cp.P0_CEILING["crm"], "tasks": 1}
    comments = {**_ceiling_comments(), "crm": "", "tasks": ""}
    assert raises_without_a_task(ceiling, cp.P0_CEILING, comments, cp.WAVES) == []
