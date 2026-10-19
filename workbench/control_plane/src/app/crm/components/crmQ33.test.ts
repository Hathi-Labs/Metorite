// Answer first, clarify over time, on screen (WS-53 CRM-Q33), spec
// crm_platform.md §18.15. The runner has no DOM, so each view is rendered to
// a string, and a wire that only the page holds is read from its source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CrmError } from "../lib/api";
import {
  RULE_SUM_LINE,
  byLine,
  choiceView,
  evidenceLine,
  fillLabel,
  suggestedSum,
  type Question,
} from "../lib/questions";
import * as rulesLib from "../lib/rules";
import {
  BODY_MAX,
  choiceLine,
  readRules,
  ruleByLine,
  ruleProblem,
  rulesGate,
  scopeOptions,
  splitRules,
  type BusinessRule,
  type MyChoice,
  type RulesRead,
} from "../lib/rules";
import { visibleSections } from "../lib/settingsSections";
import { AddRule, BusinessRulesView, MyChoices, type RuleHandlers } from "./BusinessRules";
import { QuestionCard, type QuestionHandlers } from "./OpenQuestions";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const noop = () => undefined;
const NOW = new Date("2026-10-18T12:00:00Z");
const textOf = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

// ── Fixtures ───────────────────────────────────────────────────────────────

const AMOUNT: Question = {
  id: "key:Payment_Received:amount",
  kind: "key_field",
  module: "Payment_Received",
  label: "Collection",
  part: "amount",
  reason: "unsure",
  records: 8681,
  money: true,
  why: "Totals of Collection use this field.",
  choices: [
    { value: "IP", label: "IP Basic Amount", fill: 0.52, samples: ["12400"], best: true, evidence: ["Excludes GST"] },
    { value: "Rent", label: "Rent Basic Amount", fill: 0.52, samples: [], best: false, evidence: ["Excludes GST"] },
    { value: "EB_Total_Amount", label: "EB Basic Amount", fill: 0.46, samples: [], best: false, evidence: ["Excludes GST"] },
  ],
  none: true,
  other: true,
  current: null,
  by: null,
  dismissible: true,
  options: [
    { value: "Billing_Amount", label: "Rent Net Amount", fill: 0.52, evidence: ["Includes GST", "After TDS"] },
  ],
  suggested_sum: ["IP", "Rent", "EB_Total_Amount"],
};

const RULE_ORG: BusinessRule = {
  id: "r1", scope: "org", slug: "all-amounts-net-of-gst", body: "All amounts net of GST",
  editable: false, by: "Asha Rao", at: "2026-10-18T09:00:00Z", origin: "settings",
};
const RULE_MINE: BusinessRule = {
  id: "r2", scope: "me", slug: "ip-is-our-real-earning", body: "IP is our real earning",
  editable: true, by: "A member", at: "2026-10-18T11:00:00Z", origin: "whatsapp",
};
const CHOICE: MyChoice = {
  id: "key:Payment_Received:amount", question: "Which field holds the amount of each record in Collection?",
  value: "Rent", label: "Rent Basic Amount", at: "2026-10-18T10:00:00Z",
};

const questionHandlers = (onAnswer: QuestionHandlers["onAnswer"] = noop): QuestionHandlers => ({
  busy: null, onAnswer, onDismiss: noop, onSlot: noop, onSum: noop,
});
const ruleHandlers: RuleHandlers = { busy: null, onChange: async () => true, onRemove: noop };
const ON = (rules: BusinessRule[], canAdmin = false): RulesRead => ({
  off: false, body: { rules, can_admin: canAdmin },
});

function view(read: RulesRead | undefined, choices: MyChoice[] = [], failed = false): string {
  return renderToStaticMarkup(createElement(BusinessRulesView, {
    read, failed, onRetry: noop, handlers: ruleHandlers, onAdd: async () => true,
    choices, onClear: noop, now: NOW,
  }));
}

// ── Part 5: the evidence and the sum of the rules, on the card ─────────────

