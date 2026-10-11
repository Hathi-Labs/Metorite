// The Sync health card (WS-53 CRM-Q9, spec section 18.3 step 4 and 18.5).
// The runner has no DOM, so each component is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  UNAVAILABLE_SENTENCE,
  agoLabel,
  creditsPercent,
  difference,
  differenceLabel,
  entityLabel,
  errorSentence,
  isRunning,
  moduleState,
  statusLines,
  type HealthEntity,
  type SyncHealth,
} from "../lib/syncHealth";
import PipelineSettings from "./PipelineSettings";
import { SyncHealthCardView } from "./SyncHealthCard";

const NOW = Date.parse("2026-10-12T12:00:00Z");
const minutesAgo = (n: number) => new Date(NOW - n * 60_000).toISOString();
const inMinutes = (n: number) => new Date(NOW + n * 60_000).toISOString();

const entity = (key: string, zoho: number | null, ours: number | null, last = {}): HealthEntity => ({
  key,
  zoho_count: zoho,
  ours,
  last_run: { seen: ours ?? 0, failed: 0, unlinked: 0, ...last },
});

const healthy = (over: Partial<SyncHealth> = {}): SyncHealth => ({
  last_run: {
    kind: "incremental",
    status: "ok",
    started_at: minutesAgo(13),
    finished_at: minutesAgo(12),
    error: null,
    credits_used: 4,
  },
  last_success_at: minutesAgo(12),
  next_run_at: inMinutes(8),
  entities: [entity("leads", 40, 40), entity("deals", 10, 10)],
  modules: [
    { api_name: "Leads", label: "Leads", custom: false, api_supported: true, mirrored: true },
    { api_name: "Installs", label: "Installs", custom: true, api_supported: true, mirrored: false },
    { api_name: "Vendors", label: "Vendors", custom: false, api_supported: false, mirrored: false },
  ],
  history: [
    {
      kind: "reconcile",
      status: "ok",
      started_at: minutesAgo(600),
      finished_at: minutesAgo(599),
      error: null,
      credits_used: 40,
    },
  ],
  credits: { budget_24h: 500, used_24h: 125 },
  ...over,
});

const draw = (health: SyncHealth) =>
  renderToStaticMarkup(
    createElement(SyncHealthCardView, { health, nowMs: NOW, onSyncNow: () => undefined }),
  );

describe("the Sync health card, healthy", () => {
  const html = draw(healthy());

  it("says when the last sync ended and when the next starts", () => {
    expect(html).toContain("Synced 12 min ago");
    expect(html).toContain("Next run in 8 min");
  });

  it("lists the entities with the Zoho count and ours", () => {
    expect(html).toContain("Leads");
    expect(html).toContain("Deals");
    expect(html).not.toContain("Unavailable");
    expect(html).not.toContain(UNAVAILABLE_SENTENCE);
  });

  it("names each module with its state", () => {
    expect(html).toContain("Modules in your Zoho");
    expect(html).toContain("Mirrored");
    expect(html).toContain("Not mirrored yet");
    expect(html).toContain("Not available through the API");
  });

  it("draws the credits bar, the history and Sync now", () => {
    expect(html).toContain('role="progressbar"');
    expect(html).toContain("125 of 500 credits in 24 hours");
    expect(html).toContain("Reconcile, ok, 40 credits");
    expect(html).toContain("Sync now");
    expect(html).not.toContain('role="alert"');
  });
});

describe("a difference between Zoho and ours", () => {
  it("shows a badge with the signed number", () => {
    const html = draw(healthy({ entities: [entity("leads", 40, 37)] }));
    expect(html).toContain("-3");
  });

  it("shows no badge when the counts match or one is unknown", () => {
    expect(difference(entity("leads", 5, 5))).toBeNull();
    expect(difference(entity("leads", null, 5))).toBeNull();
    expect(difference(entity("leads", 5, null))).toBeNull();
    expect(differenceLabel(2)).toBe("+2");
    expect(differenceLabel(-2)).toBe("-2");
  });
});

