/**
 * What My Tasks tells the one assistant about its page (D99,
 * `navigation_shell.md` §7.1). Facts only: the view, the open task and the
 * counts. The general agent passes the ids to the My Tasks specialist. A
 * task's title is quoted as data, because another member may have written it.
 *
 * Fence (R7): `assistantFacts.test.ts`.
 */

import { dataText, type AssistantFacts } from "@/lib/shell/assistant";
import type { MyTask } from "./types";

/** The longest task title the chip shows. */
const CHIP_MAX = 40;

export function tasksAssistantFacts(input: {
  items: readonly Pick<MyTask, "id" | "title" | "disposition">[];
  selectedView?: string | null;
  openItemId?: string | null;
}): AssistantFacts {
  const facts: string[] = [];
  if (input.selectedView) facts.push(`They are looking at the "${input.selectedView}" view of My Tasks.`);
  const open = input.openItemId ? input.items.find((i) => i.id === input.openItemId) : undefined;
  if (open) {
    facts.push(
      `They have the task ${dataText(open.title)} open (item_id: ${open.id}, disposition: ${open.disposition}). ` +
        `"This task" means it.`,
    );
  }
  const count = (d: string) => input.items.filter((i) => i.disposition === d).length;
  facts.push(`Their inbox holds ${count("INBOX")}, next actions ${count("NEXT")}, waiting-for ${count("WAITING")}.`);
  const title = open?.title.replace(/\s+/g, " ").trim();
  return {
    label: title ? (title.length > CHIP_MAX ? `${title.slice(0, CHIP_MAX - 1)}…` : title) : undefined,
    facts,
  };
}
