// The short read and the activities that the mirror does not hold, on the Sync
// health card (WS-53 CRM-Q24, spec section 18.10.2 items 2, 4, 6 and 11).
// The runner has no DOM, so the card is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  activitiesOf,
  activityLines,
  shortBy,
  shortReadSentence,
  shortReads,
  type HealthEntity,
  type SyncHealth,
} from "../lib/syncHealth";
import { SyncHealthCardView } from "./SyncHealthCard";

const NOW = Date.parse("2026-10-16T12:00:00Z");

const row = (key: string, zoho: number | null, ours: number | null, short?: number | null): HealthEntity => ({
  key,
  zoho_count: zoho,
  ours,
  last_run: { seen: ours ?? 0, failed: 0 },
  short_by: short,
});

const health = (over: Partial<SyncHealth> = {}): SyncHealth => ({
  last_run: null,
  last_success_at: null,
  next_run_at: null,
  entities: [row("deals", 10, 10)],
  modules: [
    { api_name: "Deals", label: "Deals", custom: false, api_supported: true, mirrored: true },
    { api_name: "Payment_Made", label: "Payments Made", custom: true, api_supported: true, mirrored: true },
    { api_name: "Install_Base", label: "Install Base", custom: true, api_supported: true, mirrored: true },
    { api_name: "Vendors_X", label: "Vendor Visits", custom: true, api_supported: true, mirrored: false },
  ],
  history: [],
  running: false,
  credits: { budget_24h: 500, used_24h: 0 },
  ...over,
});

const draw = (h: SyncHealth) =>
  renderToStaticMarkup(createElement(SyncHealthCardView, { health: h, nowMs: NOW, onSyncNow: () => undefined }));

describe("a short read (items 2 and 6)", () => {
  const payments = row("module:Payment_Made", 4947, 3003, 1944);

  it("says the numbers of the spec, with the label of the row", () => {
    expect(shortReadSentence(payments)).toBe(
      "Zoho counts 4,947. The connected Zoho user can read 3,003. Connect with a Zoho admin to read all of them.",
    );
    const html = draw(health({ entities: [row("deals", 10, 10), payments] }));
    expect(html).toContain("Short read");
    expect(html).toContain("Payments Made");
    expect(html).toContain("Zoho counts 4,947. The connected Zoho user can read 3,003.");
  });

  it("takes ours plus the gap when the row has no Zoho count", () => {
    expect(shortReadSentence(row("organizations", null, 3003, 1944))).toContain("Zoho counts 4,947.");
  });

  it("shows a line for each row with a gap, and none for a whole read", () => {
    const h = health({
      entities: [row("deals", 10, 10, null), row("organizations", 7, 4, 3), payments, row("leads", 2, 2, 0)],
    });
    expect(shortReads(h).map((e) => e.key)).toEqual(["organizations", "module:Payment_Made"]);
    expect(shortBy(row("leads", 2, 2))).toBeNull();
    expect(draw(health())).not.toContain("Short read");
  });
});

describe("the activities left out (items 4 and 11)", () => {
  const activities = {
    parentless: { task: 1145 },
    dropped: { Vendors_X: 12, Install_Base: 3, Deals: 2 },
    waiting: { note: 3, call: 1 },
    recovered_7d: { Install_Base: 40 },
  };

  it("says the line of item 4 for the tasks with no parent", () => {
    const lines = activityLines(health({ activities }));
    expect(lines[0]).toEqual({
      key: "parentless:task",
      kind: "parentless",
      text: "1,145 Zoho tasks belong to no record. Metorite leaves them out.",
    });
  });

  it("names a module that Metorite does not sync, and never a module that it syncs", () => {
    const dropped = activityLines(health({ activities })).filter((l) => l.kind === "dropped");
    expect(dropped.map((l) => l.text)).toEqual([
      "12 Zoho activities belong to Vendor Visits, a module that Metorite does not sync.",
    ]);
  });

  it("says that the notes wait for their record, and that some came back", () => {
    const texts = activityLines(health({ activities })).map((l) => l.text);
    expect(texts).toContain("1 Zoho call waits for its record. Metorite links it when it reads that record.");
    expect(texts).toContain("3 Zoho notes wait for their record. Metorite links them when it reads that record.");
    expect(texts).toContain("40 Zoho activities on Install Base came back in the last 7 days.");
  });

  it("draws one line for each number above 0", () => {
    const html = draw(health({ activities: { ...activities, waiting: { note: 0 } } }));
    expect(html).toContain("1,145 Zoho tasks belong to no record.");
    expect(html).toContain("Vendor Visits");
    expect(html).toContain("came back in the last 7 days");
    expect(html).not.toContain("wait for");
    expect(activityLines(health({ activities: { ...activities, parentless: { task: 0 } } })).some(
      (l) => l.kind === "parentless",
    )).toBe(false);
  });

  it("reads an older answer with no block as no line", () => {
    expect(activitiesOf(health())).toEqual({ parentless: {}, dropped: {}, waiting: {}, recovered_7d: {} });
    expect(activityLines(health())).toEqual([]);
    expect(draw(health())).not.toContain("Activities left out");
  });
});
