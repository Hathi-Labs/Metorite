// WS-17 EM-T6d, part 1: the stage model and the import progress panel.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6d",
// items 1, 4 to 7, 12 and 13. Part 1 is narrowed (orchestrator, 2026-10-02):
// the rules step, the drafting step and "Done" (items 8 to 11) wait for
// part 2, and the storage UI waits for EM-T6e.
//
// vitest here runs in the node environment and reads `*.test.ts` only. So a
// decision is run from `onboarding.ts`, the panel is drawn with
// `createElement` and `renderToStaticMarkup`, and the page's wiring is held
// by a source scan with the comments stripped.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { OnboardingPanel } from "../components/OnboardingPanel";
import { SyncBanner } from "../components/SyncBanner";
import {
  IMPORT_PHASE_LINES,
  IMPORT_PROGRESS_LABEL,
  IMPORT_WAITING_DETAIL,
  SYNC_BANNER_LABEL,
  SYNC_BANNER_LINES,
  SYNC_BANNER_MAX_PERCENT,
  SYNC_BANNER_REGION,
  importPanelShows,
  importProgress,
  onboardingStage,
  rangeDonePercent,
  shortDate,
  syncBanners,
} from "./onboarding";
import type { EmailAccount } from "./types";

const EMAIL_APP = join(__dirname, "..");

function read(rel: string): string {
  return readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" });
}

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.(tsx?|jsx?)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

const DAY = 86_400_000;
// Noon, local time, so no time zone moves the day.
const NOW = new Date(2026, 9, 2, 12, 0, 0);
const daysAgo = (n: number) => new Date(NOW.getTime() - n * DAY).toISOString();

/** A new mailbox that EM-T6a connected with a range of 3 months. */
const IMPORTING: Partial<EmailAccount> = {
  importSince: daysAgo(90),
  onboardingDone: false,
  syncStatus: "syncing",
  initialSyncDone: false,
};

function panel(account: Partial<EmailAccount>): string {
  return renderToStaticMarkup(
    createElement(OnboardingPanel, {
      address: "ravi@contoso.test",
      progress: importProgress(account, { now: NOW, locale: "en-US" }),
    }),
  );
}

