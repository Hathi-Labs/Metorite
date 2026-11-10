/**
 * The number format selects, and the client half of the preference
 * (owner, 2026-10-19). Spec: `whatsapp_assistant_channel.md` §13.10.
 *
 * vitest runs in the node environment, so the selects draw with
 * `renderToStaticMarkup`. The saves run against a recorded `fetch`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MyNumberFormat, OrgNumberFormat } from "@/components/NumberFormatSettings";

import {
  NUMBER_SYSTEM_LABELS,
  USE_ORG,
  memberChoice,
  memberOptions,
  memberValue,
  orgChoiceLabel,
  publishNumberSystem,
  resetNumberSystemForTests,
  saveMyNumberSystem,
  saveOrgNumberSystem,
  settingsFrom,
  subscribeNumberSystem,
} from "./numberSystem";

const SRC = join(__dirname, "..");

afterEach(() => {
  vi.unstubAllGlobals();
  resetNumberSystemForTests();
});

describe("the gateway's answer, read field by field", () => {
  it("keeps the two systems and nothing else, and resolved is never empty", () => {
    expect(settingsFrom({ org: "international", member: null, resolved: "international" }))
      .toEqual({ org: "international", member: null, resolved: "international" });
    expect(settingsFrom({ org: "roman", member: 3 })).toEqual({ org: null, member: null, resolved: "indian" });
    expect(settingsFrom(null)).toEqual({ org: null, member: null, resolved: "indian" });
  });
});

describe("the member select", () => {
  it("shows the override, or 'Use the organization's setting' when there is none", () => {
    expect(memberChoice({ org: "international", member: null, resolved: "international" })).toBe(USE_ORG);
    expect(memberChoice({ org: "international", member: "indian", resolved: "indian" })).toBe("indian");
  });

  it("saves null for 'Use the organization's setting', so the org's value applies", () => {
    expect(memberValue(USE_ORG)).toBeNull();
    expect(memberValue("international")).toBe("international");
    expect(memberValue("roman")).toBeNull();
  });

  it("names what the organization has now", () => {
    expect(orgChoiceLabel({ org: null, member: null, resolved: "indian" }))
      .toBe("Use the organization's setting (Indian (lakh, crore))");
    expect(orgChoiceLabel({ org: "international", member: null, resolved: "international" }))
      .toBe("Use the organization's setting (International (million, billion))");
  });
});

describe("the selects, drawn", () => {
  it("the member's list offers 'Use the organization's setting' first, then the two systems", () => {
    expect(memberOptions(null).map((o) => o.value)).toEqual([USE_ORG, "indian", "international"]);
    expect(memberOptions({ org: "international", member: null, resolved: "international" }).map((o) => o.label))
      .toEqual([
        "Use the organization's setting (International (million, billion))",
        NUMBER_SYSTEM_LABELS.indian,
        NUMBER_SYSTEM_LABELS.international,
      ]);
  });

  it("the organization's select shows Indian until an admin chose", () => {
    const html = renderToStaticMarkup(createElement(OrgNumberFormat));
    expect(html).toContain("Number format");
    expect(html).toContain('aria-label="Organization number format"');
    expect(html).toContain(NUMBER_SYSTEM_LABELS.indian);
    expect(html).not.toContain("Use the organization");
  });

  it("the member's select shows 'Use the organization's setting' until the member chose", () => {
    const html = renderToStaticMarkup(createElement(MyNumberFormat));
    expect(html).toContain('aria-label="My number format"');
    expect(html).toContain("Use the organization&#x27;s setting");
  });

  it("uses the house SelectButton, and sits on the two settings pages", () => {
    const comp = readFileSync(join(SRC, "components", "NumberFormatSettings.tsx"), "utf-8");
    expect(comp).toMatch(/import \{ SelectButton \} from "@\/components\/ui\/SelectButton"/);
    expect(comp).not.toMatch(/<[Ss]elect\b/);
    const branding = readFileSync(join(SRC, "app", "settings", "organization", "BrandingTab.tsx"), "utf-8");
    expect(branding).toMatch(/<OrgNumberFormat \/>/);
    const appearance = readFileSync(join(SRC, "app", "settings", "appearance", "page.tsx"), "utf-8");
    expect(appearance).toMatch(/<MyNumberFormat \/>/);
  });
});

describe("a save", () => {
  function record(answer: unknown, status = 200) {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(answer), { status });
    });
    return calls;
  }

  it("the member's clear sends null to /me, and every chart hears the answer", async () => {
    const calls = record({ org: "international", member: null, resolved: "international" });
    const heard: string[] = [];
    publishNumberSystem("indian");
    const stop = subscribeNumberSystem((v) => heard.push(v));
    const s = await saveMyNumberSystem(null);
    stop();
    expect(calls[0].url).toBe("/api/settings/number-system/me");
    expect(calls[0].init.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ value: null });
    expect(s.resolved).toBe("international");
    expect(heard).toEqual(["international"]);
  });

  it("the org's save goes to the org path", async () => {
    const calls = record({ org: "international", member: null, resolved: "international" });
    await saveOrgNumberSystem("international");
    expect(calls[0].url).toBe("/api/settings/number-system");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ value: "international" });
  });

  it("a refused save says why", async () => {
    record({ detail: "Permission denied" }, 403);
    await expect(saveOrgNumberSystem("international")).rejects.toThrow("Permission denied");
  });
});
