// WS-CAL CE-10d: the Also sync its calendar offer, drawn and mounted. The runner has no DOM, so
// each face renders to a string and the three mounts are source fences.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ConnectionsList, MailboxOffer } from "../lib/calendarConnections";
import { AlsoSyncView, type AlsoSyncViewProps } from "./AlsoSyncCalendar";

const noop = () => undefined;
const G: MailboxOffer = { provider: "google", address: "ada@example.com" };
const O: MailboxOffer = { provider: "outlook", address: "bo@contoso.com" };

function list(offers: MailboxOffer[] | undefined, over: Partial<ConnectionsList> = {}): ConnectionsList {
  return {
    enabled: true,
    providers: { google: { available: true }, outlook: { available: true } },
    connections: [],
    ...(offers ? { mailbox_offers: offers } : {}),
    ...over,
  };
}

function draw(over: Partial<AlsoSyncViewProps> = {}): string {
  return renderToStaticMarkup(
    createElement(AlsoSyncView, { list: list([G]), activeAddress: null, busy: false, error: null, onSync: noop, ...over }),
  );
}

describe("AlsoSyncView", () => {
  it("draws nothing while loading, when not enabled, or with no offer", () => {
    expect(draw({ list: undefined })).toBe("");
    expect(draw({ list: null })).toBe("");
    expect(draw({ list: list([G], { enabled: false }) })).toBe("");
    expect(draw({ list: list([]) })).toBe("");
    expect(draw({ list: list(undefined) })).toBe("");
  });

  it("draws the line and the button of each offer", () => {
    const html = draw({ list: list([G, O]) });
    expect(html).toContain("Your mailbox ada@example.com is connected. Also sync its calendar?");
    expect(html).toContain("Your mailbox bo@contoso.com is connected. Also sync its calendar?");
    expect(html.match(/Sync its calendar/g)?.length).toBeGreaterThanOrEqual(2);
  });

  it("names the provider in the row and in the button label", () => {
    const html = draw({ list: list([G, O]) });
    expect(html).toContain("Sync the Google calendar of ada@example.com");
    expect(html).toContain("Sync the Outlook calendar of bo@contoso.com");
  });

  it("hides an offer whose provider cannot connect", () => {
    const html = draw({ list: list([G, O], { providers: { google: { available: false }, outlook: { available: true } } }) });
    expect(html).not.toContain("ada@example.com");
    expect(html).toContain("bo@contoso.com");
  });

  it("disables every button while a request runs", () => {
    const idle = draw({ list: list([G, O]) });
    expect(idle).not.toContain('disabled=""');
    const busy = draw({ list: list([G, O]), busy: true, activeAddress: G.address });
    expect((busy.match(/disabled=""/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });

  it("explains a failure and names the guide as plain text", () => {
    const html = draw({ error: "That mailbox is not connected to your account. Reload the page." });
    expect(html).toContain('role="alert"');
    expect(html).toContain("That mailbox is not connected to your account. Reload the page.");
    expect(html).toContain("Full guide: docs/calendar_connect.md");
  });
});

describe("the three mounts", () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf-8");
  it("mounts in Calendar settings, above the plain buttons", () => {
    expect(read("./CalendarConnections.tsx")).toMatch(/<AlsoSyncCalendar[\s\S]*<ConnectButtons/);
  });
  it("mounts in the Calendar page prompt", () => {
    const src = read("./CalendarConnections.tsx");
    expect(src.slice(src.indexOf("export function CalendarConnectPrompt"))).toContain("<AlsoSyncCalendar");
  });
  it("mounts under the email Accounts list", () => {
    expect(read("../../email/components/AccountSidebar.tsx")).toContain("<AlsoSyncCalendar");
  });
  it("opens the consent URL only through safeConsentUrl, one request at a time", () => {
    const src = read("../lib/useCalendarConnect.ts");
    expect(src).toContain("safeConsentUrl(p, r.url)");
    expect(src).toContain("inFlight");
  });
});
