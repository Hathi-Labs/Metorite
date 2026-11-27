/**
 * "Build it" and "New app" in the Studio library (`ai_studio.md` §6.3, slice G3a).
 *
 * The steps, in order: make a draft package, hold the brief for it, go to its IDE. The IDE sends the
 * brief to the dock once its facts are published (`buildHandoff.ts`), so the first ask works on THIS
 * package and never makes a second one. A create that fails opens nothing and returns the sentence
 * for the member. The effects come in as arguments, so the order is a test and not a promise.
 */
import { holdBrief } from "./buildHandoff";
import { appBrief } from "./packageLibrary";
import { buildHref, createDraftPackage } from "./packageDraft";
import { STUDIO_OFF } from "./realDoor";

export const NOT_ON_LINE = "AI Studio is not on for your workspace. Ask an admin to turn it on, then try again.";

export interface BuildSteps {
  create: (brief: string) => Promise<{ id: string; name: string }>;
  go: (href: string) => void;
  /** Hold the brief for the IDE page of package `id`. */
  hold: (id: string, brief: string) => void;
}

export type BuildResult = { ok: true; id: string; href: string } | { ok: false; message: string };

export async function startBuild(seed: string, steps: Partial<BuildSteps> & Pick<BuildSteps, "go">): Promise<BuildResult> {
  const brief = appBrief(seed);
  const create = steps.create ?? ((b: string) => createDraftPackage(b));
  let made: { id: string; name: string };
  try {
    made = await create(brief);
  } catch (e) {
    const message = e instanceof Error && e.message === STUDIO_OFF ? NOT_ON_LINE : e instanceof Error ? e.message : "";
    return { ok: false, message: message || "Studio could not make the app. Nothing was opened. Press Build it again." };
  }
  const href = buildHref(made.id);
  (steps.hold ?? holdBrief)(made.id, brief);
  steps.go(href);
  return { ok: true, id: made.id, href };
}
