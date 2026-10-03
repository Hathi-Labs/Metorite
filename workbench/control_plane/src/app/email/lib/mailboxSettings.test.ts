// WS-17 EM-T8f-2 — the settings UI of each mailbox (§11.7.6 of
// `project-docs/specs/email_app_master_plan.md`, MB-11, D-EM-24, D-EM-25,
// §11.6 cases 18 and 23).
//
// R7 fences named here:
//   * `email-settings-header-names-mailbox`: each automation header names the
//     label and the address of its mailbox. With two or more mailboxes it
//     draws a picker. The picker has no All option, and a pick calls the
//     store's `selectAccount` and clears the Process past date.
//   * `email-rules-step-copy`: the rules step offers one copy for each other
//     mailbox, and none with one mailbox. It names its own mailbox with two or
//     more. A copy runs once: a double click starts one copy, and a pair that
//     copied is not offered again. The answer names the copied, renamed and
//     left-out rules, with words for each reason that `rule_copy.py` sends.
//   * `email-disconnect-names-default`: `nextDefaultAfter` orders by
//     `createdAt`, then `id`. The test reads the ORDER BY of the re-election
//     in `transport/accounts.py` and fails when the two differ. The dialog
//     and the store read the same function.
//   * `email-notes-from-label`: the From options of Notes show
//     "label · address", and the picker starts on the default mailbox.
//
// Review fix round 1 (the verifier of 5d82548a) widened four of them:
//   * F1, in `email-settings-header-names-mailbox`: an answer for mailbox A
//     that lands after a pick of B is never shown. The view body has a key of
//     the mailbox, and each load of the three views goes through
//     `guardedLoad`.
//   * F2, in `email-notes-from-label`: a pick in the From field changes the
//     `account_id` that Send posts.
//   * F3, in `email-disconnect-names-default`: the names of the dialog hold
//     while the disconnect runs, when the list changes under it.
//   * F4, in `email-rules-step-copy`: a finished copy shows its answer, and a
//     failed re-read still moves the step on.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SelectButton } from "@/components/ui/SelectButton";
import { AutomationHeader, AutomationView } from "../components/automation/AutomationView";
import { RulesStepView, type RulesStepViewProps } from "../components/OnboardingRulesStep";
import { FollowupFromField, followupSendRequest } from "../../notes/components/FollowupEmailModal";
import { copyRules, listEmailAccounts } from "./api";
import { disconnectCopy } from "./connect";
import { useEmailStore } from "./emailStore";
import {
  COPY_STEP,
  LEFT_OUT_UNKNOWN,
  LEFT_OUT_WORDS,
  byElectionOrder,
  copyReport,
  disconnectNames,
  guardedLoad,
  holdNames,
  loadGuard,
  nextDefaultAfter,
  notesFromOptions,
  notesFromStart,
  pickSettingsMailbox,
  ruleCopier,
  rulesStepCopyChoices,
  rulesStepMailbox,
  runRuleCopy,
  settingsHeader,
  settingsPickerOptions,
  type CopyReport,
  type DefaultCandidate,
  type StepRule,
} from "./mailboxSettings";
import { rulesStepPhase } from "./onboarding";
import type { AutomationFeature, EmailAccount, RuleCopyResult } from "./types";

