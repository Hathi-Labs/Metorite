// The Sync health card (WS-53 CRM-Q9, spec section 18.3 step 4 and 18.5).
// The runner has no DOM, so each component is rendered to a string.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import * as api from "../lib/api";
import { syncFailureMessage } from "../lib/connect";
import {
  HIDDEN_SENTENCE,
  LARGE_MODULE_RECORDS,
  UNAVAILABLE_SENTENCE,
  agoLabel,
  creditsPercent,
  difference,
  differenceLabel,
  entityLabel,
  errorSentence,
  heldKeys,
  holdFailureMessage,
  holdSentence,
  holdsOf,
  isHeld,
  isHoldable,
  isRunning,
  largeSentence,
  largeUnheld,
  moduleState,
  needsYou,
  openQuestionsOf,
  questionsSentence,
  recordsOf,
  statusLines,
  unmappedCount,
  type HealthEntity,
  type SyncHealth,
  type SyncHold,
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
  running: false,
  credits: { budget_24h: 500, used_24h: 125 },
  ...over,
});

// CRM-Q17b item 8 folds the table under "Details". These tests read the
// table, so they draw it open. `crmQ17b.test.ts` holds the fold.
const draw = (health: SyncHealth) =>
  renderToStaticMarkup(
    createElement(SyncHealthCardView, { health, nowMs: NOW, onSyncNow: () => undefined, detailsOpen: true }),
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
    expect(html).toContain("Synced");
    expect(html).toContain("Not synced yet");
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

  it("says a core module is turned off in Zoho, and does not ask for a reconnect (CRM-Q15)", () => {
    const html = draw(
      healthy({
        entities: [
          entity("organizations", null, 0, { unavailable: 1, hidden: 1 }),
          entity("contacts", 2, 2),
        ],
      }),
    );
    expect(html).toContain("Unavailable");
    expect(html).toContain("Companies");
    expect(html).toContain(HIDDEN_SENTENCE);
    expect(html).not.toContain(UNAVAILABLE_SENTENCE);
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
    running: true,
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
    expect(html).toMatch(/<button[^>]*\sdisabled(=|\s|>)/);
    expect(isRunning(running)).toBe(true);
    expect(isRunning(healthy())).toBe(false);
    expect(isRunning(null)).toBe(false);
  });
});

describe("a run that crashed", () => {
  // The row still says running, but the heartbeat is old, so the gateway says running=false.
  const stale = healthy({
    running: false,
    last_run: {
      kind: "incremental",
      status: "running",
      started_at: minutesAgo(60),
      finished_at: null,
      error: null,
      credits_used: null,
    },
  });

  it("keeps Sync now enabled, does not say Running now and does not poll", () => {
    const html = draw(stale);
    expect(html).not.toContain("Running now");
    expect(html).not.toMatch(/<button[^>]*\sdisabled(=|\s|>)/);
    expect(isRunning(stale)).toBe(false);
  });
});

