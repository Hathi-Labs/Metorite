// H-300: the "always confirm" switch of an MCP server, in the console.
//
// With the switch on, every tool of the server waits for a member's card,
// whatever hints the server sends. A new server starts with it on. The panel
// reads it from the row's `detail.always_confirm` and sends a PATCH.
//
// MCP-C2 (agent_architecture.md §12.2 WS-8c item 32, A38): the second switch,
// "This key can only read". With it on, no tool of the server waits for a
// card. The two are never both on: a tick of one clears the other, and the
// console sends both keys in one PATCH. The BFF passes on only the keys that
// it got, and refuses a value that is not a boolean.
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NextRequest, NextResponse } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlwaysConfirmField, McpFormFields, ReadOnlyKeyField } from "../components/AddMcpModal";
import { SheetBody } from "../components/ConnectionSheet";
import * as api from "./api";
import {
  ALWAYS_CONFIRM_HELP,
  ALWAYS_CONFIRM_LABEL,
  connectDialogCopy,
  EMPTY_MCP_FORM,
  detailRows,
  heldSwitches,
  mcpActsThroughOAuth,
  mcpAlwaysConfirm,
  mcpBody,
  mcpReadOnlyKey,
  mcpSwitches,
  panelPlan,
  READ_ONLY_KEY_HELP,
  READ_ONLY_KEY_LABEL,
  READ_ONLY_KEY_OAUTH_NOTE,
  switchesSavedText,
  tickFormSwitch,
  tickSwitch,
  type HeldSwitches,
  type McpForm,
  type McpSwitches,
} from "./connections";
import { row } from "./testRows";

// The BFF route imports the gateway helpers. Only the route test below reads
// them, and no module of the console that this file imports does.
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
const mcp = (always?: string, readOnlyKey?: string) =>
  row({
    kind: "mcp",
    id: "mcp:brave",
    provider: "mcp",
    owning_app: "integrations",
    setup_href: null,
    detail: {
      name: "brave",
      ...(always === undefined ? {} : { always_confirm: always }),
      ...(readOnlyKey === undefined ? {} : { read_only_key: readOnlyKey }),
    },
  });

/** Every element of *type* in a React element tree, in order. */
function findAll(node: unknown, type: unknown, out: ReactElement<Record<string, unknown>>[] = []) {
  if (Array.isArray(node)) {
    for (const child of node) findAll(child, type, out);
    return out;
  }
  if (node && typeof node === "object" && "props" in node) {
    const el = node as ReactElement<Record<string, unknown>>;
    if (el.type === type) out.push(el);
    findAll(el.props.children, type, out);
  }
  return out;
}

/** Tick the field of *type* in a tree, the way the Checkbox does. */
function tick(tree: unknown, type: unknown, next: boolean) {
  const [field] = findAll(tree, type);
  expect(field, "the field is not in the tree").toBeTruthy();
  (field.props.onChange as (v: boolean) => void)(next);
}

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

describe("the read-only key in the Add form (MCP-C2)", () => {
  const base: McpForm = { ...EMPTY_MCP_FORM, name: "n", url: "https://x" };

  it("starts OFF for a new server, and the body always sends it as a boolean", () => {
    expect(EMPTY_MCP_FORM.readOnlyKey).toBe(false);
    expect(mcpBody(base).read_only_key).toBe(false);
    expect(mcpBody({ ...base, alwaysConfirm: false, readOnlyKey: true }).read_only_key).toBe(true);
    expect(typeof mcpBody(base).read_only_key).toBe("boolean");
  });

  it("draws a second labelled tick with the help line of the spec", () => {
    expect(READ_ONLY_KEY_LABEL).toBe("This key can only read");
    expect(READ_ONLY_KEY_HELP).toBe(
      "Every tool of this server runs without asking. " +
        "Turn this on only when the key cannot change data at the source.",
    );
    const html = renderToStaticMarkup(
      createElement(McpFormFields, { form: { ...base, alwaysConfirm: false, readOnlyKey: true }, onChange: noop }),
    );
    expect(html).toContain(READ_ONLY_KEY_LABEL);
    expect(html).toContain(READ_ONLY_KEY_HELP);
    // Two shared Checkboxes, and only the read-only key is ticked.
    expect(html.match(/cc-checkbox/g)?.length).toBe(2);
    expect(html.match(/checked=""/g)?.length).toBe(1);
    expect(html.indexOf(ALWAYS_CONFIRM_LABEL)).toBeLessThan(html.indexOf(READ_ONLY_KEY_LABEL));
  });

  it("clears always confirm when the admin ticks the read-only key, and back", () => {
    const changes: McpForm[] = [];
    tick(McpFormFields({ form: base, onChange: (f) => changes.push(f) }), ReadOnlyKeyField, true);
    expect(changes.at(-1)).toMatchObject({ alwaysConfirm: false, readOnlyKey: true });

    const keyed: McpForm = { ...base, alwaysConfirm: false, readOnlyKey: true };
    tick(McpFormFields({ form: keyed, onChange: (f) => changes.push(f) }), AlwaysConfirmField, true);
    expect(changes.at(-1)).toMatchObject({ alwaysConfirm: true, readOnlyKey: false });

    // A tick OFF leaves the other switch as it is.
    tick(McpFormFields({ form: keyed, onChange: (f) => changes.push(f) }), ReadOnlyKeyField, false);
    expect(changes.at(-1)).toMatchObject({ alwaysConfirm: false, readOnlyKey: false });
  });

  it("never builds a form with both switches on", () => {
    for (const which of ["always_confirm", "read_only_key"] as const) {
      for (const start of [EMPTY_MCP_FORM, { ...EMPTY_MCP_FORM, alwaysConfirm: false, readOnlyKey: true }]) {
        const f = tickFormSwitch(start, which, true);
        expect(f.alwaysConfirm && f.readOnlyKey).toBe(false);
      }
    }
  });
});

