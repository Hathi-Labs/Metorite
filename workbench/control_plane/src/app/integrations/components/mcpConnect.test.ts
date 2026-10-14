// WS-54 IN-8c (D98): Connect, Reconnect and the acts-as label of an OAuth MCP
// server. Row O10 of `integrations_console.md` §3.6.11, the console half.
//
// The runner has no DOM, so each component is rendered to a string, as in
// `console.test.ts`. The rules themselves are pure functions in
// `lib/connections.ts`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  actsAsLine,
  connectDialogCopy,
  consentReturn,
  mcpConnect,
  panelPlan,
  startErrorMessage,
  type Connection,
} from "../lib/connections";
import { row } from "../lib/testRows";
import ConnectionList from "./ConnectionList";
import { SheetBody } from "./ConnectionSheet";

const noop = () => undefined;
const NOW = Date.parse("2026-10-14T12:00:00Z");
const ADMIN = "dana@fracktal.test";

/** An MCP row as the gateway sends it with the flag on. */
const mcp = (over: Partial<Connection> = {}): Connection =>
  row({
    id: "mcp:notion",
    kind: "mcp",
    provider: "mcp",
    label: "Notion",
    owning_app: "integrations",
    setup_href: "/integrations",
    detail: { name: "notion", host: "mcp.notion.com", always_confirm: "true" },
    acts_as: ADMIN,
    can_connect: true,
    ...over,
  });

/** The same row with the flag off: the gateway sends neither key. */
const dark = (over: Partial<Connection> = {}): Connection => {
  const r = mcp(over);
  delete r.acts_as;
  delete r.can_connect;
  return r;
};

const sheet = (r: Connection, over: Partial<Parameters<typeof SheetBody>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(SheetBody, {
      row: r,
      canManage: true,
      onNavigate: noop,
      onDisconnect: noop,
      onTest: noop,
      onConnect: noop,
      now: NOW,
      ...over,
    }),
  );

const list = (rows: Connection[]) =>
  renderToStaticMarkup(
    createElement(ConnectionList, { rows, total: rows.length, loading: false, onOpen: noop, now: NOW }),
  );

describe("the acts-as label (O10)", () => {
  it("reads Acts as <admin> at <host>", () => {
    expect(actsAsLine(mcp())).toBe(`Acts as ${ADMIN} at mcp.notion.com`);
    // No host in the row: the label names the server.
    expect(actsAsLine(mcp({ detail: { name: "notion" } }))).toBe(`Acts as ${ADMIN} at Notion`);
  });

  it("is absent on a header server, on another kind, and with the flag off", () => {
    expect(actsAsLine(mcp({ acts_as: null }))).toBeNull();
    expect(actsAsLine(mcp({ acts_as: "  " }))).toBeNull();
    expect(actsAsLine(row({ acts_as: ADMIN }))).toBeNull();
    expect(actsAsLine(dark())).toBeNull();
  });

  it("shows on the console row and in its side panel", () => {
    expect(list([mcp()])).toContain(`Acts as ${ADMIN} at mcp.notion.com`);
    expect(sheet(mcp())).toContain(`Acts as ${ADMIN} at mcp.notion.com`);
  });

  it("draws the admin as text, so markup in it never runs", () => {
    const evil = '<img src=x onerror="alert(1)">@x.test';
    for (const html of [list([mcp({ acts_as: evil })]), sheet(mcp({ acts_as: evil }))]) {
      expect(html).not.toContain("<img");
      expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;@x.test");
    }
  });
});

describe("Connect and Reconnect", () => {
  it("offers Connect on a server with no consent yet", () => {
    const fresh = mcp({ acts_as: null });
    expect(mcpConnect(fresh, true)).toBe("connect");
    const html = sheet(fresh);
    expect(html).toContain('data-mcp-connect="connect"');
    expect(html).toMatch(/>Connect</);
  });

  it("offers Reconnect on a connected OAuth server, and on one that needs it", () => {
    expect(mcpConnect(mcp(), true)).toBe("reconnect");
    const needs = mcp({ status: "needs_reconnect", last_error: "invalid_grant" });
    expect(mcpConnect(needs, true)).toBe("reconnect");
    const html = sheet(needs);
    expect(html).toContain('data-mcp-connect="reconnect"');
    expect(html).toMatch(/>Reconnect</);
    expect(html).toContain("The sign-in was revoked. Connect again.");
    // The old link would only open this page again, so it is gone.
    expect(html).not.toContain("Connect again</");
    expect(panelPlan(needs, true).reconnect).toBe(false);
  });

  it("shows the consent as loading while it starts", () => {
    const html = sheet(mcp(), { connecting: true });
    expect(html).toMatch(/data-mcp-connect="reconnect"[^>]*disabled|disabled[^>]*data-mcp-connect="reconnect"/);
  });

  it("shows nothing new to a member without integrations:manage", () => {
    expect(mcpConnect(mcp(), false)).toBeNull();
    const html = sheet(mcp(), { canManage: false });
    expect(html).not.toContain("data-mcp-connect");
    // The label is a fact about the connection, and the reader still sees it.
    expect(html).toContain(`Acts as ${ADMIN}`);
  });

  it("shows nothing new when the gateway offers no consent (the flag is off)", () => {
    for (const r of [dark(), mcp({ can_connect: false, acts_as: null })]) {
      expect(mcpConnect(r, true)).toBeNull();
      const html = sheet(r);
      expect(html).not.toContain("data-mcp-connect");
      expect(html).not.toContain("data-acts-as");
    }
    expect(list([dark()])).not.toContain("data-acts-as");
    // The flag off keeps the plan of IN-4: a needs-reconnect row keeps its link.
    expect(panelPlan(dark({ status: "needs_reconnect" }), true).reconnect).toBe(true);
  });

  it("never offers a consent on a row that is not an MCP server", () => {
    expect(mcpConnect(row({ can_connect: true }), true)).toBeNull();
    expect(mcpConnect(mcp({ id: "x", detail: {} }), true)).toBeNull();
  });
});

