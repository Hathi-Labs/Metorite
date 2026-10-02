// WS-17 EM-T6d part 2: the rules step, the drafting step and "Done".
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6d",
// items 8 to 11, plus two follow-ups of the EM-T6b review (a paused mailbox,
// and a count over the estimate) and the owner decision (d) of #576: the
// automatic rule run touches only new mail, so the step offers "Process past
// emails" for the imported mail.
//
// vitest here runs in the node environment and reads `*.test.ts` only. The
// decisions run from `onboarding.ts` with a fake `RulesStepApi`, the view is
// drawn with `createElement`, and the container's and the page's wiring is
// held by source scans with the comments stripped.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { OnboardingPanel } from "../components/OnboardingPanel";
import { RulesStepView, type RulesStepViewProps } from "../components/OnboardingRulesStep";
import { Toggle } from "../components/automation/ui";
import { isFirstSyncPending, shouldPollFirstSync } from "./connect";
import {
  RULES_STEP_COPY,
  firstSyncSurface,
  importProgress,
  installRecommendedRules,
  installedLine,
  onboardingStage,
  processPastFrom,
  rulesStepPhase,
  setDraftReplies,
  type RulesStepApi,
} from "./onboarding";
import type { AssistantSettings } from "./types";

const EMAIL_APP = join(__dirname, "..");

function read(rel: string): string {
  return readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" });
}

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

const DAY = 86_400_000;
const NOW = new Date(2026, 9, 3, 12, 0, 0);
const daysAgo = (n: number) => new Date(NOW.getTime() - n * DAY).toISOString();

/** A mailbox whose import of 3 months ended. */
const IMPORTED = {
  importSince: daysAgo(90),
  onboardingDone: false,
  syncStatus: "idle",
  syncEnabled: true,
  initialSyncDone: true,
};

// ── The stage and the two review follow-ups ────────────────────────────────

describe("the stage after the import (item 8)", () => {
  it("is rules once the import ended, and null after Done", () => {
    expect(onboardingStage(IMPORTED)).toBe("rules");
    expect(onboardingStage({ ...IMPORTED, onboardingDone: true })).toBeNull();
    expect(onboardingStage({ ...IMPORTED, syncStatus: "error" })).toBeNull();
    expect(onboardingStage({ ...IMPORTED, importSince: null })).toBeNull();
  });
});

describe("a mailbox with sync off shows no frozen panel (EM-T6b review)", () => {
  const running = { ...IMPORTED, initialSyncDone: false, importPhase: "importing" };

  it("onboardingStage is null for sync off, in both stages", () => {
    expect(onboardingStage({ ...running, syncEnabled: false })).toBeNull();
    expect(onboardingStage({ ...IMPORTED, syncEnabled: false })).toBeNull();
    // Absent is not off.
    expect(onboardingStage({ ...running, syncEnabled: undefined })).toBe("importing");
  });

  it("isFirstSyncPending is false, so the page polls nothing and draws no banner", () => {
    expect(isFirstSyncPending({ ...running, syncEnabled: false })).toBe(false);
    expect(shouldPollFirstSync([{ ...running, syncEnabled: false }])).toBe(false);
    expect(isFirstSyncPending(running)).toBe(true);
  });

  it("firstSyncSurface never draws the progress panel for sync off", () => {
    expect(firstSyncSurface({ ...running, syncEnabled: false })).toBe("banner");
    expect(firstSyncSurface(running)).toBe("progress");
  });

  it("the page draws a pending surface only through isFirstSyncPending", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toMatch(
      /pendingAccount =\s*\(selectedAccount && isFirstSyncPending\(selectedAccount\) \? selectedAccount : null\) \?\?\s*accounts\.find\(isFirstSyncPending\)/,
    );
  });
});