describe("one tick clears the other", () => {
  const off: McpSwitches = { always_confirm: false, read_only_key: false };

  it("turns the other switch off when one turns on", () => {
    expect(tickSwitch({ always_confirm: true, read_only_key: false }, "read_only_key", true)).toEqual({
      always_confirm: false,
      read_only_key: true,
    });
    expect(tickSwitch({ always_confirm: false, read_only_key: true }, "always_confirm", true)).toEqual({
      always_confirm: true,
      read_only_key: false,
    });
  });

  it("leaves the other switch when one turns off", () => {
    expect(tickSwitch({ always_confirm: true, read_only_key: false }, "always_confirm", false)).toEqual(off);
    expect(tickSwitch({ always_confirm: false, read_only_key: true }, "read_only_key", false)).toEqual(off);
  });

  it("says what new chats do", () => {
    expect(switchesSavedText({ always_confirm: true, read_only_key: false })).toBe(
      "New chats will ask before any tool runs.",
    );
    expect(switchesSavedText({ always_confirm: false, read_only_key: true })).toBe(
      "New chats will run every tool of this server without asking.",
    );
    expect(switchesSavedText(off)).toBe("New chats will ask only before tools that can change data.");
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
        onSwitches: noop,
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

describe("the read-only key in the side panel (MCP-C2)", () => {
  it("reads the row's key, and a missing key reads as off", () => {
    expect(mcpReadOnlyKey(mcp("false", "true"))).toBe(true);
    expect(mcpReadOnlyKey(mcp("false", "false"))).toBe(false);
    expect(mcpReadOnlyKey(mcp("false"))).toBe(false);
    expect(mcpReadOnlyKey(row())).toBeNull();
    expect(mcpSwitches(mcp("false", "true"))).toEqual({ always_confirm: false, read_only_key: true });
    expect(mcpSwitches(row())).toBeNull();
  });

  it("is a switch, not a line of text", () => {
    expect(detailRows(mcp("false", "true")).map((r) => r.label)).not.toContain("Read only key");
  });

  it("shows on an MCP row only, and only a manager may change it", () => {
    expect(panelPlan(mcp("false", "true"), true).readOnlyKey).toBe("edit");
    expect(panelPlan(mcp("false", "true"), false).readOnlyKey).toBe("view");
    expect(panelPlan(row(), true).readOnlyKey).toBeNull();
  });

  const props = (r = mcp("false", "true"), canManage = true, onSwitches: (s: McpSwitches) => void = noop) => ({
    row: r,
    canManage,
    onNavigate: noop,
    onDisconnect: noop,
    onTest: noop,
    onSwitches,
  });

  it("draws the tick checked from the row, under the always-confirm tick", () => {
    const html = renderToStaticMarkup(createElement(SheetBody, props()));
    expect(html).toContain(READ_ONLY_KEY_LABEL);
    expect(html).toContain(READ_ONLY_KEY_HELP);
    expect(html.match(/checked=""/g)?.length).toBe(1);
    expect(html.indexOf(ALWAYS_CONFIRM_LABEL)).toBeLessThan(html.indexOf(READ_ONLY_KEY_LABEL));
    const off = renderToStaticMarkup(createElement(SheetBody, props(mcp("false", "false"))));
    expect(off).toContain(READ_ONLY_KEY_LABEL);
    expect(off).not.toMatch(/checked=""/);
  });

  it("draws both ticks disabled for a member without integrations:manage", () => {
    const html = renderToStaticMarkup(createElement(SheetBody, props(mcp("false", "true"), false)));
    expect(html.match(/disabled=""/g)?.length).toBe(2);
  });

  it("draws no tick on a row that is not an MCP server", () => {
    expect(renderToStaticMarkup(createElement(SheetBody, props(row())))).not.toContain(READ_ONLY_KEY_LABEL);
  });

  it("sends the pair with the other switch cleared when the admin ticks one", () => {
    const sent: McpSwitches[] = [];
    tick(SheetBody(props(mcp("true", "false"), true, (s) => sent.push(s))), ReadOnlyKeyField, true);
    expect(sent.at(-1)).toEqual({ always_confirm: false, read_only_key: true });
    tick(SheetBody(props(mcp("false", "true"), true, (s) => sent.push(s))), AlwaysConfirmField, true);
    expect(sent.at(-1)).toEqual({ always_confirm: true, read_only_key: false });
    tick(SheetBody(props(mcp("false", "true"), true, (s) => sent.push(s))), ReadOnlyKeyField, false);
    expect(sent.at(-1)).toEqual({ always_confirm: false, read_only_key: false });
  });

  it("says in the Connect dialog that the key stops after an OAuth sign-in", () => {
    const keyed = connectDialogCopy(mcp("false", "true"), "dana@x.test", "connect");
    expect(keyed.note).toContain(`The switch "${READ_ONLY_KEY_LABEL}" does not apply`);
    const plain = connectDialogCopy(mcp("false", "false"), "dana@x.test", "connect");
    expect(plain.note).not.toContain(READ_ONLY_KEY_LABEL);
  });
});

describe("the read-only key on an OAuth row (review of MCP-C2, P2)", () => {
  // The gateway names the admin of an OAuth row in `acts_as`, and it refuses
  // the read-only key there with 422.
  const oauth = (readOnlyKey = "false") => ({ ...mcp("false", readOnlyKey), acts_as: "dana@x.test" });

  it("knows an OAuth row by its acts_as", () => {
    expect(mcpActsThroughOAuth(oauth())).toBe(true);
    expect(mcpActsThroughOAuth(mcp("false", "false"))).toBe(false);
    expect(mcpActsThroughOAuth({ ...mcp("false"), acts_as: "  " })).toBe(false);
    expect(mcpActsThroughOAuth({ ...row(), acts_as: "dana@x.test" })).toBe(false);
  });

  it("plans the tick as view only, with the line that says why", () => {
    expect(READ_ONLY_KEY_OAUTH_NOTE).toBe(
      "This switch does not apply to a connection that acts as an admin through OAuth.",
    );
    const plan = panelPlan(oauth(), true);
    expect(plan.readOnlyKey).toBe("view");
    expect(plan.readOnlyKeyNote).toBe(READ_ONLY_KEY_OAUTH_NOTE);
    // Always confirm still applies to an OAuth row, so a manager edits it.
    expect(plan.alwaysConfirm).toBe("edit");
    const header = panelPlan(mcp("false", "false"), true);
    expect(header.readOnlyKey).toBe("edit");
    expect(header.readOnlyKeyNote).toBeNull();
  });

  it("draws the read-only tick disabled with the line, and the other tick live", () => {
    const html = renderToStaticMarkup(
      createElement(SheetBody, {
        row: oauth(),
        canManage: true,
        onNavigate: noop,
        onDisconnect: noop,
        onTest: noop,
        onSwitches: noop,
      }),
    );
    expect(html).toContain(READ_ONLY_KEY_LABEL);
    expect(html).toContain(READ_ONLY_KEY_OAUTH_NOTE);
    expect(html.match(/disabled=""/g)?.length).toBe(1);
    const plain = renderToStaticMarkup(
      createElement(SheetBody, {
        row: mcp("false", "false"),
        canManage: true,
        onNavigate: noop,
        onDisconnect: noop,
        onTest: noop,
        onSwitches: noop,
      }),
    );
    expect(plain).not.toContain(READ_ONLY_KEY_OAUTH_NOTE);
  });
});

describe("the pair after a save (review of MCP-C2, P3)", () => {
  const before = mcp("true", "false"); // the row as the cached list holds it
  const savedPair: McpSwitches = { always_confirm: false, read_only_key: true };
  const listA = [before];
  const listB = [mcp("false", "true")];

  it("draws the row when no save is held", () => {
    expect(heldSwitches(before, null, listA)).toEqual({
      switches: { always_confirm: true, read_only_key: false },
      wait: false,
      done: false,
    });
  });

  it("draws the saved pair until the row shows it", () => {
    const held: HeldSwitches = { id: before.id, pair: savedPair, list: listA };
    // The list read again, and it still holds the old row.
    expect(heldSwitches(before, held, [before])).toEqual({ switches: savedPair, wait: false, done: false });
    // The row shows the saved pair: the hold is done.
    expect(heldSwitches(listB[0], held, listB)).toEqual({ switches: savedPair, wait: false, done: true });
  });

  it("makes the ticks wait after a failed save, until a newer list", () => {
    const held: HeldSwitches = { id: before.id, pair: null, list: listA };
    expect(heldSwitches(before, held, listA)).toEqual({
      switches: { always_confirm: true, read_only_key: false },
      wait: true,
      done: false,
    });
    expect(heldSwitches(listB[0], held, listB)).toEqual({ switches: savedPair, wait: false, done: true });
  });

  it("drops a hold of another row", () => {
    const held: HeldSwitches = { id: "mcp:other", pair: savedPair, list: listA };
    expect(heldSwitches(before, held, listA).done).toBe(true);
    expect(heldSwitches(before, held, listA).switches).toEqual({ always_confirm: true, read_only_key: false });
  });

  it("builds the next tick from the held pair, not from the old row", () => {
    const sent: McpSwitches[] = [];
    const tree = SheetBody({
      row: before,
      canManage: true,
      onNavigate: noop,
      onDisconnect: noop,
      onTest: noop,
      onSwitches: (s) => sent.push(s),
      switches: savedPair,
    });
    // The member unticks the key. From the old row this sent always confirm
    // true, a switch the member did not touch.
    tick(tree, ReadOnlyKeyField, false);
    expect(sent.at(-1)).toEqual({ always_confirm: false, read_only_key: false });
    const html = renderToStaticMarkup(
      createElement(SheetBody, {
        row: before,
        canManage: true,
        onNavigate: noop,
        onDisconnect: noop,
        onTest: noop,
        onSwitches: noop,
        switches: savedPair,
      }),
    );
    // The saved pair shows: only the read-only key is ticked.
    expect(html.match(/checked=""/g)?.length).toBe(1);
    expect(html.indexOf('checked=""')).toBeGreaterThan(html.indexOf(ALWAYS_CONFIRM_LABEL));
  });

  it("answers the pair that the gateway saved, and that pair wins over the one sent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ ok: true, name: "x", always_confirm: false, read_only_key: true }), {
            status: 200,
          }),
      ),
    );
    await expect(api.setMcpSwitches("x", { always_confirm: true, read_only_key: false })).resolves.toEqual(
      savedPair,
    );
    // An answer with no boolean keys falls back to the pair that was sent.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
    await expect(api.setMcpSwitches("x", { always_confirm: true, read_only_key: false })).resolves.toEqual({
      always_confirm: true,
      read_only_key: false,
    });
  });
});

