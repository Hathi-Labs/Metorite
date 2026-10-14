// WS-54: the Zoho CRM tile reads the CRM's own connection, and its panel links
// to CRM settings. The owner saw "Not set up" while the CRM held a live Zoho
// connection, because the tile counted only `api_key` rows named "zoho-crm".
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import { IntegrationFormCard } from "@/components/AddAgentWizard";
import { IntegrationCard } from "@/components/IntegrationSetup";
import ZohoInCrm from "@/components/ZohoInCrm";
import {
  APP_OWNED_TILES,
  appConnection,
  appOwnedTile,
  orgKeyConfigured,
  rowsForTile,
} from "../lib/connections";
import { row } from "../lib/testRows";

const CRM_ZOHO = row({ id: "zoho:1", kind: "oauth", provider: "zoho", owning_app: "crm", status: "connected" });

describe("the Zoho CRM tile counts the CRM's connection", () => {
  it("is configured when the registry holds the CRM's live Zoho row, of any kind", () => {
    expect(orgKeyConfigured([CRM_ZOHO], "zoho-crm", false)).toBe(true);
    expect(orgKeyConfigured([{ ...CRM_ZOHO, kind: "api_key" }], "zoho-crm", false)).toBe(true);
    expect(orgKeyConfigured([{ ...CRM_ZOHO, status: "configured" }], "zoho-crm", false)).toBe(true);
  });

  it("is not configured for a row that is not live, or not the CRM's", () => {
    expect(orgKeyConfigured([{ ...CRM_ZOHO, status: "needs_reconnect" }], "zoho-crm", true)).toBe(false);
    expect(orgKeyConfigured([{ ...CRM_ZOHO, status: "disconnected" }], "zoho-crm", true)).toBe(false);
    expect(orgKeyConfigured([{ ...CRM_ZOHO, owning_app: "integrations" }], "zoho-crm", true)).toBe(false);
    expect(orgKeyConfigured([], "zoho-crm", true)).toBe(false);
  });

  it("does not count the old Zoho key row, which reaches no agent and no CRM", () => {
    const old = row({ id: "key:zoho-crm", kind: "api_key", provider: "zoho-crm", status: "configured" });
    expect(orgKeyConfigured([old], "zoho-crm", false)).toBe(false);
  });

  it("falls back to the server only when the registry is unreadable", () => {
    expect(orgKeyConfigured(null, "zoho-crm", true)).toBe(true);
    expect(orgKeyConfigured(null, "zoho-crm", false)).toBe(false);
  });

  it("uses the server's value when only the registry source failed", () => {
    const key = row({ id: "key:apollo", kind: "api_key", provider: "apollo", status: "configured" });
    // The registry did not read, so no CRM row is in the list. The tile must
    // not read "Not set up" from a list that could not hold the row.
    const rows = rowsForTile([key], ["registry"], "zoho-crm");
    expect(rows).toBeNull();
    expect(orgKeyConfigured(rows, "zoho-crm", true)).toBe(true);
    expect(appConnection(rows, "zoho-crm")).toBeNull();
    // A key tile does not read the registry, so its rows stand.
    expect(rowsForTile([key], ["registry"], "apollo")).toEqual([key]);
    // Another source failing leaves the Zoho tile on the rows.
    expect(rowsForTile([CRM_ZOHO], ["mailboxes"], "zoho-crm")).toEqual([CRM_ZOHO]);
    expect(rowsForTile(null, [], "zoho-crm")).toBeNull();
  });

  it("keeps one alias map, and a key tile still reads its own key rows", () => {
    expect(APP_OWNED_TILES["zoho-crm"]).toEqual({
      provider: "zoho", owningApp: "crm", settingsHref: "/crm?tab=settings&section=setup",
    });
    expect(appConnection([CRM_ZOHO], "zoho-crm")).toBe(CRM_ZOHO);
    expect(appConnection([CRM_ZOHO], "apollo")).toBeNull();
    const key = row({ id: "key:apollo", kind: "api_key", provider: "apollo", status: "configured" });
    expect(orgKeyConfigured([key, CRM_ZOHO], "apollo", false)).toBe(true);
  });
});