const ROOT = join(__dirname, "..");
const REPO = join(__dirname, "../../../../../..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const readRepo = (rel: string) => readFileSync(join(REPO, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
/** The text a member reads. The chip also holds the address in its `title`
 *  and `aria-label`, and a check of the markup would pass on those with no
 *  address on screen (mutation H1). */
const visible = (markup: string) => markup.replace(/<[^>]*>/g, "").replace(/&amp;/g, "&");

/** Each element in a tree that a hook-free component gave back. The test
 *  calls the component as a function, so nothing renders and the handlers on
 *  the elements are the real ones. */
function elements(node: ReactNode): ReactElement[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object" || !("props" in node)) return [];
  const el = node as ReactElement<{ children?: ReactNode }>;
  return [el, ...elements(el.props.children)];
}

/** A promise that the test settles by hand. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Let each queued promise callback run. */
const flush = () => new Promise((r) => setTimeout(r, 0));

const ACCOUNTS_PY = "apps/services/gateway/gateway/routes/email/transport/accounts.py";
const RULE_COPY_PY = "apps/services/gateway/gateway/routes/email/automation/rule_copy.py";

const box = (over: Partial<EmailAccount>): EmailAccount => ({
  id: "x",
  provider: "microsoft",
  emailAddress: "x@x.test",
  label: "",
  unreadCount: 0,
  syncEnabled: true,
  ...over,
});

const work = box({ id: "work", emailAddress: "vj@fracktal.in", displayLabel: "Fracktal", colorSlot: 1, isDefault: true });
const home = box({ id: "home", emailAddress: "vj@outlook.com", displayLabel: "Personal", colorSlot: 2 });
const side = box({ id: "side", emailAddress: "vj@constellation.io", displayLabel: "Constellation", colorSlot: 3 });
const all = [work, home, side];

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── email-settings-header-names-mailbox ────────────────────────────────────

describe("email-settings-header-names-mailbox", () => {
  const FEATURES: AutomationFeature[] = ["ai-settings", "digest", "unsubscribe", "analytics"];
  const header = (feature: AutomationFeature, accounts: EmailAccount[], accountId: string | null) =>
    renderToStaticMarkup(
      createElement(AutomationHeader, { feature, accounts, accountId, onClose: () => {}, onPickMailbox: () => {} }),
    );

  it("each of the four headers names the label and the address", () => {
    for (const feature of FEATURES) {
      const several = visible(header(feature, all, "home"));
      expect(several, feature).toContain("Personal");
      expect(several, feature).toContain("vj@outlook.com");
      const one = visible(header(feature, [home], "home"));
      expect(one, feature).toContain("Personal · vj@outlook.com");
    }
  });

  it("draws the picker for two or more mailboxes, and none for one", () => {
    expect(header("ai-settings", all, "home")).toContain('aria-haspopup="listbox"');
    expect(header("ai-settings", [home], "home")).not.toContain('aria-haspopup="listbox"');
    // One mailbox draws no chip (§11.0).
    expect(header("ai-settings", [home], "home")).not.toContain('aria-label="Mailbox ');
    expect(header("ai-settings", all, "home")).toContain('aria-label="Mailbox Personal, vj@outlook.com"');
  });

  it("names no mailbox before the list holds the one in view", () => {
    expect(settingsHeader(all, null)).toBeNull();
    expect(settingsHeader(all, "gone")).toBeNull();
    expect(header("digest", all, null)).not.toContain("@");
  });

  it("the picker offers each mailbox, and no All option", () => {
    const options = settingsPickerOptions(all);
    expect(options.map((o) => o.value)).toEqual(["work", "home", "side"]);
    expect(options[0]).toEqual({ value: "work", label: "Fracktal", hint: "vj@fracktal.in", keywords: "vj@fracktal.in" });
    for (const o of options) {
      expect(o.value).not.toBe("all");
      expect(o.label).not.toMatch(/all inboxes/i);
    }
    expect(settingsHeader(all, "work")?.options).toEqual(options);
    expect(settingsHeader([work], "work")?.options).toEqual([]);
    for (const feature of FEATURES) expect(header(feature, all, "home")).not.toMatch(/All inboxes/);
  });

  it("a pick clears the Process past date and calls selectAccount", () => {
    const calls: string[] = [];
    const ctx = {
      accounts: all,
      current: "home",
      selectAccount: (id: string) => calls.push(`select ${id}`),
      clearProcessPastFrom: () => calls.push("clear"),
    };
    expect(pickSettingsMailbox("work", ctx)).toBe(true);
    expect(calls).toEqual(["clear", "select work"]);
  });

  it("a pick of the mailbox in view, of All inboxes or of an unknown id does nothing", () => {
    const calls: string[] = [];
    const ctx = {
      accounts: all,
      current: "home",
      selectAccount: (id: string) => calls.push(`select ${id}`),
      clearProcessPastFrom: () => calls.push("clear"),
    };
    for (const id of ["home", "all", "gone", ""]) expect(pickSettingsMailbox(id, ctx), id).toBe(false);
    expect(calls).toEqual([]);
  });

  it("the page wires the pick to the store's selectAccount, on desktop and on mobile", () => {
    const page = codeOnly(read("page.tsx"));
    // One render of the automation views serves both layouts.
    expect(page.match(/<AutomationView\b/g)).toHaveLength(1);
    expect(page).toMatch(
      /<AutomationView[\s\S]*?accounts=\{accounts\}\s*onPickMailbox=\{\(id\) => \{\s*pickSettingsMailbox\(id, \{\s*accounts,\s*current: selectedAccountId,\s*selectAccount,\s*clearProcessPastFrom: \(\) => setProcessPastFrom\(null\),\s*\}\);\s*\}\}/,
    );
    const view = codeOnly(read("components/automation/AutomationView.tsx"));
    expect(view.match(/<AutomationHeader\b/g)).toHaveLength(1);
    expect(view).toMatch(/<SelectButton[^>]*?options=\{mailbox\.options\}[^>]*?onChange=\{onPickMailbox\}/);
    expect(view).not.toMatch(/<select\b/);
  });
});

describe("email-settings-header-names-mailbox: no answer of the mailbox before (review F1)", () => {
  it("an answer for A that lands after a pick of B is never shown", async () => {
    const guard = loadGuard();
    const shown: string[][] = [];
    const errors: string[] = [];
    let loading = true;
    const land = {
      data: (rules: string[]) => void shown.push(rules),
      error: (e: Error) => void errors.push(e.message),
      done: () => void (loading = false),
    };
    const a = deferred<string[]>();
    const b = deferred<string[]>();
    void guardedLoad(guard, () => a.promise, land);
    // The member picks B while the rules of A still load.
    void guardedLoad(guard, () => b.promise, land);
    a.resolve(["A: Newsletter"]);
    await flush();
    // Nothing of A shows, and the spinner of B still turns.
    expect(shown).toEqual([]);
    expect(loading).toBe(true);
    b.resolve(["B: Receipt"]);
    await flush();
    expect(shown).toEqual([["B: Receipt"]]);
    expect(loading).toBe(false);
    expect(errors).toEqual([]);
  });

  it("a failure of A after a pick of B shows no error of A", async () => {
    const guard = loadGuard();
    const errors: string[] = [];
    let done = 0;
    const land = { data: () => {}, error: (e: Error) => void errors.push(e.message), done: () => void done++ };
    const a = deferred<string[]>();
    void guardedLoad(guard, () => a.promise, land);
    void guardedLoad(guard, async () => ["B"], land);
    await flush();
    a.reject(new Error("A failed"));
    await flush();
    expect(errors).toEqual([]);
    expect(done).toBe(1);
    // One load alone lands as before.
    const solo = loadGuard();
    void guardedLoad(solo, async () => {
      throw new Error("503");
    }, land);
    await flush();
    expect(errors).toEqual(["503"]);
  });

  it("the view body has the key of the mailbox, so a pick remounts the views", () => {
    const bodyKey = (accountId: string | null) => {
      const tree = AutomationView({
        feature: "ai-settings",
        accountId,
        accounts: all,
        onPickMailbox: () => {},
        selectedEmailId: null,
        onClose: () => {},
      });
      const body = elements(tree).find(
        (el) => (el.props as { className?: string }).className === "flex-1 min-h-0",
      );
      expect(body, "the view body").toBeDefined();
      return body!.key;
    };
    expect(bodyKey("work")).toBe("work");
    expect(bodyKey("home")).toBe("home");
    expect(bodyKey("work")).not.toBe(bodyKey("home"));
  });

  it("each load of the three views goes through guardedLoad", () => {
    const views: Array<[string, string, RegExp]> = [
      [
        "components/automation/ai-settings/RulesTab.tsx",
        "const [rulesLoad] = useState(loadGuard);",
        /void guardedLoad\(rulesLoad, \(\) => listRules\(accountId\), \{\s*data: setRules,\s*error: \(e\) => setError\(e\.message \|\| "Failed to load rules"\),\s*done: \(\) => setLoading\(false\),/,
      ],
      [
        "components/automation/DashboardView.tsx",
        "const [digestLoad] = useState(loadGuard);",
        /void guardedLoad\(digestLoad, \(\) => getDigest\(accountId, period\), \{\s*data: setData,\s*error: \(e\) => setError\(e\.message \|\| "Failed to build dashboard"\),\s*done: \(\) => setLoading\(false\),/,
      ],
      [
        "components/automation/BulkUnsubscribeView.tsx",
        "const [sendersLoad] = useState(loadGuard);",
        /void guardedLoad\(sendersLoad, \(\) => listSenders\(accountId, undefined, SENDER_PAGE, 0, includeArchived\), \{\s*data: \(\{ senders: s, total \}\) => \{\s*setSenders\(s\);\s*setTotalSenders\(total\);\s*\},\s*error: \(e\) => setError\(e\.message \|\| "Failed to load senders"\),\s*done: \(\) => setLoading\(false\),/,
      ],
    ];
    for (const [file, guard, load] of views) {
      const src = codeOnly(read(file));
      expect(src, file).toContain(guard);
      expect(src, file).toMatch(load);
    }
    // No raw load of the first page is left.
    expect(codeOnly(read("components/automation/ai-settings/RulesTab.tsx"))).not.toMatch(/listRules\(accountId\)\s*\.then\(setRules\)/);
    expect(codeOnly(read("components/automation/DashboardView.tsx"))).not.toMatch(/getDigest\(accountId, period\)\s*\.then\(setData\)/);
  });
});

// ── email-rules-step-copy ──────────────────────────────────────────────────

const noop = () => {};

function stepView(over: Partial<RulesStepViewProps>): string {
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

const RESULT: RuleCopyResult = {
  copied: ["Newsletter", "Receipt (copy)"],
  renamed: [{ name: "Receipt", copiedAs: "Receipt (copy)" }],
  leftOut: [
    { name: "Old rule", reason: "disabled" },
    { name: "Send to home", reason: "forward_to_own_address" },
    { name: "Needs Reply", reason: "reply_rule_exists" },
  ],
};

describe("email-rules-step-copy", () => {
  it("one copy for each other mailbox, and none with one mailbox", () => {
    expect(rulesStepCopyChoices(all, "home")).toEqual([
      { id: "work", label: "Fracktal" },
      { id: "side", label: "Constellation" },
    ]);
    expect(rulesStepCopyChoices([home], "home")).toEqual([]);
  });

  it("the step names its own mailbox with two or more, and not with one", () => {
    expect(rulesStepMailbox(all, "home")).toBe(home);
    expect(rulesStepMailbox([home], "home")).toBeNull();
    const out = stepView({ mailbox: home });
    expect(out).toContain('aria-label="Mailbox Personal, vj@outlook.com"');
    expect(visible(out)).toContain("Personal");
    expect(visible(out)).toContain("vj@outlook.com");
    expect(stepView({})).not.toContain('aria-label="Mailbox Personal');
    expect(stepView({})).not.toContain("vj@outlook.com");
  });

  it("draws one copy button for each choice, beside the presets", () => {
    const choices = rulesStepCopyChoices(all, "home");
    const texts = buttons(stepView({ copyFrom: choices })).map((b) => b.text);
    expect(texts).toEqual(
      expect.arrayContaining(["Use the recommended rules", "Copy the rules of Fracktal", "Copy the rules of Constellation"]),
    );
    expect(texts.filter((t) => t.startsWith("Copy the rules of"))).toHaveLength(2);
    expect(buttons(stepView({ copyFrom: [] })).some((b) => b.text.startsWith("Copy"))).toBe(false);
    // Only beside the presets: the ready phase offers no copy.
    expect(buttons(stepView({ phase: "ready", copyFrom: choices })).some((b) => b.text.startsWith("Copy"))).toBe(false);
  });

  it("each copy button is locked while any act runs", () => {
    const choices = rulesStepCopyChoices(all, "home");
    for (const busy of ["copy", "install", "finish"] as const) {
      const copies = buttons(stepView({ copyFrom: choices, busy, copyingFrom: "work" })).filter((b) =>
        b.text.startsWith("Copy"),
      );
      expect(copies, busy).toHaveLength(2);
      for (const b of copies) expect(b.attrs, busy).toMatch(/\bdisabled=""/);
    }
  });

  it("a double click starts one copy, and a pair that copied is refused", async () => {
    let calls = 0;
    let release!: (r: RuleCopyResult) => void;
    const copier = ruleCopier(() => {
      calls += 1;
      return new Promise<RuleCopyResult>((resolve) => (release = resolve));
    });
    const first = copier.start("work", "home");
    expect(first).not.toBeNull();
    // The second click comes before the first copy answers.
    expect(copier.start("work", "home")).toBeNull();
    expect(copier.start("side", "home")).toBeNull();
    await Promise.resolve();
    release(RESULT);
    await expect(first).resolves.toEqual(RESULT);
    expect(calls).toBe(1);
    expect(copier.copied("work", "home")).toBe(true);
    // A copy is made once (D-EM-24).
    expect(copier.start("work", "home")).toBeNull();
    // Another mailbox can still copy.
    const second = copier.start("side", "home");
    expect(second).not.toBeNull();
    await Promise.resolve();
    release(RESULT);
    await second;
    expect(calls).toBe(2);
  });

  it("a failed copy does not count, so the member can try again", async () => {
    let calls = 0;
    const copier = ruleCopier(async () => {
      calls += 1;
      if (calls === 1) throw new Error("409");
      return RESULT;
    });
    await expect(copier.start("work", "home")).rejects.toThrow("409");
    expect(copier.copied("work", "home")).toBe(false);
    await expect(copier.start("work", "home")).resolves.toEqual(RESULT);
    expect(copier.start("work", "work")).toBeNull();
    expect(calls).toBe(2);
  });

  it("the answer names the copied, renamed and left-out rules in plain words", () => {
    const report = copyReport(RESULT, "Fracktal");
    expect(report.summary).toBe("Metorite copied 2 rules from Fracktal.");
    expect(report.lines).toEqual([
      "Copied: Newsletter, Receipt (copy).",
      '"Receipt" is now "Receipt (copy)", because this mailbox has a rule with that name.',
      'Not copied: "Old rule". It is off in Fracktal.',
      'Not copied: "Send to home". It forwards mail to one of your own mailboxes, and that can send mail around in a loop.',
      'Not copied: "Needs Reply". This mailbox has a reply rule already, and a mailbox keeps only one.',
    ]);
    expect(copyReport({ copied: ["A"], renamed: [], leftOut: [] }, "Fracktal").summary).toBe(
      "Metorite copied 1 rule from Fracktal.",
    );
    expect(copyReport({ copied: [], renamed: [], leftOut: [] }, "Fracktal").summary).toBe(
      "Fracktal has no rules to copy.",
    );
    expect(copyReport({ copied: [], renamed: [], leftOut: [{ name: "Z", reason: "new" }] }, "F")).toEqual({
      summary: "Metorite copied no rules from F.",
      lines: [`Not copied: "Z". ${LEFT_OUT_UNKNOWN}`],
    });
    const drawn = stepView({ phase: "ready", copyReport: report });
    expect(drawn).toMatch(/role="status"/);
    for (const line of report.lines) expect(drawn).toContain(line.replace(/"/g, "&quot;"));
  });

  it("has words for each reason that rule_copy.py sends", () => {
    const src = readRepo(RULE_COPY_PY);
    const reasons = [...src.matchAll(/^LEFT_OUT_[A-Z_]+ = "([a-z_]+)"$/gm)].map((m) => m[1]);
    expect(reasons.length).toBeGreaterThanOrEqual(3);
    expect(Object.keys(LEFT_OUT_WORDS).sort()).toEqual([...reasons].sort());
  });

  it("copyRules posts the two mailboxes and maps the answer", async () => {
    const calls: Array<{ url: string; method?: string; body?: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), method: init?.method, body: JSON.parse(String(init?.body)) });
        return new Response(
          JSON.stringify({
            copied: ["Newsletter", "Receipt (copy)"],
            renamed: [{ name: "Receipt", copied_as: "Receipt (copy)" }],
            left_out: [
              { name: "Old rule", reason: "disabled" },
              { name: "Send to home", reason: "forward_to_own_address" },
              { name: "Needs Reply", reason: "reply_rule_exists" },
            ],
          }),
          { status: 200 },
        );
      }),
    );
    expect(await copyRules("work", "home")).toEqual(RESULT);
    expect(calls).toEqual([
      { url: "/api/email/rules/copy", method: "POST", body: { from_account_id: "work", to_account_id: "home" } },
    ]);
  });

  it("the step guards each copy with the copier, and the page passes the mailboxes", () => {
    const step = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(step).toContain("const [copier] = useState(() => ruleCopier(RULES_API.copyRules));");
    expect(step.match(/copier\.start\(/g)).toHaveLength(1);
    expect(step).toMatch(/const pending = source \? copier\.start\(fromId, account\.id\) : null;\s*if \(!source \|\| !pending\) return;/);
    expect(step).toContain("onClick={() => p.onCopy?.(c.id)}");
    expect(step).toMatch(/copyFrom=\{rulesStepCopyChoices\(mailboxes, account\.id\)\.filter\(\(c\) => !copier\.copied\(c\.id, account\.id\)\)\}/);
    expect(step).toContain(`{COPY_STEP.copyFrom(c.label)}`);
    expect(COPY_STEP.copyFrom("Fracktal")).toBe("Copy the rules of Fracktal");
    const page = codeOnly(read("page.tsx"));
    expect(page).toMatch(/<OnboardingRulesStep[\s\S]*?mailboxes=\{accounts\}/);
  });
});

describe("email-rules-step-copy: the end of a copy (review F4)", () => {
  const REREAD: StepRule[] = [
    { enabled: true, name: "Newsletter", system_type: null },
    { enabled: true, name: "Receipt (copy)", system_type: null },
  ];

  /** The three outputs of the step, recorded. */
  function sink() {
    const out = { reports: [] as CopyReport[], failed: 0, rules: [] as Array<ReadonlyArray<StepRule>> };
    return {
      out,
      sink: {
        report: (r: CopyReport) => void out.reports.push(r),
        failed: () => void out.failed++,
        rules: (r: ReadonlyArray<StepRule>) => void out.rules.push(r),
      },
    };
  }

  it("a finished copy shows its answer, and the re-read moves the step on", async () => {
    const { out, sink: s } = sink();
    await runRuleCopy(Promise.resolve(RESULT), "Fracktal", async () => REREAD, s);
    expect(out.reports).toEqual([copyReport(RESULT, "Fracktal")]);
    expect(out.failed).toBe(0);
    expect(out.rules).toEqual([REREAD]);
    expect(rulesStepPhase(out.rules[0])).toBe("ready");
  });

  it("a failed re-read still moves the step on, with the copied names", async () => {
    const { out, sink: s } = sink();
    await runRuleCopy(Promise.resolve(RESULT), "Fracktal", () => Promise.reject(new Error("503")), s);
    expect(out.reports).toHaveLength(1);
    expect(out.rules).toEqual([
      [
        { enabled: true, name: "Newsletter", system_type: null },
        { enabled: true, name: "Receipt (copy)", system_type: null },
      ],
    ]);
    expect(rulesStepPhase(out.rules[0])).toBe("ready");
  });

  it("a failed copy says so, shows no answer, and still reads the rules again", async () => {
    const { out, sink: s } = sink();
    await runRuleCopy(Promise.reject(new Error("409")), "Fracktal", async () => REREAD, s);
    expect(out.failed).toBe(1);
    expect(out.reports).toEqual([]);
    // A copy whose answer was lost can still have landed.
    expect(out.rules).toEqual([REREAD]);
    const none = sink();
    await runRuleCopy(Promise.reject(new Error("409")), "F", () => Promise.reject(new Error("503")), none.sink);
    expect([none.out.failed, none.out.rules]).toEqual([1, []]);
    // A copy that copied nothing keeps the rules when the re-read fails.
    const empty = sink();
    await runRuleCopy(Promise.resolve({ copied: [], renamed: [], leftOut: [] }), "F", () => Promise.reject(new Error("503")), empty.sink);
    expect(empty.out.rules).toEqual([]);
  });

  it("the step gives runRuleCopy its own setters", () => {
    const step = codeOnly(read("components/OnboardingRulesStep.tsx"));
    expect(step).toMatch(
      /await runRuleCopy\(pending, mailboxLabel\(source\), \(\) => RULES_API\.listRules\(account\.id\), \{\s*report: setCopyReport,\s*failed: \(\) => setError\(COPY_STEP\.failed\),\s*rules: setRules,\s*\}\);/,
    );
    expect(step).toContain("copyReport={copyReport}");
  });
});

// ── email-disconnect-names-default ─────────────────────────────────────────

interface OrderTerm {
  column: string;
  desc: boolean;
  nullsFirst: boolean;
}

/** The ORDER BY of the re-election in `delete_account`, as terms. */
function electionOrder(): OrderTerm[] {
  const src = readRepo(ACCOUNTS_PY);
  const start = src.indexOf("async def delete_account(");
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf("\nasync def ", start + 1);
  const body = src.slice(start, end > start ? end : undefined);
  const m = body.match(/SET is_default = true[\s\S]*?ORDER BY ([^\n]+?)\s*\n\s*LIMIT 1/);
  expect(m, "the re-election of the default in delete_account").not.toBeNull();
  return m![1].split(",").map((term) => {
    const [column, ...rest] = term.trim().split(/\s+/);
    const mods = rest.join(" ").toUpperCase();
    expect(mods, `an ORDER BY term this fence can read: ${term}`).toMatch(/^(ASC|DESC)?\s*(NULLS (FIRST|LAST))?$/);
    const desc = /\bDESC\b/.test(mods);
    // Postgres: ASC puts NULL last, DESC puts it first, unless NULLS says.
    const nullsFirst = /NULLS FIRST/.test(mods) || (desc && !/NULLS LAST/.test(mods));
    return { column, desc, nullsFirst };
  });
}

/** The column of the gateway and the field of the UI that holds it. */
const FIELD: Record<string, keyof DefaultCandidate> = { created_at: "createdAt", id: "id" };

/** What the gateway elects, by the ORDER BY it holds. */
function serverElects(accounts: DefaultCandidate[], removedId: string, order: OrderTerm[]): string | null {
  if (!accounts.find((a) => a.id === removedId)?.isDefault) return null;
  const left = accounts.filter((a) => a.id !== removedId);
  if (left.length === 0) return null;
  const cmp = (a: DefaultCandidate, b: DefaultCandidate) => {
    for (const t of order) {
      const field = FIELD[t.column];
      expect(field, `nextDefaultAfter reads no field for ${t.column}`).toBeDefined();
      const va = (a[field] as string | null | undefined) ?? null;
      const vb = (b[field] as string | null | undefined) ?? null;
      if (va === vb) continue;
      if (va === null) return t.nullsFirst ? -1 : 1;
      if (vb === null) return t.nullsFirst ? 1 : -1;
      const c = va < vb ? -1 : 1;
      return t.desc ? -c : c;
    }
    return 0;
  };
  return [...left].sort(cmp)[0].id;
}

/** Six mailboxes that tell each plausible ORDER BY apart: a tie on the
 *  time, one microsecond between two, a NULL, a higher id that is older. */
const POOL: DefaultCandidate[] = [
  { id: "0a000000-0000-4000-8000-000000000001", createdAt: "2026-10-01T09:00:00.000002+00:00" },
  { id: "ff000000-0000-4000-8000-000000000002", createdAt: "2026-10-01T09:00:00.000001+00:00" },
  { id: "11000000-0000-4000-8000-000000000003", createdAt: "2026-10-01T09:00:00.000001+00:00" },
  { id: "05000000-0000-4000-8000-000000000004", createdAt: null },
  { id: "88000000-0000-4000-8000-000000000005", createdAt: "2026-09-30T23:59:59.999999+00:00" },
  { id: "33000000-0000-4000-8000-000000000006", createdAt: "2026-10-02T00:00:00.000000+00:00" },
];

describe("email-disconnect-names-default", () => {
  it("nextDefaultAfter agrees with the ORDER BY of delete_account, for each set and each order", () => {
    const order = electionOrder();
    let checked = 0;
    for (let mask = 1; mask < 1 << POOL.length; mask++) {
      const subset = POOL.filter((_, i) => mask & (1 << i));
      if (subset.length < 2) continue;
      for (const list of [subset, [...subset].reverse()]) {
        for (const removed of list) {
          const flagged = list.map((a) => ({ ...a, isDefault: a.id === removed.id }));
          const ui = nextDefaultAfter(flagged, removed.id)?.id ?? null;
          expect(ui, `remove ${removed.id} from ${list.map((a) => a.id.slice(0, 2)).join(",")}`).toBe(
            serverElects(flagged, removed.id, order),
          );
          checked += 1;
        }
      }
    }
    expect(checked).toBeGreaterThan(300);
  });

  it("reads the order of created_at, then id, with microseconds", () => {
    expect(electionOrder()).toEqual([
      { column: "created_at", desc: false, nullsFirst: false },
      { column: "id", desc: false, nullsFirst: false },
    ]);
    // The text compare holds only while the gateway sends six digits.
    const src = readRepo(ACCOUNTS_PY);
    expect(src).toContain('value.isoformat(timespec="microseconds")');
    expect(src).toMatch(/created_at=_iso_us\(row\.created_at\)/);
    const a = { id: "b", createdAt: "2026-10-01T09:00:00.000001+00:00" };
    const b = { id: "a", createdAt: "2026-10-01T09:00:00.000002+00:00" };
    expect(byElectionOrder(a, b)).toBeLessThan(0);
    expect(byElectionOrder({ id: "a", createdAt: null }, b)).toBeGreaterThan(0);
    expect(byElectionOrder({ id: "a", createdAt: "x" }, { id: "b", createdAt: "x" })).toBeLessThan(0);
  });

  it("names nothing when the mailbox is not the default, or is the last one", () => {
    const [p, q] = POOL;
    expect(nextDefaultAfter([{ ...p, isDefault: true }, q], q.id)).toBeNull();
    expect(nextDefaultAfter([{ ...p, isDefault: true }], p.id)).toBeNull();
    expect(nextDefaultAfter([], p.id)).toBeNull();
  });

  it("the store marks the mailbox the gateway elects, not the first row (STATUS DRIFT)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "DELETE") return new Response(null, { status: 204 });
        if (init?.method === "POST") return new Response(JSON.stringify({ id: "c", is_default: true }), { status: 200 });
        return new Response("[]", { status: 200 });
      }),
    );
    // Created A, then B, then C. B was made the default and the list was read
    // again, so it reads [B*, A, C]. Then the member sets C as the default.
    const A = box({ id: "a", emailAddress: "a@x.test", createdAt: "2026-10-01T09:00:00.000001+00:00" });
    const B = box({ id: "b", emailAddress: "b@x.test", createdAt: "2026-10-01T09:00:00.000002+00:00", isDefault: true });
    const C = box({ id: "c", emailAddress: "c@x.test", createdAt: "2026-10-01T09:00:00.000003+00:00" });
    useEmailStore.setState({ accounts: [B, A, C], selectedAccountId: "b", viewAll: false });
    await useEmailStore.getState().setDefaultAccount("c");
    expect(useEmailStore.getState().accounts.map((x) => [x.id, !!x.isDefault])).toEqual([
      ["b", false],
      ["a", false],
      ["c", true],
    ]);
    // The dialog names A before the removal.
    expect(disconnectNames(C, useEmailStore.getState().accounts).nextDefault).toBe("a@x.test");
    expect(await useEmailStore.getState().deleteAccount("c")).toEqual({ ok: true });
    expect(useEmailStore.getState().accounts.filter((x) => x.isDefault).map((x) => x.id)).toEqual(["a"]);
  });

  it("the names hold while busy, when the list changes under the dialog (review F3)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "DELETE" ? new Response(null, { status: 204 }) : new Response("[]", { status: 200 }),
      ),
    );
    const A = box({ id: "a", emailAddress: "a@x.test", createdAt: "2026-10-01T09:00:00.000001+00:00" });
    const B = box({ id: "b", emailAddress: "b@x.test", createdAt: "2026-10-01T09:00:00.000002+00:00", isDefault: true });
    useEmailStore.setState({ accounts: [B, A], selectedAccountId: "a", viewAll: false });
    // The dialog opens on B and holds its names.
    const held = disconnectNames(B, useEmailStore.getState().accounts);
    expect(held.nextDefault).toBe("a@x.test");
    // The member confirms. The store drops B before the dialog closes.
    expect(await useEmailStore.getState().deleteAccount("b")).toEqual({ ok: true });
    const live = disconnectNames(B, useEmailStore.getState().accounts);
    expect(live.nextDefault).toBeNull();
    // While busy, the dialog keeps the names it held.
    expect(holdNames(held, live, true)).toBe(held);
    expect(disconnectCopy(holdNames(held, live, true).name, holdNames(held, live, true).nextDefault).body).toContain(
      "After the disconnect, a@x.test becomes the default.",
    );
    // When not busy, the live names win. Equal names give back the held object.
    expect(holdNames(held, live, false)).toBe(live);
    expect(holdNames(held, { ...held }, false)).toBe(held);
  });

  it("the store and the dialog read nextDefaultAfter, and the store reads no first row", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toMatch(/deleteAccount: async \(id\) => \{[\s\S]*?const next = nextDefaultAfter\(get\(\)\.accounts, id\);/);
    expect(store).not.toMatch(/i === 0 \? \{ \.\.\.a, isDefault: true \}/);
    const dialog = codeOnly(read("components/DisconnectDialog.tsx"));
    expect(dialog).toContain("const live = disconnectNames(account, accounts);");
    // The names hold while the act runs, so the new default does not vanish
    // when the store drops the mailbox before the dialog closes (review F3).
    expect(dialog).toContain("const [held, setHeld] = useState(live);");
    expect(dialog).toContain("const names = holdNames(held, live, busy);");
    expect(dialog).toContain("if (names !== held) setHeld(names);");
    expect(dialog).toContain("const copy = disconnectCopy(names.name, names.nextDefault);");
    expect(codeOnly(read("page.tsx"))).toMatch(/<DisconnectDialog\s+account=\{disconnecting\}\s+accounts=\{accounts\}/);
  });

  it("the dialog shows the label and the address, and the new default", () => {
    const flagged = [work, home, side];
    const names = disconnectNames(work, flagged);
    expect(names.name).toBe("Fracktal · vj@fracktal.in");
    // No createdAt here, so the id breaks the tie: "home" < "side".
    expect(names.nextDefault).toBe("Personal · vj@outlook.com");
    const copy = disconnectCopy(names.name, names.nextDefault);
    expect(copy.body).toContain("Metorite stops syncing Fracktal · vj@fracktal.in");
    expect(copy.body).toContain("This is your default mailbox. After the disconnect, Personal · vj@outlook.com becomes the default.");
    const plain = disconnectCopy(disconnectNames(home, flagged).name, disconnectNames(home, flagged).nextDefault);
    expect(plain.body).not.toMatch(/default/);
    expect(disconnectNames(null, flagged)).toEqual({ name: "this mailbox", nextDefault: null });
  });

  it("each account read maps created_at", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify([
            { id: "a", email_address: "a@x.test", created_at: "2026-10-01T09:00:00.000001+00:00" },
            { id: "b", email_address: "b@x.test", created_at: null },
            { id: "c", email_address: "c@x.test" },
          ]),
          { status: 200 },
        ),
      ),
    );
    const accounts = await listEmailAccounts();
    expect(accounts.map((a) => a.createdAt)).toEqual(["2026-10-01T09:00:00.000001+00:00", null, undefined]);
  });
});

