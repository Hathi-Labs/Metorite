/**
 * The pure rules of Settings, Home layout (`package_host.md` §9.1, PKG-17c-2).
 *
 * The page edits the org default of the shell layout, one entry for each role.
 * This file holds what the page decides, with no React. The read, the body
 * and the save are in `lib/shell/orgLayoutAdmin.ts`. This file adds nothing to
 * them. It only picks the panes to list, applies one change to a draft, and
 * decides when the first save asks for a confirm.
 *
 * Fence (R7): `layoutPage.test.ts`.
 */
import { PANES, type NavPane } from "@/lib/nav";
import type { OrgLayout, OrgRole } from "@/lib/shell/orgLayout";
import { MAX_PINS, refreshShellDefault, resetRole, saveOrgLayout, setRole, toBody, type OrgLayoutSave } from "@/lib/shell/orgLayoutAdmin";
import { STARRABLE, presetById, type PresetId } from "@/lib/shell/presets";

export const HEADING = "Home layout";
export const INTRO =
  "This sets the apps that members see first. It applies only to members who have not chosen their own apps.";
export const HELPER = "A member who does not hold an app still does not see it.";
export const OFF_TEXT = "Not turned on for your organisation.";
export const FAULT_TEXT = "Could not read the default.";
export const READ_ONLY_TEXT = "Ask an admin to change the Home layout.";
export const CONFIRM_BODY = "This replaces the whole default. The read showed none. Save only if you mean it.";
export const SYSTEM_DEFAULT = "System default";
export const CAP_TEXT = `A role can star at most ${MAX_PINS} apps.`;

/** The panes an admin may star, in `nav.ts` order. A system pane only, never an install. */
export function starrablePanes(): NavPane[] {
  return PANES.filter((p) => STARRABLE.has(p.href));
}

/** The pinned hrefs of one role in a draft, or null when the role sets none. */
export function pinsOf(layout: OrgLayout, role: OrgRole): string[] | null {
  return layout.roles[role]?.pins ?? null;
}

/** The preset of one role in a draft, or null for the system default. */
export function presetOf(layout: OrgLayout, role: OrgRole): PresetId | null {
  return layout.roles[role]?.preset ?? null;
}

export interface Change {
  layout: OrgLayout;
  /** The sentence to show when the change was refused. */
  refusal: string | null;
}

/**
 * Star or unstar one pane for one role. A pane outside `STARRABLE` is ignored.
 * A pick over `MAX_PINS` is refused with a sentence. Unstarring the last pane
 * leaves the pins at null, so the role follows its preset again.
 */
export function togglePin(layout: OrgLayout, role: OrgRole, href: string, on: boolean): Change {
  if (!STARRABLE.has(href)) return { layout, refusal: null };
  const now = pinsOf(layout, role) ?? [];
  const next = on ? (now.includes(href) ? now : [...now, href]) : now.filter((h) => h !== href);
  if (next.length > MAX_PINS) return { layout, refusal: CAP_TEXT };
  return { layout: setRole(layout, role, { preset: presetOf(layout, role), pins: next.length ? next : null }), refusal: null };
}

/** Set the preset of one role. An unknown id and "" mean the system default. */
export function pickPreset(layout: OrgLayout, role: OrgRole, id: string): OrgLayout {
  const preset = presetById(id)?.id ?? null;
  return setRole(layout, role, { preset, pins: pinsOf(layout, role) });
}

/** Clear one role: the system decides again. */
export function clearRole(layout: OrgLayout, role: OrgRole): OrgLayout {
  return resetRole(layout, role);
}

/** True when two layouts send the same body. The save button waits on this. */
export function sameLayout(a: OrgLayout, b: OrgLayout): boolean {
  return JSON.stringify(toBody(a)) === JSON.stringify(toBody(b));
}

/** The first save from an empty read asks first. A later save in the same visit does not. */
export function needsConfirm(readEmpty: boolean, confirmed: boolean): boolean {
  return readEmpty && !confirmed;
}

/**
 * Save the draft, and on success hand the saved default to the shell cache.
 * The body is `toBody(draft)`, the full roles map, so an untouched role goes
 * back unchanged.
 */
export async function commitDraft(draft: OrgLayout): Promise<OrgLayoutSave> {
  const res = await saveOrgLayout(draft);
  if (res.kind === "saved") refreshShellDefault(res.layout);
  return res;
}
