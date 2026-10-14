// The AI agents view of /integrations (WS-55 EA-4b, D-EA-9, spec §5.4).
//
// A9: a member with no grants reaches /integrations and sees the AI agents
// view only. The org views stay hidden, and the bar does not offer their jobs.
// The rest holds the pure decisions of `agents.ts`.
//
// Mutations, each one red here:
// - drop "/integrations" from ALWAYS_ALLOWED in `access.ts`: the canSeePath case;
// - mark the agents view `org: true`, or drop the `org` filter in
//   `visibleViews`: the "agents only" cases;
// - drop `feature: "integrations"` from add-mcp in `registry.ts`, or the
//   feature filter in `buildItems`: the job cases;
// - headline `client_name` in `agentHost`: the host case.
import { describe, expect, it } from "vitest";

import { NO_ACCESS, canSeePath, type Access } from "@/lib/access";
import { visibleSections } from "@/lib/nav";
import { buildItems, heldPanes } from "@/lib/shell/registry";

import {
  AGENT_CLIENTS,
  CLAUDE_CODE_COMMAND,
  MCP_URL,
  agentClaim,
  agentHost,
  dayText,
  personalText,
  pinsText,
  revokeCopy,
  scopeWords,
  shownRows,
  type AgentConnection,
} from "./agents";
import { homeView, parseView, viewHref, visibleViews } from "./views";

function access(features: string[], capabilities: string[] = []): Access {
  return { ...NO_ACCESS, features, capabilities };
}

function row(over: Partial<AgentConnection> = {}): AgentConnection {
  return {
    id: "c1",
    member_email: "a@example.com",
    client_name: "Claude Code",
    client_origin: "https://claude.ai",
    scopes: ["projects:read", "projects:write", "offline_access"],
    pinned_projects: [],
    include_personal: false,
    created_at: "2026-10-14T10:00:00+00:00",
    last_used_at: null,
    expires_at: "2027-01-12T10:00:00+00:00",
    ...over,
  };
}

describe("A9: a member with no grants sees the AI agents view only", () => {
  const none = access([]);

  it("reaches /integrations, and nothing under it", () => {
    expect(canSeePath(none, "/integrations")).toBe(true);
    expect(canSeePath(none, "/integrations/anything")).toBe(false);
    expect(canSeePath(access(["integrations"]), "/integrations/anything")).toBe(true);
  });

  it("finds the pane in the sidebar", () => {
    const hrefs = visibleSections([], false).flatMap((s) => s.items.map((p) => p.href));
    expect(hrefs).toContain("/integrations");
  });

  it("gets the agents view only, and opens on it", () => {
    expect(visibleViews(false).map((v) => v.id)).toEqual(["agents"]);
    expect(homeView(false)).toBe("agents");
    for (const asked of [null, "connections", "keys", "plugins", "skills", "nope", "agents"]) {
      expect(parseView(asked, false)).toBe("agents");
    }
    expect(viewHref("agents")).toBe("/integrations?view=agents");
  });

  it("a holder of the feature keeps every view, and the agents view too", () => {
    expect(visibleViews(true).map((v) => v.id)).toEqual(["connections", "keys", "plugins", "skills", "agents"]);
    expect(parseView(null, true)).toBe("connections");
    expect(parseView("keys", true)).toBe("keys");
    expect(parseView("agents", true)).toBe("agents");
  });

  it("is offered Connect an AI agent, and not the org jobs", () => {
    const keys = (features: string[]) =>
      buildItems(heldPanes(visibleSections(features, false, false)), false, features).map((i) => i.key);
    expect(keys([])).toContain("do:connect-agent");
    expect(keys([])).not.toContain("do:add-mcp");
    expect(keys([])).not.toContain("do:review-integrations");
    expect(keys(["integrations"])).toEqual(
      expect.arrayContaining(["do:connect-agent", "do:add-mcp", "do:review-integrations"]),
    );
  });

  it("fails closed when the caller passes no features", () => {
    const panes = heldPanes(visibleSections(["integrations"], false, false));
    const keys = buildItems(panes, false).map((i) => i.key);
    expect(keys).not.toContain("do:add-mcp");
    expect(keys).toContain("do:connect-agent");
  });
});

describe("the connect card (spec §17.1)", () => {
  it("names the one address and the Claude Code command", () => {
    expect(MCP_URL).toBe("https://mcp.metorite.com/mcp");
    expect(CLAUDE_CODE_COMMAND).toBe("claude mcp add --transport http metorite https://mcp.metorite.com/mcp");
  });

  it("holds the four clients in the state the spec gives them", () => {
    expect(AGENT_CLIENTS.map((c) => [c.label, c.state])).toEqual([
      ["Claude Code", "tested"],
      ["claude.ai / Claude Desktop", "untested"],
      ["VS Code", "untested"],
      ["Cursor", "unsupported"],
    ]);
    const steps = AGENT_CLIENTS[0].steps.map((s) => s.text).join(" ");
    expect(steps).toContain("/mcp");
    expect(steps).toContain("Authenticate");
    expect(AGENT_CLIENTS[0].steps[0].code).toBe(CLAUDE_CODE_COMMAND);
  });
});

describe("one row", () => {
  it("headlines the verified host, never the claimed name", () => {
    expect(agentHost(row())).toBe("claude.ai");
    expect(agentHost(row({ client_origin: "http://127.0.0.1:8123" }))).toBe("127.0.0.1:8123");
    expect(agentHost(row({ client_origin: "" }))).toBe("Unknown agent");
  });

  it("shows the claim second, and not when it only repeats the host", () => {
    expect(agentClaim(row())).toBe("It calls itself “Claude Code”");
    expect(agentClaim(row({ client_name: "claude.ai" }))).toBeNull();
    expect(agentClaim(row({ client_name: "" }))).toBeNull();
  });

  it("says each scope in the words of the consent page", () => {
    expect(scopeWords(row().scopes)).toEqual([
      "Read your projects and tasks",
      "Change your projects and tasks",
      "Stay connected",
    ]);
    expect(scopeWords(["future:scope"])).toEqual(["future:scope"]);
  });

  it("says the pins and the personal choice in plain words", () => {
    expect(pinsText([])).toBe("Every project you can see");
    expect(pinsText(["p1"])).toBe("Pinned to 1 project");
    expect(pinsText(["p1", "p2"])).toBe("Pinned to 2 projects");
    expect(personalText(true)).toBe("Included");
    expect(personalText(false)).toBe("Not included");
  });

  it("says a day, and Never for no refresh yet", () => {
    expect(dayText("2026-10-14T10:00:00+00:00")).toBe("14 Oct 2026");
    expect(dayText(null)).toBe("Never");
    expect(dayText("not a date")).toBe("Never");
  });

  it("drops a revoked row at once", () => {
    const rows = [row({ id: "a" }), row({ id: "b" })];
    expect(shownRows(rows, new Set(["a"])).map((r) => r.id)).toEqual(["b"]);
    expect(shownRows(null, new Set())).toEqual([]);
  });

  it("names the member in an admin's revoke, and says the truth", () => {
    expect(revokeCopy(row(), false).body).toMatch(/^The agent loses access at once/);
    expect(revokeCopy(row(), true).body).toMatch(/^a@example\.com's agent loses access at once/);
    expect(revokeCopy(row(), true).body).toContain("connect it again");
  });
});