describe("crm-q33 the card of an amount question", () => {
  it("shows what each field holds", () => {
    expect(choiceView(AMOUNT, AMOUNT.choices[0]).lines).toEqual([fillLabel(0.52), "Excludes GST"]);
    expect(evidenceLine(["Includes GST", "After TDS"])).toBe("Includes GST · After TDS");
    expect(evidenceLine([])).toBeNull();
    expect(evidenceLine(undefined)).toBeNull();
  });

  it("offers the sum that the business rules point at, as one answer", () => {
    expect(suggestedSum(AMOUNT)).toEqual({
      keys: ["IP", "Rent", "EB_Total_Amount"],
      label: "Use the sum: IP Basic Amount + Rent Basic Amount + EB Basic Amount",
    });
    expect(suggestedSum({ ...AMOUNT, suggested_sum: ["IP"] })).toBeNull();
    expect(suggestedSum({ ...AMOUNT, part: "date" })).toBeNull();
    const picks: unknown[] = [];
    const html = renderToStaticMarkup(createElement(QuestionCard, {
      q: AMOUNT, handlers: questionHandlers((_q, value) => picks.push(value)),
    }));
    const text = textOf(html);
    expect(text).toContain("Use the sum: IP Basic Amount + Rent Basic Amount + EB Basic Amount");
    expect(text).toContain(RULE_SUM_LINE);
    expect(text).toContain("Excludes GST");
  });

  it("shows no sum of the rules on a card without one", () => {
    const html = renderToStaticMarkup(createElement(QuestionCard, {
      q: { ...AMOUNT, suggested_sum: [] }, handlers: questionHandlers(),
    }));
    expect(textOf(html)).not.toContain("Use the sum:");
  });
});

// ── Part 4: who set an answer and when ─────────────────────────────────────

describe("crm-q33 who set an answer", () => {
  const answered: Question = { ...AMOUNT, reason: "answered", current: "IP", by: "admin" };

  it("names the admin and when, never an email", () => {
    expect(byLine({ ...answered, set_by: "Asha Rao", set_at: "2026-10-18T09:00:00Z" }, NOW)).toBe(
      "Set by Asha Rao · 3 hours ago",
    );
    expect(byLine({ ...answered, set_by: "A member", set_at: null }, NOW)).toBe("Set by A member");
    // With no record of who set it, the line of CRM-Q32 stays.
    expect(byLine(answered, NOW)).toBe("You chose");
    expect(byLine({ ...answered, by: "ai" }, NOW)).toBe("Metorite chose");
  });
});

// ── Part 3: the business rules ─────────────────────────────────────────────

