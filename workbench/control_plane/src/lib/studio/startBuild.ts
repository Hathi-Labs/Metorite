/**
 * "Build it" and "New app" in the Studio library (`ai_studio.md` §6.3, slice G3a).
 *
 * The steps, in order: make a draft package, go to its IDE, open the dock with the brief. The
 * dock takes the facts of the IDE page, so the first ask works on THIS package. A create that
 * fails opens nothing and returns the sentence for the member. The three effects come in as
 * arguments, so the order is a test and not a promise.
 */
import { appBrief } from "./packageLibrary";
import { buildHref, createDraftPackage } from "./packageDraft";
import { STUDIO_OFF } from "./realDoor";

export const NOT_ON_LINE = "AI Studio is not on for your workspace. Ask an admin to turn it on, then try again.";

export interface BuildSteps {
  create: (brief: string) => Promise<{ id: string; name: string }>;
  go: (href: string) => void;
  /** Open the dock with `draft` waiting in the box, in a new chat. */
  openDock: (draft: string) => void;
}

export type BuildResult = { ok: true; id: string; href: string } | { ok: false; message: string };

export async function startBuild(seed: string, steps: Partial<BuildSteps> & Pick<BuildSteps, "go" | "openDock">): Promise<BuildResult> {
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
  steps.go(href);
  steps.openDock(brief);
  return { ok: true, id: made.id, href };
}