// ── email-notes-from-label ─────────────────────────────────────────────────

describe("email-notes-from-label", () => {
  const raw = [
    { id: "work", email_address: "vj@fracktal.in", label: "Outlook", display_label: "Fracktal", is_default: false },
    { id: "home", email_address: "vj@outlook.com", label: "Outlook", display_label: "Personal", is_default: true },
    { id: "bare", email_address: "ops@fracktal.in", label: "Outlook", is_default: false },
  ];

  it("shows each option as label · address, and never the raw label", () => {
    const options = notesFromOptions(raw);
    expect(options.map((o) => o.label)).toEqual([
      "Fracktal · vj@fracktal.in",
      "Personal · vj@outlook.com",
      "ops@fracktal.in",
    ]);
    expect(options.map((o) => o.value)).toEqual(["work", "home", "bare"]);
    for (const o of options) expect(o.label).not.toMatch(/Outlook/);
  });

  it("starts on the default mailbox, else the first", () => {
    expect(notesFromStart(raw)).toBe("home");
    expect(notesFromStart(raw.map((a) => ({ ...a, is_default: false })))).toBe("work");
    expect(notesFromStart([])).toBe("");
  });

  it("the Notes modal draws the house picker over these options", () => {
    const modal = codeOnly(read("../notes/components/FollowupEmailModal.tsx"));
    expect(modal).not.toMatch(/<select\b/);
    expect(modal).toMatch(/<SelectButton[\s\S]*?value=\{accountId\}[\s\S]*?options=\{notesFromOptions\(accounts\)\}/);
    expect(modal).toContain("setAccountId(notesFromStart(accts));");
    expect(modal).not.toContain("a.label || a.email_address");
  });

  it("a pick in the From field changes the account_id that Send posts (review F2)", () => {
    let accountId = notesFromStart(raw);
    const field = FollowupFromField({ accounts: raw, accountId, onPick: (id) => (accountId = id) });
    const pickers = elements(field).filter((el) => el.type === SelectButton);
    expect(pickers).toHaveLength(1);
    const picker = pickers[0].props as { value: string; options: unknown; onChange: (id: string) => void };
    expect(picker.value).toBe("home");
    expect(picker.options).toEqual(notesFromOptions(raw));
    // The default goes out when the member picks nothing.
    const before = followupSendRequest(accountId, "ana@acme.test", "Recap", "Notes");
    expect("payload" in before && before.payload.account_id).toBe("home");
    picker.onChange("work");
    const after = followupSendRequest(accountId, "ana@acme.test, raj@acme.test", "Recap", "Notes");
    expect(after).toEqual({
      payload: { account_id: "work", to: ["ana@acme.test", "raj@acme.test"], subject: "Recap", body_text: "Notes" },
    });
    expect(followupSendRequest("", "ana@acme.test", "S", "B")).toEqual({ error: "Choose an account to send from." });
    expect(followupSendRequest("work", " , ", "S", "B")).toEqual({ error: "Add at least one recipient." });
  });

  it("the modal wires the field to its state, and Send posts that request", () => {
    const modal = codeOnly(read("../notes/components/FollowupEmailModal.tsx"));
    expect(modal).toContain("<FollowupFromField accounts={accounts} accountId={accountId} onPick={setAccountId} />");
    expect(modal).toContain("const request = followupSendRequest(accountId, to, subject, body);");
    expect(modal).toContain("await sendEmail(request.payload);");
    expect(modal.match(/sendEmail\(/g)).toHaveLength(1);
  });
});