/** Every `role="progressbar"` element's attributes. */
function progressbars(markup: string): Record<string, string>[] {
  return [...markup.matchAll(/<div\b([^>]*role="progressbar"[^>]*)>/g)].map((m) => {
    const attrs: Record<string, string> = {};
    for (const a of m[1].matchAll(/([\w-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
    return attrs;
  });
}

describe("onboardingStage (item 1)", () => {
  it("is importing while the first import runs", () => {
    expect(onboardingStage(IMPORTING)).toBe("importing");
    expect(onboardingStage({ ...IMPORTING, syncStatus: "idle" })).toBe("importing");
    expect(onboardingStage({ ...IMPORTING, onboardingDone: undefined })).toBe("importing");
  });

  it("is null for a mailbox with no import_since, null or absent", () => {
    expect(onboardingStage({ ...IMPORTING, importSince: null })).toBeNull();
    expect(onboardingStage({ ...IMPORTING, importSince: undefined })).toBeNull();
  });

  it("is null when the member closed the setup", () => {
    expect(onboardingStage({ ...IMPORTING, onboardingDone: true })).toBeNull();
  });

  it("is null while sync_status is error, because the reconnect banner owns it", () => {
    expect(onboardingStage({ ...IMPORTING, syncStatus: "error" })).toBeNull();
  });

  it("is rules after the import (part 2), and null with no flag", () => {
    expect(onboardingStage({ ...IMPORTING, initialSyncDone: true })).toBe("rules");
    // Only an explicit false is a running import, and only an explicit true an ended one.
    expect(onboardingStage({ ...IMPORTING, initialSyncDone: undefined })).toBeNull();
  });
});

describe("email-storage-stage: the storage stage of EM-T6e (A7, D6)", () => {
  const MB = 1_048_576;
  // EM-T6c writes `initial_sync_done = true` with the phase `limit`.
  const FULL: Partial<EmailAccount> = {
    ...IMPORTING,
    syncStatus: "idle",
    initialSyncDone: true,
    importPhase: "limit",
    storedBytes: 512 * MB,
    storageLimitBytes: 500 * MB,
  };

  it.each([
    ["the phase limit at the limit", FULL, {}, "storage"],
    ["the meter exactly at the limit", { ...FULL, storedBytes: 500 * MB }, {}, "storage"],
    ["the phase limit under the limit (the gap of D2)", { ...FULL, storedBytes: 499 * MB }, {}, "rules"],
    ["a removal that went under the limit and ended the phase", { ...FULL, storedBytes: 120 * MB, importPhase: "done" }, {}, "rules"],
    ["'Keep it as it is'", FULL, { storageKept: true }, "rules"],
    ["the meter at the limit in the phase done", { ...FULL, importPhase: "done" }, {}, "rules"],
    ["a meter that has not run", { ...FULL, storedBytes: null }, {}, "rules"],
    ["a gateway with no limit", { ...FULL, storageLimitBytes: undefined }, {}, "rules"],
    ["a closed setup", { ...FULL, onboardingDone: true }, {}, null],
    ["a sync error, which the reconnect banner owns", { ...FULL, syncStatus: "error" }, {}, null],
    ["sync off", { ...FULL, syncEnabled: false }, {}, null],
    ["a mailbox from before EM-T6", { ...FULL, importSince: null }, {}, null],
  ] as const)("%s gives %s", (_name, account, opts, stage) => {
    expect(onboardingStage(account, opts)).toBe(stage);
  });

  it("never draws the import panel for a mailbox at the limit", () => {
    expect(importPanelShows(FULL)).toBe(false);
  });
});

describe("the progress with an estimate (item 4)", () => {
  const withEstimate = { ...IMPORTING, importPhase: "importing", importCount: 1240, importEstimate: 3100 };

  it("is import_count / import_estimate, with the count in the detail", () => {
    const v = importProgress(withEstimate, { now: NOW, locale: "en-US" });
    expect(v.basis).toBe("estimate");
    expect(v.percent).toBe(40);
    expect(v.detail).toBe("1,240 of about 3,100 messages");
  });

  it("draws a progressbar with aria-valuenow 40 and the detail", () => {
    const out = panel(withEstimate);
    const bars = progressbars(out);
    expect(bars).toHaveLength(1);
    expect(bars[0]["aria-valuenow"]).toBe("40");
    expect(bars[0]["aria-valuemin"]).toBe("0");
    expect(bars[0]["aria-valuemax"]).toBe("100");
    expect(bars[0]["aria-label"]).toBe(IMPORT_PROGRESS_LABEL);
    expect(out).toContain("1,240 of about 3,100 messages");
    expect(out).toContain("Connected as ravi@contoso.test");
  });

  it("stays at or under 100 when the count passes the estimate", () => {
    const v = importProgress({ ...withEstimate, importCount: 3300 }, { now: NOW, locale: "en-US" });
    expect(v.percent).toBe(100);
  });

  it("treats an estimate of 0 as no estimate", () => {
    const v = importProgress({ ...withEstimate, importEstimate: 0 }, { now: NOW, locale: "en-US" });
    expect(v.basis).toBe("range");
  });
});

describe("the progress with no estimate (items 4 and 5)", () => {
  const reached = new Date(2026, 8, 14, 12, 0, 0);
  const noEstimate = {
    ...IMPORTING,
    importPhase: "importing",
    importCount: 900,
    importEstimate: null,
    importReachedAt: reached.toISOString(),
  };

  it("is the share of the range done, with a 'back to' date", () => {
    const v = importProgress(noEstimate, { now: NOW });
    expect(v.basis).toBe("range");
    // 18 of the 90 days of the range are done.
    expect(v.percent).toBeCloseTo(20, 5);
    expect(v.detail).toBe("back to 14 Sep");
  });

  it("draws a progressbar whose value is the share of the range, and the date", () => {
    const out = panel(noEstimate);
    const bars = progressbars(out);
    expect(bars).toHaveLength(1);
    expect(bars[0]["aria-valuenow"]).toBe("20");
    expect(out).toContain("back to 14 Sep");
  });

  it("is 0 until the first batch lands", () => {
    const v = importProgress({ ...noEstimate, importReachedAt: null }, { now: NOW });
    expect(v.percent).toBe(0);
    expect(v.detail).toBe(IMPORT_WAITING_DETAIL);
    expect(rangeDonePercent({ importSince: daysAgo(30), importReachedAt: null }, NOW)).toBe(0);
  });

  it("stays between 0 and 100 for a bad or empty range", () => {
    expect(rangeDonePercent({ importSince: NOW.toISOString(), importReachedAt: daysAgo(1) }, NOW)).toBe(0);
    expect(rangeDonePercent({ importSince: daysAgo(30), importReachedAt: daysAgo(60) }, NOW)).toBe(100);
    expect(rangeDonePercent({ importSince: "not a date", importReachedAt: daysAgo(1) }, NOW)).toBe(0);
  });

  it("names the year of a date in another year", () => {
    expect(shortDate(new Date(2025, 11, 20, 12), NOW)).toBe("20 Dec 2025");
    expect(shortDate(new Date(2026, 0, 5, 12), NOW)).toBe("5 Jan");
  });
});

describe("the phase line, and never a spinner alone (item 6)", () => {
  it("reads the line of each phase", () => {
    expect(importProgress({ ...IMPORTING, importPhase: "counting" }, { now: NOW }).phaseLine).toBe(
      "Counting your mail",
    );
    expect(importProgress({ ...IMPORTING, importPhase: "importing" }, { now: NOW }).phaseLine).toBe(
      "Importing your mail, newest first",
    );
  });

  it("holds a progressbar while the phase is counting or importing", () => {
    for (const importPhase of ["counting", "importing"]) {
      const out = panel({ ...IMPORTING, importPhase });
      expect(progressbars(out)).toHaveLength(1);
      expect(out).toContain(importProgress({ ...IMPORTING, importPhase }, { now: NOW }).phaseLine);
      expect(out).not.toContain("animate-spin");
    }
  });

  it("the panel source draws no spinner", () => {
    const src = codeOnly(read("components/OnboardingPanel.tsx"));
    expect(src).not.toContain("animate-spin");
    expect(src).not.toContain("Loader2");
    expect(src).toMatch(/<ProgressBar\b/);
    expect(src).toMatch(/import ProgressBar from "@\/components\/ui\/ProgressBar"/);
  });

  it("draws on bg-card, where the empty track of the bar stays visible in light mode", () => {
    // Visual review, 2026-10-02: on the `bg-primary/5` tint the light-mode
    // track (`bg-muted`) had the same lightness as the panel and vanished.
    const out = panel({ ...IMPORTING, importPhase: "importing", importCount: 10, importEstimate: 100 });
    const section = out.match(/<section\b[^>]*class="([^"]*)"/)?.[1] ?? "";
    expect(section.split(/\s+/)).toContain("bg-card");
    expect(section).not.toMatch(/\bbg-primary\//);
    expect(section).not.toMatch(/\bbg-muted\b/);
  });

  it("only the phase line is a live region, so a poll does not speak each time", () => {
    const out = panel({ ...IMPORTING, importPhase: "importing", importCount: 10, importEstimate: 100 });
    const live = [...out.matchAll(/<[^>]*aria-live="polite"[^>]*>([^<]*)</g)].map((m) => m[1]);
    expect(live).toEqual([IMPORT_PHASE_LINES.importing]);
  });

  it("names only icons that exist, so none falls back to Zap", () => {
    const names = [...read("components/OnboardingPanel.tsx").matchAll(/name="([A-Za-z0-9]+)"/g)].map(
      (m) => m[1],
    );
    expect(names.length).toBeGreaterThan(0);
    for (const n of names) expect(isKnownIcon(n), n).toBe(true);
  });
});

describe("the degrade cases: a gateway before EM-T6a or EM-T6b (fix round 1, P2)", () => {
  // Orchestrator decision: the progress panel draws only when the gateway
  // sends `import_phase` (EM-T6b), because a bar held at 0% is not true.
  // Since EM-S9 no panel and no FirstSyncBanner draws then.

  it("with no import_since (before EM-T6a), the stage is null and no panel draws", () => {
    const old = { syncStatus: "syncing", initialSyncDone: false };
    expect(onboardingStage(old)).toBeNull();
    expect(importPanelShows(old)).toBe(false);
    // A phase without a range is not a guided mailbox either.
    expect(importPanelShows({ ...old, importPhase: "importing" })).toBe(false);
  });

  it("with import_since and no import_phase (EM-T6a only), the stage is importing and no panel draws", () => {
    const t6aOnly = { importSince: daysAgo(30), onboardingDone: false, initialSyncDone: false };
    expect(onboardingStage(t6aOnly)).toBe("importing");
    expect(importPanelShows(t6aOnly)).toBe(false);
    expect(importPanelShows({ ...t6aOnly, importPhase: null })).toBe(false);
    expect(importPanelShows({ ...t6aOnly, importPhase: "" })).toBe(false);
  });

  it("with import_phase (EM-T6b), the progress panel draws", () => {
    for (const importPhase of ["counting", "importing"]) {
      expect(importPanelShows({ ...IMPORTING, importPhase }), importPhase).toBe(true);
    }
  });

  it("an error, a closed setup or a finished import never draws the panel", () => {
    const withPhase = { ...IMPORTING, importPhase: "importing" };
    expect(importPanelShows({ ...withPhase, syncStatus: "error" })).toBe(false);
    expect(importPanelShows({ ...withPhase, onboardingDone: true })).toBe(false);
    expect(importPanelShows({ ...withPhase, initialSyncDone: true })).toBe(false);
  });

  it("a phase this UI does not know draws the plain line, with no order claimed", () => {
    const v = importProgress({ ...IMPORTING, importPhase: "something-new" }, { now: NOW });
    expect(v.phaseLine).toBe(IMPORT_PHASE_LINES.unknown);
    expect(v.phaseLine).not.toContain("newest first");
  });
});

describe("the account API carries the fields (items 4 and 7)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function listWith(row: Record<string, unknown>): Promise<EmailAccount> {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([row]), { status: 200 })),
    );
    const api = await import("./api");
    const [account] = await api.listEmailAccounts();
    return account;
  }

  const base = { id: "a1", provider: "microsoft", email_address: "ravi@contoso.test" };

  it("maps the six fields of EM-T6a and EM-T6b", async () => {
    const a = await listWith({
      ...base,
      initial_sync_done: false,
      import_since: "2026-07-04T12:00:00Z",
      onboarding_done: false,
      import_reached_at: "2026-09-14T12:00:00Z",
      import_phase: "importing",
      import_count: 1240,
      import_estimate: 3100,
    });
    expect(a.importSince).toBe("2026-07-04T12:00:00Z");
    expect(a.onboardingDone).toBe(false);
    expect(a.importReachedAt).toBe("2026-09-14T12:00:00Z");
    expect(a.importPhase).toBe("importing");
    expect(a.importCount).toBe(1240);
    expect(a.importEstimate).toBe(3100);
  });

  it("leaves each field absent when the gateway sends none", async () => {
    const a = await listWith({ ...base, initial_sync_done: false });
    for (const k of [
      "importSince",
      "onboardingDone",
      "importReachedAt",
      "importPhase",
      "importCount",
      "importEstimate",
    ] as const) {
      expect(a[k], k).toBeUndefined();
    }
    expect(onboardingStage(a)).toBeNull();
  });

  it("keeps a null as null, and drops a value of the wrong type", async () => {
    const a = await listWith({ ...base, import_since: null, import_estimate: null, import_count: "12" });
    expect(a.importSince).toBeNull();
    expect(a.importEstimate).toBeNull();
    expect(a.importCount).toBeUndefined();
  });
});

describe("the page draws the panel for the mailbox in view (items 7 and 12, EM-S9)", () => {
  const PAGE = codeOnly(read("page.tsx"));

  it("draws the panel for the mailbox in view only, and only for progress", () => {
    // EM-S9: the panel is the detail of the mailbox in view. The sync banner
    // names each other import, so no FirstSyncBanner is left.
    expect(PAGE).toContain(
      "!viewAll && selectedAccount && importPanelShows(selectedAccount) ? selectedAccount : null;",
    );
    expect(PAGE).toMatch(/\{importPanelAccount && \(\s*<OnboardingPanel\s+key=\{importPanelAccount\.id\}/);
    expect(PAGE.match(/<OnboardingPanel\b/g)).toHaveLength(1);
    expect(PAGE).not.toMatch(/FirstSyncBanner/);
    // One decision: the page does not test the phase itself. The panel comes
    // from importPanelShows, and the page reads the stage once, for the
    // storage step of EM-T6e and the rules step of part 2.
    expect(PAGE).not.toMatch(/importPhase/);
    expect(PAGE.match(/onboardingStage\(/g)).toEqual(["onboardingStage("]);
    expect(PAGE).toContain(
      "onboardingStage(selectedAccount, { storageKept: storageKept.includes(selectedAccount.id) })",
    );
    expect(PAGE).toContain('selectedAccount && setupStage === "rules"');
    expect(PAGE).toContain('selectedAccount && setupStage === "storage"');
    expect(PAGE).toContain("progress={importProgress(importPanelAccount, { now: new Date() })}");
  });

  it("sits in the mail pane after the reconnect banner, and not in a modal", () => {
    const panelAt = PAGE.indexOf("<OnboardingPanel");
    expect(panelAt).toBeGreaterThan(PAGE.indexOf("Reconnect Outlook"));
    expect(panelAt).toBeLessThan(PAGE.indexOf("<EmailToolbar />"));
    expect(codeOnly(read("components/OnboardingPanel.tsx"))).not.toMatch(/Modal|fixed inset-0/);
  });

  it("reads the progress through the first-sync poll, with no endpoint of its own", () => {
    expect(PAGE).toContain("setInterval(tick, FIRST_SYNC_POLL_MS)");
    expect(PAGE).toContain("refresh: refreshAccounts,");
    const files = [read("components/OnboardingPanel.tsx"), read("lib/onboarding.ts")].map(codeOnly);
    for (const f of files) {
      expect(f).not.toMatch(/fetch\(|EventSource|\/api\//);
    }
  });
});

describe("copy that promises a year goes (item 13)", () => {
  it("no copy in app/email says that the first sync fetches a year", () => {
    const claim = /\b(first|initial) sync\b[\s\S]{0,120}?\b(one year|a year|1 year|365 days)\b/i;
    const offenders = walk(EMAIL_APP).filter((f) => claim.test(readFileSync(f, { encoding: "utf-8" })));
    expect(offenders).toEqual([]);
  });

  it("no choice of Process past emails reaches further than 6 months", () => {
    const src = codeOnly(read("components/automation/ai-settings/RulesTab.tsx"));
    const block = src.match(/const PAST_PRESETS[^=]*=\s*\[([\s\S]*?)\];/);
    expect(block).not.toBeNull();
    const presets = [...block![1].matchAll(/label:\s*"([^"]+)",\s*days:\s*(\d+)/g)].map((m) => ({
      label: m[1],
      days: Number(m[2]),
    }));
    expect(presets.length).toBeGreaterThan(0);
    // A month is 30 days, the rule of the gateway's import window (EM-T6a).
    expect(Math.max(...presets.map((p) => p.days))).toBeLessThanOrEqual(180);
    expect(presets.map((p) => p.label)).not.toContain("Last year");
    expect(presets[presets.length - 1]).toEqual({ label: "Last 6 months", days: 180 });
  });
});

// ── EM-S9: the sync banner in the header (§14.4.6, §14.6.9, D-EM-58) ───────

describe("email-sync-banner: syncBanners", () => {
  type Box = Partial<EmailAccount> & Pick<EmailAccount, "id" | "emailAddress">;
  const box = (id: string, extra: Partial<EmailAccount> = {}): Box => ({
    id,
    emailAddress: `${id}@contoso.test`,
    displayLabel: id.toUpperCase(),
    colorSlot: 1,
    ...IMPORTING,
    importPhase: "importing",
    importCount: 1240,
    importEstimate: 3100,
    ...extra,
  });
  const rowsOf = (accounts: Box[], inView: string | null = null) =>
    syncBanners(accounts, inView, { locale: "en-US" });

  it.each([
    ["the first import, counting", { importPhase: "counting" }, "counting"],
    ["the first import, importing", { importPhase: "importing" }, "importing"],
    ["a Resync (EM-S9b)", { importPhase: "resyncing", initialSyncDone: true }, "resyncing"],
    ["a Resync of a mailbox from before EM-T6", { importPhase: "resyncing", initialSyncDone: true, importSince: null }, "resyncing"],
    ["an import that ended", { importPhase: "done", initialSyncDone: true }, null],
    ["an import that the limit stopped", { importPhase: "limit", initialSyncDone: true }, null],
    ["a stale phase after the end", { importPhase: "importing", initialSyncDone: true }, null],
    ["a first sync with no phase yet", { importPhase: null }, null],
    ["a phase this UI does not know", { importPhase: "awaiting_range" }, null],
    ["a sync error, which the reconnect banner owns", { syncStatus: "error" }, null],
    ["a Resync with a sync error", { importPhase: "resyncing", initialSyncDone: true, syncStatus: "error" }, null],
    ["sync off", { syncEnabled: false }, null],
  ] as const)("%s gives the phase %s", (_name, extra, phase) => {
    const rows = rowsOf([box("a", extra as Partial<EmailAccount>)]);
    if (phase === null) {
      expect(rows).toEqual([]);
    } else {
      expect(rows.map((r) => [r.account.id, r.phase, r.line])).toEqual([["a", phase, SYNC_BANNER_LINES[phase]]]);
    }
  });

  it("shows the percent of the estimate, with the count in the detail", () => {
    const [row] = rowsOf([box("a")]);
    expect(row.percent).toBe(40);
    expect(row.detail).toBe("1,240 of about 3,100 messages");
  });

  it("caps the percent at 99 until the phase ends", () => {
    expect(SYNC_BANNER_MAX_PERCENT).toBe(99);
    for (const importCount of [3099, 3100, 3400, 9_999_999]) {
      expect(rowsOf([box("a", { importCount })])[0].percent, String(importCount)).toBe(99);
    }
    expect(rowsOf([box("a", { importCount: 0 })])[0].percent).toBe(0);
    expect(rowsOf([box("a", { importCount: null })])[0].percent).toBe(0);
  });

  it("shows the count when there is no estimate", () => {
    for (const importEstimate of [null, undefined, 0]) {
      const [row] = rowsOf([box("a", { importEstimate })]);
      expect(row.percent).toBeNull();
      expect(row.detail).toBe("1,240 messages so far");
    }
    expect(rowsOf([box("a", { importEstimate: null, importCount: 1 })])[0].detail).toBe("1 message so far");
    expect(rowsOf([box("a", { importEstimate: null, importCount: 0 })])[0].detail).toBe("Starting");
    expect(rowsOf([box("a", { importEstimate: null, importCount: null })])[0].detail).toBe("Starting");
  });

  it("shows a mailbox out of view, in the order of the list", () => {
    const accounts = [box("a"), box("b", { initialSyncDone: true, importPhase: "done" }), box("c"), box("d")];
    // All inboxes: no mailbox in view, so each import draws a row.
    expect(rowsOf(accounts).map((r) => r.account.id)).toEqual(["a", "c", "d"]);
    // In the view of mailbox b, whose import ended, a, c and d still draw.
    expect(rowsOf(accounts, "b").map((r) => r.account.id)).toEqual(["a", "c", "d"]);
  });

  it("leaves out the mailbox in view while its import panel shows, and only then", () => {
    const accounts = [box("a"), box("c")];
    expect(importPanelShows(accounts[1])).toBe(true);
    expect(rowsOf(accounts, "c").map((r) => r.account.id)).toEqual(["a"]);
    // No range (a mailbox from before EM-T6): no panel, so the row stays.
    const old = [box("a"), box("c", { importSince: null })];
    expect(importPanelShows(old[1])).toBe(false);
    expect(rowsOf(old, "c").map((r) => r.account.id)).toEqual(["a", "c"]);
    // A Resync of the mailbox in view: no panel, so the row stays.
    const resync = [box("c", { importPhase: "resyncing", initialSyncDone: true })];
    expect(rowsOf(resync, "c").map((r) => r.account.id)).toEqual(["c"]);
  });

  it("removes the row when the phase ends", () => {
    const running = box("a");
    expect(rowsOf([running])).toHaveLength(1);
    expect(rowsOf([{ ...running, initialSyncDone: true, importPhase: "done" }])).toEqual([]);
    expect(rowsOf([{ ...running, initialSyncDone: true, importPhase: "limit" }])).toEqual([]);
  });
});

describe("email-sync-banner: the row on screen", () => {
  type Row = Pick<EmailAccount, "id" | "emailAddress"> & Partial<EmailAccount>;
  const a: Row = {
    id: "a",
    emailAddress: "vj@fracktal.in",
    displayLabel: "Fracktal",
    colorSlot: 2,
    initialSyncDone: false,
    importPhase: "importing",
    importCount: 1240,
    importEstimate: 3100,
  };
  const b: Row = { ...a, id: "b", emailAddress: "ravi@contoso.test", displayLabel: "Contoso", importEstimate: null };
  const draw = (accounts: Row[]) =>
    renderToStaticMarkup(createElement(SyncBanner, { rows: syncBanners(accounts, null, { locale: "en-US" }) }));

  it("draws one row for each mailbox, with its chip, and a percent or a count", () => {
    const out = draw([a, b]);
    expect(out.match(/data-sync-row="/g)).toHaveLength(2);
    expect(out).toContain(`aria-label="${SYNC_BANNER_REGION}"`);
    expect(out).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(out).toContain('aria-label="Mailbox Contoso, ravi@contoso.test"');
    // a has an estimate: a bar at 40%. b has none: the count, and no bar.
    const bars = progressbars(out);
    expect(bars).toHaveLength(1);
    expect(bars[0]["aria-valuenow"]).toBe("40");
    expect(bars[0]["aria-label"]).toBe(`${SYNC_BANNER_LABEL}, vj@fracktal.in`);
    expect(out).toContain("1,240 of about 3,100 messages");
    expect(out).toContain("1,240 messages so far");
  });

  it("draws the bar at 99 for a count past the estimate", () => {
    const bars = progressbars(draw([{ ...a, importCount: 5000 }]));
    expect(bars[0]["aria-valuenow"]).toBe("99");
  });

  it("only the phase line of each row is a status region, so a poll does not speak each count", () => {
    const out = draw([a, b]);
    const live = [...out.matchAll(/<[^>]*role="status"[^>]*>([^<]*)</g)].map((m) => m[1]);
    expect(live).toEqual([SYNC_BANNER_LINES.importing, SYNC_BANNER_LINES.importing]);
  });

  it("draws nothing when no import runs", () => {
    expect(draw([{ ...a, initialSyncDone: true, importPhase: "done" }])).toBe("");
  });

  it("uses the primitives and the tokens, and no colour of its own", () => {
    const src = codeOnly(read("components/SyncBanner.tsx"));
    expect(src).toMatch(/import ProgressBar from "@\/components\/ui\/ProgressBar"/);
    expect(src).toMatch(/<ProgressBar\b/);
    expect(src).toMatch(/<MailboxChip\b/);
    expect(src).not.toMatch(/<button\b|<input\b|<select\b/);
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(rgb|hsl)a?\(|text-white|bg-black/);
    // `bg-card`: the empty track of the bar stays visible in light mode.
    expect(src).toContain("bg-card");
    expect(src).not.toMatch(/\bbg-primary\//);
    expect(src).not.toMatch(/fetch\(|EventSource|\/api\//);
    const names = [...src.matchAll(/name="([A-Za-z0-9]+)"/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    for (const n of names) expect(isKnownIcon(n), n).toBe(true);
  });

  it("sits under the header on the page, and FirstSyncBanner is gone", () => {
    const PAGE = codeOnly(read("page.tsx"));
    expect(PAGE).toContain("const syncRows = syncBanners(accounts, importPanelAccount?.id ?? null);");
    expect(PAGE.match(/<SyncBanner\b/g)).toHaveLength(1);
    expect(PAGE).toContain("<SyncBanner rows={syncRows} />");
    const at = PAGE.indexOf("<SyncBanner");
    // Below the reconnect banner, which wins attention for a failed mailbox.
    const reconnect = PAGE.indexOf("{RECONNECT_LABEL[provider]}");
    expect(reconnect).toBeGreaterThan(-1);
    expect(at).toBeGreaterThan(reconnect);
    expect(at).toBeLessThan(PAGE.indexOf("<StorageNotice"));
    expect(at).toBeLessThan(PAGE.indexOf("<OnboardingPanel"));
    expect(at).toBeLessThan(PAGE.indexOf("<EmailToolbar />"));
    // The poll of the first sync stays as it was (§14.6.9).
    expect(PAGE).toContain("const firstSyncPending = shouldPollFirstSync(accounts);");
    const files = walk(EMAIL_APP).map((f) => f.replace(/\\/g, "/"));
    expect(files.some((f) => f.endsWith("components/FirstSyncBanner.tsx"))).toBe(false);
    expect(files.filter((f) => /FirstSyncBanner/.test(codeOnly(readFileSync(f, { encoding: "utf-8" }))))).toEqual([]);
  });
});
