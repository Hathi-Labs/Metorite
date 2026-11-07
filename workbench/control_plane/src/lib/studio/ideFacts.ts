/**
 * What the Studio IDE tells the dock (`ai_studio.md` §6.3, slice G3a).
 *
 * The dock agent is `orchestrator` on every page (D99), and the `studio` pack gives it the
 * builder tools. The tools keep no draft and `studio_package_get` does not return the
 * manifest. So the page gives the dock the package id and the draft, as facts. The `studio`
 * pack rule "when the page facts name a package id" tells the agent to edit THAT package.
 * The facts follow the draft: a new draft is a new fact, and the next turn reads it.
 *
 * Every word here is data. `surfaceContext` quotes it as data and says so to the agent.
 */
import type { AssistantFacts } from "@/lib/shell/assistant";

import { CHAT_CAP } from "./draftProblems";

/** The words that open the package fact. The pack rule looks for them. */
export const PACKAGE_FACT = "The member builds one Studio app in the Studio IDE. Its package id is";

/** A name cannot end the data fence early, and a line holds one line. */
const clean = (v: string): string => v.replace(/[«»]/g, "").replace(/\s+/g, " ").trim();

/** JSON on one line. A guillemet inside a string becomes its escape, so the text stays valid. */
function oneLine(manifest: unknown): string {
  return JSON.stringify(manifest ?? null).replace(/«/g, "\\u00ab").replace(/»/g, "\\u00bb");
}

export function ideFacts(pkg: { id: string; name: string }, manifest: unknown): AssistantFacts {
  const name = clean(pkg.name) || "Untitled app";
  const json = oneLine(manifest);
  const facts = [
    `${PACKAGE_FACT} ${pkg.id}, and its name is «${name}».`,
    "Edit this package. Save to this package id, and make no new package.",
    json.length <= CHAT_CAP
      ? `The stored draft manifest, as JSON: ${json}`
      : "The stored draft is over the size that a chat holds. Change it in Studio, and make no save from this chat.",
  ];
  return { label: name, facts };
}
