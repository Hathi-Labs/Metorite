// The consent page's decisions (WS-55 EA-4a, spec §10.5).
//
// The load-bearing rule: the approval sends the SAME request that `check`
// saw, because the consent ticket binds six of its fields. And a member can
// narrow the scopes, never widen them.
import { describe, expect, it } from "vitest";

import {
  NO_SELECTION,
  analyzeTree,
  buildProjectTree,
  cleanSelection,
  grantSummary,
  parentIds,
  projectLists,
  projectsProblem,
  searchMatches,
  selectedCount,
  summaryText,
  tickProject,
  toggleProject,
  untickProject,
  visibleNodes,
  type Selection,
} from "@/lib/accessTree";
import { isKnownIcon } from "@/lib/icons";

import {
  APPROVE_GATE_MS,
  OFFLINE_ACCESS,
  approveBody,
  gateOpen,
  gateStart,
  gateWaitMs,
  sameScreen,
  canApprove,
  grantedScopes,
  initialChoice,
  isCheckView,
  isContinueView,
  organizationName,
  parseAuthorizeRequest,
  toggleScope,
  refusalFor,
  scopeRows,
  type CheckView,
} from "./consent";

const REQUEST = {
  client_id: "https://claude.ai/oauth/claude-code-client-metadata",
  redirect_uri: "http://localhost:53122/callback",
  response_type: "code",
  code_challenge: "c".repeat(43),
  code_challenge_method: "S256",
  resource: "https://mcp.metorite.com/mcp",
  scope: "projects:read projects:write offline_access",
  state: "st-1",
};

const VIEW: CheckView = {
  client: { client_id: REQUEST.client_id, host: "claude.ai", name: "Claude Code", origin: "https://claude.ai" },
  redirect_uri: REQUEST.redirect_uri,
  redirect_display: "http://localhost:53122",
  redirect_app: false,
  consent_ticket: "t".repeat(43),
  scopes: ["projects:read", "projects:write", OFFLINE_ACCESS],
  resource: REQUEST.resource,
  state: "st-1",
  issuer: "https://mcp.metorite.com",
  deny_redirect_to: "http://localhost:53122/callback?error=access_denied&state=st-1",
};

describe("parseAuthorizeRequest", () => {
  it("keeps the eight OAuth keys and drops any other", () => {
    const parsed = parseAuthorizeRequest({ ...REQUEST, organization_id: "x", extra: "y" });
    expect(parsed).toEqual({ ok: true, request: REQUEST });
  });

  it("refuses a key sent twice", () => {
    const parsed = parseAuthorizeRequest({ ...REQUEST, state: ["a", "b"] });
    expect(parsed.ok).toBe(false);
  });

  it("refuses a request with no client_id or no redirect_uri", () => {
    expect(parseAuthorizeRequest({ redirect_uri: "x" }).ok).toBe(false);
    expect(parseAuthorizeRequest({ client_id: "x" }).ok).toBe(false);
  });

  it("leaves out a key the agent did not send", () => {
    const parsed = parseAuthorizeRequest({ client_id: "a", redirect_uri: "b" });
    expect(parsed).toEqual({ ok: true, request: { client_id: "a", redirect_uri: "b" } });
  });
});

describe("the scopes", () => {
  it("offline_access is fixed, and the others have a box", () => {
    expect(scopeRows(VIEW.scopes).map((r) => [r.scope, r.fixed])).toEqual([
      ["projects:read", false],
      ["projects:write", false],
      [OFFLINE_ACCESS, true],
    ]);
  });

  it("a new screen chooses every requested scope", () => {
    expect([...initialChoice(VIEW)]).toEqual(["projects:read", "projects:write"]);
  });

  it("narrowing drops a scope, and offline_access stays", () => {
    expect(grantedScopes(VIEW, new Set(["projects:read"]))).toEqual(["projects:read", OFFLINE_ACCESS]);
  });

  it("a choice can never add a scope the agent did not ask for", () => {
    expect(grantedScopes(VIEW, new Set(["projects:read", "context:read"]))).toEqual([
      "projects:read",
      OFFLINE_ACCESS,
    ]);
  });

  it("approve needs one resource scope, and offline_access alone is not one", () => {
    expect(canApprove(VIEW, new Set(["projects:read"]))).toBe(true);
    expect(canApprove(VIEW, new Set())).toBe(false);
  });
});

