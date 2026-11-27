// CSY-7 (chat_connected_systems.md §3.7): the chat reads the description of an
// MCP server to know when to use it. The Add form says so in its hint, and the
// side panel lets an admin edit the text in place.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SheetBody } from "./ConnectionSheet";
import { McpFormFields } from "./AddMcpModal";
import * as api from "../lib/api";
import {
  DESCRIPTION_HINT,
  detailRows,
  EMPTY_MCP_FORM,
  mcpDescription,
} from "../lib/connections";
import { row } from "../lib/testRows";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({
    "X-User-Email": "dana@example.com",
    ...extra,
  }),
  requireIdentity: async () => ({ email: "dana@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const noop = () => undefined;
const mcp = (description?: string) =>
  row({
    kind: "mcp",
    id: "mcp:shaurrya",
    provider: "mcp",
    owning_app: "integrations",
    setup_href: null,
    detail: {
      name: "shaurrya",
      always_confirm: "true",
      read_only_key: "false",
      ...(description === undefined ? {} : { description }),
    },
  });

const sheet = (r = mcp("Orders and invoices."), canManage = true, onDescription = noop) =>
  renderToStaticMarkup(
    createElement(SheetBody, {
      row: r,
      canManage,
      onNavigate: noop,
      onDisconnect: noop,
      onTest: noop,
      onSwitches: noop,
      onDescription,
    }),
  );

afterEach(() => vi.unstubAllGlobals());

describe("the Description hint of the Add form", () => {
  it("tells the admin that the chat reads it", () => {
    expect(DESCRIPTION_HINT).toBe(
      "The chat reads this to know when to use the server. Say what data it holds.",
    );
    const html = renderToStaticMarkup(
      createElement(McpFormFields, { form: EMPTY_MCP_FORM, onChange: noop }),
    );
    expect(html).toContain(DESCRIPTION_HINT);
    expect(html).not.toContain("What the server offers</span>");
  });
});

describe("the description in place, in the side panel", () => {
  it("reads the description from the row, and the list does not draw it as a line", () => {
    expect(mcpDescription(mcp("Orders and invoices."))).toBe("Orders and invoices.");
    expect(mcpDescription(mcp())).toBe("");
    expect(detailRows(mcp("Orders and invoices.")).map((r) => r.label)).not.toContain("Description");
  });

  it("draws a text field with the saved text and a Save button for an admin", () => {
    const html = sheet();
    expect(html).toContain("<textarea");
    expect(html).toContain("Orders and invoices.");
    expect(html).toContain("Save description");
    expect(html).toContain(DESCRIPTION_HINT);
    expect(html).toMatch(/maxLength="400"/i);
  });

  it("draws the text and no field for a member without integrations:manage", () => {
    const html = sheet(mcp("Orders and invoices."), false);
    expect(html).not.toContain("<textarea");
    expect(html).not.toContain("Save description");
    expect(html).toContain("Orders and invoices.");
  });

  it("draws nothing for a connection that is not an MCP server", () => {
    const html = sheet(row({ kind: "api_key", id: "key:apollo", provider: "apollo" }));
    expect(html).not.toContain("Save description");
  });
});

describe("the PATCH of the description", () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  function stubGateway(answer: Record<string, unknown> = { ok: true, description: "Saved." }) {
    calls.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify(answer), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
  }
  async function patch(body: string, name = "shaurrya") {
    const { PATCH } = await import("../../api/integrations/mcp/[name]/route");
    const req = new NextRequest(`http://localhost:3001/api/integrations/mcp/${name}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body,
    });
    return PATCH(req, { params: Promise.resolve({ name }) });
  }

  it("the BFF forwards the description and nothing else of the body", async () => {
    stubGateway();
    const res = await patch(JSON.stringify({ description: "Data.", organization_id: "org-b" }));
    expect(res.status).toBe(200);
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ description: "Data." });
  });

  it("the BFF refuses a description that is not text, and calls no gateway", async () => {
    stubGateway();
    expect((await patch('{"description": 5}')).status).toBe(422);
    expect(calls).toHaveLength(0);
  });

  it("the caller sends the description and answers the stored text", async () => {
    stubGateway({ ok: true, name: "shaurrya", description: "Trimmed." });
    await expect(api.setMcpDescription("shaurrya", "  Trimmed.  ")).resolves.toBe("Trimmed.");
    expect(calls[0].url).toBe("/api/integrations/mcp/shaurrya");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ description: "  Trimmed.  " });
  });
});