describe("the Zoho CRM panel links to CRM settings", () => {
  const html = (props: Parameters<typeof ZohoInCrm>[0]) =>
    renderToStaticMarkup(createElement(ZohoInCrm, props));

  it("says connected through the CRM, with the data centre, and links to CRM settings", () => {
    const out = html({ connected: true, connection: { ...CRM_ZOHO, detail: { location: "in" } } });
    expect(out).toContain("Connected through the CRM.");
    expect(out).toContain("Data centre: IN");
    expect(out).toContain("Manage in CRM settings");
    expect(out).not.toMatch(/<input/);
  });

  it("tells a not-connected org to connect from CRM settings, with no key inputs", () => {
    const out = html({ connected: false, connection: null });
    expect(out).toContain("Connect Zoho from CRM settings. It takes one click.");
    expect(out).toContain("Manage in CRM settings");
    expect(out).not.toMatch(/<input/);
    expect(out).not.toMatch(/Self Client|Client ID|Refresh Token/);
  });

  it("the button goes to the CRM setup hub through the router, as the app does", () => {
    const panel = readFileSync(join(__dirname, "../../../components/ZohoInCrm.tsx"), { encoding: "utf-8" });
    expect(panel).toMatch(/router\.push\(APP_OWNED_TILES\[ZOHO_SERVICE\]\.settingsHref\)/);
    const view = readFileSync(join(__dirname, "ApiKeysView.tsx"), { encoding: "utf-8" });
    expect(view).toMatch(/<ZohoInCrm connected=\{api\.configured\}/);
    for (const src of [panel, view]) expect(src).not.toMatch(/from "next\/link"/);
  });

  it("reads only an own key of the map", () => {
    expect(appOwnedTile("zoho-crm")).toBe(APP_OWNED_TILES["zoho-crm"]);
    expect(appOwnedTile("constructor")).toBeUndefined();
    expect(appOwnedTile("apollo")).toBeUndefined();
  });
});

// The two agent setup cards are not mounted today. If one comes back, its
// Zoho card must link to the CRM and must not post an empty save.
describe("the agent setup cards give Zoho CRM the CRM link, not a form", () => {
  const integration = (service: string, configured = false): IntegrationStatus => ({
    service, label: service, configured, mandatory: true, description: "",
    setup_url: "", docs_url: "", instructions: "", missing_keys: [],
    env_vars: service === "apollo" ? [{ key: "APOLLO_API_KEY", label: "API key", sensitive: true }] : [],
  });
  const noop = () => {};
  const wizard = (svc: string) => renderToStaticMarkup(createElement(IntegrationFormCard, {
    integration: integration(svc),
    state: { values: {}, saving: false, testing: false, testResult: null, saved: false, skipped: false, error: null },
    optional: false, onValueChange: noop, onSave: noop, onTest: noop,
  }));
  const setup = (svc: string) => renderToStaticMarkup(createElement(IntegrationCard, {
    integration: integration(svc),
    cardState: { values: {}, saving: false, testing: false, testResult: null, saved: false, error: null },
    onValuesChange: noop, onSave: noop, onTest: noop,
  }));

  it("AddAgentWizard", () => {
    const out = wizard("zoho-crm");
    expect(out).toContain("Manage in CRM settings");
    expect(out).not.toMatch(/<input|Save &amp; continue|>Test</);
    expect(wizard("apollo")).toMatch(/<input/);
  });

  it("IntegrationSetup", () => {
    const out = setup("zoho-crm");
    expect(out).toContain("Manage in CRM settings");
    expect(out).not.toMatch(/<input|Save credentials|>Test</);
    expect(setup("apollo")).toMatch(/<input/);
  });
});