describe("a run that stopped on the daily budget (CRM-Q10)", () => {
  // 07:30 in the local time of the runner, so the test holds in any time zone.
  const resumes = new Date(2026, 9, 13, 7, 30).toISOString();
  const paused = (resumesAt: string | null) =>
    healthy({
      last_run: {
        kind: "reconcile",
        status: "stopped",
        started_at: minutesAgo(5),
        finished_at: minutesAgo(5),
        error: "budget",
        credits_used: 0,
      },
      credits: { budget_24h: 500, used_24h: 950, resumes_at: resumesAt },
    });

  it("says that the sync is paused, and when it resumes", () => {
    const html = draw(paused(resumes));
    expect(html).toContain("Paused: the daily Zoho API budget is spent. It resumes at 07:30.");
    expect(statusLines(paused(resumes), NOW).error).toBe(
      "Paused: the daily Zoho API budget is spent. It resumes at 07:30.",
    );
  });

  it("keeps the plain sentence when the gateway gives no time", () => {
    expect(statusLines(paused(null), NOW).error).toBe(errorSentence("budget"));
  });

  it("names the time only for a budget stop", () => {
    const other = paused(resumes);
    other.last_run = { ...other.last_run!, error: "transport" };
    expect(statusLines(other, NOW).error).toBe(errorSentence("transport"));
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

describe("a held module (CRM-Q16, spec section 18.8)", () => {
  const hold = (count: number | null, held = true): SyncHold => ({
    held,
    by: "auto",
    count,
    at: minutesAgo(30),
  });

  it("shows the Held badge, the sentence with the count, and Sync it on its own", () => {
    const html = draw(
      healthy({
        entities: [entity("contacts", 805, 805), entity("activities", 18674, 3848, { held: 1 })],
        holds: { activities: hold(18674) },
      }),
    );
    expect(html).toContain("Held");
    expect(html).toContain(
      "Activities has 18,674 records in Zoho. Sync it on its own when Metorite is quiet, so the rest stays fast",
    );
    expect(html).toContain("Sync it on its own");
    // The table reads 18,674 like the sentence, and a held row gets no difference badge.
    expect(html).toContain(">18,674<");
    expect(html).not.toContain("-14826");
    expect(html).not.toContain("-14,826");
    // The Sync switch of the held row is off, and the other row is on.
    const box = (name: string) => html.match(new RegExp(`<input[^>]*aria-label="Sync ${name}"[^>]*>`))?.[0] ?? "";
    expect(box("Activities")).toContain('type="checkbox"');
    expect(box("Activities")).not.toContain('checked=""');
    expect(box("Contacts")).toContain('checked=""');
    // A held row is not also suggested for a hold.
    expect(html).not.toContain("Hold it");
  });

  it("shows the badge and the button, and no sentence, when the count is not known", () => {
    const html = draw(healthy({ entities: [entity("tasks", null, 0)], holds: { tasks: hold(null) } }));
    expect(html).toContain("Held");
    expect(html).toContain("Sync it on its own");
    expect(html).not.toContain("records in Zoho");
  });

  it("treats a hold with held:false as on", () => {
    const health = healthy({ holds: { leads: hold(9000, false) } });
    expect(isHeld(health, "leads")).toBe(false);
    expect(heldKeys(health)).toEqual([]);
    expect(draw(health)).not.toContain("Sync it on its own when");
  });

  it("still renders a health answer with no holds (an older gateway)", () => {
    const old = healthy();
    delete old.holds;
    const html = draw(old);
    expect(html).toContain("Sync health");
    expect(html).toContain(">Sync<");
    expect(html).not.toContain("Held");
    expect(isHeld(old, "leads")).toBe(false);
    expect(holdsOf(old)).toEqual({});
    expect(heldKeys(null)).toEqual([]);
  });

  it("draws no switch for a key that cannot be held", () => {
    const html = draw(healthy({ entities: [entity("converted_leads", null, 3)] }));
    expect(html).not.toContain('aria-label="Sync Converted leads"');
    expect(isHoldable("converted_leads")).toBe(false);
    expect(isHoldable("deal_contacts")).toBe(false);
    expect(isHoldable("activities")).toBe(true);
    expect(isHoldable("module:Installs")).toBe(true);
    expect(isHoldable("module:")).toBe(false);
  });
});

describe("module keys (CRM-Q16)", () => {
  const mod = "module:Custom_Installs";
  const health = healthy({
    entities: [entity("leads", 40, 40), entity(mod, 7200, 100)],
    holds: { [mod]: { held: true, by: "admin", count: 7200, at: null } },
  });

  it("names a module key from its Zoho label, or from its API name", () => {
    expect(entityLabel(mod)).toBe("Custom Installs");
    expect(
      entityLabel(mod, [
        { api_name: "Custom_Installs", label: "Installs", custom: true, api_supported: true, mirrored: true },
      ]),
    ).toBe("Installs");
  });

  it("reads the hold and the count of a module key", () => {
    expect(isHeld(health, mod)).toBe(true);
    expect(heldKeys(health)).toEqual([mod]);
    expect(recordsOf(health, mod)).toBe(7200);
    expect(holdSentence("Custom Installs", 7200)).toBe(
      "Custom Installs has 7,200 records in Zoho. Sync it on its own when Metorite is quiet, so the rest stays fast",
    );
    const html = draw(health);
    expect(html).toContain("Custom Installs has 7,200 records in Zoho.");
    expect(html).toContain('aria-label="Sync Custom Installs"');
  });

  it("lists a held key that has no entity row", () => {
    const h = healthy({ holds: { "module:Quotes": { held: true, by: "auto", count: 6001, at: null } } });
    expect(heldKeys(h)).toEqual(["module:Quotes"]);
    expect(draw(h)).toContain("Quotes has 6,001 records in Zoho.");
  });
});

describe("a large module that is not held (CRM-Q16)", () => {
  it("suggests a hold above the limit while records remain and nobody decided", () => {
    expect(LARGE_MODULE_RECORDS).toBe(5000);
    const big = entity("contacts", 12000, 800);
    const health = healthy({ entities: [entity("leads", 40, 40), big] });
    expect(largeUnheld(health, big)).toBe(true);
    const html = draw(health);
    expect(html).toContain("Large");
    expect(html).toContain("Contacts has 12,000 records. Hold it, and sync it on its own when Metorite is quiet");
    expect(html).toContain("Hold it");

    const fresh = entity("contacts", 6000, 100);
    expect(largeUnheld(healthy({ entities: [fresh] }), fresh)).toBe(true);
    const unknown = entity("contacts", 6000, null);
    expect(largeUnheld(healthy({ entities: [unknown] }), unknown)).toBe(true);
  });

  it("does not nag about a module that is already in, nor after an admin decided (review P2)", () => {
    const done = entity("contacts", 6000, 6000);
    expect(largeUnheld(healthy({ entities: [done] }), done)).toBe(false);
    expect(draw(healthy({ entities: [done] }))).not.toContain("Hold it");

    const decided = entity("contacts", 6000, 100);
    const released = healthy({
      entities: [decided],
      holds: { contacts: { held: false, by: "admin", count: 6000, at: null } },
    });
    expect(largeUnheld(released, decided)).toBe(false);
    expect(draw(released)).not.toContain("Hold it");
  });

  it("does not suggest at or below the limit, nor for a held row", () => {
    const at = entity("contacts", 5000, 5000);
    expect(largeUnheld(healthy({ entities: [at] }), at)).toBe(false);
    const big = entity("contacts", 12000, 800);
    const held = healthy({
      entities: [big],
      holds: { contacts: { held: true, by: "auto", count: 12000, at: null } },
    });
    expect(largeUnheld(held, big)).toBe(false);
    expect(draw(held)).not.toContain("Hold it");
    expect(largeSentence("Contacts", 12000)).toBe(
      "Contacts has 12,000 records. Hold it, and sync it on its own when Metorite is quiet",
    );
  });
});

describe("the hold requests (CRM-Q16)", () => {
  const calls: [string, RequestInit | undefined][] = [];
  const original = globalThis.fetch;
  const stub = (status: number, body: unknown) => {
    calls.length = 0;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as Response;
    }) as typeof globalThis.fetch;
  };
  afterEach(() => {
    globalThis.fetch = original;
  });

  it("holds a module key with PUT, and encodes the colon", async () => {
    stub(200, { holds: {} });
    await api.setModuleSync("module:Quotes", false);
    expect(calls[0][0]).toBe("/api/crm/sync/modules/module%3AQuotes");
    expect(calls[0][1]?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ sync: false });
  });

  it("posts only for one module, and no body for Sync now", async () => {
    stub(202, { started: true });
    await api.syncZohoNow({ only: "activities" });
    expect(calls[0][0]).toBe("/api/crm/sync/zoho");
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ only: "activities" });
    await api.syncZohoNow();
    expect(calls[1][1]?.body).toBeUndefined();
  });

  it("keeps the code of a refusal, and words it", async () => {
    stub(422, { detail: "unknown_module" });
    await expect(api.setModuleSync("module:Nope", false)).rejects.toMatchObject({
      status: 422,
      code: "unknown_module",
    });
    expect(holdFailureMessage(422, "unknown_module")).toContain("no such module");
    expect(holdFailureMessage(409, "no_settings")).toContain("Set up the CRM");
    expect(holdFailureMessage(403, undefined)).toContain("admin");
    expect(syncFailureMessage(422, "unknown_module")).toContain("no such module");
  });
});

