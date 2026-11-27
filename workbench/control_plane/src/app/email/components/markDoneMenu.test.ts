// Task 3995, Mark done (`email_app_master_plan.md` §6.6).
//
// R7 fence `email-done-menu-first`: "Mark done" is the first item of the row
// menu, for one mail and for a selection, and it is absent when the app does
// not offer it.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Email } from "../lib/types";

import { ContextMenu } from "./EmailList";

const email = {
  id: "one",
  accountId: "a",
  threadId: "t",
  categories: ["Follow-up"],
  folder: "inbox",
  isRead: true,
  isStarred: false,
  isFlagged: false,
} as Email;

function menu(bulk: boolean, offered: boolean): string[] {
  const noop = () => {};
  const html = renderToStaticMarkup(
    createElement(ContextMenu, {
      ctx: { x: 0, y: 0, email, bulk, count: bulk ? 3 : 1 },
      folders: [],
      colorAccountId: null,
      availableLabels: [],
      appliedCategories: new Set<string>(),
      onApplyLabel: noop,
      onClearCategories: noop,
      onClose: noop,
      onMarkDone: offered ? noop : undefined,
      snoozedView: false,
      onSnooze: noop,
      onReply: noop,
      onAddToTasks: noop,
      onUpdate: noop,
      onDelete: noop,
      onBulkUpdate: noop,
      onBulkDelete: noop,
      onFix: noop,
    }),
  );
  return [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) =>
    m[1].replace(/<[^>]+>/g, "").trim(),
  );
}

describe("the row menu", () => {
  it("has Mark done first for one mail", () => {
    expect(menu(false, true).slice(0, 2)).toEqual(["Mark done", "Reply"]);
  });

  it("has Mark done first for a selection, with the count", () => {
    expect(menu(true, true).slice(0, 2)).toEqual(["Mark 3 done", "Add 3 to My work"]);
  });

  it("is the menu of before when the app does not offer it", () => {
    expect(menu(false, false)[0]).toBe("Reply");
    expect(menu(true, false)[0]).toBe("Add 3 to My work");
    expect(menu(false, false).join()).not.toContain("done");
  });
});
