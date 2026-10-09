/**
 * My Day's rules (`navigation_shell.md` §4.3 to §4.5, NS-3).
 *
 * Each rule that decides what the page shows is pure, in `myDay.ts`, so it
 * is an assertion here and not a click: the greeting, the date, the summary
 * sentence, the groups and their order, the seven-row cut, the Next actions
 * dedupe and which cards a member gets.
 */
import { describe, expect, it } from "vitest";

import {
  APP_NAMES,
  KIND_LABELS,
  NEEDS_SHOWN,
  appIcon,
  blockState,
  cardsFor,
  dateLine,
  emptyNeedsLine,
  failedLines,
  firstName,
  greeting,
  greetingLine,
  groupNeeds,
  needsTaskIds,
  nextActions,
  rowTime,
  shownNeeds,
  summaryLine,
  todayWindow,
} from "./myDay";
import type { NeedsItem, NeedsKind } from "./needs";

const row = (id: string, kind: NeedsKind, extra: Partial<NeedsItem> = {}): NeedsItem => ({
  id,
  app: kind === "notification" ? "projects" : kind === "needs_reply" ? "email" : "tasks",
  kind,
  title: `Row ${id}`,
  detail: null,
  href: "/projects",
  at: null,
  act: null,
  act_ref: null,
  ...extra,
});

describe("the header", () => {
  it("greets by the member's local hour", () => {
    expect(greeting(0)).toBe("Good morning");
    expect(greeting(11)).toBe("Good morning");
    expect(greeting(12)).toBe("Good afternoon");
    expect(greeting(17)).toBe("Good afternoon");
    expect(greeting(18)).toBe("Good evening");
    expect(greeting(23)).toBe("Good evening");
  });

  it("uses the first word of a display name, and never an address", () => {
    expect(firstName("Vijay Raghav Varada")).toBe("Vijay");
    expect(firstName("  Asha  ")).toBe("Asha");
    expect(firstName("vijay@example.com")).toBeNull();
    expect(firstName("")).toBeNull();
    expect(firstName(undefined)).toBeNull();
    const morning = new Date(2026, 9, 9, 8, 30);
    expect(greetingLine(morning, "Vijay Varada")).toBe("Good morning, Vijay");
    expect(greetingLine(morning, null)).toBe("Good morning");
  });

  it("writes the date as 'Friday, 9 October'", () => {
    expect(dateLine(new Date(2026, 9, 9, 15))).toBe("Friday, 9 October");
    expect(dateLine(new Date(2026, 0, 1))).toBe("Thursday, 1 January");
  });

  it("says one summary sentence from the data", () => {
    expect(summaryLine(3, 2)).toBe("3 things need you, and 2 are on your calendar today.");
    expect(summaryLine(1, 1)).toBe("1 thing needs you, and 1 is on your calendar today.");
    expect(summaryLine(2, 0)).toBe("2 things need you.");
    expect(summaryLine(2, null)).toBe("2 things need you.");
    expect(summaryLine(0, 0)).toBe("Nothing needs you right now, and your calendar is clear.");
    expect(summaryLine(0, null)).toBe("Nothing needs you right now.");
    expect(summaryLine(0, 2)).toBe("Nothing needs you right now. 2 things are on your calendar today.");
    expect(summaryLine(0, 1)).toBe("Nothing needs you right now. 1 thing is on your calendar today.");
  });

  it("says the caveat once, in the card, and the summary claims nothing about needs", () => {
    // A source failed: the summary speaks of the calendar only.
    expect(summaryLine(0, 0, true)).toBe("Your calendar is clear.");
    expect(summaryLine(0, 2, true)).toBe("2 things are on your calendar today.");
    expect(summaryLine(0, null, true)).toBeNull();
    // Rows that DID arrive are still a fact.
    expect(summaryLine(2, 1, true)).toBe("2 things need you, and 1 is on your calendar today.");
    // The card: plain when every source answered, "else" when one did not.
    expect(emptyNeedsLine(false)).toBe("Nothing needs you right now.");
    expect(emptyNeedsLine(true)).toBe("Nothing else needs you right now.");
    // No sentence says "in the apps that answered" any more.
    for (const line of [summaryLine(0, 0, true), summaryLine(0, 1, true), emptyNeedsLine(true)]) {
      expect(line ?? "").not.toMatch(/apps that answered/);
    }
  });
});

