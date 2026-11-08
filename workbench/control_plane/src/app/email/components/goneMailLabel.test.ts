// WS-17 task 1987 — a SKIPPED run the server marks `mail_gone` reads "Mail moved
// or deleted". Every other SKIPPED run reads as on main, and that includes the
// suppressed run, which keeps its rule_id and has no `mail_gone`.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { accentForHue } from "@/lib/statusAccent";
import {
  GONE_MAIL_LABEL,
  currentMatchChip,
  isGoneMailSkip,
  isNoMatchRow,
  reasonPrefix,
  showActionErrors,
  statusMeta,
} from "./automation/ai-settings/actionFormat";
import { RuleResultPill } from "./automation/ai-settings/common";
import { rowTitle } from "./MessageTimelineModal";
import type { MessageTimelineEvent } from "../lib/types";

const REASON = "The mail was moved or deleted before the rule ran.";
const SUPPRESSED = "Suppressed: part of a conversation — the thread's status is the classification.";
const neutral = accentForHue("gray").chip;
const RED = "bg-red-500/15 text-red-400";
// The Fix dialog chip uses the status tokens (PR 1974), not the raw palette.
const DESTRUCTIVE = "bg-destructive/15 text-destructive";

function pill(opts: { status: string; ruleId?: string; ruleName?: string | null; gone?: boolean; reason?: string }) {
  return renderToStaticMarkup(
    createElement(RuleResultPill, {
      matched: opts.status !== "SKIPPED",
      ruleName: opts.ruleName ?? null,
      reason: opts.reason ?? null,
      status: opts.status,
      ruleId: opts.ruleId,
      mailGone: opts.gone,
    }),
  );
}

describe("isGoneMailSkip", () => {
  it("reads only the server flag", () => {
    expect(isGoneMailSkip({ mail_gone: true })).toBe(true);
    expect(isGoneMailSkip({ mail_gone: false })).toBe(false);
    expect(isGoneMailSkip({})).toBe(false);
    expect(isGoneMailSkip({ mail_gone: null })).toBe(false);
  });
});

describe("History pill", () => {
  it("shows the gone-mail label in a neutral colour, not red", () => {
    const html = pill({ status: "SKIPPED", ruleId: "r1", ruleName: "Newsletter", gone: true, reason: REASON });
    expect(html).toContain(GONE_MAIL_LABEL);
    expect(html).toContain(neutral);
    expect(html).not.toContain("No match found");
    expect(html).not.toContain("bg-red-500/15");
  });
  it("keeps 'No match found' in red for a plain SKIPPED row", () => {
    const html = pill({ status: "SKIPPED" });
    expect(html).toContain("No match found");
    expect(html).toContain(RED);
    expect(html).not.toContain(GONE_MAIL_LABEL);
  });
  it("keeps 'No match found' in red for a suppressed row (rule_id, no mail_gone)", () => {
    const html = pill({ status: "SKIPPED", ruleId: "r1", ruleName: "Newsletter", reason: SUPPRESSED });
    expect(html).toContain("No match found");
    expect(html).toContain(RED);
    expect(html).not.toContain(GONE_MAIL_LABEL);
  });
});

describe("statusMeta (popover badge)", () => {
  it("labels a gone-mail skip neutrally", () => {
    expect(statusMeta("SKIPPED", true)).toEqual({ label: GONE_MAIL_LABEL, cls: neutral });
  });
  it("keeps 'No match' red for a plain skip", () => {
    expect(statusMeta("SKIPPED")).toEqual({ label: "No match", cls: RED });
  });
  it("keeps 'No match' red for a suppressed skip", () => {
    expect(statusMeta("SKIPPED", undefined)?.label).toBe("No match");
    expect(statusMeta("SKIPPED", false)?.label).toBe("No match");
  });
  it("ignores the flag on a non-SKIPPED status", () => {
    expect(statusMeta("APPLIED", true)?.label).toBe("Applied");
  });
});

describe("popover decisions", () => {
  it("picks the reason prefix", () => {
    expect(reasonPrefix(true)).toBe("What happened:");
    expect(reasonPrefix(false)).toBe("Reason for choosing this rule:");
  });
  it("hides action errors for a gone-mail row only", () => {
    expect(showActionErrors(true, 1)).toBe(false);
    expect(showActionErrors(false, 1)).toBe(true);
    expect(showActionErrors(false, 0)).toBe(false);
  });
});

describe("Fix dialog chip", () => {
  it("is neutral for a gone-mail row", () => {
    expect(currentMatchChip(false, "Newsletter", true)).toEqual({ label: GONE_MAIL_LABEL, cls: neutral });
  });
  it("stays red 'No match found' for a suppressed row", () => {
    expect(currentMatchChip(false, "Newsletter", false)).toEqual({ label: "No match found", cls: DESTRUCTIVE });
  });
  it("shows the rule name for a match", () => {
    expect(currentMatchChip(true, "Newsletter", false).label).toBe("Newsletter");
  });
});

describe("'No match' filter", () => {
  it("includes plain and suppressed skips, excludes gone-mail and others", () => {
    expect(isNoMatchRow({ status: "SKIPPED" })).toBe(true);
    expect(isNoMatchRow({ status: "SKIPPED", mail_gone: false })).toBe(true);
    expect(isNoMatchRow({ status: "SKIPPED", mail_gone: true })).toBe(false);
    expect(isNoMatchRow({ status: "APPLIED" })).toBe(false);
  });
});

describe("timeline rowTitle", () => {
  const ev = (over: Partial<MessageTimelineEvent>): MessageTimelineEvent => ({
    kind: "skipped",
    at: null,
    status: "SKIPPED",
    ...over,
  });
  it("names a gone-mail skip", () => {
    expect(rowTitle(ev({ rule_id: "r1", mail_gone: true, reason: REASON }))).toBe(GONE_MAIL_LABEL);
  });
  it("keeps the no-match title for a plain skip", () => {
    expect(rowTitle(ev({}))).toBe("Checked — no rule matched");
  });
  it("keeps the no-match title for a suppressed skip", () => {
    expect(rowTitle(ev({ rule_id: "r1", reason: SUPPRESSED }))).toBe("Checked — no rule matched");
  });
});