describe("crm-q33 the business rules", () => {
  it("reads a 404 rules_off as off, and the gate waits for the read", async () => {
    const off = await readRules(() => Promise.reject(new CrmError("off", 404, "rules_off")));
    expect(off).toEqual({ off: true });
    await expect(readRules(() => Promise.reject(new CrmError("boom", 500)))).rejects.toThrow("boom");
    expect(rulesGate(undefined, false)).toBe("unknown");
    expect(rulesGate(undefined, true)).toBe("on");
    expect(rulesGate({ off: true }, false)).toBe("off");
    expect(rulesGate(ON([]), false)).toBe("on");
  });

  it("is a section of each member in Mirror mode, only while the gate is on", () => {
    const ids = (ctx: Parameters<typeof visibleSections>[0]) => visibleSections(ctx).map((s) => s.id);
    expect(ids({ isAdmin: false, mode: "mirror", rules: "on" })).toContain("rules");
    expect(ids({ isAdmin: true, mode: "mirror", rules: "on", questions: "on" })).toContain("rules");
    expect(ids({ isAdmin: false, mode: "mirror", rules: "off" })).not.toContain("rules");
    expect(ids({ isAdmin: false, mode: "mirror" })).not.toContain("rules");
    expect(ids({ isAdmin: false, mode: "native", rules: "on" })).not.toContain("rules");
  });

  it("checks the words before a save", () => {
    expect(ruleProblem("   ")).toBe("Write the rule first.");
    expect(ruleProblem("x".repeat(BODY_MAX + 1))).toBe(`A rule holds at most ${BODY_MAX} characters.`);
    expect(ruleProblem("All amounts net of GST")).toBeNull();
  });

  it("splits the rules, names who set each and where", () => {
    expect(splitRules([RULE_MINE, RULE_ORG])).toEqual({ org: [RULE_ORG], mine: [RULE_MINE] });
    expect(ruleByLine(RULE_ORG, NOW)).toBe("Set by Asha Rao · 3 hours ago");
    expect(ruleByLine(RULE_MINE, NOW)).toBe("Set by A member on WhatsApp · 1 hour ago");
    expect(choiceLine(CHOICE, NOW)).toBe("You chose Rent Basic Amount · 2 hours ago");
  });

  it("lets only an admin set a rule for everyone", () => {
    expect(scopeOptions(true).map((o) => o.value)).toEqual(["me", "org"]);
    expect(scopeOptions(false).map((o) => o.value)).toEqual(["me"]);
    const member = textOf(renderToStaticMarkup(createElement(AddRule, { canAdmin: false, busy: false, onAdd: async () => true })));
    const admin = textOf(renderToStaticMarkup(createElement(AddRule, { canAdmin: true, busy: false, onAdd: async () => true })));
    expect(member).not.toContain("For everyone");
    expect(admin).toContain("For you only");
  });

  it("draws the org's rules, the caller's own and the add form", () => {
    const text = textOf(view(ON([RULE_ORG, RULE_MINE])));
    expect(text).toContain("Business rules");
    expect(text).toContain("All amounts net of GST");
    expect(text).toContain("IP is our real earning");
    expect(text).toContain(rulesLib.ADD_HEADING);
    // The org rule is not the caller's to change, the own rule is.
    expect(text.match(/Change/g)?.length).toBe(1);
  });

  it("says when a list is empty, when it loads, and when it failed", () => {
    expect(textOf(view(ON([])))).toContain(rulesLib.NO_ORG_RULES);
    expect(textOf(view(ON([])))).toContain(rulesLib.NO_MY_RULES);
    expect(view(undefined)).toContain("animate-pulse");
    expect(textOf(view(undefined, [], true))).toContain(rulesLib.RULES_UNREAD);
    expect(view({ off: true })).toBe("");
  });

  it("lists the caller's own choices in the chat, with Clear", () => {
    const html = renderToStaticMarkup(createElement(MyChoices, {
      choices: [CHOICE], busy: null, onClear: noop, now: NOW,
    }));
    const text = textOf(html);
    expect(text).toContain(rulesLib.CHOICES_HEADING);
    expect(text).toContain("You chose Rent Basic Amount");
    expect(text).toContain(rulesLib.CLEAR_LABEL);
    expect(renderToStaticMarkup(createElement(MyChoices, { choices: [], busy: null, onClear: noop }))).toBe("");
  });
});

// ── The page holds the wire ────────────────────────────────────────────────

describe("crm-q33 the page wire", () => {
  it("passes the gate of the rules to the sections and the card to the settings", () => {
    const page = read("../page.tsx");
    expect(page).toContain("useRules(ctx.mode === \"mirror\")");
    expect(page).toContain("rules: rules.gate");
    expect(page).toContain("rules={rules.gate === \"on\" ? <BusinessRulesCard /> : undefined}");
    const settings = read("./PipelineSettings.tsx");
    expect(settings).toContain("{readOnly && show(\"rules\") && rules}");
  });
});

// ── Part 6: plain words, and no engine name ────────────────────────────────

describe("crm-q33 copy", () => {
  it("names no engine in any word of the rules or the card", () => {
    const strings = Object.entries(rulesLib).filter(([, v]) => typeof v === "string") as [string, string][];
    for (const [name, value] of strings) {
      expect(`${name}: ${value}`).not.toMatch(/\bJev\b/);
      expect(value).not.toMatch(/\b(tier|decide)\b/i);
    }
    const screens = [
      view(ON([RULE_ORG, RULE_MINE])),
      renderToStaticMarkup(createElement(QuestionCard, { q: AMOUNT, handlers: questionHandlers() })),
      renderToStaticMarkup(createElement(MyChoices, { choices: [CHOICE], busy: null, onClear: noop, now: NOW })),
    ];
    for (const html of screens) {
      expect(textOf(html)).not.toMatch(/\bJev\b/);
      expect(textOf(html)).not.toMatch(/\b(tier|decide)\b/i);
    }
  });
});