describe("setMcpSwitches", () => {
  it("sends one PATCH with both keys", async () => {
    const fn = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fn);
    await api.setMcpSwitches("brave search", { always_confirm: false, read_only_key: true });
    expect(fn).toHaveBeenCalledWith("/api/integrations/mcp/brave%20search", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ always_confirm: false, read_only_key: true }),
    });
  });

  it("throws the server's words on a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Forbidden" }), { status: 403 })));
    await expect(api.setMcpSwitches("x", { always_confirm: true, read_only_key: false })).rejects.toMatchObject({
      status: 403,
      message: "Forbidden",
    });
  });
});

describe("the BFF PATCH of the switches", () => {
  let calls: { url: string; init?: RequestInit }[] = [];

  function stubGateway() {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
  }

  async function patch(body: string, name = "brave"): Promise<NextResponse> {
    const { PATCH } = await import("../../api/integrations/mcp/[name]/route");
    const req = new NextRequest(`http://localhost:3001/api/integrations/mcp/${name}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body,
    });
    return PATCH(req, { params: Promise.resolve({ name }) });
  }

  it.each([
    [{ always_confirm: false }, { always_confirm: false }],
    [{ read_only_key: true }, { read_only_key: true }],
    [
      { always_confirm: false, read_only_key: true },
      { always_confirm: false, read_only_key: true },
    ],
    // Nothing else in the body reaches the gateway.
    [{ read_only_key: true, url: "https://evil.example", organization_id: "org-b" }, { read_only_key: true }],
  ])("forwards only the keys that it got: %j", async (body, forwarded) => {
    stubGateway();
    const res = await patch(JSON.stringify(body));
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://gw.test/integrations/mcp/brave");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(forwarded);
  });

  it.each([
    ["{}"],
    ['{"read_only_key": "true"}'],
    ['{"read_only_key": 1}'],
    ['{"always_confirm": null}'],
    ['{"always_confirm": true, "read_only_key": "on"}'],
    ["[true]"],
    ["not json"],
  ])("answers 422 and calls no gateway for %s", async (body) => {
    stubGateway();
    const res = await patch(body);
    expect(res.status).toBe(422);
    expect(calls).toHaveLength(0);
  });
});
