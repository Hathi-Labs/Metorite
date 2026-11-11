/**
 * Fence for the tabs of /approvals (WS-55 EA-18c, `external_agent_access.md`
 * §12.8 "Approvals" and "One list, not two").
 */
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalTabs } from "../components/ApprovalTabs";
import {
  FULL_LIST_HREF,
  activeTab,
  questionsTabLabel,
  tabsPending,
  tabsShown,
  waitingSectionOn,
} from "./approvalTabs";

const PAGE = readFileSync(new URL("../page.tsx", import.meta.url), "utf8");
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/\\])\/\/[^\n]*/g, "");

describe("the tabs follow the questions route", () => {
  it("draws the tabs only when the route is on, and the CIV-14k section only when it is off", () => {
    expect([tabsShown(true), tabsShown(false), tabsShown(null)]).toEqual([true, false, false]);
    // Not known yet: neither draws, so the old section never flashes and goes.
    expect([waitingSectionOn(true), waitingSectionOn(false), waitingSectionOn(null)]).toEqual([
      false,
      true,
      false,
    ]);
  });

  it("treats only a 404 as off: a failed first read keeps the CIV-14k section", () => {
    // on=null with an error (500, timeout, network): the old section reads.
    expect(waitingSectionOn(null, true)).toBe(true);
    expect(tabsShown(null)).toBe(false);
    expect(tabsPending(null, true)).toBe(false);
    // on=null and no error yet: the first read is on its way. Reserve the bar.
    expect(tabsPending(null, false)).toBe(true);
    expect(waitingSectionOn(null, false)).toBe(false);
    // A later failure after a good read never brings the old section back.
    expect(waitingSectionOn(true, true)).toBe(false);
    expect(tabsPending(true, false)).toBe(false);
  });

  it("words the tab with the route's count", () => {
    expect(questionsTabLabel(3)).toBe("Agent questions · 3");
    expect(questionsTabLabel(0)).toBe("Agent questions");
    expect(questionsTabLabel(undefined)).toBe("Agent questions");
  });

  it("starts on Actions, and on the questions for a member who cannot read the queue", () => {
    expect(activeTab(null, false)).toBe("actions");
    expect(activeTab(null, true)).toBe("questions");
    expect(activeTab("actions", true)).toBe("actions");
    expect(activeTab("questions", false)).toBe("questions");
  });
});

describe("the tab bar", () => {
  const draw = (active: "actions" | "questions", count?: number) =>
    renderToStaticMarkup(createElement(ApprovalTabs, { active, questionCount: count, onPick: () => {} }));

  it("draws Actions and Agent questions with the count, and marks the active one", () => {
    const html = draw("questions", 4);
    expect(html).toContain("Actions");
    expect(html).toContain("Agent questions · 4");
    expect(html).toMatch(/aria-selected="true"[^>]*data-approval-tab="questions"/);
    expect(html).toMatch(/aria-selected="false"[^>]*data-approval-tab="actions"/);
    expect(html).toContain('role="tablist"');
  });
});

describe("the page", () => {
  const src = code(PAGE);

  it("reads the count of the shared hook, and draws the one shared list", () => {
    expect(src).toContain("useQuestionsBadge()");
    expect(src).toMatch(/<QuestionsView\s+embedded/);
    expect(src).toContain("<ApprovalTabs");
  });

  it("links to the full list in Projects with ?app=questions", () => {
    expect(FULL_LIST_HREF).toBe("/projects?app=questions");
    expect(src).toContain("href={FULL_LIST_HREF}");
  });

  it("reads the CIV-14k route only while the questions route is off", () => {
    // `loadWaiting` runs inside an effect that returns early unless the section is on.
    expect(src).toMatch(/if \(!waitingOn\) \{\s*setWaiting\(null\);\s*return;\s*\}\s*void loadWaiting\(\)/);
    expect(src).toContain("waitingSectionOn(questions.on, questions.failed)");
  });

  it("adds no second tab beside the CIV-14k section", () => {
    // Two tabs only, and the old section stays a section.
    expect(src.match(/data-approval-tab=/g) ?? []).toHaveLength(0);
    expect(src).toContain('aria-label="Waiting for you"');
    const bar = readFileSync(new URL("../components/ApprovalTabs.tsx", import.meta.url), "utf8");
    expect(bar.match(/data-approval-tab=/g)).toHaveLength(2);
  });

  it("keeps the old section and says so when the first read fails, and retries on the poll", () => {
    expect(src).toContain("waitingSectionOn(questions.on, questions.failed)");
    expect(src).toContain("QUESTIONS_READ_FAILED");
    const hook = readFileSync(new URL("../../projects/components/useQuestionsBadge.ts", import.meta.url), "utf8");
    expect(code(hook)).toMatch(/catch \{[\s\S]*setFailed\(true\)/);
    // Only a 404 (null) sets off. The poll keeps reading while `on` is not false.
    expect(code(hook)).toMatch(/if \(page === null\) \{\s*offRef\.current = true;/);
  });

  it("reserves the tab bar and decides the default tab once (no layout shift)", () => {
    expect(src).toContain("data-approval-tabs-reserved");
    expect(src).toMatch(/defaultTab\.current === null && queue\.load !== "pending"/);
    expect(src).toContain("picked ?? defaultTab.current");
    // The old per-render flip is gone.
    expect(src).not.toContain("activeTab(picked,");
  });

  it("builds no shell part (R9)", () => {
    for (const bad of ["NotificationBell", "AgentChat", "CommandPalette", "SearchPalette"]) {
      expect(src).not.toContain(bad);
    }
  });
});
