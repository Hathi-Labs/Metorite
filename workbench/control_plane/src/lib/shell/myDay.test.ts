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
  actError,
  appIcon,
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
    expect(summaryLine(0, 0)).toBe("Nothing needs you, and your calendar is clear.");
    expect(summaryLine(0, null)).toBe("Nothing needs you right now.");
    expect(summaryLine(0, 2)).toBe("Nothing needs you. 2 things are on your calendar today.");
    expect(summaryLine(0, 1)).toBe("Nothing needs you. 1 thing is on your calendar today.");
  });

  it("claims nothing about an app that did not answer", () => {
    expect(summaryLine(0, 0, true)).toBe("Nothing needs you in the apps that answered, and your calendar is clear.");
    expect(summaryLine(0, null, true)).toBe("Nothing needs you in the apps that answered.");
    expect(summaryLine(2, 1, true)).toBe("2 things need you, and 1 is on your calendar today.");
    expect(emptyNeedsLine(false)).toBe("Nothing needs you right now.");
    expect(emptyNeedsLine(true)).toBe("Nothing needs you in the apps that answered.");
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
      "Projects did not answer. Showing the rest.",
    ]);
    expect(failedLines({})).toEqual([]);
    expect(APP_NAMES.email).toBe("Email");
  });

  it("draws each app with its own manifest icon", () => {
    expect(appIcon("tasks")).toBe("CheckSquare");
    expect(appIcon("projects")).toBe("FolderKanban");
    expect(appIcon("email")).toBe("Mail");
  });

  it("words an act's failure by its act", () => {
    expect(actError("done")).toMatch(/done/);
    expect(actError("read")).toMatch(/read/);
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

describe("which cards a member gets", () => {
  it("shows a card only for an app the member holds", () => {
    expect(cardsFor(["tasks", "projects", "email"])).toEqual({ needs: true, today: true, next: true });
    expect(cardsFor(["email"])).toEqual({ needs: true, today: false, next: false });
    expect(cardsFor(["projects"])).toEqual({ needs: true, today: false, next: false });
    expect(cardsFor(["chat"])).toEqual({ needs: false, today: false, next: false });
    expect(cardsFor([])).toEqual({ needs: false, today: false, next: false });
  });
});
