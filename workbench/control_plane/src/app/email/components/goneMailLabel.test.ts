// WS-17 task 1987 — a SKIPPED run that keeps its rule_id means the mail was
// moved or deleted before the rule ran. It must not read as "No match found".
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { accentForHue } from "@/lib/statusAccent";
import {
  GONE_MAIL_LABEL,
  isGoneMailSkip,
  statusMeta,
} from "./automation/ai-settings/actionFormat";
import { RuleResultPill } from "./automation/ai-settings/common";
import { rowTitle } from "./MessageTimelineModal";
import type { MessageTimelineEvent } from "../lib/types";

const REASON = "The mail was moved or deleted before the rule ran.";
const neutral = accentForHue("gray").chip;

function pill(status: string, ruleId?: string, ruleName: string | null = null) {
  return renderToStaticMarkup(
    createElement(RuleResultPill, {
      matched: status !== "SKIPPED",
      ruleName,
      reason: ruleId ? REASON : null,
      status,
      ruleId,
    }),
  );
}

describe("isGoneMailSkip", () => {
  it("is true only for SKIPPED with a rule_id", () => {
    expect(isGoneMailSkip({ status: "SKIPPED", rule_id: "r1" })).toBe(true);
    expect(isGoneMailSkip({ status: "SKIPPED", rule_id: null })).toBe(false);
    expect(isGoneMailSkip({ status: "SKIPPED" })).toBe(false);
    expect(isGoneMailSkip({ status: "APPLIED", rule_id: "r1" })).toBe(false);
  });
});

describe("History pill", () => {
  it("shows the gone-mail label in a neutral colour, not red", () => {
    const html = pill("SKIPPED", "r1", "Newsletter");
    expect(html).toContain(GONE_MAIL_LABEL);
    expect(html).toContain(neutral);
    expect(html).not.toContain("No match found");
    expect(html).not.toContain("bg-red-500/15");
  });
  it("keeps 'No match found' in red for a plain SKIPPED row", () => {
    const html = pill("SKIPPED");
    expect(html).toContain("No match found");
    expect(html).toContain("bg-red-500/15 text-red-400");
    expect(html).not.toContain(GONE_MAIL_LABEL);
  });
});

describe("statusMeta (popover badge)", () => {
  it("labels a gone-mail skip neutrally", () => {
    expect(statusMeta("SKIPPED", "r1")).toEqual({
      label: GONE_MAIL_LABEL,
      cls: neutral,
    });
  });
  it("keeps 'No match' red for a plain skip", () => {
    expect(statusMeta("SKIPPED")).toEqual({
      label: "No match",
      cls: "bg-red-500/15 text-red-400",
    });
    expect(statusMeta("SKIPPED", null)?.label).toBe("No match");
  });
});

describe("timeline rowTitle", () => {
  const ev = (rule_id: string | null): MessageTimelineEvent => ({
    kind: "skipped",
    at: null,
    status: "SKIPPED",
    rule_id,
    reason: rule_id ? REASON : null,
  });
  it("names a gone-mail skip", () => {
    expect(rowTitle(ev("r1"))).toBe(GONE_MAIL_LABEL);
  });
  it("keeps the no-match title for a plain skip", () => {
    expect(rowTitle(ev(null))).toBe("Checked — no rule matched");
  });
});
