// "Suggest mappings" on the Field map (WS-53 CRM-Q7b, spec section 18.7).
// The runner has no DOM, so each component is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import * as api from "../lib/api";
import {
  SUGGEST_LABEL,
  suggestionLine,
  type FieldMap,
  type FieldRule,
  type FieldSuggestion,
} from "../lib/fieldMap";
import type { EntitySlug } from "../lib/types";
import { FieldMapCardView, type FieldMapHandlers } from "./FieldMapCard";

const proposal = (over: Partial<FieldRule> = {}): FieldRule => ({
  id: "p1",
  entity: "lead",
  field_api_name: "Sector",
  field_type: "select",
  profile_version: 3,
  target_column: "industry",
  status: "proposed",
  proposed_by: "ai",
  confidence: 0.9,
  reason: "The labels name an industry.",
  decided_by: null,
  decided_at: null,
  ...over,
});

const leadMap = (rules: FieldRule[] = []): FieldMap => ({
  entity: "lead",
  profile_version: 3,
  fields: [{ key: "Sector", label: "Sector", type: "select", visible: true, targets: ["industry"] }],
  rules,
  targets: [],
});

const emptyMap: FieldMap = { entity: "contact", profile_version: 3, fields: [], rules: [], targets: [] };

const base: FieldMapHandlers = { busy: null, onPick: () => undefined, onDecide: () => undefined };

const draw = (handlers: FieldMapHandlers, leads: FieldMap | null = leadMap()) =>
  renderToStaticMarkup(
    createElement(FieldMapCardView, {
      maps: { leads, contacts: emptyMap, organizations: null, deals: leadMap() },
      handlers,
    }),
  );

/** The markup of one entity block, from its label to the next one. */
const block = (html: string, label: string, next: string): string =>
  html.slice(html.indexOf(`aria-label="${label}"`), html.indexOf(`aria-label="${next}"`));

const result = (over: Partial<FieldSuggestion> = {}): FieldSuggestion => ({
  entity: "lead",
  profile_version: 3,
  asked: true,
  dropped: 0,
  proposals: [],
  ...over,
});

describe("the Suggest mappings button", () => {
  const withSuggest: FieldMapHandlers = { ...base, onSuggest: () => undefined, suggesting: null };

  it("shows on each entity that has fields, for the admin who holds the handler", () => {
    const html = draw(withSuggest);
    expect(block(html, "Leads", "Contacts")).toContain(SUGGEST_LABEL);
    expect(html.split(SUGGEST_LABEL)).toHaveLength(3); // Leads and Deals
  });

  it("is absent with no handler, on an entity with no field, and while a map loads", () => {
    expect(draw(base)).not.toContain(SUGGEST_LABEL);
    const html = draw(withSuggest, null);
    expect(block(html, "Leads", "Contacts")).not.toContain(SUGGEST_LABEL);
    expect(block(html, "Contacts", "Companies")).not.toContain(SUGGEST_LABEL);
  });

  it("shows the loading state on the entity that runs, and holds the others", () => {
    const html = draw({ ...withSuggest, suggesting: "leads" as EntitySlug });
    const leads = block(html, "Leads", "Contacts");
    expect(leads).toContain("disabled");
    expect(leads).toContain("animate-spin");
    const deals = html.slice(html.indexOf('aria-label="Deals"'));
    expect(deals).toContain("disabled");
    expect(deals).not.toContain("animate-spin");
  });

  it("shows the error of a failed suggestion under its entity only", () => {
    const html = draw({
      ...withSuggest,
      suggestErrors: { leads: "The assistant could not suggest mappings now. Try again later." },
    });
    const leads = block(html, "Leads", "Contacts");
    expect(leads).toContain('role="alert"');
    expect(leads).toContain("could not suggest mappings now");
    expect(html.split('role="alert"')).toHaveLength(2);
  });

  it("draws an answer of the assistant as a proposal that waits for approve or reject", () => {
    const html = draw(withSuggest, leadMap([proposal()]));
    const leads = block(html, "Leads", "Contacts");
    expect(leads).toContain("Proposed by the assistant");
    expect(leads).toContain("90% sure");
    expect(leads).toContain("Approve");
    expect(leads).toContain("Reject");
  });
});

describe("the sentence after a press", () => {
  it("counts the proposals that wait, and never says mapped", () => {
    expect(suggestionLine(result({ proposals: [proposal(), proposal({ id: "p2" })] }))).toBe(
      "2 proposals wait for you. Approve or reject each one.",
    );
    expect(suggestionLine(result({ proposals: [proposal(), proposal({ status: "approved" })] }))).toBe(
      "1 proposal waits for you. Approve or reject each one.",
    );
    expect(suggestionLine(result())).toBe("The assistant found no field that fits a column.");
    expect(suggestionLine(result({ asked: false }))).toBe("No proposal waits for you.");
  });
});

describe("the suggest call", () => {
  it("posts the entity to the suggest route through the proxy", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      const body = JSON.stringify(result());
      return { ok: true, status: 200, json: async () => JSON.parse(body), text: async () => body };
    }) as typeof globalThis.fetch;
    try {
      const got = await api.suggestFieldRules("leads");
      expect(got.asked).toBe(true);
    } finally {
      globalThis.fetch = original;
    }
    expect(calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/crm/field-rules/suggest", "POST", JSON.stringify({ entity: "leads" })],
    ]);
  });

  it("keeps the gateway's sentence on a failure", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (async () => ({
      ok: false,
      status: 502,
      json: async () => ({
        detail: { code: "suggest_failed", message: "The assistant could not suggest mappings now." },
      }),
    })) as unknown as typeof globalThis.fetch;
    try {
      await expect(api.suggestFieldRules("deals")).rejects.toMatchObject({
        status: 502,
        code: "suggest_failed",
        message: "The assistant could not suggest mappings now.",
      });
    } finally {
      globalThis.fetch = original;
    }
  });
});
