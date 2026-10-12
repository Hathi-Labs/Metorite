// H-300: the "always confirm" switch of an MCP server, in the console.
//
// With the switch on, every tool of the server waits for a member's card,
// whatever hints the server sends. A new server starts with it on. The panel
// reads it from the row's `detail.always_confirm` and sends a PATCH.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { McpFormFields } from "../components/AddMcpModal";
import { SheetBody } from "../components/ConnectionSheet";
import * as api from "./api";
import {
  ALWAYS_CONFIRM_HELP,
  ALWAYS_CONFIRM_LABEL,
  EMPTY_MCP_FORM,
  detailRows,
  mcpAlwaysConfirm,
  mcpBody,
  panelPlan,
} from "./connections";
import { row } from "./testRows";

const noop = () => undefined;
const mcp = (always?: string) =>
  row({
    kind: "mcp",
    id: "mcp:brave",
    provider: "mcp",
    owning_app: "integrations",
    setup_href: null,
    detail: always === undefined ? { name: "brave" } : { name: "brave", always_confirm: always },
  });

afterEach(() => vi.unstubAllGlobals());

describe("the switch in the Add form", () => {
  it("starts ON for a new server", () => {
    expect(EMPTY_MCP_FORM.alwaysConfirm).toBe(true);
  });

  it("sends always_confirm, on by default and off when the admin turns it off", () => {
    const base = { ...EMPTY_MCP_FORM, name: "n", url: "https://x" };
    expect(mcpBody(base).always_confirm).toBe(true);
    expect(mcpBody({ ...base, alwaysConfirm: false }).always_confirm).toBe(false);
    // A real boolean, never a string: the gateway takes a strict bool.
    expect(typeof mcpBody(base).always_confirm).toBe("boolean");
  });

  it("draws one labelled, checked toggle with the help line", () => {
    const html = renderToStaticMarkup(
      createElement(McpFormFields, { form: EMPTY_MCP_FORM, onChange: noop }),
    );
    expect(html).toContain(ALWAYS_CONFIRM_LABEL);
    expect(ALWAYS_CONFIRM_LABEL).toBe("Always ask before any tool runs");
    expect(html).toContain(ALWAYS_CONFIRM_HELP);
    expect(html).toMatch(/type="checkbox"[^>]*checked=""|checked=""[^>]*type="checkbox"/);
    // The shared Checkbox draws it, never a raw platform control.
    expect(html).toContain("cc-checkbox");
  });

  it("draws the toggle unchecked when the admin turned it off", () => {
    const html = renderToStaticMarkup(
      createElement(McpFormFields, { form: { ...EMPTY_MCP_FORM, alwaysConfirm: false }, onChange: noop }),
    );
    expect(html).not.toMatch(/checked=""/);
  });
});

describe("the switch in the side panel", () => {
  it("reads the row's flag, and a missing key reads as off", () => {
    expect(mcpAlwaysConfirm(mcp("true"))).toBe(true);
    expect(mcpAlwaysConfirm(mcp("false"))).toBe(false);
    expect(mcpAlwaysConfirm(mcp())).toBe(false);
    expect(mcpAlwaysConfirm(row())).toBeNull();
  });

  it("is a switch, not a line of text", () => {
    expect(detailRows(mcp("true")).map((r) => r.label)).not.toContain("Always confirm");
  });

  it("shows on an MCP row only, and only a manager may change it", () => {
    expect(panelPlan(mcp("true"), true).alwaysConfirm).toBe("edit");
    expect(panelPlan(mcp("true"), false).alwaysConfirm).toBe("view");
    expect(panelPlan(row(), true).alwaysConfirm).toBeNull();
  });

  const sheet = (r = mcp("true"), canManage = true) =>
    renderToStaticMarkup(
      createElement(SheetBody, {
        row: r,
        canManage,
        onNavigate: noop,
        onDisconnect: noop,
        onTest: noop,
        onAlwaysConfirm: noop,
      }),
    );

  it("draws the toggle checked from the row", () => {
    const on = sheet(mcp("true"));
    expect(on).toContain(ALWAYS_CONFIRM_LABEL);
    expect(on).toMatch(/checked=""/);
    expect(sheet(mcp("false"))).not.toMatch(/checked=""/);
  });

  it("draws it disabled for a member without integrations:manage", () => {
    expect(sheet(mcp("true"), false)).toMatch(/disabled=""/);
    expect(sheet(mcp("true"), true)).not.toMatch(/disabled=""/);
  });

  it("draws no toggle on a row that is not an MCP server", () => {
    expect(sheet(row())).not.toContain(ALWAYS_CONFIRM_LABEL);
  });
});

describe("setMcpAlwaysConfirm", () => {
  it("sends a PATCH with the flag only", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fn);
    await api.setMcpAlwaysConfirm("brave search", false);
    expect(fn).toHaveBeenCalledWith("/api/integrations/mcp/brave%20search", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ always_confirm: false }),
    });
  });

  it("throws the server's words on a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Forbidden" }), { status: 403 })));
    await expect(api.setMcpAlwaysConfirm("x", true)).rejects.toMatchObject({ status: 403, message: "Forbidden" });
  });
});
