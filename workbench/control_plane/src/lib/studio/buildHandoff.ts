/**
 * The hand-off of the brief from "Build it" to the Studio IDE page (`ai_studio.md` §6.3, G3a fix 1).
 *
 * Race that this closes: "Build it" used to open the dock with the brief at once. The IDE had not
 * published its facts yet, so an early Enter ran with no package fact and Mira made a SECOND package.
 * Now the library holds the brief here, keyed by the package id, and goes to the IDE. The IDE sends
 * the brief as an ask of the shell (the dock sends an `ask` by itself) only AFTER the first draft
 * loaded and `useAssistantFacts` published the facts of THIS package. One take, one send.
 */
import type { AssistantFacts } from "@/lib/shell/assistant";

import { PACKAGE_FACT } from "./ideFacts";

const held = new Map<string, string>();

/** Hold the brief of a new app. A second hold for the same id replaces the first. */
export function holdBrief(pkg: string, brief: string): void {
  held.set(pkg, brief);
}

/** Take the held brief of `pkg`. It is gone after this call, so a brief goes once. */
export function takeBrief(pkg: string): string | null {
  const brief = held.get(pkg) ?? null;
  held.delete(pkg);
  return brief;
}

/** True when the facts on screen are the facts of `pkg`. */
export function factsNamePackage(facts: Pick<AssistantFacts, "facts"> | null | undefined, pkg: string): boolean {
  return Boolean(facts?.facts?.[0]?.startsWith(`${PACKAGE_FACT} ${pkg},`));
}

/**
 * Send the held brief, if one waits and the facts of this package are published. Returns true when
 * it sent. With no facts of the package, it sends nothing and keeps the brief for the next call.
 */
export function sendHeldBrief(a: {
  pkg: string;
  facts: Pick<AssistantFacts, "facts"> | null | undefined;
  send: (text: string) => void;
}): boolean {
  if (!factsNamePackage(a.facts, a.pkg)) return false;
  const text = takeBrief(a.pkg);
  if (!text) return false;
  a.send(text);
  return true;
}
