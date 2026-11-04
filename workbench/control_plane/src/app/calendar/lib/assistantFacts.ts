/**
 * What Calendar tells the one assistant about its page (D99, TC-1, D172.4).
 * Facts only: the page and the open task card. Before TC-1 Calendar published
 * nothing, so "this task" meant nothing while a card was open. A task title is
 * quoted as data, because another member may have written it.
 *
 * Fence (R7): `assistantFacts.test.ts` beside this file.
 */

import { dataText, type AssistantFacts } from "@/lib/shell/assistant";

/** The longest task title the chip shows. */
const CHIP_MAX = 40;

export function calendarAssistantFacts(input: {
  openTask?: { id: string; title: string } | null;
}): AssistantFacts {
  const facts: string[] = ["They have the Calendar page open."];
  const open = input.openTask;
  if (open) {
    facts.push(`They have the task ${dataText(open.title)} open (item_id: ${open.id}). "This task" means it.`);
  }
  const title = open?.title.replace(/\s+/g, " ").trim();
  return {
    label: title ? (title.length > CHIP_MAX ? `${title.slice(0, CHIP_MAX - 1)}…` : title) : undefined,
    facts,
  };
}
