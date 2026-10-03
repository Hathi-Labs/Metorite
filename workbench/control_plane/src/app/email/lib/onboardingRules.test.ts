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
import { useEmailStore } from "./emailStore";
import {
  REPLY_RULE_NAMES,
  REPLY_SYSTEM_TYPES,
  RULES_STEP_COPY,
  firstSyncPanels,
  firstSyncSurface,
  hasEnabledReplyRule,
  importProgress,
  installRecommendedRules,
  installedLine,
  isReplyRule,
  onboardingStage,
  processPastFrom,
  readDraftSwitch,
  rulesStepPhase,
  setDraftReplies,
  type RulesStepApi,
} from "./onboarding";
import type { AssistantSettings, EmailAccount } from "./types";

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
    // EM-T8f-3: the page draws one surface for each pending mailbox, and
    // `firstSyncPanels` picks them by `isFirstSyncPending`.
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain(
      "const importPanels = firstSyncPanels(accounts, viewAll ? null : selectedAccountId);",
    );
    expect(firstSyncPanels([{ ...running, id: "a", syncEnabled: false }], null)).toEqual([]);
    expect(firstSyncPanels([{ ...running, id: "a" }], null).map((p) => p.account.id)).toEqual(["a"]);
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
      return { id, onboardingDone: true } as EmailAccount;
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
    expect(await installRecommendedRules(api, "acc-1")).toEqual(["Needs Reply", "Newsletter"]);
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

  it("seeds the switch from the stored setting: true reads ON, false reads OFF (fix round 1, P1)", async () => {
    for (const stored of [true, false]) {
      const { api, calls } = fakeApi({ ...SETTINGS, draft_replies: stored });
      expect(await readDraftSwitch(api, "acc-1")).toEqual({ state: "ready", on: stored });
      // A read only: nothing is saved by showing the switch.
      expect(calls.map((c) => c[0])).toEqual(["getAssistantSettings"]);
    }
  });

  it("a new mailbox reads OFF (D-EM-6): no field, or not exactly true", async () => {
    const { draft_replies: _omit, ...noField } = SETTINGS as unknown as Record<string, unknown>;
    void _omit;
    const { api } = fakeApi(noField as unknown as AssistantSettings);
    expect(await readDraftSwitch(api, "acc-1")).toEqual({ state: "ready", on: false });
  });

  it("a failed read is 'failed', never a guess", async () => {
    const { api } = fakeApi(SETTINGS);
    api.getAssistantSettings = async () => {
      throw new Error("503");
    };
    expect(await readDraftSwitch(api, "acc-1")).toEqual({ state: "failed" });
  });

  it("the step reads the setting on every mount once ready, and writes only when the switch moves", () => {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).toContain('const [draft, setDraft] = useState<DraftSwitch>({ state: "loading" });');
    expect(src).toMatch(
      /useEffect\(\(\) => \{\s*if \(phase !== "ready"\) return;[\s\S]*?readDraftSwitch\(RULES_API, account\.id\)\.then\(\(d\) => live && setDraft\(d\)\);[\s\S]*?\}, \[phase, account\.id\]\);/,
    );
    // The only write path is the switch's own handler.
    expect(src.match(/setDraftReplies\(/g)).toHaveLength(1);
    expect(src).toMatch(/const changeDraft = async \(on: boolean\) => \{[\s\S]*?await setDraftReplies\(RULES_API, account\.id, on\);/);
    expect(src).toContain("onDraftChange={(on) => void changeDraft(on)}");
    expect(src).not.toMatch(/useState\(false\);\s*\/\/|draftOn, setDraftOn/);
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
    expect(src).toMatch(
      /const updated = await RULES_API\.finishOnboarding\(account\.id\);\s*setFinished\(true\);\s*onFinished\(updated\);/,
    );
    expect(src).toContain("if (finished) return null;");
  });
});

// ── The view ───────────────────────────────────────────────────────────────

const noop = () => {};

