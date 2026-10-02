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
import {
  IMPORT_PHASE_LINES,
  IMPORT_PROGRESS_LABEL,
  IMPORT_WAITING_DETAIL,
  firstSyncSurface,
  importProgress,
  onboardingStage,
  rangeDonePercent,
  shortDate,
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
  // sends `import_phase` (EM-T6b). Before that, FirstSyncBanner stays,
  // because its text is true there and a bar held at 0% is not.

  it("with no import_since (before EM-T6a), the stage is null and the banner draws", () => {
    const old = { syncStatus: "syncing", initialSyncDone: false };
    expect(onboardingStage(old)).toBeNull();
    expect(firstSyncSurface(old)).toBe("banner");
    // A phase without a range is not a guided mailbox either.
    expect(firstSyncSurface({ ...old, importPhase: "importing" })).toBe("banner");
  });

  it("with import_since and no import_phase (EM-T6a only), the stage is importing and the banner draws", () => {
    const t6aOnly = { importSince: daysAgo(30), onboardingDone: false, initialSyncDone: false };
    expect(onboardingStage(t6aOnly)).toBe("importing");
    expect(firstSyncSurface(t6aOnly)).toBe("banner");
    expect(firstSyncSurface({ ...t6aOnly, importPhase: null })).toBe("banner");
    expect(firstSyncSurface({ ...t6aOnly, importPhase: "" })).toBe("banner");
  });

  it("with import_phase (EM-T6b), the progress panel draws", () => {
    for (const importPhase of ["counting", "importing"]) {
      expect(firstSyncSurface({ ...IMPORTING, importPhase }), importPhase).toBe("progress");
    }
  });

  it("an error, a closed setup or a finished import never draws the panel", () => {
    const withPhase = { ...IMPORTING, importPhase: "importing" };
    expect(firstSyncSurface({ ...withPhase, syncStatus: "error" })).toBe("banner");
    expect(firstSyncSurface({ ...withPhase, onboardingDone: true })).toBe("banner");
    expect(firstSyncSurface({ ...withPhase, initialSyncDone: true })).toBe("banner");
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

describe("the page draws the panel where FirstSyncBanner drew (items 7 and 12)", () => {
  const PAGE = codeOnly(read("page.tsx"));

  it("draws the panel only for progress, and the banner for each other pending mailbox", () => {
    expect(PAGE).toMatch(
      /\{pendingAccount &&\s*\(firstSyncSurface\(pendingAccount\) === "progress" \? \(\s*<OnboardingPanel[\s\S]*?\) : \(\s*<FirstSyncBanner address=\{pendingAccount\.emailAddress\} \/>/,
    );
    // One decision: the page does not test the phase itself. It reads the
    // stage once, for the rules step of part 2.
    expect(PAGE).not.toMatch(/importPhase/);
    expect(PAGE.match(/onboardingStage\(/g)).toEqual(["onboardingStage("]);
    expect(PAGE).toContain('onboardingStage(selectedAccount) === "rules"');
    expect(PAGE).toContain("progress={importProgress(pendingAccount, { now: new Date() })}");
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
