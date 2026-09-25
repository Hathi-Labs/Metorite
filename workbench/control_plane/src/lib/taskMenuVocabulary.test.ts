/**
 * One right-click menu vocabulary for both task apps (continuity P3, item 6).
 *
 * Pinned: the Projects registry follows the shared order and uses the shared
 * labels. The My Tasks card menu follows the same order, puts its own acts
 * last, and says "Mark done" / "Reopen" as Projects does.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { TASK_MENU_ACTIONS, type TaskMenuContext } from "@/app/projects/lib/taskMenu";
import { MY_TASK_ONLY_ACTS, cardMenuActs } from "@/app/tasks/lib/cardMenu";

import {
  SHARED_TASK_ACTS,
  TASK_ACT_LABEL,
  doneLabel,
  inTaskMenuOrder,
  taskActRank,
} from "./taskMenuVocabulary";

/** `task.unarchive` is the archive slot's other face. */
const actOf = (id: string) => {
  const act = id.replace(/^task\./, "");
  return act === "unarchive" ? "archive" : act;
};

const CTX = {
  task: { id: "t1", status_id: "s1", archived_at: null },
  statuses: [],
  canSelect: true,
  selected: false,
} as unknown as TaskMenuContext;

describe("the shared order", () => {
  it("every Projects menu action is a shared act", () => {
    for (const action of TASK_MENU_ACTIONS) {
      expect(SHARED_TASK_ACTS as readonly string[]).toContain(actOf(action.id));
    }
  });

  it("the Projects registry is declared in the shared order", () => {
    const ranks = TASK_MENU_ACTIONS.map((a) => taskActRank(actOf(a.id)));
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
  });

  it("inTaskMenuOrder puts shared acts first, in order, and keeps app-only order", () => {
    expect(inTaskMenuOrder(["eliminate", "status", "schedule", "markDone"])).toEqual([
      "markDone",
      "status",
      "eliminate",
      "schedule",
    ]);
  });
});

describe("the shared labels", () => {
  it("Projects draws each fixed label from the vocabulary", () => {
    for (const action of TASK_MENU_ACTIONS) {
      const act = actOf(action.id) as keyof typeof TASK_ACT_LABEL;
      if (action.id === "task.unarchive" || !(act in TASK_ACT_LABEL)) continue;
      expect(action.label(CTX), action.id).toBe(TASK_ACT_LABEL[act]);
    }
  });

  it("one wording for done", () => {
    expect(doneLabel(false)).toBe("Mark done");
    expect(doneLabel(true)).toBe("Reopen");
    const markDone = TASK_MENU_ACTIONS.find((a) => a.id === "task.markDone")!;
    expect(markDone.label(CTX)).toBe(doneLabel(false));
  });
});

describe("the My Tasks card menu", () => {
  it("shared acts in the shared order, then My Tasks' own acts", () => {
    const acts = cardMenuActs({ canPromote: true }).filter((a) => a !== "sep");
    expect(acts).toEqual(["markDone", "moveToProject", "status", "schedule", "eliminate"]);
    const shared = acts.filter((a) => !(MY_TASK_ONLY_ACTS as readonly string[]).includes(a));
    expect(shared).toEqual(inTaskMenuOrder(shared));
  });

  it("separators fall between blocks only", () => {
    for (const canPromote of [true, false]) {
      const menu = cardMenuActs({ canPromote });
      expect(menu[0]).not.toBe("sep");
      expect(menu.at(-1)).not.toBe("sep");
      menu.forEach((entry, at) => {
        if (entry === "sep") expect(menu[at + 1]).not.toBe("sep");
      });
    }
    expect(cardMenuActs({ canPromote: false })).toEqual([
      "markDone",
      "sep",
      "status",
      "sep",
      "schedule",
      "eliminate",
    ]);
  });

  it("the card draws the shared words (source fence: .tsx is not collected)", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../app/tasks/components/TaskCard.tsx", import.meta.url)),
      "utf8",
    );
    expect(src).toMatch(/cardMenuActs\(/);
    expect(src).toMatch(/doneLabel\(actions\.isDone\)/);
    expect(src).toMatch(/TASK_ACT_LABEL\.status/);
    expect(src).not.toMatch(/Mark as Done|Mark as not done/);
  });
});