describe("approveBody", () => {
  it("sends the same request, the ticket and the choices, and nothing else", () => {
    const body = approveBody(REQUEST, VIEW, {
      scopes: new Set(["projects:read"]),
      pins: ["p1"],
      leftOut: ["p2"],
      includePersonal: false,
    });
    expect(body).toEqual({
      ...REQUEST,
      consent_ticket: VIEW.consent_ticket,
      scopes: ["projects:read", OFFLINE_ACCESS],
      pinned_projects: ["p1"],
      left_out_projects: ["p2"],
      include_personal: false,
    });
  });
});

describe("toggleScope (read is needed for the rest)", () => {
  it("turning read off clears the other scopes", () => {
    const next = toggleScope(VIEW, new Set(["projects:read", "projects:write"]), "projects:read");
    expect([...next]).toEqual([]);
  });

  it("turning write on turns read on, when the agent asked for read", () => {
    const next = toggleScope(VIEW, new Set(), "projects:write");
    expect(next.has("projects:read")).toBe(true);
    expect(next.has("projects:write")).toBe(true);
  });

  it("never adds a scope that the agent did not ask for", () => {
    const view = { ...VIEW, scopes: ["projects:write"] };
    expect([...toggleScope(view, new Set(), "projects:write")]).toEqual(["projects:write"]);
  });

  it("offline_access has no box", () => {
    expect([...toggleScope(VIEW, new Set(["projects:read"]), OFFLINE_ACCESS)]).toEqual(["projects:read"]);
  });
});

// ── The project tree (EA-17b, spec §10.8, D-EA-12) ───────────────────────────
//
//   eng           Engineering
//     plat          Platform
//       api           API
//       ui            UI
//     data          Data
//   sales         Sales
//   old           Old (archived, with a hidden child)
const RAW_TREE = {
  rows: [
    {
      id: "eng",
      name: "Engineering",
      children: [
        {
          id: "plat",
          name: "Platform",
          children: [
            { id: "api", name: "API" },
            { id: "ui", name: "UI" },
          ],
        },
        { id: "data", name: "Data" },
      ],
    },
    { id: "sales", name: "Sales" },
    { id: "old", name: "Old", archived_at: "2026-01-01", children: [{ id: "hidden", name: "Hidden" }] },
    { id: 7, name: "Bad id" },
  ],
};
const TREE = buildProjectTree(RAW_TREE);
const states = (sel: Selection) => {
  const facts = analyzeTree(TREE, sel);
  return Object.fromEntries(TREE.nodes.map((n) => [n.id, facts[n.id].state]));
};

describe("buildProjectTree", () => {
  it("lists team projects in tree order with depth, and drops an archived subtree", () => {
    expect(TREE.nodes.map((n) => [n.id, n.depth, n.parent])).toEqual([
      ["eng", 0, null],
      ["plat", 1, "eng"],
      ["api", 2, "plat"],
      ["ui", 2, "plat"],
      ["data", 1, "eng"],
      ["sales", 0, null],
    ]);
  });

  it("reads a shape it does not know as no projects", () => {
    expect(buildProjectTree(null).nodes).toEqual([]);
    expect(buildProjectTree({ rows: "x" }).nodes).toEqual([]);
  });
});

describe("the tick cascades down (D-EA-12)", () => {
  it("a tick on a parent reaches every child, with ONE pin", () => {
    const sel = tickProject(TREE, NO_SELECTION, "eng");
    expect(sel).toEqual({ pins: ["eng"], leftOut: [] });
    expect(states(sel)).toEqual({
      eng: "on", plat: "on", api: "on", ui: "on", data: "on", sales: "off",
    });
  });

  it("a click on an empty box ticks, and a click on a full box unticks", () => {
    const on = toggleProject(TREE, NO_SELECTION, "plat");
    expect(on.pins).toEqual(["plat"]);
    expect(toggleProject(TREE, on, "plat")).toEqual(NO_SELECTION);
  });

  it("a tick on a parent drops the marks below it, because it covers them", () => {
    let sel = tickProject(TREE, NO_SELECTION, "api");
    sel = tickProject(TREE, sel, "ui");
    expect(sel.pins).toEqual(["api", "ui"]);
    sel = tickProject(TREE, sel, "eng");
    expect(sel).toEqual({ pins: ["eng"], leftOut: [] });
  });
});

