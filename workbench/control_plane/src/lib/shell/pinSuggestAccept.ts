/**
 * The Yes of the pin suggestion (`package_host.md` §9.3 item 2, PKG-19a-2).
 *
 * D111: no learning in silence. This is the ONLY code of the suggestion that
 * writes the arrangement, and it runs only on a member's Yes. The fence is
 * `arrangement.test.ts`, check 4. It writes through the one existing path,
 * `saveShellPrefs`, in the way the sidebar's star does.
 */
import type { NavSection } from "@/lib/nav";

import { EMPTY_SHELL, starBase, starredHrefs, toggleStar, type Stars } from "./presets";
import { latestShell, saveShellPrefs } from "./shellPrefs";

/** What the member's arrangement is now: the stored copy and the drawn layout. */
export type PinAcceptInput = {
  stored: Parameters<typeof saveShellPrefs>[0];
  layout: Stars;
  sections: readonly NavSection[];
};

/**
 * Star `href`. A pane that is starred already writes nothing. `save` is
 * `saveShellPrefs`, and a test passes a fake.
 */
export async function acceptPin(
  href: string,
  input: PinAcceptInput,
  save: typeof saveShellPrefs = saveShellPrefs,
): Promise<boolean> {
  const base = starBase({ ...EMPTY_SHELL, ...(latestShell() ?? input.stored) }, input.layout);
  if (starredHrefs(base, input.sections)?.has(href)) return false;
  await save({ ...base, ...toggleStar(base, input.sections, href) });
  return true;
}
