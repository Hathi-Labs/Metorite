/**
 * EA-18e (task 2964): the agent-questions card answers inside My Day.
 * Fences R7: break the rule, and one of these fails.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { AGENT_QUESTIONS_HREF, AGENT_QUESTIONS_KIND, isExpandAct } from "./feed";
import { phoneMenuItems } from "./FeedCard";

const read = (f: string) => fs.readFileSync(path.join(__dirname, f), "utf8").replace(/\r\n/g, "\n");
const card = { kind: AGENT_QUESTIONS_KIND, href: AGENT_QUESTIONS_HREF };
const answer = { id: "answer", way: "open" as const, label: "Answer", href: AGENT_QUESTIONS_HREF };

describe("isExpandAct", () => {
  it("is true only for the answer act of the agent-questions card", () => {
    expect(isExpandAct(card, answer)).toBe(true);
    expect(isExpandAct({ kind: "task" }, answer)).toBe(false);
    expect(isExpandAct(card, { id: "open", way: "open" })).toBe(false);
    expect(isExpandAct(card, { id: "answer", way: "tap" })).toBe(false);
  });
});

describe("phoneMenuItems with an expand act", () => {
  it("runs onAct and never goes to the link", () => {
    const ran: string[] = [];
    const items = phoneMenuItems(
      card,
      [{ ...answer }],
      (a) => ran.push(`act:${a.id}`),
      (h) => ran.push(`go:${h}`),
      undefined,
      (a) => isExpandAct(card, a),
    );
    const first = items[0] as { onSelect: () => void };
    first.onSelect();
    expect(ran).toEqual(["act:answer"]);
  });
  it("keeps the link without the expand rule", () => {
    const ran: string[] = [];
    const items = phoneMenuItems(card, [{ ...answer }], () => ran.push("act"), (h) => ran.push(`go:${h}`));
    (items[0] as { onSelect: () => void }).onSelect();
    expect(ran).toEqual([`go:${AGENT_QUESTIONS_HREF}`]);
  });
});

describe("the card draws the embedded Questions list, with no second writer", () => {
  const inCard = read("QuestionsInCard.tsx");
  it("reuses QuestionsView in its embedded form", () => {
    expect(inCard).toContain('from "@/app/projects/components/QuestionsView"');
    expect(inCard).toMatch(/<QuestionsView\s+embedded/);
    expect(inCard).not.toMatch(/agentRunsApi|fetch\(/);
  });
  it("FeedCard toggles it with aria-expanded and loads it on demand", () => {
    const feedCard = read("FeedCard.tsx");
    expect(feedCard).toContain("data-feed-expand");
    expect(feedCard).toContain("aria-expanded={expanded}");
    expect(feedCard).toContain('dynamic(() => import("./QuestionsInCard")');
  });
  it("D101 point 4: an archive request is read-only in the card", () => {
    expect(inCard).toMatch(/<QuestionsView[\s\S]*noArchiveDecision/);
    const view = read("../../app/projects/components/QuestionsView.tsx");
    expect(view).toContain('answerRoute(row) === "archive-approval" && !noArchiveDecision');
    expect(view).toContain("Decide in Projects");
  });
  it("Escape closes the list", () => {
    expect(inCard).toMatch(/e\.key === "Escape"/);
  });
});