describe("an untick under a ticked parent leaves the child out", () => {
  const base = tickProject(TREE, NO_SELECTION, "eng");

  it("adds the child to the left-out list, and the parent shows a dash", () => {
    const sel = untickProject(TREE, base, "ui");
    expect(sel).toEqual({ pins: ["eng"], leftOut: ["ui"] });
    expect(states(sel)).toMatchObject({ eng: "partial", plat: "partial", api: "on", ui: "off", data: "on" });
    expect(analyzeTree(TREE, sel).ui.leftOutMark).toBe(true);
    expect(analyzeTree(TREE, sel).api.leftOutMark).toBe(false);
  });

  it("leaving out a subtree leaves out its children too, with one mark", () => {
    const sel = untickProject(TREE, base, "plat");
    expect(sel.leftOut).toEqual(["plat"]);
    expect(states(sel)).toMatchObject({ plat: "off", api: "off", ui: "off", data: "on", eng: "partial" });
  });

  it("an untick of a project with no mark above sends no mark at all", () => {
    const sel = untickProject(TREE, tickProject(TREE, NO_SELECTION, "plat"), "plat");
    expect(sel).toEqual(NO_SELECTION);
  });

  it("a click on a dashed box ticks everything under it again", () => {
    const partial = untickProject(TREE, base, "ui");
    expect(toggleProject(TREE, partial, "eng")).toEqual({ pins: ["eng"], leftOut: [] });
  });
});

describe("a pin under a left-out parent wins (the nearest mark)", () => {
  it("a tick under a left-out project becomes a pin and keeps its own reach", () => {
    let sel = tickProject(TREE, NO_SELECTION, "eng");
    sel = untickProject(TREE, sel, "plat");
    sel = tickProject(TREE, sel, "api");
    expect(sel).toEqual({ pins: ["eng", "api"], leftOut: ["plat"] });
    expect(states(sel)).toMatchObject({ plat: "partial", api: "on", ui: "off", data: "on" });
    expect(analyzeTree(TREE, sel).api.reached).toBe(true);
    expect(analyzeTree(TREE, sel).ui.reached).toBe(false);
  });
});

describe("the counts", () => {
  it("count the project itself and every project beneath it", () => {
    const sel = untickProject(TREE, tickProject(TREE, NO_SELECTION, "eng"), "ui");
    const facts = analyzeTree(TREE, sel);
    expect([facts.eng.inside, facts.eng.total]).toEqual([4, 5]);
    expect([facts.plat.inside, facts.plat.total]).toEqual([2, 3]);
    expect([facts.data.inside, facts.data.total]).toEqual([1, 1]);
    expect(selectedCount(TREE, sel)).toBe(4);
    expect(selectedCount(TREE, NO_SELECTION)).toBe(0);
  });

  it("a mark for an id that is not in the tree is dropped before it is sent", () => {
    const clean = cleanSelection(TREE, { pins: ["eng", "gone"], leftOut: ["ghost"] });
    expect(clean).toEqual({ pins: ["eng"], leftOut: [] });
  });
});

describe("the two choices", () => {
  const sel: Selection = { pins: ["eng"], leftOut: ["ui"] };

  it("All my projects sends no list, even when the tree holds marks", () => {
    expect(projectLists("all", sel)).toEqual(NO_SELECTION);
    expect(projectLists("pick", sel)).toEqual(sel);
  });

  it("Only the ones I pick with nothing ticked is refused, because no pin means every project", () => {
    expect(projectsProblem("pick", TREE, NO_SELECTION)).toMatch(/Pick one project/);
    expect(projectsProblem("pick", TREE, { pins: [], leftOut: ["ui"] })).toMatch(/Pick one project/);
    expect(projectsProblem("pick", TREE, sel)).toBeNull();
    expect(projectsProblem("all", TREE, NO_SELECTION)).toBeNull();
  });

  it("refuses more than 50 marks", () => {
    const wide = buildProjectTree({ rows: Array.from({ length: 60 }, (_, i) => ({ id: `p${i}`, name: `P${i}` })) });
    let s: Selection = NO_SELECTION;
    for (const n of wide.nodes) s = tickProject(wide, s, n.id);
    expect(projectsProblem("pick", wide, s)).toMatch(/at most 50/);
  });

  it("the approval body carries the lists of the picked choice", () => {
    const lists = projectLists("pick", sel);
    const body = approveBody(REQUEST, VIEW, {
      scopes: new Set(["projects:read", "projects:write"]),
      pins: lists.pins,
      leftOut: lists.leftOut,
      includePersonal: true,
    });
    expect(body.pinned_projects).toEqual(["eng"]);
    expect(body.left_out_projects).toEqual(["ui"]);
    expect(body.scopes).toEqual(["projects:read", "projects:write", OFFLINE_ACCESS]);
  });
});

describe("the search", () => {
  it("shows a match with its parents, and no query shows all", () => {
    expect(searchMatches(TREE, "")).toBeNull();
    expect([...(searchMatches(TREE, " api ") as Set<string>)].sort()).toEqual(["api", "eng", "plat"]);
    const rows = visibleNodes(TREE, new Set(), searchMatches(TREE, "api"));
    expect(rows.map((n) => n.id)).toEqual(["eng", "plat", "api"]);
  });

  it("a folded parent hides its children", () => {
    expect(visibleNodes(TREE, new Set(), null).map((n) => n.id)).toEqual(["eng", "sales"]);
    expect(visibleNodes(TREE, new Set(parentIds(TREE)), null)).toHaveLength(6);
  });
});

