// ── The business rules of the CRM (WS-53 CRM-Q33) ─────────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 18.15. The owner's words,
// 2026-10-18: "Store their answers as preferences." A rule is a short
// statement of how the business reads its numbers, such as "All amounts net
// of GST". A CRM admin sets a rule for everyone, and each member sets a rule
// for themselves. The AI follows each rule in its answers.
//
// This file holds the wire types, every word of the section and each rule,
// as pure functions, so the test holds them without a browser. Say "the AI"
// on screen. `components/crmQ33.test.ts` fails if a string names the engine.

import { CrmError } from "./api";
import { whenLabel } from "./questions";

// ── The wire ───────────────────────────────────────────────────────────────

/** `org` is for everyone, `me` is for the caller only. */
export type RuleScope = "org" | "me";

/** One rule of `GET /crm/rules`. */
export type BusinessRule = {
  id: string;
  scope: RuleScope;
  slug: string;
  body: string;
  /** The caller may change and remove it. */
  editable: boolean;
  /** Who changed it last, by display name ("A member" with none), never an email. */
  by: string;
  at: string;
  origin: "settings" | "chat" | "whatsapp";
};

export type RulesBody = { rules: BusinessRule[]; can_admin: boolean };

/** One choice that the caller made in the chat (`GET /crm/questions/mine`). */
export type MyChoice = { id: string; question: string; value: string | null; label: string; at: string };

export type RulesRead = { off: true } | { off: false; body: RulesBody };

/** The keys of the one read cache. Each write wakes `crm:`. */
export const RULES_KEY = "crm:rules";
export const MY_CHOICES_KEY = "crm:rules:mine";

/** The statuses that say the rules are not for this caller. */
const OFF_STATUSES = new Set([403, 404, 409]);

/** A failed read as `off` when it says the rules are not for this caller. */
export function readRules(fetch: () => Promise<RulesBody>): Promise<RulesRead> {
  return fetch().then(
    (body) => ({ off: false as const, body }),
    (err: unknown) => {
      if (err instanceof CrmError && OFF_STATUSES.has(err.status)) return { off: true as const };
      throw err;
    },
  );
}

/** The gate of the section: `unknown` while the first read runs. */
export type RulesGate = "unknown" | "off" | "on";

export function rulesGate(read: RulesRead | undefined, failed: boolean): RulesGate {
  if (read === undefined) return failed ? "on" : "unknown";
  return read.off ? "off" : "on";
}

// ── The words ──────────────────────────────────────────────────────────────

export const RULES_TITLE = "Business rules";
export const RULES_NOTE =
  "Tell the AI how your business reads its numbers. The AI follows each rule in every answer about your CRM data.";
export const ORG_HEADING = "For everyone";
export const MINE_HEADING = "For you only";
export const ADD_HEADING = "Add a rule";
export const RULE_PLACEHOLDER = "For example: All amounts net of GST";
export const SAVE_LABEL = "Save rule";
export const CHANGE_LABEL = "Change";
export const REMOVE_LABEL = "Remove";
export const CANCEL_LABEL = "Cancel";
export const NO_ORG_RULES = "No rule for everyone yet.";
export const NO_MY_RULES = "No rule of your own yet.";
export const SAVED_LINE = "Rule saved.";
export const REMOVED_LINE = "Rule removed.";
export const RULES_UNREAD = "Could not load the business rules. Try again.";
export const CHOICES_HEADING = "Your choices in the chat";
export const CHOICES_NOTE =
  "Each one is used in your own answers until a CRM admin confirms it for everyone. Clear one to go back to the AI's choice.";
export const CLEAR_LABEL = "Clear";
export const CLEARED_LINE = "Your choice is cleared. The AI uses its own choice again.";
export const SCOPE_LABEL = "Who the rule is for";
export const SCOPE_OPTIONS: { value: RuleScope; label: string }[] = [
  { value: "me", label: "For you only" },
  { value: "org", label: "For everyone" },
];

/** The longest rule. The gateway refuses a longer one with 422. */
export const BODY_MAX = 300;

// ── The rules ──────────────────────────────────────────────────────────────

/** Why *body* cannot be saved, or `null`. */
export function ruleProblem(body: string): string | null {
  const folded = body.split(/\s+/).filter(Boolean).join(" ");
  if (folded === "") return "Write the rule first.";
  if (folded.length > BODY_MAX) return `A rule holds at most ${BODY_MAX} characters.`;
  return null;
}

/** The rules for everyone, and the caller's own, in the order of the read. */
export function splitRules(rules: BusinessRule[]): { org: BusinessRule[]; mine: BusinessRule[] } {
  return { org: rules.filter((r) => r.scope === "org"), mine: rules.filter((r) => r.scope === "me") };
}

/** "Set by Asha Rao · 3 hours ago". The name is never an email. */
export function ruleByLine(rule: BusinessRule, now: Date = new Date()): string {
  const when = whenLabel(rule.at, now);
  const from = rule.origin === "whatsapp" ? " on WhatsApp" : rule.origin === "chat" ? " in the chat" : "";
  return when ? `Set by ${rule.by}${from} · ${when}` : `Set by ${rule.by}${from}`;
}

/** "You chose IP Basic Amount · 2 hours ago". */
export function choiceLine(choice: MyChoice, now: Date = new Date()): string {
  const when = whenLabel(choice.at, now);
  return when ? `You chose ${choice.label} · ${when}` : `You chose ${choice.label}`;
}

/** The scopes that the caller may pick: an admin picks, a member saves for themselves. */
export function scopeOptions(canAdmin: boolean): { value: RuleScope; label: string }[] {
  return canAdmin ? SCOPE_OPTIONS : SCOPE_OPTIONS.filter((o) => o.value === "me");
}

/** The words of an error of a write. */
export function ruleError(err: unknown, fallback: string): string {
  return err instanceof CrmError && err.message ? err.message : fallback;
}
