/**
 * What Projects tells the one assistant about its page (D99,
 * `navigation_shell.md` §7.1). Facts only: the node in view, the canvas, the
 * filters, the open task and the selection, each with its id, so the general
 * agent can pass them to the Projects specialist. Names are quoted as data,
 * because members write them.
 *
 * It replaces the Projects chat's focus picker (`lib/chatScope.ts`). The owner
 * asked for no extra control (2026-10-12): the page says where the member is,
 * and the assistant asks when a request is not clear.
 *
 * Fence (R7): `assistantFacts.test.ts`.
 */

import { dataText, type AssistantFacts } from "@/lib/shell/assistant";
import { holdsNoTasksNote, holdsTasksNote, type PersonaInput } from "./assistantPersona";

/** The most selected ids the facts name. */
const MAX_PICKED = 50;
/** The longest name the chip shows. */
const CHIP_MAX = 40;

function chip(name: string | undefined): string | undefined {
  const clean = name?.replace(/\s+/g, " ").trim();
  if (!clean) return undefined;
  return clean.length > CHIP_MAX ? `${clean.slice(0, CHIP_MAX - 1)}…` : clean;
}

/** The app id of the My tasks roll-up (WS-64 OP-11). */
const MY_TASKS_APP = "my-tasks";

/**
 * What the assistant is told on My tasks (WS-64 OP-11). The chat tools work
 * the same on any page. This fact keeps "here" from reading as a project.
 */
export const MY_TASKS_FACT =
  "They are on their My tasks roll-up (everything assigned to them), not a project. " +
  "Creating a task here goes to their task inbox unless they name a project.";

export function projectsAssistantFacts(input: Omit<PersonaInput, "today" | "timezone"> & {
  /** The Projects destination open instead of a canvas: Reports or Settings. */
  app?: string | null;
  /** WS-64 OP-11 — the chip of My tasks that is on, when `app` is My tasks. */
  chip?: string | null;
}): AssistantFacts {
  const facts: string[] = [];
  if (input.app === MY_TASKS_APP) {
    // WS-64 OP-11. My tasks is a roll-up, not a project, so no node is named
    // here, even when the page still holds the last project selected.
    facts.push(MY_TASKS_FACT);
    if (input.view) facts.push(`They are looking at the ${input.view} view of My tasks.`);
    if (input.chip && input.chip !== "all") facts.push(`The ${input.chip} chip is on.`);
  } else if (input.node) {
    const level = input.node.level ?? "node";
    facts.push(
      `They have the ${level} ${dataText(input.node.name)} selected ` +
        `(project_id: ${input.node.id}${input.node.archived ? ", archived" : ""}). ` +
        `"This project" or "here" means it.`,
    );
    const note = holdsNoTasksNote(input.node) ?? holdsTasksNote(input.node);
    if (note) facts.push(note);
  } else {
    facts.push("They are looking at every space they can see, with no project selected.");
  }
  if (input.app === MY_TASKS_APP) {
    // Said above, with the view and the chip.
  } else if (input.app) facts.push(`They have the ${input.app} page of Projects open.`);
  else if (input.view) facts.push(`They are looking at the ${input.view} view.`);
  if (input.filterSummary) facts.push(`Active filters: ${input.filterSummary}.`);
  if (input.openTask) {
    const number = input.openTask.number != null ? `#${input.openTask.number} ` : "";
    facts.push(
      `They have task ${number}${dataText(input.openTask.title)} open (task_id: ${input.openTask.id}). ` +
        `"This task" means it.`,
    );
  }
  const picked = input.selectedTaskIds ?? [];
  if (picked.length > 0) {
    const shown = picked.slice(0, MAX_PICKED);
    facts.push(
      `They have ${picked.length} task${picked.length === 1 ? "" : "s"} selected ` +
        `(task_ids: ${shown.join(", ")}${picked.length > shown.length ? ", …" : ""}). ` +
        `"These tasks" means that selection.`,
    );
  }
  const place = input.app === MY_TASKS_APP ? "My tasks" : chip(input.node?.name);
  return { label: chip(input.openTask?.title) ?? place, facts };
}