describe("the summary line", () => {
  const facts = {
    who: "claude.ai",
    scopes: ["projects:read", "projects:write"],
    mode: "pick" as const,
    picked: 7,
    includePersonal: true,
    offline: true,
  };

  it("says what the agent can do, from the verified host", () => {
    expect(summaryText(grantSummary(facts))).toBe(
      "claude.ai will read and change tasks in 7 projects and your personal projects. It stays connected until you remove it.",
    );
  });

  it("follows each choice", () => {
    const read = { ...facts, scopes: ["projects:read"], picked: 1, includePersonal: false, offline: false };
    expect(summaryText(grantSummary(read))).toBe("claude.ai will only read tasks in 1 project.");
    expect(summaryText(grantSummary({ ...facts, mode: "all" }))).toContain(
      "in all your team projects and your personal projects",
    );
  });
});

describe("the rest", () => {
  it("isCheckView accepts the gateway's answer and refuses a partial one", () => {
    expect(isCheckView(VIEW)).toBe(true);
    expect(isCheckView({ ...VIEW, consent_ticket: undefined })).toBe(false);
    expect(isCheckView(null)).toBe(false);
  });

  it("names the organization, or nothing", () => {
    expect(organizationName({ display_name: "Fracktal Works", slug: "fracktal" })).toBe("Fracktal Works");
    expect(organizationName({ slug: "fracktal" })).toBe("fracktal");
    expect(organizationName({})).toBeNull();
    expect(organizationName(undefined)).toBeNull();
  });

  it("a ticket refusal says to start again, with no way back", () => {
    const r = refusalFor(400, {
      detail: {
        error: "invalid_request",
        error_description: "The consent ticket is missing or not valid.",
        redirect_to: null,
      },
    });
    expect(r.message).toMatch(/expired or was already used/);
    expect(r.redirectTo).toBeNull();
  });

  it("a verified refusal offers the agent's own error redirect", () => {
    const r = refusalFor(400, {
      detail: {
        error: "invalid_target",
        error_description: "resource must name this server's MCP endpoint.",
        redirect_to: "http://localhost:5/cb?error=invalid_target",
      },
    });
    expect(r.redirectTo).toBe("http://localhost:5/cb?error=invalid_target");
  });

  it("an organization off the allowlist gets its own text, with no way back (EA-10p)", () => {
    const r = refusalFor(403, {
      detail: {
        error: "org_not_enabled",
        error_description: "Your organization has not turned on agent connections yet.",
        redirect_to: null,
      },
    });
    expect(r.message).toBe("Your organization has not turned on agent connections yet.");
    expect(r.redirectTo).toBeNull();
    // Any other 403 keeps the member text.
    expect(refusalFor(403, { detail: "An active member of an organization must approve an agent." }).message)
      .toMatch(/active member/);
    expect(refusalFor(403, { error: "cross_site" }).message).toMatch(/active member/);
  });

  it("every icon the screen draws exists", () => {
    for (const name of ["Bot", "ShieldCheck", "Lock", "AlertCircle", "ArrowLeft", "ArrowUpRight", "Check"]) {
      expect(isKnownIcon(name), name).toBe(true);
    }
  });
});