describe("the Connect dialog (§3.6.9)", () => {
  it("says, before the consent, that every member's agents use it as the admin", () => {
    const c = connectDialogCopy(mcp({ acts_as: null }), ADMIN, "connect");
    expect(c.title).toBe("Connect Notion?");
    expect(c.body).toContain(`acts as ${ADMIN} at mcp.notion.com`);
    expect(c.body).toContain(`The agents of every member in your organization use it as ${ADMIN}.`);
    expect(c.confirmLabel).toBe("Open the sign-in");
  });

  it("says that a read-only tool runs with no card, unless the server always confirms", () => {
    const loose = connectDialogCopy(
      mcp({ acts_as: null, detail: { name: "notion", host: "mcp.notion.com", always_confirm: "false" } }),
      ADMIN,
      "connect",
    );
    expect(loose.note).toContain("A tool that the server marks as read-only runs with no approval.");
    expect(loose.note).toContain(`what ${ADMIN} can read at mcp.notion.com`);
    const strict = connectDialogCopy(mcp({ acts_as: null }), ADMIN, "connect");
    expect(strict.note).toContain("set to always confirm");
    expect(strict.note).toContain("runs with no approval");
  });

  it("names the admin it acts as now, when another admin reconnects", () => {
    const other = connectDialogCopy(mcp(), "sam@fracktal.test", "reconnect");
    expect(other.title).toBe("Reconnect Notion?");
    expect(other.body).toContain(`It acts as ${ADMIN} now. After your sign-in, it acts as you.`);
    const same = connectDialogCopy(mcp(), ADMIN.toUpperCase(), "reconnect");
    expect(same.body).not.toContain("acts as you");
  });

  it("is a ConfirmDialog of the view, with the error inside it", () => {
    const view = readFileSync(join(__dirname, "ConnectionsView.tsx"), { encoding: "utf-8" });
    expect(view).toMatch(/connectCopy && \(\s*<ConfirmDialog/);
    expect(view).toContain("note={connectCopy.note}");
    expect(view).toContain("window.location.assign(url)");
    // The URL comes from the BFF only. The view builds none.
    expect(view).not.toMatch(/authorize\?|redirect_uri|code_challenge/);
  });
});

describe("the words around the consent", () => {
  it("names a refused start, and any other code still gets a sentence", () => {
    expect(startErrorMessage("no_registration")).toBe(
      "This server does not let Metorite register as a client.",
    );
    expect(startErrorMessage("not_available")).toBe("MCP sign-in is not on for your organization.");
    expect(startErrorMessage("<b>x</b>")).toBe("Metorite cannot start the sign-in.");
    expect(startErrorMessage(undefined)).toBe("Metorite cannot start the sign-in.");
  });

  it("reads the two return shapes of the callback, and nothing else", () => {
    const p = (q: string) => new URLSearchParams(q);
    expect(consentReturn(p("mcp_connected=notion"))).toEqual({ ok: true, title: "notion is connected." });
    expect(consentReturn(p("mcp_connect_error=access_denied"))).toEqual({
      ok: false,
      title: "The sign-in was cancelled at the provider.",
    });
    expect(consentReturn(p("mcp_connect_error=x"))?.ok).toBe(false);
    // A name that breaks the name rule is never echoed back.
    const odd = consentReturn(p("mcp_connected=%3Cscript%3E"));
    expect(odd?.ok).toBe(false);
    expect(odd?.title).not.toContain("script");
    expect(consentReturn(p("view=keys"))).toBeNull();
  });
});

describe("the client call", () => {
  it("posts to the BFF start with no body, and takes only an https URL", () => {
    const api = readFileSync(join(__dirname, "../lib/api.ts"), { encoding: "utf-8" });
    const fn = api.slice(api.indexOf("export async function startMcpOAuth"));
    // CRLF-safe: a Windows checkout has `\r\n` line ends.
    const body = fn.split(/\r?\n\}\r?\n/)[0];
    expect(body).toContain("/oauth/start`");
    expect(body).toContain('method: "POST"');
    expect(body).not.toMatch(/body:/);
    expect(body).toContain('startsWith("https://")');
  });
});
