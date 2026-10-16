/**
 * What Knowledge tells the one assistant about its page (WS-61 KB-2a,
 * `knowledge_base.md` §11.1, `navigation_shell.md` §7.1).
 *
 * Facts only: the page id, the title, the space and the state. A title and a
 * space name are member text, so each is quoted as data (`dataText`).
 */

import { dataText, type AssistantFacts } from "@/lib/shell/assistant";

import { stateLabel } from "./labels";

/** The longest title the chip shows. */
const CHIP_MAX = 40;

export function knowledgeAssistantFacts(input: {
  page: { id: string; title: string; state: string };
  spaceName: string | null;
}): AssistantFacts {
  const { page } = input;
  const where = input.spaceName ? ` in the space ${dataText(input.spaceName)}` : "";
  const facts = [
    `They have the Knowledge page ${dataText(page.title)}${where} open ` +
      `(page_id: ${page.id}). "This page" means it.`,
    `The page is ${stateLabel(page.state).toLowerCase()}.`,
  ];
  const title = page.title.replace(/\s+/g, " ").trim();
  return {
    label: title.length > CHIP_MAX ? `${title.slice(0, CHIP_MAX - 1)}…` : title,
    facts,
  };
}