function view(over: Partial<RulesStepViewProps>): string {
  const props: RulesStepViewProps = {
    phase: "choose",
    installed: 0,
    installedNone: false,
    busy: null,
    error: null,
    replyRule: true,
    draft: { state: "ready", on: false },
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
        const out = view({ phase, pastFrom, installed: 3, error: RULES_STEP_COPY.failed, draft: { state: "ready", on: true } });
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
      /\{selectedAccount && onboardingStage\(selectedAccount\) === "rules" && \(\s*<OnboardingRulesStep\s+key=\{selectedAccount\.id\}\s+account=\{selectedAccount\}\s+onOpenAutomation=\{openFromSetup\}\s+onFinished=\{\(updated\) => \{\s*replaceAccount\(updated\);\s*void refreshAccounts\(\);\s*\}\}/,
    );
    // "<FirstSyncBanner" alone: since EM-T8f-3 a `key` comes before `address`,
    // and an indexOf of -1 would pass this check with no banner at all.
    expect(page.indexOf("<FirstSyncBanner")).toBeGreaterThan(-1);
    expect(page.indexOf("<OnboardingRulesStep")).toBeGreaterThan(page.indexOf("<FirstSyncBanner"));
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

// ── EM-T6d part 2, fix round 1 ─────────────────────────────────────────────

/** The `disabled` ATTRIBUTE. The class string also holds `disabled:opacity-50`. */
const DISABLED = /(?:^|\s)disabled=""/;

describe("the switch draws what is stored (fix round 1, P1)", () => {
  const sw = (out: string) => buttons(out).filter((b) => b.attrs.includes('role="switch"'));

  it("ON when the stored value is ON, after a remount", () => {
    const s1 = sw(view({ phase: "ready", draft: { state: "ready", on: true } }));
    expect(s1).toHaveLength(1);
    expect(s1[0].attrs).toContain('aria-checked="true"');
    expect(s1[0].attrs).not.toMatch(DISABLED);
  });

  it("disabled while the read runs", () => {
    const out = view({ phase: "ready", draft: { state: "loading" } });
    expect(sw(out)[0].attrs).toMatch(DISABLED);
    expect(sw(out)[0].attrs).toContain('aria-checked="false"');
  });

  it("disabled with a short error when the read fails", () => {
    const out = view({ phase: "ready", draft: { state: "failed" } });
    expect(sw(out)[0].attrs).toMatch(DISABLED);
    expect(out).toContain(RULES_STEP_COPY.draftReadFailed);
    expect(out).not.toContain(RULES_STEP_COPY.draftNote);
  });
});

describe("the switch needs an enabled reply rule (fix round 1, P2)", () => {
  it("matches the gateway's reply rule: the system type or the name", () => {
    expect(isReplyRule({ name: "Needs Reply", system_type: null })).toBe(true);
    expect(isReplyRule({ name: "  To Reply ", system_type: null })).toBe(true);
    expect(isReplyRule({ name: "Reply", system_type: null })).toBe(true);
    expect(isReplyRule({ name: "Anything", system_type: "to_reply" })).toBe(true);
    expect(isReplyRule({ name: "Anything", system_type: "REPLY" })).toBe(true);
    expect(isReplyRule({ name: "Newsletter", system_type: "NEWSLETTER" })).toBe(false);
    expect(isReplyRule({ name: "Needs Reply soon", system_type: null })).toBe(false);
  });

  it("needs the reply rule enabled", () => {
    expect(hasEnabledReplyRule([{ enabled: true, name: "Newsletter", system_type: null }])).toBe(false);
    expect(hasEnabledReplyRule([{ enabled: false, name: "Needs Reply", system_type: null }])).toBe(false);
    expect(hasEnabledReplyRule([{ enabled: true, name: "Needs Reply", system_type: null }])).toBe(true);
    expect(hasEnabledReplyRule(null)).toBe(false);
  });

  it("mirrors _REPLY_SYSTEM_TYPES and _REPLY_RULE_NAMES of rules.py exactly", () => {
    const py = readFileSync(
      join(__dirname, "../../../../../../apps/services/gateway/gateway/routes/email/automation/rules.py"),
      { encoding: "utf-8" },
    );
    const tuple = (name: string) => {
      const m = py.match(new RegExp(`^${name} = \\(([^)]*)\\)`, "m"));
      expect(m, name).not.toBeNull();
      return [...m![1].matchAll(/"([^"]*)"/g)].map((x) => x[1]);
    };
    expect([...REPLY_SYSTEM_TYPES]).toEqual(tuple("_REPLY_SYSTEM_TYPES"));
    expect([...REPLY_RULE_NAMES]).toEqual(tuple("_REPLY_RULE_NAMES"));
  });

  it("with no reply rule: no switch, and one line that names the Needs Reply rule", () => {
    const out = view({ phase: "ready", replyRule: false });
    expect(buttons(out).filter((b) => b.attrs.includes('role="switch"'))).toEqual([]);
    expect(out).toContain(RULES_STEP_COPY.draftNeedsReplyRule.replace(/"/g, "&quot;"));
    expect(RULES_STEP_COPY.draftNeedsReplyRule).toContain('"Needs Reply"');
  });

  it("the container decides it from the rules it read, in one helper", () => {
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).toContain("replyRule={hasEnabledReplyRule(rules)}");
    expect(src).not.toMatch(/needs reply|to_reply|system_type\s*===/i);
  });
});

describe("the Process past date is cleared after first use (fix round 1, P2)", () => {
  it("the page clears it when the Rules tab reports the dialog open", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("onProcessPastOpened={() => setProcessPastFrom(null)}");
    expect(codeOnly(read("components/automation/AutomationView.tsx"))).toMatch(
      /<AISettingsView[\s\S]*?onProcessPastOpened=\{onProcessPastOpened\}/,
    );
    expect(codeOnly(read("components/automation/AISettingsView.tsx"))).toMatch(
      /<RulesTab[\s\S]*?onProcessPastOpened=\{onProcessPastOpened\}/,
    );
  });

  it("the Rules tab reports it once, after it opened the dialog from the date", () => {
    const rules = codeOnly(read("components/automation/ai-settings/RulesTab.tsx"));
    expect(rules).toMatch(
      /const pastReported = useRef\(false\);\s*useEffect\(\(\) => \{\s*if \(!processPastFrom \|\| pastReported\.current\) return;\s*pastReported\.current = true;\s*onProcessPastOpened\?\.\(\);\s*\}, \[processPastFrom, onProcessPastOpened\]\);/,
    );
  });
});

describe("Done writes the server's account into the store (fix round 1, P3)", () => {
  it("replaceAccount swaps that one account by id", () => {
    const a = { id: "a", emailAddress: "a@x.test", onboardingDone: false } as EmailAccount;
    const b = { id: "b", emailAddress: "b@x.test", onboardingDone: false } as EmailAccount;
    useEmailStore.setState({ accounts: [a, b] });
    useEmailStore.getState().replaceAccount({ ...a, onboardingDone: true });
    const after = useEmailStore.getState().accounts;
    expect(after.map((x) => x.id)).toEqual(["a", "b"]);
    expect(after[0].onboardingDone).toBe(true);
    expect(after[1]).toBe(b);
    expect(onboardingStage({ ...IMPORTED, ...after[0] })).toBeNull();
  });

  it("the store write comes before the re-read, so a failed re-read cannot bring the step back", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toMatch(/onFinished=\{\(updated\) => \{\s*replaceAccount\(updated\);\s*void refreshAccounts\(\);/);
  });
});

describe("the recommended rules (fix round 1, P3)", () => {
  it("is disabled while it installs", () => {
    const b = buttons(view({ phase: "choose", busy: "install" })).find((x) => x.text === "Use the recommended rules");
    expect(b).toBeDefined();
    expect(b!.attrs).toMatch(DISABLED);
    expect(buttons(view({ phase: "choose" })).find((x) => x.text === "Use the recommended rules")!.attrs).not.toMatch(DISABLED);
  });

  it("when it added none and no rule is on, a line says to turn one on in AI Settings", () => {
    expect(view({ phase: "choose", installedNone: true })).toContain(RULES_STEP_COPY.noneAdded);
    expect(view({ phase: "choose", installedNone: false })).not.toContain(RULES_STEP_COPY.noneAdded);
    expect(RULES_STEP_COPY.noneAdded).toMatch(/Turn on a rule in AI Settings/);
    const src = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(src).toContain("installedNone={installTried && installed === 0}");
  });
});

describe("no sort action when nothing was imported (fix round 1, item 7)", () => {
  const since = "2026-07-05T12:00:00Z";

  it("with EM-T6b: a finished import with no imported row offers nothing", () => {
    expect(processPastFrom({ importSince: since, importPhase: "done", importCount: null }, NOW)).toBeNull();
    expect(processPastFrom({ importSince: since, importPhase: "done", importCount: 0 }, NOW)).toBeNull();
    expect(processPastFrom({ importSince: since, importPhase: "done", importCount: 120 }, NOW)).toBe("2026-07-05");
  });

  it("before EM-T6b (no phase): only the one-day rule applies", () => {
    expect(processPastFrom({ importSince: since }, NOW)).toBe("2026-07-05");
  });
});
