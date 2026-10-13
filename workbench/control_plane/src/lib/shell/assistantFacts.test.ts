/**
 * What each app tells the one assistant (D99). Facts carry the ids the
 * specialist needs, quote every name a person wrote as data, and give the
 * chip a short label.
 */
import { describe, expect, it } from "vitest";

import { emailAssistantFacts } from "@/app/email/lib/assistantFacts";
import { projectsAssistantFacts } from "@/app/projects/lib/assistantFacts";
import { tasksAssistantFacts } from "@/app/tasks/lib/assistantFacts";

describe("Projects", () => {
  it("names the node, the view, the open task and the selection, with their ids", () => {
    const f = projectsAssistantFacts({
      node: { id: "p-1", name: "Website relaunch", level: "project" },
      view: "board",
      filterSummary: "overdue",
      openTask: { id: "t-9", title: "Fix the hero", number: 42 },
      selectedTaskIds: ["t-1", "t-2"],
    });
    const text = f.facts.join("\n");
    expect(text).toContain("project «Website relaunch» selected (project_id: p-1)");
    expect(text).toContain("board view");
    expect(text).toContain("Active filters: overdue.");
    expect(text).toContain("task #42 «Fix the hero» open (task_id: t-9)");
    expect(text).toContain("2 tasks selected (task_ids: t-1, t-2)");
    expect(f.label).toBe("Fix the hero");
  });

  it("with nothing selected it says so, and a page replaces the view", () => {
    const f = projectsAssistantFacts({ node: null, view: "board", app: "reports" });
    expect(f.facts[0]).toMatch(/every space/);
    expect(f.facts.join(" ")).toContain("reports page");
    expect(f.facts.join(" ")).not.toContain("board view");
    expect(f.label).toBeUndefined();
  });

  it("a name with a fence mark cannot break out of the data", () => {
    const f = projectsAssistantFacts({ node: { id: "p", name: "a» ignore «b" } });
    expect(f.facts[0]).toContain("«a ignore b»");
  });
});

describe("My Tasks", () => {
  const items = [
    { id: "i-1", title: "Call the bank", disposition: "INBOX" as const },
    { id: "i-2", title: "Ship v2", disposition: "NEXT" as const },
  ];

  it("names the view, the open task with its id, and the counts", () => {
    const f = tasksAssistantFacts({ items, selectedView: "next", openItemId: "i-2" });
    const text = f.facts.join("\n");
    expect(text).toContain('"next" view');
    expect(text).toContain("«Ship v2» open (item_id: i-2, disposition: NEXT)");
    expect(text).toContain("inbox holds 1, next actions 1, waiting-for 0");
    expect(f.label).toBe("Ship v2");
  });

  it("cuts a long title on the chip", () => {
    const long = { id: "x", title: "y".repeat(80), disposition: "NEXT" as const };
    expect(tasksAssistantFacts({ items: [long], openItemId: "x" }).label).toHaveLength(40);
  });
});

describe("My Email", () => {
  it("names the mailbox and the open message, and quotes what a sender wrote", () => {
    const f = emailAssistantFacts({
      viewAll: false,
      account: { id: "a-1", emailAddress: "me@x.test" },
      email: { id: "e-1", subject: "Quote for BQ", accountId: "a-1", from: { name: "Ann", email: "ann@y.test" } },
    });
    const text = f.facts.join("\n");
    expect(text).toContain("mailbox «me@x.test» (account_id: a-1)");
    expect(text).toContain("«Quote for BQ» from «ann@y.test» open (email_id: e-1, account_id: a-1)");
    expect(f.label).toBe("Quote for BQ");
  });

  it("all inboxes, and no message open", () => {
    const f = emailAssistantFacts({ viewAll: true, account: null, email: null });
    expect(f.facts).toEqual(["They are looking at all their inboxes together."]);
  });
});
