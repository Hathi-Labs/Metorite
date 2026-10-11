// The Integrations console rules (IN-2): filters, words, actions, copy, access.
import { describe, expect, it } from "vitest";

import { NO_ACCESS, type Access } from "@/lib/access";
import {
  appPills,
  consoleAccess,
  detailRows,
  disconnectCopy,
  errorMessage,
  filterConnections,
  lastSyncLabel,
  mcpBody,
  mcpName,
  needsAttention,
  panelPlan,
  refusalText,
  scopeLabel,
  statusPills,
  EMPTY_MCP_FORM,
  type Connection,
} from "./connections";
import { parseView, viewHref } from "./views";

import { row } from "./testRows";

const ROWS: Connection[] = [
  row(),
  row({ id: "m1", kind: "mailbox", provider: "gmail", label: "dana@x.test", owning_app: "email", scope: "member", owner: "dana@x.test", status: "needs_reconnect", can_disconnect: false }),
  row({ id: "w1", kind: "whatsapp", provider: "whatsapp", label: "+91 99", owning_app: "whatsapp", status: "error", last_error: "token_expired" }),
  row({ id: "s1", kind: "mcp", provider: "mcp", label: "Search", owning_app: "integrations", status: "configured", detail: { name: "brave-search", url: "https://x.test" } }),
];

describe("filters", () => {
  it("filters by app and by status, and by both", () => {
    expect(filterConnections(ROWS, "email", "all").map((r) => r.id)).toEqual(["m1"]);
    expect(filterConnections(ROWS, "all", "error").map((r) => r.id)).toEqual(["w1"]);
    expect(filterConnections(ROWS, "crm", "error")).toEqual([]);
    expect(filterConnections(ROWS, "all", "all")).toHaveLength(4);
  });

  it("draws a pill only for an app that has rows, with counts", () => {
    const pills = appPills(ROWS.filter((r) => r.owning_app !== "whatsapp"));
    expect(pills.map((p) => p.id)).toEqual(["all", "crm", "email", "integrations"]);
    expect(pills[0].count).toBe(3);
  });

  it("counts statuses inside the chosen app", () => {
    expect(statusPills(ROWS, "all").map((p) => p.id)).toEqual([
      "all", "connected", "configured", "needs_reconnect", "error",
    ]);
    expect(statusPills(ROWS, "email").map((p) => p.id)).toEqual(["all", "needs_reconnect"]);
  });

  it("counts the rows that need an admin", () => {
    expect(needsAttention(ROWS)).toBe(2);
  });
});

describe("words", () => {
  it("maps an error code to a human line, and never shows a bare code", () => {
    expect(errorMessage("token_expired")).toBe("The sign-in expired. Connect again.");
    expect(errorMessage("weird_new_code")).toContain("weird_new_code");
    expect(errorMessage("weird_new_code")).toMatch(/^The last sync failed/);
    expect(errorMessage(null)).toBe("");
  });

  it("names the scope and the last sync", () => {
    expect(scopeLabel(row())).toBe("Org");
    expect(scopeLabel(ROWS[1])).toBe("dana@x.test");
    const now = Date.parse("2026-10-11T12:00:00Z");
    expect(lastSyncLabel("2026-10-11T11:56:00Z", now)).toBe("4m ago");
    expect(lastSyncLabel(null, now)).toBe("Never");
  });

  it("turns detail keys into labels", () => {
    expect(detailRows(row({ detail: { connected_by: "a@x.test" } }))).toEqual([
      { label: "Connected by", value: "a@x.test" },
    ]);
  });
});