describe("the bar stops at 100% when the count passes the estimate (EM-T6b review)", () => {
  it("clamps the percent and draws aria-valuenow 100", () => {
    const over = { ...IMPORTED, initialSyncDone: false, importPhase: "importing", importCount: 3400, importEstimate: 3100 };
    const view = importProgress(over, { now: NOW, locale: "en-US" });
    expect(view.percent).toBe(100);
    const out = renderToStaticMarkup(createElement(OnboardingPanel, { address: "ravi@contoso.test", progress: view }));
    expect(out).toContain('aria-valuenow="100"');
    expect(out).toContain("3,400 of about 3,100 messages");
    expect(out).not.toContain('aria-valuenow="110"');
  });
});

// ── The decisions of the rules step ─────────────────────────────────────────

describe("the phase of the rules step", () => {
  it("is checking while the read runs, choose with no enabled rule, ready with one", () => {
    expect(rulesStepPhase(null)).toBe("checking");
    expect(rulesStepPhase([])).toBe("choose");
    expect(rulesStepPhase([{ enabled: false }])).toBe("choose");
    expect(rulesStepPhase([{ enabled: false }, { enabled: true }])).toBe("ready");
  });

  it("counts what the install added", () => {
    expect(installedLine(0)).toBeNull();
    expect(installedLine(1)).toBe("Metorite added 1 rule.");
    expect(installedLine(10)).toBe("Metorite added 10 rules.");
  });
});

describe("Process past emails over the imported mail (owner decision (d))", () => {
  it("starts on the date of import_since", () => {
    expect(processPastFrom({ importSince: "2026-07-05T12:00:00Z" }, NOW)).toBe("2026-07-05");
  });

  it("offers nothing for 'Only new mail', or with no range", () => {
    expect(processPastFrom({ importSince: new Date(NOW.getTime() - 60_000).toISOString() }, NOW)).toBeNull();
    expect(processPastFrom({ importSince: null }, NOW)).toBeNull();
    expect(processPastFrom({ importSince: "x" }, NOW)).toBeNull();
  });

  it("the copy says the automatic run sorts new mail, and the imported mail needs one run", () => {
    expect(RULES_STEP_COPY.readyBody).toMatch(/each new message/);
    expect(RULES_STEP_COPY.readyBody).toMatch(/mail you imported/);
    expect(RULES_STEP_COPY.processPast).toBe("Sort my imported mail");
  });
});

/** A fake API that records each call. */
function fakeApi(settings: AssistantSettings) {
  const calls: Array<[string, unknown]> = [];
  const api: RulesStepApi = {
    listRules: async (id) => {
      calls.push(["listRules", id]);
      return [];
    },
    installPresetRules: async (id) => {
      calls.push(["installPresetRules", id]);
      return { installed: ["Needs Reply", "Newsletter"] };
    },
    getAssistantSettings: async (id) => {
      calls.push(["getAssistantSettings", id]);
      return { ...settings };
    },
    saveAssistantSettings: async (s) => {
      calls.push(["saveAssistantSettings", s]);
      return s;
    },
    finishOnboarding: async (id) => {
      calls.push(["finishOnboarding", id]);
      return null;
    },
  };
  return { api, calls };
}

const SETTINGS = {
  account_id: "acc-1",
  about: "Founder of a small design studio",
  signature: "Ravi",
  auto_run: true,
  cold_email_blocker: "OFF",
  draft_model: "tier-powerful",
  compose_model: "tier-fast",
  chat_model: "tier-powerful",
  digest_frequency: "WEEKLY",
  personal_instructions: "Keep it short.",
  writing_style: "Plain",
  draft_replies: false,
} as unknown as AssistantSettings;

describe("the rules actions (item 9)", () => {
  it("Use the recommended rules calls installPresetRules once, with the account id", async () => {
    const { api, calls } = fakeApi(SETTINGS);
    expect(await installRecommendedRules(api, "acc-1")).toBe(2);
    expect(calls).toEqual([["installPresetRules", "acc-1"]]);
  });
});