describe("values that did not map (CRM-Q19, spec section 18.9.6 item 7)", () => {
  it("shows the count of each entity row under Not mapped", () => {
    const html = draw(
      healthy({ entities: [entity("leads", 40, 40, { unmapped: 3 }), entity("deals", 10, 10)] }),
    );
    expect(html).toContain("Not mapped");
    // The last cell of each row is the count.
    const lastCell = (label: string) =>
      html.match(new RegExp(`<tr aria-label="${label}">.*?<td[^>]*>([^<]*)</td></tr>`))?.[1];
    expect(lastCell("Leads")).toBe("3");
    expect(lastCell("Deals")).toBe("0");
  });

  it("reads 0 from an older gateway, and never a value", () => {
    expect(unmappedCount(entity("leads", 1, 1))).toBe(0);
    expect(unmappedCount(entity("leads", 1, 1, { unmapped: 2 }))).toBe(2);
    expect(unmappedCount(entity("leads", 1, 1, { unmapped: -1 }))).toBe(0);
  });
});

// ── CRM-Q32-4: the open questions in "Needs you" (crm_platform.md §18.14.5) ──

describe("the open questions in Needs you (CRM-Q32-4)", () => {
  it("gives one item with the action answer while a question is open", () => {
    const items = needsYou(healthy({ open_questions: 3 }));
    expect(items).toEqual([
      { key: "@questions", kind: "questions", name: null, text: "3 open questions about your data", action: "answer" },
    ]);
    expect(questionsSentence(1)).toBe("1 open question about your data");
  });

  it("gives none at 0, outside the list (null), and from an older gateway", () => {
    expect(needsYou(healthy({ open_questions: 0 }))).toEqual([]);
    expect(needsYou(healthy({ open_questions: null }))).toEqual([]);
    expect(needsYou(healthy())).toEqual([]);
    expect(openQuestionsOf(healthy({ open_questions: -2 }))).toBe(0);
  });

  it("draws the Questions badge in primary, the text and Answer, which opens the section", () => {
    const html = draw(healthy({ open_questions: 3 }));
    expect(html).toContain('aria-label="Needs you"');
    expect(html).toMatch(/bg-primary\/10 text-primary[^"]*">Questions</);
    expect(html).toContain("3 open questions about your data");
    expect(html).toContain(">Answer</button>");
    // A link to another section waits for no run, and for no other write.
    const busy = draw(healthy({ open_questions: 3, running: true }));
    expect(busy).toMatch(/<button(?![^>]*disabled="")[^>]*>Answer<\/button>/);
  });

  it("links Answer to Settings → Open questions by default", () => {
    const src = readFileSync(new URL("./SyncHealthCard.tsx", import.meta.url), "utf8");
    expect(src).toContain('onAnswer = () => pushView(viewHref(selectSection(DEFAULT_VIEW, "questions"))),');
    expect(src).toContain('else if (item.action === "answer") onAnswer();');
  });
});