describe("the panel", () => {
  it("links to the owning app, and offers Connect again only when needed", () => {
    const ok = panelPlan(row(), true);
    expect(ok.openLabel).toBe("Open in CRM");
    expect(ok.href).toBe("/crm?tab=settings");
    expect(ok.reconnect).toBe(false);
    expect(panelPlan(row({ status: "needs_reconnect" }), true).reconnect).toBe(true);
    expect(panelPlan(row({ setup_href: null }), true).openLabel).toBeNull();
  });

  it("offers Disconnect only to a manager, and only when the row allows it", () => {
    expect(panelPlan(row(), true).disconnect).toBe(true);
    expect(panelPlan(row(), false).disconnect).toBe(false);
    expect(panelPlan(row({ can_disconnect: false }), true).disconnect).toBe(false);
  });

  it("offers Test on an MCP row for a manager", () => {
    const mcp = row({ kind: "mcp", id: "mcp:brave", owning_app: "integrations" });
    expect(panelPlan(mcp, true).test).toBe(true);
    expect(panelPlan(mcp, false).test).toBe(false);
    expect(panelPlan(row(), true).test).toBe(false);
  });

  it("tells who manages a member row, and where", () => {
    expect(panelPlan(ROWS[1], true).memberNote).toBe("dana@x.test manages this connection in Email.");
    expect(panelPlan(row(), true).memberNote).toBeNull();
  });

  it("tests an MCP server by detail.name, then by the id, and never by the label", () => {
    expect(mcpName(ROWS[3])).toBe("brave-search");
    expect(mcpName(row({ id: "mcp:zed", label: "Zed Pro" }))).toBe("zed");
    expect(mcpName(row({ id: "x9", label: "Zed" }))).toBeNull();
  });

  it("hides Test when the row names no server", () => {
    expect(panelPlan(row({ kind: "mcp", id: "x9" }), true).test).toBe(false);
    expect(panelPlan(row({ kind: "mcp", id: "mcp:zed" }), true).test).toBe(true);
  });
});

describe("the confirm copy names what happens", () => {
  it("Zoho", () => {
    const c = disconnectCopy(row());
    expect(`${c.title} ${c.body}`).toBe("Disconnect Zoho? The synced records stay, read-only.");
  });
  it("an MCP server", () => {
    const c = disconnectCopy(row({ kind: "mcp" }));
    expect(c.title).toBe("Remove this MCP server?");
    expect(c.body).toBe("Agents stop using it.");
  });
  it("an API key", () => {
    expect(disconnectCopy(row({ kind: "api_key" })).title).toBe("Remove this key?");
  });
  it("anything else says where to connect again", () => {
    const c = disconnectCopy(ROWS[1]);
    expect(c.title).toBe("Disconnect dana@x.test?");
    expect(c.body).toContain("Email");
  });
});

describe("who may do what", () => {
  const as = (over: Partial<Access>): Access => ({ ...NO_ACCESS, ...over });
  it("reads on the admin test", () => {
    expect(consoleAccess(as({ is_admin: true })).canRead).toBe(true);
    expect(consoleAccess(as({ capabilities: ["admin:members:read"] })).canRead).toBe(true);
    expect(consoleAccess(as({})).canRead).toBe(false);
  });
  it("manages on integrations:manage only", () => {
    expect(consoleAccess(as({ is_admin: true })).canManage).toBe(false);
    expect(consoleAccess(as({ capabilities: ["integrations:manage"] })).canManage).toBe(true);
  });
});

describe("the MCP form", () => {
  it("builds the body for the existing route, with the header when both halves are set", () => {
    const body = mcpBody({
      ...EMPTY_MCP_FORM,
      name: " brave ",
      url: " https://mcp.example.com/sse ",
      headerName: "Authorization",
      headerValue: "Bearer s3cret",
      agentScope: "a, b",
    });
    expect(body).toMatchObject({
      name: "brave",
      label: "brave",
      transport: "http-sse",
      url: "https://mcp.example.com/sse",
      headers: { Authorization: "Bearer s3cret" },
      agent_scope: ["a", "b"],
      enabled: true,
    });
  });

  it("sends the optional description", () => {
    expect(mcpBody({ ...EMPTY_MCP_FORM, name: "n", url: "https://x", description: " Search " }).description).toBe("Search");
  });

  it("sends no header when one half is empty", () => {
    const body = mcpBody({ ...EMPTY_MCP_FORM, name: "n", url: "https://x", headerName: "X-Key" });
    expect(body.headers).toEqual({});
  });

  it("shows the server's own message on a refusal", () => {
    expect(refusalText({ detail: "URL must use https" }, 422)).toBe("URL must use https");
    expect(refusalText({ detail: { code: "private_address" } }, 422)).toBe("private address");
    expect(refusalText({ detail: [{ msg: "Field required" }] }, 422)).toBe("Field required");
    expect(refusalText({}, 500)).toBe("The request failed (500).");
  });
});

describe("the views", () => {
  it("opens Connections for a missing or unknown view", () => {
    expect(parseView(null)).toBe("connections");
    expect(parseView("email")).toBe("connections");
    expect(parseView("plugins")).toBe("plugins");
  });
  it("builds the link of a view", () => {
    expect(viewHref("connections")).toBe("/integrations");
    expect(viewHref("keys")).toBe("/integrations?view=keys");
  });
});