describe("an unavailable module", () => {
  it("shows the chip and the sentence for Tasks", () => {
    const html = draw(
      healthy({ entities: [entity("leads", 1, 1), entity("tasks", null, null, { unavailable: 1 })] }),
    );
    expect(html).toContain("Unavailable");
    expect(html).toContain("Tasks");
    expect(html).toContain("Connect Zoho again to allow this module");
  });

  it("names a key it does not know, from its words", () => {
    expect(entityLabel("converted_leads")).toBe("Converted leads");
    expect(entityLabel("some_new_key")).toBe("Some new key");
  });
});

describe("an error", () => {
  const failed = healthy({
    last_success_at: minutesAgo(300),
    next_run_at: null,
    last_run: {
      kind: "incremental",
      status: "failed",
      started_at: minutesAgo(10),
      finished_at: minutesAgo(9),
      error: "needs_reconnect",
      credits_used: 0,
    },
  });

  it("maps the code to a sentence and never shows the code", () => {
    const html = draw(failed);
    expect(html).toContain("Zoho needs you to connect again.");
    expect(html).not.toContain("needs_reconnect");
    expect(html).toContain("Synced 5 h ago");
  });

  it("gives every code a sentence, and an unknown code a general one", () => {
    for (const code of ["budget", "rate_limited", "transport", "stale", "disconnected", "internal"]) {
      expect(errorSentence(code)).toBeTruthy();
    }
    expect(errorSentence("zz_unknown")).toContain("failed");
    expect(errorSentence(null)).toBeNull();
  });
});

describe("a running sync", () => {
  const running = healthy({
    next_run_at: null,
    last_run: {
      kind: "import",
      status: "running",
      started_at: minutesAgo(1),
      finished_at: null,
      error: null,
      credits_used: null,
    },
  });

  it("says Running now and holds the Sync now button", () => {
    const html = draw(running);
    expect(html).toContain("Running now");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>/);
    expect(isRunning(running)).toBe(true);
    expect(isRunning(healthy())).toBe(false);
    expect(isRunning(null)).toBe(false);
  });
});

describe("the helpers", () => {
  it("reads the time", () => {
    expect(agoLabel(minutesAgo(0), NOW)).toBe("just now");
    expect(agoLabel(minutesAgo(90), NOW)).toBe("2 h ago");
    expect(agoLabel(null, NOW)).toBe("");
    expect(statusLines(healthy({ last_success_at: null }), NOW).synced).toBe("Never synced");
  });

  it("states a module and caps the credits bar", () => {
    expect(moduleState({ api_name: "A", label: "A", custom: false, api_supported: false, mirrored: true }))
      .toBe("Not available through the API");
    expect(creditsPercent({ budget_24h: 100, used_24h: 250 })).toBe(100);
    expect(creditsPercent({ budget_24h: 0, used_24h: 5 })).toBe(0);
  });
});

describe("where the card appears", () => {
  const page = (over: Record<string, unknown>) =>
    renderToStaticMarkup(
      createElement(PipelineSettings, {
        showAdminCards: true,
        dealStatuses: [],
        leadStatuses: [],
        lostReasons: [],
        saving: false,
        onSaveStatus: async () => true,
        onRemoveStatus: async () => true,
        onReorderStatuses: async () => undefined,
        onSaveLostReason: async () => true,
        onRemoveLostReason: async () => true,
        onReorderLostReasons: async () => undefined,
        ...over,
      }),
    );

  it("is hidden in Native mode", () => {
    expect(page({ readOnly: false })).not.toContain("Sync health");
  });

  it("is hidden from a member in Mirror mode", () => {
    expect(page({ readOnly: true, showAdminCards: false })).not.toContain("Sync health");
  });

  it("shows for an admin in Mirror mode", () => {
    expect(page({ readOnly: true })).toContain("Sync health");
  });
});
