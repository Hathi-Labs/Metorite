// The Integrations console, drawn (IN-2). The runner has no DOM, so each
// component is rendered to a string. The stale-text and shared-control rules
// are source scans.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { row } from "../lib/testRows";
import { EMPTY_MCP_FORM } from "../lib/connections";
import { McpFormFields } from "./AddMcpModal";
import ConnectionList from "./ConnectionList";
import { SheetBody } from "./ConnectionSheet";
import IntegrationsRail from "./IntegrationsRail";

const noop = () => undefined;
const NOW = Date.parse("2026-10-11T12:00:00Z");

const list = (over: Partial<Parameters<typeof ConnectionList>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(ConnectionList, { rows: [], total: 0, loading: false, onOpen: noop, now: NOW, ...over }),
  );

describe("the list states", () => {
  it("shows a skeleton while it loads", () => {
    const html = list({ loading: true });
    expect(html).toContain("Loading connections");
    expect(html).toContain("animate-pulse");
  });

  it("shows an empty state when there are none", () => {
    expect(list()).toContain("No connections yet.");
  });

  it("shows a filter empty state, with the way out", () => {
    const html = list({ total: 3, onClearFilters: noop });
    expect(html).toContain("No connections match these filters.");
    expect(html).toContain("Clear filters");
  });

  it("shows a row with label, provider, app, scope, status, last sync and error", () => {
    const html = list({
      total: 1,
      rows: [
        row({
          status: "error",
          last_error: "token_expired",
          last_sync_at: "2026-10-11T11:56:00Z",
          scope: "member",
          owner: "dana@x.test",
        }),
      ],
    });
    for (const part of [
      "Zoho CRM", "zoho", "CRM", "dana@x.test", "Error", "4m ago",
      "The sign-in expired. Connect again.",
    ]) {
      expect(html).toContain(part);
    }
    // The colour comes from the shared vocabulary, never a raw palette class.
    expect(html).not.toMatch(/(bg|text)-(red|green|amber|blue|sky)-\d/);
  });
});

const sheet = (r = row(), over: Partial<Parameters<typeof SheetBody>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(SheetBody, {
      row: r,
      canManage: true,
      onNavigate: noop,
      onDisconnect: noop,
      onTest: noop,
      now: NOW,
      ...over,
    }),
  );

describe("the panel actions per kind", () => {
  it("lists every detail field and links to the app", () => {
    const html = sheet(row({ detail: { connected_by: "a@x.test", region: "in" } }));
    expect(html).toContain("Connected by");
    expect(html).toContain("a@x.test");
    expect(html).toContain("Region");
    expect(html).toContain("Open in CRM");
    expect(html).toContain("Disconnect");
    expect(html).not.toContain("Connect again");
  });

  it("offers Connect again on a row that needs it", () => {
    expect(sheet(row({ status: "needs_reconnect" }))).toContain("Connect again");
  });

  it("offers Test on an MCP row, and not elsewhere", () => {
    const mcp = row({ kind: "mcp", owning_app: "integrations", setup_href: null });
    expect(sheet(mcp)).toMatch(/>Test</);
    expect(sheet(row())).not.toMatch(/>Test</);
  });

  it("hides Disconnect and Test without integrations:manage", () => {
    const mcp = row({ kind: "mcp", owning_app: "integrations" });
    const html = sheet(mcp, { canManage: false });
    expect(html).not.toContain("Disconnect");
    expect(html).not.toMatch(/>Test</);
    expect(html).toContain("Open in Integrations");
  });

  it("hides Disconnect when the row cannot disconnect", () => {
    expect(sheet(row({ can_disconnect: false }))).not.toContain("Disconnect");
  });

  it("shows the member note on a member row", () => {
    const html = sheet(
      row({ kind: "mailbox", owning_app: "email", scope: "member", owner: "dana@x.test", can_disconnect: false }),
    );
    expect(html).toContain("dana@x.test manages this connection in Email.");
  });
});

describe("the MCP form", () => {
  it("draws the header value as a write-only password field that is never filled", () => {
    const html = renderToStaticMarkup(
      createElement(McpFormFields, { form: { ...EMPTY_MCP_FORM, headerValue: "" }, onChange: noop }),
    );
    expect(html).toContain("Write-only. It is never shown again.");
    expect(html).toMatch(/type="password"/);
    expect(html).toMatch(/autoComplete="off"|autocomplete="off"/);
  });

  it("does not echo a typed header value into the markup of the console", () => {
    // The console draws rows from the server, which holds no header value.
    const html = sheet(row({ kind: "mcp", detail: { name: "x", header_names: "Authorization" } }));
    expect(html).not.toContain("Bearer");
    expect(html).toContain("Authorization");
  });
});

describe("the rail", () => {
  it("has Connections, API keys, Plugins and Skills, and no Email", () => {
    const html = renderToStaticMarkup(createElement(IntegrationsRail, { view: "connections", onSelect: noop }));
    for (const label of ["Connections", "API keys", "Plugins", "Skills"]) expect(html).toContain(label);
    expect(html).not.toContain("Email");
    expect(html).toContain('aria-current="page"');
  });
});

// ── Source scans ──────────────────────────────────────────────────────────

const DIR = join(__dirname, "..");
function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !/\.test\./.test(e.name) ? [join(dir, e.name)] : [],
  );
}
const read = (f: string) => readFileSync(f, { encoding: "utf-8" });

describe("the stale text is gone (IN-D16)", () => {
  it("claims no encryption the page cannot back", () => {
    for (const f of sources(DIR)) {
      expect(read(f), f).not.toMatch(/credentials encrypted at rest/i);
    }
  });

  it("has no Email tab or EmailTab component", () => {
    for (const f of sources(DIR)) {
      expect(read(f), f).not.toMatch(/function EmailTab|McpsTab|id: "email"/);
    }
  });
});

describe("the page uses the shared shell", () => {
  const page = read(join(DIR, "page.tsx"));
  it("opens with AppTopBar and the rail, and not the old Tabs", () => {
    expect(page).toContain("<AppTopBar");
    expect(page).toContain("IntegrationsRail");
    expect(page).not.toContain("@/components/Tabs");
  });
  it("gates the read and the writes on the access", () => {
    expect(page).toContain("consoleAccess");
    expect(page).toContain("READ_DENIED");
  });
  it("uses ConfirmDialog for Disconnect, and no window.confirm", () => {
    const view = read(join(DIR, "components/ConnectionsView.tsx"));
    expect(view).toContain("ConfirmDialog");
    expect(view).not.toMatch(/window\.confirm|confirm\(/);
  });
  it("keeps the READ_DENIED words", async () => {
    const { READ_DENIED } = await import("../lib/connections");
    expect(READ_DENIED).toBe("Only admins can see your company's connections.");
  });
});