describe("Needs you", () => {
  it("groups in the server's kind order, under plain labels", () => {
    const items = [row("r", "needs_reply"), row("n", "notification"), row("o", "overdue"), row("d", "due_today")];
    const groups = groupNeeds(items);
    expect(groups.map((g) => g.label)).toEqual([
      "Overdue",
      "Due today",
      "From your projects",
      "Waiting for your reply",
    ]);
    expect(KIND_LABELS.needs_reply).toBe("Waiting for your reply");
  });

  it("keeps the server's order inside a group, and drops an empty group", () => {
    const items = [row("o2", "overdue"), row("o1", "overdue"), row("n1", "notification")];
    const groups = groupNeeds(items);
    expect(groups).toHaveLength(2);
    expect(groups[0].items.map((i) => i.id)).toEqual(["o2", "o1"]);
  });

  it("shows the first seven rows across the whole feed, then all on request", () => {
    const items = [
      ...Array.from({ length: 5 }, (_, i) => row(`o${i}`, "overdue")),
      ...Array.from({ length: 5 }, (_, i) => row(`n${i}`, "notification")),
    ];
    expect(NEEDS_SHOWN).toBe(7);
    const cut = shownNeeds(items, false);
    expect(cut.groups.flatMap((g) => g.items)).toHaveLength(7);
    expect(cut.hidden).toBe(3);
    expect(cut.groups.map((g) => g.items.length)).toEqual([5, 2]);
    const all = shownNeeds(items, true);
    expect(all.groups.flatMap((g) => g.items)).toHaveLength(10);
    expect(all.hidden).toBe(0);
  });

  it("hides nothing when the feed is short", () => {
    expect(shownNeeds([row("a", "overdue")], false).hidden).toBe(0);
  });

  it("says when a row is due, and how long ago anything else came", () => {
    const now = Date.parse("2026-10-09T12:00:00Z");
    expect(rowTime({ kind: "overdue", at: "2026-10-07T12:00:00Z" }, now)).toBe("Due 2d ago");
    expect(rowTime({ kind: "due_today", at: "2026-10-09T15:00:00Z" }, now)).toBe("Due in 3h");
    expect(rowTime({ kind: "notification", at: "2026-10-09T11:55:00Z" }, now)).toBe("5m ago");
    expect(rowTime({ kind: "needs_reply", at: null }, now)).toBe("");
  });

  it("names a failed source in one muted line, and only a failed one", () => {
    expect(failedLines({ tasks: "ok", projects: "failed", email: "absent" })).toEqual([
      "Projects did not answer, so its notifications may be missing.",
    ]);
    expect(failedLines({})).toEqual([]);
    expect(APP_NAMES.email).toBe("Email");
  });

  it("draws each app with its own manifest icon", () => {
    expect(appIcon("tasks")).toBe("CheckSquare");
    expect(appIcon("projects")).toBe("FolderKanban");
    expect(appIcon("email")).toBe("Mail");
  });
});

describe("Next actions", () => {
  const tasks = Array.from({ length: 8 }, (_, i) => ({ id: `t${i}` }));

  it("takes the first five", () => {
    expect(nextActions(tasks, []).map((t) => t.id)).toEqual(["t0", "t1", "t2", "t3", "t4"]);
  });

  it("leaves out a task Needs you already shows, by its act or by its link", () => {
    const needs = [
      row("tasks:t1", "overdue", { act: "done", act_ref: "t1" }),
      row("projects:n1", "notification", { act: "read", act_ref: "n1", href: "/projects?task=t3" }),
      row("email:a:x", "needs_reply", { href: "/email?email=m1&account=a" }),
    ];
    expect([...needsTaskIds(needs)].sort()).toEqual(["t1", "t3"]);
    expect(nextActions(tasks, needs).map((t) => t.id)).toEqual(["t0", "t2", "t4", "t5", "t6"]);
  });
});

describe("Today", () => {
  it("asks for the member's local day, half open", () => {
    const now = new Date(2026, 9, 9, 15, 20);
    const { start, end } = todayWindow(now);
    expect(new Date(start).getTime()).toBe(new Date(2026, 9, 9).getTime());
    expect(new Date(end).getTime()).toBe(new Date(2026, 9, 10).getTime());
  });
});

describe("Today's rows against the clock", () => {
  const now = Date.parse("2026-10-09T16:40:00Z");
  it("calls a block past once its end is behind now", () => {
    expect(blockState("2026-10-09T09:30:00Z", "2026-10-09T10:00:00Z", now)).toBe("past");
    expect(blockState("2026-10-09T16:00:00Z", "2026-10-09T16:40:00Z", now)).toBe("past");
  });
  it("calls a block that has started and not ended 'now'", () => {
    expect(blockState("2026-10-09T16:30:00Z", "2026-10-09T17:30:00Z", now)).toBe("now");
  });
  it("calls a block still to come 'later', and reads a missing end safely", () => {
    expect(blockState("2026-10-09T18:00:00Z", "2026-10-09T19:00:00Z", now)).toBe("later");
    expect(blockState("2026-10-09T18:00:00Z", null, now)).toBe("later");
    expect(blockState("2026-10-09T08:00:00Z", null, now)).toBe("past");
    expect(blockState(null, null, now)).toBe("later");
  });
  it("is the rule the Today card draws muted by", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../../app/calendar/components/TodayCard.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/blockState\(task\.scheduledStart, task\.scheduledEnd, nowMs\) === "past"/);
    expect(src).toMatch(/over \? "text-muted-foreground"/);
  });
});

describe("which cards a member gets", () => {
  it("shows a card only for an app the member holds", () => {
    expect(cardsFor(["tasks", "projects", "email"])).toEqual({ needs: true, today: true, next: true });
    expect(cardsFor(["email"])).toEqual({ needs: true, today: false, next: false });
    expect(cardsFor(["projects"])).toEqual({ needs: true, today: false, next: false });
    expect(cardsFor(["chat"])).toEqual({ needs: false, today: false, next: false });
    expect(cardsFor([])).toEqual({ needs: false, today: false, next: false });
  });

  it("asks for what the server asks for: the lens needs feature:projects", () => {
    // `/projects/my/*` sits on the Projects router, which demands
    // `projects`. A member with the My Tasks pane alone must see no card
    // that can only fail, and the feed gives them nothing either.
    expect(cardsFor(["tasks"])).toEqual({ needs: false, today: false, next: false });
    expect(cardsFor(["tasks", "email"])).toEqual({ needs: true, today: false, next: false });
    expect(cardsFor(["tasks", "projects"])).toEqual({ needs: true, today: true, next: true });
  });
});