describe("the approve gate (fix round 1, P1: double-clickjacking)", () => {
  const ON = { visible: true, focused: true };
  const T0 = 1_000_000;

  it("is not ready before 500 ms of visible, focused attention", () => {
    const start = gateStart(null, ON, T0);
    expect(start).toBe(T0);
    expect(gateOpen(start, T0)).toBe(false);
    expect(gateOpen(start, T0 + APPROVE_GATE_MS - 1)).toBe(false);
    expect(gateWaitMs(start, T0 + 100)).toBe(APPROVE_GATE_MS - 100);
  });

  it("is ready at 500 ms and after", () => {
    const start = gateStart(null, ON, T0);
    expect(APPROVE_GATE_MS).toBe(500);
    expect(gateOpen(start, T0 + 500)).toBe(true);
    expect(gateOpen(start, T0 + 60_000)).toBe(true);
    expect(gateWaitMs(start, T0 + 900)).toBe(0);
  });

  it("a page that loads without focus or hidden is never ready", () => {
    expect(gateStart(null, { visible: true, focused: false }, T0)).toBeNull();
    expect(gateStart(null, { visible: false, focused: true }, T0)).toBeNull();
    expect(gateOpen(null, T0 + 10_000)).toBe(false);
    expect(gateWaitMs(null, T0)).toBeNull();
  });

  it("resets on blur, and a new spell waits 500 ms again", () => {
    let start = gateStart(null, ON, T0);
    expect(gateOpen(start, T0 + 600)).toBe(true);
    start = gateStart(start, { visible: true, focused: false }, T0 + 700);
    expect(start).toBeNull();
    expect(gateOpen(start, T0 + 800)).toBe(false);
    start = gateStart(start, ON, T0 + 900);
    expect(start).toBe(T0 + 900);
    expect(gateOpen(start, T0 + 1_000)).toBe(false);
    expect(gateOpen(start, T0 + 1_400)).toBe(true);
  });

  it("resets on hidden", () => {
    let start = gateStart(null, ON, T0);
    start = gateStart(start, { visible: false, focused: true }, T0 + 600);
    expect(start).toBeNull();
    expect(gateOpen(start, T0 + 5_000)).toBe(false);
  });

  it("a spell that goes on keeps its start", () => {
    const start = gateStart(T0, ON, T0 + 300);
    expect(start).toBe(T0);
  });
});

describe("sameScreen (fix round 1, the retry after a 503)", () => {
  it("accepts a fresh check of the same screen with a new ticket", () => {
    expect(sameScreen(VIEW, { ...VIEW, consent_ticket: "n".repeat(43) })).toBe(true);
  });

  it.each([
    ["host", { client: { ...VIEW.client, host: "evil.example" } }],
    ["name", { client: { ...VIEW.client, name: "Other" } }],
    ["redirect", { redirect_display: "http://localhost:1" }],
    ["scopes", { scopes: ["projects:read"] }],
    ["deny", { deny_redirect_to: "http://localhost:1/x" }],
  ])("refuses a fresh check whose %s differs", (_name, change) => {
    expect(sameScreen(VIEW, { ...VIEW, ...change } as CheckView)).toBe(false);
  });
});

describe("the claim (fix round 1, P2)", () => {
  it("a view with no claimed name is still a view", () => {
    expect(isCheckView({ ...VIEW, client: { ...VIEW.client, name: null } })).toBe(true);
  });

  it("a view with no verified host is not", () => {
    expect(isCheckView({ ...VIEW, client: { ...VIEW.client, host: "" } })).toBe(false);
  });
});

// D-EA-11 (spec §10.7): the gateway alone decides that a first-party client
// skips the screen. The page reads only that exact answer as a pass, so a
// client off the list always gets the screen and its approve gate.
describe("isContinueView (D-EA-11, first-party clients)", () => {
  const CONTINUE = {
    first_party: true,
    redirect_to: "https://aroh.example/hub/auth/callback?code=c&state=st-1&iss=https%3A%2F%2Fmcp.metorite.com",
  };

  it("accepts the gateway's first-party answer", () => {
    expect(isContinueView(CONTINUE)).toBe(true);
  });

  it("never reads a consent screen as a pass", () => {
    expect(isContinueView(VIEW)).toBe(false);
    // A screen that also names a redirect is still a screen: it holds a ticket.
    expect(isContinueView({ ...VIEW, first_party: true, redirect_to: CONTINUE.redirect_to })).toBe(false);
    expect(isCheckView(CONTINUE)).toBe(false);
  });

  it("needs first_party to be exactly true", () => {
    for (const first_party of ["true", 1, false, undefined, null]) {
      expect(isContinueView({ ...CONTINUE, first_party })).toBe(false);
    }
    expect(isContinueView({ redirect_to: CONTINUE.redirect_to })).toBe(false);
  });

  it("refuses a redirect that is not https", () => {
    for (const redirect_to of [
      "http://localhost:53122/callback?code=c",
      "javascript:alert(1)",
      "com.example.app:/cb?code=c",
      "//aroh.example/cb",
      "",
    ]) {
      expect(isContinueView({ ...CONTINUE, redirect_to })).toBe(false);
    }
    expect(isContinueView({ ...CONTINUE, redirect_to: 42 })).toBe(false);
  });

  it("a client that calls itself Aroh is a screen like any other", () => {
    const named = { ...VIEW, client: { ...VIEW.client, name: "Aroh" } };
    expect(isContinueView(named)).toBe(false);
    expect(isCheckView(named)).toBe(true);
  });

  it("refuses null and a value that is not an object", () => {
    expect(isContinueView(null)).toBe(false);
    expect(isContinueView("https://aroh.example")).toBe(false);
  });
});