describe("the drafting switch (item 10, D-EM-6)", () => {
  it("turning it on saves draft_replies: true and changes no other field", async () => {
    const { api, calls } = fakeApi(SETTINGS);
    await setDraftReplies(api, "acc-1", true);
    expect(calls.map((c) => c[0])).toEqual(["getAssistantSettings", "saveAssistantSettings"]);
    const saved = calls[1][1] as Record<string, unknown>;
    expect(saved).toEqual({ ...SETTINGS, draft_replies: true });
    for (const k of Object.keys(SETTINGS)) {
      if (k !== "draft_replies") expect(saved[k], k).toEqual((SETTINGS as unknown as Record<string, unknown>)[k]);
    }
  });

  it("turning it off again saves draft_replies: false", async () => {
    const { api, calls } = fakeApi({ ...SETTINGS, draft_replies: true });
    await setDraftReplies(api, "acc-1", false);
    expect((calls[1][1] as AssistantSettings).draft_replies).toBe(false);
  });

  it("opens OFF, and the step writes nothing until it moves", () => {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).toContain("const [draftOn, setDraftOn] = useState(false);");
    // The only write path is the switch's own handler.
    expect(src.match(/setDraftReplies\(/g)).toHaveLength(1);
    expect(src).toMatch(/const draft = async \(on: boolean\) => \{[\s\S]*?await setDraftReplies\(RULES_API, account\.id, on\);/);
    expect(src).toContain("onDraftChange={(on) => void draft(on)}");
  });
});

describe("Done (item 11)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("updateEmailAccount sends PATCH /email/accounts/{id} with onboarding_done: true", async () => {
    const calls: Array<{ url: string; method?: string; body?: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), method: init?.method, body: JSON.parse(String(init?.body)) });
        return new Response(JSON.stringify({ id: "acc-1", onboarding_done: true }), { status: 200 });
      }),
    );
    const api = await import("./api");
    const account = await api.updateEmailAccount("acc-1", { onboardingDone: true });
    expect(calls).toEqual([{ url: "/api/email/accounts/acc-1", method: "PATCH", body: { onboarding_done: true } }]);
    expect(account.onboardingDone).toBe(true);
  });

  it("Done, Skip for now and Skip setup all send it, and the step then draws nothing", () => {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).toContain("finishOnboarding: (accountId) => updateEmailAccount(accountId, { onboardingDone: true }),");
    expect(src).toContain("onSkip={() => void finish()}");
    expect(src).toContain("onDone={() => void finish()}");
    expect(src).toMatch(/await RULES_API\.finishOnboarding\(account\.id\);\s*setFinished\(true\);\s*onFinished\(\);/);
    expect(src).toContain("if (finished) return null;");
  });
});

// ── The view ───────────────────────────────────────────────────────────────

const noop = () => {};

function view(over: Partial<RulesStepViewProps>): string {
  const props: RulesStepViewProps = {
    phase: "choose",
    installed: 0,
    busy: null,
    error: null,
    draftOn: false,
    draftBusy: false,
    pastFrom: "2026-07-05",
    onRecommended: noop,
    onChooseOwn: noop,
    onSkip: noop,
    onProcessPast: noop,
    onDraftChange: noop,
    onInsights: noop,
    onDone: noop,
    ...over,
  };
  return renderToStaticMarkup(createElement(RulesStepView, props));
}

/** The visible text of each `<button>`, and its attributes. */
function buttons(markup: string): Array<{ text: string; attrs: string }> {
  return [...markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map((m) => ({
    attrs: m[1],
    text: m[2].replace(/<[^>]*>/g, "").trim(),
  }));
}

describe("the rules step draws (items 8 to 11)", () => {
  it("choose: the title, the sort line and the three actions", () => {
    const out = view({ phase: "choose" });
    expect(out).toContain(">Set up AI rules<");
    expect(out).toContain(RULES_STEP_COPY.body);
    expect(RULES_STEP_COPY.body).toMatch(/sort/i);
    expect(RULES_STEP_COPY.body).toMatch(/mail you imported/);
    const texts = buttons(out).map((b) => b.text);
    expect(texts).toEqual(
      expect.arrayContaining(["Use the recommended rules", "Choose my own", "Skip for now"]),
    );
    expect(out).toContain('aria-label="Skip setup"');
    // The drafting switch waits for a rule (item 10).
    expect(out).not.toContain('role="switch"');
  });

  it("ready: sort the imported mail, the switch OFF, See insights and Done", () => {
    const out = view({ phase: "ready", installed: 10 });
    expect(out).toContain(">Your AI rules are on<");
    expect(out).toContain("Metorite added 10 rules.");
    expect(out).toContain(RULES_STEP_COPY.readyBody);
    const texts = buttons(out).map((b) => b.text);
    expect(texts).toEqual(expect.arrayContaining(["Sort my imported mail", "See insights", "Done"]));
    const sw = buttons(out).filter((b) => b.attrs.includes('role="switch"'));
    expect(sw).toHaveLength(1);
    expect(sw[0].attrs).toContain('aria-checked="false"');
    expect(out).toMatch(/<label[^>]*>[\s\S]*role="switch"[\s\S]*Draft replies for me<\/label>/);
  });

  it("ready with no imported mail: no sort action, and a plain line", () => {
    const out = view({ phase: "ready", pastFrom: null });
    expect(buttons(out).map((b) => b.text)).not.toContain("Sort my imported mail");
    expect(out).toContain(RULES_STEP_COPY.readyBodyNoImport);
  });

  it("checking: no action row yet, only the way out", () => {
    const out = view({ phase: "checking" });
    expect(out).toContain('aria-busy="true"');
    expect(buttons(out).map((b) => b.text)).toEqual([""]);
    expect(out).toContain('aria-label="Skip setup"');
  });

  it("names no model, in any phase or state", () => {
    for (const phase of ["checking", "choose", "ready"] as const) {
      for (const pastFrom of ["2026-07-05", null]) {
        const out = view({ phase, pastFrom, installed: 3, error: RULES_STEP_COPY.failed, draftOn: true });
        expect(out, phase).not.toMatch(/model/i);
      }
    }
    for (const text of Object.values(RULES_STEP_COPY)) expect(text).not.toMatch(/model/i);
  });

  it("the switch's OFF track is visible in light mode (visual review)", () => {
    // `bg-secondary` is about 96% light in light mode, the same as the white
    // card under it, so the OFF switch vanished. The track is now a token
    // that reads in both modes.
    const off = renderToStaticMarkup(createElement(Toggle, { enabled: false, onChange: noop }));
    const on = renderToStaticMarkup(createElement(Toggle, { enabled: true, onChange: noop }));
    expect(off).toContain("bg-muted-foreground/30");
    expect(off).not.toMatch(/\bbg-secondary\b/);
    expect(on).toMatch(/\bbg-primary\b/);
  });

  it("an error draws as an alert", () => {
    expect(view({ error: RULES_STEP_COPY.failed })).toMatch(/role="alert"[^>]*>Metorite could not save that\. Try again\.</);
  });

  it("names only icons that exist, so none falls back to Zap", () => {
    const src = read("components/OnboardingRulesStep.tsx");
    const names = [...src.matchAll(/(?:name|icon)=(?:"|\{ready \? ")([A-Za-z0-9]+)"/g)].map((m) => m[1]);
    names.push("CheckCircle2", "Sparkles", "History", "X");
    for (const n of names) expect(isKnownIcon(n), n).toBe(true);
  });
});

describe("the handlers of the view", () => {
  /** The source of each `<Button …>` element in RulesStepView, comments stripped. */
  function blocks(): string[] {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    const body = src.slice(src.indexOf("export function RulesStepView("), src.indexOf("export function OnboardingRulesStep("));
    return body.split("<Button").slice(1).map((b) => b.slice(0, b.search(/<\/Button>|\/>/)));
  }
  const find = (label: string) => blocks().filter((b) => b.includes(label));

  it("each action calls its own handler", () => {
    expect(find("COPY.recommended")[0]).toContain("onClick={p.onRecommended}");
    expect(find("COPY.chooseOwn")[0]).toContain("onClick={p.onChooseOwn}");
    expect(find("COPY.skipForNow")[0]).toContain("onClick={p.onSkip}");
    expect(find("COPY.processPast")[0]).toContain("onClick={p.onProcessPast}");
    expect(find("COPY.insights")[0]).toContain("onClick={p.onInsights}");
    expect(find("COPY.done")[0]).toContain("onClick={p.onDone}");
    expect(find("aria-label={COPY.skipSetup}")[0]).toContain("onClick={p.onSkip}");
  });

  it("the container maps each handler to the right act", () => {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).toContain('onChooseOwn={() => onOpenAutomation("ai-settings")}');
    expect(src).toContain('onInsights={() => onOpenAutomation("analytics")}');
    expect(src).toContain('onProcessPast={() => onOpenAutomation("ai-settings", pastFrom)}');
    expect(src.match(/installRecommendedRules\(RULES_API, account\.id\)/g)).toHaveLength(1);
    expect(src).toContain("onRecommended={() => void recommended()}");
  });

  it("is not a modal", () => {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).not.toMatch(/Modal|fixed inset-0/);
  });
});

// ── The page and the path to Process past emails ───────────────────────────

describe("the page wiring", () => {
  const page = codeOnly(read("page.tsx"));

  it("draws the rules step for the mailbox in view, where the import panel drew", () => {
    expect(page).toMatch(
      /\{selectedAccount && onboardingStage\(selectedAccount\) === "rules" && \(\s*<OnboardingRulesStep\s+key=\{selectedAccount\.id\}\s+account=\{selectedAccount\}\s+onOpenAutomation=\{openFromSetup\}\s+onFinished=\{\(\) => void refreshAccounts\(\)\}/,
    );
    expect(page.indexOf("<OnboardingRulesStep")).toBeGreaterThan(page.indexOf("<FirstSyncBanner address"));
    expect(page.indexOf("<OnboardingRulesStep")).toBeLessThan(page.indexOf("<EmailToolbar />"));
  });

  it("only the setup passes a Process past date, and every other way in clears it", () => {
    expect(page).toMatch(
      /const openFromSetup = useCallback\(\s*\(feature: AutomationFeature, pastFrom: string \| null = null\) => \{\s*setProcessPastFrom\(pastFrom\);\s*setAutomationFeature\(feature\);/,
    );
    expect(page).toMatch(/const handleOpenAutomation = useCallback\(\s*\(feature: AutomationFeature\) => \{\s*setProcessPastFrom\(null\);/);
    expect(page).toMatch(/onNavigate=\{\(feature\) => \{\s*setProcessPastFrom\(null\);\s*setAutomationFeature\(feature\);/);
    expect(page).toContain("processPastFrom={processPastFrom}");
  });

  it("the date reaches the Process past dialog as its start", () => {
    const view = codeOnly(read("components/automation/AutomationView.tsx"));
    expect(view).toMatch(/<AISettingsView[\s\S]*?processPastFrom=\{processPastFrom\}/);
    const settings = codeOnly(read("components/automation/AISettingsView.tsx"));
    expect(settings).toMatch(/<RulesTab[\s\S]*?processPastFrom=\{processPastFrom\}/);
    const rules = codeOnly(read("components/automation/ai-settings/RulesTab.tsx"));
    expect(rules).toContain("const [showPast, setShowPast] = useState(() => !!processPastFrom);");
    expect(rules).toMatch(/<ProcessPastEmailsDialog[\s\S]*?initialStart=\{processPastFrom\}/);
    expect(rules).toContain("const [start, setStart] = useState(initialStart ?? isoDaysAgo(7));");
  });
});
